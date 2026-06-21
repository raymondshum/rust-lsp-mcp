# Plan: repo-agnostic target + host-launchable Docker image

A phasal plan ([output contract](../conventions/phasal-plan.md)) addressing two
issues surfaced 2026-06-21. Decisions below are **settled** (made in a grill +
verification pass) and frozen for the implementation cycle.

## The two issues

1. **The connection config assumes the client runs inside the container.** The
   README tells an MCP client to run `uv run --directory /workspaces/rust-lsp-mcp
   rust-lsp-mcp` ([README.md:76-85](../../README.md)). For that to resolve, the
   launcher needs `uv`, the `/workspaces/...` path, the `.venv`, `rust-analyzer`,
   and `/workspaces/ripgrep` — all **container-only**. A host-side client (e.g.
   Claude Desktop) fails immediately. There is no self-contained runnable image
   and no `docker exec`/`docker run` launch path in the repo today.
2. **ripgrep is baked in; the project is meant to be repo-agnostic.** The
   navigation code is *mostly* already generic (setting `RLM_RIPGREP_SRC`
   re-points rust-analyzer and doc ingestion), but ripgrep is hardwired in: the
   setting **name** (`ripgrep_src` / `RLM_RIPGREP_SRC`), a **hardcoded** Chroma
   collection name (`_COLLECTION_NAME = "ripgrep_docs"`,
   [doc_store.py:35](../../src/rust_lsp_mcp/doc_store.py)), the **provisioning**
   (devcontainer only ever clones/bind-mounts ripgrep), a ripgrep-specific
   `status` docstring, and a deferred UTF-16 offset gap justified by "ripgrep is
   all-ASCII".

## Settled decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Launch via `docker run -i --rm` + cache volumes.** Client spawns an ephemeral container per session over stdio; target bind-mounted `:ro`; chroma + cargo-target on named volumes. | Honors "host stays clean" + "download once"; no container to babysit. `docker exec` is the documented warm-start upgrade path, not the default. |
| D2 | **Target supplied by bind-mounting a host directory.** `RLM_PROJECT_ROOT` points at a mounted host checkout. | Works for any local Rust project, always current, no clone/copy. Clone-by-URL was rejected as the primary path. |
| D3 | **Rename `ripgrep_src` → `project_root` with a deprecated alias.** `RLM_PROJECT_ROOT` primary; `RLM_RIPGREP_SRC` kept as a deprecated alias that warns. Collection name made configurable (`RLM_DOC_COLLECTION`, default `project_docs`). | De-ripgreps the public surface without breaking existing `.env` files. |
| D4 | **ripgrep stays as the default *sample*, not the only target.** Devcontainer keeps cloning ripgrep for dev/test; the production image bakes no target. | Keeps the test suite green and gives newcomers a working example. |

## Verified inventory (from the 2026-06-21 verification pass)

All load-bearing claims confirmed against Context7 / source before this plan was frozen:

- **U2 — CONFIRMED:** the production image needs the **full minimal Rust toolchain**
  (`rustup` + `rustc` + `cargo` + `rust-std` + `rust-src` + the `rust-analyzer`
  component), not the bare RA binary. rust-analyzer runs `cargo check`, build
  scripts, and proc-macro expansion, and uses `sysroot: "discover"`
  (multilspy `initialize_params.json`; devcontainer installs the same set).
  Equivalent to `rustup toolchain install stable --profile minimal --component
  rust-analyzer rust-src`.
- **U1 — CONFIRMED:** `AliasChoices("RLM_PROJECT_ROOT", "RLM_RIPGREP_SRC")` is the
  mechanism. **Gotcha:** `env_prefix_target` defaults to `'variable'`, so the
  `RLM_` prefix is *not* applied to aliases — both choices must include `RLM_`
  literally. Emit the deprecation warning in a `model_validator(mode="after")`
  that checks `os.environ` membership (new absent + old present → warn).
- **U4 — CONFIRMED:** making the collection name configurable is safe. A name
  change makes `init_doc_store` fall through to `rebuild()` (delete→create→
  populate); the old collection is **orphaned, inert** on the volume. No rename
  API; optional one-time `delete_collection(old)` to reclaim disk.
- **U3 — CONFIRMED clean:** no `print`/`sys.stdout` in `src/`; logging is module
  loggers (default sink = stderr); RA's stdout is multilspy's JSON-RPC pipe (not
  inherited); the git staleness call captures output. *Hardening:* set chromadb
  `anonymized_telemetry=False` (also removes a network call on a single-host
  service).
- **U5 — REFUTED:** rust-analyzer's salsa index is **in-memory, rebuilt every
  process start**. Volumes only make `cargo check` incremental + skip crate
  re-downloads. **Every client session re-indexes** — document the per-session
  warmup honestly; do not claim warm-start. (`docker exec` is the path to a hot
  RA process if the warmup ever becomes painful.)

## Phasal plan (risk-first)

### Phase 1 — Production image + host launch (HIGHEST RISK)

Retires the U2/U3/U5 build-time risk first.

- **Scope:** new production `Dockerfile` (full minimal toolchain per U2; bake
  `uv sync --frozen`; bake **no** target); `.dockerignore`; chroma + cargo-target
  as named volumes; image env defaults (`RLM_PROJECT_ROOT=/project`, cache dirs
  under `/data`); chromadb `anonymized_telemetry=False` hardening; rewrite the
  README "Connect it to an AI assistant" block to the `docker run -i --rm` JSON
  (target `:ro`, chroma + cargo-target volumes). Optional `docker-compose.yml`
  for the `docker exec` upgrade path.
