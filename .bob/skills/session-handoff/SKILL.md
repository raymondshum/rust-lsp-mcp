---
name: session-handoff
description: Produce a next-session handoff — a durable committed handoff doc under docs/handoff/ PLUS a paste-ready kickoff prompt — for a completed or paused effort in this project. Use when the user says "write a handoff", "hand this off to the next session", "produce a kickoff prompt for next time", "create a seed for picking this up later", or after closing a multi-phase effort that will continue in a fresh session.
---

# session-handoff (rust-lsp-mcp)

Captures the state of an effort so a **fresh session can resume cold with full context**. Produces two
artifacts: (1) a **durable committed handoff doc** under `docs/handoff/` (the authoritative seed), and
(2) a **paste-ready kickoff prompt** that points at the doc and carries the standing preferences. This is
the inverse of the "continue the build" / orient step (which *reads* state): `session-handoff` *writes* the
state a future session will read.

Distinct from `log-this`-style single-decision notes and from the progress trackers
(`docs/handoff/*progress*.md`, which track a phase build). Use `session-handoff` when the unit of continuity
is **a whole effort across a session boundary** — what shipped, what's left, how to drive it, and the traps
not to re-hit.

## When to invoke

- A multi-phase effort just closed (or paused) and the remaining work continues later.
- Trigger phrasings: "write a handoff", "hand this off", "kickoff prompt for the next session", "seed for
  resuming this", "what should the next session know".
- If the scope is unclear (bare "write a handoff"), ask: *"Handoff for which effort, and what's the
  next-session goal — finish remaining work, or a fresh phase?"* Do not guess the remaining-work landscape.

## Core principle — GROUND the state, don't recite it

The single most important step is that **"what's left" is verified against current code/docs/issues, not
recalled from conversation memory.** Audits and issue lists go stale (a finding may already be fixed). Before
writing the handoff, grep/inspect the repo and query the tracker (GitHub issues via `gh`, or the relevant
`docs/handoff/*progress*.md`) to confirm each open item is actually still open. A handoff that sends the next
session at already-done work is worse than no handoff.

## Steps

1. **Establish the anchor.** Identify the driving document(s) the effort traces to. In this project that is
   typically an audit under `docs/security/` or `docs/reference/`, a plan under `docs/planning/`, a phase
   tracker under `docs/handoff/`, or the living [known-issues register](../../../docs/impl/known-issues.md).
   For issue-driven efforts, the anchor is the set of GitHub issues (`gh issue list`).

2. **Ground the remaining work.** For each candidate open item, verify it is genuinely unaddressed:
   `gh issue view <n>` for state; grep for the symbol/file/helper the item names; re-read the module. Confirm
   a "deferred" row hasn't since been addressed. Record what you verified. Discard items that turn out done;
   flag any the driving doc lists as open but that are actually closed.

