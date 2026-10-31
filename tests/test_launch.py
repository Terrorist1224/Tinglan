import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import launch


def test_windows_uses_selector_loop():
    assert launch.LOOP == "asyncio:SelectorEventLoop"
    factory = launch.server_config("fixture:app", 0).get_loop_factory()
    assert factory is asyncio.SelectorEventLoop


def test_non_windows_keeps_auto(monkeypatch):
    monkeypatch.setattr(launch, "LOOP", "auto")
    assert launch.server_config("fixture:app", 0).loop == "auto"


SERVER_SCRIPT = r"""
import asyncio
import socket
import sys
import threading
import time

import uvicorn

from scripts.launch import server_config


async def app(scope, receive, send):
    print(f"LOOP:{type(asyncio.get_running_loop()).__name__}", flush=True)
    if scope["path"] == "/boom":
        raise RuntimeError("expected-visible-error")
    body = b"x" * 4_000_000
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-length", str(len(body)).encode())],
    })
    for offset in range(0, len(body), 16384):
        await send({
            "type": "http.response.body",
            "body": body[offset:offset + 16384],
            "more_body": offset + 16384 < len(body),
        })


sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen(128)
port = sock.getsockname()[1]
server = uvicorn.Server(server_config(app, port))
thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
thread.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.05)
if not server.started:
    raise RuntimeError("server did not start")
print(f"READY:{port}", flush=True)


def request(path):
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.sendall(
        f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
    )
    data = client.recv(8192)
    client.close()
    return data


try:
    for _ in range(100):
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        client.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        try:
            client.recv(64)
        except OSError:
            pass
        client.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_LINGER,
            b"\x01\x00\x00\x00\x00\x00\x00\x00",
        )
        client.close()
    time.sleep(0.5)
    print(f"AFTER:{request('/')[:15]!r}", flush=True)
    print(f"BOOM:{request('/boom')[:24]!r}", flush=True)
finally:
    server.should_exit = True
    thread.join(10)
    sock.close()
if thread.is_alive():
    raise RuntimeError("server did not stop")
print("STOPPED", flush=True)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loop regression")
def test_real_server_disconnects_and_errors_remain_visible():
    result = subprocess.run(
        [sys.executable, "-c", SERVER_SCRIPT],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "LOOP:_WindowsSelectorEventLoop" in result.stdout
    assert "AFTER:b'HTTP/1.1 200" in result.stdout
    assert "BOOM:b'HTTP/1.1 500" in result.stdout
    assert "expected-visible-error" in output
    assert "ProactorEventLoop" not in output
    assert "_call_connection_lost" not in output
    assert "STOPPED" in output
