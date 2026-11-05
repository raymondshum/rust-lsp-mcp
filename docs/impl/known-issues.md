# Known issues (living register)

Open design and documentation issues that are **known but not yet fixed**. This is
a living list — add an entry when one is surfaced (by a review, a red-team pass, or
a user), and close it (move to *Resolved*) when fixed.

It exists so a discrepancy surfaced in one session isn't rediscovered in the next.
It is not a bug tracker for runtime defects; it is for design warts, code/doc
drift, and gaps that need a decision.

## Review cadence

Check this list at these lifecycle checkpoints (see
[lifecycle.md](../conventions/lifecycle.md)):

- **At the start of a grill/plan session** — does an open issue affect the design
  being decided?
- **At each phase's record step** — did this phase touch an open issue? Close it,
  or note why it's carried.
- **When editing a module an open issue names** — fix it in passing if cheap, or
  confirm it's still open.

## Entry format

`### <id> — <one-line title>` then: **Where** (file:line or area), **What**
(the discrepancy), **Why it matters**, **Status** (`open` / `decided: <plan>`).

---

## Open

### KI-10 — Call-hierarchy tool needs a design session before implementation
- **Where:** prospective `incoming_calls` tool (not yet implemented) — LSP
  `textDocument/prepareCallHierarchy` + `callHierarchy/incomingCalls`,
  composed through [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py)'s
  `_race_teardown` (KI-9).
- **What:** `incoming_calls` via LSP callHierarchy is the right primitive, but the
  design must resolve: `selectionRange` (not `range.start`) mapping so positions
  round-trip, prepare-null → `not_found` vs incomingCalls-`[]` → ok+empty semantics,
  `fromRanges` retention, `CallHierarchyItem.data` threading, and KI-9
  single-coroutine composition through `_race_teardown`.
- **Why it matters:** A two-request (`prepareCallHierarchy` then
  `incomingCalls`) delegate must still race teardown as a single coroutine per
  KI-9's rule ("every `self._lsp` await must go through `_race_teardown`"), and
  the position/null-vs-empty ambiguities mirror the exact class of bugs UR-10
  and UR-11 already found in the existing tools — building this without a
  grill/plan session risks repeating both.
