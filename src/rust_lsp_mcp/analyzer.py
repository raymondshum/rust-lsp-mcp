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
       - Maintains a ``state`` flag (``"indexing"`` → ``"ready"``, or ``"error"``
         if the background run raises or rust-analyzer exits on its own) that
         flips to ``"ready"`` only after the context is live (i.e.
         rust-analyzer reports quiescent).
       - Records rust-analyzer's own health report (``analyzer_health``), its
         exit code after a death (``analyzer_exit_code``) and its last stderr
         lines.
       - Exposes narrow LSP delegate methods (Phase 2 / Phase 3-4) so tools
         can call the live analyzer without accessing the raw LSP object.
       - Provides a clean ``shutdown()`` coroutine for teardown.
       - Provides a ``restart()`` coroutine for live re-indexing (Phase 4) —
         this is also the recovery path from ``"error"``: it clears
         ``error_message`` and resets ``state`` to ``"indexing"``.

Refresh seam (implemented in Phase 3-4; races closed in the DS-03/DS-04/DS-21
follow-up):
    ``restart()`` and ``shutdown()`` both serialize behind a single
    ``_lifecycle_lock`` — concurrent callers queue up rather than racing each
    other's teardown/respawn sequence.  ``restart()`` resets
    ``state = STATE_INDEXING`` as its first action after acquiring the lock —
    before signalling or awaiting the old task — so callers never observe a
    stale ``"ready"`` during re-indexing.  A monotonically increasing
    ``_generation`` counter (bumped by ``_drain_task`` before anything else)
    is threaded through each background ``_run`` invocation: a run whose
    generation no longer matches ``self._generation`` has been superseded by
    a later restart and exits immediately, without ever publishing
    ``state``, ``_lsp``, or ``_ready_event`` — this is what stops a
    slow-to-quiesce outgoing run from clobbering a newer one's readiness.
    After the old task is torn down cleanly (same drain logic as
    ``shutdown()``), ``restart()`` re-asserts ``state = STATE_INDEXING`` and
    re-clears ``_indexed_commit`` (defense-in-depth: the outgoing run's
    in-flight commit capture may have landed during the drain window), fresh
    ``asyncio.Event`` objects are created (the old ones are set/consumed and
    cannot be reused), and ``start()`` is called again to spawn a new
    background ``_run`` coroutine.  The new ``_run`` captures
    ``indexed_commit`` from ``git rev-parse HEAD`` at start, so the commit
    reflects the tree being indexed after each restart.  Every ``_run``'s
    ``finally`` clause force-stops the underlying rust-analyzer subprocess
    (``lsp.server.stop()``, idempotent) regardless of how the run exits —
    multilspy's own ``start_server()`` has no try/finally around its
    ``yield``, so a cancellation (e.g. a drain timeout) or a pre-yield
    failure would otherwise leak the subprocess.

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
import collections
import contextlib
import logging
import pathlib
import subprocess
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any, TypeVar, cast

from multilspy.language_servers.rust_analyzer.rust_analyzer import RustAnalyzer
from multilspy.lsp_protocol_handler.lsp_types import InitializeParams
from multilspy.lsp_protocol_handler.server import StopLoopException
from multilspy.multilspy_config import Language, MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger
from multilspy.multilspy_types import Hover, Location, UnifiedSymbolInformation

# Readiness flag values — treated as an opaque string by callers.
STATE_INDEXING = "indexing"
STATE_READY = "ready"
STATE_ERROR = "error"

# How long _drain_task() waits for the outgoing background task to finish on
# its own before cancelling it.  Module-level (not a class default / __init__
# capture) so tests can monkeypatch it and have _drain_task observe the new
# value at call time.
DRAIN_TIMEOUT_SECONDS: float = 10.0

_log = logging.getLogger(__name__)

_T = TypeVar("_T")

# Signature of the ``$/progress`` notification handler a manager hands to
# ``PatchedRustAnalyzer``: multilspy awaits notification handlers with the
# notification's ``params`` (``lsp_protocol_handler/server.py`` line 557).
ProgressHandler = Callable[[Any], Coroutine[Any, Any, None]]

# Signature of the health callback a manager hands to ``PatchedRustAnalyzer``
# as ``status_handler``: awaited with the ``health`` (``"ok"``, ``"warning"``
# or ``"error"``) and ``message`` fields of each ``experimental/serverStatus``
# notification.
StatusHandler = Callable[[Any, Any], Coroutine[Any, Any, None]]

# How many rust-analyzer stderr lines the manager keeps, and how many of the
# newest ones go into the error message after the process dies.
STDERR_TAIL_LINES = 200
STDERR_MESSAGE_LINES = 20

# Poll interval while waiting for multilspy to assign ``lsp.server.process``
# (None until ``LanguageServerHandler.start()`` spawns the process,
# ``lsp_protocol_handler/server.py`` line 217).
PROCESS_POLL_SECONDS: float = 0.05

# Interval at which the exit watch reads ``process.returncode`` beside
# ``process.wait()``.  On CPython 3.12 (the image) ``wait()`` resolves only
# after every pipe closes, and a proc-macro-srv child holding rust-analyzer's
# inherited stderr pipe keeps it open after rust-analyzer dies; the child
# watcher sets ``returncode`` as soon as the process exits.
EXIT_POLL_SECONDS: float = 0.5

# Exit status /bin/sh reports when it cannot find the command.  multilspy
# launches rust-analyzer through ``create_subprocess_shell``, so a missing
# binary is the shell exiting 127, not an exception from the entry.
SHELL_COMMAND_NOT_FOUND = 127

# How long the death path waits for the stderr reader to reach EOF, so the
# error message carries the dying process's last lines.  Module-level so
# tests can shorten it.
STDERR_DRAIN_SECONDS: float = 1.0


class _AnalyzerProcessExited(Exception):
    """Raised inside the ready context to leave it after rust-analyzer died.

    Leaving by exception skips multilspy's own exit path (a ``shutdown``
    request to a dead process); ``_run``'s ``finally`` force-stops instead.
    Never escapes ``_run``.
    """


# rust-analyzer work-done progress tokens whose ``end`` means the index now
# reflects the tree on disk: cache priming, the workspace fetch and the VFS
# roots scan.  Only these trigger the ``indexed_commit`` re-capture; any other
# token (flycheck's ``rust-analyzer/flycheck/<n>`` included) says nothing
# about which tree the index holds (R-gate falsification, 2026-11-04: with
# the watcher dead, a flycheck ``end`` set ``stale: false`` while the index
# held the old branch).
REINDEX_PROGRESS_TOKENS: frozenset[str] = frozenset(
    {"rustAnalyzer/cachePriming", "rustAnalyzer/Fetching", "rustAnalyzer/Roots Scanned"}
)


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string (seconds precision)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class AnalyzerTornDownError(RuntimeError):
    """The analyzer run serving an in-flight request was torn down (restart/shutdown)
    before the request completed.  Maps to a not_ready envelope at the tool layer."""


