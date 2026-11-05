"""Status tool — analyzer state, commit freshness, progress and doc index state.

Registered with the FastMCP app at import time via ``@mcp.tool()``.

This tool is UNGATED (it is the readiness check itself) and never returns
``not_ready``.
"""

import asyncio
import importlib.metadata
import subprocess
from typing import Any

from mcp.types import ToolAnnotations

from rust_lsp_mcp.core import get_manager, get_preflight_warnings, mcp
from rust_lsp_mcp.doc_store import (
    DOC_STATE_READY,
    doc_index_chunk_count,
    doc_index_progress,
    doc_store_state,
    doc_store_status_fields,
    reported_doc_state,
)
from rust_lsp_mcp.envelope import ok
from rust_lsp_mcp.settings import get_settings


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def status() -> dict[str, Any]:
    """Return the full status of the rust-analyzer backend and the doc index.

    Returns an ``ok`` envelope — this tool is always ``ok`` (never
    ``not_ready``/``error``, even when the analyzer or doc index has failed;
    failures are reported as *field values*, not as the envelope status):

    - ``state``          — ``"indexing"`` while warming up, ``"ready"`` when
                           the analyzer is live, ``"error"`` if the background
                           indexing run failed (see ``analyzer_error``).  Gated
                           tools return an ``error`` envelope (not
                           ``not_ready``) while ``state == "error"``; call
                           ``refresh`` to retry.
    - ``analyzer_error``  — diagnostic message when ``state == "error"``, else
                           ``null``.
    - ``analyzer_health`` — rust-analyzer's own health report from its
                           latest ``experimental/serverStatus`` notification:
                           ``"ok"``, ``"warning"`` or ``"error"`` (e.g. a
                           failed workspace load), or ``null`` before the
                           first report, after ``refresh`` and after
                           rust-analyzer exits (``state`` is then
                           ``"error"``).  ``"error"`` can coexist with
                           ``state == "ready"``: the server answers queries,
                           but over an incomplete workspace.
    - ``analyzer_health_message`` — the message of that report, or ``null``.
    - ``watcher_health`` — health of rust-analyzer's file watcher:
                           ``"error"`` once rust-analyzer's stderr reports a
                           watch failure in this run (e.g. the host inotify
                           limit, ``notify error: OS file watch limit
                           reached.``), so edits and branch switches may not
                           reach the index until ``refresh``; ``"ok"`` once
                           ``state == "ready"`` with no such failure;
                           ``null`` with no manager, before the run is
                           ready unless a watch failure was already seen in
                           this run (then ``"error"``), after ``refresh``
                           and after rust-analyzer exits.
    - ``watcher_message`` — the stderr line of the run's first watch
                           failure, or ``null``.
    - ``analyzer_exit_code`` — exit code of a rust-analyzer process that died
                           without a shutdown request (``state`` is then
                           ``"error"``), else ``null``.  Cleared by
                           ``refresh``.
    - ``indexed_commit`` — git HEAD hash captured at analyzer start and
                           re-captured when rust-analyzer reports itself
                           quiescent after a watcher-driven re-index, or when
                           HEAD moved over a tree the index already reflects
                           (see ``stale``); ``null`` if not yet captured or
                           git is unavailable.
    - ``current_commit`` — git HEAD hash at call time (``git -C <repo> rev-parse
                           HEAD``), or ``null`` on any failure (non-git directory,
                           git binary missing, subprocess error).  Never raises.
    - ``stale``          — tri-state:
                           ``null``  if either commit hash is unknown (cannot
                                     determine freshness);
                           ``false`` if ``indexed_commit == current_commit``
                                     (no committed changes since indexing);
                           ``true``  if the commits differ (committed changes
                                     have landed since indexing began).
                           rust-analyzer watches the tree, so after a branch
                           switch that makes it re-index, ``indexed_commit``
                           is re-captured once rust-analyzer reports
                           ``quiescent: true`` after that re-index.  After a
                           commit of saved edits, or a switch it does not
                           re-index for (one that changes no Rust or Cargo
                           file included), ``status`` re-captures
                           ``indexed_commit`` itself while ``stale`` would
                           read ``true``: when rust-analyzer is quiescent
                           with no re-index in flight, and every Rust or
                           Cargo file that differs between the two commits
                           matches HEAD on disk and was last modified more
                           than ``HEAD_SETTLE_SECONDS`` (2 s) ago.  Either
                           way ``stale`` returns to ``false`` without
                           ``refresh``.  ``status`` makes no such
                           re-capture while ``watcher_health == "error"``
                           (an edit on disk may never have reached the
                           index), so ``stale`` then stays ``true`` until
                           ``refresh``.
    - ``indexing_started_at`` — ISO 8601 UTC time the current analyzer
                           indexing run began (reset by every ``refresh``),
                           or ``null`` before the first run / with no manager.
    - ``analyzer_progress`` — latest rust-analyzer progress title and message
                           (e.g. ``"Indexing 120/512 (serde)"``); after that
                           work ends, ``"<title> done"`` (e.g. ``"Indexing
                           done"``), or ``null`` when it had no title.
                           ``null`` before the first progress notification
                           of the run.
    - ``analyzer_progress_at`` — ISO 8601 UTC time of that progress
                           notification, or ``null``.
    - ``doc_index_state`` — ``"building"``/``"ready"``/``"error"`` for the
                           documentation search index (independent of
                           ``state`` above — see ``search_docs``).  Stays
                           ``"ready"`` during a rebuild over an existing
                           index (``search_docs`` keeps answering from the
                           old index); ``"building"`` for the first build
                           on an empty store, and for an errored index
                           while a ``refresh`` rebuild of it is in flight
                           (``doc_index_error`` then reads ``null``).  Read
                           again after the doc fields, so a reply whose
                           chunk-count read found the collection deleted by
                           another process reports ``"error"``.
    - ``doc_index_error`` — diagnostic message when ``doc_index_state ==
                           "error"``; also set with ``doc_index_state ==
                           "ready"`` when the last rebuild failed and the old
                           index is still being served; else ``null``.
    - ``doc_index_progress`` — progress of a running doc index rebuild, or
                           ``null`` when none is running: ``files_total``,
                           ``files_done``, ``started_at`` (ISO 8601 UTC) and
                           ``phase`` (``"starting"`` while it waits for
                           its locks, then ``"scanning"``, ``"embedding"``
                           and ``"swapping"``).
    - ``doc_index_chunk_count`` — number of indexed Markdown chunks
                           (``collection.count()``), or ``null`` when
                           ``doc_index_state != "ready"``. ``0`` is a valid
                           reading (a completed corpus with zero matching
                           Markdown files) and is distinct from ``null``
                           (index not ready yet / no store) — see UR-20.
    - ``doc_indexed_commit`` — git HEAD hash stamped on the doc index when it
                           was built, or ``null`` when ``doc_index_state !=
                           "ready"`` (``"none"`` if git failed at build time).
                           Informational: ``doc_stale`` does not compare it.
    - ``doc_stale``      — ``true`` when a Markdown file the index covers was
                           added, removed or changed (size or mtime) since
                           the build; ``false`` when none was, including
                           after a branch switch or commit that leaves the
                           doc files byte-identical; ``null`` when
                           ``doc_index_state != "ready"``.  No running server
                           rebuilds the doc index on its own: ``doc_stale``
                           stays ``true`` until the next ``refresh`` or
                           server restart.
    - ``doc_unreadable_files`` — number of matched doc files the build that
                           produced the ready index could not read (an
                           ``OSError`` such as a permission error); a
                           non-zero count means that many matched files are
                           absent from the index.  ``0`` when every file was
                           read; ``null`` when ``doc_index_state !=
                           "ready"`` or the index was built before this
                           count existed.
    - ``preflight_warnings`` — list of advisory strings computed once at
                           server startup (empty list = all clear, or the
                           preflight never ran — e.g. most unit tests). Never
                           fatal and never affects ``state``/``doc_index_state``
                           — see UR-21.
    - ``server_version``  — installed ``rust-lsp-mcp`` package version
                           (``importlib.metadata.version``, computed fresh per
                           call), or ``null`` if the distribution metadata is
                           not resolvable (``PackageNotFoundError``).
    - ``multilspy_version`` — installed ``multilspy`` package version, same
                           mechanism and null-degradation as ``server_version``.
    - ``rust_analyzer_version`` — the analyzer binary's ``--version`` output
                           (``rust-analyzer <semver> (<sha> <date>)``),
                           captured **once** at analyzer-manager start and
                           cached for the process lifetime (never re-run per
                           ``status`` call). ``null`` before a manager exists,
                           or if the binary was missing/failed/timed out at
                           capture time.

    .. caution::

        Commit-hash comparison does **not** detect uncommitted working-tree
        edits, so ``stale: false`` means "no *committed* changes since
        indexing," not a freshness guarantee.  For a target project with no
        commits since indexing began, ``stale`` is ``false``.  A newer commit
        reads ``true`` until the index reflects it: at once when the commit
        only records files already saved and settled on disk, else when the
        re-index it starts ends.

    This tool is always callable regardless of analyzer state — it is the
    readiness check itself and therefore never returns ``not_ready``.
    """
    mgr = get_manager()

    # Read all in-process state up front, BEFORE the suspension points below,
    # so the analyzer/doc-store fields form one point-in-time snapshot that a
    # concurrent restart()/refresh() cannot tear across the awaits.
    # (Exception: doc_index_chunk_count is read in a thread hop below, so
    # during an active rebuild it can momentarily disagree with the
    # doc_index_state snapshotted here — a stale count, never corruption.)
    state: str = mgr.state if mgr is not None else "indexing"
    analyzer_error: str | None = mgr.error_message if mgr is not None else None
    indexed_commit: str | None = mgr.indexed_commit if mgr is not None else None
    generation: int = mgr.generation if mgr is not None else 0
    repo_root: str = mgr.repository_root if mgr is not None else get_settings().project_root
    doc_state, doc_err = doc_store_state()
    doc_progress = doc_index_progress()
    preflight_warnings = get_preflight_warnings()
    rust_analyzer_version: str | None = mgr.rust_analyzer_version if mgr is not None else None
    indexing_started_at: str | None = mgr.indexing_started_at if mgr is not None else None
    analyzer_progress: str | None = mgr.progress_message if mgr is not None else None
    analyzer_progress_at: str | None = mgr.progress_at if mgr is not None else None
    analyzer_health: str | None = mgr.analyzer_health if mgr is not None else None
    analyzer_health_message: str | None = mgr.analyzer_health_message if mgr is not None else None
    analyzer_exit_code: int | None = mgr.analyzer_exit_code if mgr is not None else None
    watcher_health: str | None = mgr.watcher_health if mgr is not None else None
    watcher_message: str | None = mgr.watcher_message if mgr is not None else None
    # Package versions are cheap to resolve (importlib.metadata reads
    # installed distribution metadata, no I/O beyond that) — compute fresh
    # per call rather than caching, unlike rust_analyzer_version's subprocess.
    server_version = _package_version("rust-lsp-mcp")
    multilspy_version = _package_version("multilspy")

    # DS-19: the pinned MCP SDK runs non-async tools INLINE on the event loop
    # (no thread offload), so synchronous I/O here would block every other
    # in-flight request on the hottest polling path. Offload both the git
    # fork+exec and the doc-store's collection.count() (a ChromaDB/SQLite
    # query) to worker threads; each helper stays synchronous (it's the
    # worker-thread body — mirrors AnalyzerManager._capture_head_commit's
    # identical pattern).
    current_commit: str | None = await asyncio.to_thread(_git_head, repo_root)
    doc_chunk_count: int | None = await asyncio.to_thread(doc_index_chunk_count)
    # SW-04 / backlog line 63: doc_indexed_commit, doc_stale and
    # doc_unreadable_files come from one snapshot of one ready collection
    # (a metadata read and a stat walk over the doc files, no git call) —
    # same DS-19 worker-thread offload.
    doc_fields = await asyncio.to_thread(doc_store_status_fields)
    doc_indexed_commit, doc_stale, doc_unreadable_files = doc_fields
    # Re-read the doc state after the doc awaits (and before reconcile_head,
    # which stays the last await): the chunk-count read can itself move the
    # store from "ready" to "error" (a collection another process deleted,
    # KI-13), and that reply must say so.
    doc_state_now, doc_err_now = doc_store_state()
    if doc_state == DOC_STATE_READY and doc_state_now != DOC_STATE_READY:
        doc_state, doc_err = doc_state_now, doc_err_now
    # An errored index whose refresh rebuild is in flight (for example a
    # re-init waiting on another process's doc-store lock) reads "building",
    # so a client polling for ready keeps waiting instead of acting on the
    # error the rebuild is about to replace.  The mask is reported_doc_state's,
    # so refresh and search_docs report the same state (backlog line 117).
    doc_state, doc_err = reported_doc_state((doc_state, doc_err))
    # Backlog line 50: the doc fields read after the awaits belong to the
    # doc_state reported; a store that is not ready reports null for all of
    # them, so one reply never pairs "building" or "error" with values.
    if doc_state != DOC_STATE_READY:
        doc_chunk_count = None
        doc_indexed_commit = None
        doc_stale = None
        doc_unreadable_files = None

    stale: bool | None
    if indexed_commit is None or current_commit is None:
        stale = None
    else:
        stale = indexed_commit != current_commit
    if stale and mgr is not None and current_commit is not None:
        # Backlog line 49: HEAD moved over a tree the index already reflects
        # (a commit of saved edits) and no re-index will re-capture it.
        # Only indexed_commit/stale take the result; every other field stays
        # at the snapshot above.
        reconciled = await mgr.reconcile_head(current_commit, generation)
        if reconciled is not None:
            indexed_commit = reconciled
            stale = False

    return ok(
        state=state,
        analyzer_error=analyzer_error,
        analyzer_health=analyzer_health,
        analyzer_health_message=analyzer_health_message,
        analyzer_exit_code=analyzer_exit_code,
        watcher_health=watcher_health,
        watcher_message=watcher_message,
        indexed_commit=indexed_commit,
        current_commit=current_commit,
        stale=stale,
        indexing_started_at=indexing_started_at,
        analyzer_progress=analyzer_progress,
        analyzer_progress_at=analyzer_progress_at,
        doc_index_state=doc_state,
        doc_index_error=doc_err,
        doc_index_progress=doc_progress,
        doc_index_chunk_count=doc_chunk_count,
        doc_indexed_commit=doc_indexed_commit,
        doc_stale=doc_stale,
        doc_unreadable_files=doc_unreadable_files,
        preflight_warnings=preflight_warnings,
        server_version=server_version,
        multilspy_version=multilspy_version,
        rust_analyzer_version=rust_analyzer_version,
    )


def _package_version(name: str) -> str | None:
    """Return the installed version of the *name* distribution, or ``None``.

    Wraps ``importlib.metadata.version``; degrades to ``None`` on
    ``PackageNotFoundError`` (e.g. an editable/unusual install where the
    distribution metadata isn't resolvable). Never raises.
    """
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git_head(repo_root: str) -> str | None:
    """Return ``git -C <repo_root> rev-parse HEAD`` output, or ``None`` on any failure.

    Uses ``subprocess.run`` (synchronous) without ``shell=True``.  Never raises.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception:
        return None
