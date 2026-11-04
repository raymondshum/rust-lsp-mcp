"""stdout carries MCP messages only (T05, decision ledger item 5).

Two processes whose stdout is an MCP stream:

* ``rust-lsp-mcp`` in stdio mode, on a tiny project, with
  ``RLM_RUST_ANALYZER_BIN`` pointing at a stub that exits at once (so the
  analyzer start, its death detection and the doc-store startup all log);
* ``rust-lsp mcp-bridge`` against a stub daemon, logging at ``DEBUG``.

Each is driven through ``initialize``, ``notifications/initialized`` and a
``tools/call`` of ``status``, its stdout is read for five seconds, and every
line must parse as a JSON-RPC 2.0 message.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from tests.test_bridge_fast import (
    INITIALIZE,
    INITIALIZED,
    STATUS_CALL,
    BridgeProcess,
    StubDaemon,
)

READ_SECONDS = 5.0


def _assert_all_jsonrpc(lines: list[bytes]) -> list[dict[str, Any]]:
    messages = []
    for raw in lines:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - the failure path
            raise AssertionError(f"non-JSON stdout line: {raw!r}") from exc
        assert isinstance(message, dict), raw
        assert message.get("jsonrpc") == "2.0", raw
        assert "method" in message or "result" in message or "error" in message, raw
        messages.append(message)
    return messages


def _collect(q: queue.Queue[bytes], seconds: float) -> list[bytes]:
    deadline = time.monotonic() + seconds
    lines: list[bytes] = []
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            lines.append(q.get(timeout=remaining))
        except queue.Empty:
            break
    return lines


def test_stdio_server_stdout_is_jsonrpc_only(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "Cargo.toml").write_text('[package]\nname = "tiny"\nversion = "0.1.0"\n')
    (project / "src" / "main.rs").write_text("fn main() {}\n")
    stub = tmp_path / "ra-stub"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)

    env = {k: v for k, v in os.environ.items() if not k.startswith("RLM_")}
    env.update(
        RLM_TRANSPORT="stdio",
        RLM_PROJECT_ROOT=str(project),
        RLM_CHROMA_PATH=str(tmp_path / "chroma"),
        RLM_RUST_ANALYZER_BIN=str(stub),
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "rust_lsp_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,  # no repo .env is read
        env=env,
    )
    out: queue.Queue[bytes] = queue.Queue()
    err_chunks: list[bytes] = []

    def _pump_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            out.put(line)

    def _pump_stderr() -> None:
        assert proc.stderr is not None
        err_chunks.append(proc.stderr.read())

    threading.Thread(target=_pump_stdout, daemon=True).start()
    threading.Thread(target=_pump_stderr, daemon=True).start()
    try:
        assert proc.stdin is not None
        for message in (INITIALIZE, INITIALIZED, STATUS_CALL):
            proc.stdin.write(json.dumps(message).encode() + b"\n")
            proc.stdin.flush()
        lines = _collect(out, READ_SECONDS)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    messages = _assert_all_jsonrpc(lines)
    ids = {m.get("id") for m in messages if "result" in m or "error" in m}
    assert {1, 2} <= ids, (lines, b"".join(err_chunks)[-2000:])


def test_bridge_stdout_is_jsonrpc_only_at_debug() -> None:
    with StubDaemon() as stub:
        bridge = BridgeProcess(stub.port, "--log-level", "DEBUG")
        for message in (INITIALIZE, INITIALIZED, STATUS_CALL):
            bridge.send(message)
        lines = _collect(bridge.lines, READ_SECONDS)
        rest, stderr = bridge.close()
    messages = _assert_all_jsonrpc(lines + rest)
    assert {m.get("id") for m in messages} == {1, 2}
    assert "DEBUG" in stderr  # debug logging was on, and it went to stderr
