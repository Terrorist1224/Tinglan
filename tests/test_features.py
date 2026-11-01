from backend import cloud_processing
import json
import srt
import pytest
from backend import db
from backend.engine import Engine
from backend.export import render, slice_intervals
from backend.translation import (
    DEFAULT_POLISH_PROMPT,
    DEFAULT_TRANSLATE_PROMPT,
    TranslationError,
    create_batches,
    polish_batch,
    translate_batch,
    validate_response,
)

H = {"X-Tinglan": "1"}


def test_overlapping_export_intervals():
    # 1. 无重叠
    r_no = [
        {"id": "1", "start": 0, "end": 1000, "source": "A", "translation": "甲", "source_version": 1, "translated_version": 1},
        {"id": "2", "start": 2000, "end": 3000, "source": "B", "translation": "乙", "source_version": 1, "translated_version": 1},
    ]
    int_no = slice_intervals(r_no, "source")
    assert len(int_no) == 2
    assert int_no[0]["start"] == 0 and int_no[0]["end"] == 1000 and int_no[0]["text"] == "A"
    assert int_no[1]["start"] == 2000 and int_no[1]["end"] == 3000 and int_no[1]["text"] == "B"

    # 2. 部分重叠
    r_part = [
        {"id": "1", "start": 0, "end": 4000, "source": "A", "translation": "甲", "source_version": 1, "translated_version": 1},
        {"id": "2", "start": 2000, "end": 6000, "source": "B", "translation": "乙", "source_version": 1, "translated_version": 1},
    ]
    int_part = slice_intervals(r_part, "source")
    assert len(int_part) == 3
    assert (int_part[0]["start"], int_part[0]["end"], int_part[0]["text"]) == (0, 2000, "A")
    assert (int_part[1]["start"], int_part[1]["end"], int_part[1]["text"]) == (2000, 4000, "A\nB")
    assert (int_part[2]["start"], int_part[2]["end"], int_part[2]["text"]) == (4000, 6000, "B")

    # 3. 包含关系
    r_contain = [
        {"id": "1", "start": 0, "end": 6000, "source": "A", "translation": "甲", "source_version": 1, "translated_version": 1},
        {"id": "2", "start": 2000, "end": 4000, "source": "B", "translation": "乙", "source_version": 1, "translated_version": 1},
    ]
    int_contain = slice_intervals(r_contain, "source")
    assert len(int_contain) == 3
    assert int_contain[0]["text"] == "A"
    assert int_contain[1]["text"] == "A\nB"
    assert int_contain[2]["text"] == "A"

    # 4. 相同起止时间
    r_same = [
        {"id": "1", "start": 1000, "end": 3000, "source": "A", "translation": "甲", "source_version": 1, "translated_version": 1},
        {"id": "2", "start": 1000, "end": 3000, "source": "B", "translation": "乙", "source_version": 1, "translated_version": 1},
    ]
    int_same = slice_intervals(r_same, "source")
    assert len(int_same) == 1
    assert (int_same[0]["start"], int_same[0]["end"], int_same[0]["text"]) == (1000, 3000, "A\nB")

    # 5. 三段重叠
    r_tri = [
        {"id": "1", "start": 0, "end": 5000, "source": "A", "translation": "甲", "source_version": 1, "translated_version": 1},
        {"id": "2", "start": 2000, "end": 6000, "source": "B", "translation": "乙", "source_version": 1, "translated_version": 1},
        {"id": "3", "start": 3000, "end": 4000, "source": "C", "translation": "丙", "source_version": 1, "translated_version": 1},
    ]
    int_tri = slice_intervals(r_tri, "source")
    assert len(int_tri) == 5
    assert int_tri[2]["text"] == "A\nB\nC"

    # 6. 双语导出：原文在上，译文在下
    rendered_bi = render(r_part, "srt", "bilingual")
    parsed = list(srt.parse(rendered_bi))
    assert len(parsed) == 3
    assert parsed[1].content == "甲\nA\n乙\nB"


