# Plan: CLI frontend (warm HTTP daemon + `rust-lsp` client + skill)

A phasal plan ([output contract](../conventions/phasal-plan.md)) from the
2026-07-02 grill. Goal: offer the server's tools as local CLI commands — usable
by agents **without MCP tool access** (or subagents that don't inherit it) —
while keeping the MCP-over-stdio server unchanged. Analogy: use it like the
`gh` CLI instead of the API.

**Status: decisions settled 2026-07-02; verification pass DONE 2026-07-02.**
U1–U3 and U5–U8 are `VERIFIED` (cached in `docs/reference/` — see inventory);
U4 is `UNVERIFIED — runtime-only` (measured in the podman integration gate).
**The plan is frozen for the implementation cycle.**

## Why a daemon (the load-bearing rationale)

rust-analyzer's salsa index is in-memory and rebuilt every process start
(plan U5 in [repo-agnostic-and-docker-launch.md](repo-agnostic-and-docker-launch.md)
— REFUTED warm-start; measured ~40 s cold / ~12.6 s warm). Upstream has no
on-disk index persistence (VERIFIED against the rust-analyzer book, 2026-07-02);
the bind mounts already cache everything cacheable (cargo registry/target,
Chroma collection, embedding model). Navigation is iterative and
data-dependent (`find_symbol` → position → `hover`), so a batch one-shot mode
cannot amortize the warm-up either. The only way to give a shell-invoked CLI
acceptable per-call latency is a **long-lived warm server process** the CLI
connects to. Today's `docker exec` "warm-start" path re-indexes per session —
the daemon is the piece that actually delivers a hot analyzer.

## Topology (settled vision)

One warm sidecar container per Rust project (the existing compose service,
repurposed): its entrypoint runs the server with a **streamable-HTTP transport
bound to `127.0.0.1` inside the container, never published**. The CLI is a thin
client that always talks to loopback; where the agent sits only changes the
prefix:

- Host shell: `docker exec rust-lsp-mcp rust-lsp <cmd> …` (or `podman exec`).
- Shell inside the daemon's environment (e.g. this repo's dev container running
  the daemon directly): plain `rust-lsp <cmd> …`, no prefix.

MCP-over-stdio (`docker run -i --rm …`) stays untouched as the MCP-client path.

