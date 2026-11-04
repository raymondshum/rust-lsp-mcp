"""``rust-lsp mcp-bridge``: relay MCP messages between stdio and the daemon's listener.

``rlm-connect`` execs this inside the daemon's container (``docker exec -i
<name> /app/.venv/bin/rust-lsp mcp-bridge``), so an IDE that only speaks MCP
over stdio reaches the one warm daemon per project. The daemon's
streamable-HTTP listener binds ``127.0.0.1`` inside the container and is never
published, so only a process inside the container can reach it.

Protocol. stdin carries newline-delimited JSON-RPC messages. Each one is
POSTed, as its own HTTP request, to ``http://127.0.0.1:<port>/mcp`` with
``Accept: application/json, text/event-stream``: the daemon runs the ``mcp``
SDK's stateless streamable-HTTP mode, which needs no session header and
accepts one POST per message (docs/reference/mcp-streamable-http-daemon.md).
An ``application/json`` reply is written to stdout as one JSON line; a
``text/event-stream`` reply is split into its ``data:`` events and each is
written as one line; a ``202 Accepted`` (a notification or a response) writes
nothing. After ``initialize`` the negotiated protocol version is sent on later
requests as the ``MCP-Protocol-Version`` header.

Listener wait. A cold ``rlm-connect`` execs the bridge right after
``docker run -d``, so the IDE's ``initialize`` can arrive before uvicorn
listens. Before relaying the first message the bridge opens TCP connections to
the port every 0.5 seconds until one succeeds or ``--listener-timeout``
(default 120 seconds) expires. stdin is read on a separate thread throughout,
so messages arriving during the wait are held in order and dispatched in
order once the listener accepts (each on its own thread, so they can reach the
daemon in any order). If the wait expires, or a connection fails after the
listener was reached, the bridge writes a JSON-RPC error response for that
request's id to stdout and a hint to stderr, then carries on with the next
message.

Isolation. This module uses the standard library only. It imports nothing
from the server package, opens no doc store client and loads no index, so it
is not a second server writing the project volume (KI-13). stdout carries MCP
messages only, written by ``_StdoutWriter``; every diagnostic goes to stderr
through the logging configured in ``run``.
"""

from __future__ import annotations

import http.client
import json
import logging
import queue
import socket
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, BinaryIO

DEFAULT_LISTENER_TIMEOUT_SECONDS = 120.0
LISTENER_POLL_INTERVAL_SECONDS = 0.5
MCP_PATH = "/mcp"
ACCEPT = "application/json, text/event-stream"
PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"

# JSON-RPC error codes the bridge synthesizes. -32000 is the start of the
# implementation-defined server-error range; -32700 is the spec's parse
# error; -32603 is its internal error.
ERROR_DAEMON_UNREACHABLE = -32000
ERROR_DAEMON_HTTP = -32001
ERROR_PARSE = -32700
ERROR_INTERNAL = -32603

_log = logging.getLogger("rust_lsp_cli.bridge")


class _StdoutWriter:
    """The bridge's MCP message writer: the only code that writes stdout.

    Each message is one compact JSON line, written under a lock so replies
    from concurrent requests never interleave mid-line.
    """

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def write(self, message: Any) -> None:
        line = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        with self._lock:
            self._stream.write(line + b"\n")
            self._stream.flush()


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _is_request(message: Any) -> bool:
    """A JSON-RPC request carries both a method and an id; it expects a reply."""
    return isinstance(message, dict) and "method" in message and "id" in message


def _iter_sse_data(response: http.client.HTTPResponse) -> Iterator[str]:
    """Yield the ``data`` payload of each server-sent event in the response body."""
    data_lines: list[str] = []
    while True:
        raw = response.readline()
        if not raw:
            break
        line = raw.decode("utf-8").rstrip("\r\n")
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue  # SSE comment / keep-alive
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
    if data_lines:
        yield "\n".join(data_lines)


