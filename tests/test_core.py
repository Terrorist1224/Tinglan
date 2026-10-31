import json
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
import srt
from backend import db
from backend.engine import Engine
from backend.export import render
from backend.translation import TranslationError, translate_batch, validate_response

H = {"X-Tinglan": "1"}


def edit_body(row, **changes):
    return {
        **{
            k: row[k]
            for k in ("version", "start", "end", "source", "translation", "speaker")
        },
        "confirmed": bool(row["confirmed"]),
        **changes,
    }


def test_local_boundary(client, task):
    assert client.post(f"/api/tasks/{task}/control/pause").status_code == 403
    assert (
        client.get("/api/tasks", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert client.get("/api/tasks", headers={"Host": "evil.example"}).status_code == 400
    assert (
        client.get("/api/tasks", headers={"Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )
    assert client.get("/api/tasks/not-a-real-task/media").status_code == 404
    assert client.get("/api/tasks/..%2f..%2fsecret/media").status_code == 404


def test_edit_versions_and_stale_translation(client, task):
    row = db.subtitles(task)[0]
    r = client.patch(
        f"/api/tasks/{task}/subtitles/s0",
        headers=H,
        json=edit_body(row, translation="第一句。"),
    )
    assert r.status_code == 200
    saved = r.json()
    assert (
        saved["manual_translation"] == 1
        and saved["translated_version"] == saved["source_version"]
    )
    assert (
        client.patch(
            f"/api/tasks/{task}/subtitles/s0",
            headers=H,
            json=edit_body(row, source="wrong"),
        ).status_code
        == 409
    )
    r = client.patch(
        f"/api/tasks/{task}/subtitles/s0",
        headers=H,
        json=edit_body(saved, source="Corrected sentence."),
    )
    updated = r.json()
    assert updated["translation"] == "第一句。"
    assert updated["source_version"] != updated["translated_version"]
    # 有旧译文但版本不一致时，“未完成”筛选必须保留它，同时能区分过期译文。
    rows = client.get(f"/api/tasks/{task}").json()["subtitles"]
    stale = next(item for item in rows if item["id"] == "s0")
    assert stale["translation"] == "第一句。"
    assert stale["translated_version"] != stale["source_version"]
    assert stale["translation"] and stale["translated_version"] != stale["source_version"]
    exp_res = client.get(f"/api/tasks/{task}/export?fmt=srt&mode=bilingual")
    assert exp_res.status_code == 200
    assert "Corrected sentence." in exp_res.text
    assert "第一句。" not in exp_res.text
    assert (
        client.get(f"/api/tasks/{task}/export?fmt=srt&mode=source").status_code == 200
    )
    assert not db.apply_translation(task, row, "迟到的译文")
    assert db.subtitles(task)[0]["translation"] == "第一句。"


def test_time_change_preserves_translation(client, task):
    row = db.subtitles(task)[0]
    db.apply_translation(task, row, "第一句")
    row = db.subtitles(task)[0]
    r = client.patch(
        f"/api/tasks/{task}/subtitles/s0",
        headers=H,
        json=edit_body(row, start=50, end=2600),
    )
    assert r.status_code == 200
    assert r.json()["translated_version"] == r.json()["source_version"]
    bad = edit_body(r.json(), end=30)
    assert (
        client.patch(f"/api/tasks/{task}/subtitles/s0", headers=H, json=bad).status_code
        == 400
    )


def test_simultaneous_edit_is_conflict(client, task):
    row = db.subtitles(task)[0]

    def write(text):
        return client.patch(
            f"/api/tasks/{task}/subtitles/s0",
            headers=H,
            json=edit_body(row, source=text),
        ).status_code

    with ThreadPoolExecutor(2) as pool:
        codes = list(pool.map(write, ["One", "Two"]))
    assert sorted(codes) == [200, 409]


def test_checkpoint_is_idempotent_and_restart_recovers(client, task):
    engine = Engine()
    event = {
        "type": "unit",
        "unit": 0,
        "rows": [
            {"start": 10000, "end": 11000, "source": "Checkpoint", "language": "en"}
        ],
    }
    engine.consume(task, "transcribe", event)
    engine.consume(task, "transcribe", event)
    assert len(db.subtitles(task)) == 4
    db.update_task(task, status="running", phase="transcribe")
    db.init()
    assert db.task(task)["status"] == "interrupted"
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 1


def test_split_merge_and_history(client, task):
    assert (
        client.post(
            f"/api/tasks/{task}/subtitles/s0/split",
            headers=H,
            json={"version": 0, "at": 1000, "position": 6},
        ).status_code
        == 200
    )
    rows = db.subtitles(task)
    assert len(rows) == 4 and rows[0]["end"] == 1000 and rows[1]["start"] == 1000
    assert (
        client.post(
            f"/api/tasks/{task}/merge",
            headers=H,
            json={
                "ids": [rows[0]["id"], rows[2]["id"]],
                "versions": [r["version"] for r in [rows[0], rows[2]]],
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/tasks/{task}/merge",
            headers=H,
            json={
                "ids": [rows[0]["id"], rows[1]["id"]],
                "versions": [rows[0]["version"], rows[1]["version"]],
            },
        ).status_code
        == 200
    )
    assert len(db.subtitles(task)) == 3
    history = client.get(f"/api/tasks/{task}/history").json()
    original = next(
        h for h in history if json.loads(h["snapshot"])["source"] == "First sentence."
    )
    row = db.subtitles(task)[0]
    assert (
        client.post(
            f"/api/tasks/{task}/history/{original['id']}/restore",
            headers=H,
            json={"version": row["version"]},
        ).status_code
        == 200
    )
    assert db.subtitles(task)[0]["source"] == "First sentence."


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '[{"id":"a","text":""}]',
        '[{"id":"a","text":"A"},{"id":"a","text":"B"}]',
        '[{"id":"wrong","text":"A"}]',
    ],
)
def test_translation_rejects_bad_ids(content):
    with pytest.raises(TranslationError):
        validate_response(content, ["a"])


def test_translation_retries_and_auth_stops():
    requests = []

    def handler(req):
        requests.append(req)
        if len(requests) < 3:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '[{"id":"a","text":"你好"}]'}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = translate_batch(
            {"base_url": "https://example.invalid/v1", "model": "configured-model"},
            "secret",
            [{"id": "a", "source": "Hello"}],
            [],
            client,
            lambda _: None,
        )
    assert result == {"a": "你好"} and len(requests) == 3
    requests.clear()

    def denied(req):
        requests.append(req)
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(denied)) as client:
        with pytest.raises(PermissionError):
            translate_batch(
                {"base_url": "https://example.invalid/v1", "model": "m"},
                "secret",
                [{"id": "a", "source": "Hello"}],
                [],
                client,
                lambda _: None,
            )
    assert len(requests) == 1


def test_translation_refusal_is_reported_without_retry():
    requests = []

    def handler(req):
        requests.append(req)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Sorry, I can't respond to this question."
                        }
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TranslationError) as exc_info:
            translate_batch(
                {"base_url": "https://example.invalid/v1", "model": "configured-model"},
                "secret",
                [{"id": "a", "source": "Hello"}],
                [],
                client,
                lambda _: None,
            )
    assert exc_info.value.refusal
    assert "拒绝" in str(exc_info.value)
    assert len(requests) == 1



