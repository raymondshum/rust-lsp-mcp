# devcontainer features + rust-analyzer cache relocation

**Date:** 2026-06-19. **Sources:** devcontainers/features repo (rust feature
`devcontainer-feature.json` v1.5.0); rust-analyzer config book; uv issue tracker /
web search for the uv feature.

## Rust feature (Phase 0.1) — VERIFIED
- ID: **`ghcr.io/devcontainers/features/rust:1`** (current **v1.5.0**).
- **Default `components` already includes `rust-analyzer`** (full default:
  `rust-analyzer,rust-src,rustfmt,clippy`) — so the native linux-arm64 rust-analyzer
  is installed at build time with no extra config. This is exactly what Option B
  needs.
- Sets `containerEnv`: `CARGO_HOME=/usr/local/cargo`, `RUSTUP_HOME=/usr/local/rustup`,
  `PATH=/usr/local/cargo/bin:...`.
- **Native rust-analyzer path for the multilspy override:**
  `/usr/local/cargo/bin/rust-analyzer` (or resolve at runtime via
  `rustup which rust-analyzer`). Feed this into the `RustAnalyzer` subclass'
  `setup_runtime_dependencies()` override (see multilspy-rust-backend-audit.md).
- Useful option: pin `version` (default `latest`) for reproducibility; `profile`
  default `minimal` is fine since `components` adds what we need.

## uv feature (Phase 0.1) — CORRECTION
- **There is NO official Astral uv devcontainer feature** (open request:
  astral-sh/uv #8737). The plan's "maintained dev-container feature" assumption is
  only partly right.
- **DECIDED (2026-06-19): first-party uv image layer.** Add a minimal Dockerfile on
  top of the features-based image and copy the pinned uv binary in:
  ```dockerfile
  COPY --from=ghcr.io/astral-sh/uv:0.x.y /uv /uvx /usr/local/bin/
  ```
  `devcontainer.json` uses `build.dockerfile` + `features` together (the rust feature
  still installs the Rust toolchain + rust-analyzer). First-party, digest-pinnable,
  download-once; the 2-line COPY does not reintroduce a hand-rolled toolchain.
  - **Build caveat:** keep uv's Python consistent with the container interpreter
    (let uv manage Python, or set `UV_PYTHON`) so CI and container agree.
- **Fallbacks (not chosen):** community feature
  `ghcr.io/va-h/devcontainers-features/uv:1` (Dockerfile-free but single-maintainer);
  `postCreateCommand` install (not layer-cached, least reproducible).

## rust-analyzer cache relocation (Phase 0.2) — CORRECTION
- **rust-analyzer has no separate on-disk "index cache."** Its semantic index
  (salsa) is **in-memory**, rebuilt each `start_server`. What persists on disk is:
  (a) the **cargo `target/`** produced by RA's `cargo check`, and (b) the **cargo
  registry/git caches** under `CARGO_HOME`.
- The exact setting to relocate RA's build artifacts:
  **`rust-analyzer.cargo.targetDir`** (default `null`). Set to `true` (use a
  subdirectory of `target/`) or a workspace-relative path. Purpose: keeps RA's
  `cargo check` from locking `Cargo.lock` against normal builds (at the cost of
  duplicated artifacts).
- **Net for the three bind mounts:** the "rust-analyzer cache" mount should back
  **`CARGO_HOME`** (registry) and the **RA target dir** (`rust-analyzer.cargo.targetDir`),
  not a mythical RA index dir. The ripgrep build-output mount backs the regular
  `target/` (or `CARGO_TARGET_DIR`). Persistence across rebuilds comes from cargo +
  the OS-native binary, plus rust-analyzer re-indexing on each start (blocks until
  quiescent — see multilspy-readiness.md).

## To re-verify at build (UNVERIFIED specifics)
- Confirm `rust:1` resolves ≥1.5.0 and `rust-analyzer` still in default components.
- Confirm `rustup which rust-analyzer` returns `/usr/local/cargo/bin/rust-analyzer`
  inside the built container; wire that path to the override via settings/env.
- Pick + pin the uv install method; confirm the va-h feature version if used.
