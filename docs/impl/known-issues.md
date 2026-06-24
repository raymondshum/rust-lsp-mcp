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

### KI-8 — devcontainer still provisions the Claude Code IDE extension
- **Where:** [.devcontainer/devcontainer.json](../../.devcontainer/devcontainer.json)
  (+ the extension table in [development.md](../guide/development.md)).
- **What:** The Bob harness port (Phases 1–5) re-tools the *agent-facing* scaffolding
  (`AGENTS.md`, `.bob/skills/`, custom modes, docs) but does **not** touch the dev
  container's installed VS Code extensions, which still include the Claude Code
  extension. `development.md` documents that accurately, so it is **not stale** — but
  it is a residue: an "IDE-first" Bob harness ought to provision the Bob extension.
- **Why it matters:** the harness isn't fully Bob until the IDE the container opens is
  Bob's; tooling and intent otherwise disagree.
- **Status:** `open` — **out of the documented Phase 1–5 scope** (docs/skills/modes/mcp,
  not `devcontainer.json`). Decide separately whether the port extends to dev-container
  provisioning.

---

## Resolved

### KI-7 — `grill-me` style content is duplicated (canonical convention + bundled skill copy)
- **Where:** [docs/conventions/grill-me.md](../conventions/grill-me.md) (canonical) and
  [.bob/skills/grill-me/project-style.md](../../.bob/skills/grill-me/project-style.md) (bundled copy).
- **What:** The Bob harness port bundles a copy of the grilling-style convention
  *inside* the `grill-me` skill folder, because Bob skills can only reliably read files
  in their own directory (`U6`, [bob-harness-capabilities.md](../reference/bob-harness-capabilities.md)).
- **Resolved:** 2026-06-24 (Phase 5) — **won't dedupe; the split is structural.** `U6`
  requires the in-folder skill copy, and the canonical `docs/conventions/grill-me.md`
  is the single source the `AGENTS.md` core points to (with `CLAUDE.md` now retired,
  one referrer is gone, but the canonical still serves the convention). Both copies are
  load-bearing, so this is a documented **sync obligation** (noted in both files), not a
  removable duplicate.

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
