# Defect sweep — 2026-07-01

A whole-project audit for defects, doc/code inconsistencies, deviations from
stated abilities, and implementation gaps. Run as a multi-agent sweep: seven
parallel finders (core runtime, MCP tool layer, doc-RAG, docs-vs-code claims,
infrastructure/scripts, test quality, security/robustness), each finding then
checked by an independent adversarial verifier instructed to refute it against
the actual code.

- **Branch audited:** `bob_prototype`
- **Raw findings:** 34 → **31 confirmed** after verification, **1 refuted**,
  2 recovered from agents that died on API overload (the security finder and
  one verifier) and hand-verified afterward.
- **Merged for this register:** duplicate findings that name the same defect
  from different angles are collapsed (e.g. the dead `RLM_CARGO_*` knobs
  surfaced from three finders; the `refresh`/`search_docs` race from two).

IDs are `DS-NN`. Each entry records **Where**, **What**, **Why it matters**,
and the **GitHub issue** tracking it.

> Scope note: this is a point-in-time audit record, distinct from the living
> [known-issues register](../impl/known-issues.md). Design/doc-drift entries
> here that need a carried decision should also be reflected there.

---

## Summary table

| ID | Sev | Area | One-line | Issue |
|----|-----|------|----------|-------|
| DS-01 | High | security | Unvalidated `file` param → arbitrary file read (path traversal) | #45 ✅ |
| DS-02 | High | tools | Out-of-workspace definitions leak as `..`-prefixed "relative" paths | #46 ✅ |
| DS-03 | High | core | `refresh` during indexing sticks state at a stale `ready` | #47 ✅ |
| DS-04 | High | core | Drain-timeout cancel orphans the rust-analyzer subprocess | #48 ✅ |
| DS-05 | High | rag | Doc store adopts an existing collection with zero freshness check | #49 ✅ |
| DS-06 | High | tests | The real `init_doc_store()` is executed by no test | #50 ✅ |
| DS-07 | Med | core | Failed analyzer startup is swallowed — `indexing` forever | #51 ✅ |
| DS-08 | Med | core | Blocking doc rebuild on the event loop during lifespan startup | #52 ✅ |
| DS-09 | Med | tools | `document_symbols` returns `range.start`, not `selectionRange` | #53 ✅ |
| DS-10 | Med | rag | `---` after a closing code fence misparsed as a setext header | #54 ✅ |
| DS-11 | Med | rag | Doc starting with `---` swallowed whole as frontmatter | #55 ✅ |
| DS-12 | Med | rag | `refresh` rebuild races in-flight `search_docs` (no lock) | #56 ✅ |
| DS-13 | Med | infra | `RLM_CARGO_*` / `RLM_RUST_ANALYZER_TARGET_DIR` are dead knobs | #57 ✅ |
| DS-14 | Med | docs | `status` can't report doc-index readiness; recovery path loops | #58 ✅ |
| DS-15 | Med | infra | `setup.sh` disables host-global git commit signing | #59 ✅ |
| DS-16 | Med | infra | `status` git-staleness always null on rootful Linux Docker | #60 ✅ |
| DS-17 | Med | tests | Malformed-LSP-response branch has zero CI coverage | #61 ✅ |
| DS-18 | Med | tests | `_lifespan` / `analyzer_lifespan` have zero coverage | #62 ✅ |
| DS-19 | Low | core | `status` runs `subprocess.run` synchronously on the event loop | #63 |
| DS-20 | Low | tools | Null `documentSymbol` → `error` envelope; `None`-check is dead code | #63 |
| DS-21 | Low | core | Concurrent `refresh` not serialized → duplicate analyzer processes | #63 ✅ |
| DS-22 | Low | tools | `search_docs` accepts empty/whitespace query, returns arbitrary top-k | #63 ✅ |
| DS-23 | Low | rag | Indented (1–3 space) fences/headers missed, swallowing later headers | #63 ✅ |
| DS-24 | Low | rag | Empty-corpus `build_complete` sentinel is dead code | #63 |
| DS-25 | Low | docs | Model-persistence advice contradicts the baked-model design | #63 ✅ |
| DS-26 | Low | infra | Dockerfile comment claims RA reads `RA_TARGET_DIR`; it doesn't | #63 ✅ |
| DS-27 | Low | infra | `prime-cache.sh` SELinux relabel applied only to project mount | #63 |
| DS-28 | Low | tests | No test asserts tools are actually registered on the app | #63 ✅ |

