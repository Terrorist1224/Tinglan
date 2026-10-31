import json
import time

import httpx
import pytest

from backend import db
from backend.engine import Engine
from backend.export import ExportError, render
from backend.translation import (
    DEFAULT_POLISH_PROMPT,
    DEFAULT_TRANSLATE_PROMPT,
    TranslationError,
    translate_batch,
    validate_response,
)

H = {"X-Tinglan": "1"}


def api_response(content, finish_reason="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": finish_reason,
                }
            ]
        },
    )


def test_settings_api_has_no_azure_fields(client):
    data = client.get("/api/settings").json()
    assert "azure_fallback_on_refusal" not in data
    assert "azure_region" not in data
    assert "has_azure_key" not in data
    assert "google_fallback_on_refusal" not in data
    assert "base_url" in data
    assert "model" in data

    saved = client.put(
        "/api/settings",
        headers=H,
        json={"base_url": "https://api.example.com/v1", "model": "gpt-4o"},
    )
    assert saved.status_code == 200
    res = saved.json()
    assert res["model"] == "gpt-4o"
    assert "azure_fallback_on_refusal" not in res


@pytest.mark.parametrize(
    "content",
    [
        "I am sorry, but I cannot assist with this request.",
        "Request blocked by content policy.",
        "抱歉，我无法翻译该内容。",
        '{"code":"content_filter"}',
    ],
)
def test_explicit_refusal_markers(content):
    with pytest.raises(TranslationError) as exc:
        validate_response(content, ["a"])
    assert exc.value.refusal


def test_refusal_is_requested_only_once_without_retry():
    calls = []

    def mock_api(req):
        calls.append(req)
        return api_response("I am sorry, but I cannot translate this.")

    with httpx.Client(transport=httpx.MockTransport(mock_api)) as client:
        with pytest.raises(TranslationError) as exc:
            translate_batch(
                {"base_url": "https://api.example.com/v1", "model": "test"},
                "key",
                [{"id": "a", "source": "Sensitive content"}],
                [],
                client=client,
            )
    assert exc.value.refusal
    assert len(calls) == 1  # Only one single request; NO prompt-switching retry!


def test_refused_batch_does_not_block_other_batches(task, monkeypatch):
    import backend.engine as engine_module

    with db.connect(write=True) as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        for i in range(2):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (f"b{i}", task, i * 1000, i * 1000 + 800, f"Source {i}", f"Source {i}", "en", "fixture"),
            )
        t = db.task(task)
        p = t["params"]
        p["context_mode"] = "independent"
        conn.execute("UPDATE tasks SET params=? WHERE id=?", (json.dumps(p), task))

    def mock_batch(settings, key, batch, context, **kwargs):
        if batch[0]["id"] == "b0":
            raise TranslationError("上游模型拒绝翻译该批次", refusal=True)
        return {batch[0]["id"]: f"译文_{batch[0]['id']}"}

    monkeypatch.setattr(engine_module, "translate_batch", mock_batch)
    engine = Engine()
    engine.start(task, "translate", {"base_url": "https://api.example.com/v1", "model": "test"}, "key")
    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic())
    assert db.task(task)["status"] == "complete"
    assert "有 1 句" in db.task(task)["error"]

    rows = {r["id"]: r for r in db.subtitles(task)}
    assert rows["b0"]["translation"] == ""
    assert rows["b1"]["translation"] == "译文_b1"
    assert rows["b1"]["translation_provider"] == "llm"

    detail = db.task(task)["params"]["progress_detail"]
    assert detail["completed"] == 2
    assert detail["translated"] == 1
    assert detail["restricted"] == 1
    assert detail["remaining"] == 0


