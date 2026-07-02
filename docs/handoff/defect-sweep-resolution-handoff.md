# Handoff — resolve the 2026-07-01 defect sweep to completion

Seed for a fresh session that drives the defect-sweep findings to done. Produced by the
[session-handoff skill](../../.bob/skills/session-handoff/SKILL.md). **Read this first**, then the anchor
docs below. Kickoff command: [`/resolve-defect-sweep`](../../.bob/skills/resolve-defect-sweep/SKILL.md).

- **Anchor (evidence):** [docs/security/defect-sweep-2026-07-01.md](../security/defect-sweep-2026-07-01.md)
  — 28 findings DS-01…DS-28, each with Where / What / Why / verifier reasoning.
- **Tracker:** GitHub issues **#45–#63** (label `audit-2026-07-01`). DS-19…DS-28 are consolidated in roll-up
  **#63**.
- **Living register:** [docs/impl/known-issues.md](../impl/known-issues.md) (design/doc-drift items).

## Where we are

- The sweep ran on `bob_prototype`: 7 parallel finders + adversarial verification (40 agents). 31 confirmed,
  1 refuted, 2 recovered from overloaded agents and hand-verified.
- The **audit doc shipped**: PR **#64** merged to `main`, cherry-picked onto `bob_prototype` (commit
  `3f93b34`). It exists on both branches.
