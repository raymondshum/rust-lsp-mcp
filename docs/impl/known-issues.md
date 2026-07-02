# Known issues (living register)

Open design and documentation issues that are **known but not yet fixed**. This is
a living list — add an entry when one is surfaced (by a review, a red-team pass, or
a user), and close it (move to *Resolved*) when fixed.

It exists so a discrepancy surfaced in one session isn't rediscovered in the next.
It is not a bug tracker for runtime defects; it is for design warts, code/doc
drift, and gaps that need a decision.

## Review cadence

Check this list at these lifecycle checkpoints (see
[lifecycle.md](../conventions/lifecycle.md)):

- **At the start of a grill/plan session** — does an open issue affect the design
  being decided?
- **At each phase's record step** — did this phase touch an open issue? Close it,
  or note why it's carried.
- **When editing a module an open issue names** — fix it in passing if cheap, or
  confirm it's still open.

## Entry format

`### <id> — <one-line title>` then: **Where** (file:line or area), **What**
(the discrepancy), **Why it matters**, **Status** (`open` / `decided: <plan>`).

---

## Open

---

## Resolved

### KI-9 — an in-flight nav delegate can hang across a `refresh` drain of a wedged analyzer
- **Where:** [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py) (the
  `request_*` delegates) + multilspy 0.0.15 `lsp_protocol_handler/server.py`
  (`send_request` waits on `request.cv`; `stop()` does not fail pending
  `_response_handlers`).
- **What:** If a navigation tool was awaiting `self._lsp.request_*(...)` at the moment a
  `refresh` (→`restart`) drained and tore down a **wedged/unresponsive** analyzer, the
  pending request never received a response and multilspy never cancels it on `stop()`,
  so the delegate await could hang indefinitely. Analyzer-side analog of the doc-store
  race **DS-12**. Tracked as **GitHub #87** (label `followup-2026-07-02`).
- **Resolved:** 2026-07-02. Every delegate now routes its LSP await through
  `AnalyzerManager._race_teardown`, which races the request against the run's
  `_shutdown_event` (set first by `_drain_task` on both `restart()` and `shutdown()`)
  and fails the in-flight request with `AnalyzerTornDownError`; all six tool call
  sites map it to a `not_ready` envelope (`TORN_DOWN_RETRY_MESSAGE`) — a refresh is
  genuinely in flight, so `not_ready` is truthful. External cancellation (client
  disconnect) still propagates as `CancelledError`, including across the helper's
  reap windows (adversarial finding, fixed in the same unit). **Rule for future
  delegates: every `self._lsp` await must go through `_race_teardown`** — a raw await
  reopens the hang. Deliberately NO wall-clock timeout on delegate awaits (a fixed
  timeout risks false `error`s on legitimately slow queries; the helper is the single
  seam if one is ever needed). Guarded by
  [tests/test_ki9_delegate_teardown.py](../../tests/test_ki9_delegate_teardown.py)
  (17 tests: teardown races, tie-breaks, cancellation discipline, envelope mapping,
  fail-fast, leak checks). Adversarial review: 1 finding (swallowed external cancel
  in the reap windows), fixed + regression-tested; re-verified `closed`.

### KI-4 — `RLM_CHROMA_MODEL_CACHE` is a no-op setting
- **Where:** [src/rust_lsp_mcp/settings.py](../../src/rust_lsp_mcp/settings.py).
- **What:** A `chroma_model_cache` settings field (env `RLM_CHROMA_MODEL_CACHE`)
  that did nothing — ChromaDB hardcodes the model cache to `~/.cache/chroma` and
  ignored it. A knob with no effect is a usability wart.
- **Resolved:** 2026-06-21 — removed the field from `settings.py` and `env.sample`;
  the fixed `~/.cache/chroma` model-cache path is now documented in prose in the
  [configuration guide](../guide/configuration.md) ("download once" section).

### KI-1 — Ghost script reference in the env-sample honesty test
- **Where:** [tests/test_env_sample_honesty.py](../../tests/test_env_sample_honesty.py) (docstring).
- **What:** The docstring referenced a `scripts/check-env-sample.py` that never
  existed; the test itself is the check.
- **Resolved:** 2026-06-21 in PR #23 — docstring corrected to say CI runs the test
  directly in the fast tier.

### KI-2 — Stale `UNVERIFIED` marker in hover
- **Where:** [src/rust_lsp_mcp/tools/hover.py](../../src/rust_lsp_mcp/tools/hover.py).
- **What:** A comment marked rust-analyzer's hover `contents` shape `UNVERIFIED`,
  but the Phase 3+4 gate confirmed it is `MarkupContent`.
- **Resolved:** 2026-06-21 in PR #23 — comment updated to the verified shape; the
  defensive normalization of the other documented shapes was kept.

### KI-5 — UTF-16 character offsets unhandled for non-ASCII target repos
- **Where:** [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py)
  (`PatchedRustAnalyzer._get_initialize_params`).
- **What:** LSP positions default to UTF-16 code units, and multilspy advertised
  only `["utf-16"]`, so on non-ASCII (astral) lines `find_symbol`,
  `goto_definition`, `find_references`, and `hover` returned `character` values
  off by the surrogate count — a real correctness bug once the project became
  repo-agnostic (ripgrep's all-ASCII source had hidden it).
- **Resolved:** 2026-06-21 (Approach A). `PatchedRustAnalyzer` now advertises
  `positionEncodings: ["utf-32","utf-16"]`, so rust-analyzer reports **Unicode
  codepoint** offsets (verified supported — [lsp-position-encoding.md](../reference/lsp-position-encoding.md)).
  No transcoding; `positions.py` stays pure ±1. Guarded by
  [tests/test_ki5_position_encoding.py](../../tests/test_ki5_position_encoding.py)
  (unit: the negotiated list; integration: output + input side codepoint-correct
  on an astral-emoji fixture). Adversarial review: `no-breaks`.

### KI-3 — Node.js not declared in the dev container, but tasks use it
- **Where:** [.devcontainer/devcontainer.json](../../.devcontainer/devcontainer.json) · [.vscode/tasks.json](../../.vscode/tasks.json).
- **What:** The optional MCP Inspector tasks run `npx`, which needs Node.js, but no
  Node.js dev-container feature was declared, so they failed out of the box.
- **Resolved:** 2026-06-21. Declared the `ghcr.io/devcontainers/features/node:1`
  feature (`version: lts`, Node 22.x — satisfies the Inspector's Node >= 22.7.5);
  the development-guide note now says the tasks work out of the box.

### KI-6 — ripgrep-specific claim in the `status` tool docstring
- **Where:** [src/rust_lsp_mcp/tools/status.py](../../src/rust_lsp_mcp/tools/status.py) ~line 42.
- **What:** The docstring stated "For the pinned ripgrep clone (no active
  development commits) this is effectively always ready and not stale" — false
  for an actively-developed target project.
- **Resolved:** 2026-06-21 in PR #12 (Phase 2 of the
  [repo-agnostic plan](../planning/repo-agnostic-and-docker-launch.md)). The
  ripgrep-specific sentence was replaced with a repo-agnostic description of the
  staleness semantics.
