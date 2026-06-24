# Plan-verification pass

How to walk a plan's `UNVERIFIED` items and flip them to `VERIFIED` before build.
Run this when asked to "verify the plan", do a "verification pass", "flip
UNVERIFIED to VERIFIED", or "audit the references" — typically once a design is
settled and before handing the plan to implementation. This is a checking pass,
**not** a design pass: don't reopen settled decisions (see the plan's "Settled
architecture") without new information.

Builds on [research-policy.md](research-policy.md) (how to confirm a detail) and
[caching.md](caching.md) (where the finding is recorded). This doc only adds the
orchestration; it does not restate those policies.

## Procedure

1. **Inventory.** Collect *every* `UNVERIFIED` item across the plan and the
   `docs/reference/` notes — exact commands, versions, flags, config syntax, API
   signatures, install paths. One checklist; nothing skipped.

2. **Confirm each item** per [research-policy.md](research-policy.md): Context7
   `resolve-library-id` → `query-docs` first; **when docs are silent, read the
   source** (`pip download <pkg> --no-deps`, unpack/inspect the wheel) rather than
   speculate. Check `docs/reference/` first — a prior pass may already cover it.

3. **Record the outcome.** For each item, either:
   - flip it to `VERIFIED (date)` with a cached `docs/reference/` entry (stamped
     library + version + date, per [caching.md](caching.md)); or
   - if the doc/source differs from what was assumed, **record the corrected
     detail** as a `CORRECTION` in both the plan and the reference entry — don't
     silently "verify" a wrong assumption.

4. **Leave the irreducible residue `UNVERIFIED`, but annotate it.** Some items can
   only be confirmed at build or against a live system (runtime behavior), or are
   intentionally deferred (lowest-priority work). Mark these
   `UNVERIFIED — runtime-only` / `UNVERIFIED — intentionally deferred` inline so a
   later reader knows they were considered in the pass, not missed.

5. **Keep indexes current.** New `docs/reference/` entries get a line in
   `docs/reference/index.md`; update the plan's status header and the planning
   handoff so a fresh session sees the pass is done (navigation rule in AGENTS.md).

## Definition of done

No un-annotated `UNVERIFIED` remains: every one is either `VERIFIED` (with a cached
reference) or annotated runtime-only / deferred. A final `grep -n UNVERIFIED` over
the plan and reference docs is the cheap self-check.
