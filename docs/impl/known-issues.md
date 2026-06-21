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

### KI-1 — Ghost script reference in the env-sample honesty test
- **Where:** [tests/test_env_sample_honesty.py](../../tests/test_env_sample_honesty.py) line 7 (docstring).
- **What:** The docstring states the check "is duplicated in CI via
  `scripts/check-env-sample.py`," but that script does not exist. The honesty
  check is actually covered by the test itself (which CI runs).
- **Why it matters:** Documentation/comment vs. reality drift; misleads anyone
  looking for the referenced script.
- **Status:** open. Fix: either add the script, or correct the docstring to
  describe how the check actually runs.

### KI-2 — Stale `UNVERIFIED` marker in hover
- **Where:** [src/rust_lsp_mcp/tools/hover.py](../../src/rust_lsp_mcp/tools/hover.py) ~line 94.
- **What:** A code comment marks the shape of rust-analyzer's hover `contents` as
  `UNVERIFIED`, but the Phase 3+4 integration gate confirmed it live (it is
  `MarkupContent`). The comment was never updated.
- **Why it matters:** Code comment contradicts confirmed runtime behavior; could
  mislead a future change.
- **Status:** open. Fix: update the comment to reflect the verified shape (and
  keep the defensive normalization either way).

### KI-3 — Node.js not declared in the dev container, but tasks use it
- **Where:** [.vscode/tasks.json](../../.vscode/tasks.json) (the MCP Inspector
  tasks) vs. [.devcontainer/](../../.devcontainer/) (no Node feature).
- **What:** The optional "MCP Inspector" tasks run `npx`, which needs Node.js, but
  no Node.js dev-container feature is declared. The
  [development guide](../guide/development.md) now tells the user to install it
  manually, but the underlying design choice is unresolved.
- **Why it matters:** The tasks fail out of the box; the fix is a small design
  decision, not just documentation.
- **Status:** open. Decision needed: declare a Node.js dev-container feature, or
  drop the Inspector tasks.

### KI-4 — `RLM_CHROMA_MODEL_CACHE` is a no-op setting
- **Where:** [src/rust_lsp_mcp/settings.py](../../src/rust_lsp_mcp/settings.py)
  (`chroma_model_cache`).
- **What:** The setting is informational only — ChromaDB hardcodes the model cache
  path to `~/.cache/chroma` and ignores this value. It is documented as such in
  both the code and the [configuration guide](../guide/configuration.md).
- **Why it matters:** A configuration knob that does nothing is a usability wart; a
  user may set it expecting an effect.
- **Status:** open. Decision needed: keep it purely as documentation of the
  bind-mount target, rename it to signal "informational," or remove it and
  document the path elsewhere.

---

## Resolved

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

### KI-6 — ripgrep-specific claim in the `status` tool docstring

### KI-6 — ripgrep-specific claim in the `status` tool docstring
- **Where:** [src/rust_lsp_mcp/tools/status.py](../../src/rust_lsp_mcp/tools/status.py) ~line 42.
- **What:** The docstring stated "For the pinned ripgrep clone (no active
  development commits) this is effectively always ready and not stale" — false
  for an actively-developed target project.
- **Resolved:** 2026-06-21 in PR #12 (Phase 2 of the
  [repo-agnostic plan](../planning/repo-agnostic-and-docker-launch.md)). The
  ripgrep-specific sentence was replaced with a repo-agnostic description of the
  staleness semantics.