def test_translation_split_and_invalid_json_retry():
    count = 0

    def handler(req):
        nonlocal count
        count += 1
        data = json.loads(req.content)
        rows = json.loads(data["messages"][1]["content"])["subtitles"]
        if len(rows) > 1:
            return httpx.Response(413)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                [{"id": rows[0]["id"], "text": "译文"}]
                            )
                        }
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = translate_batch(
            {"base_url": "https://e.invalid", "model": "m"},
            "secret",
            [{"id": "a", "source": "A"}, {"id": "b", "source": "B"}],
            [],
            client,
            lambda _: None,
        )
    assert set(result) == {"a", "b"} and count == 3


def test_export_roundtrip_and_unicode(client, task):
    rows = db.subtitles(task)
    for row in rows:
        db.apply_translation(task, row, "中文译文")
    rows = db.subtitles(task)
    text = render(rows, "srt", "bilingual")
    parsed = list(srt.parse(text))
    assert len(parsed) == 3 and parsed[0].content.startswith(rows[0]["translation"] + "\nFirst")
    assert parsed[-1].end.total_seconds() == 8.5
    vtt = render(rows, "vtt", "source")
    assert vtt.startswith("WEBVTT\n") and "00:00:00.000 -->" in vtt
    assert (
        json.loads(render(rows, "json", "source"))["subtitles"][0]["original"]
        == "First sentence."
    )


