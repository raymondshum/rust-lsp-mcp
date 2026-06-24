# Grill-me session style (preferences)

_Set: 2026-06-19. Canonical source for how to run a grilling session in this
project; the `AGENTS.md` core points here. The Bob `grill-me` skill
bundles an in-folder copy ([.bob/skills/grill-me/project-style.md](../../.bob/skills/grill-me/project-style.md))
because Bob skills only read their own directory — keep the two in sync (the
in-folder copy is required by `U6`; see KI-7). Do not duplicate this content anywhere
else._

When running a grilling session (stress-testing a plan or design), follow these
preferences in addition to the base `grill-me` skill instructions.

## Flow

- Start with an **overview of the decisions that need to be made**, then align on
  the **high-level vision first**.
- Once the vision is roughly settled, **drill down into each question** one at a
  time.
- **Realign on the vision at the end.**

## Asking questions

- Ask **one question at a time** and wait for feedback before continuing. Multiple
  questions at once is bewildering.
- For each question, **provide a recommended answer**.
- If a question can be answered by **exploring the codebase, do that instead** of
  asking.

## Explaining

- **Explain every concept.** Don't assume prior familiarity.
- Keep explanations **concise**.
- Use **plain language** — no jargon, shorthand, or slang. Speak clearly about the
  options in front of us.

## Format

Use nested bullets:

- Main point / question
    - Major supporting point
        - Minor detail

## After the session — emit the UNVERIFIED inventory

A grilling resolves decisions **in principle**. Before the session is done, write
each decision durably to `docs/` (per [working-style.md](working-style.md)) **and
tag the concrete things it depends on as `UNVERIFIED`** — exact commands, versions,
flags, config syntax, API signatures, install paths. Those tags are the inventory
the verification pass later confirms.

- Resolving "use library X for Y" is not finished until the exact call/signature/
  install path it rests on is recorded as `UNVERIFIED` (or `VERIFIED` if you
  confirmed it live during the grill).
- This is the bridge: **grill (decide) → `UNVERIFIED` inventory → verify.** If
  nothing is tagged, the [verification-pass.md](verification-pass.md) has nothing
  to find — so don't end a grill with decisions but no inventory.