Issues #45–#63 track these findings (DS-19…DS-28 are consolidated in roll-up #63).

---

## High

### DS-01 — Unvalidated `file` parameter enables arbitrary file read (path traversal)
- **Where:** `src/rust_lsp_mcp/tools/goto_definition.py:72`,
  `hover.py:112`, `find_references.py:111`, `document_symbols.py`.
- **What:** All four position tools forward the client-supplied `file`
  argument to the manager delegate with only line/character validation — no
  path validation. multilspy joins it via
  `str(PurePath(repository_root_path, relative_file_path))`. Verified locally:
  an absolute `file` (`/etc/hostname`) discards the root entirely, and
  `../../etc/hostname` joins to `…/ripgrep/../../etc/hostname`, which resolves
  outside the workspace. multilspy then `FileUtils.read_file`s the path and
  sends the contents to rust-analyzer via `didOpen`; `document_symbols` returns
  the outline and `hover` returns signatures/doc-comments from that file.
- **Why it matters:** For a server advertised as read-only navigation of a
  single workspace, this is an arbitrary-file-read primitive across the
  container filesystem. The output side (`core._uri_to_relative_path`) carries
  an explicit `..`-containment security comment, so the workspace boundary is
  clearly an intended invariant that the *input* side does not enforce.
- **Fix direction:** normalize and reject any `file` resolving outside
  `project_root` before calling the delegate; add tests for absolute and
  `..`-escaping inputs.