3. **Write the durable handoff doc** at `docs/handoff/{slug}-handoff.md` and **update
   [docs/handoff/index.md](../../../docs/handoff/index.md)** in the same step (index-currency rule from
   `AGENTS.md`). Sections:
   - **Where we are** — what shipped (PR/issue/commit refs), what's deferred and why.
   - **What's left** — a table of open items, each *grounded* (step 2), with issue/audit refs and a one-line
     status/caveat. Include a **recommended sequencing** (mark it a recommendation; scope is the human's call).
   - **Model & orchestration preferences** — carry the user's standing strategy. **Confirm/refresh with the
     user if they've restated it this session.** Two supported shapes for this project:
     - *Claude Code main-thread* (default when resuming in this harness): **Fable 5** on the main thread as
       orchestrator, last-stop QA, and decision-maker; deploy subagents to keep the main thread lean —
       **Fable** subagents for logic/judgment-critical work, **Sonnet** subagents for implementation and
       testing. Orchestrator does the final QA pass and owns merges + the PR/pause decision.
     - *Bob custom-modes* (when driving under the Bob harness): the Orchestrator + `build`/`review`/`qa`/
       `adversarial` modes in `.bob/custom_modes.yaml`, **sequential** delegation, per
       [docs/handoff/roles.md](../../../docs/handoff/roles.md) and the
       [continue-build](../continue-build/SKILL.md) playbook.
     Either way the gate sequence is the [implementation cycle](../../../docs/conventions/implementation-cycle.md):
     Orient → (gate-zero once) → pick → build → review → QA → adversarial → PR+record; one unit per pass,
     stop for human review. Research policy is Context7-first
     ([research-policy.md](../../../docs/conventions/research-policy.md)). When an adversarial/QA finding
     bounces back, **add a regression test before re-fixing**. Do not reopen settled decisions.
   - **Stack commands** — the fast tier and the integration gate (see below).
   - **Key file references** — a table: the driving doc, the design-of-record, reference implementations of
     any established pattern, the lifecycle/process docs, config to keep in lockstep (`pyproject.toml`,
     `env.sample`, CI workflow).
   - **Gotchas / things NOT to retry** — the hard-won *settled* facts: decisions not to relitigate, pinned
     dependency behaviors (e.g. multilspy 0.0.15 / mcp 1.12.4 quirks), footguns, and an explicit "don't try"
     list. Highest-value section.
   - **How to start** — the resume protocol (read this doc + the anchor + known-issues; pick the unit; honor
     prefs/gotchas; run the kickoff command).

4. **Produce the paste-ready kickoff prompt** — a chat message (in a fenced ```text block) the user can paste
   to seed the next session. It states the goal, says **read the durable handoff doc first** (by path) as
   authoritative, names what's left + the recommended start, restates the model/orchestration prefs compactly,
   and lists the top gotchas / don't-retry. If a companion goal command exists (e.g.
   [resolve-defect-sweep](../resolve-defect-sweep/SKILL.md)), name it as the kickoff. Keep it self-contained
   but lean — the detail lives in the committed doc.

5. **Surface both.** Print the handoff-doc path and the kickoff prompt for review. Reconcile any stale status
   bookkeeping the handoff implies (flip an audit/known-issues row to reflect what shipped) if the user wants
   the close-out in the same pass.

## Stack commands (this project)

Fast tier (CI-equivalent) — run on every unit:
```
uv sync
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run --frozen pytest -m "not integration"
uv run --frozen pytest tests/test_env_sample_honesty.py -v
```
Integration gate — **local QA only, never CI** (needs live rust-analyzer + the ripgrep fixture):
```
uv run --frozen pytest -m integration
```
Full suite: `uv run --frozen pytest`.

## Exit condition

- The durable handoff doc exists under `docs/handoff/` with all sections, `index.md` is updated, and its
  "what's left" is **grounded** (verified, not recited).
- A paste-ready kickoff prompt is surfaced to the user.
- (If closing out an effort) stale status rows are reconciled.

## PR / branch awareness (this project's rule)

`bob_prototype` is a long-running feature branch that is **never merged into `main`**; changes flow
`main → bob_prototype` via cherry-pick (see the [branch-flow rule](../../../docs/impl/known-issues.md) and
project memory). Apply it to where the handoff artifacts land:

- **Bob-harness scaffolding** — anything under `.bob/` (skills, modes) and effort-continuity docs that
  reference it — is **`bob_prototype`-only**. Commit directly to `bob_prototype`; do **not** cherry-pick to
  `main` (`.bob/` does not exist there). Handoff docs under `docs/handoff/` that seed a `bob_prototype`
  effort ride the same rule.
- **General code / reference docs** (`src/`, `tests/`, `docs/security/` audits, `docs/guide/`, `Dockerfile`,
  `scripts/`) go **`main`-first**: branch off `origin/main`, commit, open a PR against `main`, then
  cherry-pick onto `bob_prototype`. Never merge `bob_prototype → main`.
- Match the surrounding convention (skills use `name` + `description` frontmatter only). After `gh pr merge`
  verify `state == MERGED`. **Degraded mode:** if `gh` is unavailable or not a git repo, write the doc
  locally, skip the PR, and print a one-line warning.
