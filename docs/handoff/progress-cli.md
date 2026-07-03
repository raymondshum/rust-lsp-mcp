# Build progress tracker — CLI frontend effort

**Single source of truth for "where are we" on the
[cli-frontend plan](../planning/cli-frontend.md).** The **orchestrator is the
sole writer**; build/reviewer/QA/adversarial agents report results, the
orchestrator records them here. Read by [continue.md](continue.md) (efforts
table) to pick the next phase. The original build's tracker is
[progress.md](progress.md) (complete).

## State vocabulary

`not-started` → `authoring` → `in-progress` → `qa` → `adversarial` →
`pr-open` → `done`. (`blocked` = paused for human, with a one-line reason.
No container seam in this effort — the dev container already exists.)

## Gate-zero (handoff self-review)

`gate-zero: passed (2026-07-03)` — adversarial pass over this effort's handoff artifacts
(the five `cli-phase-*.md` prompts, this tracker, and the continue.md
multi-effort routing) must be `passed` before any phase starts. Values:
`not-run` | `passed` | `blocked: <reason>`. Orchestrator flips it and records
the date in the log below.

## Phase status

| Phase | Prompt | Depends on | Parallelizable? | State |
|-------|--------|-----------|-----------------|-------|
| C1 — Daemon transport | [cli-phase-1-daemon.md](cli-phase-1-daemon.md) | — | With C3 on the fast tier only; podman integration gates serialize | done |
| C2 — `rust-lsp` CLI client | [cli-phase-2-cli.md](cli-phase-2-cli.md) | C1, C3 | No (single package build; needs C1's daemon fixture + C3's pinned version fields) | done |
| C3 — KI-12 status versions | [cli-phase-3-versions.md](cli-phase-3-versions.md) | — | With C1 on the fast tier only; integration gates serialize | done |
| C4 — Deployment + docs | [cli-phase-4-deploy-docs.md](cli-phase-4-deploy-docs.md) | C1, C2 | With C5 (disjoint files) | pr-open |
| C5 — Skill revision | [cli-phase-5-skill.md](cli-phase-5-skill.md) | C2 | With C4 (disjoint files) | not-started |

## Dependency graph (what the orchestrator may fan out)

```
C1 ──> C2 ──> C4
C3 ──┘ └────> C5      (C4 ∥ C5 after C2; disjoint files)
                      (C3: no deps; ∥ C1 on the fast tier; integration gate
                       serial; must be done before C2 — it pins the status
                       version fields C2's `version` surfaces)
```

- Cross-phase: strictly the arrows above. Never start a phase whose dependency
  isn't `done`.
- The live analyzer + podman integration gate remain a **single serialized
  resource** across phases and across efforts (see
  [progress.md](progress.md) invariants); parallelism is fast-tier only.
- `uv add`/lockfile changes are orchestrator-only (none expected — the plan
  adds zero dependencies; C2 touches `pyproject.toml` for packaging only).

## Per-phase log (orchestrator appends)

> One line per state transition: `<date> Phase Cn → <state> (PR #/notes)`.

- 2026-07-02 Tracker created with the frozen plan (grill + verification pass +
  2-reviewer adversarial plan review all done same day; see the plan's status
  header). Gate-zero not yet run — first `continue` invocation must red-team
  the five prompts + this tracker + the continue.md routing change before
  starting C1.
- 2026-07-03 Gate-zero → **passed**. Opus red-team over the 8 handoff
  artifacts: verdict `fixable` — 3 must-fixes + 5 minors, all applied:
  (1) C2↔C3 version-field contract pinned (`server_version`,
  `multilspy_version`, `rust_analyzer_version`) + C2 now depends on C3;
  (2) C5 dry-run transcript recorded by the orchestrator, not the QA agent
  (sole-writer rule); (3) automation directive explicitly covers gate-zero's
  stop-and-report; (4) roles.md/adversarial-review.md de-hardcoded from
  progress.md to "active tracker"; (5) continue.md concurrency example gains
  the C1+C3 / C4+C5 pairs; (6) C2's pyproject packaging edit reconciled with
  the shared-config rule; (7) `_server_instances` assertion clarified as
  in-process-only; (8) C1 fixture location/marker/harness pinned. Non-issues
  confirmed: parity exclusions match live tool set; dispatcher still routes
  the original effort correctly. Per the standing automation directive,
  proceeding directly to C1 (∥ C3 fast-tier) this run.
