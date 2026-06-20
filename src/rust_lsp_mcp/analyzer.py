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
       - Provides a clean ``shutdown()`` coroutine for teardown.

Refresh seam:
    A future ``refresh`` operation can call ``manager.restart()`` (not implemented
    here) which would: set state back to ``"indexing"``, cancel/await the existing
    background task, then re-enter ``start_server()`` in a new task.  The
    ``start_server()`` context manager blocks until quiescent on each entry, so
    gating is automatically correct across refreshes.  The flag must be reset to
    ``"indexing"`` *before* the new task starts so no window exists where callers
    see a stale ``"ready"``.

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

    async def start(self) -> None:
        """Spawn the background indexing task.  Returns immediately."""
        self._task = asyncio.get_event_loop().create_task(self._run(), name="analyzer-lifecycle")

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

    async def shutdown(self) -> None:
        """Signal shutdown and wait for the background task to finish.

        Signals the shutdown event so ``_run`` exits its ``start_server()``
        context cleanly (triggering the server's own shutdown/stop sequence).
        """
        self._shutdown_event.set()
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except TimeoutError:
                _log.warning("AnalyzerManager: shutdown timed out; cancelling task")
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task


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