def test_resume_preserves_uncompleted_subtitles(task, monkeypatch):
    import backend.engine as engine_module

    with db.connect(write=True) as conn:
        conn.execute("UPDATE subtitles SET translation='', translated_version=-1 WHERE task_id=?", (task,))
    params = db.task(task)["params"]
    params["operation"] = {
        "phase": "translate",
        "ids": ["s0"],
        "overwrite": False,
        "config_snapshot": {
            "base_url": "https://api.example.com/v1",
            "model": "test",
            "azure_fallback_on_refusal": True,
            "google_fallback_on_refusal": True,
        },
    }
    db.update_task(task, params=params)
    seen = {}

    def mock_batch(settings, key, batch, context, **kwargs):
        seen.update(settings)
        return {batch[0]["id"]: "成功"}

    monkeypatch.setattr(engine_module, "translate_batch", mock_batch)
    engine = Engine()
    engine.start(
        task,
        "translate",
        {"base_url": "https://api.example.com/v1", "model": "test"},
        "key",
        resume=True,
    )
    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic())
    assert seen["model"] == "test"
    assert "azure_fallback_on_refusal" not in seen
    assert "google_fallback_on_refusal" not in seen


def test_single_subtitle_translate_and_refusal(client, task, monkeypatch):
    client.put(
        "/api/settings",
        headers=H,
        json={"base_url": "https://api.example.com/v1", "model": "gpt-4o", "key": "test-key"},
    )
    row = db.subtitles(task)[0]

    def mock_refusal(*args, **kwargs):
        raise TranslationError("模型拒绝", refusal=True)

    monkeypatch.setattr("backend.translation.translate_batch", mock_refusal)
    res = client.post(f"/api/tasks/{task}/subtitles/{row['id']}/translate", headers=H)
    assert res.status_code == 400
    assert "模型拒绝" in res.json()["detail"]
    assert db.subtitles(task)[0]["translation"] == ""

    monkeypatch.setattr("backend.translation.translate_batch", lambda *args, **kwargs: {row["id"]: "大模型单句译文"})
    res2 = client.post(f"/api/tasks/{task}/subtitles/{row['id']}/translate", headers=H)
    assert res2.status_code == 200
    assert res2.json()["translation"] == "大模型单句译文"
    assert res2.json()["translation_provider"] == "llm"


def test_polish_refusal_translates_original_text(client, task, monkeypatch):
    client.put(
        "/api/settings",
        headers=H,
        json={"base_url": "https://api.example.com/v1", "model": "gpt-4o", "key": "test-key"},
    )
    row = db.subtitles(task)[0]

    def mock_polish_refusal(*args, **kwargs):
        raise TranslationError("润色拒绝", refusal=True)

    seen = {}

    def mock_translate(settings, key, batch, context, **kwargs):
        seen["source"] = batch[0]["source"]
        return {batch[0]["id"]: "使用原文翻译的译文"}

    monkeypatch.setattr("backend.translation.polish_batch", mock_polish_refusal)
    monkeypatch.setattr("backend.translation.translate_batch", mock_translate)

    res = client.post(f"/api/tasks/{task}/subtitles/{row['id']}/polish-and-translate", headers=H)
    assert res.status_code == 200
    assert seen["source"] == row["source"]
    assert res.json()["source"] == row["source"]
    assert res.json()["translation"] == "使用原文翻译的译文"
    assert "润色被模型拒绝" in db.task(task)["error"]


