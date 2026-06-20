# Build progress tracker

**Single source of truth for "where are we."** The **orchestrator is the sole writer**;
build/reviewer/QA/adversarial agents report results, the orchestrator records them here.
Read by [continue.md](continue.md) to pick the next phase.

## State vocabulary

`not-started` → `authoring` → `awaiting-container-build` (Phase 0 only) →
`in-progress` → `qa` → `adversarial` → `pr-open` → `done`.
(`blocked` = paused for human, with a one-line reason.)

## Gate-zero (handoff self-review)

`gate-zero: passed (2026-06-19)` — adversarial pass over `docs/handoff/` (incl.
`continue.md`) must be `passed` before any phase starts. Values: `not-run` | `passed` |
`blocked: <reason>`. Orchestrator flips it and records the date in the log below.

## Phase status

| Phase | Prompt | Depends on | Parallelizable? | State |
|-------|--------|-----------|-----------------|-------|
| 0 — Foundation | [phase-0-foundation.md](phase-0-foundation.md) | — | No (shared config; serial) | done |
| 1 — Readiness gating | [phase-1-readiness.md](phase-1-readiness.md) | 0 | No (analyzer-bound, serial) | done |
| 2 — Name→position | [phase-2-resolution.md](phase-2-resolution.md) | 1 | No (analyzer-bound, serial) | done |
| 3+4 — Nav + operational tools | [phase-3-4-tools.md](phase-3-4-tools.md) | 2 | **Yes** — the 5 tools fan out on the fast-test tier (faked analyzer); integration gate serial | in-progress |
| 5 — Doc-RAG | [phase-5-doc-rag.md](phase-5-doc-rag.md) | 0 | **Yes** — off the LSP path; may run parallel to 3+4 | not-started |

## Dependency graph (what the orchestrator may fan out)

```
0 ──> 1 ──> 2 ──> 3+4
└────────────────> 5      (5 needs only Phase 0; can run alongside 3+4)
```

- Cross-phase: strictly the arrows above. Never start a phase whose dependency isn't
  `done`.
- Intra-phase parallelism is allowed **only for analyzer-free tasks** (see
  [roles.md](roles.md)); the live analyzer + integration gate are a single serialized
  resource even when phases overlap (e.g. 3+4 and 5 must not both drive the analyzer at
  once).

## Per-phase log (orchestrator appends)

> One line per state transition: `<date> Phase N → <state> (PR #/notes)`.

- 2026-06-19 Gate-zero → passed (adversarial pass over `docs/handoff/`; 3 must-fixes +
  4 minors applied to `continue.md` and `progress.md`). Build not yet started.
- 2026-06-19 Phase 0 → awaiting-container-build (Beat A authored on `phase0`: devcontainer
  + Dockerfile, 5 bind mounts, pyproject src layout + both launch paths, settings layer +
  env.sample + init.sh, ruff/ty + `.vscode/`, pytest tiers, setup/teardown, CI + env-honesty
  check, `.gitignore`. Uncommitted, pending human review + container build).