- 2026-07-03 Phase C3 → **pr-open**. KI-12 status versions built, reviewed, QA'd,
  red-teamed — all gates green. Shipped: `status` ok-envelope gains the three pinned
  fields `server_version`/`multilspy_version` (importlib.metadata per call, null on
  PackageNotFoundError) + `rust_analyzer_version` (`<bin> --version` captured once in
  `AnalyzerManager.start()`, boolean-guarded so `restart()` never re-captures; 5s
  timeout, thread-offloaded like the git-HEAD capture; any failure → null, never
  raises). KI-12 moved to Resolved (closes #115 — orchestrator closes the issue on
  merge). Gates: fast tier 701 passed; podman integration gate 34 passed / 1
  documented skip (11m41s) incl. live version-field assertions; review `minor` (2
  fixed: TimeoutExpired degradation test, #115 backlink); adversarial `no-breaks`
  (PermissionError/garbage-stdout/KeyboardInterrupt/capture-once-on-failure all
  held). Record note: none — phase touched no seams. Built in parallel with C1 on
  the fast tier; integration gate ran serially.
- 2026-07-03 Phase C3 → **done**. PR #121 merged to `main` (71656e8) after green CI;
  #115 auto-closed. This flip rides in C1's PR (direct pushes to main blocked).
- 2026-07-03 Phase C1 → **pr-open**. Daemon transport built, reviewed, QA'd (2 rounds),
  red-teamed — all gates green. Shipped: `Settings.transport`/`http_port` (range-
  validated); conditional FastMCP construction via `_build_mcp()` (stdio byte-identical
  bar a recorded import-time `get_settings()`; HTTP: no lifespan, loopback hard-coded,
  stateless+json); `compose_daemon_lifespan()` nesting `_lifespan` wholesale + owned
  `uvicorn.run()`; reusable in-process daemon fixture (self-cleaning reload-back) +
  warm-across-connections and refresh/nav teardown-race integration tests (fail_after-
  bounded). Gates: fast tier 720 passed; review `minor` (timeout bounds + fixture
  reload-back applied); QA round 1 FAILED (daemon_app fixture never registered —
  conftest re-export + fast-tier setup-plan guard added); QA round 2 GREEN (36 passed
  + 1 documented skip, 10m19s, both daemon criteria proven live); adversarial HIGH
  `no-breaks` (once-per-process proven at source+live; loopback unreachable by any
  env; double-refresh clean; typo'd RLM_TRANSPORT fails loud). Post-adversarial
  hardening applied: http_port Field(ge=1, le=65535) + regression test (record note 1).
  Record notes: import-time get_settings() blast radius (review + adversarial note 3);
  race test can pass without exercising the drain window (covered by lifecycle-races
  fake tests + adversarial double-refresh probe); FASTMCP_LOG_LEVEL pre-existing.
- 2026-07-03 Phase C1 → **done**. PR #122 merged to `main` (bacf1fb) after green CI.
  This flip rides in C2's PR (direct pushes to main blocked).
- 2026-07-03 Phase C2 → **pr-open**. `rust-lsp` CLI client built, reviewed, QA'd (2
  rounds), red-teamed — all gates green. Shipped: import-light `src/rust_lsp_cli/`
  (stdlib module scope; `mcp` client lazy; never imports `rust_lsp_mcp` —
  subprocess-guarded); 9 subcommands + `version`; D8 exit codes (ok/not_found→0,
  error→1, not_ready→2, transport→3; argparse usage errors keep exit 2, documented
  + signed off); `--wait` (finite-only, boot-riding, never-connected→3 vs stuck→2);
  URL derived from RLM_HTTP_PORT with RLM_CLI_URL override; parity test vs
  list_tools() (exclusions: probe, analyzer_status); packaging = packages append +
  script entry only, lock unchanged. Gates: fast tier 766 passed; review `clean`;
  QA round 1 FAILED — pre-existing harness bug exposed (autouse
  `_reset_doc_store_singleton` cleared the live in-process daemon's doc store →
  search-docs over HTTP not_ready; production path proven healthy) — fixed via
  snapshot/clear/restore reset window + daemon-test skip + 7 guard tests
  (falsified by reverting); QA round 2 GREEN (38 passed + 1 documented skip,
  11m53s; **U4 recorded: warm `rust-lsp status` subprocess = 0.892s**, bound <5s);
  adversarial MEDIUM `no-breaks` (isError→exit 1 synthesized envelope; stdout pure
  in all failure modes incl. KeyboardInterrupt; import-light held incl. chromadb).
  Post-adversarial hardening: `--wait` rejects non-finite values (nan/inf hung the
  poll loop forever — note N1) + 3 regression tests. Record notes: N2 raw traceback
  on Ctrl-C (cosmetic), N3 RLM_HTTP_PORT= (empty) yields port-less URL → clean exit
  3, N4 uv-wrapper stderr warning (not the installed script).
- 2026-07-03 Phase C2 → **done**. PR #123 merged to `main` (ed4e8e7); post-merge CI
  verified green. Flip rides in C4's PR.
- 2026-07-03 Phase C4 → **pr-open**. Deployment + docs built, reviewed, QA'd,
  red-teamed. Shipped: compose flip (both services daemons via RLM_TRANSPORT env;
  entrypoint override removed; restart: unless-stopped; no ports + forbidding
  comments; project mount gains `:ro,z` shared SELinux label; header rewritten with
  migration note — old exec-stdio warm-start retired as KI-13 double-writer);
  README CLI-access section + security note; new docs/guide/cli.md (full reference,
  exit codes, --wait, health check, log story, troubleshooting incl. SELinux
  masked-failure row); configuration.md (RLM_TRANSPORT/RLM_HTTP_PORT server-side;
  RLM_CLI_URL CLI-side, non-empty-overrides semantics); KI-14 (refresh shared-index,
  decided:documented), KI-15 (ClosedResourceError noise, open-low), KI-16
  (unreadable /project masked as ready-with-zero-docs, open — found live by QA on
  SELinux-enforcing podman), KI-13 addendum. Gates: fast tier 766 green; review
  `major` (development.md still taught the retired warm-start — rewritten with
  KI-13 pointer; RLM_CLI_URL empty-vs-unset nit; historical handoff log annotated;
  in-passing ,Z→,z correction accepted); QA live compose smoke PASS (image built,
  daemon ready, exec sweep green, no ports, restart policy, health check, expected
  log noise; SELinux gap found → :ro,z + troubleshooting + KI-16); adversarial
  `breaks-found` → 2 fixed (cli.md exit-3 hint named the service; development.md
  false "separate caches" claim corrected — compose `rlm-data` is pinned to the
  SAME physical volume `rust-lsp-mcp-data` as the docker-run examples, KI-13
  applies across launch methods). Orchestrator decision recorded: full `-m
  integration` re-run waived for this docs-only phase (no src/tests changes; live
  smoke was the meaningful gate; CI fast tier on the PR).
