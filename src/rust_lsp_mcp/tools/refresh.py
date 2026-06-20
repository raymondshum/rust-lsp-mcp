"""Refresh tool — unconditional teardown and wholesale re-index of the analyzer.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

from typing import Any

from anyio.to_thread import run_sync

from rust_lsp_mcp.core import get_manager, mcp
from rust_lsp_mcp.doc_store import get_doc_store
from rust_lsp_mcp.envelope import error, ok


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
          during the re-index window.
        - If the doc store was never initialised (``get_doc_store()`` is
          ``None``), the rebuild is skipped gracefully; the analyzer restart
          still proceeds and ``ok`` is still returned.

    Returns:
        ``ok`` envelope with ``state="indexing"`` and a polling hint message,
        or an ``error`` envelope if the analyzer manager is not running.
    """
    mgr = get_manager()
    if mgr is None:
        return error("Analyzer is not running; cannot refresh.")

    await mgr.restart()

    # Phase 5 seam: trigger a wholesale doc-store rebuild after restart().
    # rebuild() is synchronous/blocking; offload it to a worker thread so we
    # don't block the event loop.  is_ready flips False→True inside rebuild(),
    # so concurrent search_docs calls return not_ready during the rebuild window.
    store = get_doc_store()
    if store is not None:
        await run_sync(store.rebuild)

    return ok(
        state=mgr.state,
        message="Re-index started; poll status until state is 'ready'.",
    )
