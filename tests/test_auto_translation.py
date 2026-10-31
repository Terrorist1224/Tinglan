import copy
import json
import threading
import time
from collections import Counter

import httpx
import pytest

from backend import db
import backend.engine as engine_module
from backend.engine import AUTO_CONCURRENCY_CEILING, Engine
from backend.translation import TranslationError, translate_batch

SETTINGS = {"base_url": "https://example.invalid/v1", "model": "fixture", "concurrency": "ignored"}


def seed(task, count, independent=False):
    with db.connect(write=True) as conn:
        conn.execute("DELETE FROM subtitles WHERE task_id=?", (task,))
        for i in range(count):
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original) VALUES(?,?,?,?,?,?)",
                (f"r{i}", task, i * 1000, i * 1000 + 800, f"Source {i}", f"Source {i}"),
            )
    params = db.task(task)["params"]
    params["context_mode"] = "independent" if independent else "batch"
    db.update_task(task, params=params)


def finish(engine):
    limit = time.monotonic() + 10
    assert engine.wait_idle(timeout=limit - time.monotonic()), "task did not finish"


def record_progress(monkeypatch):
    records = []
    original = db.update_task

    def update(task_id, **fields):
        detail = fields.get("params", {}).get("progress_detail")
        if detail:
            records.append(copy.deepcopy(detail))
        return original(task_id, **fields)

    monkeypatch.setattr(db, "update_task", update)
    return records


@pytest.mark.parametrize("restricted", [{"r3"}, {"r0", "r3", "r7"}])
def test_refusal_bisection_preserves_successes_and_order(task, monkeypatch, restricted):
    seed(task, 8)
    calls = []

    def translate(settings, key, batch, context):
        ids = [s["id"] for s in batch]
        calls.append(ids)
        if restricted.intersection(ids):
            raise TranslationError("content refused", refusal=True)
        return {sid: "Translated " + sid for sid in ids}

    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine = Engine()
    engine.start(task, "translate", SETTINGS, "key")
    finish(engine)
    current = db.task(task)
    assert current["status"] == "complete"
    assert f"有 {len(restricted)} 句" in current["error"]
    assert calls[0] == [f"r{i}" for i in range(8)]
    for batch in calls:
        indices = [int(sid[1:]) for sid in batch]
        assert indices == list(range(indices[0], indices[-1] + 1))
    singles = Counter(batch[0] for batch in calls if len(batch) == 1)
    assert all(singles[sid] == 2 for sid in restricted)
    rows = db.subtitles(task)
    assert {r["id"] for r in rows if not r["translation"]} == restricted
    detail = current["params"]["progress_detail"]
    assert detail["completed"] == 8
    assert detail["translated"] == 8 - len(restricted)
    assert detail["restricted"] == len(restricted)
    assert detail["remaining"] == 0
    assert detail["active_requests"] == 0
    assert detail["concurrency_ceiling"] == AUTO_CONCURRENCY_CEILING == 16
    assert set(current["params"]["operation"]["restricted_ids"]) == restricted
    assert "concurrency" not in current["params"]["operation"]["config_snapshot"]


@pytest.mark.parametrize("independent", [False, True])
def test_single_refusal_retry_keeps_original_context_and_is_bounded(task, monkeypatch, independent):
    seed(task, 3, independent)
    contexts = []

    def translate(settings, key, batch, context):
        contexts.append(copy.deepcopy(context))
        raise TranslationError("refused", refusal=True)

    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine = Engine()
    engine.start(task, "translate", SETTINGS, "key", ids=["r1"])
    finish(engine)
    assert len(contexts) == 2
    assert contexts[0] == contexts[1]
    assert bool(contexts[0]) is (not independent)
    assert db.task(task)["params"]["progress_detail"]["restricted"] == 1


@pytest.mark.parametrize("error", [TranslationError("connection failed"), TranslationError("invalid JSON")])
def test_technical_errors_do_not_split_or_mark_restricted(task, monkeypatch, error):
    seed(task, 8)
    calls = []

    def translate(settings, key, batch, context):
        calls.append(len(batch))
        raise error

    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine = Engine()
    engine.start(task, "translate", SETTINGS, "key")
    finish(engine)
    current = db.task(task)
    assert calls == [8]
    assert current["status"] == "failed"
    assert str(error) in current["error"]
    assert current["params"]["progress_detail"]["restricted"] == 0
    assert len(current["params"]["operation"]["ids"]) == 8


