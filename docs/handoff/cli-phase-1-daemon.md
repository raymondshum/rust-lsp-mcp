# Phase C1 — Daemon transport (durable prompt)

**Highest-risk phase of the CLI-frontend effort. Analyzer-bound integration
gate.** Prove the once-per-process warm daemon before the CLI client exists.

## Read first
- [cli-frontend.md](../planning/cli-frontend.md) — D2/D3/D4 and the Phase 1
  section. Decisions are frozen; do not relitigate.
- Reference (the implementation template):
  [mcp-streamable-http-daemon.md](../reference/mcp-streamable-http-daemon.md)
  — the exact wiring, incl. the **nest-`_lifespan`** production note.
- `src/rust_lsp_mcp/core.py` (`_lifespan`, the FastMCP construction) and
  `server.py` (`main()`), `settings.py`.

## Build
- New `Settings` fields: `transport` (`stdio`|`streamable-http`, default
  `stdio`) and `http_port` (default 8000) + `env.sample` entries (the
  env-sample honesty test must cover both).
- Conditional FastMCP construction keyed on transport read at import:
  stdio → `lifespan=_lifespan` exactly as today (byte-identical); HTTP →
  no `lifespan=`, plus `host="127.0.0.1"` (hard-coded, never configurable),
  `port`, `stateless_http=True`, `json_response=True`. The `_lifespan`
  definition itself is untouched.
- `main()` branches: stdio → `mcp.run()`; HTTP → `streamable_http_app()`,
  replace `app.router.lifespan_context` with the composed lifespan that nests
  `_lifespan` and delegates to the SDK original, then own `uvicorn.run()`.
- **New daemon-mode integration fixture**: launch the streamable-HTTP server
  in-container + a raw-client loopback driver. Reused by C2 and C5. Lives
  under `tests/`, behind the existing `integration` marker; the gate runs via
  the podman harness described in
  [cli-frontend-effort-handoff.md](cli-frontend-effort-handoff.md)
  "Stack commands" (docker socket is denied — podman, rust:1 image,
  persistent `rlm-*` volumes).

## Scope / stop boundary
No CLI client, no compose/docs changes. Stop once the daemon is proven warm
and stdio is proven unchanged.

## Definition of done (QA gate)
Fast tier (ruff, ty, fast pytest) **plus** the podman integration gate:
(a) two sequential raw-client connections hit the *same warm* analyzer (no
re-index; assert via `status` + timing); (b) **teardown-race test**: `refresh`
concurrent with N in-flight nav calls over separate HTTP requests → nav
returns clean `not_ready` (no hang), manager recovers to `ready`,
`_server_instances` stays empty (observe `_server_instances` via an
in-process variant — run the composed app under uvicorn in a task/thread
inside the test process, as the reference proof did; it is not observable
across processes); (c) stdio regression — existing integration
suite green. Note: HTTP wiring can't be tested by in-process transport
flipping (module built at import) — use the fixture.

## Adversarial (full red-team, HIGH)
Falsify: the once-per-process claim (any path where a session/request start
or teardown touches the process-level manager or doc store); dropped
`_lifespan` duties (preflight warnings absent from `status`, doc store not
cleared on shutdown); `refresh` racing nav (KI-9 rule — re-derive, don't
assume); port/env plumbing (`RLM_*` vs `FASTMCP_*`); accidental non-loopback
bind. Findings → regression tests; 2-round rework cap.
