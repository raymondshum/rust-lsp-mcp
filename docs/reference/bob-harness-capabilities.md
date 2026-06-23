# IBM Bob — harness capabilities (verification cache)

Source: **live `bob.ibm.com/docs`** (authoritative; Context7's `bob_ibm` index is
incomplete — see [[bob-research-source]] / memory). Stamp: **IBM Bob docs as of
2026-06-23**. Confirms the `UNVERIFIED` inventory in
[bob-harness-port.md](../planning/bob-harness-port.md) Phase 0.

Verdict legend: `VERIFIED` · `CORRECTION` (assumption was wrong) ·
`UNVERIFIED — runtime-only` (docs silent; confirm by testing in Bob).

## AGENTS.md & imports

- **U1 — `@`-import syntax · VERIFIED.** `@./file.md` (same dir), `@../file.md`
  (parent), `@./sub/file.md`, `@/abs/path.md`. Recurses (files import files),
  **default max depth 5**. `@` inside fenced code blocks is ignored. *Glob/wildcard
  support: not documented (UNVERIFIED — runtime-only).*
  Src: `/docs/shell/configuration/memory-import`.
- **U2 — plain markdown links auto-followed? · UNVERIFIED — runtime-only.** Docs
  describe inclusion **only** via `@`-import; silent on whether a bare `[x](./x.md)`
  is read. **Design rule: use `@`-imports for anything that must load — do not rely
  on link-following.** Src: `/docs/shell/configuration/memory-import`.
- **U3 — `/init` output · VERIFIED; overwrite behavior UNVERIFIED — runtime-only.**
  `/init` generates a root `AGENTS.md` (project overview, directory structure, stack,
  patterns, workflows) **and** `.bob/rules-{mode}/AGENTS-{mode}.md` per built-in mode
  (e.g. `.bob/rules-code/AGENTS-code.md`). Whether it overwrites an existing
  `AGENTS.md` is not documented. Src: `/docs/ide/getting-started/tutorials/start-a-project`.
- **U4 — loading hierarchy & precedence · VERIFIED.** Context hierarchy: global
  `~/.bob/AGENTS.md` → project root (+ parents) → local subdirs. Rules precedence:
  **mode-specific rules → AGENTS.md → general workspace rules**; global rules below
  workspace; everything **accumulates** (combined with the mode's `customInstructions`),
  not replace. Src: `/docs/shell/configuration/configuring`, `/docs/ide/configuration/rules`.

## Skills

- **U5 — invocation · VERIFIED (consequential).** Activation is **model-decided
  from the skill's `description`** ("Bob automatically determines when to activate a
  skill"); **no documented explicit user trigger** (no `/skill` slash command, no
  by-name invoke). User control is approve/deny only. **Impact: `grill-me` cannot be
  deliberately invoked the way it is under Claude — it must be auto-activated by a
  well-written `description`.** Src: `/docs/ide/features/skills`.
- **U6 — out-of-folder file refs · UNVERIFIED — runtime-only.** Docs say Bob "gains
  access to any supporting files **in the skill directory**"; silent on referencing
  repo paths outside the skill folder. **Design rule: bundle a skill's material
  *inside* its folder** (e.g. copy `grill-me.md` into `.bob/skills/grill-me/`) rather
  than pointing at `docs/conventions/`. Src: `/docs/ide/features/skills`.
- **U7 — approval setting · VERIFIED.** Skills require approval by default; bypass via
  **Bob Settings → Auto-Approve → "Skills" toggle** (also the Auto-Approve toolbar);
  risk level Medium. No `settings.json` key name is documented. Src:
  `/docs/ide/features/skills`, `/docs/ide/features/auto-approving-actions`.
- **U8 — mechanism inventory · CORRECTION.** Skills/commands/modes are **not** the
  full set: Bob also documents **custom rules**, **context mentions**, and **code
  actions** (different category — not behavior-teaching). No plugins/hooks. **Skills
  are IDE-only — there is no Skills page in the Bob Shell docs** (Shell extensibility
  = custom modes + slash commands). *Impact on the CLI stretch goal: skills won't
  port to Bob Shell as-is; they'd need re-expression as modes/commands.* Src:
  `/docs/ide/features/skills`, `/docs/shell`.

## Modes & orchestration

