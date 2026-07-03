# Phase C2 — `rust-lsp` CLI client (durable prompt)

**Depends on C1** (daemon + its integration fixture).

## Read first
- [cli-frontend.md](../planning/cli-frontend.md) — D5–D10 and the Phase 2
  section.
- Reference: [hatchling-two-packages.md](../reference/hatchling-two-packages.md)
  (packaging), [mcp-streamable-http-daemon.md](../reference/mcp-streamable-http-daemon.md)
  (parse `content[0].text` as authoritative; `/mcp` path),
  [version-introspection-sources.md](../reference/version-introspection-sources.md)
  (client half of `version`).

## Build
- New top-level package `src/rust_lsp_cli/` — **import-light**: `argparse`,
  `json`, `os`, the `mcp` client only. It must NEVER import `rust_lsp_mcp`
  (heavy imports + a second Chroma client). Console script `rust-lsp` via a
  one-line `packages` append + `[project.scripts]` entry in `pyproject.toml`.
- Subcommands (D7): `find-symbol`, `goto-definition`, `find-references`,
  `hover`, `document-symbols`, `search-docs`, `status`, `refresh`,
  `validate-file-path`, `version`. Static argparse; positional ergonomics for
  the four position-taking tools.
- Exit codes (D8): 0 = `ok`/`not_found`; 1 = `error` envelope; 2 = `not_ready`
  (incl. `--wait` expiring reachable-but-not-ready); 3 = never reachable
  (incl. `--wait` expiring without a connection). Envelope from
  `content[0].text` to stdout; diagnostics to stderr.
- `--wait SECS` (D9): poll `status` every 2 s; connection-refused/handshake
  errors are **retriable inside the window**. URL default
  `http://127.0.0.1:${RLM_HTTP_PORT:-8000}/mcp`, `RLM_CLI_URL` override.
- `version` (D10): client version via `importlib.metadata`; daemon fields
  `null` + stderr note + **exit 0** when the daemon is down.
- **Parity test** (D6): fast test imports the server in-process, compares
  `mcp.list_tools()` names against the CLI's command table with documented
  exclusions (`probe`, `analyzer_status`).

## Scope / stop boundary
No compose/docs/skill changes. Stop once the full subcommand sweep passes
against the C1 daemon fixture.

## Definition of done (QA gate)
Fast tier incl. parity test + exit-code/`--wait` unit tests with a mocked
transport (CI-safe). Podman gate: `podman exec` full subcommand sweep against
the ripgrep fixture via C1's daemon fixture; **measure U4 per-call latency**
(budget ~1–2 s warm) and record the number in the tracker + docs.

## Adversarial (MEDIUM)
Falsify: exit-code edges (`not_found` vs `error`, `--wait` boot sequence
refused→not_ready→ready, timeout classification); stdout purity (JSON only —
no stray prints); daemon-down behavior of every subcommand; import-light rule
(assert `rust_lsp_mcp` absent from `sys.modules` after CLI import); `refresh`
via CLI against the shared daemon. Findings → regression tests; 2-round cap.
