"""Shared daemon-mode integration fixture (Phase C1, docs/handoff/cli-phase-1-daemon.md).

Provides a session-scoped, IN-PROCESS streamable-HTTP daemon (the real
production wiring — ``rust_lsp_mcp.server.compose_daemon_lifespan`` nesting
the real ``rust_lsp_mcp.core._lifespan``, not a stub) plus a raw MCP client
driver, for tests that need to observe the daemon's process-level behaviour
(warm-across-connections, teardown races) that cannot be observed across a
real process boundary (podman exec / a subprocess) — see U1/U2 in
docs/planning/cli-frontend.md and the reference doc's live-proof methodology.

Not a test file itself (pytest's default ``python_files`` glob is
``test_*.py``/``*_test.py`` — this module is never collected). Test files
that need the daemon import fixtures/helpers from here directly, e.g.::

    from tests.daemon_fixture import DaemonHandle, call_tool, daemon_app, wait_until_ready

    @pytest.mark.integration
    def test_something(daemon_app: DaemonHandle) -> None:
        status = anyio.run(wait_until_ready, daemon_app.base_url)
        ...

Deliberately reusable by later phases (Phase 2's CLI sweep, Phase 5's skill
dry-run — see cli-frontend.md's Phase 1 scope note) — nothing here is
specific to this phase's own two tests.

Why the module-reload dance (``_reload_daemon_app_modules``) is necessary:
    ``rust_lsp_mcp.core.mcp`` (and ``rust_lsp_mcp.server.mcp``) are built
    exactly ONCE per process, at first import — the whole point of Phase 1's
    "process-level" contract (D2). By the time any integration test runs,
    ``rust_lsp_mcp.core`` has already been imported by pytest's COLLECTION
    pass (which imports every test module up front, before any fixture runs)
    with ``RLM_TRANSPORT`` unset (stdio default). Two things make a plain
    "just re-import with a different env" approach impossible:

    1. Python caches modules — importing ``rust_lsp_mcp.core`` again after
       setting ``RLM_TRANSPORT=streamable-http`` is a no-op; the cached
       stdio-built ``mcp`` object is returned unchanged.
    2. Even mutating ``mcp.settings.transport``-equivalent fields after the
       fact would not help: FastMCP's low-level ``Server`` captures its
       lifespan PERMANENTLY at ``__init__`` time
       (``self._mcp_server = MCPServer(..., lifespan=(lifespan_wrapper(...)
       if self.settings.lifespan else default_lifespan))`` —
       mcp/server/fastmcp/server.py). A stdio-built instance's low-level
       server would keep running ``_lifespan`` per-session/per-request
       forever, exactly the bug D2 exists to prevent — it can never
       legally be repurposed as an HTTP daemon after construction.

    So this module forces a full reload, in dependency order, of
    ``rust_lsp_mcp.core`` (rebuilds ``mcp`` via ``_build_mcp(get_settings())``
    against the now-current env), every already-imported
    ``rust_lsp_mcp.tools.*`` submodule (each one re-runs its
    ``from rust_lsp_mcp.core import mcp`` + ``@mcp.tool()`` decoration,
    attaching every tool onto the FRESH instance — ``tools/__init__.py``'s
    own ``_register_all()`` uses ``importlib.import_module``, a no-op for
    already-cached modules, so it cannot do this on its own), and
    ``rust_lsp_mcp.server`` (rebinds ``server.mcp`` / ``server._lifespan`` /
    ``server.find_symbol``).

    This is safe within the shared pytest process because this fixture is
    session-scoped and used only by ``integration``-marked tests: no other
    test module in this suite reads ``core.mcp``/``server.mcp`` from inside a
    test BODY (only ``tests/test_tool_registration.py`` touches it, and it
    carries no ``integration`` marker, so with ``-m integration`` its test
    bodies never execute) — and every test module's own
    ``from rust_lsp_mcp.core import mcp``-style binding was already resolved
    at collection time, before this fixture's setup ever runs, so the reload
    cannot retroactively change what an unrelated test observes.
"""

import importlib
import json
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import anyio
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

_TOOLS_PREFIX = "rust_lsp_mcp.tools."

# Generous — a cold ripgrep index can take several minutes (see
# tests/test_phase1_integration.py's 300s budget for the same reason).
DEFAULT_READY_DEADLINE_SECONDS = 300.0


def _reload_daemon_app_modules() -> None:
    """Reload core -> tools submodules -> server so `core.mcp`/`server.mcp`
    are rebuilt from the environment now in effect. See the module
    docstring for the full "why" — this is the mechanical "how".
    """
    import rust_lsp_mcp.core as core_mod

    importlib.reload(core_mod)

    # Reload every already-imported tools submodule so its `@mcp.tool()`
    # decoration re-runs against the NEW `core.mcp` from the reload above.
    # Sorted for deterministic iteration order (harmless either way — each
    # submodule only decorates its own function — but deterministic is
    # easier to debug if one ever raises on reload).
    tool_module_names = sorted(name for name in sys.modules if name.startswith(_TOOLS_PREFIX))
    for name in tool_module_names:
        importlib.reload(sys.modules[name])

    # Re-run tools/__init__.py's own _register_all() too, for completeness —
    # importlib.import_module is a no-op for the (now-fresh) already-cached
    # submodules above, so this is belt-and-suspenders, not load-bearing.
    import rust_lsp_mcp.tools as tools_mod

    importlib.reload(tools_mod)

    # Rebind server.mcp / server._lifespan / server.find_symbol to the fresh
    # objects — server.py is what test code and production `main()` drive.
    import rust_lsp_mcp.server as server_mod

    importlib.reload(server_mod)


