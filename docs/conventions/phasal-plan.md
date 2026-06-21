# Phasal plan (the output contract for planning)

A grill/planning session is not done when the design is decided — it is done when
it has produced a **phasal plan** the [implementation cycle](implementation-cycle.md)
can execute without re-deciding anything. This page defines the required shape.

This is the exit artifact of the **Plan** stage of the
[delivery lifecycle](lifecycle.md). The implementation cycle consumes *exactly*
this shape.

## Why phasal

The implementation cycle advances **one phase per pass** and fans work out within
a phase. For that to work, the plan must already say what a phase is, what it
depends on, what can run in parallel, and how "done" is proven. A prose design
document is not implementable; a phasal plan is.

## Required elements

A complete phasal plan has all of the following:

1. **Ordered phases, sequenced risk-first.** Break the work into phases that each
   ship a coherent slice. Put the highest-risk, most load-bearing phases first, so
   risk is retired early.
2. **Per-phase dependencies.** For each phase, which other phases must be `done`
   before it can start. This forms the dependency graph the cycle walks.
3. **Parallelizable flag + partition.** For each phase, whether its internal tasks
   can fan out, and the **file-ownership partition** that makes them conflict-free.
   Call out any task that contends on a shared serialized resource (a live server,
   an index, a warm cache) — those run serially.
4. **Definition of done (the QA gate).** What proves the phase works: the fast-tier
   checks (always) plus any heavier gate (live services, integration tests) the
   phase needs. State the exact gate so QA is unambiguous.
5. **Adversarial intensity.** How hard to red-team the phase, scaled to its risk —
   a light contract-check for low-risk glue, a full red-team for the risk core.
6. **Runtime `UNVERIFIED` inventory.** The claims that can only be confirmed at
   build time (an API's real shape, a library's runtime behavior). The cycle
   confirms these as it builds the phase.
7. **A durable per-phase prompt.** A self-contained brief for each phase that
   survives across sessions, so the build can resume cold. (In this project these
   live under [docs/handoff/](../handoff/index.md).)

## The progress tracker

The plan ships with a **single-source-of-truth progress tracker** that the
[implementation cycle](implementation-cycle.md) reads to pick the next phase and
writes to record state. It holds:

- a **state vocabulary** (e.g. `not-started → in-progress → qa → adversarial →
  pr-open → done`, plus `blocked: <reason>`);
- the **gate-zero** line (the plan/handoff self-review must pass before any phase
  starts);
- a **phase table** (phase · depends-on · parallelizable · state);
- the **dependency graph**;
- an append-only **log** (one line per state transition).

The orchestrator is its **sole writer**. In this project that tracker is
[docs/handoff/progress.md](../handoff/progress.md).

## Settled decisions

The plan records the decisions that are **settled** and must not be reopened
without new information. Grilling is where decisions are made; the plan is where
they are frozen. The implementation cycle's step 1 ("do not relitigate settled
decisions") relies on this list existing.

## Related

- [grill-me.md](grill-me.md) — how the decisions feeding the plan get made.
- [verification-pass.md](verification-pass.md) — confirms the plan's `UNVERIFIED`
  items before building.
- [implementation-cycle.md](implementation-cycle.md) — what consumes this plan.
- [lifecycle.md](lifecycle.md) — where Plan sits in the whole.