- **Depends on:** none.
- **Parallelizable:** partly. The Dockerfile / compose / .dockerignore are
  independent of the README edit. The telemetry-hardening one-liner lives in
  `doc_store.py` — **serialize with Phase 2** (same file) or land it here and have
  Phase 2 rebase.
- **File ownership:** `Dockerfile`, `.dockerignore`, `docker-compose.yml` (new);
  `README.md` (connection section); `doc_store.py` (telemetry line only — coordinate
  with Phase 2).
- **Definition of done (QA gate):** fast tier (ruff, ty, fast pytest) **plus** an
  empirical gate — build the image, `docker run -i` it with a bind-mounted
  **non-ripgrep** Rust project, drive an MCP `initialize` + one nav tool + one
  `search_docs` over stdio, and confirm **clean JSON-RPC on stdout** and correct
  results. This gate is local-only (heavy; never in CI per the CI constraint).
- **Adversarial intensity:** HIGH. Red-team stdout integrity under `docker run`,
  toolchain completeness (proc-macro/build-script crates), and the cold-start path
  with empty volumes.

### Phase 2 — Repo-agnostic config (MEDIUM RISK)

- **Scope:** `settings.py` — rename `ripgrep_src → project_root`, `AliasChoices`
  with explicit `RLM_` prefixes (U1), `model_validator` deprecation warning, add
  `doc_collection` setting; `doc_store.py` — replace hardcoded `_COLLECTION_NAME`
  with the setting; update the three call sites that read `settings.ripgrep_src`
  by name ([analyzer.py:509](../../src/rust_lsp_mcp/analyzer.py),
  [tools/status.py:53](../../src/rust_lsp_mcp/tools/status.py),
  [doc_store.py:103](../../src/rust_lsp_mcp/doc_store.py)); fix the ripgrep-specific
  `status` docstring (KI-6); update `env.sample` + `docs/guide/configuration.md`.
- **Depends on:** none (disjoint files from Phase 1, modulo the `doc_store.py`
  telemetry line — coordinate).
- **Parallelizable:** yes — settings/doc-store/tools edits are independent once
  the new setting names are fixed.
- **File ownership:** `src/rust_lsp_mcp/settings.py`, `doc_store.py`, `analyzer.py`,
  `tools/status.py`, `env.sample`, `docs/guide/configuration.md`,
  `tests/test_smoke.py` + affected integration fixtures.
- **Definition of done (QA gate):** fast tier, **plus** new tests: (a) back-compat —
  `RLM_RIPGREP_SRC` still maps in and emits a `DeprecationWarning`; (b) the new
  primary name works; (c) a collection-name change drives a clean `rebuild()`;
  (d) integration — point at a **second** Rust project and confirm nav + RAG.
- **Adversarial intensity:** MEDIUM. Focus on the alias/prefix gotcha and the
  collection-rename orphan path.

### Phase 3 — Provisioning + docs generalization (LOW RISK)

- **Scope:** keep the devcontainer cloning ripgrep as the **sample** (D4); reframe
  `clone-ripgrep.sh` as "fetch the example project" (rename to `clone-sample.sh`
  optional); update README Quick-start / Status-&-scope to say repo-agnostic with
  ripgrep as the default sample; update `docs/guide/development.md`; document the
  per-session re-index warmup honestly (U5).
- **Depends on:** Phase 1 + Phase 2 (docs must describe the shipped behavior).
- **Parallelizable:** yes (doc pages are disjoint — one page per writer per the
  [documentation-writing](../conventions/documentation-writing.md) loop).
- **File ownership:** `scripts/clone-ripgrep.sh`, `.devcontainer/devcontainer.json`
  (comments), `README.md` (quick-start/scope), `docs/guide/development.md`.
- **Definition of done (QA gate):** fast tier; link-integrity; indexes current;
  the env-sample honesty test still passes.
- **Adversarial intensity:** LIGHT (contract + link check).

## Dependency graph

```
Phase 1 (image/launch) ─┐
                        ├─▶ Phase 3 (provisioning + docs)
Phase 2 (config) ───────┘
```

Phase 1 and Phase 2 run in parallel (disjoint files except the one `doc_store.py`
telemetry line — land it in whichever ships first; the other rebases). Phase 3
waits on both so its docs describe shipped behavior.

## Runtime UNVERIFIED residue (confirm during the build)

- **R1 (Phase 1):** the image builds and `docker run -i` carries MCP JSON-RPC
  end-to-end cleanly (U3 confirmed *in-process*; the docker-run boundary is the
  empirical gate above).
- **R2 (Phase 1):** rust-analyzer's `sysroot: "discover"` actually resolves inside
  the image (rustup present) and proc-macro/build-script crates compile.
- **R3 (Phase 1, non-blocking):** quantify the warm-cargo-volume startup time vs
  cold (U5 says re-index always runs; measure the delta).
- **R4 (Phase 2, minor):** chromadb `anonymized_telemetry=False` silences any
  init output in the pinned chromadb 1.5.x.

## Related

- Known issues opened by this plan: **KI-5** (UTF-16 offsets for non-ASCII targets),
  **KI-6** (ripgrep-specific `status` docstring) — see
  [known-issues.md](../impl/known-issues.md).
- [verification-pass.md](../conventions/verification-pass.md) — the pass that
  confirmed U1–U5 above.
</content>
</invoke>