def test_export_fill_in_rules_srt_vtt_txt_and_json():
    rows = [
        {
            "id": "r0",
            "start": 0,
            "end": 2000,
            "source": "First sentence.",
            "translation": "第一句。",
            "source_version": 1,
            "translated_version": 1,
            "translation_provider": "llm",
        },
        {
            "id": "r1",
            "start": 2500,
            "end": 4500,
            "source": "Second sentence.",
            "translation": "",
            "source_version": 1,
            "translated_version": -1,
            "translation_provider": "",
        },
        {
            "id": "r2",
            "start": 5000,
            "end": 7000,
            "source": "Third sentence modified.",
            "translation": "旧第三句译文",
            "source_version": 2,
            "translated_version": 1,
            "translation_provider": "llm",
        },
    ]

    # 1. Mode: source
    for fmt in ("srt", "vtt"):
        out = render(rows, fmt, "source")
        assert "First sentence." in out
        assert "Second sentence." in out
        assert "Third sentence modified." in out
        assert "第一句" not in out

    txt_src = render(rows, "txt", "source")
    assert "First sentence." in txt_src
    assert "Second sentence." in txt_src
    assert "Third sentence modified." in txt_src

    # 2. Mode: translation (missing and stale filled with source)
    for fmt in ("srt", "vtt"):
        out = render(rows, fmt, "translation")
        assert "第一句。" in out
        assert "Second sentence." in out  # filled with source
        assert "Third sentence modified." in out  # filled with source
        assert "旧第三句译文" not in out  # stale translation excluded!

    txt_trans = render(rows, "txt", "translation")
    assert "第一句。" in txt_trans
    assert "Second sentence." in txt_trans
    assert "Third sentence modified." in txt_trans
    assert "旧第三句译文" not in txt_trans

    # 3. Mode: bilingual (valid has both; missing/stale outputs source ONCE, no duplicate)
    srt_bi = render(rows, "srt", "bilingual")
    assert "第一句。\nFirst sentence." in srt_bi
    assert "Second sentence." in srt_bi
    assert "Second sentence.\nSecond sentence." not in srt_bi  # only once!
    assert "Third sentence modified." in srt_bi
    assert "旧第三句译文" not in srt_bi

    txt_bi = render(rows, "txt", "bilingual")
    assert "原文: First sentence." in txt_bi
    assert "译文: 第一句。" in txt_bi
    assert "原文: Second sentence." in txt_bi
    assert "译文: Second sentence." not in txt_bi  # no fake translation line
    assert "原文: Third sentence modified." in txt_bi
    assert "旧第三句译文" not in txt_bi

    # 4. JSON: retains all raw data
    js_raw = json.loads(render(rows, "json", "bilingual"))
    assert js_raw["subtitles"][0]["translation"] == "第一句。"
    assert js_raw["subtitles"][1]["translation"] == ""
    assert js_raw["subtitles"][2]["translation"] == "旧第三句译文"

    # 5. Invalid time or empty source still raises ExportError
    bad_time = [{"id": "bad", "start": 3000, "end": 2000, "source": "Invalid"}]
    with pytest.raises(ExportError) as exc:
        render(bad_time, "srt", "bilingual")
    assert exc.value.reason == "invalid_subtitle"


def test_provider_persistence_history_split_merge(client, task):
    first, second, third = db.subtitles(task)
    assert db.apply_translation(task, first, "历史谷歌译文", "google")
    assert db.apply_translation(task, second, "历史Azure译文", "azure")
    assert db.apply_translation(task, third, "新LLM译文", "llm")

    subs = client.get(f"/api/tasks/{task}").json()["subtitles"]
    assert subs[0]["translation_provider"] == "google"
    assert subs[1]["translation_provider"] == "azure"
    assert subs[2]["translation_provider"] == "llm"

    # Export directly without missing-check exception
    exp_srt = client.get(f"/api/tasks/{task}/export?fmt=srt&mode=bilingual").text
    assert "历史谷歌译文" in exp_srt
    assert "历史Azure译文" in exp_srt
    assert "新LLM译文" in exp_srt

    # Edit subtitle manually
    current_third = db.subtitles(task)[2]
    edited = client.patch(
        f"/api/tasks/{task}/subtitles/{current_third['id']}",
        headers=H,
        json={
            "version": current_third["version"],
            "start": current_third["start"],
            "end": current_third["end"],
            "source": current_third["source"],
            "translation": "人工改动",
            "speaker": "",
            "confirmed": False,
        },
    ).json()
    assert edited["translation_provider"] == "manual"

    # Split retains provider
    current_first = db.subtitles(task)[0]
    split_res = client.post(
        f"/api/tasks/{task}/subtitles/{current_first['id']}/split",
        headers=H,
        json={"version": current_first["version"], "at": 1000, "position": 5},
    )
    assert split_res.status_code == 200
    parts = db.subtitles(task)[:2]
    assert parts[0]["translation_provider"] == "google"
    assert parts[1]["translation_provider"] == "google"

