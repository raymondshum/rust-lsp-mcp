# AGENTS.md layout convention

Read this before adding to or restructuring `AGENTS.md` (the root Bob context
file) or its `@`-imported core.

`AGENTS.md` is the project's instruction spine. Bob loads the root-project
`AGENTS.md` into context at the start of **every** session (precedence:
mode-rules → `AGENTS.md` → workspace rules, all *accumulated*), so it must stay
**thin**: it holds **pointers, not long instructions**.

## How loading works in Bob (the load model)

Two facts drive the layout, both confirmed in
[bob-harness-capabilities.md](../reference/bob-harness-capabilities.md):

- **The root `AGENTS.md` loads natively every session** (`U4`). Anything written
  in it — or pulled in by an `@`-import — is *guaranteed* present.
- **`@`-import inlines a file; bare links are not guaranteed to be followed**
  (`U1`/`U2`). `@./file.md`, `@../file.md`, `@./sub/file.md`, `@/abs/path.md`
  inline that file's content (recursively, max depth 5; an `@path` inside a fenced
  code block is ignored). A plain markdown link is read **on demand** — Bob's docs
  only promise inclusion via `@`-import, so on-demand reading is runtime behavior
  we rely on but do not assume.

The consequence is a **three-tier** structure:

1. **Must-load core — `@`-imported.** The behavior-critical essentials a session
   must see even if it never follows a single pointer: the hard constraints, the
   research-first essence, and the **pointer/trigger table itself** (so the agent
   knows what docs exist and *when* to read them). These live in a small core file
   ([agents-core.md](../../agents-core.md)) that `AGENTS.md` `@`-imports — keeping
   `AGENTS.md` itself a thin shell while guaranteeing the core is always in context.
2. **On-demand detail — bare pointers.** The deep convention/plan/reference docs.
   Each is a link plus a **brief topic description** and/or a **trigger**
   ("read before X"). The agent opens them when the trigger fires. This is the only
   tier that rests on link-following (`U2`); keep behavior-critical essence *out* of
   it (promote essence to tier 1).
3. **Inline in `AGENTS.md`.** The project one-liner, Navigation, highest-risk
   pointer, and settled-decisions pointer — short, structural, and fine to read
   natively from the root file.

## Rules

- **Thin pointers, detail in `docs/`.** Never inline long instructions. The full
  text lives in a `docs/` file (`conventions/` for how-we-work; `planning/`,
  `impl/`, `reference/` for the rest); the spine only points.
- **Must-load → `@`-import, not a bare link.** If a session must see it every time
  regardless of whether a trigger fires, put it in the `@`-imported core
  ([agents-core.md](../../agents-core.md)). Do **not** rely on a bare pointer for
  must-load content — link-following is unverified (`U2`).
- **Don't over-import.** `@`-import inlines the *whole* file into every session.
  Import only the small core; importing deep docs would bloat context and defeat
  "thin." Deep docs stay on-demand pointers.
- **Triggers only when there's a clear event.** Add an explicit "when to read"
  (grilling, researching a library, editing `AGENTS.md`) only where a discrete
  moment earns the words. Otherwise a topic description already implies when to
  reach for it.
- **Don't duplicate.** If content already lives elsewhere (e.g. settled decisions
  in the implementation plan), point to it rather than restating — single source
  of truth.
- **Paths.** `@`-imports use Bob's relative/absolute `@`-syntax and must resolve
  (a wrong relative path is silently dropped). On-demand pointers use repo-root-
  relative paths (the agent works from the repo root).

## When adding new guidance

1. Write the detail in the right `docs/` file (create one if needed).
2. Decide the tier: must-load → add to the `@`-imported core
   ([agents-core.md](../../agents-core.md)); on-demand → add a thin pointer there
   or in `AGENTS.md`.
3. Update the relevant `index.md` in the same step.
