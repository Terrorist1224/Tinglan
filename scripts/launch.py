# 听澜桌面服务启动脚本：
# 1. Windows 显式传入 Selector 循环工厂：Uvicorn 0.54 会绕过 asyncio 全局策略；
# 2. 预绑定套接字：检测并占用 8765 端口，防止端口竞态与重复启动；
# 3. 绕过系统代理：通过 ProxyHandler({}) 直连本机 API 探测服务就绪并自动唤起浏览器。
import faulthandler
import os
import traceback
from datetime import datetime
import socket
import sys
import threading
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

LOOP = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"


def server_config(app, port):
    return uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_level="warning",
        loop=LOOP,
    )


def init_diagnostics():
    """Keep the fault file open for the lifetime of the interpreter."""
    log_file = None
    try:
        runtime = ROOT / ".tmp" / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(
            prefix=f"{datetime.now():%Y%m%d-%H%M%S-%f}-{os.getpid()}-", dir=runtime,
        ))
        log_file = (folder / "runtime.log").open("a", encoding="utf-8", buffering=1)
        log_file.write(
            f"Started: {datetime.now().astimezone().isoformat()}\n"
            f"PID: {os.getpid()}\nPython: {sys.version}\nExecutable: {sys.executable}\n"
        )
        log_file.flush()
        faulthandler.enable(file=log_file, all_threads=True)

        def record_exception(exc_type, exc_value, tb):
            # Exception messages and source lines can contain user input or credentials.
            log_file.write(f"\n{datetime.now().astimezone().isoformat()} Unhandled {exc_type.__name__}\n")
            for frame in traceback.extract_tb(tb):
                log_file.write(f"  {frame.filename}:{frame.lineno} in {frame.name}\n")
            log_file.flush()

        previous = sys.excepthook
        def exception_hook(exc_type, exc_value, tb):
            try:
                record_exception(exc_type, exc_value, tb)
            finally:
                previous(exc_type, exc_value, tb)

        previous_thread = threading.excepthook
        def thread_hook(args):
            try:
                record_exception(args.exc_type, args.exc_value, args.exc_traceback)
            finally:
                previous_thread(args)

        sys.excepthook = exception_hook
        threading.excepthook = thread_hook
        print(f"诊断日志：{folder / 'runtime.log'}")
        return log_file
    except Exception as exc:
        if log_file is not None:
            log_file.close()
        print(f"诊断日志初始化失败：{type(exc).__name__}: {exc}；继续启动服务。", file=sys.stderr)
        return None


def launch():
    log_file = init_diagnostics()

    port = 8765
    try:
        check = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            f"http://127.0.0.1:{port}/api/system", timeout=1
        )
        import json

        if "privacy" in json.load(check):
            webbrowser.open(f"http://127.0.0.1:{port}")
            return
    except Exception:
        pass
    # 预绑定 127.0.0.1:8765 端口，防止端口抢占竞态并确认端口未被其他进程占用
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
    except OSError:
        print(f"端口 {port} 已被其他程序占用，请先关闭该程序。")
        return

    def open_browser():
        for _ in range(60):
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    f"http://127.0.0.1:{port}/api/system", timeout=1
                ):
                    webbrowser.open(f"http://127.0.0.1:{port}")
                    return
            except Exception:
                time.sleep(0.5)

    threading.Thread(target=open_browser, daemon=True).start()
    print(
        f"听澜字幕已启动：http://127.0.0.1:{port}\n保持此窗口开启。按 Ctrl+C 停止；正在处理的任务可在下次启动后恢复。"
    )
    config = server_config("backend.main:app", port)
    factory = config.get_loop_factory()
    if log_file:
        log_file.write(
            f"Uvicorn: {uvicorn.__version__}\n"
            f"Loop: {config.loop}\n"
            f"Loop factory: {factory.__name__ if factory else 'default'}\n"
        )
        log_file.flush()
    print(
        f"Uvicorn {uvicorn.__version__}，事件循环配置：{config.loop}，工厂：{factory.__name__ if factory else '默认'}"
    )
    try:
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        sock.close()


if __name__ == "__main__":
    launch()