- **U9 — subtask context isolation / boomerang · UNVERIFIED — runtime-only.** Docs
  state only: "Subtasks are separate task instances that Bob creates to break down
  complex work." **No** documented fresh-context-per-subtask, **no** result/summary
  passing to parent, **no** parallel-vs-sequential statement. **The adversarial
  "cold-context" independence cannot be guaranteed from docs — confirm by test in
  Phase 3.** Src: `/docs/ide/features/auto-approving-actions`, `/docs/ide/features/modes`.
- **U10 — built-in modes & custom delegation · CORRECTION + VERIFIED.** Built-ins are
  **five**: 💻 Code (`read,edit,command`), ❓ Ask (`read,browser,mcp`), 📝 Plan
  (`read,edit`-markdown,`browser,mcp`), 🛠️ Advanced (all groups), 🔀 Orchestrator
  (Tool Access **None**). **No Architect, no Debug mode.** Orchestrator **can delegate
  to custom modes** — `whenToUse` is "used by Orchestrator for task coordination."
  Src: `/docs/ide/features/modes`, `/docs/ide/configuration/custom-modes`.
- **U11 — `custom_modes.yaml` schema · VERIFIED (two sub-points UNVERIFIED).**
  Top-level `customModes:` array in `.bob/custom_modes.yaml`. Fields: `slug`, `name`,
  `roleDefinition`, `whenToUse` (opt), `customInstructions` (opt), `groups`. A no-tools
  mode is supported in principle (Orchestrator = "None"), but the explicit empty-groups
  YAML form is not shown (UNVERIFIED — runtime-only). The `command` group has **no**
  documented restriction sub-syntax (only `edit` takes `fileRegex`). Bob Shell schema
  adds a `description` field. Example:
  ```yaml
  customModes:
    - slug: docs-writer
      name: 📝 Documentation Writer
      roleDefinition: You are a technical writer...
      whenToUse: Use this mode for writing and editing documentation.
      customInstructions: Focus on clarity and completeness.
      groups:
        - read
        - - edit
          - fileRegex: \.(md|mdx)$
            description: Markdown files only
        - browser
  ```
  Src: `/docs/ide/configuration/custom-modes`, `/docs/shell/configuration/custom-modes-bobshell`.

## Memory

- **U12 — `/memory` & autonomous write · VERIFIED + UNVERIFIED.** `/memory refresh`
  (reload context files) and `/memory show` (view current context) are documented; no
  add/write subcommand. Whether the agent can **autonomously write** to an
  `@`-imported file (the optional `AGENTS.local.md` emulation) is **not documented**
  (UNVERIFIED — intentionally deferred; memory routes to `docs/` per decision #6).
  Src: `/docs/shell/configuration/configuring`, `/docs/shell/configuration/memory-import`.

## Permissions & MCP

- **U13 — `.bob/mcp.json` schema · VERIFIED.** Top-level `mcpServers`. Per-server keys:
  `command`,`args`,`cwd`,`env`,`url`,`headers`,`alwaysAllow`,`disabled` (Shell adds
  `httpURL`,`timeout`). **Project `.bob/mcp.json` overrides global**, and the global
  file is named differently: **`~/.bob/mcp_settings.json` (global) vs `.bob/mcp.json`
  (project)**. Note: docs call `.bob/mcp.json` shareable "via version control" — i.e.
  it is committable by design; **decision #7 keeps it gitignored anyway (our choice,
  not a Bob limit).** Src: `/docs/ide/configuration/mcp/mcp-in-bob`,
  `/docs/shell/configuration/mcp/mcp-bobshell`.
- **U14 — `groups` schema · CORRECTION.** Allowed values: `read`, `edit`
  (+`fileRegex`), `command`, `browser`, `mcp`, **`skill`** (the earlier list omitted
  `skill`). Src: `/docs/ide/configuration/custom-modes`.
- **U15 — auto-approval · VERIFIED.** A **UI toolbar** of per-action toggles (11
  actions: Read, Write, Browser, Retry, MCP, Mode, Subtasks, Execute, Question, Todo,
  Skills); no documented `settings.json` key; storage location / committability not
  stated (UNVERIFIED — runtime-only; decision #7 treats it as per-user). Src:
  `/docs/ide/features/auto-approving-actions`.
