# CLAUDE.md layout convention

Read this before adding to or restructuring `CLAUDE.md`.

`CLAUDE.md` is loaded into context at the start of **every** session, so it must
stay thin. It holds **pointers, not long instructions**.

## Rules

- **Thin pointers, detail in `docs/`.** Each entry is a link plus a **brief topic
  description** and/or a **trigger** ("read before X"). The full instructions live
  in a `docs/` file (`conventions/` for how-we-work; `planning/`, `impl/`,
  `reference/` for the rest).
- **Triggers only when there's a clear event.** Add an explicit "when to read"
  (e.g. grilling, researching a library, editing `CLAUDE.md`) only where a discrete
  moment makes it earn the words. Otherwise a topic description is enough — it
  already implies when to reach for it.
- **Inline essence sparingly.** Keep a one-sentence essence inline only for
  behavior-critical guidance (e.g. Context7-first) that a session must see even if it
  never follows the pointer. Everything else moves fully.
- **Don't duplicate.** If content already lives elsewhere (e.g. settled decisions in
  the implementation plan), point to it rather than restating — single source of
  truth.
- **Keep inline only:** the project one-liner, Navigation, and short hard
  Constraints. Prose-heavy sections belong in `docs/`.

## When adding new guidance

1. Write the detail in the right `docs/` file (create one if needed).
2. Add a thin pointer in `CLAUDE.md` (description and/or trigger).
3. Update the relevant `index.md` in the same step.