class Bridge:
    """Relay JSON-RPC lines from stdin to the daemon and replies to stdout."""

    def __init__(
        self,
        port: int,
        listener_timeout: float,
        writer: _StdoutWriter,
        host: str = "127.0.0.1",
    ) -> None:
        self.host = host
        self.port = port
        self.listener_timeout = listener_timeout
        self.writer = writer
        self.url = f"http://{host}:{port}{MCP_PATH}"
        self._protocol_version: str | None = None
        self._listener_ready = False

    # -- listener wait -----------------------------------------------------

    def wait_for_listener(self) -> bool:
        """Poll the listener every 0.5 s until it accepts or the wait expires."""
        _log.info(
            "mcp-bridge: waiting up to %gs for the daemon listener at %s:%d",
            self.listener_timeout,
            self.host,
            self.port,
        )
        deadline = time.monotonic() + self.listener_timeout
        while True:
            try:
                with socket.create_connection((self.host, self.port), timeout=1.0):
                    pass
            except OSError:
                if time.monotonic() >= deadline:
                    _log.error(
                        "mcp-bridge: daemon listener at %s:%d did not accept within %gs",
                        self.host,
                        self.port,
                        self.listener_timeout,
                    )
                    return False
                time.sleep(LISTENER_POLL_INTERVAL_SECONDS)
                continue
            _log.info("mcp-bridge: daemon listener at %s:%d accepted", self.host, self.port)
            self._listener_ready = True
            return True

    def _unreachable_hint(self) -> str:
        return (
            f"mcp-bridge: the rust-lsp-mcp daemon is not reachable at {self.url}. "
            "Check the container with `docker logs <name>` and its readiness with "
            "`docker exec <name> /app/.venv/bin/rust-lsp --wait 600 status`; "
            "restart the MCP server in the IDE to re-run rlm-connect."
        )

    def fail_unreachable(self, message: Any, reason: str) -> None:
        """Answer a request the daemon could not take with a JSON-RPC error."""
        _log.error("%s (%s)", self._unreachable_hint(), reason)
        if _is_request(message):
            self.writer.write(
                _error_response(
                    message["id"],
                    ERROR_DAEMON_UNREACHABLE,
                    f"rust-lsp-mcp daemon not reachable at {self.url}: {reason}",
                )
            )

    # -- relay -------------------------------------------------------------

    def relay_line(self, raw: bytes) -> None:
        """Relay one stdin line to the daemon and write its reply message(s)."""
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except json.JSONDecodeError as exc:
            _log.error("mcp-bridge: stdin line is not JSON: %s", exc)
            self.writer.write(_error_response(None, ERROR_PARSE, f"Parse error: {exc}"))
            return
        try:
            self._post(message, text.encode("utf-8"))
        except (OSError, http.client.HTTPException) as exc:
            self.fail_unreachable(message, f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            # Anything else (e.g. an undecodable reply) must still answer the
            # request, or the client waits forever on that id.
            _log.exception("mcp-bridge: relaying a message failed")
            if _is_request(message):
                self.writer.write(
                    _error_response(
                        message["id"],
                        ERROR_INTERNAL,
                        f"mcp-bridge failed to relay the message: {type(exc).__name__}: {exc}",
                    )
                )

    def _post(self, message: Any, body: bytes) -> None:
        headers = {"Content-Type": "application/json", "Accept": ACCEPT}
        if self._protocol_version:
            headers[PROTOCOL_VERSION_HEADER] = self._protocol_version
        method = message.get("method") if isinstance(message, dict) else None
        _log.debug("mcp-bridge: -> %s", method or "message")
        conn = http.client.HTTPConnection(self.host, self.port)
        try:
            conn.request("POST", MCP_PATH, body=body, headers=headers)
            response = conn.getresponse()
            self._handle_response(message, method, response)
        finally:
            conn.close()

    def _handle_response(
        self, message: Any, method: str | None, response: http.client.HTTPResponse
    ) -> None:
        status = response.status
        content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        _log.debug("mcp-bridge: <- HTTP %d %s", status, content_type or "-")

        if status == 202:
            response.read()
            return

        if status >= 400:
            detail = response.read().decode("utf-8", errors="replace").strip()
            try:
                parsed = json.loads(detail)
                detail = parsed.get("error", {}).get("message", detail)
            except (json.JSONDecodeError, AttributeError):
                pass
            _log.error("mcp-bridge: daemon answered HTTP %d: %s", status, detail)
            if _is_request(message):
                self.writer.write(
                    _error_response(
                        message["id"], ERROR_DAEMON_HTTP, f"daemon HTTP {status}: {detail}"
                    )
                )
            return

        if content_type == "text/event-stream":
            for data in _iter_sse_data(response):
                self._emit(method, data)
            return

        body = response.read().decode("utf-8")
        if body.strip():
            self._emit(method, body)
        elif _is_request(message):
            self.writer.write(
                _error_response(
                    message["id"], ERROR_DAEMON_HTTP, f"daemon HTTP {status} with an empty body"
                )
            )

    def _emit(self, method: str | None, data: str) -> None:
        try:
            reply = json.loads(data)
        except json.JSONDecodeError:
            _log.error("mcp-bridge: daemon sent a non-JSON message; dropped: %.200s", data)
            return
        if method == "initialize" and isinstance(reply, dict):
            version = (reply.get("result") or {}).get("protocolVersion")
            if isinstance(version, str):
                self._protocol_version = version
        self.writer.write(reply)


def _read_stdin(stream: BinaryIO, inbox: queue.Queue[bytes | None]) -> None:
    """Reader thread: queue every stdin line in order, then ``None`` at EOF."""
    try:
        for line in stream:
            inbox.put(line)
    finally:
        inbox.put(None)


def run(
    port: int,
    listener_timeout: float = DEFAULT_LISTENER_TIMEOUT_SECONDS,
    log_level: str = "INFO",
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
) -> int:
    """Run the bridge until stdin closes; return the process exit code (0)."""
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    in_stream = stdin if stdin is not None else sys.stdin.buffer
    out_stream = stdout if stdout is not None else sys.stdout.buffer
    bridge = Bridge(port, listener_timeout, _StdoutWriter(out_stream))

    inbox: queue.Queue[bytes | None] = queue.Queue()
    reader = threading.Thread(
        target=_read_stdin, args=(in_stream, inbox), name="mcp-bridge-stdin", daemon=True
    )
    reader.start()

    listener_ok = bridge.wait_for_listener()
    if not listener_ok:
        # Answer every request already held, then keep serving: each later
        # message is tried directly and answered with an error if refused.
        while True:
            try:
                held = inbox.get_nowait()
            except queue.Empty:
                break
            if held is None:
                inbox.put(None)
                break
            try:
                message = json.loads(held.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                message = None
            bridge.fail_unreachable(message, "listener wait expired")

    workers: list[threading.Thread] = []
    while True:
        line = inbox.get()
        if line is None:
            break
        worker = threading.Thread(
            target=bridge.relay_line, args=(line,), name="mcp-bridge-relay", daemon=True
        )
        worker.start()
        workers.append(worker)
        workers = [w for w in workers if w.is_alive()]

    for worker in workers:
        worker.join()
    _log.info("mcp-bridge: stdin closed; exiting")
    return 0
