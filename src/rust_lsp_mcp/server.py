"""FastMCP server for rust-lsp-mcp — thin wiring layer.

This module is the entry point that wires together the FastMCP application
(defined in ``rust_lsp_mcp.core``) and auto-registers all tool modules (defined
in ``rust_lsp_mcp.tools``).

Structure:
    core.py      — FastMCP app instance, lifespan, readiness gate, shared helpers.
    tools/*.py   — Individual tool modules; each registers itself via ``@mcp.tool()``
                   at import time.  Importing ``rust_lsp_mcp.tools`` auto-discovers
                   and imports every submodule, so no central registry edit is needed
                   when adding a new tool file.

Entry point:
    ``main()`` branches on ``settings.transport`` (docs/planning/cli-frontend.md
    D2/D3): "stdio" (default) calls ``mcp.run()`` exactly as before; "streamable-
    http" runs the warm daemon via ``_run_streamable_http()`` below. It is
    importable from ``rust_lsp_mcp`` so both launch paths work:
        - ``uv run rust-lsp-mcp``   (console script)
        - ``python -m rust_lsp_mcp``
"""

import asyncio
import contextlib
import logging
import os
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import Any

import uvicorn

import rust_lsp_mcp.tools  # noqa: F401 — importing the package registers all tools
from rust_lsp_mcp.core import _lifespan, mcp
from rust_lsp_mcp.settings import Settings, get_settings

# ``find_symbol`` is re-exported because the Phase 2 integration test reaches it
# via ``rust_lsp_mcp.server.find_symbol``.  New code should import tools from
# ``rust_lsp_mcp.tools.*`` directly; the canonical manager/gate live in
# ``rust_lsp_mcp.core`` (tests monkeypatch ``rust_lsp_mcp.core._manager``).
from rust_lsp_mcp.tools.find_symbol import find_symbol  # noqa: F401

_log = logging.getLogger(__name__)

# Starlette's ``app.router.lifespan_context`` type (a lifespan is any callable
# taking the app and returning an async context manager) — typed loosely and
# by name here, rather than importing Starlette's private ``Lifespan`` alias,
# so this module and its tests can pass plain stub callables.
LifespanCallable = Callable[[Any], contextlib.AbstractAsyncContextManager[Any]]


def compose_daemon_lifespan(original_lifespan: LifespanCallable) -> LifespanCallable:
    """Build the process-level lifespan for streamable-HTTP mode.

    Nests ``core._lifespan`` (the SAME analyzer/doc-store/preflight code path
    stdio uses — one code path, never a hand-rolled subset covering only the
    analyzer) around ``original_lifespan`` (the SDK's own lifespan, which runs
    the mandatory ``session_manager.run()`` — delegating to it is not
    optional, sessions break otherwise). See
    docs/reference/mcp-streamable-http-daemon.md for the proven wiring this
    mirrors, and the D2 production note: do not re-implement a subset of
    startup here.

    ``original_lifespan`` is typed loosely (``LifespanCallable``, matching
    Starlette's ``app.router.lifespan_context``) so this function is reusable
    by tests with a stubbed callable, and by ``_run_streamable_http`` with the
    real SDK-provided one.
    """

    @contextlib.asynccontextmanager
    async def process_lifespan(app: Any) -> AsyncIterator[None]:
        # A single `async with a, b:` enters a-then-b and exits b-then-a —
        # identical ordering to the reference doc's nested form, just
        # combined per ruff SIM117. _lifespan ignores its argument (see
        # core.py) — passing the Starlette app instead of the FastMCP
        # instance is harmless.
        async with _lifespan(app), original_lifespan(app):
            yield

    return process_lifespan


# ASGI callable shapes, typed loosely (no Starlette private aliases) so the
# idle tracker's middleware wraps any ASGI app, including test stubs.
_Scope = MutableMapping[str, Any]
_Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
_Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
_ASGIApp = Callable[[_Scope, _Receive, _Send], Awaitable[None]]

# How long a graceful shutdown after the idle timeout may take before the
# process exits anyway: a wedged analyzer shutdown must not keep an idle
# daemon alive forever.
IDLE_SHUTDOWN_GRACE_SECONDS = 60.0