def test_settings_do_not_expose_or_write_key(client, task, monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main, "vault_key", lambda: "")
    body = {
        "base_url": "https://example.invalid/v1",
        "model": "my-model",
        "key": "sensitive-value",
        "save_key": False,
    }
    result = client.put("/api/settings", headers=H, json=body)
    assert result.status_code == 200 and "sensitive-value" not in result.text
    assert client.get("/api/settings").json()["has_key"]
    with db.connect() as conn:
        assert (
            "sensitive-value"
            not in conn.execute("SELECT value FROM settings").fetchone()[0]
        )
    assert (
        client.put(
            "/api/settings",
            headers=H,
            json={**body, "base_url": "https://user:password@example.invalid"},
        ).status_code
        == 400
    )
    client.put("/api/settings", headers=H, json={**body, "key": "", "clear_key": True})


def test_track_changes_and_cleanup_protect_source(client, task):
    p = db.task(task)["params"]
    p["audio_track"] = 5
    assert client.put(f"/api/tasks/{task}/params", headers=H, json=p).status_code == 400
    from backend.config import DATA

    folder = DATA / task
    (folder / "preview.mp4").write_bytes(b"preview")
    assert client.delete(f"/api/tasks/{task}/cache", headers=H).status_code == 200
    assert (folder / "source.mp4").exists() and not (folder / "preview.mp4").exists()
    assert len(db.subtitles(task)) == 3


@pytest.mark.parametrize("phase", ["review", "align"])
def test_deferred_model_phases_are_rejected(client, task, phase):
    result = client.post(f"/api/tasks/{task}/run", headers=H, json={"phase": phase})
    assert result.status_code == 400
    assert db.task(task)["status"] != "running"
    result = client.post(
        "/api/models/download",
        headers=H,
        json={
            "model": {
                "review": "qwen3-asr-1.7b",
                "align": "align-en",
                "diarize": "community-1",
            }[phase]
        },
    )
    assert result.status_code == 400
    assert [m["id"] for m in client.get("/api/system").json()["models"]] == [
        "whisper-large-v3",
        "community-1",
    ]


def wait_engine(engine):
    import time

    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic()), "后台测试未结束"


def test_legacy_flags_never_start_deferred_models(client, task, monkeypatch):
    import backend.engine as module

    engine = Engine()
    params = db.task(task)["params"]
    db.update_task(task, params={**params, "align": True, "diarize": True})
    phases = []

    def fake_worker(task, phase, ids):
        phases.append(phase)
        if phase == "diarize":
            engine.apply_diarization(
                task["id"], [{"start": 0, "end": 1000, "speaker": "SPEAKER_00"}]
            )

    monkeypatch.setattr(engine, "worker", fake_worker)
    monkeypatch.setattr(module, "model_ready", lambda name: True)
    engine.start(task, "transcribe")
    wait_engine(engine)
    assert phases == ["transcribe", "diarize"]
    assert db.task(task)["status"] == "complete"