- 2026-06-20 Phase 0 Beat B done in-container (reconciliation: Beat A was merged to `main`
  via PR #1 but this tracker was never advanced). Verified: `uv sync` reproducible; both
  launch paths boot (`uv run rust-lsp-mcp` / `python -m rust_lsp_mcp`, stub exits 0); 11
  fast tests pass; **analyzer path confirmed** — `rustup which rust-analyzer` =
  `/usr/local/rustup/toolchains/stable-aarch64-unknown-linux-gnu/bin/rust-analyzer`; on
  PATH = `/usr/local/cargo/bin/rust-analyzer` (v1.96.0); settings default
  `rust_analyzer_bin` matches the PATH location (correct for Phase 1's override).
- 2026-06-20 Phase 0 DoD gate was **RED on merged PR #1** (CI bypassed): ruff I001
  (unsorted imports in `tests/test_env_sample_honesty.py`) + ty `unknown-argument` on
  `Settings(_env_file=None)` (the `# type: ignore[call-arg]` was a mypy code ty ignores).
  Fixed on branch `phase0-gate-fix`: ruff `--fix`/format + `# ty: ignore[unknown-argument]`.
  All gates now green locally (ruff check/format, ty, fast tests). Adversarial light pass:
  config honest — all 5 caches on bind mounts, CI runs only `-m "not integration"` with no
  `.env`. Non-blocking minors: docstrings reference a non-existent `scripts/check-env-sample.py`;
  env-honesty test checks only the forward direction (no orphan-key check).
- 2026-06-20 Phase 0 → **blocked**: gate-fix PR cannot be opened — `gh` is not installed in
  the container (PR #1 was likely merged manually, which is how its red gate slipped
  through). Branch `phase0-gate-fix` is committed and ready (test fixes + this tracker).
  **Human action:** install `gh` (or merge `phase0-gate-fix` to `main` manually), confirm
  CI green, then re-issue continue — Phase 0 → done unblocks Phase 1.
- 2026-06-20 Phase 0 → **done** (blocker cleared by human). `gh` now installed +
  authenticated (commit `8acd639` added the gh CLI feature + disabled container signing);
  `phase0-gate-fix` merged to `main` via **PR #2** (`c6c977c`). DoD gate re-verified green
  on `main`: `ruff check` clean, `ruff format --check` (7 files formatted), `ty check`
  clean, 11 fast tests pass (incl. env-sample honesty). Resumed at the `pr-open` gate per
  continue.md step 3 — PR open+merged, so the gate is satisfied. Phase 1 (readiness gating)
  is now the next eligible phase. Stopping at the phase boundary; re-issue continue to start
  Phase 1.
- 2026-06-20 Phase 1 → **pr-open** (PR #3). Readiness gating built, reviewed, QA'd,
  red-teamed — all gates green. Single serial analyzer-bound build (no fan-out). Shipped:
  `PatchedRustAnalyzer` (overrides `setup_runtime_dependencies` → `settings.rust_analyzer_bin`,
  instantiated directly, not via `create()`); `AnalyzerManager` runs multilspy `start_server()`
  in a background task on FastMCP's lifespan loop, flips own readiness flag `indexing`→`ready`
  only post-quiescent; `{status}` envelope infra (`ok`/`not_ready`/`not_found`/`error`);
  `require_ready` fail-fast gate; minimal tools `analyzer_status` (ungated state report) +
  `probe` (gated, proves not_ready). Gates: ruff/format/ty clean; **29 fast tests**;
  **2 integration tests** cold-start the live analyzer over the ripgrep 14.1.1 fixture and
  prove no gated call returns a misleading empty/`ok` before `ready` (the load-bearing
  invariant). Review verdict `minor` (2 nits fixed: `asyncio.create_task`, `anyio.sleep`).
  Adversarial verdict `no-breaks` — invariant holds; 2 seam notes addressed (accurate refresh
  docstring; drain dead-task exception on shutdown, + regression test). **Seam left for
  Phase 4:** teardown/refresh never resets `state`→`indexing`; a future `restart()` must set
  `state = STATE_INDEXING` as its first action before tearing down the old live context.
  Awaiting human merge → then Phase 2 (+5) unlock. (PR also carries the Phase 0 done-marking
  tracker commit, which couldn't be pushed to `main` directly.)
- 2026-06-20 Phase 1 → **done**. **PR #3 merged** to `main` (merge commit `a71bded`) after
  CI ran green (lint + type + fast tests, ~15s); `origin/main` now carries the Phase 1 code
  + the Phase 0 done-marking. Local `main` synced. Readiness gating is live on `main`.
- 2026-06-20 Phase 2 → **in-progress** (branch `phase2-resolution`). Name→position
  resolution (`find_symbol`), the sole name→symbol bridge. Build contract confirmed against
  installed multilspy 0.0.15: `request_workspace_symbol(query) -> list[UnifiedSymbolInformation]`
  (`name`, `kind` SymbolKind int, `location.relativePath`, `location.range.start` 0-indexed,
  `containerName` NotRequired = the runtime-only `UNVERIFIED` container label). Lands the
  single 1↔0-indexed boundary helper. The live `lsp` instance (currently a `_run()` local in
  `analyzer.py`) gets exposed on `AnalyzerManager` so the gated tool can reach it. This tracker
  entry also finalizes Phase 1 → done (the flip rides in Phase 2's PR, mirroring how Phase 0's
  done-flip rode in PR #3, since direct pushes to `main` are blocked).
- 2026-06-20 Phase 2 → **pr-open** (PR #4). `find_symbol` built, reviewed, QA'd,
  red-teamed (full pass + focused re-verify) — all gates green. Shipped: `positions.py` (the
  single 1↔0-indexed boundary helper, both directions, line+character); `find_symbol(name)`
  async tool (gate → `request_workspace_symbol` → map to `{name, kind, file, line, character,
  container}`, 1-indexed, workspace-relative `file`); `AnalyzerManager._lsp` exposed via a
  guarded `request_workspace_symbol` delegate + `is_ready` property. Reuses Phase 1 envelope +
  gate. **Zero/None/all-skipped → `not_found`** (never `ok`+empty). Gates: ruff/format/ty clean;
  **76 fast tests**; **7 integration tests** resolve real ripgrep symbols (positions round-trip
  into the exact source location, verified live on ~50 symbols; overloads surface as multi-hit).
  **Runtime UNVERIFIED closed:** (1) multilspy 0.0.15 does NOT populate `relativePath`/`absolutePath`
  for `workspace_symbol` — only `uri`+`range`; `file` is derived via a load-bearing
  `_uri_to_relative_path` (URL-decoded, `normpath`-hardened, out-of-repo → skipped). (2)
  `containerName` is **always absent** for workspace-symbol results → `container: null`; Phase 3
  must not lean on it (may differ for `document_symbols`/`textDocument/documentSymbol`).
  Review `minor` (3 nits fixed: top-level import, URL-decode, URI-fallback fast test).
  Adversarial `breaks-found` → **1 confirmed break fixed** (rework round 1/2): teardown/context-loss
  window (analyzer dies mid-session or post-shutdown; `_lsp` cleared but `state` stale-`ready`) made
  `find_symbol` return `error` and touch the dead delegate — fixed by gating on `is_ready`
  (`state==ready AND _lsp is not None`); re-verified `break-closed` with live happy-path intact.
  **Seams left for Phase 4:** (a) `state` still never resets off `ready` on teardown (existing); a
  `restart()` must set `state=indexing` first. (b) `is_ready` is an identity check, not a liveness
  check — a dead-but-still-referenced rust-analyzer process reports `ready` and surfaces as `error`
  (contract preserved: never a misleading `ok`/empty), but Phase 4 should reset `state` on process
  death to return `not_ready` instead.
- 2026-06-20 Phase 2 → **done**. **PR #4 merged** to `main` (merge commit `536b88d`) after CI
  ran green; `origin/main` now carries the Phase 2 `find_symbol` code + the Phase 1 done-marking.
  Local `main` synced (fast-forward, already up to date). DoD gates re-verified green on `main`:
  `ruff check` clean, `ruff format --check` (15 files formatted), `ty check` clean, **76 fast
  tests pass** (7 integration deselected). Name→position resolution is live on `main`. Resumed at
  the `pr-open` gate per continue.md step 3 (PR open+merged → gate satisfied). **Next eligible:**
  Phase 3+4 (nav + operational tools) and Phase 5 (doc-RAG) — both unlock now (5 needs only Phase
  0; 3+4 needs Phase 2). Stopping at the phase boundary; re-issue continue to start.
- 2026-06-20 Phase 3+4 → **in-progress** (branch `phase-3-4-tools`). Nav + operational tools.
  **Concurrency decision:** Phase 5 NOT run alongside — `refresh` is a shared module (Phase 4's
  `refresh` re-indexes the analyzer; Phase 5's doc store is "rebuilt wholesale by `refresh`"),
  which per continue.md step 3 forbids concurrent execution; Phase 5 lands sequentially afterward
  and extends `refresh`. Build contracts confirmed against installed multilspy 0.0.15:
  `request_document_symbols(rel) -> (flat_list, tree)` (use `[0]`); `request_definition/references(
  rel, line, column) -> List[Location]`; `request_hover(rel, line, column) -> Optional[Hover]` —
  all take 0-indexed line/column (via `external_to_lsp`); `Hover.contents` =
  MarkupContent|MarkedString|list (normalize to markdown str); `Location` may carry `relativePath`
  here (prefer it, else derive from `uri`). **Partition (file-ownership, conflict-free):** Wave 1
  foundation (2 parallel, disjoint files) — (A) `analyzer.py`: 4 guarded LSP delegates + `restart()`
  (sets `state=indexing` FIRST, closing the carried Phase-1/2 seam) + `indexed_commit` capture; (B)
  `server.py`→thin + new `core.py` (mcp, lifespan, `require_ready`, shared mapping helpers) + `tools/`
  auto-discovery pkg + move existing tools. Wave 2 (6 parallel worktrees, analyzer-free) — one file
  per tool: `document_symbols`, `goto_definition`, `find_references`, `hover`, `status`, `refresh`.
  Integration gate serial once. This entry also carries the Phase 2 → done flip from the prior run.
