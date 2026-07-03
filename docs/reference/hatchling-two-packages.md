# hatchling — two top-level packages + two console scripts in one wheel

**Build backend:** hatchling (via `uv build`) · **Verified:** 2026-07-02
(scratch project: build → install → run both scripts) · For plan
[cli-frontend.md](../planning/cli-frontend.md) (D5, U5).

## What was asked

Can one wheel ship `src/rust_lsp_mcp` + a new import-light `src/rust_lsp_cli`,
each with its own `[project.scripts]` entry?

## Answer

Yes — plain list append, no special syntax:

```toml
[project.scripts]
rust-lsp-mcp = "rust_lsp_mcp:main"
rust-lsp = "rust_lsp_cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/rust_lsp_mcp", "src/rust_lsp_cli"]
```

Both directories land at wheel root; both scripts appear under
`[console_scripts]` in `entry_points.txt`. Proven end-to-end (wheel built,
installed into a fresh venv, both binaries executed).

**No auto-detection gotcha:** hatchling's single-package auto-detect only
applies when the `packages` key is absent (then it errors unless a dir matches
the project name). This project already declares
`packages = ["src/rust_lsp_mcp"]` explicitly (`pyproject.toml:27-28`), so
adding the second entry is a non-breaking one-line change.
