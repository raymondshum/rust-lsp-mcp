# MCP Inspector — invocation (stdio)

**Tool:** `@modelcontextprotocol/inspector` (npm). **Date:** 2026-06-19.
**Source:** modelcontextprotocol/inspector README + modelcontextprotocol.io docs.

## Question
Exact invocation to exercise our stdio server by hand (Phase 0.8).

## Answer — VERIFIED
- **UI mode:** `npx @modelcontextprotocol/inspector -- uv run rust-lsp-mcp`
  (everything after `--` is the server launch command + args). UI serves at
  `http://localhost:6274`.
- **CLI mode (no UI, scriptable):**
  `npx @modelcontextprotocol/inspector --cli uv run rust-lsp-mcp`.
- Pass env vars with `-e KEY=value` before the `--` separator.
- **Requires Node.js ≥ 22.7.5** (add to devcontainer if not present; node is needed
  only for the inspector, not the server).

## To re-verify at build (UNVERIFIED specifics)
- Confirm current default UI port (6274) and Node floor on the installed inspector
  version at build.
