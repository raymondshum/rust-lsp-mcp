"""FastMCP server for rust-lsp-mcp — Phase 1: readiness gating.

This module wires together:
    - FastMCP application with lifespan management (starts/stops the analyzer).
    - ``require_ready`` gate — returns ``not_ready`` immediately if the analyzer
      is still indexing.  Never blocks a request; never returns a misleading empty.
    - Minimal tool surface to prove the gate and envelope contract (Phase 1 scope).
      Full navigation tools (``find_symbol``, ``goto_definition``, etc.) are Phase 3.

Tool surface (Phase 1):
    analyzer_status  — Reports the current readiness state (``indexing``|``ready``)
                       wrapped in an ``ok`` envelope.  The full 4-field Phase 4
                       ``status`` tool (with ``indexed_commit``, ``current_commit``,
                       ``stale``) extends this later.
    probe            — A gated no-op that returns ``ok`` only once the analyzer is
                       ready; returns ``not_ready`` while indexing.  Proves the
                       fail-fast gate via an actual tool call.

Entry point:
    ``main()`` calls ``mcp.run()``, which is synchronous (wraps anyio.run) and
    uses stdio transport by default.  It is importable from ``rust_lsp_mcp``
    so both launch paths work:
        - ``uv run rust-lsp-mcp``   (console script)
        - ``python -m rust_lsp_mcp``
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from rust_lsp_mcp.analyzer import STATE_READY, AnalyzerManager, analyzer_lifespan
from rust_lsp_mcp.envelope import not_ready, ok

# ---------------------------------------------------------------------------
# Module-level manager reference — set during lifespan startup, cleared on exit.
# Tools call require_ready() which reads this.
# ---------------------------------------------------------------------------

_manager: AnalyzerManager | None = None


@asynccontextmanager
async def _lifespan(app: FastMCP) -> AsyncIterator[dict[str, Any]]:  # type: ignore[type-arg]
    """Thin wrapper around analyzer_lifespan that also wires the module-level ref."""
    global _manager
    async with analyzer_lifespan(app) as ctx:
        _manager = ctx["manager"]
        try:
            yield ctx
        finally:
            _manager = None


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

mcp: FastMCP[dict[str, Any]] = FastMCP(  # type: ignore[type-arg]
    "rust-lsp-mcp",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Readiness gate
# ---------------------------------------------------------------------------


def require_ready() -> dict[str, Any] | None:
    """Check whether the analyzer is ready; return a ``not_ready`` envelope or None.

    Usage in tools::

        if (guard := require_ready()) is not None:
            return guard
        # ... proceed with analyzer call ...

    Returns:
        ``not_ready`` envelope dict if the analyzer is not yet ready, else ``None``.
    """
    if _manager is None or _manager.state != STATE_READY:
        return not_ready()
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def analyzer_status() -> dict[str, Any]:
    """Return the current readiness state of the rust-analyzer backend.

    Returns an ``ok`` envelope with a ``state`` field:
        - ``"indexing"`` — still warming up; gated tools return ``not_ready``.
        - ``"ready"``    — indexing complete; all tools are available.

    Phase 4 will extend this with ``indexed_commit``, ``current_commit``, ``stale``.
    """
    state = _manager.state if _manager is not None else "indexing"
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the MCP server over stdio (synchronous — wraps anyio.run internally).

    This is the console-script entry point (``rust-lsp-mcp``) and is also
    called by ``__main__.py`` for ``python -m rust_lsp_mcp``.
    """
    mcp.run()
