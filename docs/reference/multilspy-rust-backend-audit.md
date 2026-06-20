# multilspy rust backend — pre-implementation audit (§9)

**Library:** `multilspy` **0.0.15** (latest on PyPI). **Date:** 2026-06-19.
**Source:** package source inspection (`pip download multilspy --no-deps --no-binary
:all:`): `language_servers/rust_analyzer/{rust_analyzer.py,runtime_dependencies.json,
initialize_params.json}` and `language_server.py`.

## What works (de-risked)

- **Readiness**: handled — see [multilspy-readiness.md](multilspy-readiness.md)
  (serverStatus/quiescent; `start_server()` blocks until indexed).
- **Navigation primitives present**: `request_workspace_symbol`,
  `request_document_symbols`, `request_definition`, `request_references`,
  `request_hover` — covers all five planned tools + the name→position bridge.
- Paths returned as both absolute and repo-relative; inputs are repo-relative.

## The real risk found: rust-analyzer binary provisioning

`RustAnalyzer.setup_runtime_dependencies()` **downloads** rust-analyzer from a fixed
table and hard-asserts exactly one platform match
(`assert len(runtime_dependencies) == 1`). The table (0.0.15):

| platformId | binary |
|------------|--------|
| `osx-arm64`  | rust-analyzer `2023-10-09`, aarch64-apple-darwin |
| `linux-x64`  | rust-analyzer `2023-10-09`, x86_64-unknown-linux-gnu |
| `win-x64`    | rust-analyzer `2023-10-09`, x86_64-pc-windows-msvc |

Two problems:

1. **No `linux-arm64` entry.** The Phase 0 Linux dev container on an Apple-Silicon
   Mac is linux/arm64 → the filter yields zero matches → assertion fails → RustAnalyzer
   can't start. (Note: native macOS is fine — `osx-arm64` is covered.)
2. **Stale pin.** All three are rust-analyzer `2023-10-09` (~2.5 yrs old at time of
   writing).

## Decision (2026-06-19): container + Option B, nothing on host OS

- Keep the Phase 0 **Linux dev container** (reproducibility, clean host, CI parity,
  observable bind mounts). Nothing installed on the host macOS.
- **Option B — supply rust-analyzer ourselves:** the devcontainer's Rust feature
  already installs rust-analyzer **natively** (linux-arm64) at build time
  (e.g. `rustup component add rust-analyzer`; path via `rustup which rust-analyzer`).
  **Subclass `RustAnalyzer` and override `setup_runtime_dependencies()`** to return
  that existing binary path instead of downloading. multilspy downloads nothing.
- Benefits beyond the arm64 fix: we **control the rust-analyzer version** (kills the
  stale pin) and stop depending on multilspy's platform table.
- Rejected alt (A): force the container to `linux/amd64` → multilspy's emulated x86
  download runs under QEMU, taxing the indexing-heavy hot path, *and* leaves the
  devcontainer's native analyzer unused. Kept only as a fallback.

## Override contract — VERIFIED 2026-06-19 (multilspy 0.0.15 source re-inspection)

- **Signature:** `RustAnalyzer.setup_runtime_dependencies(self, logger, config) -> str`.
  It is called from `RustAnalyzer.__init__` and its return value (a string path to the
  rust-analyzer executable) is passed straight to `ProcessLaunchInfo(cmd=...)`.
  **So the override just returns the native binary path** — no download, no archive
  extraction. Clean, stable contract.
- **Gotcha — `LanguageServer.create()` hard-codes `RustAnalyzer`.** For
  `Language.RUST` it does `return RustAnalyzer(config, logger, repository_root_path)`
  (line ~99), so it will **not** pick up our subclass. We must **instantiate our
  subclass directly** (`PatchedRustAnalyzer(config, logger, repo_root)`) rather than
  going through `create()`. (Or monkeypatch, but direct instantiation is cleaner.)
- **Native path (from the rust devcontainer feature):**
  `/usr/local/cargo/bin/rust-analyzer` (feature `rust:1` v1.5.0 installs it by default
  via the `rust-analyzer` component; `CARGO_HOME=/usr/local/cargo`). Resolve robustly
  with `rustup which rust-analyzer`. See
  [devcontainer-features.md](devcontainer-features.md).

## To verify at build (UNVERIFIED specifics)

- Confirm `setup_runtime_dependencies` signature unchanged on any multilspy bump.
- Confirm `rustup which rust-analyzer` returns the expected path inside the built
  container, and feed it to the override via a settings value / env var.
- Confirm a current rust-analyzer release works against multilspy's pinned
  `initialize_params.json` capabilities (esp. `experimental/serverStatus`).
- **Runtime, build-only:** the `container` label rust-analyzer attaches to
  `workspace_symbol` results (`UnifiedSymbolInformation`) — still UNVERIFIED, can only
  be confirmed against the live analyzer (Phase 2).
