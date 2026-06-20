"""Entry point for the rust-lsp-mcp MCP server.

Beat A stub: boots and exits cleanly so both launch paths smoke-check in Beat B.
No MCP tools, no analyzer, no FastMCP server logic yet (Phase 1+).
"""

import sys


def main() -> None:
    """Minimal entry point — Phase 0 stub.

    Prints a startup banner and exits 0.  Both launch paths must reach this
    function:
      - `uv run rust-lsp-mcp`  (console script via [project.scripts])
      - `python -m rust_lsp_mcp`  (via __main__.py)

    Phase 1 will replace this with a real FastMCP server:
        mcp = FastMCP("rust-lsp-mcp")
        mcp.run()  # stdio transport
    """
    print("rust-lsp-mcp: Phase 0 stub — server not yet implemented.", file=sys.stderr)
    sys.exit(0)
