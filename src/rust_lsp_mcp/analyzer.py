"""Analyzer lifecycle management for rust-lsp-mcp.

Responsibilities:
    1. ``PatchedRustAnalyzer`` — subclass of multilspy's ``RustAnalyzer`` that
       overrides ``setup_runtime_dependencies`` to return the container's native
       rust-analyzer binary path instead of downloading one.  This bypasses
       multilspy's download table (which has no linux-arm64 entry and pins a stale
       2023-10-09 build).

    2. ``AnalyzerManager`` — lifecycle manager that:
       - Spawns ``start_server()`` as a background asyncio Task so the MCP server
         remains responsive while indexing is in progress.
       - Maintains a ``state`` flag (``"indexing"`` → ``"ready"``) that flips only
         after the context is live (i.e. rust-analyzer reports quiescent).
       - Exposes a narrow ``request_workspace_symbol`` delegate (Phase 2) so tools
         can call the live analyzer without accessing the raw LSP object.
       - Provides a clean ``shutdown()`` coroutine for teardown.

Refresh seam (Phase 4, NOT implemented here):
    Refresh is **not** implemented in Phase 1.  ``state`` is written only in
    ``__init__`` (→ ``"indexing"``) and in ``_run`` once the context is live
    (→ ``"ready"``); it is **never** reset to ``"indexing"`` on teardown or
    shutdown.  A future Phase-4 ``restart()`` MUST set ``state = STATE_INDEXING``
    as its **first** action — before cancelling or awaiting the old task — so
    that callers never observe a stale ``"ready"`` during re-indexing.  Omitting
    that reset would create a window where the invariant "state is ``ready`` only
    when the LSP context is live" is violated.

Live LSP exposure (Phase 2):
    ``_lsp`` is set to the live ``PatchedRustAnalyzer`` instance *only* while inside
    the ``start_server()`` context (i.e. exactly when ``state == "ready"``).  It is
    ``None`` before the context is entered and after it exits.  Callers must never
    access ``_lsp`` directly; use ``request_workspace_symbol()`` instead.  That
    method guards against ``_lsp`` being ``None``, enforcing the invariant that the
    LSP object is never reachable before ready or after teardown via the public API.

Instantiation note (verified against multilspy 0.0.15 source):
    ``LanguageServer.create()`` hard-codes ``RustAnalyzer`` for ``Language.RUST``
    and will not pick up our subclass.  We must instantiate ``PatchedRustAnalyzer``
    directly: ``PatchedRustAnalyzer(config, logger, repository_root_path)``.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from multilspy.language_servers.rust_analyzer.rust_analyzer import RustAnalyzer
from multilspy.multilspy_config import Language, MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger
from multilspy.multilspy_types import UnifiedSymbolInformation

# Readiness flag values — treated as an opaque string by callers.
STATE_INDEXING = "indexing"
STATE_READY = "ready"

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Patched subclass — bypasses multilspy's download table
# ---------------------------------------------------------------------------


class PatchedRustAnalyzer(RustAnalyzer):
    """RustAnalyzer subclass that uses the container's native rust-analyzer binary.

    Override contract (verified vs multilspy 0.0.15 source):
        ``setup_runtime_dependencies(self, logger, config) -> str``
        is called from ``RustAnalyzer.__init__`` and its return value (a str path)
        is passed directly to ``ProcessLaunchInfo(cmd=...)``.  Our override simply
        returns the preconfigured binary path — no download, no archive extraction.
    """

    def __init__(
        self,
        config: MultilspyConfig,
        logger: MultilspyLogger,
        repository_root_path: str,
        rust_analyzer_bin: str,
    ) -> None:
        # Store binary path before super().__init__ so the override can see it.
        # RustAnalyzer.__init__ calls setup_runtime_dependencies immediately.
        self._rust_analyzer_bin = rust_analyzer_bin
        super().__init__(config, logger, repository_root_path)

    def setup_runtime_dependencies(  # type: ignore[override]
        self, logger: MultilspyLogger, config: MultilspyConfig
    ) -> str:
        """Return the native binary path, skipping multilspy's download logic."""
        _log.info("PatchedRustAnalyzer: using binary at %s", self._rust_analyzer_bin)
        return self._rust_analyzer_bin


# ---------------------------------------------------------------------------
# Lifecycle manager
# ---------------------------------------------------------------------------