## Settled decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Warm daemon + thin CLI client; no per-call server spawn.** | See "Why a daemon". Rejected: one-shot in-process CLI (12–40 s/call), batch mode (navigation is data-dependent), generic MCP CLI clients (Node dep, JSON-blob ergonomics, no `--wait`/exit codes), stdio-session broker (daemon with more moving parts), bind-mount index persistence (unsupported upstream). |
| D2 | **Process-level analyzer/doc-store init.** Under SDK 1.12.4 streamable-HTTP, the FastMCP `lifespan=` runs **per MCP session** (per request when stateless) — as-is, every CLI call would cold-spawn and tear down its own analyzer, and sessions clobber the module-level singletons. Fix: build the app via `mcp.streamable_http_app()`, wrap it in an **outer ASGI lifespan** that runs `analyzer_lifespan` + `init_doc_store_background` once per process around `session_manager.run()`; the per-session MCP lifespan becomes a no-op in HTTP mode. | Verified against installed SDK source (`mcp/server/fastmcp/server.py:172-174,956`, `lowlevel/server.py:575`, `streamable_http_manager.py:146-269`). Without this the design is inert (adversarial review, Findings 1–2). Stdio keeps today's lifespan wiring. |
| D3 | **Transport switch by env var: `RLM_TRANSPORT=stdio\|streamable-http`, default `stdio`.** Port via `RLM_HTTP_PORT` (default 8000); host **hard-coded `127.0.0.1`**, not configurable. Server mode `stateless_http=True, json_response=True`. `RLM_*` values plumbed explicitly into FastMCP settings (it reads `FASTMCP_*`/constructor, not `RLM_*`). | Daemon is compose-launched → env is the natural knob; entry point stays argument-free; nobody can bind `0.0.0.0` in the untrusted-code container. Stateless is safe once D2 holds and leaks no session state (kwargs + no-leak VERIFIED, see U2). |
| D4 | **Security posture: loopback listener inside the untrusted-code container is an accepted, documented change.** Never a `ports:` mapping in compose; README security section gets a note (processes in the container — incl. `build.rs`/proc-macro code rust-analyzer runs — can reach the read-only tools on loopback). Works under `network_mode: none` (loopback ≠ egress). | Reopens the settled "stdio transport, single host" decision **legitimately**: new info = agents without MCP access. Single-host intent preserved (no network exposure). Recorded in implementation-plan.md "Settled architecture" as an amendment. |
| D5 | **CLI = separate import-light top-level package `rust_lsp_cli`** in the same distribution; console script `rust-lsp`. Imports only `argparse`/`json` + the `mcp` client — **never** `rust_lsp_mcp` (whose `__init__` pulls in the server → chromadb import + a second Chroma client risk). Zero new dependencies. | Per-call latency = Python startup + handshake, not chromadb import; avoids cross-process Chroma hazard by construction. |
| D6 | **Static hand-written subcommands + a fast parity test** (test imports the server in-process, compares `mcp.list_tools()` names against the CLI's command table, with documented exclusions). | Runtime `list_tools()` generation would make `--help` require a live daemon. Parity test gives the same drift protection in CI. |
| D7 | **Subcommand surface: 9 tools + version.** `find-symbol`, `goto-definition`, `find-references`, `hover`, `document-symbols`, `search-docs`, `status`, `refresh`, `validate-file-path`, plus `version`. Excluded: `probe` (internal gate demo), `analyzer_status` (superseded by `status`). `refresh` is included but the skill warns it tears down the shared daemon's index for all users. | Parity where it matters; no noise commands. |
| D8 | **Exit codes keyed to actionability:** `0` = `ok` **and** `not_found` (an answer, not a failure — matches the skill's "empty ≠ error" doctrine and `gh` convention); `1` = tool `error` envelope; `2` = `not_ready` (incl. `--wait` timeout); `3` = transport failure (daemon unreachable / handshake) with a "start the daemon" hint on stderr. Envelope JSON (structuredContent) pretty-printed to stdout; all diagnostics to stderr. No human-text format for now (agents parse JSON). | `not_found`→nonzero would re-introduce KI-11's false-negative at the shell layer for `set -e` agents. |
| D9 | **`--wait SECS` opt-in readiness handling:** polls `status` (2 s interval) until `ready` or timeout, then runs the command. Skill teaches `--wait 180` for the first call after container start. Daemon discovery via `RLM_CLI_URL` (default `http://127.0.0.1:8000/mcp`). | Folds the failure-prone bash poll loop into the client once, instead of into every agent. Opt-in keeps un-flagged calls non-blocking. |
| D10 | **KI-12 fixed server-side, not CLI-side:** `status` envelope gains server/rust-analyzer/multilspy versions; `rust-lsp version` prints client version and surfaces the daemon's. Closes #115 for MCP clients and CLI alike. | A CLI-only `version` would leave the MCP surface blind — scope confusion flagged in review. |
| D11 | **One capability-branched skill, not two.** Extend `.claude/skills/rust-code-navigation/SKILL.md` with a "How to invoke" section: MCP tools if available, else the CLI (runtime auto-detect docker/podman as in `scripts/prime-cache.sh`, container-name parameter, `--wait`, exit-code table, refresh warning). Triggers unchanged. | A duplicate-trigger CLI skill would misfire (both load or wrong one fires). |
| D12 | **Compose: repurpose the existing warm service as the daemon** (entrypoint: server with `RLM_TRANSPORT=streamable-http`), same treatment for `-isolated`. **Chroma single-writer mandate documented:** exactly one process opens `/data/chroma`; while the daemon is up, don't run stdio sessions in that container or ephemeral `docker run` sessions against the same volume (use a differently-named volume if both are needed concurrently); don't run both compose services at once. | The service's warm-start promise was already false (U5). One container, one volume, one writer. Guarded-invariant test only covers intra-process; cross-process SQLite writers risk corruption. |
| D13 | **Out of scope:** container-free production mode (pip-installed CLI + host rust-analyzer — violates "host stays clean"); publishing the HTTP port for external MCP clients. **Recorded as future work, not scheduled:** a tiny stdio↔loopback-HTTP bridge subcommand (`rust-lsp mcp-proxy`) that would let MCP stdio clients share the warm daemon (would also retire the residual chroma-concurrency footgun by making the daemon the only server process anyone needs). | Keep scope tight; bridge is additive and separable. |

## Verified inventory (2026-07-02 verification pass)

- **U1 — VERIFIED, with a CORRECTION** (cached:
  [mcp-streamable-http-daemon.md](../reference/mcp-streamable-http-daemon.md)):
  once-per-process init works, but **not** by wrapping `mcp.run()` —
  `run_streamable_http_async()` builds its own app/uvicorn internally with no
  injection hook. The proven wiring: `app = mcp.streamable_http_app()`, then
  **replace `app.router.lifespan_context`** with a composed lifespan that runs
  process startup, delegates to the SDK's original lifespan (which runs
  `session_manager.run()` — mandatory), and yields; **we own the
  `uvicorn.run()` call**. HTTP mode passes no `lifespan=` to FastMCP; stdio
  keeps today's wiring. Live-proven: two separate client connections saw
  `init_count == 1` and the same shared-object id.
- **U2 — VERIFIED** (same reference entry): `stateless_http`/`json_response`
  are 1.12.4 constructor kwargs; the stateless path creates a fresh transport
  per request, never populates `_server_instances`, and terminates each
  transport — confirmed empty after repeated one-shot calls. Note: benign
  per-request `ClosedResourceError` log noise in stateless mode (harmless in
  1.12.4; expect it in daemon logs).
- **U3 — VERIFIED live** (cached:
  [podman-exec-loopback.md](../reference/podman-exec-loopback.md)): under
  `podman run --network none`, `lo` exists and a 127.0.0.1 HTTP round-trip
  succeeds in-container (200 OK). The `-isolated` variant keeps working.
- **U4 — UNVERIFIED — runtime-only:** per-call CLI latency budget (Python
  startup + `mcp` client import + handshake + call ≤ ~1–2 s against a warm
  daemon). Measure in the podman integration gate (Phase 2 DoD); record
  honestly in docs.
- **U5 — VERIFIED end-to-end** (cached:
  [hatchling-two-packages.md](../reference/hatchling-two-packages.md)):
  append `"src/rust_lsp_cli"` to the existing explicit `packages` list + a
  second `[project.scripts]` entry; proven build → install → run. No
  auto-detection gotcha (the project already declares `packages`).
- **U6 — VERIFIED** (cached:
  [version-introspection-sources.md](../reference/version-introspection-sources.md)):
  multilspy discards the LSP `InitializeResult` (no `serverInfo` access) —
  use `<RLM_RUST_ANALYZER_BIN> --version` once at manager start (cached for
  process lifetime; format `rust-analyzer <semver> (<sha> <date>)`), and
  `importlib.metadata.version()` for multilspy + server versions.
- **U7 — VERIFIED live** (same entry as U3): `podman exec` (non-TTY) keeps
  stdout/stderr separated, propagates exact exit codes, injects no `\r`, and
  `-i` forwards stdin — full parity with docker exec for the skill's
  prefix-swap pattern.
- **U8 — VERIFIED** (same entry as U1): with default `transport_security=None`
  the SDK skips Host/Origin validation entirely
  (`enable_dns_rebinding_protection=False`), so loopback CLI requests need no
  `allowed_hosts` config. Gotcha: POST `Content-Type: application/json` is
  validated regardless. Optional hardening documented in the reference entry.

## Phasal plan (risk-first)

### Phase 1 — Daemon transport (HIGHEST RISK)

- **Scope:** D2 process-level wiring; D3 transport switch/port/stateless
  config in `settings.py` + `server.py`/`core.py`; loopback hard-code; keep
  stdio path byte-identical in behavior.
- **Depends on:** none (verification pass done; U1's corrected wiring is the
  implementation template).
- **Parallelizable:** with Phase 3 (disjoint files).
- **File ownership:** `src/rust_lsp_mcp/server.py`, `core.py`, `settings.py`,
  `env.sample`.
- **DoD (QA gate):** fast tier (ruff, ty, fast pytest) **plus** local podman
  gate: start daemon in the container, drive two sequential raw-client calls —
  second call must hit the *same warm* analyzer (no re-index; assert via
  `status` state + timing); concurrent-call smoke against one manager;
  stdio regression (existing integration suite green).
- **Adversarial intensity:** HIGH — red-team the lifespan rewiring (session
  teardown must not touch the process-level manager), singleton lifetimes,
  and `refresh` during concurrent CLI calls (KI-9 `_race_teardown` rule holds
  once there is exactly one manager — re-derive, don't assume).

### Phase 2 — `rust-lsp` CLI client (MEDIUM RISK)

- **Scope:** new `src/rust_lsp_cli/` (D5); subcommands (D7), exit codes (D8),
  `--wait`/`RLM_CLI_URL` (D9), `version` client half (D10); parity test (D6);
  pyproject packaging (U5).
- **Depends on:** Phase 1.
- **File ownership:** `src/rust_lsp_cli/**`, `pyproject.toml`,
  `tests/test_cli_*.py`.
- **DoD:** fast tier incl. parity test + exit-code/`--wait` unit tests (mocked
  transport) — CI-safe; podman gate: `podman exec` into the daemon container,
  full subcommand sweep against ripgrep fixture, latency measurement (U4).
- **Adversarial intensity:** MEDIUM — exit-code taxonomy edge cases, daemon-down
  behavior, stdout purity (JSON only).

### Phase 3 — KI-12 status versions (LOW RISK, parallel with Phase 1)

- **Scope:** version fields in the `status` envelope (D10, U6); closes #115;
  update known-issues register.
- **File ownership:** `src/rust_lsp_mcp/tools/status.py`, `analyzer.py`
  (version capture), related fast tests, `docs/impl/known-issues.md`.
- **DoD:** fast tier; integration assertion in the existing status test.
- **Adversarial intensity:** LOW.

### Phase 4 — Deployment + docs (LOW RISK)

- **Scope:** compose entrypoint flip + `-isolated` variant + no-`ports:`
  warning (D12, D4); README third launch story (daemon+CLI) reconciled with
  the two existing ones; `docs/guide/configuration.md` (`RLM_TRANSPORT`,
  `RLM_HTTP_PORT`, `RLM_CLI_URL`); new `docs/guide/` CLI page; security-note
  addition; implementation-plan.md "Settled architecture" amendment (D4);
  `index.md` updates in-step.
- **Depends on:** Phases 1–2.
- **File ownership:** `docker-compose.yml`, `README.md`, `docs/guide/**`,
  `docs/planning/implementation-plan.md`, index files.
- **DoD:** fast tier; docs verification per
  [documentation-writing.md](../conventions/documentation-writing.md); manual
  compose up → exec smoke.
- **Adversarial intensity:** LOW–MEDIUM (three launch stories must not
  contradict; chroma single-writer mandate stated everywhere it matters).

### Phase 5 — Skill revision (LOW RISK)

- **Scope:** capability-branched "How to invoke" section in
  `rust-code-navigation/SKILL.md` (D11): runtime auto-detect, container name,
  `--wait`, exit-code table, `refresh` warning, `not_found`-is-an-answer note.
- **Depends on:** Phase 2 (real command syntax).
- **File ownership:** `.claude/skills/rust-code-navigation/SKILL.md`.
- **DoD:** dry-run the skill's commands verbatim in the podman gate.
- **Adversarial intensity:** LOW.

## QA-gate summary (CI stays light)

CI: ruff + ty + fast pytest only (incl. D6 parity test, CLI unit tests with
mocked transport). The daemon end-to-end, latency measurement, `network_mode:
none` proof, and exec-prefix dry-runs all live in the **local podman
integration gate** (`-m integration`), never CI.
