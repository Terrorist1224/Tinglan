import json
import time
import httpx
import pytest
from backend import db
from backend.engine import Engine, recommended_concurrency
from backend.translation import (
    TranslationError,
    translate_batch,
    polish_batch,
)

H = {"X-Tinglan": "1"}


def test_adaptive_concurrency_ladders():
    assert [recommended_concurrency(n) for n in (1, 2, 3)] == [1, 2, 3]
    assert [recommended_concurrency(n) for n in (4, 10)] == [4, 4]
    assert [recommended_concurrency(n) for n in (11, 30)] == [6, 6]
    assert [recommended_concurrency(n) for n in (31, 60)] == [8, 8]
    assert [recommended_concurrency(n) for n in (61, 120, 121, 10000)] == [12, 12, 16, 16]


def test_concurrency_settings_api(client):
    with db.connect(write=True) as conn:
        conn.execute("INSERT INTO settings(id,value) VALUES(1,?)", (json.dumps({"concurrency": "legacy-invalid"}),))
    assert "concurrency" not in client.get("/api/settings").json()
    for legacy in (0, 5, 100, "ignored"):
        res = client.put("/api/settings", headers=H, json={
            "base_url": "https://api.example.com/v1", "model": "test", "concurrency": legacy,
        })
        assert res.status_code == 200
        assert "concurrency" not in res.json()
        with db.connect() as conn:
            assert "concurrency" not in json.loads(conn.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])


def test_retry_after_respected(monkeypatch):
    calls = []
    sleeps = []

    def fake_sleep(sec):
        sleeps.append(sec)

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '[{"id":"a","text":"译文"}]'}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        res = translate_batch(
            {"base_url": "https://api.example.com/v1", "model": "test"},
            "key",
            [{"id": "a", "source": "Hello"}],
            [],
            client=client,
            sleeper=fake_sleep,
        )
    assert res == {"a": "译文"}
    assert len(calls) == 2
    assert sleeps == [5.0]


def test_translation_429_retries_while_reducing_concurrency(monkeypatch):
    calls = []
    retries = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '[{"id":"a","text":"译文"}]'}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = translate_batch(
            {"base_url": "https://api.example.com/v1", "model": "test"},
            "key",
            [{"id": "a", "source": "Hello"}],
            [],
            client=client,
            sleeper=lambda _: None,
            on_retry=lambda exc: retries.append((exc.status_code, exc.retry_after)),
        )
    assert result == {"a": "译文"}
    assert len(calls) == 2
    assert retries and retries[0][0] == 429 and retries[0][1] == 1.0


def test_engine_concurrency_ceiling_and_out_of_order(client, task, monkeypatch):
    import backend.engine as engine_mod

    # 插入 6 条字幕，设为 independent 模式，产生 6 个 batch
    with db.connect(write=True) as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        for i in range(6):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (f"sub_{i}", task, i * 1000, i * 1000 + 800, f"Sentence {i}", f"Sentence {i}", "en", "fixture"),
            )
        # Update params to independent mode
        t = db.task(task)
        p = t["params"]
        p["context_mode"] = "independent"
        conn.execute("UPDATE tasks SET params=? WHERE id=?", (json.dumps(p), task))

    active_count = 0
    max_active = 0
    lock = time.sleep

    def mock_translate(settings, key, batch, context):
        nonlocal active_count, max_active
        active_count += 1
        if active_count > max_active:
            max_active = active_count
        # Let sub_0 take longer so sub_1 and sub_2 finish before sub_0 (out of order)
        sid = batch[0]["id"]
        if sid == "sub_0":
            time.sleep(0.08)
        else:
            time.sleep(0.01)
        active_count -= 1
        return {s["id"]: f"译_{s['source']}" for s in batch}

    monkeypatch.setattr(engine_mod, "translate_batch", mock_translate)

    engine = Engine()
    settings = {
        "base_url": "https://api.example.com/v1",
        "model": "test",
        "concurrency": 3,
    }
    engine.start(task, "translate", settings, "mock_key")

    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic())

    assert max_active <= 4
    assert max_active >= 2

    subs = db.subtitles(task)
    assert len(subs) == 6
    # 验证乱序完成后按字幕 ID 正确写入，保持时间顺序
    for i, s in enumerate(subs):
        assert s["id"] == f"sub_{i}"
        assert s["translation"] == f"译_Sentence {i}"


def test_polish_translate_combo_ordering(client, task, monkeypatch):
    import backend.engine as engine_mod

    with db.connect(write=True) as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        for i in range(4):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (f"sub_{i}", task, i * 1000, i * 1000 + 800, f"Raw {i}", f"Raw {i}", "en", "fixture"),
            )
        t = db.task(task)
        p = t["params"]
        p["context_mode"] = "independent"
        conn.execute("UPDATE tasks SET params=? WHERE id=?", (json.dumps(p), task))

    polish_called_on = []
    translate_called_on = []

    def mock_polish(settings, key, batch, context):
        sid = batch[0]["id"]
        polish_called_on.append(sid)
        time.sleep(0.02)
        return {s["id"]: f"Polished {s['source']}" for s in batch}

    def mock_translate(settings, key, batch, context):
        sid = batch[0]["id"]
        # 验证润色写入后，翻译收到的原文必须是已润色后的文字！
        assert "Polished" in batch[0]["source"], f"Translate received unpolished source: {batch[0]['source']}"
        translate_called_on.append(sid)
        time.sleep(0.02)
        return {s["id"]: f"Translated {s['source']}" for s in batch}

    monkeypatch.setattr(engine_mod, "polish_batch", mock_polish)
    monkeypatch.setattr(engine_mod, "translate_batch", mock_translate)

    engine = Engine()
    settings = {
        "base_url": "https://api.example.com/v1",
        "model": "test",
        "concurrency": 2,
    }
    engine.start(task, "polish_translate", settings, "mock_key")

    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic())

    subs = db.subtitles(task)
    assert len(subs) == 4
    for i, s in enumerate(subs):
        assert s["source"] == f"Polished Raw {i}"
        assert s["translation"] == f"Translated Polished Raw {i}"