def test_polish_and_revert_workflow(client, task, monkeypatch):
    import backend.engine as module

    engine = Engine()
    subs = db.subtitles(task)
    assert len(subs) >= 3

    # Mock polish
    def mock_polish(settings, key, batch, context):
        return {s["id"]: s["source"] + " [润色]" for s in batch}

    monkeypatch.setattr(cloud_processing, "polish_batch", mock_polish)

    settings = {"base_url": "https://example.invalid/v1", "model": "test-model"}
    engine.start(task, "polish", settings, "mock-key", ids=[subs[0]["id"], subs[1]["id"]])
    import time
    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic())

    updated = db.subtitles(task)
    assert updated[0]["source"].endswith("[润色]")
    assert updated[1]["source"].endswith("[润色]")
    assert updated[2]["source"] == subs[2]["source"]  # 未选中的不变
    # 润色改变原文后，translated_version 变为过期
    assert updated[0]["translated_version"] != updated[0]["source_version"]

    # 撤回本次润色
    res = client.post(f"/api/tasks/{task}/revert-polish", headers=H)
    assert res.status_code == 200
    reverted_data = res.json()
    assert reverted_data["reverted"] == 2

    after_revert = db.subtitles(task)
    assert after_revert[0]["source"] == subs[0]["source"]
    assert after_revert[1]["source"] == subs[1]["source"]


def test_polish_conflict_detection(client, task, monkeypatch):
    import backend.engine as module

    engine = Engine()
    subs = db.subtitles(task)
    s0 = subs[0]

    def mock_polish(settings, key, batch, context):
        # Simulate user editing the subtitle mid-request
        with db.connect(write=True) as conn:
            conn.execute("UPDATE subtitles SET source='人工并发修改', version=version+1 WHERE id=?", (s0["id"],))
        return {s["id"]: "AI 迟到的润色" for s in batch}

    monkeypatch.setattr(cloud_processing, "polish_batch", mock_polish)
    settings = {"base_url": "https://example.invalid/v1", "model": "test-model"}
    engine.start(task, "polish", settings, "mock-key", ids=[s0["id"]])
    import time
    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic())

    # 人工修改应被保留，AI 结果被跳过
    current = [r for r in db.subtitles(task) if r["id"] == s0["id"]][0]
    assert current["source"] == "人工并发修改"
    assert "人工修改" in db.task(task)["error"]


def test_diarization_application_and_word_splitting(client, task):
    engine = Engine()
    # Insert a subtitle with word-level timestamps that cross speakers
    words = [
        {"word": "Hi ", "start": 1000, "end": 1800, "prob": 0.95},
        {"word": "there, ", "start": 1800, "end": 2500, "prob": 0.92},
        {"word": "I ", "start": 3000, "end": 3500, "prob": 0.88},
        {"word": "agree.", "start": 3500, "end": 4200, "prob": 0.90},
    ]
    with db.connect(write=True) as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        conn.execute(
            "INSERT INTO subtitles(id,task_id,start,end,source,original,words) VALUES(?,?,?,?,?,?,?)",
            ("sub_cross", task, 1000, 4200, "Hi there, I agree.", "Hi there, I agree.", json.dumps(words)),
        )

    diar_segments = [
        {"start": 500, "end": 2600, "speaker": "SPEAKER_00"},
        {"start": 2800, "end": 5000, "speaker": "SPEAKER_01"},
    ]
    engine.apply_diarization(task, diar_segments)

    updated = db.subtitles(task)
    assert len(updated) == 2
    assert updated[0]["source"] == "Hi there," and updated[0]["speaker"] == "1"
    assert updated[1]["source"] == "I agree." and updated[1]["speaker"] == "2"

    t = db.task(task)
    assert "speakers" in t["params"]
    assert t["params"]["speakers"]["1"]["name"] == "说话人 1"
    assert t["params"]["speakers"]["2"]["name"] == "说话人 2"