class AnalyzerNotReadyError(RuntimeError):
    """Raised by the delegate guards when the manager is not ready at call time.

    A ``RuntimeError`` subclass for backward compatibility — existing callers
    and tests that catch/assert ``RuntimeError`` keep working unchanged.  Tools
    map it via ``require_ready()``'s canonical classification (see
    ``rust_lsp_mcp.core.require_ready``): the same guard trips both while
    still indexing and after a permanent failure (``state == "error"``), so
    the tool layer must re-consult ``require_ready()`` rather than assume
    ``not_ready`` — see #98.
    """


# Canonical tool-layer not_ready message for AnalyzerTornDownError — every tool
# call site returns this verbatim.
TORN_DOWN_RETRY_MESSAGE = (
    "The analyzer was restarted or shut down while this request was in flight. "
    "Retry after analyzer_status reports ready."
)

# Canonical not_ready message for the everyday "still indexing" case — used by
# require_ready() (core.py) and every gated tool's ``else: not_ready(...)``
# fallback.  Names the exact poll tool and field/value to await, mirroring
# TORN_DOWN_RETRY_MESSAGE's specificity.
INDEXING_RETRY_MESSAGE = (
    "The analyzer is still indexing. Retry after analyzer_status reports state == 'ready'."
)


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
        progress_handler: ProgressHandler | None = None,
        status_handler: StatusHandler | None = None,
    ) -> None:
        # Store binary path before super().__init__ so the override can see it.
        # RustAnalyzer.__init__ calls setup_runtime_dependencies immediately.
        self._rust_analyzer_bin = rust_analyzer_bin
        # Registered for ``$/progress`` by _get_initialize_params (see there).
        self._progress_handler = progress_handler
        # Called by _on_server_status with each health report (SW-07).
        self._status_handler = status_handler
        super().__init__(config, logger, repository_root_path)

    def setup_runtime_dependencies(  # type: ignore[override]
        self, logger: MultilspyLogger, config: MultilspyConfig
    ) -> str:
        """Return the native binary path, skipping multilspy's download logic."""
        _log.info("PatchedRustAnalyzer: using binary at %s", self._rust_analyzer_bin)
        return self._rust_analyzer_bin

    def _get_initialize_params(self, repository_absolute_path: str) -> InitializeParams:
        """Override multilspy's initialize parameters and two notification handlers.

        **Position encoding.** Advertise UTF-32 to ensure Unicode codepoint offsets.

        multilspy's bundled init params request ``positionEncodings: ["utf-16"]``,
        so rust-analyzer reports character offsets as UTF-16 code units — wrong on
        non-ASCII (astral) lines.  We instead advertise ``["utf-32", "utf-16"]`` so
        rust-analyzer reports **Unicode codepoint** offsets (verified supported —
        see docs/reference/lsp-position-encoding.md).  ``utf-16`` is listed second
        purely as a protocol-level fallback for a server that lacks utf-32; the
        positions then stay UTF-16 (the pre-fix behaviour), never an error.

        All offset handling downstream (positions.py, the tool layer) is
        encoding-agnostic — it forwards whatever the negotiated encoding yields —
        so changing the negotiation is sufficient; no per-line transcoding.

        **File watching (SW-03).** Set ``initializationOptions.files.watcher``
        to ``"server"``.  multilspy's bundled ``"client"`` makes rust-analyzer
        register a file-watch with the client, and multilspy accepts that
        registration but never sends a ``workspace/didChangeWatchedFiles``
        event, so a new file after a branch switch, a pull or an agent's edit
        never reached the index without ``refresh``.  With ``"server"``,
        rust-analyzer watches the project tree itself.

        **Progress capture (SW-12).** Bind ``$/progress`` to the manager's
        ``progress_handler``.  multilspy's ``start_server()`` binds
        ``$/progress`` to ``do_nothing`` (``rust_analyzer.py`` line 152) and
        then calls this method (line 160), before ``initialize`` is sent and
        before the ready wait (line 177).  ``on_notification`` stores the
        callback in a dict keyed by method (``lsp_protocol_handler/server.py``
        line 508), so this later binding replaces ``do_nothing`` and every
        indexing progress event reaches the handler.  Rebinding after
        ``start_server()`` yields would be too late: the yield comes after the
        ready wait, so all indexing progress would already be lost.

        **Symbol search (SW-05).** Set ``workspace.symbol.search.kind`` to
        ``"all_symbols"`` (multilspy bundles ``"only_types"``, so a
        ``find_symbol`` query never returned a function) and ``limit`` to
        ``MAX_LIST_RESULTS + 1``: one item past the tool's cap, so
        ``cap_list_results`` can see that rust-analyzer had more than 200
        matches and set ``truncated``.

        **No ``cargo check`` on load (SW-09).** Set ``checkOnSave`` to
        ``False``.  The server exposes no diagnostics from that check, and the
        check holds the cargo lock on the shared target directory.

        **``cargo.cfgs`` removed.** multilspy bundles it as ``{}``, which
        current rust-analyzer rejects ("invalid type: map, expected a
        sequence") with a ``"warning"`` health report on every load; that
        masked the health signal below.

        **Health report (SW-07).** Bind ``experimental/serverStatus`` to
        :meth:`_on_server_status` in place of multilspy's
        ``check_experimental_status`` closure (registered at line 155, before
        this method runs; same timing argument as ``$/progress`` above).
        """
        # Deferred import: core imports this module at load time.
        from rust_lsp_mcp.core import MAX_LIST_RESULTS

        params = super()._get_initialize_params(repository_absolute_path)
        # The bundled JSON always carries capabilities.general and
        # initializationOptions.files / .workspace.symbol.search; assert-fail
        # loudly rather than silently no-op if multilspy ever restructures them.
        raw = cast(dict[str, Any], params)
        general = raw["capabilities"]["general"]
        general["positionEncodings"] = ["utf-32", "utf-16"]
        options = raw["initializationOptions"]
        options["files"]["watcher"] = "server"
        search = options["workspace"]["symbol"]["search"]
        search["kind"] = "all_symbols"
        search["limit"] = MAX_LIST_RESULTS + 1
        options["checkOnSave"] = False
        # multilspy bundles cargo.cfgs as {}; rust-analyzer wants a list and
        # reports health "warning" on every load, masking real warnings.
        options["cargo"].pop("cfgs", None)
        if self._progress_handler is not None:
            self.server.on_notification("$/progress", self._progress_handler)
        self.server.on_notification("experimental/serverStatus", self._on_server_status)
        return params

    async def _on_server_status(self, params: Any) -> None:
        """Handle rust-analyzer's ``experimental/serverStatus`` notification.

        Re-implements multilspy's ``check_experimental_status`` (a closure
        inside ``start_server()``, so it cannot be called from here): a
        ``quiescent: true`` report sets ``server_ready``, which releases
        ``start_server()``'s ready wait.  Then passes the report's ``health``
        and ``message`` to the ``status_handler`` given to the constructor.
        """
        if not isinstance(params, dict):
            return
        if params.get("quiescent") is True:
            self.server_ready.set()
        if self._status_handler is not None:
            await self._status_handler(params.get("health"), params.get("message"))

    @contextlib.contextmanager
    def open_file(self, relative_file_path: str) -> Iterator[None]:
        """Wrap multilspy's ``open_file`` so a failed request releases its buffer (SW-08).

        multilspy's ``open_file`` (``language_server.py`` lines 198 to 249)
        counts the users of one file in ``open_file_buffers[uri].ref_count``
        and sends ``didClose`` when the count reaches zero, but has no
        ``try/finally`` around its ``yield``: a request that raised (or was
        cancelled) left the count above zero, so the file stayed open and its
        buffer never refreshed from disk again.

        On any exception from the body, ``asyncio.CancelledError`` included,
        this decrements the count by one; only when it reaches zero does it
        send ``didClose`` and remove the entry.  Then it re-raises.  While the
        count is above zero another request still holds the buffer, and
        multilspy's exit path for that request decrements the entry (it would
        raise ``KeyError`` if the entry were gone).  The success path is
        multilspy's, unchanged.
        """
        # Same URI derivation as multilspy's open_file.
        absolute_file_path = str(PurePath(self.repository_root_path, relative_file_path))
        uri = pathlib.Path(absolute_file_path).as_uri()
        inner = super().open_file(relative_file_path)
        inner.__enter__()
        try:
            yield
        except BaseException as exc:
            # Close multilspy's suspended generator.  The exception re-raises
            # at its yield, before its decrement, so this changes nothing; the
            # cleanup below is the decrement it skipped.
            inner.__exit__(type(exc), exc, exc.__traceback__)
            # multilspy's buffer dictionary is LanguageServer.open_file_buffers
            # (language_server.py line 175), keyed by file URI.
            buffer = self.open_file_buffers.get(uri)
            if buffer is not None:
                buffer.ref_count -= 1
                if buffer.ref_count <= 0:
                    # Remove the entry first: a close that raises (a broken
                    # pipe to a dead process) must not strand a zero-count
                    # entry that a later open would reuse without didOpen.
                    del self.open_file_buffers[uri]
                    self.server.notify.did_close_text_document({"textDocument": {"uri": uri}})
            raise
        else:
            inner.__exit__(None, None, None)


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
    If the background run raises before reaching quiescence, ``state`` becomes
    ``"error"`` and ``error_message`` carries a diagnostic string; ``restart()``
    (the ``refresh`` tool) is the recovery path — it clears the error and
    resets ``state`` to ``"indexing"``.  It never blocks the caller — callers
    must check ``state`` themselves via ``require_ready`` or inspect
    ``manager.state`` directly.
    """

    # Class-level defaults for the freshness/progress fields (SW-12), so a
    # manager built without __init__ (test stubs use AnalyzerManager.__new__)
    # still reads them as None.  __init__ and _run assign instance values.
    _indexing_started_at: str | None = None
    _progress_message: str | None = None
    _progress_at: str | None = None
    _head_read_seq: int = 0
    # Health and process-death fields (SW-06, SW-07), same reason.
    analyzer_health: str | None = None
    analyzer_health_message: str | None = None
    analyzer_exit_code: int | None = None

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
        # Re-captured by the progress handler after a watcher re-index.
        self._indexed_commit: str | None = None
        # ISO 8601 UTC time the current run began (top of _run); None before
        # the first run.  Reset by every restart, since each restart runs _run.
        self._indexing_started_at: str | None = None
        # Latest rust-analyzer $/progress title/message and its ISO 8601 UTC
        # arrival time; None until the first progress notification of a run.
        self._progress_message: str | None = None
        self._progress_at: str | None = None
        # Ticket counter for the progress handler's HEAD reads: each re-capture
        # takes the next ticket before its git call and writes only if its
        # ticket is still the latest, so of two overlapping re-captures the
        # older read can never land last.
        self._head_read_seq: int = 0
        # rust-analyzer's own health report from experimental/serverStatus
        # ("ok", "warning" or "error") and its message; None until the first
        # report of a run.  Reset by restart().
        self.analyzer_health: str | None = None
        self.analyzer_health_message: str | None = None
        # Exit code of a rust-analyzer process that died without a shutdown
        # request; None while it runs, before it starts, and after restart().
        self.analyzer_exit_code: int | None = None
        # The last STDERR_TAIL_LINES lines of the current run's rust-analyzer
        # stderr; replaced with an empty deque at the top of every _run.
        self._stderr_lines: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL_LINES)
        # rust-analyzer `--version` output, captured once by start();
        # None until captured, or if capture fails.  Never re-captured by
        # restart() — the binary underlying rust_analyzer_bin cannot change
        # without a process restart, so one capture covers the manager's
        # whole lifetime.
        self._rust_analyzer_version: str | None = None
        self._rust_analyzer_version_captured: bool = False
        # Set (gen-guarded) when a background _run raises; cleared whenever
        # restart() resets state back to STATE_INDEXING (the recovery path).
        self._error: str | None = None
        # Bumped by _drain_task() before anything else; a _run() invocation
        # whose captured generation no longer matches this value has been
        # superseded and must exit without publishing state/_lsp/_ready_event.
        self._generation: int = 0
        # Serializes restart()/shutdown() so concurrent callers queue up
        # instead of racing each other's teardown/respawn sequence.
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        # Set by shutdown(); restart() becomes a no-op once this is True.
        self._closed: bool = False

    @property
    def repository_root(self) -> str:
        """The workspace root path passed at construction time."""
        return self._repository_root

    @property
    def indexed_commit(self) -> str | None:
        """Git commit hash of the tree currently indexed, or None if not yet known."""
        return self._indexed_commit

    @property
    def indexing_started_at(self) -> str | None:
        """ISO 8601 UTC time the current indexing run began, or None before the first."""
        return self._indexing_started_at

    @property
    def progress_message(self) -> str | None:
        """Latest rust-analyzer ``$/progress`` title and message, or None."""
        return self._progress_message

    @property
    def progress_at(self) -> str | None:
        """ISO 8601 UTC time of the latest ``$/progress`` notification, or None."""
        return self._progress_at

    @property
    def rust_analyzer_version(self) -> str | None:
        """Cached ``<rust_analyzer_bin> --version`` output.

        Captured once, synchronously within ``start()``, before the first
        background indexing task is spawned; ``restart()`` does not
        re-capture it (same binary path for the manager's lifetime).  ``None``
        if the binary is missing, exits non-zero, times out, or ``start()``
        has not yet been called.
        """
        return self._rust_analyzer_version

    @property
    def error_message(self) -> str | None:
        """Human-readable ``"{ExceptionType}: {message}"`` if the last run failed.

        ``None`` while indexing/ready, or after ``restart()`` has cleared it.
        Set (gen-guarded) by ``_run``'s ``except Exception`` branch, or by
        ``_record_process_exit`` as ``rust-analyzer exited with code <n>; last
        stderr: <lines>`` when the process dies — never by
        ``asyncio.CancelledError``, since cancellation is not a failure.
        """
        return self._error

    @property
    def is_ready(self) -> bool:
        """True only when state is "ready" AND the live LSP context is set.

        This guards the teardown window where _run's finally has cleared
        _lsp but state has not been reset, and the analogous window during a
        restart() where a superseded run's generation check kept it from
        ever setting _lsp/state in the first place (see the ``_generation``
        mechanism on ``_run``).  Callers must use this instead of checking
        state == STATE_READY alone.
        """
        return self.state == STATE_READY and self._lsp is not None

    async def start(self) -> None:
        """Spawn the background indexing task.  Returns immediately.

        Does NOT acquire ``_lifecycle_lock`` — ``restart()`` calls this while
        already holding the lock.  Never call this directly while a restart
        or shutdown may be concurrently in flight; go through ``restart()``.

        On the FIRST call only, captures ``rust_analyzer_version``
        before spawning the background task (a short, thread-offloaded
        subprocess call — see ``_capture_rust_analyzer_version``).
        ``restart()`` also calls ``start()`` (to respawn after a drain), but
        the ``_rust_analyzer_version_captured`` guard skips re-capture there:
        the binary can't change without a process restart, so one capture
        covers the manager's whole lifetime.
        """
        if not self._rust_analyzer_version_captured:
            await self._capture_rust_analyzer_version()
            self._rust_analyzer_version_captured = True
        self._task = asyncio.create_task(self._run(self._generation), name="analyzer-lifecycle")

    async def _read_head_commit(self) -> str | None:
        """Return ``git -C <repo> rev-parse HEAD``, or None on any failure.

        The git call runs through ``asyncio.to_thread`` so it never blocks
        the event loop.  On any failure (not a git repo, git missing, etc.)
        log at debug and return None — never raise.
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
                return result.stdout.strip()
            _log.debug(
                "AnalyzerManager: git rev-parse HEAD failed (rc=%d): %s",
                result.returncode,
                result.stderr.strip(),
            )
            return None
        except Exception:
            _log.debug("AnalyzerManager: could not capture HEAD commit", exc_info=True)
            return None

    async def _capture_head_commit(self) -> None:
        """Capture git HEAD commit into ``_indexed_commit``.

        Delegates to :meth:`_read_head_commit`; on any failure
        ``_indexed_commit`` becomes None — never raises.
        """
        self._indexed_commit = await self._read_head_commit()

    def _make_progress_handler(self, gen: int) -> ProgressHandler:
        """Build the ``$/progress`` handler for the run spawned under *gen*.

        ``_run`` calls this and passes the result to ``PatchedRustAnalyzer``
        as ``progress_handler``; ``_get_initialize_params`` registers it with
        multilspy, which awaits it for every ``$/progress`` notification.

        Every notification of the current generation records
        ``progress_message`` (the work-done ``title``, remembered per token
        from its ``begin`` event, joined with the latest ``message``) and
        ``progress_at``; ``begin`` and ``end`` events are logged at info as
        ``rust-analyzer progress: <title> <message>``.  A superseded run
        (``gen != self._generation``) records nothing.

        **Commit re-capture after a watcher re-index.**  rust-analyzer
        watches the tree itself (SW-03), so a branch switch re-indexes without
        a restart and ``_run``'s one commit capture would go stale.  On the
        ``end`` event of a re-index token (:data:`REINDEX_PROGRESS_TOKENS`;
        any other token, flycheck's included, is ignored), while ``state ==
        "ready"`` and *gen* is current, the handler takes a read ticket,
        awaits :meth:`_read_head_commit`, re-checks the generation after the
        await (a run superseded during the git call must not overwrite the
        value ``restart()`` cleared) and checks that its ticket is still the
        latest (multilspy runs each notification as its own task, so two
        re-captures can overlap and the older read must not land last), and
        only then writes ``_indexed_commit``, logging at info when the commit
        changed.

        Limit: rust-analyzer emits progress only when a change touches a file
        it loads (cache priming is on in multilspy's ``initialize_params.json``
        line 574), so a branch switch that changes no Rust or Cargo file
        leaves ``indexed_commit`` at the old commit and ``stale`` at ``true``
        until ``refresh``.
        """
        titles: dict[Any, str] = {}

        async def _on_progress(params: Any) -> None:
            if gen != self._generation or not isinstance(params, dict):
                return
            value = params.get("value")
            if not isinstance(value, dict):
                return
            token = params.get("token")
            kind = value.get("kind")
            if kind == "begin":
                titles[token] = str(value.get("title") or "")
            title = titles.get(token, "")
            message = value.get("message")
            text = " ".join(part for part in (title, str(message or "")) if part) or None
            if kind == "end":
                titles.pop(token, None)
            self._progress_message = text
            self._progress_at = _utc_now_iso()
            if kind in ("begin", "end"):
                _log.info("rust-analyzer progress: %s %s", title, message or "")
            if kind != "end" or token not in REINDEX_PROGRESS_TOKENS:
                return
            if self.state != STATE_READY:
                return
            self._head_read_seq += 1
            ticket = self._head_read_seq
            new_commit = await self._read_head_commit()
            if gen != self._generation or ticket != self._head_read_seq:
                return
            old_commit = self._indexed_commit
            self._indexed_commit = new_commit
            if new_commit != old_commit:
                _log.info(
                    "indexed_commit re-captured after re-index: %s -> %s",
                    old_commit,
                    new_commit,
                )

        return _on_progress

    def _make_status_handler(self, gen: int) -> StatusHandler:
        """Build the health callback for the run spawned under *gen* (SW-07).

        ``_run`` passes the result to ``PatchedRustAnalyzer`` as
        ``status_handler``; its ``_on_server_status`` awaits it with the
        ``health`` and ``message`` of every ``experimental/serverStatus``
        notification.  The callback records both as ``analyzer_health`` and
        ``analyzer_health_message`` and logs a change of health at warning as
        ``rust-analyzer health <value>: <message>`` (the first report of a run
        is logged only when it is not ``"ok"``).  A superseded run
        (``gen != self._generation``) records nothing.
        """

        async def _on_status(health: Any, message: Any) -> None:
            if gen != self._generation:
                return
            new_health = str(health) if health is not None else None
            new_message = str(message) if message is not None else None
            previous = self.analyzer_health
            self.analyzer_health = new_health
            self.analyzer_health_message = new_message
            if new_health != previous and not (previous is None and new_health == "ok"):
                _log.warning("rust-analyzer health %s: %s", new_health, new_message or "")

        return _on_status

    def _make_stderr_reader(
        self,
        lsp: PatchedRustAnalyzer,
        lines: collections.deque[str],
        started: asyncio.Event,
        done: asyncio.Event,
    ) -> Callable[[], Coroutine[Any, Any, None]]:
        """Build the replacement for multilspy's ``run_forever_stderr``.

        multilspy's reader (``lsp_protocol_handler/server.py`` lines 391 to
        400) passes each stderr line to the handler's logger, which
        ``RustAnalyzer`` leaves unset, so the lines were dropped.  ``_run``
        binds this reader on ``lsp.server`` as ``run_forever_stderr`` before
        ``start_server()``; ``LanguageServerHandler.start()`` spawns it as a
        task right after it assigns ``process`` (line 230).  It keeps each
        line in *lines* (the run's ``deque``, last ``STDERR_TAIL_LINES``
        lines), logs it at debug as ``rust-analyzer stderr: <line>`` (at info,
        ``RA_LOG=info`` grew the log to 271 MB and stalled indexing).  A line
        past the stream limit makes ``readline()`` discard the buffered data and
        raise ``ValueError``; the reader records a marker line and keeps
        draining.  It sets
        *started* on entry and *done* when it stops (EOF, or the process
        handle cleared by ``stop()``).
        """
        server = lsp.server

        async def run_forever_stderr() -> None:
            started.set()
            try:
                while (
                    server.process is not None
                    and server.process.stderr is not None
                    and not server.process.stderr.at_eof()
                ):
                    try:
                        raw = await server.process.stderr.readline()
                    except ValueError:
                        # A line past the stream limit (64 KiB): readline()
                        # has already discarded the buffered data and raises.
                        # Note it and keep draining so the pipe never fills.
                        raw = b"<over-long stderr line discarded>"
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    lines.append(line)
                    _log.debug("rust-analyzer stderr: %s", line)
            except (BrokenPipeError, ConnectionResetError, StopLoopException):
                pass
            finally:
                done.set()

        return run_forever_stderr

    @staticmethod
    async def _await_process_exit(lsp: PatchedRustAnalyzer) -> int:
        """Return the exit code of *lsp*'s rust-analyzer process once it exits.

        ``lsp.server.process`` is None until multilspy's
        ``LanguageServerHandler.start()`` assigns the ``asyncio`` subprocess
        handle (``lsp_protocol_handler/server.py`` line 217, inside
        ``start_server()``'s entry), so this polls for the handle every
        ``PROCESS_POLL_SECONDS``.  Then it awaits ``process.wait()`` and, every
        ``EXIT_POLL_SECONDS``, reads ``process.returncode`` directly: on
        CPython 3.12 ``wait()`` resolves only after every pipe closes, so a
        proc-macro-srv child still holding the inherited stderr pipe would
        hide the death.  Runs until cancelled if no process is ever assigned.
        """
        while (process := lsp.server.process) is None:
            await asyncio.sleep(PROCESS_POLL_SECONDS)
        wait_task = asyncio.ensure_future(process.wait())
        try:
            while not wait_task.done():
                if process.returncode is not None:
                    return process.returncode
                await asyncio.wait({wait_task}, timeout=EXIT_POLL_SECONDS)
            return wait_task.result()
        finally:
            if not wait_task.done():
                wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await wait_task

    async def _record_process_exit(
        self, gen: int, code: int, stderr_started: asyncio.Event, stderr_done: asyncio.Event
    ) -> None:
        """Publish a rust-analyzer death for the run spawned under *gen* (SW-06).

        Waits up to ``STDERR_DRAIN_SECONDS`` for the stderr reader to reach EOF
        (only if it started), so the message carries the dying process's last
        lines.  Then, if *gen* is still current and no shutdown was requested,
        sets ``state = "error"``, ``analyzer_exit_code`` and ``_error`` (``rust-analyzer
        exited with code <n>; last stderr: <up to 20 lines>``), logs at error,
        and sets ``_shutdown_event`` so a request already in flight on the dead
        process ends (``_race_teardown``) instead of awaiting a reply forever.
        """
        if stderr_started.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stderr_done.wait(), STDERR_DRAIN_SECONDS)
        if gen != self._generation or self._shutdown_event.is_set():
            return
        tail = list(self._stderr_lines)[-STDERR_MESSAGE_LINES:]
        stderr_text = "\n".join(tail) if tail else "(none)"
        self.state = STATE_ERROR
        self.analyzer_exit_code = code
        if code == SHELL_COMMAND_NOT_FOUND:
            self._error = (
                f"rust-analyzer binary not found (shell exit code {code}); "
                f"last stderr: {stderr_text}"
            )
        else:
            self._error = f"rust-analyzer exited with code {code}; last stderr: {stderr_text}"
        _log.error("AnalyzerManager: %s", self._error)
        self._shutdown_event.set()

    async def _capture_rust_analyzer_version(self) -> None:
        """Capture ``<rust_analyzer_bin> --version`` into ``_rust_analyzer_version``.

        Run once, from ``start()``, before the background indexing task is
        spawned.  Output format (verified,
        docs/reference/version-introspection-sources.md): ``rust-analyzer
        <semver> (<sha> <date>)`` — the full trimmed stdout is stored
        verbatim, no parsing.  Mirrors ``_capture_head_commit``'s
        thread-offload pattern (``subprocess.run`` is synchronous; running it
        inline would block the event loop).  A short timeout bounds how long
        a missing/hanging binary can delay startup.  On any failure (missing
        binary, non-zero exit, timeout) leaves ``_rust_analyzer_version`` as
        ``None`` and logs at debug — never raises.
        """
        try:

            def _run_version() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [self._rust_analyzer_bin, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

            result = await asyncio.to_thread(_run_version)
            if result.returncode == 0:
                self._rust_analyzer_version = result.stdout.strip()
            else:
                _log.debug(
                    "AnalyzerManager: rust-analyzer --version failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
                self._rust_analyzer_version = None
        except Exception:
            _log.debug("AnalyzerManager: could not capture rust-analyzer version", exc_info=True)
            self._rust_analyzer_version = None

    async def _run(self, gen: int) -> None:
        """Background task: enter start_server(), flip state, wait for shutdown.

        Args:
            gen: The generation this run was spawned under (``self._generation``
                at the time ``start()`` created this task).  ``_drain_task()``
                bumps ``self._generation`` before draining, so if a restart()
                races ahead of this run reaching quiescence, ``gen`` no longer
                matches ``self._generation`` by the time we get there.  A
                superseded run writes nothing (no ``state``/``_lsp``/
                ``_ready_event``) and exits the ``start_server()`` context
                immediately — which drives multilspy's own clean
                shutdown()/stop() sequence for that (doomed) run.

        Process death (SW-06): the ``start_server()`` entry runs as a task
        raced against :meth:`_await_process_exit` with
        ``asyncio.wait(..., return_when=FIRST_COMPLETED)``, and after
        readiness the run races ``_shutdown_event`` against the same process
        wait.  When rust-analyzer exits and no shutdown was requested, before
        or after readiness, :meth:`_record_process_exit` sets ``state =
        "error"`` and ``analyzer_exit_code`` and the run falls through to the
        teardown below.  A start failure that raises out of the entry
        before ``lsp.server.process`` is set stays on the ``except
        Exception`` "failed to start" path.  A missing binary is not such a
        failure: multilspy launches through ``/bin/sh -c``, so it is the
        shell exiting 127, recorded on the death path as "rust-analyzer
        binary not found".

        Stderr: ``lsp.server.run_forever_stderr`` is rebound to
        :meth:`_make_stderr_reader`'s reader before the entry, so the run's
        stderr lands in ``_stderr_lines`` and the log.

        Teardown: on any exit path, ``finally`` clears ``_lsp`` (identity-
        guarded — only if it still points at *this* run's ``lsp``) and then
        unconditionally, idempotently force-stops the subprocess via
        ``lsp.server.stop()``.  This is required because multilspy 0.0.15's
        ``start_server()`` has no try/finally around its ``yield``: a
        cancellation (e.g. a drain timeout), a pre-yield failure or a dead
        process would otherwise skip multilspy's own teardown and leak the
        rust-analyzer process.  ``server.stop()`` is a no-op on an
        already-clean exit (multilspy already called it; the process handle
        is already None).
        """
        self._indexing_started_at = _utc_now_iso()
        self._progress_message = None
        self._progress_at = None
        stderr_lines: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL_LINES)
        self._stderr_lines = stderr_lines
        _log.info("AnalyzerManager: analyzer start (gen %d)", gen)
        # Capture the commit being indexed before starting the server.
        await self._capture_head_commit()
        if gen != self._generation:
            _log.info(
                "AnalyzerManager: superseded run (gen %d != current %d) after "
                "commit capture; tearing down without spawning",
                gen,
                self._generation,
            )
            return

        # Bound to None first so the finally (and the gen-guarded error write
        # below) are safe even if construction itself raises — see FINDING 2:
        # PatchedRustAnalyzer(...) is INSIDE the try so a constructor failure
        # (e.g. a bad rust_analyzer_bin path) also hits the gen-guarded
        # ``except Exception`` and sets ``state = STATE_ERROR`` instead of
        # leaving the manager stuck in ``"indexing"`` forever.
        lsp: PatchedRustAnalyzer | None = None
        exit_watch: asyncio.Task[int] | None = None
        try:
            config = MultilspyConfig(code_language=Language.RUST)
            logger = MultilspyLogger()
            lsp = PatchedRustAnalyzer(
                config=config,
                logger=logger,
                repository_root_path=self._repository_root,
                rust_analyzer_bin=self._rust_analyzer_bin,
                progress_handler=self._make_progress_handler(gen),
                status_handler=self._make_status_handler(gen),
            )
            stderr_started = asyncio.Event()
            stderr_done = asyncio.Event()
            # Instance attribute shadows the method; start() looks it up on
            # the instance (``self.run_forever_stderr()``, server.py line 230).
            cast(Any, lsp.server).run_forever_stderr = self._make_stderr_reader(
                lsp, stderr_lines, stderr_started, stderr_done
            )
            _log.info("AnalyzerManager: workspace load begun (%s)", self._repository_root)
            server_cm = lsp.start_server()
            exit_watch = asyncio.create_task(self._await_process_exit(lsp))
            enter_task = asyncio.ensure_future(server_cm.__aenter__())
            try:
                await asyncio.wait({enter_task, exit_watch}, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                enter_task.cancel()
                with contextlib.suppress(BaseException):
                    await enter_task
                raise
            if not enter_task.done():
                # rust-analyzer died before quiescence: the entry would wait
                # on server_ready forever.
                enter_task.cancel()
                with contextlib.suppress(BaseException):
                    await enter_task
                await self._record_process_exit(
                    gen, exit_watch.result(), stderr_started, stderr_done
                )
                return
            if enter_task.exception() is not None and exit_watch.done():
                # The entry failed because the process died under it (e.g. a
                # broken pipe on initialize): report the death, not the
                # symptom.
                await self._record_process_exit(
                    gen, exit_watch.result(), stderr_started, stderr_done
                )
                return
            enter_task.result()  # re-raise an entry failure -> except Exception

            # We are now inside the context: indexing is complete (quiescent).
            # The block below mirrors ``async with``'s exit protocol (it has
            # no ``return``: a ``try`` body that returns skips ``else``).
            try:
                if gen != self._generation:
                    _log.info(
                        "AnalyzerManager: superseded run (gen %d != current %d); tearing down",
                        gen,
                        self._generation,
                    )
                else:
                    await self._hold_ready(gen, lsp, exit_watch, stderr_started, stderr_done)
            except BaseException as body_exc:
                if not await server_cm.__aexit__(type(body_exc), body_exc, body_exc.__traceback__):
                    raise
            else:
                await server_cm.__aexit__(None, None, None)
        except _AnalyzerProcessExited:
            pass
        except asyncio.CancelledError:
            _log.info("AnalyzerManager: background task cancelled")
            raise
        except Exception as exc:
            _log.exception("AnalyzerManager: unexpected error in background task")
            # Gen-guarded, synchronous (no await between the check and the
            # writes) so a superseded run can never clobber a newer run's
            # state with a stale error — mirrors the gen-check above.
            if gen == self._generation:
                self.state = STATE_ERROR
                self._error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if exit_watch is not None:
                exit_watch.cancel()
                with contextlib.suppress(BaseException):
                    await exit_watch
            # If construction failed, lsp is still None — there is no LSP object
            # to clear or subprocess to stop, so skip the whole teardown.
            if lsp is not None:
                # Clear the reference on any exit path so the object is never
                # reachable after the start_server() context has exited.
                # Identity-guarded: a superseded run must not clear a newer
                # run's already-published _lsp.
                if self._lsp is lsp:
                    self._lsp = None
                # DS-04: multilspy's start_server() has no try/finally around
                # its yield; on cancellation mid-__aenter__ (or a pre-yield
                # failure, or a dead process) its shutdown()/stop() never run
                # and the rust-analyzer process leaks. server.stop() is
                # multilspy's own idempotent teardown (a no-op after a normal
                # exit — the process handle is already None). Covers every
                # cancel window where a process handle exists; the one
                # residual gap is a cancel landing *inside* multilspy's
                # create_subprocess_shell before it assigns self.process, which
                # is not reachable from here (no handle to terminate yet).
                with contextlib.suppress(Exception):
                    await lsp.server.stop()

    async def _hold_ready(
        self,
        gen: int,
        lsp: PatchedRustAnalyzer,
        exit_watch: asyncio.Task[int],
        stderr_started: asyncio.Event,
        stderr_done: asyncio.Event,
    ) -> None:
        """Publish readiness for *gen*'s run and hold it until shutdown or process exit.

        Called by ``_run`` inside the ``start_server()`` context once the
        generation check passed.  Races ``_shutdown_event`` against
        *exit_watch* with ``FIRST_COMPLETED``.  When the process exited and no
        shutdown was requested, records the death and raises
        ``_AnalyzerProcessExited`` so ``_run`` leaves the context without
        multilspy's shutdown request to a dead process.
        """
        # The three writes below are one synchronous block with no await
        # between them and the caller's gen-check — inserting an await here
        # would reopen the race that check exists to close.
        self._lsp = lsp
        self.state = STATE_READY
        self._ready_event.set()
        _log.info("AnalyzerManager: rust-analyzer ready (state=ready)")
        shutdown_wait = asyncio.create_task(self._shutdown_event.wait())
        try:
            await asyncio.wait({shutdown_wait, exit_watch}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            shutdown_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await shutdown_wait
        if exit_watch.done() and not self._shutdown_event.is_set():
            await self._record_process_exit(gen, exit_watch.result(), stderr_started, stderr_done)
            raise _AnalyzerProcessExited

    async def _race_teardown(self, coro: Coroutine[Any, Any, _T], op: str) -> _T:
        """Await *coro*, aborting with AnalyzerTornDownError if this run is torn down.

        INVARIANT: reached synchronously (no await) from the delegate's guard, so the
        event read here pairs with the _lsp the caller snapshotted.  multilspy's stop()
        never fails pending _response_handlers (verified, 0.0.15), so without this race
        an in-flight request awaits request.cv forever across a drain (issue #87).
        """
        event = self._shutdown_event
        if event.is_set():
            coro.close()
            raise AnalyzerTornDownError(f"analyzer torn down before {op} request was issued")
        request_task = asyncio.ensure_future(coro)
        teardown_task = asyncio.create_task(event.wait())
        try:
            done, _ = await asyncio.wait(
                {request_task, teardown_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            request_task.cancel()
            teardown_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await request_task
            with contextlib.suppress(asyncio.CancelledError):
                await teardown_task
            raise
        if request_task in done:
            teardown_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await teardown_task
            # An external cancellation (client disconnect) delivered while we
            # were suspended in the reap above is suppressed along with the
            # child's own CancelledError.  Any earlier cancel would have been
            # raised at the asyncio.wait suspension (and re-raised by the
            # except branch above), so a nonzero cancelling() count here can
            # only mean a cancel landed in the reap window — honour it instead
            # of returning a result to a caller that already cancelled us.
            cur = asyncio.current_task()
            if cur is not None and cur.cancelling():
                # request_task is done; retrieve any stored exception so GC
                # never warns about it — we are abandoning the result either
                # way (mirrors _drain_task's drain idiom).
                if not request_task.cancelled():
                    request_task.exception()
                raise asyncio.CancelledError
            return request_task.result()
        request_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await request_task
        # Same reap-window guard as the fast path: an external cancel landing
        # while the request was acknowledging its own cancellation must
        # propagate as CancelledError, not be converted to a teardown error.
        cur = asyncio.current_task()
        if cur is not None and cur.cancelling():
            raise asyncio.CancelledError
        raise AnalyzerTornDownError(f"analyzer torn down during {op} request")

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
            AnalyzerNotReadyError: If the manager is not in the ready state (defensive guard).
            AnalyzerTornDownError: If the run is torn down (restart/shutdown) while
                this request is in flight.
        """
        lsp = self._lsp
        if lsp is None or self.state != STATE_READY:
            raise AnalyzerNotReadyError(
                "request_workspace_symbol called before analyzer is ready — "
                "call require_ready() first"
            )
        return await self._race_teardown(lsp.request_workspace_symbol(query), "workspace_symbol")

    async def request_document_symbols(
        self, relative_file_path: str
    ) -> list[UnifiedSymbolInformation]:
        """Delegate document-symbols query to the live LSP instance.

        Returns the flat list of symbols (element [0] of the tuple returned by
        multilspy); the tree representation (element [1]) is discarded.

        DS-20: multilspy 0.0.15 asserts ``isinstance(response, list)`` inside
        ``request_document_symbols`` and RAISES ``AssertionError`` on a
        JSON-RPC ``null`` response (e.g. an empty/unrecognised file) instead of
        returning ``None`` — so, unlike the docstring below's ``if result is
        None`` branch, that branch is never actually reached for the null
        case; it is retained only as a harmless defensive fallback.  We mirror
        ``request_definition``/``request_references``: catch the
        ``AssertionError`` and use ``_is_null_response_assertion`` to tell a
        legitimate null response (→ ``[]``, a file with no symbols is a valid
        answer) from a malformed non-null payload (→ re-raise, a genuine
        protocol failure that must surface as ``error``).

        Raises:
            AnalyzerNotReadyError: If the manager is not in the ready state.
            AnalyzerTornDownError: If the run is torn down (restart/shutdown) while
                this request is in flight.
        """
        lsp = self._lsp
        if lsp is None or self.state != STATE_READY:
            raise AnalyzerNotReadyError(
                "request_document_symbols called before analyzer is ready — "
                "call require_ready() first"
            )
        try:
            result = await self._race_teardown(
                lsp.request_document_symbols(relative_file_path), "document_symbols"
            )
        except AssertionError as exc:
            if _is_null_response_assertion(exc):
                return []
            raise
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
            AnalyzerNotReadyError: If the manager is not in the ready state.
            AnalyzerTornDownError: If the run is torn down (restart/shutdown) while
                this request is in flight.
        """
        lsp = self._lsp
        if lsp is None or self.state != STATE_READY:
            raise AnalyzerNotReadyError(
                "request_definition called before analyzer is ready — call require_ready() first"
            )
        try:
            result = await self._race_teardown(
                lsp.request_definition(relative_file_path, line, column), "definition"
            )
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
            AnalyzerNotReadyError: If the manager is not in the ready state.
            AnalyzerTornDownError: If the run is torn down (restart/shutdown) while
                this request is in flight.
        """
        lsp = self._lsp
        if lsp is None or self.state != STATE_READY:
            raise AnalyzerNotReadyError(
                "request_references called before analyzer is ready — call require_ready() first"
            )
        try:
            result = await self._race_teardown(
                lsp.request_references(relative_file_path, line, column), "references"
            )
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
            AnalyzerNotReadyError: If the manager is not in the ready state.
            AnalyzerTornDownError: If the run is torn down (restart/shutdown) while
                this request is in flight.
        """
        lsp = self._lsp
        if lsp is None or self.state != STATE_READY:
            raise AnalyzerNotReadyError(
                "request_hover called before analyzer is ready — call require_ready() first"
            )
        return await self._race_teardown(
            lsp.request_hover(relative_file_path, line, column), "hover"
        )

    # -----------------------------------------------------------------------
    # Lifecycle helpers
    # -----------------------------------------------------------------------

    async def _drain_task(self) -> None:
        """Bump the generation, signal ``_shutdown_event``, and drain the task.

        Used by both ``shutdown()`` and ``restart()``; callers are expected to
        hold ``_lifecycle_lock`` (both public methods do).  Safe to call when
        ``_task`` is None.

        The generation bump is the FIRST thing this method does — it
        immediately supersedes whatever run is currently outstanding, so even
        if that run reaches quiescence during the drain window below, its
        gen-check in ``_run`` aborts before publishing ``state``/``_lsp``/
        ``_ready_event``.

        If the task does not finish within ``DRAIN_TIMEOUT_SECONDS`` it is
        cancelled; cancellation drives ``_run``'s ``finally`` to force-stop
        the underlying subprocess (see ``_run`` and DS-04), so this method's
        own await may legitimately take longer than ``DRAIN_TIMEOUT_SECONDS``
        while that teardown completes.

        Failure-swallowing contract (DS-07 recovery path): the outgoing run may
        have *failed* (raised an ``Exception``) — either before this drain
        started or during the ``wait_for`` below.  ``wait_for`` RE-RAISES the
        awaited task's own exception, so we must catch that here and
        log-and-continue rather than let it propagate: the whole purpose of
        ``_drain_task`` is to tear the old run down, and its failure must never
        abort the caller (``restart()`` must still go on to spawn the new run).
        Were this exception allowed to escape, ``restart()`` would bail out
        after step 4 — no new task spawned, ``state`` stuck at ``"indexing"``
        forever, and the errored run's evidence erased — which would break the
        documented ``status`` → ``refresh`` recovery from ``state == "error"``
        (it would take a *second* refresh to recover).  ``CancelledError`` is
        deliberately NOT swallowed here (only ``TimeoutError`` from our own
        wait_for is handled, via the cancel path); a cancellation of *this*
        coroutine must still propagate as before.  3.12+ note: a ``wait_for``
        timeout cancels-and-awaits, DELEGATING the cancellation to the drained
        task, so the ``except TimeoutError`` branch below is entered only once
        that task is already done — except for the razor-thin ``_must_cancel``
        race (timeout firing while this coroutine is between suspension points).
        The ``done()``-checked re-raise in that branch guards exactly that race,
        preserving the event/``_lsp`` pairing invariant ``_race_teardown``
        (issue #87) depends on.
        """
        self._generation += 1
        self._shutdown_event.set()
        if self._task is None:
            return
        if not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=DRAIN_TIMEOUT_SECONDS)
            except TimeoutError:
                _log.warning("AnalyzerManager: drain timed out; cancelling task")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    if not self._task.done():
                        raise  # cancellation of the drain itself, not the drained task
                except Exception:
                    pass
            except asyncio.CancelledError:
                # Cancellation of *this* drain coroutine — propagate unchanged.
                raise
            except Exception as exc:
                # The outgoing run itself failed during the drain window.
                # Swallow it (the run is being torn down anyway) so restart()
                # continues to spawn the replacement run — see the docstring.
                _log.info("AnalyzerManager: drained failed outgoing run: %r", exc)
        # Drain any stored exception so GC does not warn about an un-retrieved
        # task exception.  This covers: (a) task already done when we arrived,
        # (b) task finished normally during wait_for above, (c) task that was
        # already cancelled before shutdown was called, (d) task that failed and
        # whose exception we already logged in the except-Exception branch above
        # (retrieving it here is idempotent — Future.exception() can be read
        # repeatedly).
        if self._task.done() and not self._task.cancelled():
            exc = self._task.exception()
            if exc is not None:
                _log.debug(
                    "AnalyzerManager: drained stored task exception: %r",
                    exc,
                )

    async def shutdown(self) -> None:
        """Signal shutdown and wait for the background task to finish.

        Serializes behind ``_lifecycle_lock`` and sets ``_closed = True``
        before draining, so any ``restart()`` call that arrives afterward
        (whether already queued on the lock or issued later) becomes a no-op.

        Signals the shutdown event so ``_run`` exits its ``start_server()``
        context cleanly (triggering the server's own shutdown/stop sequence,
        followed by ``_run``'s own idempotent force-stop).

        Exception draining: if the task has already finished (e.g. it raised
        unexpectedly), its exception is retrieved here so asyncio does not emit
        a "Task exception was never retrieved" warning at GC time.
        """
        async with self._lifecycle_lock:
            self._closed = True
            await self._drain_task()

    async def restart(self) -> None:
        """Re-index the workspace by tearing down and restarting the analyzer.

        Lock-serialized: the entire sequence below runs under
        ``_lifecycle_lock``, so concurrent ``restart()``/``shutdown()`` calls
        queue up behind each other rather than racing.  If ``shutdown()`` has
        already run (``_closed``), this is a no-op — once closed, the manager
        never restarts.

        State-reset-first contract: ``state`` is set to ``STATE_INDEXING`` as
        the first action after acquiring the lock — before signalling or
        awaiting the old task — so that callers never observe a stale
        ``"ready"`` during re-indexing.

        Sequence:
            1. Acquire ``_lifecycle_lock``; if ``_closed``, log and return.
            2. Set ``self.state = STATE_INDEXING`` (must be first after 1),
               and clear ``_error``, ``analyzer_exit_code``,
               ``analyzer_health`` and ``analyzer_health_message`` to None, so
               a new run that fails before its process starts reports "failed
               to start", not the previous run's exit code.
            3. Clear ``_indexed_commit`` to None so ``status`` reports an honest
               "unknown" during the re-index window instead of the *previous*
               cycle's commit (which would let ``stale`` read ``false`` mid-reindex).
            4. Drain the old task cleanly (same logic as ``shutdown()``); this
               bumps ``_generation``, which is the primary defence against a
               stale run publishing readiness after this point.
            5. Re-assert ``state = STATE_INDEXING`` and re-clear
               ``_error``, ``analyzer_exit_code``, ``analyzer_health``,
               ``analyzer_health_message`` and ``_indexed_commit`` —
               defense-in-depth: the old run's
               in-flight ``_capture_head_commit()`` may have landed during the
               drain window in step 4.  This is race-free because the old
               task is now provably done.
            6. Replace ``_shutdown_event`` and ``_ready_event`` with fresh
               instances (the old ones are set/consumed and cannot be reused).
            7. Call ``start()`` to spawn a new background ``_run`` task, which
               recaptures ``indexed_commit`` from git HEAD.

        Safe to call even if ``_task`` is None (e.g. before the first ``start()``).
        """
        async with self._lifecycle_lock:
            if self._closed:
                _log.warning("AnalyzerManager.restart() called after shutdown; ignoring")
                return

            # Step 2: mark not-ready FIRST so callers see indexing immediately.
            self.state = STATE_INDEXING
            # refresh (restart()) is the recovery path from an errored
            # analyzer — clear any stale failure the moment we commit to
            # re-indexing.  The death and health fields go with it (SW-06).
            self._error = None
            self.analyzer_exit_code = None
            self.analyzer_health = None
            self.analyzer_health_message = None

            # Step 3: forget the old indexed commit — during the re-index window
            # the previous value is no longer what's indexed; None → status
            # reports indexed_commit=null, stale=null (honest "unknown") until
            # _run recaptures.
            self._indexed_commit = None

            # Step 4: drain the old task (bumps _generation).
            await self._drain_task()

            # Step 5: re-assert — race-free, the old task is provably done, but
            # its in-flight commit capture (or, pre-generation-check, its own
            # state write) could otherwise have landed during the drain above.
            self.state = STATE_INDEXING
            self._error = None
            self.analyzer_exit_code = None
            self.analyzer_health = None
            self.analyzer_health_message = None
            self._indexed_commit = None

            # Step 6: fresh events — old ones are spent.
            self._shutdown_event = asyncio.Event()
            self._ready_event = asyncio.Event()

            # Step 7: spawn new background task (recaptures indexed_commit internally).
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
