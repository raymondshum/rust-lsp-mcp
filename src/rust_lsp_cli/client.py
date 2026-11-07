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

# How long one call waits for the daemon's reply once connected: the
# ``sse_read_timeout`` default of mcp 1.12.4's ``streamablehttp_client``,
# passed explicitly so the "timed out after N s" message cannot drift from it.
READ_TIMEOUT_SECONDS = 300.0


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
        streamablehttp_client(base_url, sse_read_timeout=READ_TIMEOUT_SECONDS) as (
            read,
            write,
            _get_session_id,
        ),
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


def innermost_exception(exc: BaseException) -> BaseException:
    """Return the innermost exception of *exc*, unwrapping nested exception groups.

    The mcp client raises its transport errors from inside anyio task groups,
    so ``call_tool_sync`` returns an ``ExceptionGroup`` ("unhandled errors in
    a TaskGroup") whose text hides the cause.  Follows the first
    sub-exception at each level.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def is_read_timeout(exc: BaseException) -> bool:
    """Return whether any leaf of *exc* (groups unwrapped) is an ``httpx.ReadTimeout``.

    A read timeout means the daemon accepted the connection and never
    replied within :data:`READ_TIMEOUT_SECONDS` -- not an unreachable daemon.
    """
    import httpx

    if isinstance(exc, BaseExceptionGroup):
        return any(is_read_timeout(sub) for sub in exc.exceptions)
    return isinstance(exc, httpx.ReadTimeout)


def wait_for_ready(
    base_url: str,
    timeout_seconds: float,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    status_check: Callable[[str], tuple[dict[str, Any] | None, Exception | None]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    need_docs: bool = True,
) -> tuple[bool, bool, str | None]:
    """Poll ``status`` every ``poll_interval`` seconds until ready, or timeout.

    With ``need_docs=True`` (the default, for ``search-docs``, ``status`` and
    ``refresh``), "ready" means BOTH indexes are ready (SW-L25): ``state ==
    "ready"`` (the code index) and ``doc_index_state == "ready"`` (the doc
    index), so a ``search-docs`` call right after a successful wait does not
    exit 2.  A doc index that stays ``"building"`` keeps the wait polling
    until the window expires.  A doc index in ``"error"`` is permanent until
    ``refresh``, so the first ``ok`` envelope reporting it ends the wait at
    once.  A ``"ready"`` doc index with ``doc_index_error`` set (a failed
    rebuild while the old index serves) is not a stop.

    With ``need_docs=False`` (every navigation command, which never reads the
    doc index; backlog line 165), "ready" means ``state == "ready"`` alone,
    whatever ``doc_index_state`` reads, and a doc index error is no stop.

    Implements D9: within the window, a transport-level failure (daemon not
    up yet) is retriable exactly like an ``ok``-but-not-``ready`` status -- a
    daemon booting passes through refused -> not_ready -> ready, and this
    function rides through all of it rather than giving up on the first
    connection error.

    ``status_check``/``sleep``/``monotonic`` are injectable seams for fast,
    real-time-free unit tests (a scripted ``status_check`` sequence plus a
    no-op ``sleep``); production callers rely on the defaults.

    Returns ``(ready, ever_connected, doc_error)``:
        - ``(True, True, None)``   -- reached ``state == "ready"`` (and,
          with ``need_docs``, ``doc_index_state == "ready"``) within the
          window.
        - ``(False, True, <text>)`` -- with ``need_docs`` only, an ``ok``
          envelope reported ``doc_index_state == "error"``; ``<text>`` is its
          ``doc_index_error`` (``"unknown error"`` when that is null).
          Returned at once (caller exits 2 with the diagnostic).
        - ``(False, True, None)``  -- window expired; the daemon answered at
          least once but never reported ready (D8: caller exits 2).
        - ``(False, False, None)`` -- window expired without ever getting a
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
            is_ok = envelope.get("status") == "ok"
            if need_docs and is_ok and envelope.get("doc_index_state") == "error":
                return False, ever_connected, envelope.get("doc_index_error") or "unknown error"
            if (
                is_ok
                and envelope.get("state") == "ready"
                and (not need_docs or envelope.get("doc_index_state") == "ready")
            ):
                return True, ever_connected, None
        if monotonic() >= deadline:
            return False, ever_connected, None
        sleep(poll_interval)