def test_diarize_disabled_skips_community_model(client, task, monkeypatch):
    import backend.engine as module

    engine = Engine()
    params = db.task(task)["params"]
    db.update_task(task, params={**params, "diarize": False})
    phases = []
    monkeypatch.setattr(engine, "worker", lambda task, phase, ids: phases.append(phase))
    monkeypatch.setattr(module, "model_ready", lambda name: True)
    engine.start(task, "transcribe")
    wait_engine(engine)
    assert phases == ["transcribe"]
    assert db.task(task)["status"] == "complete"


def test_translation_batches_resume_and_protect_manual_text(client, task, monkeypatch):
    import backend.engine as module

    engine = Engine()
    row = db.subtitles(task)[0]
    result = client.patch(
        f"/api/tasks/{task}/subtitles/{row['id']}",
        headers=H,
        json=edit_body(row, translation="人工译文"),
    )
    assert result.status_code == 200
    with db.connect() as con:
        for i in range(3, 18):
            con.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"s{i}",
                    task,
                    i * 3000,
                    i * 3000 + 2500,
                    "Public sample.",
                    "Public sample.",
                    "en",
                    "fixture",
                ),
            )
    calls = []

    def translate(settings, key, rows, context):
        calls.append([r["id"] for r in rows])
        if len(calls) == 2:
            raise TranslationError("模拟网络中断")
        return {r["id"]: "公共样例" for r in rows}

    monkeypatch.setattr(module, "translate_batch", translate)
    settings = {"base_url": "https://example.invalid/v1", "model": "user-model"}
    engine.start(task, "translate", settings, "local-fixture")
    wait_engine(engine)
    assert db.task(task)["status"] == "failed"
    operation = db.task(task)["params"]["operation"]
    assert len(operation["ids"]) == 1
    rows_before = {r["id"]: r for r in db.subtitles(task)}
    completed = [r for r in rows_before.values() if r["translation"]]
    assert completed
    engine.start(task, "translate", settings, "local-fixture", resume=True)
    wait_engine(engine)
    assert db.task(task)["status"] == "complete"
    assert [len(c) for c in calls] == [16, 1, 1]
    assert calls[1] == calls[2]
    assert not any(r["id"] in calls[2] for r in completed)
    rows_after = {r["id"]: r for r in db.subtitles(task)}
    assert rows_after["s0"]["translation"] == "人工译文"
    assert rows_after["s0"]["manual_translation"] == 1
    for sid in calls[0]:
        assert rows_after[sid]["version"] == rows_before[sid]["version"]
    assert all(r["translation"] for r in rows_after.values())


def test_json_export_preserves_untranslated_state(client, task):
    response = client.get(f"/api/tasks/{task}/export?fmt=json&mode=bilingual")
    assert response.status_code == 200
    assert len(response.json()["subtitles"]) == 3
    assert response.json()["subtitles"][0]["translation"] == ""


def test_export_reports_exact_problem_subtitle(client, task):
    row = db.subtitles(task)[0]
    assert db.apply_translation(task, row, "公共样例译文")
    response = client.get(f"/api/tasks/{task}/export?fmt=srt&mode=bilingual")
    assert response.status_code == 200
    assert "公共样例译文" in response.text
    with db.connect() as con:
        con.execute("UPDATE subtitles SET end=start WHERE id='s0'")
    response = client.get(f"/api/tasks/{task}/export?fmt=vtt&mode=source")
    assert response.status_code == 400
    assert response.json()["subtitle_id"] == "s0"
    assert response.json()["reason"] == "invalid_subtitle"



