# MCP Python SDK — server construction (stdio, tools)

**Library:** `mcp` (modelcontextprotocol/python-sdk) **v1.12.4** (current on PyPI/Context7).
**Date:** 2026-06-19. **Source:** Context7 (`/modelcontextprotocol/python-sdk`).

## Question
How do we register tools and run a stdio server, and what's the entry-point shape?

## Answer — two API tiers

**Recommended for this prototype: FastMCP (high-level).** Simplest mechanism that
meets the need (matches our working style).

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("rust-lsp-mcp")

@mcp.tool()
def find_symbol(name: str) -> dict:        # returns structured content
    ...

def main() -> None:                         # console-script entry point
    mcp.run()                               # stdio transport by default
```

- `mcp.run(transport="stdio")` is synchronous (wraps `anyio.run`) — ideal for a
  `[project.scripts]` console entry point. `stdio` is the default.

**Low-level API (use only if FastMCP can't express something).** Note the SDK is
mid-migration; there are TWO low-level shapes in the docs:
- **v1 (released, 1.12.4):** decorators on `Server` from
  `mcp.server.lowlevel.server` — `@server.list_tools()`, `@server.call_tool()`;
  run via `from mcp.server.stdio import stdio_server` + `await app.run(...)` inside
  `anyio.run`.
- **v2 (on `main`, not necessarily in 1.12.4):** constructor handlers
  `Server(name, on_list_tools=..., on_call_tool=...)` with `ServerRequestContext`
  and snake_case `input_schema`. **Do not target v2 until it ships** — pin to the
  FastMCP or v1 decorator API for 1.12.4.

## Entry point (pairs with uv reference)
`pyproject.toml`: `[project.scripts]\nrust-lsp-mcp = "rust_lsp_mcp:main"`. Run-by-name
`python -m rust_lsp_mcp` needs a `src/rust_lsp_mcp/__main__.py` calling `main()`.

## To re-verify at build (UNVERIFIED specifics)
- Confirm `uv add mcp` resolves ≥1.12.4 and whether the v2 low-level API has shipped
  (re-read `docs/migration.md`); if so, prefer it only if we drop FastMCP.
- Confirm FastMCP structured-content return typing (dict vs. `types.*Content`) for our
  `{status, ...}` envelope.
- Per AGENTS.md, run the `mcp-builder` skill first at build for server scaffolding.
