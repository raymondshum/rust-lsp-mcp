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

### KI-11 — Out-of-workspace navigation degrades to a misleading `not_found`
- **Where:** [src/rust_lsp_mcp/core.py](../../src/rust_lsp_mcp/core.py)
  `location_to_external` (~line 364) · [src/rust_lsp_mcp/tools/goto_definition.py](../../src/rust_lsp_mcp/tools/goto_definition.py)
  (the `mapped is None` skip at line 143, falling through to the same
  `not_found` at line 150 as a genuine zero-result answer).
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
- **Status:** open. Tracked as
  [#114](https://github.com/raymondshum/rust-lsp-mcp/issues/114).
  Reference: docs/audit/2026-07-02-usability-review.md,
  Cross-cutting findings, completeness critic (CC-2).

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
  separate volume names if two servers are truly needed). Entry stays open
  until a stronger guard (e.g. an on-disk lock or per-mode volumes by default)
  is decided.
  **2026-07-03 (CLI-frontend Phase 4):** the compose flip landed —
  [docker-compose.yml](../../docker-compose.yml)'s entrypoint now runs the
  daemon directly (`RLM_TRANSPORT=streamable-http`), the old `sleep infinity`
  + `docker exec … rust-lsp-mcp` stdio warm-start path is retired, and the
  single-writer mandate is spelled out in the compose header comment, the
  README's [CLI access](../../README.md#cli-access-for-agents-without-mcp)
  section, and [docs/guide/cli.md](../guide/cli.md)'s troubleshooting table.
  Entry remains open per the stronger-guard note above.

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

---

## Resolved

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
