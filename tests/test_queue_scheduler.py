from backend import cloud_processing
import json
import time

import pytest

from backend import db
import backend.engine as engine_module
from backend.engine import Engine

H = {"X-Tinglan": "1"}


def insert_task(name, subtitles=True, params=None):
    task_id = db.ident()
    folder = engine_module.DATA / task_id
    folder.mkdir()
    now = time.time()
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO tasks(id,name,media,probe,params,created,updated) VALUES(?,?,?,?,?,?,?)",
            (
                task_id,
                name,
                "source.mp4",
                json.dumps({"duration": 30, "audio": [{"index": 0, "default": True}], "subtitles": [], "video": None}),
                json.dumps(params or {"audio_track": 0, "device": "cpu", "language": "en"}),
                now,
                now,
            ),
        )
        if subtitles:
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",
                (db.ident(), task_id, 0, 1000, "hello", "hello", "en", "fixture"),
            )
    return task_id


def test_settings_defaults_and_new_task_snapshot(client, monkeypatch):
    from backend.routes import tasks as main
    monkeypatch.setattr(main, "probe", lambda path: {"duration": 1, "audio": [{"index": 0, "default": True}], "subtitles": [], "video": None})
    assert client.get("/api/settings").json()["default_diarize"] is True
    assert client.get("/api/settings").json()["default_auto_polish_translate"] is False
    res = client.put("/api/settings", headers=H, json={
        "base_url": "https://api.example.com/v1",
        "model": "test",
        "default_diarize": False,
        "default_auto_polish_translate": True,
    })
    assert res.status_code == 200
    uploaded = client.post("/api/tasks", headers=H, files={"file": ("snapshot.mp4", b"fixture", "video/mp4")}, data={"default_diarize": "false", "default_auto_polish_translate": "true"})
    assert uploaded.status_code == 200
    params = uploaded.json()["params"]
    assert params["diarize"] is False
    assert params["auto_polish_translate"] is True


def test_queue_fifo_by_start_request(task, monkeypatch):
    import backend.engine as mod
    a, b, c = task, insert_task("B"), insert_task("C")
    seen = []
    gate = __import__("threading").Event()

    def translate(settings, key, batch, context):
        seen.append("start:" + batch[0]["source"])
        gate.wait(5)
        return {r["id"]: "ok" for r in batch}

    monkeypatch.setattr(cloud_processing, "translate_batch", translate)
    engine = Engine()
    settings = {"base_url": "https://example.invalid/v1", "model": "test"}
    assert engine.start(a, "translate", settings, "key")["status"] == "started"
    assert engine.start(b, "translate", settings, "key")["position"] == 1
    assert engine.start(c, "translate", settings, "key")["position"] == 2
    assert engine.start(b, "translate", settings, "key")["status"] == "queued"
    assert engine.queue_state(c)["queue_ahead"] == 1
    gate.set()
    assert engine.wait_idle(timeout=5)


def test_two_channels_can_run_different_tasks(task, monkeypatch):
    import backend.engine as mod
    local_task = insert_task("local", subtitles=False)
    cloud_task = task
    phases = []
    gate = __import__("threading").Event()

    def fake_worker(t, phase, ids):
        phases.append(phase)
        gate.wait(5)

    def translate(settings, key, batch, context):
        phases.append("cloud")
        gate.wait(5)
        return {r["id"]: "ok" for r in batch}
    engine = Engine()

    monkeypatch.setattr(engine, "worker", fake_worker)
    monkeypatch.setattr(cloud_processing, "translate_batch", translate)
    assert engine.start(local_task, "transcribe")["channel"] == "local"
    assert engine.start(cloud_task, "translate", {"base_url": "https://example.invalid/v1", "model": "test"}, "key")["channel"] == "cloud"
    assert engine.channels["local"].active == local_task
    assert engine.channels["cloud"].active == cloud_task
    gate.set()
    assert engine.wait_idle(timeout=5)


def test_queued_task_control_removes_only_that_task(task, monkeypatch):
    import threading
    b, c = insert_task("B"), insert_task("C")
    gate = threading.Event()

    def translate(settings, key, batch, context):
        gate.wait(5)
        return {r["id"]: "ok" for r in batch}

    monkeypatch.setattr(cloud_processing, "translate_batch", translate)
    engine = Engine()
    settings = {"base_url": "https://example.invalid/v1", "model": "test"}
    engine.start(task, "translate", settings, "key")
    engine.start(b, "translate", settings, "key")
    engine.start(c, "translate", settings, "key")
    assert engine.control(b, "pause")["status"] == "queued_removed"
    assert db.task(b)["status"] == "paused"
    assert engine.queue_state(c)["queue_ahead"] == 0
    gate.set()
    assert engine.wait_idle(timeout=5)


