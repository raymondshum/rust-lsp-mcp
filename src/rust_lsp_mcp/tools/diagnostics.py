"""Diagnostic tools — analyzer_status and probe.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

from typing import Any

from rust_lsp_mcp.core import get_manager, mcp, require_ready
from rust_lsp_mcp.envelope import ok


@mcp.tool()
def analyzer_status() -> dict[str, Any]:
    """Return the current readiness state of the rust-analyzer backend.

    Returns an ``ok`` envelope with a ``state`` field:
        - ``"indexing"`` — still warming up; gated tools return ``not_ready``.
        - ``"ready"``    — indexing complete; all tools are available.

    Phase 4 will extend this with ``indexed_commit``, ``current_commit``, ``stale``.
    """
    manager = get_manager()
    state = manager.state if manager is not None else "indexing"
    return ok(state=state)


@mcp.tool()
def probe() -> dict[str, Any]:
    """Gated no-op probe — proves the fail-fast gate works end-to-end.

    Returns ``not_ready`` while the analyzer is indexing, ``ok`` once ready.
    This tool has no semantic value beyond demonstrating and testing the
    ``require_ready`` invariant; navigation tools (Phase 3) will use the same gate.
    """
    if (guard := require_ready()) is not None:
        return guard
    return ok(message="Analyzer is ready.")
