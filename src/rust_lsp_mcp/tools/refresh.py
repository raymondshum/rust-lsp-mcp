"""Refresh tool — restart the analyzer and rebuild the doc index in the background.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

import asyncio
import logging
import weakref
from collections.abc import Callable
from typing import Any

from mcp.types import ToolAnnotations

from rust_lsp_mcp.core import get_manager, mcp
from rust_lsp_mcp.doc_store import (
    DOC_STATE_ERROR,
    DocRebuildCancelled,
    current_shutdown_generation,
    doc_index_progress,
    doc_store_state,
    get_doc_store,
    init_doc_store,
    start_doc_rebuild,
)
from rust_lsp_mcp.envelope import RECOVERY_REFRESH, error, ok
from rust_lsp_mcp.settings import Settings, get_settings

_log = logging.getLogger(__name__)

# finding-3: serializes the doc-store decide-and-start block below across
# concurrent refresh() calls.  Without it, two concurrent refresh() calls on
# an absent/errored store could both decide to re-initialise and start two
# workers that construct two DocStore instances against the same on-disk
# collection (DocStore's _build_lock is per-instance, so separate instances
# don't coordinate).  Under the lock, start_doc_rebuild() checks whether a
# build is already in flight and starts at most one worker thread, so a
# second refresh() never queues another rebuild.  This is an asyncio.Lock
# (not threading.Lock) because it only serializes coroutines on the event
# loop.  Scoped to ONLY the doc-store block, not mgr.restart() — the
# analyzer side is already serialized by its own lifecycle lock.
#
# Per-loop lock (#91): a bare module-level asyncio.Lock binds to the event
# loop that first CONTENDS it (Python's lazy _get_loop(), reached only on the
# contended acquire path), not the one that constructs it.  In production
# this is a non-issue — the MCP server runs a single persistent event loop
# for its entire lifetime, so every refresh() contends the same loop, and
# there is effectively still just one lock.  But it's a footgun for tests:
# each ``asyncio.run()`` spins up and tears down its own loop, so a lock that
# got bound to one test's loop would raise "bound to a different event loop"
# the moment a later test's loop contends it — forcing those tests to patch
# in a fresh ``asyncio.Lock()`` per run.
#
# Instead of one shared Lock, keep a lock PER event loop, created lazily on
# first use via ``_get_doc_store_refresh_lock()``.  Keyed by the running
# loop in a ``WeakKeyDictionary`` rather than a plain ``dict``: event loops
# are weak-referenceable, and a plain dict would hold a strong ref to every
# loop that ever contended the lock — in production there's only ever one
# loop for the process lifetime so it wouldn't matter, but a test suite that
# creates-and-closes a fresh loop per test (this module's own style) would
# otherwise accumulate one dead, closed loop per test run for the life of
# the process. ``WeakKeyDictionary`` lets each entry disappear as soon as its
# loop is garbage-collected, with no behavioural difference for the single
# long-lived production loop.  Mutation of the WeakKeyDictionary is not
# thread-safe, which is fine because the accessor is only ever invoked from
# coroutines on the server's single event-loop thread (never off-loop).
_doc_store_refresh_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _get_doc_store_refresh_lock() -> asyncio.Lock:
    """Return the doc-store refresh lock for the CURRENTLY RUNNING event loop.

    Create-on-first-use, keyed by ``asyncio.get_running_loop()``.  Within a
    single loop this always returns the same ``asyncio.Lock`` instance (so
    finding-3's serialization guarantee holds); across different loops it
    returns a different instance each time, so contending it never trips
    asyncio's "bound to a different event loop" error (see the module
    docstring above).
    """
    loop = asyncio.get_running_loop()
    lock = _doc_store_refresh_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _doc_store_refresh_locks[loop] = lock
    return lock


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)
)
async def refresh() -> dict[str, Any]:
    """Restart the analyzer process and rebuild the doc index in the background.

    **Blast radius:** this tears down and re-indexes the single global
    analyzer shared by the whole server — every other navigation tool
    (``goto_definition``, ``find_references``, ``hover``, ``find_symbol``,
    ``document_symbols``) returns ``not_ready`` for every caller until
    re-indexing completes, which can take minutes on large projects. Do not
    invoke this mid-task unless the index is actually stale or broken (e.g.
    ``status``/``analyzer_status`` reports ``state == "error"``, or you know
    the workspace changed since the last index).  ``search_docs`` keeps
    answering from the old doc index while the doc rebuild runs.

    Behaviour contract:
        - **Unconditional**: every call restarts the analyzer regardless of
          the current commit or state — there is no hash-gating or diff check.
        - **Non-blocking (analyzer)**: returns after kicking off the analyzer
          re-index in the background.  Poll ``analyzer_status`` (or ``status``)
          until ``state == "ready"`` before issuing navigation queries.  The
          rust-analyzer process is restarted and re-reads the workspace from
          scratch.
        - **Non-blocking (doc index)**: starts the doc index rebuild on a
          background thread and returns once that thread has started, with
          ``doc_rebuild_started: true``.  The rebuild writes a staging
          collection and swaps it in on success, so ``search_docs`` keeps
          answering from the old index meanwhile and ``doc_index_state`` stays
          ``"ready"`` (it reads ``"building"`` only for a first build on an
          empty store).  Unchanged files keep their stored embeddings; a
          changed or renamed file is re-embedded, and every file is
          re-embedded when there is no complete live index for this project
          or the chunker version or the embedding model changed.
          ``status``'s ``doc_index_progress`` reports the rebuild's progress.
        - **No queued rebuilds**: while a doc build or rebuild is already
          running (from startup or an earlier ``refresh``), no second one is
          started; the envelope carries ``doc_rebuild_started: false`` and the
          current ``doc_index_progress``.  The analyzer is still restarted.
        - ``restart()`` sets ``state = "indexing"`` as its very first action
          (before teardown begins), so callers never observe a stale ``"ready"``
          during the re-index window.  Concurrent ``refresh`` calls serialize
          behind ``restart()``'s internal lifecycle lock — a warm-up run that
          gets superseded by a later refresh can never flip ``state`` back to
          ``"ready"`` out from under the newer one.  If the analyzer has
          already been shut down, ``restart()`` is a no-op (state stays
          whatever it was at shutdown) — ``refresh`` still returns ``ok``.
          ``restart()`` is also the recovery path from an errored analyzer
          (``state == "error"``): it clears the error and re-indexes.
        - **Doc-store recovery**: if the doc store was never initialised
          (``get_doc_store()`` is ``None``) or is in the ``"error"`` state, the
          worker re-initialises it from scratch via ``init_doc_store`` rather
          than calling ``rebuild()`` on a store that may not exist or is
          known-broken; otherwise it calls ``store.rebuild()``.
        - **Doc-store failures**: because this returns before the rebuild
          runs, the envelope cannot carry a doc rebuild failure.  The worker
          logs it at error and ``status`` reports it: ``doc_index_error``
          carries the message (with ``doc_index_state`` ``"error"`` when no
          old index is left to serve, else still ``"ready"``), and
          ``search_docs`` returns an ``error`` envelope when the index is
          unavailable.
        - **Serialization (finding-3)**: the decide-and-start block is
          serialized behind a per-event-loop ``asyncio.Lock`` (see
          ``_get_doc_store_refresh_lock``).

    Returns:
        ``ok`` envelope with ``state`` (the analyzer state snapshotted right
        after ``restart()``, normally ``"indexing"``), ``doc_index_state``
        (as it stands at return), ``doc_rebuild_started``,
        ``doc_index_progress`` (the running rebuild's progress or ``null``)
        and a polling hint ``message``; an ``error`` envelope if the analyzer
        manager is not running (default ``recovery: "unknown"``, a rare
        startup-ordering case), or, with ``recovery: "refresh"``, if the doc
        rebuild thread could not be started (the analyzer re-index is
        already running in that case).
    """
    mgr = get_manager()
    if mgr is None:
        return error("Analyzer is not running; cannot refresh.")

    await mgr.restart()

    # Snapshot the just-kicked-off analyzer state (``"indexing"`` in the normal
    # path) right after restart().  The returned state is an acknowledgement
    # that a re-index was started; callers poll ``status`` for the live state.
    refresh_state = mgr.state

    # finding-3: decide and start under the per-loop doc-store refresh lock
    # (#91).  The store, the settings, init_doc_store and the shutdown
    # generation are read here, on the event loop, before the thread starts,
    # and handed to the worker, so the worker acts on what this call observed
    # and a shutdown that runs before it gets going stops it (SW-L14).
    try:
        async with _get_doc_store_refresh_lock():
            work = _doc_rebuild_work(
                get_doc_store(), get_settings(), init_doc_store, current_shutdown_generation()
            )
            started = start_doc_rebuild(work)
    except Exception as exc:
        _log.exception("refresh: could not start the doc-store rebuild")
        return error(
            f"Analyzer re-index started, but the documentation rebuild could not start: {exc}. "
            "The analyzer re-index is already running in the background and will "
            "complete on its own — do NOT call refresh again to fix the analyzer. "
            "Once the underlying cause is fixed, calling refresh again will rebuild "
            "the documentation index.",
            recovery=RECOVERY_REFRESH,
        )

    doc_state, _doc_err = doc_store_state()
    if started:
        message = (
            "Re-index started; poll status until state is 'ready'. The doc index "
            "rebuilds in the background (status.doc_index_progress); search_docs "
            "keeps answering from the old index meanwhile."
        )
    else:
        message = (
            "Re-index started; poll status until state is 'ready'. A doc index "
            "rebuild was already running, so no new one was started "
            "(status.doc_index_progress)."
        )
    return ok(
        state=refresh_state,
        doc_index_state=doc_state,
        doc_rebuild_started=started,
        doc_index_progress=doc_index_progress(),
        message=message,
    )


def _doc_rebuild_work(
    store: Any, settings: Settings, reinit: Callable[..., Any], generation: int
) -> Callable[[], None]:
    """Return the worker-thread body for one doc rebuild.

    An absent or errored *store* is re-initialised through *reinit*
    (``init_doc_store``, mirroring lifespan startup); a healthy one is rebuilt
    in place.  *generation* is the shutdown generation captured on the event
    loop; both paths pass it on, so a shutdown that ran since publishes and
    rebuilds nothing.  Never raises: a cancellation at shutdown logs at info,
    any other failure at error (``status`` then reports it, SW-L22).
    """

    def _work() -> None:
        try:
            if store is None or getattr(store, "state", None) == DOC_STATE_ERROR:
                _log.info("refresh: doc store absent or errored — re-initialising")
                reinit(settings, generation=generation)
            else:
                store.rebuild(generation=generation)
        except DocRebuildCancelled:
            _log.info("refresh: doc-store rebuild cancelled at shutdown")
        except Exception:
            _log.exception("refresh: background doc-store rebuild failed")

    return _work