def test_restart_marks_queued_and_running_interrupted(client):
    running = insert_task("running", subtitles=False)
    queued = insert_task("queued", subtitles=False)
    db.update_task(running, status="running")
    db.update_task(queued, status="queued")
    db.init()
    assert db.task(running)["status"] == "interrupted"
    assert db.task(queued)["status"] == "interrupted"



def test_transcribe_triggers_auto_cloud_and_next_local(task, monkeypatch):
    import threading
    local_a = insert_task("A", subtitles=False, params={"audio_track": 0, "device": "cpu", "language": "en"})
    db.update_task(local_a, params={**db.task(local_a)["params"], "diarize": False, "auto_polish_translate": True})
    local_b = insert_task("B", subtitles=False)
    gate = threading.Event()
    cloud_started = threading.Event()
    phases = []
    monkeypatch.setattr(engine_module, "model_ready", lambda name: True)
    monkeypatch.setattr(engine_module.Engine, "resolve_settings", lambda self: ({"base_url": "https://example.invalid/v1", "model": "test"}, "key"))

    def fake_worker(self, task, phase, ids):
        phases.append(("local", task["name"], phase))
        if phase == "transcribe" and not db.subtitles(task["id"]):
            with db.connect(write=True) as conn:
                conn.execute("INSERT INTO subtitles(id,task_id,start,end,source,original) VALUES(?,?,?,?,?,?)", (db.ident(), task["id"], 0, 1000, "hello", "hello"))

    def fake_translate(settings, key, batch, context):
        cloud_started.set()
        phases.append(("cloud", "start", batch[0]["id"]))
        gate.wait(5)
        return {r["id"]: "ok" for r in batch}

    monkeypatch.setattr(engine_module.Engine, "worker", fake_worker)
    def fake_process(self, task, phase, settings, key, ids, overwrite):
        cloud_started.set()
        phases.append(("cloud", "start", phase))
        gate.wait(5)
    monkeypatch.setattr(engine_module.Engine, "process_llm", fake_process)
    engine = Engine()
    assert engine.start(local_a, "transcribe")["status"] == "started"
    assert engine.start(local_b, "transcribe")["status"] == "queued"
    time.sleep(0.1)
    assert cloud_started.wait(5)
    gate.set()
    assert engine.wait_idle(timeout=5)
    local_names = [p[1] for p in phases if p[0] == "local"]
    assert local_names[:2] == ["A", "B"]
    assert local_names.count("A") == 1
    assert any(p[0] == "cloud" for p in phases)



