def test_create_batches_context_and_independent():
    all_subs = [{"id": f"s{i}", "source": f"Text {i}"} for i in range(20)]
    # 逐句独立模式
    indep = create_batches(all_subs, {"s1", "s2", "s3"}, context_mode="independent")
    assert len(indep) == 3
    assert indep[0][1] == []

    # 批次上下文模式
    batched = create_batches(all_subs, {"s1", "s2", "s3"}, context_mode="batch")
    assert len(batched) == 1
    batch_items, ctx = batched[0]
    assert len(batch_items) == 3
    assert len(ctx) <= 8


def test_local_key_persistence_and_removal(client):
    from backend.config import DATA

    key_file = DATA / "api.key"
    key_file.unlink(missing_ok=True)

    # 1. 保存设置和本地密钥
    body = {
        "base_url": "https://example.invalid/v1",
        "model": "gpt-4o",
        "key": "test-local-key-12345",
        "save_key": False,
    }
    res = client.put("/api/settings", headers=H, json=body)
    assert res.status_code == 200
    assert "test-local-key-12345" not in res.text
    assert key_file.exists() and key_file.read_text(encoding="utf-8") == "test-local-key-12345"
    assert client.get("/api/settings").json()["has_key"] is True

    # 2. 清除密钥
    res = client.put("/api/settings", headers=H, json={**body, "clear_key": True})
    assert res.status_code == 200
    assert not key_file.exists()
    assert client.get("/api/settings").json()["has_key"] is False


def test_fetch_api_models_endpoint(client, monkeypatch):
    import httpx

    class FakeResponse:
        status_code = 200
        is_error = False

        def json(self):
            return {
                "object": "list",
                "data": [
                    {"id": "gpt-4o"},
                    {"id": "gpt-4o-mini"},
                    {"id": "claude-3-5-sonnet"},
                ],
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers=None):
            assert url == "https://api.openai.com/v1/models"
            assert headers.get("Authorization") == "Bearer sk-test"
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    res = client.post(
        "/api/settings/models",
        headers=H,
        json={"base_url": "https://api.openai.com/v1", "key": "sk-test"},
    )
    assert res.status_code == 200
    models = res.json()["models"]
    assert models == ["claude-3-5-sonnet", "gpt-4o", "gpt-4o-mini"]
import json
import pytest
from backend import db
from backend.export import render

H = {"X-Tinglan": "1"}


def test_txt_export_formatting():
    # 1. 有说话人时的 TXT 排版
    rows_with_spk = [
        {"id": "1", "start": 83000, "end": 85000, "source": "这是第一句润色内容", "translation": "This is first.", "speaker": "1", "source_version": 1, "translated_version": 1},
        {"id": "2", "start": 95000, "end": 98000, "source": "这是第二句", "translation": "This is second.", "speaker": "2", "source_version": 1, "translated_version": 1},
    ]
    speakers = {
        "1": {"name": "说话人 1", "note": ""},
        "2": {"name": "嘉宾张三", "note": ""},
    }
    txt_source = render(rows_with_spk, "txt", "source", speakers=speakers)
    lines = txt_source.splitlines()
    assert lines[0] == "[00:01:23] 说话人 1: 这是第一句润色内容"
    assert lines[1] == "[00:01:35] 嘉宾张三: 这是第二句"

    # 2. 无说话人时的 TXT 排版
    rows_no_spk = [
        {"id": "1", "start": 83000, "end": 85000, "source": "纯口述录音内容", "translation": "", "speaker": "", "source_version": 1, "translated_version": 0},
    ]
    txt_no_spk = render(rows_no_spk, "txt", "source")
    assert txt_no_spk == "[00:01:23] 纯口述录音内容"

    # 3. 双语模式下的 TXT 分行标注
    txt_bilingual = render(rows_with_spk[:1], "txt", "bilingual", speakers=speakers)
    bi_lines = txt_bilingual.splitlines()
    assert len(bi_lines) == 2
    assert bi_lines[0] == "[00:01:23] 说话人 1: 原文: 这是第一句润色内容"
    assert bi_lines[1] == "[00:01:23] 说话人 1: 译文: This is first."


