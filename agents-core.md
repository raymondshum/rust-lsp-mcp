# Agent core — always-load

This file is `@`-imported by the root [AGENTS.md](AGENTS.md), so its content is
inlined into **every** Bob session. It holds the must-load core: the conventions
pointer/trigger table and the hard constraints. Keep it thin — deep detail stays
in the linked `docs/` files, read on demand. See
[agents-md-layout.md](docs/conventions/agents-md-layout.md) for the load model and
the tier rules.

> Paths below are repo-root-relative (this file lives at the repo root, and Bob
> works from the repo root). They are **on-demand pointers**, not imports: open
> the file when its topic or trigger applies.

## Conventions

The [delivery lifecycle](docs/conventions/lifecycle.md) is the **spine**
(Grill → Plan → Verify → Implement → Document); the per-stage docs below
implement it.

- [AGENTS.md layout](docs/conventions/agents-md-layout.md) — read before editing
  `AGENTS.md` or this core: thin pointers + the `@`-import load model; detail
  lives in `docs/`.
- [working style](docs/conventions/working-style.md) — how to propose approaches and decide.
- [research policy](docs/conventions/research-policy.md) — read before trusting
  memory on any library/API detail. **Essence:** prefer Context7 (and current
  first-party docs) over training knowledge; when docs are silent, read the
  package source.
- [caching & docs/ layout](docs/conventions/caching.md) — where learned patterns
  go (`docs/` is the durable store; Bob has no per-user auto-recall memory), and
  the `docs/` category folders.
- [documentation writing](docs/conventions/documentation-writing.md) — read before
  writing or revising human-facing docs (README, `docs/guide/`). The loop:
  **ground (fan-out explorers → fact-sheets) → shared contract → write (one
  page/agent) → verify**.
- [verification pass](docs/conventions/verification-pass.md) — read before a
  "verify the plan" / "flip UNVERIFIED to VERIFIED" pass: inventory → confirm via
  Context7/source → cache + annotate the residue.
- [phasal plan](docs/conventions/phasal-plan.md) — the output contract for
  planning: what a grill/plan session must produce (phases, deps, file-ownership
  partitions, definition-of-done, adversarial intensity) to be implementable.
- [implementation cycle](docs/conventions/implementation-cycle.md) — the standard
  build loop (build → review → QA → adversarial → PR → record), one phase per
  pass. rust-lsp-mcp's instance is the handoff dispatcher
  [continue.md](docs/handoff/continue.md).
- Grilling a plan/design: first read
  [grill-me.md](docs/conventions/grill-me.md). The loop is **grill (decide) →
  `UNVERIFIED` inventory → [verification pass](docs/conventions/verification-pass.md)**.
- [known issues](docs/impl/known-issues.md) — living register of open design /
  documentation issues. **Review** at the start of a grill/plan session, at each
  phase's record step, and when editing a module an open issue names.

## Constraints

- **CI must stay light.** Free-tier GitHub Actions has a monthly quota. CI runs
  lint, type checks, and fast tests only; heavy/integration tests run locally as a
  named QA gate, never in CI.
- **Host stays clean.** All toolchains, binaries, and services live in the dev
  container; nothing is installed on the host macOS. Prefer dev-container features /
  in-container installs over anything that touches the host.
- **Download once.** Heavy downloaded artifacts (models, analyzer binaries,
  build/index caches) go on observable, gitignored bind mounts so they survive
  rebuilds and aren't refetched.
