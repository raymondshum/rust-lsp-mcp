# Phase C4 — Deployment + docs (durable prompt)

**Depends on C1 + C2. May run parallel with C5 (disjoint files).**

## Read first
- [cli-frontend.md](../planning/cli-frontend.md) — D4, D12, D13 and the
  Phase 4 section.
- [known-issues.md](../impl/known-issues.md) KI-13 (chroma single-writer).
- Conventions: [documentation-writing.md](../conventions/documentation-writing.md);
  CLAUDE.md navigation rule (index.md updates in-step).

## Build
- `docker-compose.yml`: entrypoint flip to the daemon
  (`RLM_TRANSPORT=streamable-http`) on both services; `restart:
  unless-stopped`; **no `ports:` mapping ever** (comment-warn); **rewrite the
  header wholesale** (the old exec-warm-start story is superseded and, post-
  flip, dangerous).
- **Migration note** for existing `docker exec … rust-lsp-mcp` warm-start
  users: that pattern now runs a second stdio *server* in the daemon container
  → the KI-13 Chroma double-writer footgun. Point them to
  `docker exec … rust-lsp <cmd>` or a separate volume.
- README: third launch story (daemon + CLI) reconciled with `docker run -i`
  (MCP default) and the retired exec-warm-start; security-section note (D4:
  loopback listener in the untrusted-code container, read-only tools, never
  published).
- `docs/guide/configuration.md`: `RLM_TRANSPORT`, `RLM_HTTP_PORT`;
  **`RLM_CLI_URL` documented here/README, NOT env.sample** (CLI var, not a
  server Settings field). New `docs/guide/` CLI page: subcommands, exit codes,
  `--wait`, `rust-lsp status --wait` as the health check, daemon log story
  (stderr → container logs; benign per-request `ClosedResourceError` noise,
  SDK 1.12.4).
- Index files updated in the same step.

## Scope / stop boundary
No code changes. Stop when the three launch stories are consistent and the
compose smoke passes.

## Definition of done (QA gate)
Fast tier; docs verification per documentation-writing.md; manual compose
up → `exec rust-lsp status --wait` smoke. **Record step:** file the two new
known-issues entries (refresh-nukes-shared-index caveat; ClosedResourceError
log-noise wart) and note KI-13's mandate is now documented.

## Adversarial (LOW–MEDIUM)
Falsify: any doc page still teaching the old exec-stdio warm-start; the three
launch stories contradicting; the single-writer mandate missing anywhere it
matters; a `ports:` example sneaking in. 2-round cap.