- **Status:** open — needs a grill/plan session before implementation. Tracked
  as [#113](https://github.com/raymondshum/rust-lsp-mcp/issues/113).
  Reference: [docs/audit/2026-07-02-usability-review.md](../audit/2026-07-02-usability-review.md) UR-17.

### KI-14 — `refresh` blast radius on a shared daemon
- **Where:** `refresh` MCP tool ([src/rust_lsp_mcp/tools/refresh.py](../../src/rust_lsp_mcp/tools/refresh.py))
  and the `rust-lsp refresh` CLI subcommand
  ([src/rust_lsp_cli/cli.py](../../src/rust_lsp_cli/cli.py)) · daemon topology
  in [docker-compose.yml](../../docker-compose.yml).
- **What:** the CLI-frontend daemon (D1/D12,
  [cli-frontend.md](../planning/cli-frontend.md)) is one long-lived server
  shared by every MCP client and every `rust-lsp` invocation against it.
  `refresh` tears down and rebuilds the single analyzer and doc index that
  server owns, so one caller's `refresh` makes every other caller's next
  navigation call return `not_ready` until re-indexing completes — there is
  no per-caller isolation to lose, this is inherent to the shared-daemon
  design, not a bug.
- **Why it matters:** an agent that calls `refresh` mid-task (e.g. reflexively,
  without checking `status` first) can silently stall every other concurrent
  user of the same daemon, which is easy to miss since the tool's own
  response (`ok`, `state: "indexing"`) looks the same whether or not anyone
  else is affected.
- **Status:** decided: documented, not code-mitigated. Both the
  `refresh` tool's own docstring/[Tools reference](../guide/tools.md#refresh)
  blast-radius note and the CLI's
  [docs/guide/cli.md](../guide/cli.md#refresh-shared-daemon-blast-radius)
  warning tell callers to check `status` first; the
  `rust-code-navigation` skill's refresh guidance (Phase 5) carries the same
  warning. No stronger guard (e.g. per-caller index isolation) is planned —
  it would undercut the "one warm analyzer" design the daemon exists for.

  **2026-11-04 (defect sweep SW-10, remediation T04):** the shared daemon's
  `refresh` still restarts the one analyzer, so every caller's navigation
  calls still return `not_ready` until it re-indexes. Doc search no longer
  blocks: `refresh` returns once the doc rebuild thread has started, the
  rebuild builds into a staging collection while `search_docs` keeps
  answering from the old index for every caller, and a second `refresh`
  during a rebuild queues nothing (`doc_rebuild_started: false`). Entry
  stays open for the analyzer half.

  **2026-11-04 (remediation T05, daemon):** under `rlm-connect`
  ([docs/guide/daemon.md](../guide/daemon.md)) there is one daemon per project
  folder, shared by every IDE window and `rust-lsp` call on that project, so
  the blast radius applies per project: one window's `refresh` stalls
  navigation in the other windows on the same project, never in another
  project's daemon.

### KI-15 — Benign `ClosedResourceError` log noise in stateless HTTP mode
- **Where:** the streamable-HTTP daemon's per-request teardown, inside the
  pinned `mcp` SDK (1.12.4) — not this project's code. Surfaces in
  `docker logs`/`podman logs` for the daemon container
  ([docker-compose.yml](../../docker-compose.yml)).
- **What:** every stateless HTTP request (`stateless_http=True`, D3/U2 in
  [cli-frontend.md](../planning/cli-frontend.md)) opens and tears down its
  own transport; the SDK's teardown path logs a `ClosedResourceError`
  traceback per request as part of that normal teardown. It does not affect
  the response the caller receives (confirmed harmless during Phase 1/2's
  live verification, U2) — it is log noise, not a failed call.
- **Why it matters:** an operator tailing daemon logs (especially during a
  long `--wait` poll loop, which issues a `status` call every 2 seconds) will
  see a steady stream of tracebacks that look alarming but indicate nothing
  wrong. Documented in [docs/guide/cli.md](../guide/cli.md#daemon-logs) so
  it isn't mistaken for a real failure.
- **Status:** open — upstream SDK behavior, not something this project's code
  controls. Low severity (cosmetic/log-noise only). Revisit whether it's
  still present on the next `mcp` SDK upgrade past 1.12.4.

### KI-16 — Unreadable `/project` mount (e.g. SELinux) is masked as ready-with-zero-docs + generic errors
- **Where:** doc-store index build in
  [src/rust_lsp_mcp/doc_store.py](../../src/rust_lsp_mcp/doc_store.py)
  (the corpus glob + `read_text`, ~lines 270–307 — an unreadable `/project`
  simply yields zero files, which builds a "successful" empty index) ·
  analyzer-side file reads, surfaced through the navigation tools' generic
  LSP-error envelope. Trigger observed on an SELinux-enforcing
  rootless-podman host where the `${RUST_PROJECT}:/project:ro` bind mount
  lacked a relabel: `container_t` cannot read e.g. `user_tmp_t` files.
- **What:** when the container can mount but not **read** the project, no
  in-band signal distinguishes it from a healthy empty project: `status`
  reports `state: "ready"` with `doc_index_chunk_count: 0`
  (indistinguishable from a project that genuinely ships no Markdown), and
  every navigation call returns a generic "LSP error: File read failed …
  Retry" envelope with `recovery: "unknown"` — guidance that is actively
  misleading, since retrying an access denial never helps.
- **Why it matters:** silent wrong answers (an agent concludes "this project
  has no docs" / "file reads fail transiently") plus retry-suggesting
  recovery text for a permanent host-side condition. QA hit exactly this in
  the C4 compose smoke and needed a host-side
  `chcon -Rt container_file_t` to unmask it.
- **Status:** open — docs mitigate for now: the compose project mount ships
  `:ro,z` (the shared SELinux relabel, matching the README's `z`-not-`Z`
  convention) and [docs/guide/cli.md](../guide/cli.md#troubleshooting) has a
  troubleshooting row for the symptom pair. A real fix is a **preflight
  readability check** on `/project` (surfaced via `preflight_warnings`, or
  failing louder) — future work, out of the C4 phase's docs-only scope.

### KI-17 — The host inotify watch limit stops rust-analyzer's file watcher
- **Where:** the rust-analyzer stderr reader in
  [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py)
  (`_make_stderr_reader`, which matches `notify error:` lines) · the host's
  `fs.inotify.max_user_watches` and `fs.inotify.max_user_instances`.
- **What:** once the host limit is reached, rust-analyzer stops receiving
  change events for the paths it could not watch (it logs `notify error: OS
  file watch limit reached.`, or an I/O error when no inotify instance is
  left). `status` now reports this as `watcher_health: "error"`, with the
  stderr line in `watcher_message`.
- **Why it matters:** a branch switch or an edit no longer reaches the code
  index until `refresh`, so `stale` and query answers can describe an older
  tree.
- **Status:** decided: the operator raises the limit on the host. No project
  file sets it from a container, because rootless podman containers share one
  user namespace, so a container write to the sysctl would change it for
  every container of that user.

---

## Resolved

### KI-11 — Out-of-workspace navigation degrades to a misleading `not_found`
- **Where:** [src/rust_lsp_mcp/core.py](../../src/rust_lsp_mcp/core.py)
  `location_to_external` · [src/rust_lsp_mcp/tools/goto_definition.py](../../src/rust_lsp_mcp/tools/goto_definition.py)
  `goto_definition` (the `mapped is None` skip, falling through to the
  all-skipped branch, which returned the same `not_found` as a genuine
  zero-result answer).
- **What:** `location_to_external` drops std/dependency locations (they resolve
  outside `repo_root`), so `goto_definition` on e.g. `Vec` silently skips the
  location and reports "No definition found" — the identical message it uses
  when there really is no definition — teaching agents the symbol doesn't
  exist. `find_symbol`'s Batch-A message (PR #108) now explains the workspace
  boundary for that tool, but `goto_definition`'s `not_found` path does not.
- **Why it matters:** An agent asking "where is `Vec` defined?" gets a
  false-negative indistinguishable from "you misspelled this / it doesn't
  exist," which is worse than an honest "outside workspace" answer and can
  send the agent down an unproductive debugging path.
- **Status:** resolved. Tracked as
  [#114](https://github.com/raymondshum/rust-lsp-mcp/issues/114).
  Reference: docs/audit/2026-07-02-usability-review.md,
  Cross-cutting findings, completeness critic (CC-2).
- **Resolved:** 2026-11-05 (remediation-3 T02). When rust-analyzer returns
  definition locations and every one maps to `None` in `location_to_external`,
  `goto_definition` now returns a `not_found` whose message reads "<n>
  definition location(s) for <file>:<line>:<character> resolve outside the
  workspace; only in-workspace definitions are returned (the standard library
  and dependencies are not)." An empty result keeps "No definition found at
  <file>:<line>:<character>.", and a mix of inside and outside locations
  returns `ok` with the inside ones. Regression tests in
  `tests/test_goto_definition.py` (`TestOutOfWorkspaceDefinitions`).

### KI-13 — ChromaDB cross-process single-writer hazard on a shared `/data` volume
- **Where:** [src/rust_lsp_mcp/doc_store.py](../../src/rust_lsp_mcp/doc_store.py)
  (`PersistentClient` at `chroma_path`) · [docker-compose.yml](../../docker-compose.yml)
  (both services share the `rust-lsp-mcp-data` volume).
- **What:** the guarded one-client-per-path invariant (regression test in
  `tests/test_doc_store.py`) is **intra-process only**. Two OS processes opening
  the same on-disk Chroma SQLite concurrently — two `docker run` sessions on one
  volume today, or (post-CLI-frontend) the HTTP daemon plus a `docker exec …
  rust-lsp-mcp` stdio *server* in the same container — are unguarded and risk
  `database is locked` errors or store corruption, especially during a
  `refresh`-driven rebuild.
- **Why it matters:** silent doc-index corruption; the CLI-frontend daemon
  ([cli-frontend.md](../planning/cli-frontend.md) D12) makes the concurrent-
  process scenario more likely because a long-lived writer now always exists.
  Note the import-light CLI itself never opens Chroma — the hazard is a second
  *server* process, not `rust-lsp`.
- **Status:** decided: D12's single-writer mandate (documented in the
  CLI-frontend Phase 4 compose/README rewrite: one process per Chroma path;
  separate volume names if two servers are truly needed). At that point the
  entry stood open until a stronger guard (e.g. an on-disk lock or per-mode
  volumes by default) was decided.
  **2026-07-03 (CLI-frontend Phase 4):** the compose flip landed —
  [docker-compose.yml](../../docker-compose.yml)'s entrypoint now runs the
  daemon directly (`RLM_TRANSPORT=streamable-http`), the old `sleep infinity`
  + `docker exec … rust-lsp-mcp` stdio warm-start path is retired, and the
  single-writer mandate is spelled out in the compose header comment, the
  README's [CLI access](../../README.md#cli-access-for-agents-without-mcp)
  section, and [docs/guide/cli.md](../guide/cli.md)'s troubleshooting table.
  On that date the entry remained open per the stronger-guard note above.
  **2026-11-03 (defect sweep SW-02, remediation T01):** the hazard was shown on
  the very first start, in the README's documented config. Two containers started
  together on a fresh shared volume: the first built its doc index normally, and
  the second reported `doc_index_state: "error"` with `InternalError: error
  returned from database: (code: 1) table collections already exists`, its
  `search_docs` returning an error envelope that recommended `refresh`. Fix: one
  exclusive `fcntl.flock` on `<chroma_path>/.rlm-doc-store.lock` now covers
  `PersistentClient` construction, the adopt check and any rebuild
  (`_prepare_store`), and a `rebuild()` on a live store (`refresh`) takes the same
  flock, so two servers on one volume serialise their first builds (the second
  waits, then adopts) and never rebuild concurrently. Per-project collections
  (SW-01, below) keep different projects on one volume apart. On that date the
  entry stood open only for the cross-process read of a collection another
  process deletes: a `refresh` in one server still deleted and recreated the
  collection another server on the same volume was serving, and that server's
  doc search failed until it restarted, so the one-server-per-volume guidance
  above still held for serving.
  **2026-11-04 (remediation T05, daemon):** under `rlm-connect`
  ([docs/guide/daemon.md](../guide/daemon.md)) every IDE window on a project
  attaches through `rust-lsp mcp-bridge` to that project's one daemon
  container, `rlm-<ID>`, on its own volume, `rlm-<ID>-data`. The bridge opens no
  Chroma client, and no offline ingest command exists, so the daemon is the
  only writer on each project volume; the cross-process read above cannot
  arise between windows that use `rlm-connect`. It still applies to the
  per-session `docker run` config and to any second server started by hand on
  the same volume.
- **Resolved:** 2026-11-04 (remediation-2 T02). Live reproduction first, on
  the pre-fix image with the ripgrep fixture: two daemons, `r2-t02-a` and
  `r2-t02-b`, on one volume (`RLM_PROJECT_ID=r2probe`); `b` adopted `a`'s
  collection, then `rust-lsp refresh` on `b` swapped it. `a`'s `search-docs
  "regex syntax"` then returned `error` with `Documentation search error:
  Collection [e05a22ef-…] does not exist.` and `recovery: "unknown"` (the
  traceback ends in `chromadb.errors.NotFoundError` from `collection.count()`
  in `DocStore.search`), while `a`'s `status` read `doc_index_state: "ready"`
  with `doc_index_chunk_count: null`. Fix: the store keeps the live collection
  name from its last build or adopt, and when `search()` or `chunk_count()`
  gets `NotFoundError` from the snapshotted collection it re-opens the
  collection by that name and serves it if it carries `build_complete`.
  Otherwise it probes `<chroma_path>/.rlm-doc-store.lock` without blocking: a
  held flock (a rebuild mid-swap) reads not ready with the state unchanged; a
  free flock with the collection still gone sets `doc_index_state: "error"`
  with `the doc index collection was deleted by another process on this
  volume; call refresh`, so `search_docs` returns `error` with `recovery:
  "refresh"`. The same probe on the fixed image: `a`'s search after `b`'s
  `refresh` returned `ok` with 5 results, and `a` logged `doc_store: re-opened
  project_docs_ab332e26137a after another process replaced it`. Guarded by
  `TestCollectionDeletedByAnotherProcess` in
  [tests/test_doc_store.py](../../tests/test_doc_store.py), which runs the
  second client in a subprocess. Two servers on one volume still serialise
  their builds through the flock, and the one-writer guidance for serving
  stands; nothing in this entry remains open.

### SW-10 — `refresh` blocked on the doc re-embed after deleting the old index
- **Where:** [src/rust_lsp_mcp/doc_store.py](../../src/rust_lsp_mcp/doc_store.py)
  (`_rebuild_locked`, `start_doc_rebuild`, `clear_doc_store`) ·
  [src/rust_lsp_mcp/tools/refresh.py](../../src/rust_lsp_mcp/tools/refresh.py) ·
  [src/rust_lsp_mcp/tools/status.py](../../src/rust_lsp_mcp/tools/status.py).
- **What:** the 2026-11-03 defect sweep (SW-10, medium) timed `refresh` on ripgrep
  at 17.5 seconds, almost all of it the re-embed of every doc chunk, while the
  docstring called the rebuild fast. The rebuild deleted the live collection
  first, so `search_docs` returned `not_ready` for the whole pass and a failed or
  interrupted rebuild lost a good index.
- **Resolved:** 2026-11-04 (remediation T04). The rebuild writes
  `<live name>_staging` and, on success, deletes the live collection and renames
  staging to the live name (`collection.modify(name=...)`) under the read lock,
  carrying the `build_complete`, identity, `indexed_commit` and `doc_digest`
  stamps; on failure or a shutdown cancel it deletes staging and the live index
  keeps answering. Each chunk stores `file_sha256`, and a file whose hash matches
  the live collection has its chunks and embeddings copied, not re-embedded.
  `refresh` starts the rebuild on a background thread and returns
  (`doc_rebuild_started`); `status` reports `doc_index_progress`.
  `clear_doc_store()` cancels a running rebuild and waits up to 10 seconds
  (SW-L14). Guarded by `TestBuildAndSwap`, `TestIncrementalReembed` and
  `TestShutdownCancelsRebuild` in [tests/test_doc_store.py](../../tests/test_doc_store.py)
  and `TestRefreshDocRebuildNonBlocking` in
  [tests/test_refresh.py](../../tests/test_refresh.py).

### SW-05 — `find_symbol` returned no functions
- **Where:** [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py)
  (`PatchedRustAnalyzer._get_initialize_params`) ·
  [src/rust_lsp_mcp/tools/find_symbol.py](../../src/rust_lsp_mcp/tools/find_symbol.py).
- **What:** the 2026-11-03 defect sweep (SW-05, medium) found that multilspy's bundled
  initialize parameters set `workspace.symbol.search.kind` to `"only_types"` with a
  limit of 128, so a shared-prefix `find_symbol` query returned types only and never a
  function, and `truncated` could never become `true`.
- **Resolved:** 2026-11-04 (remediation T03). `_get_initialize_params` sets the kind to
  `"all_symbols"` and the limit to `MAX_LIST_RESULTS + 1` (201), one past the tool's
  cap. `find_symbol` sets `truncated` when rust-analyzer returned more than 200
  items, counted before it drops symbols outside the workspace (on ripgrep, 2 of
  the 201 for `"s"` were dependency crate roots, so the cap alone saw 199); `total`
  counts the in-workspace items and is then a lower bound. Guarded by
  [tests/test_analyzer_init_params.py](../../tests/test_analyzer_init_params.py) and
  `test_find_symbol_returns_functions_and_truncates` in
  [tests/test_phase2_integration.py](../../tests/test_phase2_integration.py).

### SW-06 — A killed rust-analyzer left `status` reading `ready`
- **Where:** [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py)
  (`AnalyzerManager._run`, `_hold_ready`, `_record_process_exit`) ·
  [src/rust_lsp_mcp/core.py](../../src/rust_lsp_mcp/core.py) (`require_ready`) ·
  [src/rust_lsp_mcp/envelope.py](../../src/rust_lsp_mcp/envelope.py).
- **What:** the 2026-11-03 defect sweep (SW-06, medium; also SW-L01) found that nothing
  watched the rust-analyzer process: after it died `status` kept reading `ready` (or
  `indexing` forever, for a death before quiescence), and every request failed with
  the "analyzer is still running, retry" message. Its stderr was dropped.
- **Resolved:** 2026-11-04 (remediation T03). `_run` races the `start_server()` entry,
  and after readiness `_shutdown_event`, against `lsp.server.process.wait()` with
  `FIRST_COMPLETED`; the process wait also reads `process.returncode` every 0.5 s,
  because on CPython 3.12 `Process.wait()` resolves only after every pipe closes and
  a proc-macro-srv child holding the inherited stderr pipe hid the death. An exit
  without a shutdown request sets `state` to `"error"`,
  `analyzer_exit_code`, and `error_message` to `rust-analyzer exited with code <n>;
  last stderr: <up to 20 lines>` (a shell exit 127, multilspy's `/bin/sh -c` launch
  of a missing binary, reads "rust-analyzer binary not found"). A replacement
  stderr reader keeps the last 200 lines, logs each at debug and survives a line
  past the 64 KiB stream limit. Gated tools, including a call already in flight
  at the death, return `rust-analyzer is not running (exited with
  code <n>); call refresh` (`recovery: "refresh"`), and `lsp_failure` defers to that
  envelope once `state` is `"error"`. `refresh` clears the exit code. Guarded by
  [tests/test_analyzer_death.py](../../tests/test_analyzer_death.py).

### SW-07 — A failed workspace load read `ready`
- **Where:** [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py)
  (`PatchedRustAnalyzer._on_server_status`, `AnalyzerManager._make_status_handler`) ·
  [src/rust_lsp_mcp/tools/status.py](../../src/rust_lsp_mcp/tools/status.py).
- **What:** the 2026-11-03 defect sweep (SW-07, medium) found that multilspy's
  `experimental/serverStatus` handler reads only `quiescent` and discards `health` and
  `message`, so a workspace that failed to load (for example an isolated container
  with an unprimed cargo cache) reported `ready` with no sign of the failure.
- **Resolved:** 2026-11-04 (remediation T03). `_get_initialize_params` rebinds the
  notification to `_on_server_status`, which keeps multilspy's readiness behaviour and
  passes `health` and `message` to the manager's `status_handler`, and drops
  multilspy's `cargo.cfgs = {}` (rust-analyzer wants a list and reported
  `"warning"` on every clean load, masking the signal); `status` reports
  `analyzer_health` and `analyzer_health_message`, and a change of health is logged at
  warning. Guarded by [tests/test_analyzer_init_params.py](../../tests/test_analyzer_init_params.py)
  and [tests/test_analyzer_error_state.py](../../tests/test_analyzer_error_state.py).

### SW-08 — A failed request kept its file open with a stale buffer
- **Where:** [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py)
  (`PatchedRustAnalyzer.open_file`).
- **What:** the 2026-11-03 defect sweep (SW-08, medium) found that multilspy's
  `open_file` has no `try/finally` around its `yield`: a request that raised or was
  cancelled left `open_file_buffers[uri].ref_count` above zero, so the file was never
  closed and later requests read its old text instead of the file on disk.
- **Resolved:** 2026-11-04 (remediation T03). The override wraps multilspy's
  `open_file`; on any exception from the body it decrements the count and sends
  `didClose` and removes the entry only when the count reaches zero, so a concurrent
  user of the same file keeps its buffer. Guarded by
  [tests/test_analyzer_open_file.py](../../tests/test_analyzer_open_file.py).

### SW-03 — A new file after a branch switch was not found without `refresh`
- **Where:** [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py)
  (`PatchedRustAnalyzer._get_initialize_params`, `AnalyzerManager._make_progress_handler`).
- **What:** the 2026-11-03 defect sweep (SW-03, high) found that multilspy's bundled
  initialize parameters set `files.watcher` to `"client"`: rust-analyzer registered a
  file watch with the client, multilspy accepted it and never sent a change event, so
  a `.rs` file added by a branch switch, a pull or an agent's edit stayed invisible to
  `find_symbol` until `refresh`.
- **Resolved:** 2026-11-04 (remediation T02). `_get_initialize_params` sets
  `files.watcher` to `"server"`, so rust-analyzer watches the tree itself, and binds
  `$/progress` to the manager's handler, which re-captures `indexed_commit` when a
  re-index ends while the analyzer is ready, so `stale` returns to `false` without
  `refresh`. Live probe under rootless podman with the ripgrep fixture bind-mounted
  `:ro,z`: after `git checkout` of a branch adding a new function, `find_symbol`
  found it on the first poll (0.8 s) and `status` read `stale: false` at the same
  poll. A switch that changes no Rust or Cargo file starts no re-index, so `stale`
  stays `true` until `refresh` (stated in the skill). Guarded by
  [tests/test_analyzer_init_params.py](../../tests/test_analyzer_init_params.py),
  [tests/test_analyzer_commit_recapture.py](../../tests/test_analyzer_commit_recapture.py)
  and [tests/test_freshness_integration.py](../../tests/test_freshness_integration.py).

### SW-04 — A doc index built on another branch was served as current
- **Where:** [src/rust_lsp_mcp/doc_store.py](../../src/rust_lsp_mcp/doc_store.py)
  (`doc_store_files`, `doc_store_digest`, `_rebuild_locked`, `_try_adopt`,
  `doc_store_freshness`) · [src/rust_lsp_mcp/tools/status.py](../../src/rust_lsp_mcp/tools/status.py).
- **What:** the 2026-11-03 defect sweep (SW-04, high) found that the adopt check
  compared only the project identity and the build sentinel, so a restart after a
  branch switch adopted the doc index built on the old branch, and `status` gave no
  sign that `search_docs` answered from another tree.
- **Resolved:** 2026-11-04 (remediation T02). A successful rebuild stamps the
  collection with `indexed_commit` (git `HEAD`, or `"none"`) and `doc_digest` (16 hex
  characters of SHA-256 over path, size and mtime of every file `doc_store_files`
  selects, the one helper that globs the doc patterns, so an excluded file never
  changes the digest). `_try_adopt` refuses on either difference and the startup
  thread rebuilds; `status` reports `doc_indexed_commit` and `doc_stale`. In the
  same live probe, `doc_stale` read `false` at ready and `true` after the branch
  switch. Guarded by `TestDocStoreFreshnessStamp` in
  [tests/test_doc_store.py](../../tests/test_doc_store.py) and
  `TestStatusFreshnessFields` in [tests/test_status.py](../../tests/test_status.py).

### SW-01 — Every project shared one doc index identity on a shared volume
- **Where:** [src/rust_lsp_mcp/doc_store.py](../../src/rust_lsp_mcp/doc_store.py)
  (`_project_fingerprint`, `_try_adopt`, the collection name) ·
  [README.md](../../README.md) (MCP client configs).
- **What:** the 2026-11-03 defect sweep (SW-01, critical) found that the DS-05
  fingerprint was only the resolved `project_root`, which is `/project` in every
  container, and that every container used the same collection name on
  `/data/chroma`. A container on one project adopted the doc index another project
  had built on the same volume and answered `search_docs` with that project's docs.
- **Resolved:** 2026-11-03 (remediation T01). A new `RLM_PROJECT_ID` setting names
  the project; when it is unset, empty or whitespace-only, `project_identity()`
  derives one from the resolved `project_root`, its git `remote.origin.url` and its
  root `Cargo.toml`'s `[package].name` (not the file bytes, so a version bump does
  not rename the collection). The identity is part of the fingerprint (`<project_root>|<identity>`),
  is stamped into the collection metadata as `project_identity` (checked on
  adopt), and suffixes the collection name (`<RLM_DOC_COLLECTION>_<12 hex>`), so
  projects sharing one volume keep separate collections. The README's configs now
  pass `-e RLM_PROJECT_ID=<project-name>` and a per-project volume, and
  `docker-compose.yml` takes `RLM_CONTAINER_NAME`, `RLM_DATA_VOLUME` and
  `RLM_PROJECT_ID` (SW-11). Guarded by the one-path-two-contents regression in
  [tests/test_project_identity.py](../../tests/test_project_identity.py).

### KI-12 — No version introspection
- **Where:** [src/rust_lsp_mcp/tools/status.py](../../src/rust_lsp_mcp/tools/status.py)
  · [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py) (`AnalyzerManager`).
- **What:** `status` surfaced no server, rust-analyzer, or multilspy version,
  leaving mismatched/stale analyzer binaries undiagnosable in-band.
- **Resolved:** 2026-07-03 (CLI-frontend Phase 3). `status`'s ok-envelope
  gained three fields: `server_version` and `multilspy_version`
  (`importlib.metadata.version("rust-lsp-mcp")` / `"multilspy"`, resolved
  fresh per call — cheap, in-process; degrade to `null` on
  `PackageNotFoundError`) and `rust_analyzer_version` (`<rust_analyzer_bin>
  --version` output, verbatim trimmed string — see
  [version-introspection-sources.md](../reference/version-introspection-sources.md)
  for why the LSP `serverInfo` route was dead). The rust-analyzer subprocess
  runs **once**, inside `AnalyzerManager.start()`, guarded by a
  `_rust_analyzer_version_captured` flag so `restart()`'s own call to
  `start()` does not re-invoke it; cached on the manager for the process
  lifetime. Any capture failure (missing binary, non-zero exit, timeout)
  degrades to `null`, never raises or delays startup meaningfully (5s
  timeout, thread-offloaded like the existing git-HEAD capture). Guarded by
  unit tests in `tests/test_status.py` (null-degradation, capture-once
  semantics via mock) and an integration assertion in
  `tests/test_phase34_integration.py` (`rust_analyzer_version` matches
  `^rust-analyzer\s` against the live container binary). CLI-side surfacing
  (`rust-lsp version`/`status`) is a separate phase of the same plan.
  Closes [#115](https://github.com/raymondshum/rust-lsp-mcp/issues/115).

### KI-9 — an in-flight nav delegate can hang across a `refresh` drain of a wedged analyzer
- **Where:** [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py) (the
  `request_*` delegates) + multilspy 0.0.15 `lsp_protocol_handler/server.py`
  (`send_request` waits on `request.cv`; `stop()` does not fail pending
  `_response_handlers`).
- **What:** If a navigation tool was awaiting `self._lsp.request_*(...)` at the moment a
  `refresh` (→`restart`) drained and tore down a **wedged/unresponsive** analyzer, the
  pending request never received a response and multilspy never cancels it on `stop()`,
  so the delegate await could hang indefinitely. Analyzer-side analog of the doc-store
  race **DS-12**. Tracked as **GitHub #87** (label `followup-2026-07-02`).
- **Resolved:** 2026-07-02. Every delegate now routes its LSP await through
  `AnalyzerManager._race_teardown`, which races the request against the run's
  `_shutdown_event` (set first by `_drain_task` on both `restart()` and `shutdown()`)
  and fails the in-flight request with `AnalyzerTornDownError`; all six tool call
  sites map it to a `not_ready` envelope (`TORN_DOWN_RETRY_MESSAGE`) — a refresh is
  genuinely in flight, so `not_ready` is truthful. External cancellation (client
  disconnect) still propagates as `CancelledError`, including across the helper's
  reap windows (adversarial finding, fixed in the same unit). **Rule for future
  delegates: every `self._lsp` await must go through `_race_teardown`** — a raw await
  reopens the hang. Deliberately NO wall-clock timeout on delegate awaits (a fixed
  timeout risks false `error`s on legitimately slow queries; the helper is the single
  seam if one is ever needed). Guarded by
  [tests/test_ki9_delegate_teardown.py](../../tests/test_ki9_delegate_teardown.py)
  (17 tests: teardown races, tie-breaks, cancellation discipline, envelope mapping,
  fail-fast, leak checks). Adversarial review: 1 finding (swallowed external cancel
  in the reap windows), fixed + regression-tested; re-verified `closed`.

### KI-4 — `RLM_CHROMA_MODEL_CACHE` is a no-op setting
- **Where:** [src/rust_lsp_mcp/settings.py](../../src/rust_lsp_mcp/settings.py).
- **What:** A `chroma_model_cache` settings field (env `RLM_CHROMA_MODEL_CACHE`)
  that did nothing — ChromaDB hardcodes the model cache to `~/.cache/chroma` and
  ignored it. A knob with no effect is a usability wart.
- **Resolved:** 2026-06-21 — removed the field from `settings.py` and `env.sample`;
  the fixed `~/.cache/chroma` model-cache path is now documented in prose in the
  [configuration guide](../guide/configuration.md) ("download once" section).

### KI-1 — Ghost script reference in the env-sample honesty test
- **Where:** [tests/test_env_sample_honesty.py](../../tests/test_env_sample_honesty.py) (docstring).
- **What:** The docstring referenced a `scripts/check-env-sample.py` that never
  existed; the test itself is the check.
- **Resolved:** 2026-06-21 in PR #23 — docstring corrected to say CI runs the test
  directly in the fast tier.

### KI-2 — Stale `UNVERIFIED` marker in hover
- **Where:** [src/rust_lsp_mcp/tools/hover.py](../../src/rust_lsp_mcp/tools/hover.py).
- **What:** A comment marked rust-analyzer's hover `contents` shape `UNVERIFIED`,
  but the Phase 3+4 gate confirmed it is `MarkupContent`.
- **Resolved:** 2026-06-21 in PR #23 — comment updated to the verified shape; the
  defensive normalization of the other documented shapes was kept.

### KI-5 — UTF-16 character offsets unhandled for non-ASCII target repos
- **Where:** [src/rust_lsp_mcp/analyzer.py](../../src/rust_lsp_mcp/analyzer.py)
  (`PatchedRustAnalyzer._get_initialize_params`).
- **What:** LSP positions default to UTF-16 code units, and multilspy advertised
  only `["utf-16"]`, so on non-ASCII (astral) lines `find_symbol`,
  `goto_definition`, `find_references`, and `hover` returned `character` values
  off by the surrogate count — a real correctness bug once the project became
  repo-agnostic (ripgrep's all-ASCII source had hidden it).
- **Resolved:** 2026-06-21 (Approach A). `PatchedRustAnalyzer` now advertises
  `positionEncodings: ["utf-32","utf-16"]`, so rust-analyzer reports **Unicode
  codepoint** offsets (verified supported — [lsp-position-encoding.md](../reference/lsp-position-encoding.md)).
  No transcoding; `positions.py` stays pure ±1. Guarded by
  [tests/test_ki5_position_encoding.py](../../tests/test_ki5_position_encoding.py)
  (unit: the negotiated list; integration: output + input side codepoint-correct
  on an astral-emoji fixture). Adversarial review: `no-breaks`.

### KI-3 — Node.js not declared in the dev container, but tasks use it
- **Where:** [.devcontainer/devcontainer.json](../../.devcontainer/devcontainer.json) · [.vscode/tasks.json](../../.vscode/tasks.json).
- **What:** The optional MCP Inspector tasks run `npx`, which needs Node.js, but no
  Node.js dev-container feature was declared, so they failed out of the box.
- **Resolved:** 2026-06-21. Declared the `ghcr.io/devcontainers/features/node:1`
  feature (`version: lts`, Node 22.x — satisfies the Inspector's Node >= 22.7.5);
  the development-guide note now says the tasks work out of the box.

### KI-6 — ripgrep-specific claim in the `status` tool docstring
- **Where:** [src/rust_lsp_mcp/tools/status.py](../../src/rust_lsp_mcp/tools/status.py) ~line 42.
- **What:** The docstring stated "For the pinned ripgrep clone (no active
  development commits) this is effectively always ready and not stale" — false
  for an actively-developed target project.
- **Resolved:** 2026-06-21 in PR #12 (Phase 2 of the
  [repo-agnostic plan](../planning/repo-agnostic-and-docker-launch.md)). The
  ripgrep-specific sentence was replaced with a repo-agnostic description of the
  staleness semantics.
