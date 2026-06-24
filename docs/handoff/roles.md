# Orchestration model (Bob modes, sequential delegation)

How the implementation is executed under the **IBM Bob** harness. Read with
[progress.md](progress.md) (state) and [continue.md](continue.md) (how to kick it
off). The live playbook the Orchestrator runs is the
[`continue-build` skill](../../.bob/skills/continue-build/SKILL.md); the role modes
are defined in [`.bob/custom_modes.yaml`](../../.bob/custom_modes.yaml). Bob facts
are verified in [bob-harness-capabilities.md](../reference/bob-harness-capabilities.md).

## Roles (Bob modes)

- **Orchestrator — built-in Orchestrator mode (tool access `None`).** Coordinates by
  **delegating** to the role modes as subtasks **one at a time**; it does not read,
  edit, or run anything itself. It owns the integration branch, the PR/pause
  decision, and is the **sole writer** of [progress.md](progress.md) and shared
  config (`pyproject.toml`, `uv.lock`, `.vscode/`, CI) — including **all
  dependency/lockfile changes**, done serially *before* any delegation. Because it
  cannot read, it delegates even orientation (a read-only report of the next phase).
- **Build — `build` mode (`read, edit, command, mcp`).** Implements a phase's tasks
  against its durable brief in `docs/handoff/`. The **only** mode that edits. Does
  not write `progress.md` or shared config (role discipline — `fileRegex` is
  positive-match only, so it isn't a hard lock).
- **Review — `review` mode (`read, command`).** Reads the build's diff for
  correctness/quality before merge; reports concrete findings. Does **not** edit.
- **QA — `qa` mode (`read, command`).** Runs the gates: the fast tier always; the
  **live-analyzer integration gate** where the phase requires it (the local QA gate,
  never CI). Does **not** edit.
- **Adversarial — `adversarial` mode (`read, command`), in a fresh session.** Tries
  to *falsify the contract* after QA passes. See
  [adversarial-review.md](adversarial-review.md). Does **not** edit — confirmed
  breaks bounce to `build` as regression tests.

## Sequential delegation (no parallelism)

Bob's Orchestrator delegates **sequentially** — there is no parallel fan-out and no
git-worktree isolation. The Orchestrator delegates build → review → QA → adversarial
**one subtask at a time**; `build` edits the working tree directly, so there are no
parallel branches to merge. One phase per pass; stop at the boundary.

The single warm rust-analyzer (and the ChromaDB store) live on shared bind mounts
inside the devcontainer. Under sequential delegation this constraint is **trivially
satisfied** — only one subtask runs at a time, so the analyzer is never contended and
the integration gate never races.

## Named parity gaps (vs the former Claude harness)

- **No parallelism.** Bob delegates sequentially, so the analyzer-free slice (docs,
  independent edits) that the Claude harness fanned out now runs serially. Impact is
  narrow — the single serialized analyzer already forced most build work one-at-a-time.
- **No per-mode model pinning.** Bob `custom_modes.yaml` has no model field, so the
  Claude model's Opus-for-orchestrator/adversarial, Sonnet-for-the-rest split can't be
  encoded. **The human selects the model per mode/subtask in the UI** — run the
  Orchestrator and the adversarial pass on a strong (Opus-class) model.

## Integration & PR

`build` edits the working tree → `review`/`qa`/`adversarial` verify → the Orchestrator
commits and opens **one PR per phase** to `main` for human review. Opening a PR needs
`gh`/GitHub auth; **if it can't open the PR, the Orchestrator pauses for the human**
(with the branch ready). Granularity is per phase, not per task.

## Guardrails (all modes)

Do not relitigate settled decisions (the plan's "Settled architecture" and the Bob
port's frozen decisions). Stay inside the phase's scope/stop boundary. Confirm that
phase's runtime-only `UNVERIFIED` items as you go. Stop at the boundary and report —
never roll into the next phase automatically. Rework caps: **2 rounds per gate**.
