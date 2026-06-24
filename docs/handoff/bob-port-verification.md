# Bob harness port — end-of-port Bob-IDE verification checklist

The Bob harness port (Phases 1–5) is **code-complete** but was built without a live
Bob, so every runtime-only behavior was deferred to **one live Bob-IDE pass**. This
is that pass: a mechanical, ordered walk through each deferred smoke, with the
**expected** result and the **fallback** if it fails.

Run it once, in order (later checks assume earlier ones passed). Record outcomes in
the [results table](#results) and fold any `CORRECTION` back into
[bob-harness-capabilities.md](../reference/bob-harness-capabilities.md) and the
tracker [bob-port-progress.md](bob-port-progress.md).

## Prerequisites

- [ ] Open the repository in the **Bob IDE** (the harness lives at the repo root:
  `AGENTS.md`, `agents-core.md`, `.bob/`).
- [ ] A per-user `.bob/mcp.json` is present if you want Context7 during the run
  (developer-local, gitignored — not required for these smokes).
- [ ] Know how to pick the model per mode (no per-mode model field exists — see the
  no-model-pinning parity gap in [roles.md](roles.md)); use a strong model for the
  Orchestrator and the adversarial pass.

---

## A. Spine & imports (Phase 1)

**A1 — `AGENTS.md` loads every session.** Start a fresh Bob session.
- *Check:* ask Bob to state the project's hard constraints (CI-light / host-clean /
  download-once) without opening any file.
- *Expected:* it answers from context — `AGENTS.md` and its `@`-imported
  `agents-core.md` are loaded natively.
- *If it fails:* confirm `AGENTS.md` is at the repo root and Bob recognizes it as the
  project context file (`/memory show`).

**A2 — the `@`-import resolves (not silently dropped).** Still cold.
- *Check:* ask for the **conventions pointer/trigger table** or the research-policy
  essence (both live in `agents-core.md`, pulled in via `@./agents-core.md`).
- *Expected:* present in context. Run `/memory show` and confirm `agents-core.md`
  appears as an imported file.
- *If it fails (U1/U2):* the import is being dropped — verify the `@./agents-core.md`
  line is **not** inside a code fence and the relative path is correct. This is a
  `CORRECTION` to record.

**A3 — a bare pointer is read on trigger (U2, runtime-only).**
- *Check:* trigger a deep doc that is a **bare link**, not an import — e.g. "grill me
  on X" should make Bob open `docs/conventions/` material, or ask it to follow the
  research policy to a specific convention.
- *Expected:* Bob opens the pointed-to doc when the trigger fires.
- *If it fails:* link-following is unreliable — promote the must-load content into the
  `@`-imported core. Record as a `CORRECTION` affecting the layout convention.

---

## B. Skills (Phase 2)

**B1 — `grill-me` activates (auto + by-request, U5).**
- *Check:* say *"grill me on this plan"* (and separately, a paraphrase like
  *"poke holes in this design"*).
- *Expected:* Bob proposes activating the `grill-me` skill; on approval it runs the
  one-question-at-a-time interview and reads its in-folder
  `project-style.md`.
- *If it fails:* widen the skill `description` triggers; confirm Skills aren't
  blocked by the auto-approve settings.

**B2 — `mcp-builder` reads its bundled material (U6).**
- *Check:* ask Bob to scaffold an MCP server.
- *Expected:* `mcp-builder` activates and reads its in-folder `reference/` + `scripts/`.

**B3 — in-folder bundling holds (U6).**
- *Expected:* `grill-me` uses `project-style.md` from its own folder (no repo-path
  read of `docs/conventions/grill-me.md`).

---

## C. Modes & tool-scoping (Phase 3)

**C1 — the four role modes load.**
- *Check:* open the mode picker / type `/`.
- *Expected:* `build`, `review`, `qa`, `adversarial` appear (custom modes show as
  `/<slug>`, U16), alongside the built-in Orchestrator.
- *If it fails:* validate `.bob/custom_modes.yaml` parses (`customModes:` array).