class AnalyzerManager:
    """Manages the rust-analyzer lifecycle for the MCP server.

    Usage (inside FastMCP lifespan)::

        manager = AnalyzerManager(rust_analyzer_bin=..., repository_root=...)
        await manager.start()
        try:
            yield  # MCP server runs here; manager.state flips to "ready" soon
        finally:
            await manager.shutdown()

    The ``state`` attribute is ``"indexing"`` until the ``start_server()`` context
    manager yields (i.e. rust-analyzer has reported quiescent), then ``"ready"``.
    It never blocks the caller — callers must check ``state`` themselves via
    ``require_ready`` or inspect ``manager.state`` directly.
    """

    def __init__(self, rust_analyzer_bin: str, repository_root: str) -> None:
        self._rust_analyzer_bin = rust_analyzer_bin
        self._repository_root = repository_root
        self.state: str = STATE_INDEXING
        self._task: asyncio.Task[None] | None = None
        self._ready_event: asyncio.Event = asyncio.Event()
        self._shutdown_event: asyncio.Event = asyncio.Event()
        # Live LSP instance — set only while inside start_server() context.
        # None before ready and after teardown.  Access via request_workspace_symbol().
        self._lsp: PatchedRustAnalyzer | None = None

    @property
    def repository_root(self) -> str:
        """The workspace root path passed at construction time."""
        return self._repository_root

    async def start(self) -> None:
        """Spawn the background indexing task.  Returns immediately."""
        self._task = asyncio.create_task(self._run(), name="analyzer-lifecycle")

    async def _run(self) -> None:
        """Background task: enter start_server(), flip state, wait for shutdown."""
        config = MultilspyConfig(code_language=Language.RUST)
        logger = MultilspyLogger()
        lsp = PatchedRustAnalyzer(
            config=config,
            logger=logger,
            repository_root_path=self._repository_root,
            rust_analyzer_bin=self._rust_analyzer_bin,
        )
        try:
            async with lsp.start_server():
                # We are now inside the context: indexing is complete (quiescent).
                # Expose the live instance before flipping state so that any
                # caller that sees state=ready is guaranteed _lsp is set.
                self._lsp = lsp
                self.state = STATE_READY
                self._ready_event.set()
                _log.info("AnalyzerManager: rust-analyzer ready (state=ready)")
                # Hold the context open until shutdown is requested.
                await self._shutdown_event.wait()
        except asyncio.CancelledError:
            _log.info("AnalyzerManager: background task cancelled")
            raise
        except Exception:
            _log.exception("AnalyzerManager: unexpected error in background task")
            raise
        finally:
            # Clear the reference on any exit path so the object is never
            # reachable after the start_server() context has exited.
            self._lsp = None

    async def request_workspace_symbol(self, query: str) -> list[UnifiedSymbolInformation] | None:
        """Delegate workspace-symbol query to the live LSP instance.

        Invariant: only callable while ``state == "ready"`` (i.e. ``_lsp`` is set).
        Callers MUST check ``require_ready()`` before calling this method; if they
        do not, this method raises ``RuntimeError`` rather than silently returning
        stale or empty data.

        Args:
            query: The symbol query string forwarded to rust-analyzer.

        Returns:
            A list of ``UnifiedSymbolInformation`` dicts (possibly empty), or
            ``None`` if the LSP returned no result.

        Raises:
            RuntimeError: If the manager is not in the ready state (defensive guard).
        """
        if self._lsp is None or self.state != STATE_READY:
            raise RuntimeError(
                "request_workspace_symbol called before analyzer is ready — "
                "call require_ready() first"
            )
        return await self._lsp.request_workspace_symbol(query)

    async def shutdown(self) -> None:
        """Signal shutdown and wait for the background task to finish.

        Signals the shutdown event so ``_run`` exits its ``start_server()``
        context cleanly (triggering the server's own shutdown/stop sequence).

        Exception draining: if the task has already finished (e.g. it raised
        unexpectedly), its exception is retrieved here so asyncio does not emit
        a "Task exception was never retrieved" warning at GC time.
        """
        self._shutdown_event.set()
        if self._task is None:
            return
        if not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except TimeoutError:
                _log.warning("AnalyzerManager: shutdown timed out; cancelling task")
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task
        # Drain any stored exception so GC does not warn about an un-retrieved
        # task exception.  This covers: (a) task already done when we arrived,
        # (b) task finished normally during wait_for above, (c) task that was
        # already cancelled before shutdown was called.
        if self._task.done() and not self._task.cancelled():
            exc = self._task.exception()
            if exc is not None:
                _log.debug(
                    "AnalyzerManager: drained stored task exception on shutdown: %r",
                    exc,
                )


# ---------------------------------------------------------------------------
# FastMCP lifespan async context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def analyzer_lifespan(app: object) -> AsyncIterator[dict[str, AnalyzerManager]]:
    """FastMCP lifespan context manager.

    Starts the ``AnalyzerManager`` background task when the MCP server boots,
    and tears it down cleanly on exit.  The manager is exposed via FastMCP's
    context so tools can retrieve it (but the pattern in this codebase is to
    access the module-level singleton directly for simplicity).

    Usage with FastMCP::

        mcp = FastMCP("rust-lsp-mcp", lifespan=analyzer_lifespan)

    The lifespan callable receives the FastMCP app instance but we don't need it
    here; the manager stores its own state.
    """
    from rust_lsp_mcp.settings import get_settings

    settings = get_settings()
    manager = AnalyzerManager(
        rust_analyzer_bin=settings.rust_analyzer_bin,
        repository_root=settings.ripgrep_src,
    )
    await manager.start()
    try:
        yield {"manager": manager}
    finally:
        await manager.shutdown()
