"""Refresh tool — unconditional teardown and wholesale re-index of the analyzer.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

import logging
from typing import Any

from anyio.to_thread import run_sync

from rust_lsp_mcp.core import get_manager, mcp
from rust_lsp_mcp.doc_store import DOC_STATE_ERROR, get_doc_store, init_doc_store
from rust_lsp_mcp.envelope import error, ok
from rust_lsp_mcp.settings import get_settings

_log = logging.getLogger(__name__)


@mcp.tool()
async def refresh() -> dict[str, Any]:
    """Tear down the running analyzer and start a fresh wholesale re-index.

    Behaviour contract:
        - **Unconditional**: every call re-indexes wholesale regardless of the
          current commit or state — there is no hash-gating or diff check.
        - **Non-blocking (analyzer)**: returns after kicking off the analyzer
          re-index in the background.  Poll ``analyzer_status`` (or ``status``)
          until ``state == "ready"`` before issuing navigation queries.
        - **Blocking (doc store)**: the documentation store rebuild is awaited
          synchronously (offloaded to a worker thread) before this call returns.
          The rebuild is fast; ``is_ready`` is ``False`` during the rebuild, so
          concurrent ``search_docs`` calls correctly return ``not_ready`` rather
          than a misleading partial result.
        - **Cache-preserving**: never wipes rust-analyzer's saved on-disk cargo
          cache; only the in-process LSP context is torn down and respawned.
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
          (``get_doc_store()`` is ``None``) or is in the ``"error"`` state
          (permanently failed until recovered), ``refresh`` re-initialises it
          from scratch via ``init_doc_store`` rather than calling ``rebuild()``
          on a store that may not exist or is known-broken.  Otherwise (a
          healthy store already present) it calls ``store.rebuild()`` directly
          — this is the doc-index half of the same recovery path DS-07 uses on
          the analyzer side.
        - If doc-store re-init/rebuild raises an exception, an ``error``
          envelope is returned with a message noting that the analyzer
          re-index was already kicked off.  ``DocStore.rebuild()`` sets
          ``state = "error"`` on failure (not left in a stale ``"ready"``), so
          ``search_docs`` will correctly return ``error`` (not a misleading
          ``ok``) afterward — the invariant holds.

    Returns:
        ``ok`` envelope with ``state="indexing"`` and a polling hint message,
        an ``error`` envelope if the analyzer manager is not running, or an
        ``error`` envelope if the doc-store rebuild/re-init failed (the
        analyzer re-index will still be running in the background in that
        case).
    """
    mgr = get_manager()
    if mgr is None:
        return error("Analyzer is not running; cannot refresh.")

    await mgr.restart()

    # Snapshot the just-kicked-off analyzer state (``"indexing"`` in the normal
    # path) BEFORE the blocking doc-store work below.  The returned state is an
    # acknowledgement that a re-index was started — it must not depend on how
    # long the doc-store rebuild takes.  Reading ``mgr.state`` at return time
    # instead would race the background re-index (a fast warm re-index can reach
    # ``"ready"`` while a cold doc-store embed is still running), yielding a
    # misleading ``"ready"``; callers poll ``status`` for the live state.
    refresh_state = mgr.state

    # Doc-store recovery: an absent or errored store is re-initialised from
    # scratch (mirrors lifespan startup); a healthy store is rebuilt in place.
    # Either way this is synchronous/blocking work offloaded to a worker
    # thread so we don't block the event loop.
    #
    # DS-12 (DEFERRED — not fixed here): concurrent refresh() calls on an
    # absent/errored store both take the re-init path and build two DocStore
    # instances against the same collection.  The worst case is a spurious
    # transient error (never a misleading ``ok``), so this is left unserialized
    # for now; proper doc-store locking is scheduled under DS-12 (the
    # refresh/search race work).
    store = get_doc_store()
    try:
        if store is None or getattr(store, "state", None) == DOC_STATE_ERROR:
            _log.info("refresh: doc store absent or errored — re-initialising")
            await run_sync(lambda: init_doc_store(get_settings()))
        else:
            await run_sync(store.rebuild)
    except Exception as exc:
        # DocStore.rebuild() sets state="error" on failure, so search_docs
        # will correctly surface the failure after this — the invariant holds.
        _log.exception("refresh: doc-store rebuild failed")
        return error(f"Analyzer re-index started, but documentation rebuild failed: {exc}")

    return ok(
        state=refresh_state,
        message="Re-index started; poll status until state is 'ready'.",
    )