def test_five_tasks_queue_rank_lifecycle(monkeypatch):
    import threading
    t1 = insert_task("T1", subtitles=False, params={"audio_track": 0, "device": "cpu", "language": "en", "diarize": False, "auto_polish_translate": True})
    t2 = insert_task("T2", subtitles=False, params={"audio_track": 0, "device": "cpu", "language": "en", "diarize": False, "auto_polish_translate": False})
    t3 = insert_task("T3", subtitles=False)
    t4 = insert_task("T4", subtitles=False)
    t5 = insert_task("T5", subtitles=False)

    t1_local_gate = threading.Event()
    t1_cloud_gate = threading.Event()
    t1_cloud_started = threading.Event()
    t1_local_done = threading.Event()
    t2_local_gate = threading.Event()

    monkeypatch.setattr(engine_module, "model_ready", lambda name: True)
    monkeypatch.setattr(engine_module.Engine, "resolve_settings", lambda self: ({"base_url": "https://example.invalid/v1", "model": "test"}, "key"))

    def fake_worker(self, task, phase, ids):
        if task["id"] == t1:
            assert t1_local_gate.wait(5)
            if not db.subtitles(t1):
                with db.connect(write=True) as conn:
                    conn.execute("INSERT INTO subtitles(id,task_id,start,end,source,original) VALUES(?,?,?,?,?,?)", (db.ident(), t1, 0, 1000, "hello", "hello"))
            t1_local_done.set()
        elif task["id"] == t2:
            t2_local_gate.wait(5)

    def fake_process(self, task, phase, settings, key, ids, overwrite):
        if task["id"] == t1:
            t1_cloud_started.set()
            t1_cloud_gate.wait(5)

    monkeypatch.setattr(engine_module.Engine, "worker", fake_worker)
    monkeypatch.setattr(engine_module.Engine, "process_llm", fake_process)

    engine = Engine()

    # 1. 依次将 5 个任务入队
    assert engine.start(t1, "transcribe")["status"] == "started"
    assert engine.start(t2, "transcribe")["status"] == "queued"
    assert engine.start(t3, "transcribe")["status"] == "queued"
    assert engine.start(t4, "transcribe")["status"] == "queued"
    assert engine.start(t5, "transcribe")["status"] == "queued"

    # 初始状态：T1 正在转录(rank=1)，T2~T5 排队中(rank=2,3,4,5)
    assert engine.queue_state(t1)["queue_rank"] == 1
    assert engine.queue_state(t2)["queue_rank"] == 2
    assert engine.queue_state(t3)["queue_rank"] == 3
    assert engine.queue_state(t4)["queue_rank"] == 4
    assert engine.queue_state(t5)["queue_rank"] == 5

    # 等待 T1 本地转录完成并转入云端翻译
    t1_local_gate.set()
    assert t1_local_done.wait(5)
    assert t1_cloud_started.wait(5)

    # T1 进入云端处理(rank仍为1)，T2 开始转录(rank仍为2)，T3~T5 保持 3, 4, 5
    assert engine.queue_state(t1)["queue_rank"] == 1
    assert engine.queue_state(t1)["queue_channel"] == "cloud"
    assert engine.queue_state(t2)["queue_rank"] == 2
    assert engine.queue_state(t2)["queue_channel"] == "local"
    assert engine.queue_state(t3)["queue_rank"] == 3
    assert engine.queue_state(t4)["queue_rank"] == 4
    assert engine.queue_state(t5)["queue_rank"] == 5

    # 放行 T1 云端完成
    t1_cloud_gate.set()
    assert engine.wait_idle(channel="cloud", timeout=5)

    # T1 全部结束：T1 退出编号(rank=None)，T2 成为 1，T3 成为 2，T4 成为 3，T5 成为 4
    assert engine.queue_state(t1)["queue_rank"] is None
    assert engine.queue_state(t2)["queue_rank"] == 1
    assert engine.queue_state(t3)["queue_rank"] == 2
    assert engine.queue_state(t4)["queue_rank"] == 3
    assert engine.queue_state(t5)["queue_rank"] == 4

    # 暂停 T3：T3 退出编号，后续 T4 顺延变为 2，T5 变为 3
    assert engine.control(t3, "pause")["status"] == "queued_removed"
    assert engine.queue_state(t3)["queue_rank"] is None
    assert engine.queue_state(t4)["queue_rank"] == 2
    assert engine.queue_state(t5)["queue_rank"] == 3

    # T3 恢复排队：加到末尾，成为 4
    assert engine.start(t3, "transcribe")["status"] == "queued"
    assert engine.queue_state(t4)["queue_rank"] == 2
    assert engine.queue_state(t5)["queue_rank"] == 3
    assert engine.queue_state(t3)["queue_rank"] == 4

    # 放行 T2 并等待全部空闲
    t2_local_gate.set()
    # 清理剩余队列
    engine.control(t4, "cancel")
    engine.control(t5, "cancel")
    engine.control(t3, "cancel")
    assert engine.wait_idle(timeout=5)


def test_local_cloud_handoff_does_not_temporarily_drop_queue_rank(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    task = insert_task("handoff", subtitles=True, params={"audio_track": 0, "device": "cpu", "language": "en", "diarize": False, "auto_polish_translate": True})
    reading = threading.Event()
    release_settings = threading.Event()
    release_cloud = threading.Event()
    observing = threading.Event()
    observed = threading.Event()
    def worker(self, item, phase, ids):
        with db.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO subtitles(id,task_id,start,end,source,original) VALUES(?,?,?,?,?,?)",
                (db.ident(), item["id"], 0, 1000, "hello", "hello"),
            )

    monkeypatch.setattr(engine_module.Engine, "worker", worker)
    monkeypatch.setattr(engine_module.Engine, "process_llm", lambda *args: release_cloud.wait(5))

    def settings(self):
        reading.set()
        assert release_settings.wait(5)
        return {"base_url": "https://example.invalid/v1", "model": "test"}, "key"

    monkeypatch.setattr(engine_module.Engine, "resolve_settings", settings)
    engine = Engine()

    def observe():
        observing.set()
        state = engine.queue_state(task)
        observed.set()
        return state

    with ThreadPoolExecutor(1) as pool:
        try:
            engine.start(task, "transcribe")
            assert reading.wait(5)
            future = pool.submit(observe)
            assert observing.wait(5)
            assert not observed.wait(0.05)
            release_settings.set()
            state = future.result(timeout=5)
            assert state["queue_rank"] == 1
            assert state["queue_channel"] == "cloud"
        finally:
            release_settings.set()
            release_cloud.set()
            assert engine.wait_idle(timeout=5)
