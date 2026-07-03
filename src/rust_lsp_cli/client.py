"""Streamable-HTTP MCP client half of the rust-lsp CLI.

Imported lazily by ``rust_lsp_cli.cli`` -- only once we are actually about to
make a network call (not for ``--help``, argparse-only invocations, or a
parity test's introspection of the command table) -- so those paths stay fast
and never pull in the ``mcp`` client's heavier dependencies (httpx, anyio).
Never imports ``rust_lsp_mcp`` -- see ``rust_lsp_cli.cli``'s module docstring.

D8/D9 (docs/planning/cli-frontend.md): envelopes are parsed from
``content[0].text`` (authoritative), falling back to ``structuredContent``;
any exception raised before a response is obtained (connection refused,
handshake failure, read timeout, ...) is classified as a transport-level
failure, never as an ``error`` envelope.
"""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_POLL_INTERVAL_SECONDS = 2.0


async def _connect_and_call(
    base_url: str, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Open one connection, call one tool, return the parsed envelope.

    A fresh connection per call -- matching the daemon fixture's driver
    (``tests/daemon_fixture.py``) and the production usage pattern: the CLI
    is short-lived, the daemon is what stays warm. Any exception raised here
    is left to propagate to ``call_tool_sync``, which classifies it as a
    transport-level failure (D8's exit 3) since it means no tool response
    was ever obtained.
    """
    async with (
        streamablehttp_client(base_url) as (read, write, _get_session_id),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(tool_name, arguments)
        return _parse_envelope(result)


def _parse_envelope(result: Any) -> dict[str, Any]:
    """Parse an MCP ``call_tool`` result into the envelope dict.

    ``content[0].text`` is authoritative -- always the full
    ``json.dumps(envelope)`` regardless of output-schema annotation drift
    (docs/reference/mcp-streamable-http-daemon.md). ``structuredContent`` is
    an equivalent fallback used only when the text does not parse. If
    neither yields a usable dict, synthesize a local ``error`` envelope
    rather than raising: this is a malformed-response case (we DID get a
    response), distinct from the transport-level failures ``call_tool_sync``
    handles, so it must not be misclassified as exit 3.
    """
    content = getattr(result, "content", None) or []
    if content:
        text = getattr(content[0], "text", None)
        if text is not None:
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                return parsed

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    return {
        "status": "error",
        "message": "Malformed response from daemon: no parseable tool result.",
        "recovery": "unknown",
    }


def call_tool_sync(
    base_url: str, tool_name: str, arguments: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, Exception | None]:
    """Run one tool call to completion; never raises.

    Returns ``(envelope, None)`` on success -- including a synthesized
    ``error`` envelope for a malformed-but-received response, see
    ``_parse_envelope`` -- or ``(None, exc)`` when the call never produced a
    response at all (connection refused, handshake failure, timeout, or any
    other transport exception). Callers map the latter to exit 3 (D8).
    """
    try:
        return asyncio.run(_connect_and_call(base_url, tool_name, arguments or {})), None
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any failure before a
        # tool response exists is a transport-level failure by definition (D8).
        return None, exc


def wait_for_ready(
    base_url: str,
    timeout_seconds: float,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    status_check: Callable[[str], tuple[dict[str, Any] | None, Exception | None]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[bool, bool]:
    """Poll ``status`` every ``poll_interval`` seconds until ready, or timeout.

    Implements D9: within the window, a transport-level failure (daemon not
    up yet) is retriable exactly like an ``ok``-but-not-``ready`` status -- a
    daemon booting passes through refused -> not_ready -> ready, and this
    function rides through all of it rather than giving up on the first
    connection error.

    ``status_check``/``sleep``/``monotonic`` are injectable seams for fast,
    real-time-free unit tests (a scripted ``status_check`` sequence plus a
    no-op ``sleep``); production callers rely on the defaults.

    Returns ``(ready, ever_connected)``:
        - ``(True, True)``   -- reached ``state == "ready"`` within the window.
        - ``(False, True)``  -- window expired; the daemon answered at least
          once but never reported ready (D8: caller exits 2).
        - ``(False, False)`` -- window expired without ever getting a
          response (D8: caller exits 3).
    """
    check = (
        status_check
        if status_check is not None
        else (lambda url: call_tool_sync(url, "status", {}))
    )
    deadline = monotonic() + timeout_seconds
    ever_connected = False
    while True:
        envelope, exc = check(base_url)
        if exc is None and envelope is not None:
            ever_connected = True
            if envelope.get("status") == "ok" and envelope.get("state") == "ready":
                return True, ever_connected
        if monotonic() >= deadline:
            return False, ever_connected
        sleep(poll_interval)
