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
from rust_lsp_mcp.core import mcp

# ``find_symbol`` is re-exported because the Phase 2 integration test reaches it
# via ``rust_lsp_mcp.server.find_symbol``.  New code should import tools from
# ``rust_lsp_mcp.tools.*`` directly; the canonical manager/gate live in
# ``rust_lsp_mcp.core`` (tests monkeypatch ``rust_lsp_mcp.core._manager``).
from rust_lsp_mcp.tools.find_symbol import find_symbol  # noqa: F401


def main() -> None:
    """Start the MCP server over stdio (synchronous — wraps anyio.run internally).

    This is the console-script entry point (``rust-lsp-mcp``) and is also
    called by ``__main__.py`` for ``python -m rust_lsp_mcp``.
    """
    mcp.run()