@pytest.mark.parametrize("status,drop", [(429, 2), (500, 0), (502, 1), (503, 1), (504, 1)])
def test_throttle_backoff_then_gradual_recovery(task, monkeypatch, status, drop):
    seed(task, 12, independent=True)
    records = record_progress(monkeypatch)
    calls = []
    first = threading.Event()
    first.set()
    lock = threading.Lock()
    throttle_at = 0
    retried_at = 0

    def translate(settings, key, batch, context):
        nonlocal throttle_at, retried_at
        with lock:
            fail = first.is_set()
            if fail:
                first.clear()
                throttle_at = time.monotonic()
                failing_id = batch[0]["id"]
                calls.append(failing_id)
            elif batch[0]["id"] == calls[0]:
                retried_at = time.monotonic()
        if fail:
            raise TranslationError("temporary", status_code=status, retry_after=1)
        time.sleep(0.02)
        return {s["id"]: "译文" for s in batch}

    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine = Engine()
    engine.start(task, "translate", SETTINGS, "key")
    finish(engine)
    assert db.task(task)["status"] == "complete"
    assert retried_at - throttle_at >= 1
    assert any(r["concurrency"] == 6 - drop and r["backoff_seconds"] > 0 for r in records)
    previous = records[0]
    for record in records[1:]:
        assert record["concurrency"] <= 16
        if record["concurrency"] > previous["concurrency"]:
            assert record["concurrency"] == previous["concurrency"] + 1
        previous = record
    if drop:
        assert any(6 - drop < r["concurrency"] <= 6 for r in records if r["completed"] >= 6)
    assert records[-1]["restricted"] == 0
    assert records[-1]["backoff_seconds"] == 0


def test_persistent_throttle_stops_with_technical_failure(task, monkeypatch):
    seed(task, 1)
    calls = []

    def translate(settings, key, batch, context):
        calls.append(len(batch))
        raise TranslationError("HTTP 429", status_code=429, retry_after=0.01)

    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine = Engine()
    engine.start(task, "translate", SETTINGS, "key")
    finish(engine)
    assert calls == [1, 1, 1]
    assert db.task(task)["status"] == "failed"
    assert db.task(task)["params"]["progress_detail"]["restricted"] == 0


def test_resume_skips_saved_success_and_restriction_but_new_operation_retries(task, monkeypatch):
    seed(task, 6, independent=True)
    engine = Engine()
    calls = []

    def translate(settings, key, batch, context):
        sid = batch[0]["id"]
        calls.append(sid)
        if sid == "r0":
            raise TranslationError("refused", refusal=True)
        if sid == "r1":
            limit = time.monotonic() + 5
            while "r0" not in db.task(task)["params"]["operation"].get("restricted_ids", []):
                assert time.monotonic() < limit
                time.sleep(0.01)
            engine.control(task, "pause")
        return {sid: "译文"}

    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine.start(task, "translate", SETTINGS, "key")
    finish(engine)
    current = db.task(task)
    assert current["status"] == "paused"
    done = {r["id"] for r in db.subtitles(task) if r["translation"]} | {"r0"}
    calls.clear()
    engine.start(task, "translate", SETTINGS, "key", resume=True)
    finish(engine)
    assert not done.intersection(calls)
    assert db.task(task)["status"] == "complete"
    assert db.task(task)["params"]["progress_detail"]["total"] == 6
    assert db.task(task)["params"]["progress_detail"]["restricted"] == 1
    calls.clear()
    engine.start(task, "translate", SETTINGS, "key")
    finish(engine)
    assert calls == ["r0", "r0"]


def test_new_run_replaces_old_operation_scope(task, monkeypatch):
    seed(task, 8, independent=True)
    first = True

    def translate(settings, key, batch, context):
        nonlocal first
        if first:
            first = False
            return {s["id"]: "译文" for s in batch if s["id"] != "r1"}
        return {s["id"]: "译文" for s in batch}

    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine = Engine()
    engine.start(task, "translate", SETTINGS, "key", ids=["r0", "r1"])
    finish(engine)
    engine.start(task, "translate", SETTINGS, "key", ids=["r2", "r3"])
    finish(engine)
    current = db.task(task)
    detail = current["params"]["progress_detail"]
    assert detail["completed"] == detail["total"] == 2
    assert set(current["params"]["operation"]["target_ids"]) == {"r2", "r3"}
    engine.start(task, "translate", SETTINGS, "key")
    finish(engine)
    current = db.task(task)
    detail = current["params"]["progress_detail"]
    assert detail["completed"] == detail["total"] == len(db.subtitles(task))
    assert len(current["params"]["operation"]["target_ids"]) == len(db.subtitles(task))


def test_polish_refusal_falls_back_then_splits_translation(task, monkeypatch):
    seed(task, 4)
    seen = []

    def polish(*args, **kwargs):
        raise TranslationError("polish refused", refusal=True)

    def translate(settings, key, batch, context):
        seen.extend(s["source"] for s in batch)
        if any(s["id"] == "r2" for s in batch):
            raise TranslationError("translate refused", refusal=True)
        return {s["id"]: "译文" for s in batch}

    monkeypatch.setattr(engine_module, "polish_batch", polish)
    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine = Engine()
    engine.start(task, "polish_translate", SETTINGS, "key")
    finish(engine)
    assert all(source.startswith("Source ") for source in seen)
    assert db.task(task)["status"] == "complete"
    assert "需要人工翻译" in db.task(task)["error"]
    assert db.task(task)["params"]["progress_detail"]["restricted"] == 1


