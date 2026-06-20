"""Entry point for the rust-lsp-mcp MCP server.

Delegates to ``server.main()`` which runs the FastMCP server over stdio.
Both launch paths call this module:
    - ``uv run rust-lsp-mcp``   (console script via [project.scripts])
    - ``python -m rust_lsp_mcp``  (via __main__.py)
"""

from rust_lsp_mcp.server import main

__all__ = ["main"]