def _build_composed_app(server_mod: Any) -> Any:
    """Mirror `server_mod._run_streamable_http`'s app-construction half.

    Deliberately does NOT call `_run_streamable_http` itself: that function
    ends in a blocking `uvicorn.run()` with no handle back to the caller (no
    ephemeral-port readback, no graceful-shutdown hook) — exactly the
    convenience/no-injection-hook problem D2 identifies with
    `run_streamable_http_async()`. The fixture needs its own
    `uvicorn.Server` for lifecycle control, but reuses the REAL
    `compose_daemon_lifespan` production function, so this test exercises
    the actual shipped composition logic, not a re-implementation of it.
    """
    app = server_mod.mcp.streamable_http_app()
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = server_mod.compose_daemon_lifespan(original_lifespan)
    return app


@dataclass
class DaemonHandle:
    """A running in-process streamable-HTTP daemon, for integration tests.

    ``base_url`` is the daemon's `/mcp` endpoint (real uvicorn server, real
    production lifespan wiring, bound to 127.0.0.1 on an OS-assigned
    ephemeral port) running in a background thread of THIS pytest process.
    ``core_mod``/``server_mod`` are the reloaded modules, for in-process
    introspection — the whole reason this fixture runs in-process rather
    than via podman exec / a subprocess: ``session_manager()._server_instances``
    and ``core_mod.get_manager()`` are Python objects, not observable across
    a process boundary (see the module docstring on U1/U2).
    """

    base_url: str
    core_mod: Any
    server_mod: Any
    uvicorn_server: uvicorn.Server
    thread: threading.Thread

    def session_manager(self) -> Any:
        """The live `StreamableHTTPSessionManager` backing this daemon."""
        return self.server_mod.mcp.session_manager


@pytest.fixture(scope="session")
def daemon_app() -> Iterator[DaemonHandle]:
    """Start the real streamable-HTTP daemon once per test session.

    Session-scoped deliberately: the whole point of the "warm daemon" claim
    under test is that a single process pays the cold-index cost once and
    every subsequent connection reuses it — a function-scoped fixture that
    restarted the daemon per test would defeat the point (and re-pay the
    multi-minute cold ripgrep index every test).
    """
    mp = pytest.MonkeyPatch()
    mp.setenv("RLM_TRANSPORT", "streamable-http")
    try:
        _reload_daemon_app_modules()
        import rust_lsp_mcp.core as core_mod
        import rust_lsp_mcp.server as server_mod

        app = _build_composed_app(server_mod)
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        uvicorn_server = uvicorn.Server(config)

        thread = threading.Thread(
            target=uvicorn_server.run, name="daemon-fixture-uvicorn", daemon=True
        )
        thread.start()

        start_deadline = time.monotonic() + 30.0
        while not uvicorn_server.started:
            if time.monotonic() > start_deadline:
                raise RuntimeError("daemon_app fixture: uvicorn did not start within 30s")
            time.sleep(0.05)

        port = uvicorn_server.servers[0].sockets[0].getsockname()[1]
        handle = DaemonHandle(
            base_url=f"http://127.0.0.1:{port}/mcp",
            core_mod=core_mod,
            server_mod=server_mod,
            uvicorn_server=uvicorn_server,
            thread=thread,
        )
        try:
            yield handle
        finally:
            # Triggers the ASGI shutdown event -> exits our composed
            # lifespan -> runs the REAL core._lifespan teardown (clears
            # _manager, doc store) exactly as a real daemon shutdown would.
            uvicorn_server.should_exit = True
            thread.join(timeout=30.0)
    finally:
        mp.undo()


# ---------------------------------------------------------------------------
# Raw MCP client driver — mcp.client.streamable_http + ClientSession.
# ---------------------------------------------------------------------------


async def call_tool(
    base_url: str, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call an MCP tool over streamable-HTTP; return the parsed envelope.

    A fresh `streamablehttp_client` + `ClientSession` per call — deliberately
    NOT reused across calls, matching how the CLI (Phase 2) and any other
    short-lived client will actually talk to the daemon: one connection per
    invocation, relying on the daemon (not the client) to stay warm.

    Parses ``content[0].text`` — per D8 (docs/planning/cli-frontend.md) that
    is the authoritative full envelope JSON regardless of `structuredContent`
    annotation drift.
    """
    async with (
        streamablehttp_client(base_url) as (read, write, _get_session_id),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(name, arguments or {})
        # content[0] is TextContent for every tool in this server (all
        # `-> dict[str, Any]`, JSON-serialized) — the union covers other
        # content kinds (images, embedded resources) this server never emits.
        text = result.content[0].text  # ty: ignore[unresolved-attribute]
        return json.loads(text)


async def wait_until_ready(
    base_url: str, deadline_seconds: float = DEFAULT_READY_DEADLINE_SECONDS
) -> dict[str, Any]:
    """Poll `status` until `state == "ready"`, or raise `TimeoutError`."""
    deadline = time.monotonic() + deadline_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await call_tool(base_url, "status")
        if last.get("state") == "ready":
            return last
        await anyio.sleep(1.0)
    raise TimeoutError(
        f"daemon never reached ready within {deadline_seconds}s; last status={last!r}"
    )
