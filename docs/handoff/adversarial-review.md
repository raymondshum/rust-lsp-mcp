# Adversarial review (contract falsification)

An independent pass that tries to **break the contract**, run after QA on every phase
and as **gate-zero** over `docs/handoff/` before any building. It is the
implementation-time analog of grill-me (design) and the verification pass (plan).

## Mandate

Given the phase's **invariants and definition of done** — not the build agent's
rationalizations — and a **fresh, cold context**, actively attempt to make the
implementation violate its contract. Independence is the point.

The project's load-bearing invariant: **never a misleading empty answer while indexing.**
Attack it directly, plus:

- `{status}` envelope misuse — `ok`+empty (valid zero answer, e.g. references with no
  callers) vs `not_found` (resolution failed). Find a case that returns the wrong one.
- Readiness race — any call sequence during indexing that returns empty/partial instead
  of `not_ready`.
- 1-indexed boundary — off-by-one or non-round-tripping positions through the single
  boundary helper.
- Staleness — `status.stale` claiming fresh when it isn't (uncommitted edits caveat).
- `refresh` wiping the analyzer's saved work, or leaving readiness wrongly set.

## Rules that keep it useful (not noise)

- **Concrete falsifiers only.** Every finding is a failing input / reproduction
  ("this call sequence returns `[]` mid-index"), never "have you considered X."
- **Findings become tests.** Each confirmed break is added to the QA suite as a
  regression test, so the attack only has to land once.
- **Scope = implementation vs contract, not architecture.** Do not reopen settled
  design decisions (same guardrail as grill-me).
- **Bounded loop.** Findings bounce back to the build agent for rework; after **N=2**
  rework rounds without resolution, escalate to the human. No adversarial ping-pong.

## Placement

`build → review → QA → adversarial → PR`. Before QA it duplicates; after the PR it is
too late. Intensity scales with risk: **full red-team on Phases 1–2 and 5**; a lighter
contract-check on Phase 0 config and trivial nav tools — but it always runs.

## Gate-zero over docs/handoff/

Before the first build agent launches, an adversarial agent reads the durable prompts,
[continue.md](continue.md) (the dispatcher logic itself), [progress.md](progress.md)'s
dependency graph, and [roles.md](roles.md) and hunts for places they would let an agent:
skip a gate, deadlock the Phase 0 seam, touch the live analyzer in parallel, write
shared state (incl. `pyproject.toml`/lock) concurrently, exceed a phase's scope, or
merge a real conflict on the lean thread. Fix findings, set `progress.md` gate-zero
`passed`, then stop for human review before building.
