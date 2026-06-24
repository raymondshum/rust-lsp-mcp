# uv — packaging, entry points, run/sync/add, GitHub Actions

**Library:** `uv` (Astral; Context7 `/astral-sh/uv`, `/astral-sh/setup-uv`).
**Date:** 2026-06-19. **Source:** Context7.

## Entry points & src layout (Phase 0.4)
- Console script in `pyproject.toml`:
  ```toml
  [project.scripts]
  rust-lsp-mcp = "rust_lsp_mcp:main"
  ```
- Scaffold a packaged (src-layout) project: `uv init --package <name>` → creates
  `src/<pkg>/` + `pyproject.toml`. Run-by-name (`python -m rust_lsp_mcp`) needs
  `src/rust_lsp_mcp/__main__.py`.
- Active launch: `uv run --directory <project> rust-lsp-mcp`.

## Everyday commands
- `uv add <pkg>` (resolves current, writes lockfile), `uv sync` (materialize env),
  `uv run <cmd>` (run inside env), `uv lock`.

## GitHub Actions (Phase 0.10) — VERIFIED
Current action: **`astral-sh/setup-uv@v8.1.0`** (commit-pinned in Astral's own docs).

```yaml
steps:
  - uses: actions/checkout@v5
  - name: Install uv (+ cache, + Python)
    uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b  # v8.1.0
    with:
      enable-cache: true
      python-version: "3.12"
  - run: uv sync
  - run: uv run ruff check .
  - run: uv run ruff format --check .
  - run: uv run ty check
  - run: uv run --frozen pytest -m "not integration"   # fast tier only in CI
```
- `enable-cache: true` caches the uv cache dir for faster runs.
- `--frozen` asserts the lockfile is up to date (good CI hygiene).
- Keep CI to lint + type + fast tests only (free-tier quota; CLAUDE.md constraint).

## To re-verify at build (UNVERIFIED specifics)
- Confirm `setup-uv` is still v8.x at build (bump the pinned SHA/tag if newer).
- Confirm Python-version pin source (`.python-version` vs. action input).