**C2 — `groups` enforce the write/verify split.**
- *Check:* in `review`, `qa`, or `adversarial`, ask Bob to edit a file.
- *Expected:* it **cannot** edit (no `edit` group); only `build` can.

---

## D. Orchestration dry-run — the 5 load-bearing unknowns (Phase 3)

Do a **dry run**: in Orchestrator mode, say *"continue the build"* against a trivial
change, and watch the cycle. Each unknown below maps to a Phase-3 design bet.

**D1 — skill-in-Orchestrator (THE load-bearing one).**
- *Check:* in **Orchestrator** mode (tool access "None"), say *"continue the build"*.
- *Expected:* the `continue-build` skill activates and supplies the playbook.
- *If it fails:* skills don't run in Orchestrator-None. **Fallback:** move the playbook
  into `.bob/rules-orchestrator/AGENTS-orchestrator.md` (mode-scoped rules, always
  loaded for the mode) — see deliverable 5 in
  [bob-port-phase-3.md](bob-port-phase-3.md). Record the `CORRECTION`.

**D2 — custom-mode delegation.**
- *Check:* watch whether the Orchestrator can spawn subtasks into `build`/`review`/etc.
- *Expected:* the built-in Orchestrator delegates to the custom role modes.
- *If it fails:* delegation may be more restricted than assumed — re-examine the
  dispatcher design (D1 in the brief).

**D3 — Orchestrator-None read ability.**
- *Check:* does the Orchestrator read any file itself, or only delegate (incl.
  orientation)?
- *Expected:* it delegates orientation (the design assumes it cannot read). If it
  **can** read, the design still works — note it as a relaxation.

**D4 — subtask isolation + result-return (U9).**
- *Check:* does a delegated subtask get a fresh context, and does its result return to
  the Orchestrator?
- *Expected (per docs):* unknown — docs are silent. If subtasks **do** isolate +
  return results, the adversarial pass may relax from "fresh session" to in-session
  delegation. If not, keep the fresh-session rule (see
  [adversarial-review.md](adversarial-review.md)).

**D5 — `.bob/rules-orchestrator/` read (fallback viability).**
- *Only if D1 failed:* create `.bob/rules-orchestrator/AGENTS-orchestrator.md` and
  confirm it loads when in Orchestrator mode.

**D6 — end-to-end.**
- *Expected:* one pass runs `build → review → QA → adversarial → PR`, sequentially,
  with the adversarial pass in a fresh session, then **stops** at the phase boundary
  (no auto-advance).

---

## Results

Fill in after the run; flip each runtime-only item in
[bob-harness-capabilities.md](../reference/bob-harness-capabilities.md) and log in the
tracker.

| ID | Check | Result (✅ / CORRECTION / blocked) | Note / fallback taken |
|----|-------|-----------------------------------|-----------------------|
| A1 | AGENTS.md loads | | |
| A2 | `@`-import resolves (U1/U2) | | |
| A3 | bare pointer read on trigger (U2) | | |
| B1 | `grill-me` activates (U5) | | |
| B2 | `mcp-builder` reads bundle (U6) | | |
| C1 | role modes load (U16) | | |
| C2 | `groups` write/verify split | | |
| D1 | skill-in-Orchestrator-None | | |
| D2 | custom-mode delegation | | |
| D3 | Orchestrator-None read | | |
| D4 | subtask isolation + result (U9) | | |
| D6 | end-to-end cycle + stop | | |

**On completion:** if all green, mark Phase 5 `done` in
[bob-port-progress.md](bob-port-progress.md) and note the port fully verified. Any
`CORRECTION` becomes a small follow-up (most likely the D1 → `.bob/rules-orchestrator/`
fallback).

## Pointers

- [bob-port-progress.md](bob-port-progress.md) — tracker (record results here)
- [bob-port-phase-3.md](bob-port-phase-3.md) — the D1–D7 design + deferred inventory
- [bob-harness-capabilities.md](../reference/bob-harness-capabilities.md) — Bob facts to flip
- [roles.md](roles.md) · [continue.md](continue.md) · [adversarial-review.md](adversarial-review.md)