def test_auth_failure_halts_dispatch(client, task, monkeypatch):
    import backend.engine as engine_mod

    with db.connect(write=True) as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        for i in range(5):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (f"sub_{i}", task, i * 1000, i * 1000 + 800, f"Text {i}", f"Text {i}", "en", "fixture"),
            )
        t = db.task(task)
        p = t["params"]
        p["context_mode"] = "independent"
        conn.execute("UPDATE tasks SET params=? WHERE id=?", (json.dumps(p), task))

    call_count = 0

    def mock_translate(settings, key, batch, context):
        nonlocal call_count
        call_count += 1
        raise PermissionError("API 认证失败，请检查密钥和模型权限")

    monkeypatch.setattr(engine_mod, "translate_batch", mock_translate)

    engine = Engine()
    settings = {
        "base_url": "https://api.example.com/v1",
        "model": "test",
        "concurrency": 2,
    }
    engine.start(task, "translate", settings, "mock_key")

    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic())

    # 认证失败后立即停止后续派发，不会派发全部 5 个批次
    assert call_count <= 4
    cur = db.task(task)
    assert cur["status"] == "failed"
    assert "认证失败" in cur["error"]


def test_pause_and_cancel_in_flight(client, task, monkeypatch):
    import backend.engine as engine_mod

    with db.connect(write=True) as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        for i in range(6):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (f"sub_{i}", task, i * 1000, i * 1000 + 800, f"Text {i}", f"Text {i}", "en", "fixture"),
            )
        t = db.task(task)
        p = t["params"]
        p["context_mode"] = "independent"
        conn.execute("UPDATE tasks SET params=? WHERE id=?", (json.dumps(p), task))

    started = __import__("threading").Event()

    def slow_translate(settings, key, batch, context):
        started.set()
        time.sleep(0.05)
        return {s["id"]: f"译_{s['source']}" for s in batch}

    monkeypatch.setattr(engine_mod, "translate_batch", slow_translate)

    # 1. 测试 Pause: 收尾已发出的请求，保存结果，停止后续派发
    engine = Engine()
    settings = {"base_url": "https://api.example.com/v1", "model": "test", "concurrency": 2}
    engine.start(task, "translate", settings, "mock_key")
    assert started.wait(5)
    engine.control(task, "pause")

    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic())

    cur = db.task(task)
    assert cur["status"] == "paused"
    subs = db.subtitles(task)
    translated_count = sum(bool(s["translation"]) for s in subs)
    assert 1 <= translated_count < 6  # 已发出的请求保存，未发出的停止

def test_engine_reduces_concurrency_and_recovers_after_throttle(client, task, monkeypatch):
    import backend.engine as engine_mod

    with db.connect(write=True) as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        for i in range(12):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"throttle_{i}",
                    task,
                    i * 1000,
                    i * 1000 + 800,
                    f"Text {i}",
                    f"Text {i}",
                    "en",
                    "fixture",
                ),
            )
        t = db.task(task)
        p = t["params"]
        p["context_mode"] = "independent"
        conn.execute("UPDATE tasks SET params=? WHERE id=?", (json.dumps(p), task))

    active = 0
    max_active = 0
    lock = __import__("threading").Lock()
    first = True

    def mock_translate(settings, key, batch, context, client=None, sleeper=None, on_retry=None):
        nonlocal active, max_active, first
        with lock:
            active += 1
            max_active = max(max_active, active)
            should_throttle = first
            first = False
        try:
            if should_throttle:
                error = TranslationError("API 限流：HTTP 429，Retry-After 1 秒")
                error.status_code = 429
                error.retry_after = 0.01
                if on_retry:
                    on_retry(error)
                raise error
            time.sleep(0.02)
            return {s["id"]: f"译_{s['source']}" for s in batch}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(engine_mod, "translate_batch", mock_translate)
    engine = Engine()
    engine.start(
        task,
        "translate",
        {"base_url": "https://api.example.com/v1", "model": "test", "concurrency": 8},
        "mock_key",
    )
    limit = time.monotonic() + 5
    assert engine.wait_idle(timeout=limit - time.monotonic())

    cur = db.task(task)
    assert cur["status"] == "complete"
    assert max_active <= 6
    assert cur["params"]["progress_detail"] == {
        "completed": 12,
        "translated": 12,
        "restricted": 0,
        "active_requests": 0,
        "concurrency": 6,
        "concurrency_ceiling": 16,
        "backoff_seconds": 0,
        "total": 12,
        "remaining": 0,
        "unit": "句",
    }
    assert all(s["translation"] for s in db.subtitles(task))

