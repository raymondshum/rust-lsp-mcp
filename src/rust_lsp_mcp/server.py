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

import contextlib
import logging
from collections.abc import AsyncIterator, Callable
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
    """
    app = mcp.streamable_http_app()
    original_lifespan: LifespanCallable = app.router.lifespan_context
    app.router.lifespan_context = compose_daemon_lifespan(original_lifespan)
    _log.info("starting streamable-http daemon on 127.0.0.1:%d", settings.http_port)
    uvicorn.run(app, host="127.0.0.1", port=settings.http_port)


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