def test_pure_source_export_without_translation(client, task):
    # 将 task 的字幕译文全部清空
    with db.connect(write=True) as conn:
        conn.execute("UPDATE subtitles SET translation='', translated_version=0 WHERE task_id=?", (task,))

    # 纯原文导出: SRT, VTT, TXT 均不得触发缺少译文异常
    for fmt in ["srt", "vtt", "txt"]:
        res = client.get(f"/api/tasks/{task}/export?fmt={fmt}&mode=source")
        assert res.status_code == 200
        assert len(res.text.strip()) > 0

    # 双语导出未完成项以当前原文补位且只输出一次
    res = client.get(f"/api/tasks/{task}/export?fmt=srt&mode=bilingual")
    assert res.status_code == 200
    assert "First sentence." in res.text
    assert (rows := list(srt.parse(res.text)))[0].content == "First sentence."
    assert all(item.content for item in rows)


def test_speaker_name_change_is_used_by_export(client, task):
    params = db.task(task)["params"]
    params["speakers"] = {
        "1": {"name": "张总", "note": "主持人"},
        "2": {"name": "李工", "note": ""},
    }
    db.update_task(task, params=params)
    rows = db.subtitles(task)
    with db.connect(write=True) as conn:
        conn.execute(
            "UPDATE subtitles SET speaker='1' WHERE task_id=? AND id=?",
            (task, rows[0]["id"]),
        )

    res = client.get(f"/api/tasks/{task}/export?fmt=txt&mode=source")
    assert res.status_code == 200
    assert "张总: First sentence." in res.text


def test_single_subtitle_polish_endpoint(client, task, monkeypatch):
    from backend.routes import subtitles as main_mod

    subs = db.subtitles(task)
    s0 = subs[0]

    def mock_polish(settings, key, batch, context):
        return {s["id"]: "已清理语气词的润色文本" for s in batch}

    monkeypatch.setattr("backend.translation.polish_batch", mock_polish)

    # 先保存设置和 key
    client.put("/api/settings", headers=H, json={
        "base_url": "https://api.example.com/v1",
        "model": "gpt-4o",
        "key": "test-key",
    })

    res = client.post(f"/api/tasks/{task}/subtitles/{s0['id']}/polish", headers=H)
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "已清理语气词的润色文本"

    # 验证撤回历史已记录
    cur_task = client.get(f"/api/tasks/{task}").json()
    assert "last_polish" in cur_task["params"]
    assert len(cur_task["params"]["last_polish"]["changes"]) == 1

    # 执行撤回
    revert_res = client.post(f"/api/tasks/{task}/revert-polish", headers=H)
    assert revert_res.status_code == 200
    assert revert_res.json()["reverted"] == 1
    reverted_sub = client.get(f"/api/tasks/{task}").json()["subtitles"][0]
    assert reverted_sub["source"] == s0["source"]

def test_process_llm_raises_translation_error_on_batch_failure(task, monkeypatch):
    engine = Engine()
    t = db.task(task)
    settings = {
        "base_url": "https://api.example.com/v1",
        "model": "gpt-4o",
        "concurrency": 1,
    }

    def failing_batch(*args, **kwargs):
        raise TranslationError("mock-network-failure")

    monkeypatch.setattr("backend.cloud_processing.translate_batch", failing_batch)

    with pytest.raises(TranslationError) as exc_info:
        engine.process_llm(t, "translate", settings, "test-key", None, overwrite=False)

    assert "mock-network-failure" in str(exc_info.value)

