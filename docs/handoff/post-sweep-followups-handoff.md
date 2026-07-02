# Handoff — post-defect-sweep hardening follow-ups

Seed for a fresh session that drives the follow-up work surfaced while resolving the 2026-07-01 defect
sweep. Produced by the [session-handoff skill](../../.bob/skills/session-handoff/SKILL.md). **Read this
first**, then the anchors below. Kickoff: "resolve the post-sweep follow-ups" (see the paste-ready prompt the
producing session surfaced).

- **Tracker:** GitHub issues **#87–#92** (label `followup-2026-07-02`).
- **Living register:** [docs/impl/known-issues.md](../impl/known-issues.md) — **KI-9** (= #87) and **KI-8**
  (a separate open decision) live here.
- **Origin:** these items were flagged (out of scope) during the defect-sweep resolution — see
  [defect-sweep-resolution-handoff.md](defect-sweep-resolution-handoff.md) and the audit
  [docs/security/defect-sweep-2026-07-01.md](../security/defect-sweep-2026-07-01.md). That effort is
  **complete** (all 28 findings DS-01…DS-28 resolved, issues #45–#63 closed).

## Where we are

- The defect sweep shipped in **11 units / PRs #65–#85** to `main`, each cherry-picked to `bob_prototype`,
  each with a regression test and both gate tiers (fast + local `-m integration`). Nothing from DS-01…DS-28
  remains.
- These follow-ups are **new work**, not sweep leftovers: five are actionable fixes, one (#92) is a decision.
  None is started. All verified genuinely-open as of this handoff (grounded below).
- Branch state: `main` is the source of truth; `bob_prototype` is in lockstep via cherry-pick. `main` has
  never absorbed `bob_prototype`.

## What's left (grounded)

Each row was verified against the current tree (not recalled): grep/inspection results noted.

| # | Sev | Where | Status / grounding |
|---|-----|-------|--------------------|
| #87 (FU-1, KI-9) | High | `analyzer.py` `request_*` delegates + multilspy 0.0.15 `server.py` | Open. Verified: no `wait_for`/timeout wraps any delegate await. A nav call awaiting `self._lsp.request_*` hangs forever if `refresh` drains a wedged analyzer (multilspy never fails pending requests on `stop()`). Analyzer-side analog of DS-12. **Highest-value.** |
| #88 (FU-2) | Med | CI (`.github/workflows/`), `Dockerfile`, `tests/test_infra_scripts.py` | Open. Verified: CI builds **no** image; **no** test builds the image. DS-16/25/26 (production-image fixes) are guarded only by static text assertions. Add an image-build smoke test (git-as-root on bind mount; `--network none` startup; `status` non-null commit). |
| #89 (FU-3) | Low | `doc_chunking.py` `_SETEXT_H1_RE`/`_SETEXT_H2_RE` + thematic/table detection | Open. Verified: setext/thematic regexes still anchor at column 0 (`^=+`, `^-+`). DS-23 fixed only `_HEADER_RE`/`_FENCE_RE`. Also the DS-11 blank-line-in-frontmatter trade-off. Low impact; watch the two non-misfire tests. |
| #90 (FU-4) | Low | `tests/test_lifecycle_races.py`, `tests/test_doc_store.py` | Open. Verified: 0 `@pytest.mark.integration` in `test_lifecycle_races.py`. The DS-03/04/21 and DS-12 races are covered only by fast-tier fakes; add a couple of `-m integration` tests against the live analyzer / real ChromaDB to catch fake-vs-real drift. |
| #91 (FU-5) | Low | `tools/refresh.py:40` | Open. Verified: `_doc_store_refresh_lock = asyncio.Lock()` is module-level. Binds to the first-contending loop (safe in prod; test footgun). Lazy per-loop accessor removes the caveat. |
| #92 (FU-6) | Low / decision | `core.py` `_is_contained_relpath` | Open. DS-01 containment is purely lexical (documented in `tools.md`); an in-workspace symlink pointing outside isn't resolved. Accepted under the current threat model. **Decide** whether hostile-workspace isolation is a requirement before fixing. |
| KI-8 | — / decision | `.devcontainer/devcontainer.json` | Open (register only, no issue). The dev container still provisions the Claude Code IDE extension rather than Bob's. A carried decision from the Bob port, not code-broken. |

**Recommended sequencing (human's call):** **#87 (KI-9)** first — it's a real hang on the documented recovery
path, a self-contained analyzer fix, and the natural close-out of the DS-12/lifecycle work. Then **#88** (the
production-image smoke test — the only guard for three shipped infra fixes). Then the three Lows (#89/#90/#91)
opportunistically. #92 and KI-8 are decisions — surface, don't build, until the human calls them.

## Model & orchestration preferences (carry forward; confirm if restated)

- **Main thread = Fable 5** — orchestrator, **last-stop QA**, decision-maker, merge/PR/pause owner. Keep it
  lean: delegate context-heavy work, retain context for judgment and integration.
- **Fable subagents** — logic/judgment: fix design where correctness is subtle (the KI-9 timeout/teardown
  semantics, any concurrency), reviews, adversarial passes.
- **Sonnet subagents** — implementation and testing (the workhorse).
- **Fallback:** Fable 5 is unavailable after **~2026-07-08**. When Fable is unavailable, use **Opus 4.8** for
  both the orchestrator and the Fable logic subagents; Sonnet's role is unchanged.
- **Gates** per the [implementation cycle](../conventions/implementation-cycle.md): Orient → decide branch
  target → build (regression-test-first) → review → **QA on-thread** (fast tier always; the local
  `-m integration` gate whenever the unit touches analyzer/tool/position/doc-RAG runtime — CI skips it) →
  adversarial (Highs/subtle — **#87 qualifies**) → PR+record → stop. One unit per pass. Research is
  Context7-first ([research-policy.md](../conventions/research-policy.md)). Bounce-backs get a regression test
  before the re-fix.

## Stack commands

Fast tier (CI-equivalent), run every unit:
```
uv sync
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run --frozen pytest -m "not integration"
uv run --frozen pytest tests/test_env_sample_honesty.py -v
```
Integration gate — **local QA only, never CI** (needs live rust-analyzer + ripgrep fixture):
```
uv run --frozen pytest -m integration
```
**No local toolchain here:** this host has no Rust/`rust-analyzer`/`/workspaces` fixtures. The sweep ran the
integration gate inside a `rust:1` container reproducing the devcontainer (rust-analyzer component + cloned
ripgrep + `HOME=/home/vscode` for the baked model cache), with named volumes for speed. Reuse that harness for
#88/#90 (the producing session left the scripts under its scratchpad; re-derive from `.devcontainer/` +
`scripts/clone-ripgrep.sh` if absent). For #88 specifically you'll need `docker`/`podman build` of `Dockerfile`.

## Key file references

| What | Path |
|------|------|
| Follow-up tracker | GitHub issues #87–#92 (`gh issue view <n>`) |
| Living register (KI-8, KI-9) | `docs/impl/known-issues.md` |
| Origin audit (context) | `docs/security/defect-sweep-2026-07-01.md` (all rows ✅) |
| Prior effort's handoff | `docs/handoff/defect-sweep-resolution-handoff.md` |
| KI-9 target | `src/rust_lsp_mcp/analyzer.py` (`request_*` delegates, `_run`, `restart`, `_drain_task`) |
| Prod-image targets | `Dockerfile`, `.github/workflows/ci.yml`, `tests/test_infra_scripts.py` |
| Chunker target | `src/rust_lsp_mcp/doc_chunking.py` |
| Race-test targets | `tests/test_lifecycle_races.py`, `tests/test_doc_store.py` |
| Implementation cycle (gates) | `docs/conventions/implementation-cycle.md` |
| Adversarial pass contract | `docs/handoff/adversarial-review.md` |
| Config to keep in lockstep | `pyproject.toml`, `env.sample`, `.github/workflows/ci.yml` |

## Gotchas / things NOT to retry

- **Branch targeting is per-issue.** General `src/`/`tests/`/`docs/guide/`/`scripts/`/`Dockerfile`/CI fixes go
  **`main`-first** (branch off `origin/main`, PR to `main`, cherry-pick to `bob_prototype`). Only `.bob/`,
  `AGENTS.md`, and effort-continuity handoff docs are `bob_prototype`-only. **Never merge `bob_prototype →
  main`.** (`Dockerfile` and `scripts/` are **shared** — identical on both branches — so #88 is main-first.)
- **`validate_file_path` is main-only** (absent on `bob_prototype`) — don't assert it in any tool-registration
  test; #90/tests should stay branch-safe (subset checks), as the DS-28 test already is.
- **Pinned-dependency facts (load-bearing for #87):** multilspy 0.0.15's `send_request` waits on
  `request.cv`; its `stop()` (psutil tree terminate→kill, own timeouts) does **not** fail pending
  `_response_handlers` — that's exactly why an in-flight delegate hangs across a drain. Its `start_server`
  has no `try/finally` around the yield (DS-04 already handled the subprocess side); the live subprocess is at
  `lsp.server.process`. mcp 1.12.4 dispatches tool requests concurrently and runs sync tools inline.
- **The lifecycle is already hardened — don't re-solve DS-03/04/21.** `restart()`/`shutdown()` are serialized
  by `_lifecycle_lock`; a generation counter prevents stale-`ready`; `_run`'s `finally` force-stops the
  subprocess; `_drain_task` now drains a failing outgoing run (so one `refresh` recovers). #87 is a *different*
  gap (a hung request the drain can't cancel), not a regression of these.
- **Don't widen `except`s or convert genuine errors to `not_ready`.** The envelope contract is load-bearing
  (`ok`/`not_ready`/`not_found`/`error`; a permanent failure is `error`, transient is `not_ready`). #87's fix
  should map a timed-out delegate to a *clear* envelope (likely `error` with a message, or `not_ready` if a
  rebuild is genuinely in flight) — decide deliberately, don't blanket-catch.
- **#88 must not become a CI cost sink** — an image build per push is heavy. Prefer a dedicated/opt-in job or
  a local gate; if in CI, gate it (path filter on `Dockerfile`/infra, or manual dispatch).

## How to start

1. Read this doc, then `docs/impl/known-issues.md` (KI-8/KI-9) and `gh issue view 87` … `92`.
2. Confirm the model/orchestration prefs above still hold (and whether Fable or the Opus 4.8 fallback applies
   — after ~2026-07-08 use Opus 4.8).
3. Pick the first unit (recommended: **#87 / KI-9**), decide its branch target (`main`-first), and run one unit
   per pass through the gates — regression-test-first, integration gate for runtime-touching units, adversarial
   for #87 — stopping for human review after each.