def test_delete_task_removes_folder_and_db(client, task):
    from backend.config import DATA

    folder = DATA / task
    assert folder.exists()
    assert len(db.subtitles(task)) == 3
    response = client.delete(f"/api/tasks/{task}", headers=H)
    assert response.status_code == 200
    assert not folder.exists()
    assert len(db.subtitles(task)) == 0
    with pytest.raises(KeyError):
        db.task(task)


def test_delete_task_allows_missing_folder(client, task):
    from backend.config import DATA
    import shutil

    folder = DATA / task
    shutil.rmtree(folder)
    response = client.delete(f"/api/tasks/{task}", headers=H)
    assert response.status_code == 200
    assert len(db.subtitles(task)) == 0
    with pytest.raises(KeyError):
        db.task(task)


def test_delete_task_protects_running_task(client, task, monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main.engine, "active", task)
    response = client.delete(f"/api/tasks/{task}", headers=H)
    assert response.status_code == 400
    assert (main.DATA / task).exists()
    assert len(db.subtitles(task)) == 3


def test_delete_task_restores_folder_when_database_fails(task, monkeypatch):
    from backend.config import DATA
    import contextlib
    import sqlite3
    import backend.main as main

    folder = DATA / task
    trash = DATA / ("deleted-" + task)

    @contextlib.contextmanager
    def broken_connect(*args, **kwargs):
        raise sqlite3.OperationalError("database unavailable")
        yield

    with monkeypatch.context() as patch:
        patch.setattr(main, "get_task", lambda _task_id: None)
        patch.setattr(main.db, "connect", broken_connect)
        with pytest.raises(sqlite3.OperationalError):
            main.delete(task)
    assert folder.exists() and not trash.exists()
    assert len(db.subtitles(task)) == 3
    assert db.task(task)["id"] == task


def test_delete_task_keeps_records_when_folder_cannot_be_isolated(task, monkeypatch):
    from backend.config import DATA
    from pathlib import Path
    import backend.main as main

    folder = DATA / task

    def deny_rename(self, target):
        raise PermissionError("access denied")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", deny_rename)
        with pytest.raises(PermissionError):
            main.delete(task)
    assert folder.exists()
    assert len(db.subtitles(task)) == 3
    assert db.task(task)["id"] == task

def test_transcribe_restart_clears_data_while_resume_preserves(client, task, monkeypatch):
    import backend.engine as module

    engine = Engine()
    params = db.task(task)["params"]
    params["speakers"] = {"1": {"name": "老说话人"}}
    db.update_task(task, params=params, status="interrupted", phase="transcribe")

    with db.connect(write=True) as conn:
        conn.execute("INSERT INTO checkpoints VALUES(?, 'transcribe', 0)", (task,))
        conn.execute(
            "INSERT INTO subtitles(id, task_id, start, end, source, original, language) VALUES('sub_old', ?, 0, 1000, '旧识别', '旧识别', 'ja')",
            (task,),
        )

    seen_completed = []

    def mock_worker(t, phase, ids):
        with db.connect() as conn:
            units = [r[0] for r in conn.execute("SELECT unit FROM checkpoints WHERE task_id=? AND phase=?", (t["id"], phase)).fetchall()]
        seen_completed.append(units)

    monkeypatch.setattr(engine, "worker", mock_worker)
    monkeypatch.setattr(module, "model_ready", lambda name: False)

    # 1. Test resume=True: preserves existing checkpoint & subtitle
    engine.start(task, "transcribe", resume=True, overwrite=False)
    wait_engine(engine)
    assert seen_completed[-1] == [0]
    assert any(s["id"] == "sub_old" for s in db.subtitles(task))

    # 2. Test overwrite=True, resume=False: resets checkpoints, subtitles, and speakers
    db.update_task(task, status="interrupted", phase="transcribe")
    engine.start(task, "transcribe", resume=False, overwrite=True)
    wait_engine(engine)
    assert seen_completed[-1] == []
    assert not any(s["id"] == "sub_old" for s in db.subtitles(task))
    assert db.task(task)["params"].get("speakers") == {}

