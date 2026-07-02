---
name: resolve-defect-sweep
description: Drive the 2026-07-01 defect-sweep issues (GitHub #45–#63, findings DS-01…DS-28) to completion — fixing each with tests (fast + integration) and running the gates — as a lean Fable-orchestrated Claude Code effort. Use when the user says "resolve the defect-sweep issues", "resolve all the audit issues", "start the issue-resolution effort", "continue resolving the sweep", or kicks off from the defect-sweep resolution handoff.
---

# Resolve the defect sweep — orchestrator playbook

Drive the open findings from the 2026-07-01 defect sweep to done. **Authoritative seed:**
[docs/handoff/defect-sweep-resolution-handoff.md](../../../docs/handoff/defect-sweep-resolution-handoff.md)
— read it first (it grounds what's left, sequencing, and the gotchas). The full evidence is
[docs/security/defect-sweep-2026-07-01.md](../../../docs/security/defect-sweep-2026-07-01.md); the tracker is
GitHub issues **#45–#63**.

## Role & model (keep the main thread lean)

You are the **main-thread orchestrator on Fable 5**: last stop for QA, the decision-maker, and the merge/
PR/pause owner. You do **not** do the context-heavy work on-thread — you delegate via the Agent tool and keep
your own context for judgment and integration.

- **Fable subagents** — logic/judgment-critical work: triage & branch-target decisions, design of a fix where
  correctness is subtle (lifecycle/race, position-encoding, path-containment), and the adversarial pass.
- **Sonnet subagents** — the workhorse: implementing fixes and writing/running tests.
- **You (Fable, main thread)** — orient, sequence, decide branch targets, do the **final QA pass yourself**,
  own merges and the one-PR-per-unit + record step, and stop for human review.

## The cycle (one unit per pass, then stop)

Follow the [implementation cycle](../../../docs/conventions/implementation-cycle.md) gates. A "unit" is one
issue or a tightly-coupled cluster (e.g. DS-01+DS-02 path handling; DS-03+DS-04+DS-21 lifecycle).

1. **Orient.** Read the handoff doc + this issue's audit entry. `gh issue view <n>` to confirm still open.
   Do not relitigate settled decisions.
2. **Decide the branch target** (the load-bearing project rule — decide per unit, don't default):
   - Fix touches **general** `src/` / `tests/` / `docs/guide/` / `scripts/` → **`main`-first**: branch off
     `origin/main`, PR to `main`, then cherry-pick onto `bob_prototype`.
   - Fix touches **`bob_prototype`-only** files (the offline/netiso `Dockerfile` path, baked-model config,
     `.bob/`, `AGENTS.md`) → commit on `bob_prototype` directly.
   - `bob_prototype` **never** merges into `main`. When unsure, delegate a Fable triage subagent to classify.
3. **Build.** Delegate to a Sonnet subagent: implement the fix per the DS entry, **inside a first-written
   failing test** that reproduces the finding (regression-test-first — several findings were reproduced in the
   audit; reuse those reproductions). Keep the fix scoped to the unit.
4. **Review.** Delegate a Fable subagent to read the diff against the finding's "fix direction" and the
   project's contracts (envelope semantics, readiness gating, path containment). Findings bounce to build.
5. **QA (you, on-thread — last stop).** Run the fast tier always; run the **integration gate**
   (`-m integration`, live rust-analyzer) whenever the unit touches analyzer/tool/position behavior or the
   doc-RAG runtime. CI only runs `-m "not integration"`, so the integration gate is **your** responsibility
   here. Failures bounce to build.
6. **Adversarial.** For High-severity or subtle units, delegate a fresh Fable subagent to try to falsify the
   fix (per [adversarial-review.md](../../../docs/handoff/adversarial-review.md)). Each confirmed break
   becomes a regression test and bounces to build. Bounded: 2 rework rounds, then escalate to the human.
7. **PR + record.** On all-pass, open one PR (to the branch chosen in step 2), close the issue with a
   reference, and check the DS row off in the handoff doc's "what's left" table. Then **stop for human
   review** — do not roll into the next unit unprompted unless the user asked for continuous drive.

## Sequencing (recommendation — the handoff doc is authoritative)

Highs first: **DS-01/DS-02** (path traversal + `..`-leak, one containment fix), then **DS-03/DS-04/DS-21**
(refresh/lifecycle + subprocess leak), then **DS-05/DS-06** (doc-store freshness + its untested init). Then
Mediums grouped by area (RAG chunking DS-10/DS-11/DS-23; docs/config DS-13/DS-25/DS-26; test gaps
DS-17/DS-18/DS-28). Lows (roll-up #63) last or opportunistically alongside a touched file.

## Don't-retry (from the sweep)

- The **CRLF chunking** claim was **refuted** — don't re-chase it.
- Doc-store **build-once persistence** (adopt an existing collection across restarts) is **intended**; only
  the **cross-project contamination** and stale-after-edit detection are in scope for DS-05.
- Pinned-dependency behaviors are load-bearing (multilspy 0.0.15 always populates `relativePath`, asserts on
  null LSP responses, `PurePath` join lets absolute/`..` escape, and its `start_server` teardown has no
  `finally`; mcp 1.12.4 runs sync tools inline and dispatches requests concurrently). Fixes must hold against
  these, not against assumed library behavior.
