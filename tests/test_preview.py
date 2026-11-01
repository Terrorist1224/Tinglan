import time
import pytest
from backend import db
from backend.config import DATA

H = {"X-Tinglan": "1"}


def test_preview_endpoints_and_isolation(client, task, monkeypatch):
    folder = DATA / task

    # 1. 验证 POST /run phase="preview" 被正确拦截
    res = client.post(f"/api/tasks/{task}/run", headers=H, json={"phase": "preview"})
    assert res.status_code == 400
    assert "生成预览" in res.json()["detail"]

    # 2. 初始状态检查
    st = client.get(f"/api/tasks/{task}/preview/status", headers=H).json()
    assert st["ready"] is False
    assert st["generating"] is False

    # 3. 模拟异步预览转码
    called = []
    def fake_preview(t):
        time.sleep(0.05)
        called.append(t["id"])
        (folder / "preview.mp4").write_bytes(b"dummy preview")

    monkeypatch.setattr("backend.media.preview", fake_preview)

    # 触发生成
    start_res = client.post(f"/api/tasks/{task}/preview", headers=H).json()
    assert start_res["ready"] is False
    assert start_res["generating"] is True

    # 等待转码完成
    for _ in range(20):
        time.sleep(0.02)
        st = client.get(f"/api/tasks/{task}/preview/status", headers=H).json()
        if st["ready"]:
            break

    assert st["ready"] is True
    assert st["generating"] is False
    assert (folder / "preview.mp4").exists()
    assert task in called

    # 再次请求应直接返回 ready
    res_ready = client.post(f"/api/tasks/{task}/preview", headers=H).json()
    assert res_ready["ready"] is True
    assert res_ready["generating"] is False

    # 4. 验证任务详情返回 has_preview 为 True
    detail = client.get(f"/api/tasks/{task}", headers=H).json()
    assert detail["has_preview"] is True

    # 5. 验证清理缓存后 preview.mp4 被删除且 status 重置
    del_cache = client.delete(f"/api/tasks/{task}/cache", headers=H)
    assert del_cache.status_code == 200
    assert not (folder / "preview.mp4").exists()
    st_after = client.get(f"/api/tasks/{task}/preview/status", headers=H).json()
    assert st_after["ready"] is False


def test_preview_does_not_affect_transcription_queue(client, task, monkeypatch):
    folder = DATA / task
    # 模拟任务正在排队转录
    db.update_task(task, status="queued", phase="transcribe", progress=0)

    def fake_preview(t):
        (folder / "preview.mp4").write_bytes(b"dummy preview")

    monkeypatch.setattr("backend.media.preview", fake_preview)

    # 在排队状态下触发生成预览
    client.post(f"/api/tasks/{task}/preview", headers=H)
    time.sleep(0.05)

    # 验证主任务依然保持 queued 和 transcribe 状态，未被更改
    t_after = db.task(task)
    assert t_after["status"] == "queued"
    assert t_after["phase"] == "transcribe"