def test_polish_translate_resume_does_not_repeat_finished_stages(task, monkeypatch):
    seed(task, 4, independent=True)
    engine = Engine()
    polished = []

    def polish(settings, key, batch, context):
        polished.extend(s["id"] for s in batch)
        engine.control(task, "pause")
        return {s["id"]: "Polished " + s["source"] for s in batch}

    monkeypatch.setattr(engine_module, "polish_batch", polish)
    monkeypatch.setattr(engine_module, "translate_batch", lambda settings, key, batch, context: {s["id"]: "译文" for s in batch})
    engine.start(task, "polish_translate", SETTINGS, "key")
    finish(engine)
    saved = set(db.task(task)["params"]["operation"]["polished_ids"])
    assert saved
    assert db.task(task)["status"] == "paused"
    polished.clear()
    monkeypatch.setattr(engine_module, "polish_batch", lambda settings, key, batch, context: polished.extend(s["id"] for s in batch) or {s["id"]: "Polished " + s["source"] for s in batch})
    engine.start(task, "polish_translate", SETTINGS, "key", resume=True)
    finish(engine)
    assert not saved.intersection(polished)
    assert db.task(task)["status"] == "complete"
    assert all(s["translation"] for s in db.subtitles(task))


def test_actual_http_request_counts_and_task_api(task, client, monkeypatch):
    seed(task, 4, independent=True)
    gate = threading.Event()
    entered = threading.Event()
    started = 0
    lock = threading.Lock()
    original_client = httpx.Client

    def handler(req):
        nonlocal started
        with lock:
            started += 1
            if started == 4:
                entered.set()
        assert gate.wait(5)
        rows = json.loads(json.loads(req.content)["messages"][1]["content"])["subtitles"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps([{"id": r["id"], "text": "译文"} for r in rows])}}]})

    monkeypatch.setattr("backend.translation.httpx.Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    engine = Engine()
    engine.start(task, "translate", SETTINGS, "key")
    try:
        assert entered.wait(5)
        detail = client.get(f"/api/tasks/{task}").json()["params"]["progress_detail"]
        assert detail == dict(completed=0, translated=0, restricted=0, total=4, remaining=4,
                             active_requests=4, concurrency=4, concurrency_ceiling=16, backoff_seconds=0, unit="句")
    finally:
        gate.set()
        finish(engine)
    assert db.task(task)["params"]["progress_detail"]["active_requests"] == 0
    assert db.task(task)["status"] == "complete"


@pytest.mark.parametrize("status,payload", [(401, {}), (403, {}), (402, {}), (429, {"error": {"code": "insufficient_quota"}})])
def test_auth_permissions_and_billing_stop_immediately(status, payload):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermissionError):
            translate_batch(SETTINGS, "key", [{"id": "a", "source": "x"}], [], client=client)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_scheduler_receives_http_pressure_without_internal_sleep(status):
    calls = []
    sleeps = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, headers={"Retry-After": "120"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TranslationError) as exc:
            translate_batch(SETTINGS, "key", [{"id": "a", "source": "x"}], [], client=client, sleeper=sleeps.append, allow_retry=False)
    assert len(calls) == 1
    assert not sleeps
    assert not exc.value.refusal
    assert exc.value.status_code == status
    if status == 429:
        assert exc.value.retry_after == 120


def test_large_task_ignores_legacy_ceiling_and_never_exceeds_16(task, monkeypatch):
    seed(task, 130, independent=True)
    peak = active = 0
    lock = threading.Lock()
    def translate(settings, key, batch, context):
        nonlocal peak, active
        assert "concurrency" not in settings
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            return {s["id"]: "译文" for s in batch}
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine = Engine()
    engine.start(task, "translate", {**SETTINGS, "concurrency": 1}, "key")
    finish(engine)
    assert peak == 16
    assert db.task(task)["params"]["progress_detail"]["concurrency"] == 16
    assert db.task(task)["status"] == "complete"


def test_http_400_content_refusal_does_not_use_size_split():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(400, json={"error": {"code": "content_policy_violation"}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TranslationError) as exc:
            translate_batch(SETTINGS, "key", [{"id": "a", "source": "x"}, {"id": "b", "source": "y"}], [], client=client)
    assert exc.value.refusal
    assert len(calls) == 1


def test_resume_retranslates_source_edited_after_previous_success(task, monkeypatch):
    seed(task, 3, independent=True)
    failed = False
    calls = []
    def translate(settings, key, batch, context):
        nonlocal failed
        sid = batch[0]["id"]
        calls.append(sid)
        if sid == "r1" and not failed:
            failed = True
            raise TranslationError("network error")
        return {sid: "译文 " + batch[0]["source"]}
    monkeypatch.setattr(engine_module, "translate_batch", translate)
    engine = Engine()
    engine.start(task, "translate", SETTINGS, "key")
    finish(engine)
    assert db.task(task)["status"] == "failed"
    with db.connect(write=True) as conn:
        conn.execute("UPDATE subtitles SET source='Edited',source_version=source_version+1,version=version+1 WHERE id='r0' AND task_id=?", (task,))
    calls.clear()
    engine.start(task, "translate", SETTINGS, "key", resume=True)
    finish(engine)
    assert set(calls) == {"r0", "r1"}
    assert db.task(task)["status"] == "complete"
    assert db.subtitles(task)[0]["translation"] == "译文 Edited"


