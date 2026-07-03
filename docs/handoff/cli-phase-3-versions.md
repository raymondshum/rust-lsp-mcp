# Phase C3 — KI-12 status versions (durable prompt)

**No dependencies; may run parallel with C1 on the fast tier only** (the
podman integration gate serializes).

## Read first
- [cli-frontend.md](../planning/cli-frontend.md) — D10 and the Phase 3 section.
- Reference: [version-introspection-sources.md](../reference/version-introspection-sources.md)
  — the verified mechanism per field.
- [known-issues.md](../impl/known-issues.md) KI-12 / GitHub #115.

## Build
- `status` envelope gains **exactly these fields** (pinned; C2's `version`
  subcommand surfaces them by these names): `server_version` + 
  `multilspy_version` (`importlib.metadata`, per-call) and
  `rust_analyzer_version` (subprocess `<rust_analyzer_bin> --version`
  **once at manager start**, cached on the manager for the process
  lifetime — never per status call). Fields degrade to `null` on capture
  failure, never crash.
- Any new `self._lsp`-adjacent code must respect the KI-9 rule (`_race_teardown`)
  — though this phase should not need new LSP awaits.

## Scope / stop boundary
Server-side only. The CLI surfacing (`rust-lsp version`/`status`) is C2's job.

## Definition of done (QA gate)
Fast tier; integration assertion added to the existing status integration
test (fields present and plausible). **Record step:** close KI-12 in the
register (move to Resolved) and close GitHub #115.

## Adversarial (LOW)
Falsify: version capture failing when the binary path is wrong (must degrade
to `null`/error field, never crash startup); subprocess called per status
request (must be once). 2-round cap.
