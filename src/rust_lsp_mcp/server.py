"""FastMCP server for rust-lsp-mcp — thin wiring layer.

This module is the entry point that wires together the FastMCP application
(defined in ``rust_lsp_mcp.core``) and auto-registers all tool modules (defined
in ``rust_lsp_mcp.tools``).

Structure:
    core.py      — FastMCP app instance, lifespan, readiness gate, shared helpers.
    tools/*.py   — Individual tool modules; each registers itself via ``@mcp.tool()``
                   at import time.  Importing ``rust_lsp_mcp.tools`` auto-discovers
                   and imports every submodule, so no central registry edit is needed
                   when adding a new tool file.

Entry point:
    ``main()`` calls ``mcp.run()``, which is synchronous (wraps anyio.run) and
    uses stdio transport by default.  It is importable from ``rust_lsp_mcp``
    so both launch paths work:
        - ``uv run rust-lsp-mcp``   (console script)
        - ``python -m rust_lsp_mcp``
"""

import rust_lsp_mcp.tools  # noqa: F401 — importing the package registers all tools

# Re-export helpers so existing tests that monkeypatch ``rust_lsp_mcp.server._manager``,
# ``rust_lsp_mcp.server.require_ready``, ``rust_lsp_mcp.server.find_symbol``, etc. continue
# to work without modification.
from rust_lsp_mcp.core import (  # noqa: F401
    _manager,
    get_manager,
    mcp,  # noqa: F401 — re-exported for legacy monkeypatching
    require_ready,
)
from rust_lsp_mcp.tools.diagnostics import analyzer_status, probe  # noqa: F401
from rust_lsp_mcp.tools.find_symbol import find_symbol  # noqa: F401


def main() -> None:
    """Start the MCP server over stdio (synchronous — wraps anyio.run internally).

    This is the console-script entry point (``rust-lsp-mcp``) and is also
    called by ``__main__.py`` for ``python -m rust_lsp_mcp``.
    """
    mcp.run()
