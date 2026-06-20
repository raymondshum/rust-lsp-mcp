"""Refresh tool — unconditional teardown and wholesale re-index of the analyzer.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

from typing import Any

from rust_lsp_mcp.core import get_manager, mcp
from rust_lsp_mcp.envelope import error, ok


@mcp.tool()
async def refresh() -> dict[str, Any]:
    """Tear down the running analyzer and start a fresh wholesale re-index.

    Behaviour contract:
        - **Unconditional**: every call re-indexes wholesale regardless of the
          current commit or state — there is no hash-gating or diff check.
        - **Non-blocking**: returns immediately after kicking off the re-index;
          the new index is built in the background.  Poll ``analyzer_status``
          (or ``status``) until ``state == "ready"`` before issuing navigation
          queries.
        - **Cache-preserving**: never wipes rust-analyzer's saved on-disk cargo
          cache; only the in-process LSP context is torn down and respawned.
        - ``restart()`` sets ``state = "indexing"`` as its very first action
          (before teardown begins), so callers never observe a stale ``"ready"``
          during the re-index window.

    Returns:
        ``ok`` envelope with ``state="indexing"`` and a polling hint message,
        or an ``error`` envelope if the analyzer manager is not running.
    """
    mgr = get_manager()
    if mgr is None:
        return error("Analyzer is not running; cannot refresh.")

    await mgr.restart()

    # Phase 5 seam: when the documentation RAG store is built (Phase 5),
    # trigger a wholesale doc-store rebuild here — after restart() returns
    # and before the ok envelope is returned.  Example:
    #   await doc_store.rebuild()

    return ok(
        state=mgr.state,
        message="Re-index started; poll status until state is 'ready'.",
    )