- **Resolved:** 2026-07-01 (PR #65, fixed with DS-02 as one containment change).
  Shared purely-lexical guard `core.validate_workspace_file`/`_is_contained_relpath`
  rejects absolute/`..`-escaping/empty/NUL `file` with an `error` before the
  delegate, and forwards the normalized path (defuses symlink+`..` laundering).
  Adversarial pass `no-breaks`; the in-workspace-symlink limitation is documented
  honestly in `tools.md`.

### DS-02 — Out-of-workspace definitions leak as `..`-prefixed "workspace-relative" paths
- **Where:** `src/rust_lsp_mcp/core.py:181` (`location_to_external`).
- **What:** The mapper trusts multilspy's pre-populated `relativePath` and only
  falls back to the containment-checking `_uri_to_relative_path` when it is
  falsy. But multilspy 0.0.15 *always* populates `relativePath` via
  `os.path.relpath`, which on Linux never returns `None` and yields e.g.
  `../../usr/local/rustup/.../alloc/src/vec/mod.rs` for stdlib/dependency
  symbols. So `goto_definition`/`find_references` return `ok` with a bogus
  "workspace-relative" path, defeating the documented `..`-escape guard. The
  repo's tests only exercise the `relativePath: None` shape, so the bypass is
  untested.
- **Why it matters:** Everyday trigger (`goto_definition` on any `Vec::push`).
  Violates the documented `file: workspace-relative path` contract, and pairs
  with DS-01 — a caller feeding the returned path back into another tool hits
  the traversal. Also inconsistent with `find_symbol`, which silently *skips*
  out-of-workspace results.
- **Resolved:** 2026-07-01 (PR #65). `location_to_external` now containment-checks
  `relativePath`; an out-of-workspace value falls back to the URI (also checked) or
  is skipped, matching `find_symbol`. `goto_definition` all-skipped → `not_found`,
  `find_references` all-skipped → `ok`+empty (each per its documented envelope).

### DS-03 — `refresh`/`restart` during indexing leaves state stuck at a stale `ready`
- **Where:** `src/rust_lsp_mcp/analyzer.py:489` (`restart`), `_run` at `:254`.
- **What:** `restart()` sets `STATE_INDEXING` once, then `await _drain_task()`.
  `_run` only ever sets `STATE_READY` and never re-asserts `INDEXING`. If
  `refresh` (which has no readiness gate) is called while the old `_run` is
  mid-index and that task reaches quiescence during the 10 s drain window, it
  stamps `STATE_READY` *after* `restart` set `INDEXING`, then exits. After the
  drain, `state == "ready"` while the new task is only beginning to index; the
  new `_run` also recaptures `indexed_commit`, so `stale` reads `false`.
- **Why it matters:** `status`/`analyzer_status` report `ready` (`stale=false`)
  while every navigation tool returns `not_ready` for the entire re-index
  (`is_ready` requires `_lsp is not None`). The status tool and the readiness
  gate contradict each other, breaking the documented poll-until-ready
  protocol for a potentially minutes-long window on large crates.
- **Resolved:** 2026-07-02 (PR #67, fixed with DS-04/DS-21 as one lifecycle cluster).
  `_drain_task` bumps a generation counter; `_run(gen)` publishes `_lsp`/`state=ready`/
  `_ready_event` only while it is the current generation (one synchronous block), so a
  superseded run can never leave a stale `ready`. `restart()` re-asserts `indexing`
  after the drain. Adversarial `no-breaks` (300-round race storm).

### DS-04 — Drain-timeout cancel path orphans the rust-analyzer subprocess
- **Where:** `src/rust_lsp_mcp/analyzer.py:440` (`_drain_task`).
- **What:** On the 10 s timeout, `_drain_task` cancels the task and suppresses
  the result. If the task is still inside `start_server().__aenter__`
  (awaiting quiescence — the dominant phase of indexing), cancellation abandons
  the context manager. In multilspy 0.0.15 the teardown (`shutdown()` /
  `stop()` → `process.terminate()/kill`) is straight-line code after the
  `yield` with **no `try/finally`**, so on cancellation neither runs; `_run`'s
  own `finally` only clears `_lsp`.
- **Why it matters:** Calling `refresh` (or shutting down) while a non-trivial
  crate is still indexing — the common case — leaks a live rust-analyzer
  process that keeps running `cargo check`, consuming CPU and multiple GB of
  RAM. Repeated refreshes during warm-up accumulate orphans until the container
  OOMs.
- **Resolved:** 2026-07-02 (PR #67). `_run`'s `finally` clears `_lsp` (identity-guarded)
  then idempotently calls `lsp.server.stop()` — multilspy's own teardown — so the
  subprocess is terminated on every reachable cancel window. Guarded by a real-sentinel
  subprocess regression test.

### DS-05 — Doc store adopts any populated collection with zero freshness checking
- **Where:** `src/rust_lsp_mcp/doc_store.py:274` (`init_doc_store` adopt gate).
- **What:** Adoption requires only `existing.count() > 0 and
  meta.get("build_complete")`. No file mtimes, content hashes, file list, glob
  patterns, or `project_root` are recorded or compared. `doc_collection`
  defaults to `"project_docs"` and `chroma_path` is a persistent mount.
- **Resolved:** 2026-07-02 (PR #69, scope = cross-project only). `rebuild()` stamps the
  resolved `project_root` into collection metadata; `init_doc_store` adopts only when the
  stored fingerprint matches the current project, else rebuilds. Build-once persistence is
  preserved; stale-after-edit remains the documented trade-off (out of scope).
- **Why it matters:** Repointing `RLM_PROJECT_ROOT` at a different project
  while keeping the default collection/path — the README's documented
  repo-agnostic flow with one shared volume — makes `search_docs` return the
  *previous* project's docs wholesale, marked ready, silently. Edited/deleted
  docs also persist stale until a manual `refresh` (this half is a documented
  trade-off; the cross-project contamination is not).

### DS-06 — The real `init_doc_store()` is executed by no test
- **Where:** `tests/test_doc_store.py:317`.
- **What:** Tests call
  `init_doc_store.__wrapped__(...) if hasattr(...) else _init_with_fake_ef(...)`.
  `init_doc_store` is undecorated, so `__wrapped__` never exists and every
  "singleton lifecycle" test runs `_init_with_fake_ef` — a hand-copied
  reimplementation that even performs its own `_mod._doc_store = store`. The
  adopt gate, interrupted-build fallback, `NotFoundError`/`Exception` handling,
  and singleton assignment at `doc_store.py:263-301` have **zero** coverage.
- **Why it matters:** Inverting the adopt condition (re-embed every restart),
  dropping the sentinel check (adopt a partial collection), or forgetting the
  singleton assignment (`search_docs` permanently `not_ready`) all pass CI
  green. Verified by mutation.
- **Resolved:** 2026-07-02 (PR #69). `init_doc_store` gained an optional
  `embedding_function` param (forwarded to `DocStore`/`get_collection`) so tests exercise
  the REAL function offline; `_init_with_fake_ef` and the dead `__wrapped__` ternary are
  removed. Adopt gate, sentinel fallback, and singleton assignment are now mutation-guarded.

---

## Medium

### DS-07 — Failed analyzer startup is swallowed: `indexing` forever, no error surfaced
- **Where:** `src/rust_lsp_mcp/analyzer.py:262`.
- **What:** `_run`'s failure path is `except Exception: _log.exception(...);
  raise`. The exception lives in the background task and is only retrieved at
  shutdown; `state` stays `STATE_INDEXING`. There is no `error` state in the
  vocabulary. Realistic triggers: a wrong `RLM_RUST_ANALYZER_BIN`, or
  multilspy's strict `initialize` assert against a newer native rust-analyzer.
- **Why it matters:** With a misconfigured binary or incompatible version, the
  server starts cleanly and answers every navigation call `not_ready`/"retry"
  forever; clients loop on `status` indefinitely with only a stderr log as
  evidence.
- **Resolved:** 2026-07-02 (PR #71). `_run` sets `STATE_ERROR` (gen-guarded) with a
  recorded reason; `require_ready()` returns an `error` envelope (not `not_ready`) so
  every gated tool surfaces it; `status`/`analyzer_status` report `state="error"`.
  `refresh` clears the error and re-indexes; `_drain_task` drains a failing outgoing run
  so a single refresh recovers.

### DS-08 — Blocking doc rebuild on the event loop during lifespan startup
- **Where:** `src/rust_lsp_mcp/core.py:56`; also `docs/guide/architecture.md:195`
  and `docs/guide/tools.md` ("`status` … Always callable").
- **What:** `_lifespan` calls `init_doc_store(get_settings())` synchronously
  before `yield`. On first run `rebuild()` — documented as "Synchronous/blocking
  … run it via a worker thread", and which `refresh.py` *does* offload — chunks
  and embeds the whole corpus inline on the loop. FastMCP enters the lifespan
  before serving, so `initialize` and even `status` are blocked until the build
  finishes.
- **Why it matters:** On a doc-heavy project with an empty chroma volume, the
  client gets no responses until the corpus is embedded; clients with startup
  timeouts (e.g. Claude Desktop) can declare the server dead. Contradicts the
  architecture doc's "available to clients immediately" claim.
- **Resolved:** 2026-07-02 (PR #71). `init_doc_store` splits into a cheap synchronous
  prepare (construct + publish singleton + adopt check) and a background thread for
  `rebuild()`; the loop serves `initialize`/`status` immediately with
  `doc_index_state="building"`. architecture.md updated to match.

### DS-09 — `document_symbols` reports `range.start` instead of `selectionRange`
- **Where:** `src/rust_lsp_mcp/core.py:252` (`symbol_to_external` doc-symbol branch).
- **What:** The branch reads `sym["range"]["start"]`. Per LSP, `range` includes
  leading doc comments and `#[attributes]`; the name position is
  `selectionRange`, which multilspy preserves but this code never reads.
- **Why it matters:** For `/// docs\npub fn foo`, positions land on the comment
  line. Feeding them back into `hover`/`goto_definition`/`find_references` — the
  intended workflow — returns `not_found`, and they disagree with `find_symbol`
  for the same symbol.
- **Resolved:** 2026-07-02 (PR #73). The document-symbol branch now prefers
  `selectionRange.start` (the name position), falling back to `range.start`;
  multilspy 0.0.15 preserves `selectionRange` verbatim.

### DS-10 — `---` after a closing code fence misparsed as a setext header
- **Where:** `src/rust_lsp_mcp/doc_chunking.py:347`.
- **What:** The fence-close branch sets `prev_body_stripped` to the ` ``` `
  line, and `_could_be_setext_preceding("```")` returns `True`, so a following
  `---` is treated as a setext-h2 underline: the closing fence is popped from
  the body and becomes a bogus header title. Reproduced.
- **Why it matters:** A common pattern (code block directly followed by a `---`
  rule, no blank line) yields a code chunk with an unclosed fence and a phantom
  section whose breadcrumb is `` … > ``` ``, polluting embeddings and
  breadcrumbs for the rest of that subtree.
- **Resolved:** 2026-07-02 (PR #75). Fence delimiter lines (open and close) are no
  longer setext-eligible, so a `---`/`===` after a closing fence is ordinary body.

### DS-11 — Doc whose first non-empty line is `---` is swallowed whole as frontmatter
- **Where:** `src/rust_lsp_mcp/doc_chunking.py:293`.
- **What:** A leading `---` sets `in_frontmatter = True`; the only exit is a
  later `---`/`...`. Per CommonMark a leading `---`+blank line is a thematic
  break, not frontmatter, and unterminated frontmatter has no closer. Reproduced:
  header splitting never runs for the whole file.
- **Why it matters:** Any doc starting with a horizontal rule (or unterminated
  frontmatter) loses all header structure and breadcrumbs, degrading retrieval
  for that file.
- **Resolved:** 2026-07-02 (PR #75). A pre-scan treats a leading `---` as frontmatter
  only when it is a compact contiguous block with a closing `---`/`...`; a thematic
  break or unterminated block falls through to normal header splitting.

### DS-12 — `refresh` rebuild races in-flight `search_docs` (no lock)
- **Where:** `src/rust_lsp_mcp/doc_store.py:81` (`rebuild`);
  `tools/search_docs.py:68`; `tools/refresh.py:67`.
- **What:** `search_docs` checks `is_ready` on the loop, then runs `search` on a
  worker thread; `refresh` runs `rebuild` on another thread with no lock.
  `rebuild` sets `_ready=False; _collection=None`, deletes + recreates the
  (empty) collection, then batch-adds. A search that passed the gate just before
  the flip can see `_collection is None` (→ misleading `not_found`), a
  half-built collection (→ partial `ok`), or the deleted collection
  (→ `error`) — exactly the outcomes the docstring says are impossible.
  Concurrent `refresh` calls also collide (create on existing name / add to
  deleted collection).
- **Why it matters:** Violates the documented "never a misleading empty/partial
  answer mid-rebuild" invariant. (The `search_docs.py:75` low-severity finding
  is the same race from the tool side.)
- **Resolved:** 2026-07-02 (PR #77). A `_read_lock` makes `search()`'s readiness
  check + collection snapshot + query atomic w.r.t. `rebuild()`'s brief
  state/collection transitions (delete under the lock; long build lockless with
  `_collection` kept None); a search mid-rebuild raises `DocStoreNotReady` →
  `not_ready`. Concurrent refresh doc-store re-init serialized.

### DS-13 — `RLM_CARGO_TARGET_DIR` / `RLM_CARGO_HOME` / `RLM_RUST_ANALYZER_TARGET_DIR` are dead knobs
- **Where:** `src/rust_lsp_mcp/settings.py:63`; `docs/guide/configuration.md:39`;
  `env.sample:21`; `Dockerfile:83`.
- **What:** The fields exist and are documented as functional (persistence /
  cache relocation), but no code reads them — `analyzer.py` passes only
  `rust_analyzer_bin` and `project_root` to the subprocess, and nothing sets a
  `targetDir` init option or exports env. Real relocation comes from the
  unprefixed `CARGO_*`/`RA_TARGET_DIR` vars set at the container level;
  `rust-analyzer.cargo.targetDir` is wired only for the VS Code extension, not
  the server's analyzer.
- **Why it matters:** Setting any of the three per the docs is a silent no-op
  (`extra="ignore"` also hides typos). In the devcontainer the knob is fiction.
- **Resolved:** 2026-07-02 (PR #79). Removed the three dead `Settings` fields, their
  `env.sample`/`configuration.md` entries, and the dead `RLM_CARGO_*` Dockerfile ENV
  lines; the persistence note now documents the real container-level `CARGO_*` mechanism.

### DS-14 — `status` can't report doc-index readiness; documented recovery loops forever
- **Where:** `docs/guide/tools.md:315`; `src/rust_lsp_mcp/tools/status.py`;
  `tools/search_docs.py`; `tools/refresh.py`.
- **What:** tools.md says the doc-index `not_ready` state "can be checked with
  `status`", but `status` returns only analyzer fields (`state`,
  `indexed_commit`, `current_commit`, `stale`) — no `DocStore` field. After a
  doc-store init failure (swallowed in `_lifespan`), `get_doc_store()` stays
  `None`, `search_docs` returns `not_ready` forever, and `refresh` skips the
  rebuild when the store is `None` and returns `ok` without re-initializing.
- **Why it matters:** An agent following the documented "poll `status` until
  ready, then retry" path loops indefinitely; no exposed tool distinguishes
  "rebuilding" from "permanently unavailable".
- **Resolved:** 2026-07-02 (PR #71, with DS-07/DS-08). `status` now reports
  `doc_index_state` (building/ready/error) + `doc_index_error`; `search_docs` returns
  `error` on permanent failure (not eternal `not_ready`); `refresh` re-initialises an
  absent/errored store instead of skipping — so the documented poll-then-refresh recovery
  terminates. tools.md updated so "check with `status`" names the field.

### DS-15 — `setup.sh` disables host-global git commit signing when run outside the container
- **Where:** `scripts/setup.sh:34`; `scripts/teardown.sh:19-22,62`.
- **What:** `git config --global commit.gpgsign false` runs unconditionally with
  no container guard, while `teardown.sh` explicitly supports host execution and
  tells users to run `setup.sh` afterward.
- **Why it matters:** A developer running teardown on the host then following its
  own instruction to re-run setup gets host-wide commit signing silently turned
  off — every subsequent commit in every repo unsigned, defeating a
  deliberately configured control.
- **Resolved:** 2026-07-02 (PR #81). The signing-disable is now gated behind an
  `_in_container` guard (detects `/.dockerenv`, `/run/.containerenv`, devcontainer/CI
  env markers); on the host it is skipped with a note.

### DS-16 — `status` git-staleness always null on rootful Linux Docker
- **Where:** `Dockerfile:80` (no `USER`, no `safe.directory`); `analyzer.py`
  `_capture_head_commit`; `tools/status.py` `_git_head`.
- **What:** The image runs as root; `/project` is bind-mounted host-uid-owned.
  Since git 2.35.2, `git -C /project rev-parse HEAD` fails with "dubious
  ownership" unless `safe.directory` is set — nothing configures it. Both call
  sites swallow the failure to `None`.
- **Why it matters:** In the primary documented deployment (rootful Docker on
  Linux), `indexed_commit`/`current_commit`/`stale` are permanently null, so
  `stale` never flips and an agent relying on it never re-indexes. One-line fix
  (`git config --system safe.directory`).
- **Resolved:** 2026-07-02 (PR #81). The Dockerfile runs
  `git config --system --add safe.directory /project` so rootful git on the host-uid
  bind mount succeeds and staleness reporting works.

### DS-17 — Malformed-LSP-response branch has zero CI coverage
- **Where:** `tests/test_phase34_adversarial.py:228`; `src/rust_lsp_mcp/analyzer.py:83`.
- **What:** analyzer.py claims "the adversarial regression tests guard both
  branches", but the tests that exercise the malformed-non-null-response →
  `error` path are `@pytest.mark.integration` ("Never runs in CI"), while CI
  runs `-m "not integration"`. They inject a fake `_MalformedLSP` and don't
  actually need a live analyzer. The fast tier only exercises the null branch.
- **Why it matters:** If discrimination regresses to a blanket
  `except AssertionError: return None` (the exact historical bug), genuine
  protocol failures get reported as `not_found` and CI stays green. Verified by
  mutation.
- **Resolved:** 2026-07-02 (PR #83). `_run_with_fake_lsp` now builds a fake-ready
  manager (no live analyzer), so the two malformed-response tests run in the fast
  tier; mutation-verified they fail under the blanket-catch regression.

### DS-18 — `_lifespan` / `analyzer_lifespan` have zero test coverage
- **Where:** `src/rust_lsp_mcp/core.py:55`.
- **What:** No test references `_lifespan` or `analyzer_lifespan`; all tool tests
  patch `core._manager` directly. The load-bearing "doc-store init failure is
  swallowed; nav tools continue" contract and the teardown wiring are untested.
- **Why it matters:** A refactor that lets `init_doc_store`'s exception propagate
  out of `_lifespan` would crash startup whenever ChromaDB is unavailable —
  violating the documented guarantee — with no failing test.
- **Resolved:** 2026-07-02 (PR #83, atop the readiness unit's test_lifespan_startup.py).
  Fast-tier tests now assert a doc-store init failure is swallowed and nav continues,
  and that analyzer_lifespan wires start→yield→shutdown.

---

## Low

### DS-19 — `status` runs `subprocess.run` synchronously on the event loop
- **Where:** `src/rust_lsp_mcp/tools/status.py:77`.
- **What:** `status()` is a plain `def` tool and `_git_head` calls
  `subprocess.run(["git", …])` synchronously; the pinned MCP SDK runs non-async
  tools inline (no thread offload). `AnalyzerManager._capture_head_commit` wraps
  the identical call in `asyncio.to_thread`.
- **Why it matters:** Every `status` poll blocks the loop for a git fork+exec;
  on a cold/slow FS this stalls concurrent tool handling and the rust-analyzer
  pump — on the hottest polling path.

### DS-20 — Null `documentSymbol` → `error` envelope; `None`-check is dead code
- **Where:** `src/rust_lsp_mcp/analyzer.py:316`.
- **What:** `request_document_symbols` does `if result is None: return []`, but
  multilspy asserts `isinstance(response, list)` and raises `AssertionError` on
  a JSON-RPC null instead of returning `None`. Unlike `request_definition`/
  `request_references`, there is no `_is_null_response_assertion`
  normalization, so it propagates to the tool's `except Exception` → `error`.
- **Why it matters:** A legal LSP null result becomes a confusing `error`
  instead of the documented `ok`+`symbols=[]`, inconsistent with the other
  delegates.

### DS-21 — Concurrent `refresh` not serialized → duplicate analyzer processes
- **Where:** `src/rust_lsp_mcp/analyzer.py:497` (`restart`).
- **What:** `restart()` has no lock and contains real await points. Two
  concurrent `refresh` calls can both pass the drain, then each call
  `start()`, whose `asyncio.create_task` overwrites `self._task` — orphaning the
  first `_run` and its subprocess (untracked by `_drain_task`).
- **Why it matters:** Two live rust-analyzer processes; whichever readies last
  wins the `_lsp` slot, so tools may talk to an analyzer indexed against a
  different commit than `indexed_commit` reports. (Related to DS-03/DS-04.)
- **Resolved:** 2026-07-02 (PR #67, with DS-03/DS-04). An `asyncio.Lock` now serializes
  `restart()`/`shutdown()`, so concurrent refreshes cannot overwrite `self._task`; a
  `_closed` flag makes refresh-after-shutdown a no-op. (Issue #63 remains open until the
  other roll-up lows land.)

### DS-22 — `search_docs` accepts empty/whitespace query, returns arbitrary top-k
- **Where:** `src/rust_lsp_mcp/tools/search_docs.py:75`.
- **What:** Only `limit = max(1, limit)` is validated; `query` is forwarded
  unchecked and embedded verbatim, so `query=""` returns `k` essentially
  arbitrary chunks with status `ok`.
- **Why it matters:** A client templating bug yields a confident `ok` full of
  unrelated docs instead of an `error` — unlike position tools, which reject
  degenerate input.
- **Resolved:** 2026-07-02 (PR #77, with DS-12). `search_docs` rejects an
  empty/whitespace query with an `error` envelope before any index round-trip.
  (Issue #63 stays open for the other lows.)

### DS-23 — Indented (1–3 space) fences/headers missed, swallowing later headers
- **Where:** `src/rust_lsp_mcp/doc_chunking.py:191`.
- **What:** `_FENCE_RE` and `_HEADER_RE` anchor at column 0 while CommonMark
  allows 1–3 leading spaces. Reproduced: an indented closing fence is missed,
  `in_fence` never closes, and every subsequent header is treated as fence body.
- **Why it matters:** Valid markdown destroys section structure/breadcrumbs for
  all following content in that file.
- **Resolved:** 2026-07-02 (PR #75, with DS-10/DS-11). `_HEADER_RE`/`_FENCE_RE` allow
  0–3 leading spaces (4+ stays indented code). (Issue #63 stays open for the other lows.)

### DS-24 — Empty-corpus `build_complete` sentinel is dead code
- **Where:** `src/rust_lsp_mcp/doc_store.py:165`.
- **What:** The empty-corpus path writes the sentinel "so the adopt branch
  recognises an intentionally-empty corpus", but the adopt condition requires
  `count() > 0`, which a count-0 collection can never satisfy.
- **Why it matters:** Empty corpora rebuild on every startup (harmless but
  wasted), and the comment misleads maintainers.

### DS-25 — Model-persistence advice contradicts the baked-model design
- **Where:** `docs/guide/configuration.md:59`.
- **What:** The persistence note tells users to persist `~/.cache/chroma` via
  "the production image's `/data` volume", but the Dockerfile sets `HOME=/opt/rlm`
  and warms the model at build time onto a deliberately non-volume path; the
  README says the model is baked in and needs no runtime download.
- **Why it matters:** A reader could try to pre-seed/wipe the model on `/data`
  or expect `/data` to supply it — a misunderstanding that would surface as a
  hard failure under `--network none`.
- **Resolved:** 2026-07-02 (PR #79). The persistence note now splits dev-container
  (download-once to a `~/.cache/chroma` bind mount) from the production image (model
  baked at build onto `HOME=/opt/rlm`; nothing to persist on `/data`).

### DS-26 — Dockerfile comment claims rust-analyzer reads `RA_TARGET_DIR`; it doesn't
- **Where:** `Dockerfile:99`.
- **What:** The comment "rust-analyzer / cargo read these from the environment"
  is false for `RA_TARGET_DIR` — RA has no such env var; it works only in the
  devcontainer via VS Code settings interpolation, absent in the production
  image where RA is launched by multilspy with only `positionEncodings` overridden.
- **Why it matters:** RA's check output lands in `/data/cargo-target`, not the
  `…/rust-analyzer` subdir the comment claims; the stated separation silently
  doesn't exist.
- **Resolved:** 2026-07-02 (PR #79). Corrected the Dockerfile comment (RA has no
  `RA_TARGET_DIR` env var; it is only honored in the dev container via VS Code settings)
  and removed the inert `RA_TARGET_DIR` ENV line; RA's output lands under
  `CARGO_TARGET_DIR`. (Issue #63 stays open for the other lows.)

### DS-27 — `prime-cache.sh` SELinux relabel applied only to the project mount
- **Where:** `scripts/prime-cache.sh:90`.
- **What:** `MOUNT_OPTS=":z"` (when SELinux enabled) is applied to
  `${PROJECT}:/project` but not to `${DATA}:/data`, though `$2` is advertised as
  accepting a host dir. Also contradicts README's `:ro,Z` advice for the same
  mount.
- **Why it matters:** On an SELinux-enforcing host, the host-dir form fails
  (cargo `EACCES` writing to `/data`), so the offline path can't be warmed that
  way; the `:z`/`,Z` mismatch can trigger the cross-container relabel conflict
  the script itself warns about.

### DS-28 — No test asserts tools are actually registered on the app
- **Where:** `src/rust_lsp_mcp/tools/__init__.py:18`.
- **What:** Tools register by import side effect (`pkgutil.iter_modules`,
  skipping `_`-prefixed). No test calls `mcp.list_tools()` or asserts the
  registered set; every test imports tool functions directly.
- **Why it matters:** If discovery regresses (an underscore rename, altered
  iteration, a tool moved out of the package), the deployed server silently
  exposes a reduced/empty tool set while the whole suite passes.
- **Resolved:** 2026-07-02 (PR #83). `tests/test_tool_registration.py` asserts the
  core tool set is registered via `mcp.list_tools()` (branch-safe subset) and that no
  registered tool name starts with `_`. (Issue #63 stays open for the other lows.)

---

## Refuted (recorded for completeness)

- **CRLF paragraph-split claim** (`doc_chunking.py`): a finder claimed the
  blank-line regex `\n{2,}` can't match `\r\n\r\n`, producing junk chunks on
  CRLF files. Refuted on verification — the splitting handles `\r\n` correctly.

---

## Method notes

- Finders and verifiers ran on `claude-fable-5`. 40 agents total; two died on
  transient API overload (`find:security`, `verify:goto_definition.py#8`) and
  were recovered from the run journal and hand-verified.
- Every confirmed finding above was checked against the installed
  `multilspy==0.0.15` and `mcp==1.12.4` sources in `.venv`, not assumed.
- Full raw per-agent output (evidence + verifier reasoning) was produced by run
  `wf_2ee0fb44-09e`.
