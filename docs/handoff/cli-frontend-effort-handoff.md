# CLI-frontend effort — session handoff

Seed for resuming the **CLI-frontend build** (warm loopback streamable-HTTP
daemon + import-light `rust-lsp` CLI + capability-branched skill) in a fresh
session. Written 2026-07-02, immediately after the planning close-out merged
(**PR #118**, merge `8ae29c8`). Grounded against `main` at that commit.

## Where we are

- **Planning is 100 % done and merged.** The full lifecycle ran on 2026-07-02:
  grill (13 settled decisions) → verification pass (U1–U3, U5–U8 `VERIFIED`,
  cached in `docs/reference/`; U4 latency = runtime-only residue) →
  two-reviewer adversarial plan review → all amendments applied. Plan of
  record: [cli-frontend.md](../planning/cli-frontend.md) — **frozen; do not
  relitigate.**
- **Governance landed:** the "stdio transport, single host" settled decision
  was formally amended in
  [implementation-plan.md](../planning/implementation-plan.md) (stdio default +
  optional loopback-only HTTP, never published). **KI-13** (ChromaDB
  cross-process single-writer hazard) filed in the register.
- **Dispatcher machinery exists:** [progress-cli.md](progress-cli.md) tracker
  (gate-zero `not-run`), five durable prompts `cli-phase-*.md`,
  [continue.md](continue.md) efforts table routes to this effort.
- **No implementation code exists yet** (verified: no `src/rust_lsp_cli/`, no
  `transport` field in `settings.py`, GitHub #115 open, all tracker rows
  `not-started`).

## What's left (grounded 2026-07-02)

| Item | Ref | Status |
|------|-----|--------|
| Gate-zero: adversarial pass over the five `cli-phase-*.md` prompts + `progress-cli.md` + the continue.md routing change | [progress-cli.md](progress-cli.md) | `not-run` — must pass before C1 |
| C1 — daemon transport (D2 wiring, Settings fields, daemon integration fixture) | [cli-phase-1-daemon.md](cli-phase-1-daemon.md) | not-started; HIGHEST RISK, go first |
| C2 — `rust-lsp` CLI client (needs C1's fixture) | [cli-phase-2-cli.md](cli-phase-2-cli.md) | not-started |
| C3 — KI-12 status versions (closes #115) | [cli-phase-3-versions.md](cli-phase-3-versions.md) | not-started; ∥ C1 fast-tier only |
| C4 — compose flip + docs + migration note (needs C1+C2) | [cli-phase-4-deploy-docs.md](cli-phase-4-deploy-docs.md) | not-started; ∥ C5 |
| C5 — capability-branched skill revision (needs C2) | [cli-phase-5-skill.md](cli-phase-5-skill.md) | not-started; ∥ C4 |

**Recommended sequencing** (matches the tracker's dependency graph):
gate-zero → C1 (∥ C3 on the fast tier; integration gates serialize) → C2 →
C4 ∥ C5. One PR per phase to `main`.

## Model & orchestration preferences (restated by the owner 2026-07-02)

- **Fable 5 on the main thread**: orchestrator, decision-maker, and last stop
  for quality. Keep the main thread lean — delegate.
- **Opus subagents for logic/judgment work** (review, adversarial, tricky
  design); **Sonnet subagents for implementation and test-writing**.
  (Fallback after ~2026-07-08 if Fable unavailable: Opus 4.8 orchestrates.)
- **Standing directive for this effort (owner, 2026-07-02):** run **all phases
  to completion fully automated** — the orchestrator opens **and merges** the
  per-phase PRs itself (CI must be green before merge; merge-commit style, as
  the repo history uses). This consciously overrides continue.md's
  stop-at-phase-boundary / await-human-merge for this effort. Everything else
  in the cycle is unchanged: per phase, build → **review subagent** → QA
  (fast tier **and** integration gate — both written *and run*) →
  **adversarial subagent** → PR + record. Rework caps: 2 rounds per gate;
  if exceeded, set `blocked` and stop for the human (automation does not
  override the blocked rule).
- Research policy: Context7-first; adversarial/QA findings become regression
  tests before the re-fix. Orchestrator is sole writer of the tracker.

## Stack commands

Fast tier (CI-equivalent) — every unit:
```
uv sync
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run --frozen pytest -m "not integration"
uv run --frozen pytest tests/test_env_sample_honesty.py -v
```
Integration gate — local only, never CI (live rust-analyzer + ripgrep fixture;
run via the **podman harness** — docker socket is denied in the dev container;
rust:1 image + persistent `rlm-*` volumes, ~10 min warm):
```
uv run --frozen pytest -m integration
```

## Key file references

| What | Where |
|------|-------|
| Plan of record (frozen decisions D1–D13, phases, DoDs) | [../planning/cli-frontend.md](../planning/cli-frontend.md) |
| Effort tracker (orchestrator-owned) | [progress-cli.md](progress-cli.md) |
| Dispatcher (efforts table) | [continue.md](continue.md) |
| **The D2 wiring template** (nest `_lifespan`; own `uvicorn.run`) | [../reference/mcp-streamable-http-daemon.md](../reference/mcp-streamable-http-daemon.md) |
| Packaging proof (two packages, one wheel) | [../reference/hatchling-two-packages.md](../reference/hatchling-two-packages.md) |
| KI-12 version-field mechanisms | [../reference/version-introspection-sources.md](../reference/version-introspection-sources.md) |
| podman loopback/exec facts | [../reference/podman-exec-loopback.md](../reference/podman-exec-loopback.md) |
| Server seams C1 touches | `src/rust_lsp_mcp/core.py` (`_lifespan`, FastMCP construction), `server.py`, `settings.py` |
| Config to keep in lockstep | `pyproject.toml` (C2 packaging), `env.sample` (+ honesty test), `.github/workflows` (CI stays fast-tier) |
| Register (KI-13 + C3/C4 record duties) | [../impl/known-issues.md](../impl/known-issues.md) |

## Gotchas / do NOT retry

- **Do not relitigate the plan.** D1–D13 are frozen; alternatives (one-shot
  CLI, batch mode, generic MCP CLI clients, bind-mount index persistence —
  rust-analyzer has no on-disk salsa cache) were investigated and rejected
  with recorded rationale.
- **`FastMCP(lifespan=)` runs per session — per request when stateless — on
  mcp 1.12.4.** Never hang the analyzer off it in HTTP mode. The only proven
  wiring is the reference entry's: replace `app.router.lifespan_context`,
  nest the existing `core._lifespan` (preflight + doc-store duties), delegate
  to the SDK lifespan, own `uvicorn.run()`. `mcp.run("streamable-http")` has
  no injection hook — don't try it.
- **CLI must never import `rust_lsp_mcp`** (chromadb import cost + a second
  Chroma client). Parse `content[0].text` as the envelope (authoritative);
  structuredContent is fallback only.
- **Exit codes:** `not_found` → **0** (it's an answer — KI-11 doctrine);
  `--wait` treats connection-refused as retriable *inside the window*
  (daemon boot goes refused → not_ready → ready).
- **One process per Chroma path (KI-13):** the daemon owns `/data/chroma`;
  never run a second stdio *server* in/against the daemon container's volume.
  The CLI is safe by construction.
- **Never add a `ports:` mapping** for the daemon (unauthenticated MCP
  endpoint in a container that executes untrusted `build.rs` code).
- **Expect benign per-request `ClosedResourceError` tracebacks** in daemon
  logs under stateless mode (SDK 1.12.4) — harmless; don't chase them.
- **HTTP wiring can't be tested by in-process transport flipping** (the `mcp`
  singleton is built at import) — use C1's daemon fixture in the podman gate.
- **`uv add`/lockfile changes are orchestrator-only** (none expected — zero
  new dependencies); parallel worktree `uv add` corrupts the lock.
- **rust-analyzer `--version` once at manager start, cached** — never per
  `status` call; multilspy discards LSP `serverInfo` (dead end, verified).
- Integration tests allow generous timeouts (cold ripgrep index up to 300 s);
  the analyzer + podman gate is **one serialized resource across efforts**.

## How to start

1. Read this doc, then [cli-frontend.md](../planning/cli-frontend.md) and
   [progress-cli.md](progress-cli.md); skim
   [known-issues.md](../impl/known-issues.md) open entries.
2. Kickoff: **"Continue the build per docs/handoff/continue.md."** The efforts
   table routes to this effort; first run executes **gate-zero** over the CLI
   handoff artifacts, then (per the standing directive above) proceed through
   C1 → C5 without pausing, merging each phase PR after green CI.
3. Honor the preferences + gotchas above; record every state transition in
   the tracker.
