"""Daemon idle timeout (T05): ``idle_timeout_seconds`` / ``RLM_IDLE_TIMEOUT_SECONDS``.

The daemon runs as a real subprocess (``rust-lsp-mcp`` in streamable-HTTP
mode), not through the in-process ``daemon_app`` fixture: that fixture builds
the app without calling ``_run_streamable_http``, so it never arms the idle
watcher, and a process exit there would end pytest. rust-analyzer is a stub
that exits at once, so no toolchain or network is needed and the file stays in
the fast tier.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from rust_lsp_mcp.server import IdleTracker
from tests.test_bridge_fast import free_port

LISTENER_WAIT_SECONDS = 30.0


def _spawn_daemon(tmp_path: Path, idle_timeout: str) -> tuple[subprocess.Popen[bytes], int]:
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "Cargo.toml").write_text('[package]\nname = "tiny"\nversion = "0.1.0"\n')
    (project / "src" / "main.rs").write_text("fn main() {}\n")
    stub = tmp_path / "ra-stub"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RLM_")}
    env.update(
        RLM_TRANSPORT="streamable-http",
        RLM_HTTP_PORT=str(port),
        RLM_IDLE_TIMEOUT_SECONDS=idle_timeout,
        RLM_PROJECT_ROOT=str(project),
        RLM_CHROMA_PATH=str(tmp_path / "chroma"),
        RLM_RUST_ANALYZER_BIN=str(stub),
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "rust_lsp_mcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tmp_path,
        env=env,
    )
    deadline = time.monotonic() + LISTENER_WAIT_SECONDS
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return proc, port
        except OSError:
            if proc.poll() is not None or time.monotonic() > deadline:
                proc.kill()
                out = proc.communicate()[0].decode(errors="replace")
                pytest.fail(f"daemon never listened on {port}:\n{out[-3000:]}")
            time.sleep(0.1)


def _post_status(port: int) -> dict[str, object]:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {}},
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def test_idle_daemon_exits_zero_after_the_window(tmp_path: Path) -> None:
    proc, port = _spawn_daemon(tmp_path, "3")
    try:
        reply = _post_status(port)
        assert reply.get("id") == 1
        sent_at = time.monotonic()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pytest.fail("daemon did not exit within 15 s of its last request")
        assert time.monotonic() - sent_at <= 15
    finally:
        if proc.poll() is None:
            proc.kill()
        out = proc.communicate()[0].decode(errors="replace")
    assert proc.returncode == 0, out[-3000:]
    assert "idle timeout reached after 3 seconds; exiting" in out


def test_zero_disables_the_idle_timeout(tmp_path: Path) -> None:
    proc, port = _spawn_daemon(tmp_path, "0")
    try:
        assert _post_status(port).get("id") == 1
        time.sleep(6)
        assert proc.poll() is None, "daemon exited although RLM_IDLE_TIMEOUT_SECONDS=0"
    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=15)[0].decode(errors="replace")
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0].decode(errors="replace")
    assert "idle timeout reached" not in out


def test_middleware_counts_in_flight_http_requests_only() -> None:
    """Drive ``IdleTracker.middleware`` with a stub ASGI app that blocks on an event."""
    now = [100.0]
    tracker = IdleTracker(10, clock=lambda: now[0])
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stub_app(scope: Any, receive: Any, send: Any) -> None:
        entered.set()
        await release.wait()

    wrapped = tracker.middleware(stub_app)

    async def _noop_receive() -> dict[str, Any]:
        return {}

    async def _noop_send(message: Any) -> None:
        return None

    async def scenario() -> None:
        # A lifespan scope is not a request: the counter stays at zero.
        lifespan = asyncio.ensure_future(wrapped({"type": "lifespan"}, _noop_receive, _noop_send))
        await entered.wait()
        now[0] += 5
        assert tracker.idle_seconds() == 5.0
        release.set()
        await lifespan
        entered.clear()
        release.clear()

        # An HTTP request in flight holds the idle time at zero.
        request = asyncio.ensure_future(wrapped({"type": "http"}, _noop_receive, _noop_send))
        await entered.wait()
        now[0] += 60
        assert tracker.idle_seconds() == 0.0
        release.set()
        await request
        now[0] += 4
        assert tracker.idle_seconds() == 4.0

    asyncio.run(scenario())


STARTUP_CRASH_DRIVER = """
import contextlib, sys
import rust_lsp_mcp.server as server
from rust_lsp_mcp.settings import Settings

@contextlib.asynccontextmanager
async def _boom(app):
    raise RuntimeError("lifespan startup failure (test)")
    yield

server._lifespan = _boom
server._run_streamable_http(
    Settings(transport="streamable-http", http_port=int(sys.argv[1]), idle_timeout_seconds=60)
)
"""


def test_startup_failure_exits_nonzero(tmp_path: Path) -> None:
    """A lifespan that raises at startup must not exit 0 (compose restart: on-failure)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("RLM_")}
    env.update(RLM_TRANSPORT="streamable-http", RLM_CHROMA_PATH=str(tmp_path / "chroma"))
    proc = subprocess.run(
        [sys.executable, "-c", STARTUP_CRASH_DRIVER, str(free_port())],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
    )
    assert "lifespan startup failure (test)" in proc.stderr + proc.stdout
    assert proc.returncode != 0, proc.stderr[-2000:]
