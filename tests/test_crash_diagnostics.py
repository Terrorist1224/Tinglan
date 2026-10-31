import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend import db
from backend.engine import Engine
from scripts import launch


@pytest.mark.parametrize("code,text", [(1, ""), (2, "ordinary error"), (-1073741819, ""), (3221225477, "x" * 2000)])
def test_worker_exit_reports_phase_code_and_log(task, monkeypatch, code, text):
    import backend.engine as module
    log = module.DATA / task / "diarize.log"

    class Process:
        stdout = []
        def wait(self):
            return code
        def poll(self):
            return code

    def popen(command, **kwargs):
        assert command[1:3] == ["-X", "faulthandler"]
        assert kwargs["stderr"].name == str(log)
        kwargs["stderr"].write(text)
        return Process()

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    engine = Engine()
    monkeypatch.setattr(module, "model_ready", lambda name: True)
    saved = db.subtitles(task)
    with pytest.raises(ValueError) as failure:
        engine.worker(db.task(task), "diarize", None)
    message = str(failure.value)
    assert message.startswith(f"diarize 子进程异常退出，退出码 {code} (0x{code & 0xffffffff:08X})")
    assert str(log) in message[:600]
    assert text[-1600:] in message if text else "日志为空" in message
    assert len(message) < 2000
    assert engine.process is None
    assert db.subtitles(task) == saved
    engine.lock.acquire()
    with pytest.raises(ValueError):
        engine.run(task, "diarize", {}, "", None, False)
    result = db.task(task)
    # 直接调用 run() 只负责抛出，状态由调度线程写入。
    assert result["status"] in ("ready", "failed")
    assert db.subtitles(task) == saved


def test_diagnostics_file_and_exception_hooks(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    enabled = []
    monkeypatch.setattr(launch.faulthandler, "enable", lambda **kw: enabled.append(kw))
    monkeypatch.setattr(sys, "excepthook", lambda *args: None)
    monkeypatch.setattr(launch.threading, "excepthook", lambda args: None)
    first = launch.init_diagnostics()
    try:
        assert enabled == [{"file": first, "all_threads": True}]
        try:
            raise RuntimeError("secret-request-and-subtitle")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
            from types import SimpleNamespace
            typ, value, tb = sys.exc_info()
            launch.threading.excepthook(SimpleNamespace(exc_type=typ, exc_value=value, exc_traceback=tb))
        content = Path(first.name).read_text(encoding="utf-8")
        assert f"PID: {os.getpid()}" in content
        assert sys.executable in content
        assert content.count("Unhandled RuntimeError") == 2
        assert "secret-request-and-subtitle" not in content
        second = launch.init_diagnostics()
        assert second.name != first.name
        second.close()
    finally:
        first.close()


def test_diagnostics_failure_continues(tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("fixture")
    monkeypatch.setattr(launch, "ROOT", blocked)
    assert launch.init_diagnostics() is None
    assert "继续启动服务" in capsys.readouterr().err


def test_diagnostics_faulthandler_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    def fail(**kwargs):
        raise OSError("fault target unavailable")
    monkeypatch.setattr(launch.faulthandler, "enable", fail)
    assert launch.init_diagnostics() is None
    assert "fault target unavailable" in capsys.readouterr().err


@pytest.mark.parametrize("fault", [False, True])
def test_isolated_crash_writes_readable_log(tmp_path, fault):
    script = """
import ctypes, faulthandler, sys
from pathlib import Path
from scripts import launch
if sys.platform == 'win32':
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
launch.ROOT = Path(sys.argv[1])
launch.init_diagnostics()
if sys.argv[2] == 'fault':
    faulthandler._sigsegv()
else:
    raise RuntimeError('private subtitle must not be logged')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), "fault" if fault else "exception"],
        cwd=launch.ROOT, capture_output=True, timeout=20,
    )
    assert result.returncode != 0
    logs = list(tmp_path.glob(".tmp/runtime/*/runtime.log"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    assert "PID:" in content
    assert "<string>" in content
    assert "private subtitle" not in content
    if not fault:
        assert "Unhandled RuntimeError" in content
    else:
        assert "Windows fatal exception" in content or "Fatal Python error" in content



def test_scheduler_records_worker_failure(task, monkeypatch):
    import backend.engine as module
    engine = Engine()
    monkeypatch.setattr(module.Engine, "worker", lambda self, task, phase, ids: (_ for _ in ()).throw(ValueError("diarize failed code 1")))
    monkeypatch.setattr(module, "model_ready", lambda name: True)
    assert engine.start(task, "diarize", {}, "", None, False)["status"] == "started"
    assert engine.wait_idle(timeout=5)
    result = db.task(task)
    assert result["status"] == "failed"
    assert "diarize failed code 1" in result["error"]

