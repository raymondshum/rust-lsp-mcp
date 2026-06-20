# docs/reference — Index

Cached Context7/library patterns & API snippets. Each entry stamps library + version + date.
Check here before re-querying Context7.

- [ty-vscode-setup.md](ty-vscode-setup.md) — ty + VSCode editor setup; extension auto-disables Pylance (ty 0.0.18, 2026-06-19)
- [multilspy-readiness.md](multilspy-readiness.md) — how multilspy detects indexing-complete (serverStatus/quiescent; start_server blocks until ready) (multilspy 0.0.15, 2026-06-19)
- [chromadb-default-embedder.md](chromadb-default-embedder.md) — default EF is local ONNX all-MiniLM-L6-v2 (delegating class in 1.5.9); cosine via configuration=; model cache path + bind-mount (chromadb 1.5.9, 2026-06-19)
- [multilspy-rust-backend-audit.md](multilspy-rust-backend-audit.md) — §9 audit: no linux-arm64 entry + stale pin; decision = container + subclass override (returns str path; create() hard-codes RustAnalyzer so instantiate subclass directly) (multilspy 0.0.15, 2026-06-19)
- [mcp-python-sdk-server.md](mcp-python-sdk-server.md) — FastMCP vs low-level Server; stdio default; console entry point; v2 API still on main (mcp 1.12.4, 2026-06-19)
- [pydantic-settings.md](pydantic-settings.md) — BaseSettings + SettingsConfigDict; precedence init>env>.env>secrets matches plan (pydantic-settings, 2026-06-19)
- [uv-packaging-ci.md](uv-packaging-ci.md) — [project.scripts] entry point; uv init/add/sync/run; setup-uv@v8.1.0 CI snippet (uv, 2026-06-19)
- [ruff-config.md](ruff-config.md) — recommended select E/F/UP/B/SIM/I; VSCode format-on-save + source.*.ruff code actions (ruff, 2026-06-19)
- [pytest-markers.md](pytest-markers.md) — [tool.pytest.ini_options] markers; integration marker; -m "not integration" for CI (pytest, 2026-06-19)
- [devcontainer-features.md](devcontainer-features.md) — rust:1 v1.5.0 (rust-analyzer in default components, path /usr/local/cargo/bin); NO official uv feature; RA cache = cargo.targetDir + CARGO_HOME, not an index dir (2026-06-19)
- [mcp-inspector.md](mcp-inspector.md) — npx @modelcontextprotocol/inspector [--cli] -- uv run rust-lsp-mcp; UI :6274; Node ≥22.7.5 (2026-06-19)
