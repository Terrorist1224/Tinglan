import struct
import wave
import pytest
from fastapi.testclient import TestClient
from backend.config import ROOT, DATA
from backend.media import audio, get_media_path, run, FFMPEG

H = {"X-Tinglan": "1"}


@pytest.fixture(scope="module")
def samples():
    folder = ROOT / ".tmp" / "media-fixtures"
    folder.mkdir(exist_ok=True)
    speech = ROOT / ".tmp" / "samples" / "english.wav"
    if not speech.exists():
        pytest.skip("先运行 scripts/make_sample.ps1 生成语音样片")
    multi = folder / "多音轨测试.mkv"
    run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:r=5:d=17",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=17",
            "-itsoffset",
            "2",
            "-i",
            str(speech),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "flac",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata:s:a:1",
            "language=jpn",
            "-disposition:a:0",
            "0",
            "-disposition:a:1",
            "default",
            str(multi),
        ]
    )
    silent = folder / "silent.mp4"
    run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:d=0.2",
            "-an",
            str(silent),
        ]
    )
    return multi, silent


def test_multitrack_upload_default_and_timeline(client, samples):
    multi, _ = samples
    before = multi.read_bytes()
    response = client.post(
        "/api/tasks",
        headers=H,
        files={"file": ("中文视频.mkv", before, "video/x-matroska")},
    )
    assert response.status_code == 200, response.text
    task = response.json()
    assert len(task["probe"]["audio"]) == 2 and task["params"]["audio_track"] == 2
    assert task["probe"]["audio"][1]["language"] == "jpn"
    output = DATA / task["id"] / "timeline.wav"
    audio(task, output)
    with wave.open(str(output)) as wav:
        assert wav.getframerate() == 16000
        first = wav.readframes(16000)
        assert (
            max(abs(x) for x in struct.unpack("<" + "h" * (len(first) // 2), first))
            < 10
        )
        rest = wav.readframes(80000)
        assert (
            max(abs(x) for x in struct.unpack("<" + "h" * (len(rest) // 2), rest)) > 100
        )
    assert multi.read_bytes() == before


def test_no_audio_corrupt_and_invalid_extension_leave_no_task(client, samples):
    _, silent = samples
    for name, content in [
        ("silent.mp4", silent.read_bytes()),
        ("broken.mp4", b"not a video"),
        ("unsupported.exe", b"anything"),
    ]:
        assert (
            client.post(
                "/api/tasks", headers=H, files={"file": (name, content)}
            ).status_code
            == 400
        )
    assert client.get("/api/tasks").json() == []


def test_audio_only_probe_and_extraction(client):
    source = ROOT / ".tmp" / "samples" / "english.wav"
    if not source.exists():
        pytest.skip("缺少语音样片")
    response = client.post(
        "/api/tasks",
        headers=H,
        files={"file": ("audio.wav", source.read_bytes(), "audio/wav")},
    )
    assert response.status_code == 200
    task = response.json()
    assert task["probe"]["video"] is None
    assert (
        client.get(
            f"/api/tasks/{task['id']}/media", headers={"Range": "bytes=0-99"}
        ).status_code
        == 206
    )


def test_disk_full_mid_upload_preserves_existing_task(client, task, monkeypatch):
    import backend.main as main
    from backend import db

    before = set(DATA.iterdir())
    existing_rows = db.subtitles(task)
    checks = []

    def space(size):
        checks.append(size)
        if len(checks) == 2:
            raise ValueError("磁盘空间不足，请先清理缓存或腾出空间")

    monkeypatch.setattr(main, "ensure_space", space)
    response = client.post(
        "/api/tasks",
        headers=H,
        files={"file": ("disk-full.wav", b"x" * (4 * 1024**2 + 1), "audio/wav")},
    )
    assert response.status_code == 400
    assert len(checks) == 2
    assert set(DATA.iterdir()) == before
    assert db.subtitles(task) == existing_rows
    assert (DATA / task / "source.mp4").read_bytes() == b"fixture"
    assert [t["id"] for t in client.get("/api/tasks").json()] == [task]


def test_import_path_reads_source_without_copy_and_delete_keeps_source(client, samples):
    multi, _ = samples
    before = multi.read_bytes()
    response = client.post(
        "/api/tasks/import-path",
        headers=H,
        json={"path": f'  "{multi}"  '},
    )
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["media"] == str(multi.resolve())
    assert task["probe"]["duration"] > 0
    folder = DATA / task["id"]
    assert list(folder.iterdir()) == []
    assert get_media_path(task) == multi.resolve()
    missing_task = {**task, "media": str(multi.with_name("missing.mkv"))}
    with pytest.raises(ValueError, match="源文件未找到"):
        get_media_path(missing_task)
    ranged = client.get(
        f"/api/tasks/{task['id']}/media", headers={"Range": "bytes=0-99"}
    )
    assert ranged.status_code == 206 and ranged.headers["content-range"].startswith(
        "bytes 0-99/"
    )

    output = DATA / task["id"] / "import-path.wav"
    audio(task, output, 0, 1)
    with wave.open(str(output)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            16000,
        )

    assert client.delete(f"/api/tasks/{task['id']}", headers=H).status_code == 200
    assert multi.read_bytes() == before
    assert not folder.exists()


def test_pick_file_cancel_and_unavailable_tk(client, monkeypatch):
    import subprocess
    import backend.main as main
    from types import SimpleNamespace

    # 1. 模拟用户取消选择
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"path":"","paths":[]}', stderr=""),
    )
    response = client.post("/api/tasks/pick-file", headers=H)
    assert response.status_code == 200 and response.json() == {"path": "", "paths": []}

    # 2. 模拟子进程崩溃（如 0xC0000005 访问冲突退出码）
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=3221225477, stdout="", stderr="Access violation in wetype_tip.dll"),
    )
    response = client.post("/api/tasks/pick-file", headers=H)
    assert response.status_code == 400
    assert "直接输入本地路径" in response.text

    # 3. 模拟重复调用时互斥锁防护
    main.pick_dialog_lock.acquire()
    try:
        response = client.post("/api/tasks/pick-file", headers=H)
        assert response.status_code == 400
        assert "已有正在打开的文件选择窗口" in response.text
    finally:
        main.pick_dialog_lock.release()

    # 4. 验证独立子进程脚本自身的 Tkinter 逻辑
    import tkinter as tk
    import scripts.pick_dialog as pick_dialog

    class Root:
        destroyed = False
        def withdraw(self): pass
        def attributes(self, *_args): pass
        def update(self): pass
        def destroy(self): self.destroyed = True

    root = Root()
    monkeypatch.setattr(tk, "Tk", lambda: root)
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", lambda **_kwargs: "D:/test.mp4")
    monkeypatch.setattr("sys.argv", ["pick_dialog.py"])
    import io, json
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    pick_dialog.main()
    assert root.destroyed
    assert json.loads(out.getvalue()) == {"path": "D:/test.mp4", "paths": ["D:/test.mp4"]}


def test_import_path_rejects_missing_directory_and_unsupported_file(client, tmp_path):
    bad = tmp_path / "bad.exe"
    bad.write_bytes(b"anything")
    for path in (tmp_path / "missing.mp4", tmp_path, bad):
        response = client.post(
            "/api/tasks/import-path", headers=H, json={"path": str(path)}
        )
        assert response.status_code == 400
    assert (
        client.post(
            "/api/tasks/import-path", headers=H, json={"path": "relative.mp4"}
        ).status_code
        == 400
    )
    assert client.get("/api/tasks").json() == []