- **19 issues opened** (#45–#63). **Progress (continuous drive, started 2026-07-01):**
  - Unit 1 — DS-01 (#45) + DS-02 (#46): closed via PR #65 → `main` (cherry-pick `0fb8627`); record PR #66.
  - Unit 2 — DS-03 (#47) + DS-04 (#48) + DS-21 (roll-up #63): closed via PR #67 → `main`
    (cherry-pick `6edd96e`); record PR #68. Surfaced [[KI-9]] (out-of-scope, recorded).
  - Unit 3 — DS-05 (#49) + DS-06 (#50): closed via PR #69 → `main` (cherry-pick `0746f11`);
    record PR #70.
  - Unit 4 — DS-07 (#51) + DS-08 (#52) + DS-14 (#58): closed via PR #71 → `main`
    (cherry-pick `8adac0e`); record PR #72. Recorded [[KI-9]]-adjacent hardening.
  - Unit 5 — DS-09 (#53): closed via PR #73 → `main` (cherry-pick `b7c5c52`); record PR #74.
  - Unit 6 — DS-10 (#54) + DS-11 (#55) + DS-23 (roll-up #63): closed via PR #75 → `main`
    (cherry-pick `b82492a`); record PR #76.
  - Unit 7 — DS-12 (#56) + DS-22 (roll-up #63): closed via PR #77 → `main`
    (cherry-pick `7e50883`); record PR #78.
  - **6 issues remain open** (#57–#63; #63 stays open until the other roll-up lows land).
- Nothing is deferred by decision yet — sequencing below is a recommendation, not a commitment.

## What's left (grounded)

All rows verified open as of this handoff; code anchors were confirmed against the installed
`multilspy==0.0.15` / `mcp==1.12.4` sources during the sweep. Fix each with a regression test first.

| DS | Issue | Sev | Where | Status / caveat |
|----|-------|-----|-------|-----------------|
| DS-01 | #45 | High | `tools/goto_definition.py:72` (+hover/find_references/document_symbols) | ✅ Done — PR #65 → `main`, cherry-picked to `bob_prototype` (`0fb8627`). Shared lexical containment guard rejects out-of-workspace `file` before the delegate; adversarial `no-breaks`. |
| DS-02 | #46 | High | `core.py:181` | ✅ Done — PR #65 (same commit). `location_to_external` containment-checks `relativePath`; out-of-workspace locations fall back to URI or are skipped. |
| DS-03 | #47 | High | `analyzer.py:489` | ✅ Done — PR #67 → `main`, cherry-picked to `bob_prototype` (`6edd96e`). Generation counter; superseded `_run` can't stamp stale `ready`. |
| DS-04 | #48 | High | `analyzer.py:440` | ✅ Done — PR #67 (same commit). `_run` finally force-stops the subprocess on every reachable cancel window. |
| DS-05 | #49 | High | `doc_store.py:274` | ✅ Done — PR #69 → `main`, cherry-picked to `bob_prototype` (`0746f11`). `project_root` fingerprint in collection metadata; cross-project adoption refused. Build-once kept; stale-after-edit out of scope. |
| DS-06 | #50 | High | `tests/test_doc_store.py:317` | ✅ Done — PR #69 (same commit). Real `init_doc_store()` now tested offline via injected EF; dead `__wrapped__` reimplementation removed. |
| DS-07 | #51 | Med | `analyzer.py:262` | ✅ Done — PR #71 → `main`, cherry-picked to `bob_prototype` (`8adac0e`). `STATE_ERROR` + `require_ready` error envelope; refresh recovers. |
| DS-08 | #52 | Med | `core.py:56` | ✅ Done — PR #71 (same commit). Doc build offloaded to a background thread; loop serves immediately. |
| DS-09 | #53 | Med | `core.py:252` | ✅ Done — PR #73 → `main`, cherry-picked to `bob_prototype` (`b7c5c52`). Prefers `selectionRange.start` (name position), falls back to `range.start`. |
| DS-10 | #54 | Med | `doc_chunking.py:347` | ✅ Done — PR #75 → `main`, cherry-picked to `bob_prototype` (`b82492a`). Fence lines no longer setext-eligible. |
| DS-11 | #55 | Med | `doc_chunking.py:293` | ✅ Done — PR #75 (same commit). Pre-scan requires a compact contiguous frontmatter block; else normal splitting. |
| DS-12 | #56 | Med | `doc_store.py:81` | ✅ Done — PR #77 → `main`, cherry-picked to `bob_prototype` (`7e50883`). `_read_lock` makes search atomic vs rebuild; empty-query (DS-22) rejected; concurrent refresh serialized. |
| DS-13 | #57 | Med | `settings.py:63` (+configuration.md, env.sample, Dockerfile) | Open. Dead `RLM_CARGO_*` knobs — wire through or remove + doc. `Dockerfile` part is `bob_prototype`-only. |
| DS-14 | #58 | Med | `tools/status.py` | ✅ Done — PR #71 (same commit). `status.doc_index_state`; search_docs errors on permanent failure; refresh re-inits an absent/errored store. |
| DS-15 | #59 | Med | `scripts/setup.sh:34` | Open. Disables host-global git signing; guard on a container marker. |
| DS-16 | #60 | Med | `Dockerfile:80` | Open. `status` staleness null on rootful Linux (no `safe.directory`). `bob_prototype`-only if Dockerfile diverges. |
| DS-17 | #61 | Med | `tests/test_phase34_adversarial.py:228` | Open. Malformed-response branch never runs in CI. Fix = drop `integration` marker on the two `_MalformedLSP` tests (they need no live analyzer). |
| DS-18 | #62 | Med | `core.py:55` | Open. `_lifespan`/`analyzer_lifespan` untested (swallow contract + teardown). |
| DS-19…DS-28 | #63 | Low | (roll-up) | Open. **DS-21 ✅ done** (PR #67, with DS-03/04). **DS-22 ✅, DS-23 ✅** (PR #77, #75). Remaining: `status` sync subprocess (DS-19), dead null-check (DS-20), dead sentinel (DS-24), model-persistence doc (DS-25), `RA_TARGET_DIR` comment (DS-26), SELinux relabel (DS-27), no tool-registration test (DS-28). Issue closes when all land. |

**Recommended sequencing (human's call):** DS-01/02 → DS-03/04/21 → DS-05/06 → Mediums by area (RAG
DS-10/11/23; docs/config DS-13/25/26; test-gaps DS-17/18/28) → Lows opportunistically.

## Model & orchestration preferences (confirmed this session)

- **Main thread = Fable 5** — orchestrator, **last-stop QA**, decision-maker, merge/PR/pause owner. Keep it
  lean: delegate context-heavy work, retain context for judgment and integration.
- **Fable subagents** — logic/judgment: triage, branch-target classification, subtle-correctness fix design,
  adversarial pass.
- **Sonnet subagents** — implementation and testing (the workhorse).
- **Fallback:** Fable 5 is unavailable after ~2026-07-08. When Fable is unavailable, use **Opus 4.8** in its
  place — both the main-thread orchestrator role and the Fable logic/judgment subagents. Sonnet's role is
  unchanged.
- **Gates** per the [implementation cycle](../conventions/implementation-cycle.md): Orient → decide branch
  target → build (regression-test-first) → review → **QA on-thread** → adversarial (Highs/subtle) → PR+record
  → stop. One unit per pass. Research is Context7-first
  ([research-policy.md](../conventions/research-policy.md)). Bounce-backs get a regression test before the
  re-fix. Don't reopen settled decisions.
- Alternative if driving under Bob instead: the `build`/`review`/`qa`/`adversarial` modes
  ([roles.md](roles.md), [continue-build](../../.bob/skills/continue-build/SKILL.md)).

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
Integration gate — **local QA only, never CI** (live rust-analyzer + ripgrep fixture):
```
uv run --frozen pytest -m integration
```
Full suite: `uv run --frozen pytest`. Run one issue's tests during dev with `-k` / a path.

## Key file references

| What | Path |
|------|------|
| Evidence (anchor) | `docs/security/defect-sweep-2026-07-01.md` |
| Living issue register | `docs/impl/known-issues.md` |
| Implementation cycle (gates) | `docs/conventions/implementation-cycle.md` |
| Adversarial pass contract | `docs/handoff/adversarial-review.md` |
| Research policy (Context7-first) | `docs/conventions/research-policy.md` |
| Delivery lifecycle | `docs/conventions/lifecycle.md` |
| Kickoff command | `.bob/skills/resolve-defect-sweep/SKILL.md` |
| Config to keep in lockstep | `pyproject.toml`, `env.sample`, `.github/workflows/ci.yml` |
| Bob orchestration alt. | `docs/handoff/roles.md`, `.bob/custom_modes.yaml` |

## Gotchas / things NOT to retry

- **Branch targeting is per-issue, not a default.** General `src/`/`tests/`/`docs/guide/`/`scripts/` fixes go
  **`main`-first** (branch off `origin/main`, PR, cherry-pick to `bob_prototype`). `bob_prototype`-only files
  (offline/netiso `Dockerfile` path, baked-model config, `.bob/`, `AGENTS.md`) commit on `bob_prototype`
  directly. **Never merge `bob_prototype → main`.** DS-13/DS-16/DS-25/DS-26 may touch the `bob_prototype`-only
  Dockerfile — classify before branching.
- **The integration gate is your job.** CI runs only `-m "not integration"`; the live-analyzer tests won't
  catch regressions unless you run `-m integration` locally. (DS-17 exists precisely because a real branch is
  guarded only by never-run integration tests — its fix is to *unmark* those tests, which need no live analyzer.)
- **Refuted:** the CRLF `\n{2,}` chunking claim — do not re-chase.
- **Intended, not bugs:** doc-store build-once adoption across restarts (only cross-project contamination is
  DS-05's scope); the `not_found` vs `ok`+empty envelope distinction.
- **Pinned-dependency facts (verified, load-bearing):** multilspy 0.0.15 always populates `relativePath`
  (DS-02), raises `AssertionError` on null LSP responses (DS-20), joins paths with `PurePath` so absolute/`..`
  escape the root (DS-01), and its `start_server` teardown is not in a `finally` (DS-04); mcp 1.12.4 runs sync
  tools inline on the loop (DS-19) and dispatches requests concurrently (DS-12/DS-22). Fixes must hold against
  these, not against assumed behavior.
- **Regression-test-first:** several findings ship with a concrete reproduction in the audit doc — reuse it as
  the failing test before fixing.
- **Keep indexes current:** update `docs/handoff/index.md` and check the DS row off in the audit doc as each
  issue closes.

## How to start

1. Read this doc, then `docs/security/defect-sweep-2026-07-01.md` and `docs/impl/known-issues.md`.
2. Confirm the model/orchestration prefs above still hold.
3. Run **`/resolve-defect-sweep`** (or say "resolve the defect-sweep issues"). Pick the first unit
   (recommended: DS-01/DS-02), decide its branch target, and run one unit per pass through the gates —
   stopping for human review after each.
