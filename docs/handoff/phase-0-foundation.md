# Phase 0 — Foundation (durable prompt)

> _**Historical record (Claude Code era).** A durable prompt from the original rust-lsp-mcp runtime build under the Claude Code harness; preserved as-is. The project's harness is now IBM Bob — see [the harness port](../planning/bob-harness-port.md)._

**Goal:** stand up the dev environment so all later work runs inside the container.
**Serial, single-agent. No live analyzer yet.**

## Read first
- [implementation-plan.md](../planning/implementation-plan.md) §§0.1–0.11 (all VERIFIED).
- Reference: [devcontainer-features.md](../reference/devcontainer-features.md),
  [uv-packaging-ci.md](../reference/uv-packaging-ci.md),
  [ruff-config.md](../reference/ruff-config.md),
  [pytest-markers.md](../reference/pytest-markers.md),
  [ty-vscode-setup.md](../reference/ty-vscode-setup.md),
  [mcp-inspector.md](../reference/mcp-inspector.md).

## Two beats with a human seam
**Beat A — authoring (host, no container):** devcontainer (`rust:1` + first-party uv
image layer), bind mounts (ripgrep source, cargo `target`, `CARGO_HOME`/RA targetDir,
chroma store, model cache), pinned ripgrep clone script, `pyproject.toml` (src layout,
`[project.scripts] rust-lsp-mcp`, `__main__.py`), pydantic-settings layer + `env.sample`
+ `init.sh`, ruff/ty + committed `.vscode/`, pytest tiers, setup/teardown scripts, CI
workflow, `.gitignore` for the mounts. Then set state `awaiting-container-build` and
**stop** — the human builds/reopens the container.

**Beat B — bootstrap (inside container):** `uv sync`; run setup script; smoke-check
`uv run rust-lsp-mcp` and `python -m rust_lsp_mcp`; **confirm the runtime-only
`UNVERIFIED`:** `rustup which rust-analyzer` → expected `/usr/local/cargo/bin/rust-analyzer`
(record the actual path for Phase 1's override). Confirm `env.sample`-honesty check
passes.

## Scope / stop boundary
No MCP tools, no analyzer driving, no doc-RAG. Stop after Beat B smoke checks + the
analyzer-path confirmation.

## Definition of done (QA gate)
Fast tests + lint + ty green locally and in CI; both launch paths boot; `uv sync`
reproducible; analyzer binary path confirmed and recorded.

## Adversarial (light — config falsification)
Find: a settings var with no `env.sample` entry; a cache that lands off the bind mount
(refetches on rebuild); CI accidentally running integration tests or needing a `.env`;
`teardown` wiping something `refresh` shouldn't.
