# pytest — markers (fast vs integration) + selection

**Library:** `pytest` (current; Context7 `/pytest-dev/pytest`). **Date:** 2026-06-19.
**Source:** Context7.

## Question
How to register the two-tier markers (fast / integration) and run each tier
(Phase 0.7)?

## Answer — VERIFIED

Register markers in `pyproject.toml` (avoids "unknown marker" warnings):

```toml
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
markers = [
  "integration: live rust-analyzer + real ripgrep fixture (slow; local QA gate only).",
]
addopts = "-ra"
```
- Fast tier = unmarked (default). Integration tier = `@pytest.mark.integration`.
- Run fast only (CI + everyday): `pytest -m "not integration"`.
- Run integration (local QA gate): `pytest -m integration`.
- `-m` accepts boolean expressions (`"integration or slow"`); selection deselects
  the rest (confirmed in docs output).

## VSCode test panel
- `.vscode/settings.json`: `"python.testing.pytestEnabled": true`,
  `"python.testing.pytestArgs": ["tests"]`. The panel discovers both tiers; the
  `integration` marker groups them so they're not run by accident (run via the
  `-m integration` profile / a `qa` script, never in the default run).

## To re-verify at build (UNVERIFIED specifics)
- Confirm `uv add --dev pytest` resolves current (8.x/9.x) and `[tool.pytest.ini_options]`
  unchanged (stable since pytest 6).
- Confirm exact VSCode test args/grouping once tests exist.
