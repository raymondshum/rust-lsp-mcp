# ruff — lint/format config + VSCode format-on-save

**Library:** `ruff` (Astral; Context7 `/astral-sh/docs`). **Date:** 2026-06-19.
**Source:** Context7 for rule set + formatter; Ruff editor docs for VSCode actions.

## pyproject.toml (Phase 0.6) — VERIFIED
Astral's own "recommended, not overly pedantic" rule set:

```toml
[tool.ruff]
src = ["src", "tests"]
line-length = 100            # choose; example uses default 88

[tool.ruff.lint]
select = ["E", "F", "UP", "B", "SIM", "I"]
# E pycodestyle · F Pyflakes · UP pyupgrade · B bugbear · SIM simplify · I isort

[tool.ruff.lint.isort]
known-first-party = ["rust_lsp_mcp"]
```
- `I` enables import sorting (built-in isort) — ruff both lints and fixes order.
- `ruff format` is the formatter (Black-compatible); CI uses `ruff format --check`.

## VSCode format-on-save (committed `.vscode/settings.json`)
Requires the **Ruff extension `charliermarsh.ruff`**. Current code-action names:

```json
{
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll.ruff": "explicit",
      "source.organizeImports.ruff": "explicit"
    }
  }
}
```
- `source.fixAll.ruff` = autofix lint on save; `source.organizeImports.ruff` =
  sort imports on save. `"explicit"` is the current value form (replaced the old
  boolean `true`).

## To re-verify at build (UNVERIFIED specifics)
- Confirm `uv add ruff` resolves current (Context7's `/astral-sh/ruff` snapshot
  lists an older 0.4.x tag; the config keys above are stable across versions).
- Confirm the Ruff VSCode extension id/action names if the extension is bumped.
