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
- **19 issues opened** (#45–#63), all currently **open** (verified via `gh issue list --label
  audit-2026-07-01 --state open` → 19). No fixes landed yet.
- Nothing is deferred by decision yet — sequencing below is a recommendation, not a commitment.

## What's left (grounded)

All rows verified open as of this handoff; code anchors were confirmed against the installed
`multilspy==0.0.15` / `mcp==1.12.4` sources during the sweep. Fix each with a regression test first.

| DS | Issue | Sev | Where | Status / caveat |
|----|-------|-----|-------|-----------------|
| DS-01 | #45 | High | `tools/goto_definition.py:72` (+hover/find_references/document_symbols) | Open. Path traversal → arbitrary file read; fix with DS-02 as one containment change. |
| DS-02 | #46 | High | `core.py:181` | Open. `..`-path leak; multilspy always populates `relativePath`. |
| DS-03 | #47 | High | `analyzer.py:489` | Open. `refresh` mid-index → stale `ready`; couple with DS-04/DS-21. |
| DS-04 | #48 | High | `analyzer.py:440` | Open. Drain-timeout orphans subprocess (multilspy teardown has no `finally`). |
| DS-05 | #49 | High | `doc_store.py:274` | Open. Adopt-without-freshness. **Only** cross-project + stale-after-edit is in scope; build-once persistence is intended. |
| DS-06 | #50 | High | `tests/test_doc_store.py:317` | Open. Real `init_doc_store()` untested (dead `__wrapped__` branch). |
| DS-07 | #51 | Med | `analyzer.py:262` | Open. Failed startup swallowed → `indexing` forever; needs an error surface. |
| DS-08 | #52 | Med | `core.py:56` (+architecture.md:195, tools.md) | Open. Blocking rebuild on the loop at startup; also a doc-mismatch. |
| DS-09 | #53 | Med | `core.py:252` | Open. `document_symbols` uses `range.start`, not `selectionRange`. |
| DS-10 | #54 | Med | `doc_chunking.py:347` | Open. `---` after a code fence → setext misparse (reproduced). |
| DS-11 | #55 | Med | `doc_chunking.py:293` | Open. Leading `---` swallowed as frontmatter (reproduced). |
| DS-12 | #56 | Med | `doc_store.py:81` / `search_docs.py:68` | Open. `refresh`/`search` race; no lock. (Low #63 DS-22 is the tool-side view.) |
| DS-13 | #57 | Med | `settings.py:63` (+configuration.md, env.sample, Dockerfile) | Open. Dead `RLM_CARGO_*` knobs — wire through or remove + doc. `Dockerfile` part is `bob_prototype`-only. |
| DS-14 | #58 | Med | `docs/guide/tools.md:315` | Open. `status` can't report doc-index readiness; recovery path loops. |
| DS-15 | #59 | Med | `scripts/setup.sh:34` | Open. Disables host-global git signing; guard on a container marker. |
| DS-16 | #60 | Med | `Dockerfile:80` | Open. `status` staleness null on rootful Linux (no `safe.directory`). `bob_prototype`-only if Dockerfile diverges. |
| DS-17 | #61 | Med | `tests/test_phase34_adversarial.py:228` | Open. Malformed-response branch never runs in CI. Fix = drop `integration` marker on the two `_MalformedLSP` tests (they need no live analyzer). |
| DS-18 | #62 | Med | `core.py:55` | Open. `_lifespan`/`analyzer_lifespan` untested (swallow contract + teardown). |
| DS-19…DS-28 | #63 | Low | (roll-up) | Open. `status` sync subprocess, dead null-check, unserialized refresh, empty-query, indented fences, dead sentinel, model-persistence doc, `RA_TARGET_DIR` comment, SELinux relabel, no tool-registration test. |

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
