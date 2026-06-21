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
       - Exposes narrow LSP delegate methods (Phase 2 / Phase 3-4) so tools
         can call the live analyzer without accessing the raw LSP object.
       - Provides a clean ``shutdown()`` coroutine for teardown.
       - Provides a ``restart()`` coroutine for live re-indexing (Phase 4).

Refresh seam (implemented in Phase 3-4):
    ``restart()`` resets ``state = STATE_INDEXING`` as its **very first** action —
    before signalling or awaiting the old task — so callers never observe a stale
    ``"ready"`` during re-indexing.  After the old task is torn down cleanly (same
    drain logic as ``shutdown()``), fresh ``asyncio.Event`` objects are created
    (the old ones are set/consumed and cannot be reused) and ``start()`` is called
    again to spawn a new background ``_run`` coroutine.  The new ``_run`` captures
    ``indexed_commit`` from ``git rev-parse HEAD`` at start, so the commit reflects
    the tree being indexed after each restart.

Live LSP exposure (Phase 2):
    ``_lsp`` is set to the live ``PatchedRustAnalyzer`` instance *only* while inside
    the ``start_server()`` context (i.e. exactly when ``state == "ready"``).  It is
    ``None`` before the context is entered and after it exits.  Callers must never
    access ``_lsp`` directly; use the delegate methods instead.  Each delegate
    guards against ``_lsp`` being ``None``, enforcing the invariant that the
    LSP object is never reachable before ready or after teardown via the public API.

Instantiation note (verified against multilspy 0.0.15 source):
    ``LanguageServer.create()`` hard-codes ``RustAnalyzer`` for ``Language.RUST``
    and will not pick up our subclass.  We must instantiate ``PatchedRustAnalyzer``
    directly: ``PatchedRustAnalyzer(config, logger, repository_root_path)``.
