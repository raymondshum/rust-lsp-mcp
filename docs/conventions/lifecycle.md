# Delivery lifecycle (the end-to-end standard)

The standard path a feature or code change travels, from idea to shipped and
documented. Each stage hands a defined artifact to the next. Most stages already
have their own convention doc; this page is the spine that connects them.

```
Grill ──▶ Plan ──▶ Verify ──▶ Implement ──▶ Document
```

## The stages

| Stage | Convention | Takes in | Produces |
|-------|-----------|----------|----------|
| **Grill** | [grill-me.md](grill-me.md) | a rough design or proposal | settled decisions + an `UNVERIFIED` inventory (claims still to confirm) |
| **Plan** | [phasal-plan.md](phasal-plan.md) | settled decisions | a **phasal plan** shaped for the implementation cycle (phases, dependencies, file-ownership partitions, definition-of-done, adversarial intensity) + a progress tracker |
| **Verify** | [verification-pass.md](verification-pass.md) | the `UNVERIFIED` inventory | each item confirmed against Context7/source and flipped to `VERIFIED`, with the residue cached |
| **Implement** | [implementation-cycle.md](implementation-cycle.md) | the verified phasal plan | the phases built one at a time — each `build → review → QA → adversarial → PR → record` |
| **Document** | [documentation-writing.md](documentation-writing.md) | the shipped behavior | human-facing docs (README, `docs/guide/`) written, reviewed, linked |

## How the handoffs fit

- **Grill → Plan.** Grilling settles the open questions and leaves a list of
  claims marked `UNVERIFIED`. The plan is built on the *decisions*; the
  `UNVERIFIED` list rides along to the next stage.
- **Plan → Verify.** A plan must be **phasal** to be implementable by the cycle —
  see [phasal-plan.md](phasal-plan.md) for the required shape. Before building,
  the verification pass confirms the plan's `UNVERIFIED` claims so the build
  isn't standing on guesses.
- **Verify → Implement.** Once the load-bearing claims are `VERIFIED`, the
  [implementation cycle](implementation-cycle.md) advances the plan **one phase
  per pass**, then stops for human review.
- **Implement → Document.** When the behavior is shipped, the
  [documentation-writing](documentation-writing.md) methodology grounds, writes,
  and verifies the human-facing docs.

## Cross-cutting (every stage)

- [research-policy.md](research-policy.md) — where facts come from (Context7-first;
  read source when docs are silent). Every stage consumes this.
- [caching.md](caching.md) — where learned patterns land (`docs/reference/`,
  memory) so they aren't re-derived.
- [working-style.md](working-style.md) — how to propose approaches and decide.
- [known issues](../impl/known-issues.md) — the living register of open design /
  documentation issues. Review it at the start of a grill/plan session, at each
  phase's record step, and when editing a module an open issue names.

## Scale to the change

Not every change needs all five stages. A one-line fix or a typo correction goes
straight to a small implementation pass. The full lifecycle earns its cost when
the change is **substantial** (new feature, risky subsystem), **uncertain** (open
design questions), or **hard to reverse**. Use judgement; the stages are a
checklist of what *can* apply, not a toll gate on every commit.
