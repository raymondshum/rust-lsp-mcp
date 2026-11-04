"""Fast tests for ``rust-lsp mcp-bridge`` (T05).

The helpers below are also imported by ``tests/test_stdio_stdout_clean.py``.
``StubDaemon`` is a local HTTP server standing in for the daemon's
streamable-HTTP endpoint: ``initialize`` is answered with
``application/json``, ``tools/call`` with ``text/event-stream`` (one ``data:``
event), and a notification with ``202 Accepted`` and no body. ``BridgeProcess``
runs the bridge as a subprocess and collects its stdout lines on a thread.
"""

from __future__ import annotations

import json
import queue
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

BRIDGE_CODE = "import sys; from rust_lsp_cli.cli import main; sys.exit(main(sys.argv[1:]))"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Handler(BaseHTTPRequestHandler):
    server: StubServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return  # keep the test output quiet

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        message = json.loads(self.rfile.read(length))
        self.server.received.append((dict(self.headers), message))
        method = message.get("method")
        if "id" not in message:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "stub", "version": "0"},
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if (message.get("params") or {}).get("name") == "bad_utf8":
            body = b"event: message\ndata: \xff\xfe not utf-8\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"content": [{"type": "text", "text": '{"status": "ok"}'}]},
            }
        )
        body = f"event: message\ndata: {payload}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StubServer(ThreadingHTTPServer):
    received: list[tuple[dict[str, str], dict[str, Any]]]


class StubDaemon:
    def __init__(self, port: int = 0) -> None:
        self.httpd = StubServer(("127.0.0.1", port), _Handler)
        self.httpd.received = []
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> StubDaemon:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def received(self) -> list[tuple[dict[str, str], dict[str, Any]]]:
        return self.httpd.received


class BridgeProcess:
    """The bridge as a subprocess, with stdout lines collected on a thread."""

    def __init__(self, port: int, *extra: str) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-c", BRIDGE_CODE, "mcp-bridge", "--port", str(port), *extra],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=REPO_ROOT,
        )
        self.lines: queue.Queue[bytes] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line)

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message).encode() + b"\n")
        self.proc.stdin.flush()

    def next_line(self, timeout: float = 15.0) -> bytes:
        return self.lines.get(timeout=timeout)

    def close(self, timeout: float = 15.0) -> tuple[list[bytes], str]:
        """Close stdin, wait for exit, return the remaining stdout lines and all stderr."""
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise
        self._reader.join(timeout=5)
        rest: list[bytes] = []
        while not self.lines.empty():
            rest.append(self.lines.get_nowait())
        assert self.proc.stderr is not None
        return rest, self.proc.stderr.read().decode("utf-8", errors="replace")


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
STATUS_CALL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "status", "arguments": {}},
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _parse(line: bytes) -> dict[str, Any]:
    parsed = json.loads(line)
    assert isinstance(parsed, dict)
    return parsed


def test_relays_json_and_sse_replies_and_nothing_else() -> None:
    with StubDaemon() as stub:
        bridge = BridgeProcess(stub.port)
        bridge.send(INITIALIZE)
        first = _parse(bridge.next_line())
        bridge.send(INITIALIZED)
        bridge.send(STATUS_CALL)
        second = _parse(bridge.next_line())
        rest, stderr = bridge.close()
    assert bridge.proc.returncode == 0, stderr
    assert first["id"] == 1 and first["result"]["serverInfo"]["name"] == "stub"
    assert second["id"] == 2 and second["result"]["content"][0]["type"] == "text"
    assert rest == []  # the 202 notification produced no line
    # Relays run concurrently, so the two later POSTs may land in either order.
    methods = [m.get("method") for _, m in stub.received]
    assert methods[0] == "initialize"
    assert sorted(methods[1:]) == ["notifications/initialized", "tools/call"]
    call_headers = next(h for h, m in stub.received if m.get("method") == "tools/call")
    headers = {k.lower(): v for k, v in call_headers.items()}
    assert headers["accept"] == "application/json, text/event-stream"
    assert headers["content-type"] == "application/json"
    assert headers["mcp-protocol-version"] == "2025-06-18"


def test_notification_with_202_writes_no_line() -> None:
    with StubDaemon() as stub:
        bridge = BridgeProcess(stub.port)
        bridge.send(INITIALIZED)
        rest, stderr = bridge.close()
    assert bridge.proc.returncode == 0, stderr
    assert rest == []
    assert [m.get("method") for _, m in stub.received] == ["notifications/initialized"]


def test_late_listener_gets_the_held_initialize() -> None:
    port = free_port()
    bridge = BridgeProcess(port)
    bridge.send(INITIALIZE)
    time.sleep(2.0)
    with StubDaemon(port) as stub:
        reply = _parse(bridge.next_line(timeout=15))
        rest, stderr = bridge.close()
    assert reply["id"] == 1 and "result" in reply
    assert rest == []
    assert [m.get("method") for _, m in stub.received] == ["initialize"]
    assert "waiting up to" in stderr
    assert "accepted" in stderr


def test_refusal_past_listener_timeout_gives_one_error_line() -> None:
    port = free_port()
    bridge = BridgeProcess(port, "--listener-timeout", "1")
    bridge.send(INITIALIZE)
    reply = _parse(bridge.next_line(timeout=15))
    rest, stderr = bridge.close()
    assert bridge.proc.returncode == 0
    assert reply["jsonrpc"] == "2.0" and reply["id"] == 1
    assert reply["error"]["code"] == -32000
    assert rest == []
    assert "not reachable" in stderr
    assert "rust-lsp --wait 600 status" in stderr


def test_bridge_imports_no_server_package() -> None:
    code = (
        "import sys, rust_lsp_cli.cli, rust_lsp_cli.bridge; "
        "bad = [m for m in sys.modules if m.startswith(('rust_lsp_mcp', 'chromadb'))]; "
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30, check=True
    )
    assert out.stdout.strip() == "[]"


def test_undecodable_reply_still_answers_the_request() -> None:
    """Any relay failure, not only OSError/HTTPException, answers the request's id."""
    bad_call = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "bad_utf8", "arguments": {}},
    }
    with StubDaemon() as stub:
        bridge = BridgeProcess(stub.port)
        bridge.send(bad_call)
        try:
            first = bridge.next_line(timeout=10)
        except queue.Empty:
            first = None
        rest, stderr = bridge.close()
    lines = ([first] if first is not None else []) + rest
    assert len(lines) == 1, (lines, stderr[-2000:])
    reply = _parse(lines[0])
    assert reply["id"] == 7
    assert reply["error"]["code"] == -32603