"""

import asyncio
import contextlib
import logging
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from multilspy.language_servers.rust_analyzer.rust_analyzer import RustAnalyzer
from multilspy.multilspy_config import Language, MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger
from multilspy.multilspy_types import Hover, Location, UnifiedSymbolInformation

# Readiness flag values — treated as an opaque string by callers.
STATE_INDEXING = "indexing"
STATE_READY = "ready"

_log = logging.getLogger(__name__)


def _is_null_response_assertion(exc: AssertionError) -> bool:
    """Whether a multilspy AssertionError signals a *null* LSP response.

    multilspy 0.0.15 raises ``AssertionError`` for two very different conditions
    in ``request_definition`` / ``request_references``:

    1. The LSP returned JSON-RPC ``null`` — i.e. there is no symbol at the
       requested position.  multilspy's shape asserts then fail with a message
       ending in the response repr ``None`` (e.g.
       ``"Unexpected response from Language Server: None"``).  This is a normal
       *resolution-failed* outcome → callers should map it to ``not_found``.
    2. The LSP returned a *malformed but non-null* payload (a list item missing
       ``uri``/``range``, or a response that is neither Location nor LocationLink).
       That is a genuine protocol failure → callers must surface ``error``.

    We must NOT collapse (2) into ``not_found`` (that would hide a real failure
    behind a misleading "nothing here").  Only (1) — message ending in ``None`` —
    is the null case.  Tied to multilspy 0.0.15 (pinned); the adversarial
    regression tests guard both branches if the message ever changes.
    """
    return str(exc).rstrip().endswith("None")


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
        # None before ready and after teardown.  Access via delegate methods.
        self._lsp: PatchedRustAnalyzer | None = None
        # Git commit hash of the tree being indexed; None until _run captures it.
        self._indexed_commit: str | None = None

    @property
    def repository_root(self) -> str:
        """The workspace root path passed at construction time."""
        return self._repository_root

    @property
    def indexed_commit(self) -> str | None:
        """Git commit hash of the tree currently indexed, or None if not yet known."""
        return self._indexed_commit

    @property
    def is_ready(self) -> bool:
        """True only when state is "ready" AND the live LSP context is set.

        This guards the teardown window where _run's finally has cleared
        _lsp but state has not been reset (Phase 4 owns that reset).
        Callers must use this instead of checking state == STATE_READY alone.
        """
        return self.state == STATE_READY and self._lsp is not None

    async def start(self) -> None:
        """Spawn the background indexing task.  Returns immediately."""
        self._task = asyncio.create_task(self._run(), name="analyzer-lifecycle")

    async def _capture_head_commit(self) -> None:
        """Capture git HEAD commit into ``_indexed_commit``.

        Run ``git -C <repo> rev-parse HEAD`` in a thread.  On any failure
        (not a git repo, git missing, etc.) set ``_indexed_commit`` to None
        and log at debug — never raise.
        """
        try:

            def _run_git() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["git", "-C", self._repository_root, "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                )

            result = await asyncio.to_thread(_run_git)
            if result.returncode == 0:
                self._indexed_commit = result.stdout.strip()
            else:
                _log.debug(
                    "AnalyzerManager: git rev-parse HEAD failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
                self._indexed_commit = None
        except Exception:
            _log.debug("AnalyzerManager: could not capture HEAD commit", exc_info=True)
            self._indexed_commit = None

    async def _run(self) -> None:
        """Background task: enter start_server(), flip state, wait for shutdown."""
        # Capture the commit being indexed before starting the server.
        await self._capture_head_commit()

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

    # -----------------------------------------------------------------------
    # LSP delegate methods — each guards against not-ready state
    # -----------------------------------------------------------------------

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

    async def request_document_symbols(
        self, relative_file_path: str
    ) -> list[UnifiedSymbolInformation]:
        """Delegate document-symbols query to the live LSP instance.

        Returns the flat list of symbols (element [0] of the tuple returned by
        multilspy); the tree representation (element [1]) is discarded.

        Raises:
            RuntimeError: If the manager is not in the ready state.
        """
        if self._lsp is None or self.state != STATE_READY:
            raise RuntimeError(
                "request_document_symbols called before analyzer is ready — "
                "call require_ready() first"
            )
        result = await self._lsp.request_document_symbols(relative_file_path)
        if result is None:
            return []
        return result[0]

    async def request_definition(
        self, relative_file_path: str, line: int, column: int
    ) -> list[Location] | None:
        """Delegate go-to-definition query to the live LSP instance.

        Args:
            relative_file_path: Workspace-relative path to the file.
            line: 0-indexed line number.
            column: 0-indexed column number.

        Returns:
            A list of ``Location`` dicts (possibly empty), or ``None`` when the
            LSP returned a null response (no symbol at this position).

            ``None`` vs ``[]`` distinction (multilspy 0.0.15 behaviour):
                - ``None``  → rust-analyzer returned JSON-RPC ``null``; multilspy
                  asserts on that shape and raises ``AssertionError`` before
                  returning.  We catch it here and normalise to ``None`` so callers
                  can distinguish "no symbol at this position" (None → not_found)
                  from "symbol with zero definition sites" ([] → ok+empty).
                - ``[]``    → rust-analyzer returned an empty list; resolution
                  succeeded with no hits (rare but valid).

        Raises:
            RuntimeError: If the manager is not in the ready state.
        """
        if self._lsp is None or self.state != STATE_READY:
            raise RuntimeError(
                "request_definition called before analyzer is ready — call require_ready() first"
            )
        try:
            result = await self._lsp.request_definition(relative_file_path, line, column)
        except AssertionError as exc:
            # multilspy 0.0.15 asserts on a null LSP response instead of returning
            # None.  Only the null case → None (→ not_found); a malformed non-null
            # payload is a real protocol failure and must propagate to error.
            if _is_null_response_assertion(exc):
                return None
            raise
        return result

    async def request_references(
        self, relative_file_path: str, line: int, column: int
    ) -> list[Location] | None:
        """Delegate find-references query to the live LSP instance.

        Args:
            relative_file_path: Workspace-relative path to the file.
            line: 0-indexed line number.
            column: 0-indexed column number.

        Returns:
            A list of ``Location`` dicts (possibly empty), or ``None`` when the
            LSP returned a null response (no symbol at this position).

            ``None`` vs ``[]`` distinction (multilspy 0.0.15 behaviour):
                - ``None``  → rust-analyzer returned JSON-RPC ``null``; multilspy
                  asserts on that shape and raises ``AssertionError`` before
                  returning.  We catch it here and normalise to ``None`` so callers
                  can distinguish "no symbol at this position" (None → not_found)
                  from "symbol with zero callers" ([] → ok+empty).
                - ``[]``    → rust-analyzer returned an empty list; the symbol
                  exists but has no in-tree callers (the zero-callers case).

        Raises:
            RuntimeError: If the manager is not in the ready state.
        """
        if self._lsp is None or self.state != STATE_READY:
            raise RuntimeError(
                "request_references called before analyzer is ready — call require_ready() first"
            )
        try:
            result = await self._lsp.request_references(relative_file_path, line, column)
        except AssertionError as exc:
            # multilspy 0.0.15 asserts on a null LSP response instead of returning
            # None.  Only the null case → None (→ not_found); a malformed non-null
            # payload is a real protocol failure and must propagate to error.
            if _is_null_response_assertion(exc):
                return None
            raise
        return result

    async def request_hover(self, relative_file_path: str, line: int, column: int) -> Hover | None:
        """Delegate hover query to the live LSP instance.

        Args:
            relative_file_path: Workspace-relative path to the file.
            line: 0-indexed line number.
            column: 0-indexed column number.

        Returns:
            A ``Hover`` dict, or ``None`` if the LSP returned no hover info.

        Raises:
            RuntimeError: If the manager is not in the ready state.
        """
        if self._lsp is None or self.state != STATE_READY:
            raise RuntimeError(
                "request_hover called before analyzer is ready — call require_ready() first"
            )
        return await self._lsp.request_hover(relative_file_path, line, column)

    # -----------------------------------------------------------------------
    # Lifecycle helpers
    # -----------------------------------------------------------------------

    async def _drain_task(self) -> None:
        """Signal ``_shutdown_event`` and drain the current task to completion.

        Used by both ``shutdown()`` and ``restart()``.  Safe to call when
        ``_task`` is None.
        """
        self._shutdown_event.set()
        if self._task is None:
            return
        if not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except TimeoutError:
                _log.warning("AnalyzerManager: drain timed out; cancelling task")
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
                    "AnalyzerManager: drained stored task exception: %r",
                    exc,
                )

    async def shutdown(self) -> None:
        """Signal shutdown and wait for the background task to finish.

        Signals the shutdown event so ``_run`` exits its ``start_server()``
        context cleanly (triggering the server's own shutdown/stop sequence).

        Exception draining: if the task has already finished (e.g. it raised
        unexpectedly), its exception is retrieved here so asyncio does not emit
        a "Task exception was never retrieved" warning at GC time.
        """
        await self._drain_task()

    async def restart(self) -> None:
        """Re-index the workspace by tearing down and restarting the analyzer.

        State-reset-first contract: ``state`` is set to ``STATE_INDEXING`` as the
        very first action — before signalling or awaiting the old task — so that
        callers never observe a stale ``"ready"`` during re-indexing.

        Sequence:
            1. Set ``self.state = STATE_INDEXING`` (must be first).
            2. Clear ``_indexed_commit`` to None so ``status`` reports an honest
               "unknown" during the re-index window instead of the *previous*
               cycle's commit (which would let ``stale`` read ``false`` mid-reindex).
               The new ``_run`` recaptures it from git HEAD before going ready.
            3. Drain the old task cleanly (same logic as ``shutdown()``).
            4. Replace ``_shutdown_event`` and ``_ready_event`` with fresh instances
               (the old ones are set/consumed and cannot be reused for the next cycle).
            5. Call ``start()`` to spawn a new background ``_run`` task, which
               recaptures ``indexed_commit`` from git HEAD.

        Safe to call even if ``_task`` is None (e.g. before the first ``start()``).
        """
        # Step 1: mark not-ready FIRST so callers see indexing immediately.
        self.state = STATE_INDEXING

        # Step 2: forget the old indexed commit — during the re-index window the
        # previous value is no longer what's indexed; None → status reports
        # indexed_commit=null, stale=null (honest "unknown") until _run recaptures.
        self._indexed_commit = None

        # Step 3: drain the old task.
        await self._drain_task()

        # Step 3: fresh events — old ones are spent.
        self._shutdown_event = asyncio.Event()
        self._ready_event = asyncio.Event()

        # Step 4: spawn new background task (recaptures indexed_commit internally).
        await self.start()


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
        repository_root=settings.project_root,
    )
    await manager.start()
    try:
        yield {"manager": manager}
    finally:
        await manager.shutdown()