class IdleTracker:
    """Track request activity for the daemon's idle timeout (``idle_timeout_seconds``).

    ``middleware`` wraps the ASGI app and records the start and end of every
    HTTP request; ``idle_seconds`` is 0 while any request is in flight, else
    the time since the last one ended. ``watch`` runs as a background task in
    the process lifespan and calls ``on_idle`` once the idle time reaches the
    timeout. Lifespan events are not requests and never count as activity.
    """

    def __init__(self, timeout_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._last_activity = clock()
        self._in_flight = 0

    def mark_activity(self) -> None:
        self._last_activity = self._clock()

    def idle_seconds(self) -> float:
        if self._in_flight > 0:
            return 0.0
        return self._clock() - self._last_activity

    def middleware(self, app: _ASGIApp) -> _ASGIApp:
        async def tracked(scope: _Scope, receive: _Receive, send: _Send) -> None:
            if scope.get("type") != "http":
                await app(scope, receive, send)
                return
            self._in_flight += 1
            self.mark_activity()
            try:
                await app(scope, receive, send)
            finally:
                self._in_flight -= 1
                self.mark_activity()

        return tracked

    def poll_interval(self) -> float:
        return min(max(self.timeout_seconds / 4, 0.25), 30.0)

    async def watch(self, on_idle: Callable[[], None]) -> None:
        """Sleep in short steps until the idle window passes, then call ``on_idle`` once."""
        self.mark_activity()  # the window starts when the daemon is up, not at import
        while True:
            await asyncio.sleep(self.poll_interval())
            if self.idle_seconds() >= self.timeout_seconds:
                on_idle()
                return


def _with_idle_watch(
    lifespan: LifespanCallable, tracker: IdleTracker, on_idle: Callable[[], None]
) -> LifespanCallable:
    """Wrap ``lifespan`` so the idle watcher runs while the daemon serves."""

    @contextlib.asynccontextmanager
    async def idle_lifespan(app: Any) -> AsyncIterator[None]:
        async with lifespan(app):
            task = asyncio.create_task(tracker.watch(on_idle), name="idle-timeout-watch")
            try:
                yield
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return idle_lifespan


def _idle_exit(server: uvicorn.Server, timeout_seconds: int) -> None:
    """Ask uvicorn for a graceful shutdown; force exit 0 if it hangs past the grace period."""
    _log.info("idle timeout reached after %d seconds; exiting", timeout_seconds)
    server.should_exit = True

    def _force_exit() -> None:
        _log.warning(
            "graceful shutdown after the idle timeout took over %gs; exiting now",
            IDLE_SHUTDOWN_GRACE_SECONDS,
        )
        os._exit(0)

    timer = threading.Timer(IDLE_SHUTDOWN_GRACE_SECONDS, _force_exit)
    timer.daemon = True
    timer.start()


def _run_streamable_http(settings: Settings) -> None:
    """Run the warm HTTP daemon (docs/planning/cli-frontend.md D2/D3).

    ``core._build_mcp`` skips ``lifespan=`` for HTTP mode because, under this
    SDK version, FastMCP's own ``lifespan=`` kwarg runs per MCP session (per
    request when ``stateless_http=True``) — passing it here would cold-spawn
    and tear down the analyzer on every call. Instead we own process startup:
    build the Starlette app via ``streamable_http_app()``, replace its
    lifespan with the composed one, then own the ``uvicorn.run()`` call
    (``run_streamable_http_async()`` builds its own app/uvicorn internally
    with no injection hook — this is why we cannot use ``mcp.run()`` here).

    Host is hard-coded to ``"127.0.0.1"`` — never taken from settings or any
    other configuration path (D3/D4): nothing in this function can bind a
    non-loopback address.

    Idle timeout: with ``settings.idle_timeout_seconds > 0`` an
    ``IdleTracker`` middleware records every request, and a watcher task in
    the lifespan asks uvicorn to shut down gracefully once no request has
    arrived for that long. ``uvicorn.Server.run`` then returns and the
    process exits with code 0, so compose's ``restart: on-failure`` leaves an
    idle daemon stopped. ``0`` disables the timeout. A lifespan that fails at
    startup exits 3, as ``uvicorn.run`` would.
    """
    app = mcp.streamable_http_app()
    original_lifespan: LifespanCallable = app.router.lifespan_context
    composed = compose_daemon_lifespan(original_lifespan)
    asgi_app: Any = app
    server_ref: list[uvicorn.Server] = []
    timeout = settings.idle_timeout_seconds
    if timeout > 0:
        tracker = IdleTracker(timeout)
        app.router.lifespan_context = _with_idle_watch(
            composed, tracker, lambda: _idle_exit(server_ref[0], timeout)
        )
        asgi_app = tracker.middleware(app)
        _log.info("idle timeout: %d seconds", timeout)
    else:
        app.router.lifespan_context = composed
        _log.info("idle timeout: disabled")
    _log.info("starting streamable-http daemon on 127.0.0.1:%d", settings.http_port)
    server = uvicorn.Server(uvicorn.Config(asgi_app, host="127.0.0.1", port=settings.http_port))
    server_ref.append(server)
    server.run()
    if not server.started:
        # uvicorn.run() exits 3 (STARTUP_FAILURE) when the lifespan fails at
        # startup; Server.run() just returns. Keep the non-zero code so
        # compose's `restart: on-failure` restarts a daemon that crashed at
        # start, while the idle exit (started, then shut down) stays 0.
        sys.exit(3)


def main() -> None:
    """Start the MCP server (synchronous).

    Branches on ``settings.transport``: "stdio" (default) calls ``mcp.run()``
    exactly as before (wraps anyio.run internally); "streamable-http" runs the
    warm daemon via ``_run_streamable_http()``. This is the console-script
    entry point (``rust-lsp-mcp``) and is also called by ``__main__.py`` for
    ``python -m rust_lsp_mcp``.
    """
    settings = get_settings()
    if settings.transport == "streamable-http":
        _run_streamable_http(settings)
    else:
        mcp.run()
