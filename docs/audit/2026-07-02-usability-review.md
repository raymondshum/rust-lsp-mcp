# Usability review — 2026-07-02

**Date:** 2026-07-02
**Method:** 3-phase multi-agent workflow — 6 fact-sheet readers → 5 usability analysts by dimension → 1 adversarial verifier per finding (36 agents total). 1 finding rejected in verification.
**Status:** Round-2 adversarial review (24 red-team agents + 3 cross-cutting critics, 2026-07-02): COMPLETE — dispositions below. Implementation: COMPLETE 2026-07-03 — Batch A (PR #108), Batch B (PR #109), Batch C (PR #110), Batch D (PR #111); UR-17 parked for a design session; CC-2/CC-3/CC-5 registered in known-issues.

## Summary

| ID | Dimension | Title | Analyst impact | Verifier-revised impact | Effort |
|----|-----------|-------|-----------------|--------------------------|--------|
| UR-1 | tool-ergonomics | Position tools never tell the agent how to obtain a position (the find_symbol→goto chain is undocumented) | high | medium | small |
| UR-2 | tool-ergonomics | The 1-indexed (and codepoint) position convention is absent from the parameter schema — a silent off-by-one trap | high | medium | small |
| UR-3 | tool-ergonomics | The `results` output key is overloaded for two incompatible shapes, and no list-key convention is shared across tools | medium | low | medium |
| UR-4 | tool-ergonomics | `probe` is a self-declared no-value tool shipped in the production tool surface, diluting tool selection | medium | low | small |
| UR-5 | tool-ergonomics | find_references forces the agent to branch on BOTH status and list length, with no explicit signal for the ok+[] case | medium | medium | small |
| UR-6 | error-recovery | The `error` status conflates four recovery classes into one indistinguishable payload | high | medium | medium |
| UR-7 | error-recovery | `refresh` reports doc-rebuild failure as a flat `error` even though the analyzer re-index already started — inviting a destructive retry | high | medium | small |
| UR-8 | error-recovery | The most common `not_ready` message names neither which poll tool to call nor which field/value to wait for | medium | low | small |
| UR-9 | error-recovery | The catch-all LSP/search error passes a raw exception string with zero next-step guidance | medium | medium | small |
| UR-10 | error-recovery | `find_symbol` not_found — the sole name→position entry point — is a dead end with no recovery hint | medium | medium | small |
| UR-11 | response-economy | No cap or truncation on unbounded LSP result lists (find_references / find_symbol / document_symbols) | high | medium | medium |
| UR-12 | response-economy | search_docs `limit` is floor-clamped only — no upper bound on response size | medium | low | small |
| UR-13 | response-economy | Position-only results force a follow-up round-trip per hit | medium | medium | medium |
| UR-14 | response-economy | search_docs ships the breadcrumb twice in every result | medium | low | small |
| UR-15 | response-economy | document_symbols emits `container: null` on every entry | low | low | small |
| UR-16 | workflow-gaps | Reference/definition results are bare coordinates — every hit forces a follow-up Read or hover | high | high | medium |
| UR-17 | workflow-gaps | No call-hierarchy tool — 'trace a call chain' degrades into recursive find_references with no caller identity | high | high | large |
| UR-18 | workflow-gaps | document_symbols throws away rust-analyzer's signature (detail), forcing one hover per symbol to understand a file | high | high | small |
| UR-19 | workflow-gaps | 'Understand this symbol' always costs hover + goto_definition as two separate calls | medium | medium | small |
| UR-20 | onboarding-observability | `status` never echoes the effective config or the doc-corpus size, so the most common first-hour misconfig (wrong root / missing mount / glob matches nothing) reports fully healthy | high | high | medium |
| UR-21 | onboarding-observability | No pre-flight validation of project_root / Cargo.toml / analyzer binary — a mistyped path or missing mount surfaces as an opaque rust-analyzer error or a silent empty doc index | high | medium | medium |
| UR-22 | onboarding-observability | `search_docs` `not_found` deterministically means "doc corpus is empty" (a config bug) but is worded as a soft "no match for your query" | medium | medium | small |
| UR-23 | onboarding-observability | During indexing there is no elapsed-time signal, so a polling agent cannot distinguish "still warming up" from "wedged" | medium | low | medium |
| UR-24 | onboarding-observability | Key startup diagnostics go only to `warnings.warn`/`logging`, which is invisible to an MCP client over stdio | medium | medium | medium |

## Final dispositions (post round 2)

| ID | Disposition | Note |
|---|---|---|
| UR-1 | implement | docstring cross-refs; APPEND (mirror document_symbols precedent), not prepend; optionally add FastMCP `instructions=` preamble |
| UR-2 | implement (revised) | description-only half via `Annotated[int, Field(description=…)]`; **no `ge=1`** — it breaks the settled error-envelope contract at the MCP boundary; keep the manual `< 1` guards |
| UR-3 | drop | rename menu conflicts with settled Phase-3 schemas; harm hypothetical; at most an envelope-seam `element_type` tag if ever needed |
| UR-4 | drop | registration-gating breaks test_core_tools_are_registered; probe has marginal gate smoke-test value; at most a one-line description tweak |
| UR-5 | merge → UR-11 | the always-present `total` field answers the ok+[] question; drop `symbol_resolved` |
| UR-6 | implement (narrowed) | single additive `recovery` enum field (fix_input\|refresh\|poll_status\|unknown); drop `retriable` bool and 4-value `code`; land the envelope builder change FIRST — UR-7/8/9/10 depend on it |
| UR-7 | implement (revised) | keep status=error (Option A breaks tests + documented contract); enrich the message at refresh.py:178: analyzer re-index already running, don't retry to fix analyzer; refresh again only after fixing doc-index root cause |
| UR-8 | implement (revised) | message-text half only: INDEXING_RETRY_MESSAGE constant naming analyzer_status + the field/value to wait for; structured `recovery` field arrives with UR-6 |
| UR-9 | implement (revised) | shared `lsp_failure(exc)` helper in envelope.py; keep "LSP error:" prefix (preserves tests); append neutral retry-once guidance; do NOT advise refresh |
| UR-10 | implement (revised) | two distinct not_found messages: zero matches (suggest shorter prefix/exact name) vs all-matches-outside-workspace; drop the readiness sentence |
| UR-11 | implement (revised) | NO pagination/offset (breaks settled full-list schema + ok+[] semantics); always-present `total`, one high safety cap (~200) with `truncated:true` only when exceeded; absorbs UR-5 and UR-12 |
| UR-12 | merge → UR-11 | bare ceiling clamp only; no `truncated` flag (semantically wrong for k-NN); share UR-11's cap convention |
| UR-13 | merge → UR-16 | same feature, clashing surface (snippet/include_snippet=True vs source/context=False); one opt-in design |
| UR-14 | implement (revised) | guarded read-time strip in DocStore.search (`startswith(bc+"\n\n")`); update the two stale docstrings |
| UR-15 | drop | omit-when-null removes a documented always-present key (tools.md + settled schema); cosmetic saving, contract break |
| UR-16 | implement (revised, absorbs UR-13) | opt-in source-line enrichment, off by default, scoped to find_references (goto_definition optional); one file-grouped read pass, errors='replace', point-in-time semantics documented; defer `container` |
| UR-17 | redesign | right primitive (callHierarchy) but spec must fix: selectionRange (not range.start) for round-tripping, prepare-null vs incoming-[] semantics, fromRanges retention, `data` threading, KI-9 single-coroutine composition — needs a grill/plan session |
| UR-18 | implement (revised) | read `sym.get("detail")` locally in document_symbols.py loop; do NOT thread through symbol_to_external (workspace symbols have no detail — would add an always-null key to find_symbol's settled contract) |
| UR-19 | drop (fold into UR-1) | documentation-only: note that hover + goto_definition can be issued as parallel calls in one turn; no `include_definition` param (undefined status precedence, erodes settled Option A separation) |
| UR-20 | implement (revised) | ONE field: `doc_index_chunk_count` via lock-safe accessor wrapped in asyncio.to_thread (DS-19); drop the three config-echo fields; absorbs UR-24's durable value |
| UR-21 | implement (narrowed) | non-fatal advisory preflight only (analyzer binary on disk + project_root is_dir), computed once in lifespan, surfaced as advisory field — no Cargo.toml gate (fights repo-agnostic settled design), no STATE_ERROR |
| UR-22 | implement (revised) | diagnostic-but-neutral not_found message enumerating the ≥3 causes of an empty corpus incl. "project ships no Markdown docs"; no misconfig assertion, no auto-refresh advice |
| UR-23 | drop | value refuted: client times its own poll loop; every clock reset is client-initiated via refresh |
| UR-24 | merge → UR-20 | freeform notes array dominated by structured chunk count; adopt-path bypass makes rebuild-local counts wrong |

- **Sequencing:** Batch A (text-only, contract-safe): UR-1, UR-2, UR-7, UR-8, UR-9, UR-10, UR-14, UR-22. Batch B (envelope): UR-6 first, then structured wiring of 7/8/9/10. Batch C (response shape): UR-11 (+5/12), UR-16 (+13), UR-18. Batch D (observability): UR-20 (+24), UR-21. UR-17 goes to a grill/plan session.
- **New candidates from the completeness critic (not yet triaged):** CC-1 no ToolAnnotations (readOnlyHint/destructiveHint) on any tool; CC-2 out-of-workspace (std/deps) definitions degrade to a misleading not_found; CC-3 MCP resources/prompts unused; CC-4 refresh's global blast radius/latency undisclosed in its description; CC-5 no version introspection (server / rust-analyzer / multilspy).

### Implementation record (2026-07-03)

- **Batch A — PR #108** (merged): UR-1 (+UR-19 folded), UR-2 (description-only), UR-7/8/9/10 message halves, UR-14, UR-22. 4 tests added post-review for the UR-14 strip and UR-10's two messages; review also surfaced that an empty-list LSP response must take the zero-matches message (`if not raw:`).
- **Batch B — PR #109** (merged): UR-6 narrowed (`recovery` field: fix_input | refresh | poll_status | unknown) + structured wiring of UR-7/8/9/10; CC-1 (ToolAnnotations: readOnlyHint everywhere, refresh destructive/non-idempotent); CC-4 (refresh blast-radius disclosure). Accepted tradeoff: the doc-rebuild-failure site carries `recovery="refresh"` under the four-value constraint — a purely field-branching agent could re-trigger the destructive restart; the message text is the guardrail. Revisit only if agents are observed looping on it.
- **Batch C — PR #110** (merged): UR-11 (total/truncated, MAX_LIST_RESULTS=200; absorbs UR-5/UR-12 incl. the search_docs limit ceiling), UR-16 (opt-in include_source on find_references; absorbs UR-13), UR-18 (local `detail` on document_symbols). Review catch fixed pre-merge: source lines split on "\n" only (splitlines() would shift lines after \f et al.).
- **Batch D — PR #111** (merged): UR-20 (`doc_index_chunk_count`, None-vs-0 semantics incl. the DS-24 adopt path; absorbs UR-24), UR-21 narrowed (advisory preflight via lifespan, surfaced as `preflight_warnings`). Review hardening applied: preflight body exception-guarded so "never fatal" is unconditional.
- **Caveat on UR-15**: its round-2 red-team agent returned near-placeholder output; the drop disposition rests on the contract-compat critic's independent evidence (removing a documented always-present key), which was verified against tools.md and the settled schema.
- **Dropped, confirmed not implemented**: UR-3, UR-4, UR-15, UR-23.

## UR-1 — Position tools never tell the agent how to obtain a position (the find_symbol→goto chain is undocumented)

- **Dimension:** tool-ergonomics
- **Impact:** high (analyst) → medium (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `src/rust_lsp_mcp/tools/goto_definition.py`
  - `src/rust_lsp_mcp/tools/find_references.py`
  - `src/rust_lsp_mcp/tools/hover.py`
  - `src/rust_lsp_mcp/tools/find_symbol.py`

**Problem**

The intended workflow is a two-step chain: resolve a name with find_symbol/document_symbols, then feed the returned file/line/character into goto_definition, find_references, or hover. But no tool description states this. goto_definition's description is only "Jump to the definition of the symbol at the given 1-indexed position" and its Args list just file/line/character — it never says "you need an already-resolved position; if you only have a name, call find_symbol first." Conversely find_symbol says "Resolve a Rust symbol name to its workspace position(s)" but never says its results are meant to be fed into the position tools, nor that its own result already IS the declaration site (so "where is X defined?" is answerable by find_symbol alone). An agent cold-reading the schemas, asked "who calls function foo?", has a name and no coordinates, and the only tool whose description mentions references (find_references) demands a line/character it cannot produce. It will either guess coordinates, mis-try passing a name where an int is required, or abandon the server for grep — failing on the first try.

**Proposed solution**

Add a one-line "When to use / prerequisite" clause to each position tool's description. goto_definition/find_references/hover: prepend "Requires an exact position (file+line+character), e.g. one returned by find_symbol or document_symbols. If you only have a symbol NAME, call find_symbol first." find_symbol: append "Each result's {file,line,character} is the symbol's declaration site and can be passed directly to goto_definition/find_references/hover. For \"where is X defined?\" this tool alone usually answers it." This documents the chain without reopening the settled "find_symbol is the sole name→position bridge" decision.

**Round-1 verification**

Verdict: `confirmed`

Problem is REAL and unfixed in all four named files. goto_definition.py:29 docstring is verbatim "Jump to the definition of the symbol at the given 1-indexed position" with Args only file/line/character (lines 36-39) — no prerequisite or how-to-obtain-position guidance. find_references.py:34-105 never mentions find_symbol or how to produce the required line/character from a bare name. hover.py:98 references find_symbol only as an indexing-convention aside ("same convention as find_symbol output"), not a prerequisite. find_symbol.py:21-60 never states its results feed into the position tools nor that its result is the declaration site answering "where is X defined?" (accurate: workspace_symbol locations are declaration sites).

NOT tracked: known-issues.md Open section is empty (line 30-32); no covering entry. NOT implemented in the four target files.

NO CONFLICT with settled architecture: implementation-plan.md:259-272 settles Option A — find_symbol is the SOLE name→position bridge, position tools take positions never names, and "the assistant's natural loop: find_symbol / document_symbols to get a position → act with that position." The proposal documents this already-settled loop; it reinforces rather than reopens it. Pure docstring change, no impact on read-only/stdio/single-host scope. Implementable, effort small.

One caveat that lowers impact from the claimed high to medium: the finding's absolute claim "no tool description states this" is overstated — document_symbols.py:53-58 already documents the forward direction ("This position is suitable for feeding directly into hover, goto_definition, or find_references"). That is a ready-made precedent the edits should mirror, but it also means the pattern is half-present and a capable agent has additional signal (the tool name find_symbol, its "resolve name to position(s)" description, position tools returning positions) to infer the chain. Real ergonomic win, cheap to make, but "fails first try / abandons for grep" is pessimistic for a strong coding agent, so impact is medium rather than high.

### Round-2 red-team verdict

**Claim verdict:** holds

Real, unfixed doc gap across the four files; even round 1's medium is generous given existing cues (document_symbols already documents the forward chain, find_symbol's name/description signal the bridge). Realistic impact is low but the gap is genuine.

**Solution verdict:** sound

**Recommended action:** implement

- **minor** — 'Prepend' buries the one-line summary and contradicts the cited precedent (document_symbols.py:56-58 APPENDS); should append instead.
- **minor** — find_symbol addition over-trusts a fuzzy multi-candidate tool; keep the pick-by-kind/container caveat adjacent.
- **minor** — 'declaration site' phrasing is looser than the codebase's name-vs-range distinction (find_symbol uses location.range.start, not selectionRange; core.py:334).
- **minor** — Unused DRYer hook: FastMCP instructions= is unset (core.py:79-82); a server-instructions preamble is a single-source home vs 4 duplicated cross-refs, though not all clients surface it.

**Revised proposal:** Keep the docstring edits (reliably surfaced to clients) but APPEND rather than prepend, mirroring document_symbols' existing precedent, and trim the fuzzy over-claim. Optionally add a FastMCP instructions= preamble (currently unset, core.py:79) as the DRY single-source home for the compose pattern — complementing, not replacing, the per-tool notes. No tests assert on descriptions, so all edits are contract-safe; effort 'small' is honest.

## UR-2 — The 1-indexed (and codepoint) position convention is absent from the parameter schema — a silent off-by-one trap

- **Dimension:** tool-ergonomics
- **Impact:** high (analyst) → medium (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `src/rust_lsp_mcp/tools/goto_definition.py`
  - `src/rust_lsp_mcp/tools/find_references.py`
  - `src/rust_lsp_mcp/tools/hover.py`
  - `src/rust_lsp_mcp/positions.py`

**Problem**

I dumped the generated MCP schema: goto_definition's params are literally {"line":{"title":"Line","type":"integer"},"character":{"title":"Character","type":"integer"}} with NO per-parameter description. The 1-indexed convention and the codepoint-offset semantics of `character` live only in the docstring prose. This is the classic 0-vs-1 trap: LSP itself, rust-analyzer, and most editors/tools are 0-indexed, so an agent that pattern-matches on "this is LSP" will pass 0-based coordinates. Because line/character>=1 is only rejected at the literal boundary (0 or negative), a 0-based input like line=41 instead of 42 is >=1, sails past validation, and resolves to a real-but-wrong adjacent position — producing a confidently wrong definition/hover rather than an error. The parameter name `character` also gives no hint it is a Unicode codepoint offset, not a UTF-16 unit or byte column.

**Proposed solution**

Attach per-parameter descriptions via typing.Annotated + pydantic Field (FastMCP propagates these into inputSchema, unlike Google-style Args which it drops). E.g. line: Annotated[int, Field(ge=1, description="1-indexed line; the first line is 1 (NOT 0-indexed like raw LSP).")] and character: Annotated[int, Field(ge=1, description="1-indexed Unicode-codepoint offset within the line; first char is 1.")]. The ge=1 constraint also moves the >=1 rejection into the schema so bad input is caught client-side. Apply uniformly to goto_definition, find_references, and hover.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL (verified by dumping the live generated schema, not just reading source). With mcp==1.12.4, `mcp.list_tools()` yields for all three tools exactly bare `line`/`character` integer properties with no `description` and no `minimum`. The signatures in src/rust_lsp_mcp/tools/{goto_definition.py:28, hover.py:75, find_references.py:28} are bare `line: int, character: int`; the only `Field(` usage in the whole src tree is settings.py:53. The 1-indexed + codepoint convention lives solely in docstring prose, and FastMCP drops Google-style `Args:` blocks from inputSchema.

The off-by-one trap is plausible: validation is only `if line < 1 or character < 1: return error(...)` (goto_definition.py:65, find_references.py ~step1, hover.py step1). A 0-based input like line=41 for line 42 is >=1, passes, and external_to_lsp (positions.py:63) subtracts 1 → a real-but-wrong adjacent LSP position → confidently wrong result.

PROPOSAL WORKS (verified): a probe tool with `Annotated[int, Field(ge=1, description=...)]` produced `{"description":"1-indexed line","minimum":1,...}` in inputSchema. Effort is small (three signatures).

NOT ALREADY DONE / NOT TRACKED: known-issues.md "Open" section is empty; KI-5 (resolved) covers utf-32 codepoint negotiation/correctness, not documenting the convention in the param schema.

NO CONFLICT: Settled architecture (implementation-plan.md:376-383) covers MCP-vs-skill, read-only, refresh/reindex, RAG, stdio/single-host — none touched.

IMPACT revised high→medium: the tool-level description docstrings already state "Positions are 1-indexed on both input and output" — FastMCP DOES propagate the tool description, so the convention is not fully silent; per-parameter descriptions are incremental hardening, not plugging a wide-open hole. CAVEAT: `Field(ge=1)` is enforced by pydantic only on MCP-boundary calls, not direct Python calls, and raises a protocol-level validation error rather than the project's `error()` envelope. Existing tests (tests/test_goto_definition.py:111-134 and parallels in test_hover.py/test_find_references.py) call the tool functions directly with line=0 and assert `status==error` + `"1-indexed" in message` — so the manual `if line < 1` guards must be KEPT; removing them would break those tests.

### Round-2 red-team verdict

**Claim verdict:** holds

Real gap but 'silent' framing overstated; see solution issues for the load-bearing findings.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **blocker** — The `ge=1` constraint breaks the documented error-envelope contract at the real MCP boundary. Verified path in mcp 1.12.4: pydantic ValidationError -> base.py:109 ToolError -> lowlevel server.py:501 _make_error_result -> CallToolResult(isError=True, TextContent='Error executing tool ...: 1 validation error'). A client sending line=0 no longer gets the documented {status:error,message} envelope (tools.md:176/227/289) and this violates the settled principle that malformed input is envelope data not a protocol-level error (implementation-plan.md:240-241).
- **major** — Round-1's mitigation 'keep the manual guards' does NOT preserve the contract. Pydantic validates arguments before the function body, so the `if line < 1` guards are unreachable at the MCP boundary and fire only on direct Python calls (the tests). Result: split-brain (direct call -> error envelope; MCP client -> isError result); the tests at test_goto_definition.py:111-134 etc. stay green while production behavior for the documented case silently regresses, and the guard becomes dead code on the real path.
- **minor** — The pydantic isError text ('Error executing tool goto_definition: 1 validation error for goto_definitionArguments...') leaks the internal arg-model name and is more verbose and less actionable than the curated 'line and character are 1-indexed; must be >= 1' message — a usability regression on the very case the finding wants to improve.
- **minor** — The description-only half is uncontroversial and sound, but note find_symbol/document_symbols are correctly out of scope (they take name/file, not line/character), so 'apply uniformly to goto_definition, find_references, hover' is the right and complete set — no hidden extra files.

**Revised proposal:** Ship the Annotated + Field(description=...) half ONLY; drop `ge=1`. Keep the existing manual `if line < 1` guards — they already return the documented error envelope for BOTH direct and MCP calls, staying inside the settled envelope contract. This delivers the per-param schema documentation goal (the real ergonomic win), avoids the boundary contract break, and is strictly simpler than descriptions+ge=1+redundant-guards. Effort stays honestly small.

## UR-3 — The `results` output key is overloaded for two incompatible shapes, and no list-key convention is shared across tools

- **Dimension:** tool-ergonomics
- **Impact:** medium (analyst) → low (verifier-revised)
- **Effort:** medium
- **Affected files:**
  - `src/rust_lsp_mcp/tools/search_docs.py`
  - `src/rust_lsp_mcp/tools/find_symbol.py`

**Problem**

find_symbol returns ok+results where each element is {name,kind,file,line,character,container}; search_docs returns ok+results where each element is {file,breadcrumb,text,distance}. Same key `results`, completely different element schema and meaning (code symbols vs doc chunks). An agent (or any shared response-handling code) that keys on `results` and expects `.line`/`.character` will silently get doc chunks with no position, or vice-versa. More broadly the "list of hits" key is different for every tool — definitions, references, symbols, results (x2), contents — so there is no single field an agent can rely on to find the payload. This raises per-tool parsing cost and error rate.

**Proposed solution**

Give each list a distinct, self-describing key so the shape is inferable from the key: rename search_docs's list to `chunks` (or `docs`) and keep find_symbol's as `results` or rename to `symbols`. Document in each tool that the top-level payload key names the element type. (If a uniform key is preferred instead, standardize all six on `results` AND state the element schema in every description — but distinct keys better prevent the cross-tool shape confusion above.) Whichever direction, do it once and note the convention in the envelope docs.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL (verified in current code):
- find_symbol.py:126 returns `ok(results=results)` where each element is `{name, kind, file, line, character, container}` (docstring at find_symbol.py lines ~40-48 pins this shape).
- search_docs.py (final line) returns `ok(results=hits)` where each element is `{file, breadcrumb, text, distance}` (docstring pins EXACTLY this shape). No `line`/`character`.
Both tools thus emit the same top-level `results` key with incompatible element schemas. Confirmed the broader claim too — the list key differs per tool: goto_definition.py:124 `definitions=`, find_references.py:206 `references=`, document_symbols.py:130 `symbols=`, hover.py:152 `contents=` (a string, not a list), plus `results` used twice. So there is no uniform payload key AND the one collision (`results`) is on two mismatched shapes.

NOT FIXED / NOT TRACKED: The "Open" section of docs/impl/known-issues.md is empty; no KI entry covers envelope key naming. envelope.py's `ok(**kwargs)` is shape-agnostic, so nothing centrally constrains this.

NO CONFLICT with settled decisions: implementation-plan.md §"Settled architecture" (line 376+) covers transport/read-only/RAG topology, not payload key names. The Phase-3 schema block (line 294) does pin `find_symbol → results` as decided — and the proposal's preferred direction KEEPS find_symbol as `results` and renames only search_docs's list (to `chunks`/`docs`). search_docs's key is NOT specified anywhere in the plan or phase-5-doc-rag.md (both only say "returns relevant chunks", `{status}` envelope), so renaming it is fully compatible with the settled contract and doesn't touch read-only/stdio/single-host scope.

IMPLEMENTABLE, low cost: one-line change in search_docs.py plus its docstring; doc touch-ups in docs/guide/components.md and docs/guide/architecture.md (already call them "chunks" in prose); test updates in tests/test_search_docs.py:218/223 (asserts `result["results"]`) — tests/test_doc_store.py and test_phase5_integration.py assert on the store layer (`store.search`), not the envelope, so they're unaffected. Non-breaking to internal logic.

IMPACT — revised down to low: each tool's docstring already documents its exact element shape verbatim, and MCP agents dispatch by tool name (they know whether they called find_symbol or search_docs), so the "silently gets the wrong shape" scenario is limited to hypothetical generic response-handlers keying on `results` alone — not the primary LLM-consumer path. The self-describing-key change is a genuine but minor consistency/ergonomics win worth doing opportunistically (e.g. next time search_docs is edited), not an urgent fix. Verdict: confirmed (real, unfixed, untracked, non-conflicting) at low impact.

### Round-2 red-team verdict

**Claim verdict:** weakened

The factual core holds: find_symbol.py:100 and search_docs.py:142 both emit ok(results=...) with incompatible element schemas, and the list key differs per tool (definitions/references/symbols/contents). But the stated harm — "shared response-handling code that keys on results silently gets the wrong shape" — is hypothetical: no such central consumer exists (each tool builds its envelope independently; envelope.py ok(**kwargs) is shape-agnostic), and MCP agents dispatch by tool name knowing which shape to expect. Round 1 already downgraded medium→low for exactly this reason. So the collision is real but the severity is genuinely low, not the analyst's medium.

**Solution verdict:** needs-revision

**Recommended action:** drop

- **major** — Internally inconsistent with its own goal. It renames search_docs results→chunks but keeps find_symbol as generic `results`. find_symbol is the shape WITH .line/.character — the exact one the hypothetical bad consumer expects — so the least self-describing key stays on the most position-bearing tool. The 'self-describing key' principle is only half-applied; a coherent version needs find_symbol→symbols too.
- **major** — Its offered alternatives conflict with settled architecture. implementation-plan.md Phase-3 schema block explicitly pins find_symbol→results, document_symbols→symbols, goto_definition→definitions, find_references→references, hover→contents. The proposal's 'rename find_symbol to symbols' option and its 'standardize all six on results' fallback both contradict these settled schemas. Round 1 only vetted the single preferred direction and missed that the menu it offered breaks settled decisions.
- **minor** — Effort/churn undercounted. Round 1 called it a 'one-line change plus tests/test_search_docs.py:218/223.' Actual: test_search_docs.py has 31 'results' references including two dedicated assertions (test_not_found_has_no_results_key:296, test_error_envelope_has_no_results:334) that would need renaming, plus tools.md rows 321/333/355 and prose in components.md/architecture.md. Small but not 'one line'; 'medium' vs 'low' framings also disagree with each other.
- **minor** — Weak net benefit for the cited harm. For the hypothetical consumer keying on `results`, the rename converts silent-wrong-shape into a missing-key on search_docs only — an improvement solely if that consumer is defensive — while find_symbol still hands it `results` with .line. It half-fixes an already-hypothetical failure mode, and introduces a code/doc-drift risk (the class of issue known-issues.md exists to catch) if the rename isn't landed in lockstep across code, tools.md, and prose.

**Revised proposal:** The tension is unresolvable cleanly: full self-description (rename every list key) conflicts with the settled Phase-3 schemas, and the settled surface is already near-uniform (find_symbol=results is decided). If any change is made, do it at the envelope seam, not per-tool: have ok() optionally carry an element_type tag (e.g. {"status":"ok","element_type":"doc_chunk","results":[...]}) so a generic consumer can branch without renaming keys or reopening settled schemas. Otherwise leave as-is and, at most, log a known-issue note. Not worth a tracked rename at this (low) impact.

## UR-4 — `probe` is a self-declared no-value tool shipped in the production tool surface, diluting tool selection

- **Dimension:** tool-ergonomics
- **Impact:** medium (analyst) → low (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `src/rust_lsp_mcp/tools/diagnostics.py`

**Problem**

probe() is registered as a normal @mcp.tool() visible to every client, yet its own docstring says "This tool has no semantic value beyond demonstrating and testing the require_ready invariant." Every extra tool an LLM must consider raises selection difficulty and the chance of a wasted/erroneous call; a no-op diagnostic tool sitting next to the 9 real ones is pure noise for the agent. Its readiness signal is already fully covered by analyzer_status (state) and status (full report), so probe adds a third overlapping readiness-ish entry with no user-facing purpose.

**Proposed solution**

Do not register probe on the production surface: gate its @mcp.tool() registration behind a debug/env flag (e.g. only register when RLM_DEBUG_TOOLS is set), or move it out of the auto-discovered tools/ package into a test-only harness so integration tests can still exercise require_ready end-to-end. If it must stay visible, at minimum prefix its description with "Diagnostic/self-test only — not for navigation" so an agent deprioritizes it.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL (verified in code). src/rust_lsp_mcp/tools/diagnostics.py:33-44 registers `probe()` with a bare `@mcp.tool()` decorator and no gating. Its docstring (lines 35-40) states verbatim: "This tool has no semantic value beyond demonstrating and testing the ``require_ready`` invariant; navigation tools use the same gate." tools/__init__.py:18-26 auto-imports every submodule not prefixed with `_`, so probe is registered on the FastMCP app for every client. Readiness is triple-covered: analyzer_status, status, and probe (gated no-op) — a third overlapping readiness-ish entry with no user-facing purpose. docs/guide/tools.md:487-493 itself labels probe "Diagnostic only... This tool has no everyday use."

NOT FIXED, NOT TRACKED. known-issues.md has an EMPTY "Open" section (line 30-32); nothing about probe or a debug-tool flag. Grep for RLM_DEBUG / debug-gating found nothing.

NO CONFLICT with settled architecture. implementation-plan.md:376-383 covers MCP-vs-skill, read-only, refresh semantics, RAG/ChromaDB, stdio/single-host — nothing about the tool surface or probe. tests/test_tool_registration.py uses a SUBSET check (_CORE_TOOL_NAMES, line 32-45) not exact equality, precisely so branch-specific tool sets are allowed.

IMPLEMENTABLE but effort/impact are OVERSTATED, so I revise impact to low (not medium). Caveats: (1) The docstring's FIRST words are already "Gated no-op probe — proves the fail-fast gate works end-to-end," which already gives an agent a strong deprioritization signal — the proposal's weakest fallback (prefix its description) is substantially already met. (2) The cost of one clearly-labeled diagnostic tool among ~10 real ones is marginal for LLM selection. (3) The "small" fix has real ripples: probe is directly imported and exercised by tests/test_phase1_fast.py, tests/test_phase2_fast.py, tests/test_analyzer_error_state.py, and tests/test_phase2_integration.py (container probe), and is cherry-picked across main/bob_prototype; gating registration is fine for direct-call unit tests but test_tool_registration.py's core set and the integration container probe would need adjusting on both branches. Verdict is confirmed because the objective bar is met — real, unfixed, untracked, non-conflicting — but impact is a low ergonomic nit rather than the claimed medium.

### Round-2 red-team verdict

**Claim verdict:** weakened

Problem is real and low is the right severity, but the claim overreaches on "no user-facing purpose." probe is the ONLY argless, side-effect-free tool that traverses the real require_ready() gate (analyzer_status at diagnostics.py:13 just returns state without gating), so a human operator on a live MCP session can use it as a clean gate smoke-test — a genuine, if marginal, diagnostic use the finding dismisses. Combined with already-shipped deprioritization signals (docstring opens "Gated no-op probe", tools.md:489 "Diagnostic only"), this is borderline won't-fix, not a substantive ergonomics defect.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **blocker** — Gating probe's @mcp.tool() registration breaks tests/test_tool_registration.py: probe is a member of _CORE_TOOL_NAMES (line 43), and test_core_tools_are_registered asserts _CORE_TOOL_NAMES - registered is empty. Unregistering probe fails it. Round-1 cited this test's subset check as proof of NO CONFLICT — inverted reasoning: the subset check tolerates extra tools, never a missing core member.
- **major** — Cross-branch tax + config drift make 'small' dishonest. test_tool_registration.py is cherry-picked identically to bob_prototype (file header + memory branch-flow rule), so the core-set edit AND the conditional decorator must land on both branches main-first. An RLM_DEBUG_TOOLS env check also lives outside settings.py (the config home, sole Field( user at settings.py:53), an ad-hoc knob inconsistent with the settings module.
- **minor** — Round-1's ripple evidence is wrong: it cites test_phase2_integration.py's 'container probe' as an affected probe test, but _run_container_probe (line 237) calls _find_symbol_live(manager,'SearcherBuilder') to inspect containerName — nothing to do with the probe tool. It is unaffected; the citation is a misidentification.
- **minor** — The valuable part is already done, so code-gating adds cost for near-zero marginal value: diagnostics.py:35 docstring opens 'Gated no-op probe...' and docs/guide/tools.md:489 opens 'Diagnostic only.', already satisfying the finding's fallback. Direct-call unit tests (test_phase1_fast:172, test_analyzer_error_state:227) call diag.probe() as a function so they survive gating — confirming the only real casualty is the registration test, i.e. cost with no offsetting coverage gain.

**Revised proposal:** Abandon registration-gating (env flag + decorator + cross-branch core-set edits) — it breaks test_core_tools_are_registered for no capability gain. The deprioritization signal the finding wants already exists (docstring "Gated no-op probe", tools.md "Diagnostic only"). If any touch is worth it, cap it at a one-line description tweak. A cleaner hook than an env flag is the existing '_'-prefix skip in tools/__init__.py (split probe into tools/_probe.py so auto-discovery drops it), but that STILL requires removing probe from _CORE_TOOL_NAMES on both branches, so it is not free either. Simplest honest outcome: leave probe as-is.

## UR-5 — find_references forces the agent to branch on BOTH status and list length, with no explicit signal for the ok+[] case

- **Dimension:** tool-ergonomics
- **Impact:** medium (analyst) → medium (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `src/rust_lsp_mcp/tools/find_references.py`

**Problem**

find_references deliberately splits "no symbol at this position" (not_found) from "real symbol, zero callers" (ok + empty references). This is the opposite of its sibling goto_definition, which returns not_found for an empty result. So across two adjacent position tools, an empty answer means not_found in one and ok+[] in the other. The not_found path carries an explanatory message, but the ok+[] path returns ok(references=[]) with no message and no flag — the agent must infer "the symbol exists but has no callers" purely from an empty array under an ok status, which is exactly the kind of empty-collection reasoning LLMs get wrong (easily misread as "the tool failed / returned nothing useful").

**Proposed solution**

Without reopening the settled ok+[]-vs-not_found semantics, make the ok+[] case self-describing: when the reference list is empty, attach a message to the ok envelope, e.g. ok(references=[], message="Symbol resolved; 0 references found in the indexed workspace (no in-tree callers)."). Optionally add a stable boolean like symbol_resolved: true on find_references' ok payload so the agent never has to distinguish resolution-failure from zero-callers by list length. This gives an explicit textual/flag signal rather than relying on empty-array inference.

**Round-1 verification**

Verdict: `confirmed`

Problem is real and unfixed. src/rust_lsp_mcp/tools/find_references.py:206 returns `ok(references=list(seen.values()))` with no message/flag for the empty case, yielding a bare `{"status":"ok","references":[]}`. The claimed sibling asymmetry is confirmed: goto_definition.py:108,121 return not_found (with message) on empty, as do find_symbol.py:85,98 and search_docs.py:140; only find_references uses ok+[] (its deliberate, docstring-documented exception, lines 45-58). So an empty answer maps to not_found in one adjacent position tool and to an unannotated ok+[] in the other, and only the not_found path carries explanatory text. Not tracked: docs/impl/known-issues.md Open section is empty (KI-1..KI-9 all Resolved). No conflict: the proposal preserves the settled ok+[]-vs-not_found semantics (envelope.py:19-27) and is purely additive; the Settled-architecture block (implementation-plan.md:376-383) is silent on envelope messages. Directly implementable with in-repo precedent — diagnostics.py:44 already returns `ok(message=...)` and envelope.py:46 `ok(**kwargs)` accepts arbitrary fields, so the empty-case message is ~3 lines and idiomatic. Effort small is accurate. Impact is medium but on the low side: the docstring already documents the semantic at selection time, so the gain is limited to the call-return signal — real, but modest. The optional symbol_resolved boolean is a larger (new stable payload field) change; the message-only variant is the low-risk core of the fix.

### Round-2 red-team verdict

**Claim verdict:** weakened

Core holds: find_references.py:206 returns bare ok(references=[]) with no message/flag, the only list tool using ok+[] for resolved-zero-results (goto_definition.py:109,122 returns not_found on empty). Two overstatements round-1 accepted: (1) the sibling asymmetry is semantically principled, not arbitrary (a symbol always has a definition, so empty=unresolved=not_found; references can legitimately be zero=ok+[]); (2) the ok+[] semantic is already documented at selection time in docstring find_references.py:45-58 and tools.md:239-254 with a dedicated zero-callers example, and status==ok already signals success. The LLM-confusion claim has no evidence. Net impact is low, not medium.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — Round-1 called the fix purely additive / non-breaking / ~3 lines. False: ok(references=[], message=...) directly breaks tests/test_find_references.py:328 (test_zero_references_result_shape, a headline semantic test) which asserts message-not-in-result for the ok+[] case. Editable, but it refutes the additive/effort framing.
- **major** — The symbol_resolved:true boolean is informationally redundant. Under status==ok the symbol is always resolved; resolution failure returns not_found (find_references.py:150), a distinct status, never ok+[]. The flag is constant true (token noise). Its rationale rests on a false premise: resolution-failure vs zero-callers is carried by the status field, never by list length.
- **minor** — message-only-on-empty makes the ok envelope shape vary by list length (message present iff empty), reintroducing a within-status shape branch and diverging from the other list tools whose ok payloads carry no message (goto_definition definitions=, find_symbol results=, document_symbols symbols=).
- **minor** — A simpler cross-tool-uniform alternative already exists in the same audit: UR-11 adds total/truncated count fields to find_references ok envelope. A numeric count(0) signals emptiness across all three list tools without a status-varying message and composes with UR-11/UR-6 instead of a find_references-only hack.

**Revised proposal:** Drop the symbol_resolved boolean (constant-true noise). For the empty signal prefer a uniform always-present count/total field on all three list tools ok envelope (aligning with UR-11) rather than a message appearing only when empty. If a message is still wanted, update tests/test_find_references.py:328 in the same change and stop calling it purely additive.

## UR-6 — The `error` status conflates four recovery classes into one indistinguishable payload

- **Dimension:** error-recovery
- **Impact:** high (analyst) → medium (verifier-revised)
- **Effort:** medium
- **Affected files:**
  - `src/rust_lsp_mcp/envelope.py`
  - `src/rust_lsp_mcp/core.py`
  - `src/rust_lsp_mcp/tools/search_docs.py`
  - `src/rust_lsp_mcp/tools/find_references.py`
  - `src/rust_lsp_mcp/tools/hover.py`

**Problem**

`envelope.error()` returns only `{"status":"error","message":...}` (envelope.py:69-71) — no code, no retriable flag. Yet the same `error` status is returned for opposite situations: (a) client input mistakes, e.g. `error("line and character are 1-indexed; must be >= 1")` (goto_definition.py:71); (b) a PERMANENT analyzer failure recoverable only via refresh, from `require_ready()` (core.py:106-111); (c) a PERMANENT doc-index failure (search_docs.py:26-29); and (d) a possibly-TRANSIENT LSP exception, `error(f"LSP error: {exc}")` (hover.py:139, find_references.py:144, goto_definition.py:105, find_symbol.py:81). These demand mutually exclusive next actions (fix args and do NOT retry / call refresh / poll-and-retry), but an LLM client sees identical field structure and must NLP-parse free-text English to choose. That is fragile and non-deterministic.

**Proposed solution**

Add machine-parseable, purely-additive fields to non-ok envelopes (does NOT change the settled 4-value status vocabulary or add `ambiguous`): a `code` enum (`invalid_input` | `lsp_failure` | `analyzer_unavailable` | `doc_index_unavailable`) plus `retriable: bool` and a `recovery` hint (`"fix_input"` | `"poll_status"` | `"refresh"`). Thread them from each raise site through the pure builders in envelope.py. An agent can then branch on `code`/`retriable` without prose parsing while the human-legible `message` stays.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL AND CURRENT. `envelope.error()` returns exactly `{"status": STATUS_ERROR, "message": message}` with no code/retriable/recovery fields (envelope.py:69-71). The same `error` status is emitted for genuinely distinct recovery classes:
- (a) client input mistakes: goto_definition.py:71, hover.py:108, search_docs.py:86, find_references.py:108, core.py:236 (validate_workspace_file).
- (b) PERMANENT analyzer failure, recoverable only via refresh: core.py:106-111 (require_ready when state==STATE_ERROR).
- (c) PERMANENT doc-index failure, recoverable only via refresh: search_docs.py:26-29 and 103-106.
- (d) catch-all, possibly-transient LSP exception: hover.py:139, goto_definition.py:105, find_references.py:144 (and 187), find_symbol.py:81, document_symbols.py:109.
These demand mutually exclusive next actions (fix args / call refresh / retry) yet a client can only distinguish them by NLP-parsing free-text English.

NOT FIXED, NOT TRACKED. envelope.error() has no machine-parseable fields. known-issues.md "Open" section is empty. Note: transient-teardown cases that ARE cleanly retriable were already carved out to `not_ready` under #98/KI-9 (AnalyzerTornDownError -> not_ready), so the remaining `error(f"LSP error: {exc}")` is the truly-ambiguous unexpected-exception path.

NO CONFLICT WITH SETTLED DECISIONS. The settled envelope decision (implementation-plan.md:230-247) fixes the 4-value status vocabulary and records that `ambiguous` was considered-and-dropped. The proposal explicitly does NOT alter the status vocabulary; it adds purely-additive fields (code/retriable/recovery) to non-ok envelopes.

IMPLEMENTABLE, effort medium is plausible. Add optional params to the error() builder with sensible defaults and thread a code from each raise site. Two caveats that temper the "high" impact: (1) the finding's affected-files list is INCOMPLETE — it omits goto_definition.py and find_symbol.py plus document_symbols.py, refresh.py, find_references.py, and validate_file_path.py, all of which also call error(); a real implementation touches ~8 files. (2) Because existing prose messages already carry the distinguishing recovery hint (e.g. "Call the refresh tool", "must be >= 1"), a capable LLM can already mostly infer the recovery class — so the win is deterministic/reliable branching rather than newly-unlocked capability, which I rate impact medium rather than high. Verdict: confirmed — real, unfixed, untracked, non-conflicting, worth doing.

### Round-2 red-team verdict

**Claim verdict:** weakened

Problem is real (all 9 raise sites do emit status="error" with only message), but the framing is overstated and round-1 accepted it. Title says "four recovery classes," yet the proposal's own recovery enum has only THREE values (fix_input|poll_status|refresh): cases (b) analyzer_unavailable and (c) doc_index_unavailable both collapse to the same next action — call refresh (envelope.py:12-17 already documents them identically; tools.md:24 lumps them). More importantly, cases (a)/(b)/(c) are already reliably inferable from status+message ("must be >= 1" vs "Call the refresh tool"); the ONLY genuinely ambiguous site is (d) error(f"LSP error: {exc}") — and after #98/KI-9 moved cleanly-retriable teardown to not_ready, that residue is the truly-unknown exception, which no static flag can honestly classify (see solution_issues). So the deterministic-branching win is far narrower than "four classes."

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — The 4-value `code` enum (invalid_input|lsp_failure|analyzer_unavailable|doc_index_unavailable) doesn't cover real raise sites the proposal never enumerated: refresh.py:138 ("Analyzer is not running"), refresh.py:178 (partial success — analyzer re-index ALREADY started, doc rebuild failed), and validate_file_path.py:58 ("Workspace root is not configured" — server misconfig, not client input). None fit the enum, and refresh.py:178 is destructive to retry (restart() re-runs), so any retriable/recovery value there is actively wrong (this is UR-7's exact concern).
- **major** — The one motivating case — error(f"LSP error: {exc}") at hover.py:139/goto_definition.py:105/find_references.py:144,187/find_symbol.py:81/document_symbols.py:109 — is fundamentally un-labelable. It is the unexpected-exception residue after KI-9 carved retriable teardown to not_ready. Setting retriable=True/recovery=poll_status risks infinite retry loops on a deterministic crash (e.g. a file that always faults rust-analyzer); retriable=False loses genuinely transient retries. The proposal promises deterministic branching but hands a false-confident flag on precisely the case that was ambiguous.
- **minor** — Three overlapping fields (code + retriable + recovery) where `recovery` alone suffices and the others can contradict it: analyzer_unavailable/doc_index_unavailable are 'recoverable via refresh' — is retriable true (you CAN recover) or false (plain retry won't help)? Undefined. Over-specified surface invites inconsistent raise-site wiring.
- **minor** — Hidden doc cost understated: tools.md documents error as `message`-only across the top-level table (line 24) plus 7 per-tool rows (53,118,176,227,289,324,458). Threading code through ~9 sites + changing the envelope.error() signature + per-code tests + these doc rows pushes 'medium' to its ceiling; borderline-honest, not comfortable-medium.

**Revised proposal:** Narrow it: add ONE additive field, `recovery` (fix_input | refresh | poll_status | unknown), and drop both the redundant `retriable` bool and the 4-value `code`. Map the already-clear sites (input→fix_input; analyzer/doc permanent→refresh) and, crucially, emit recovery="unknown" for the residual LSP-exception path so the agent surfaces/reports rather than auto-loops on a possibly-deterministic crash. Add a distinct do-not-retry recovery for refresh.py:178's partial-success case. This captures the only real win (case d + refresh partial) without a contradictory three-field contract, and keeps effort honestly small. Tests only assert status==STATUS_ERROR, so additive fields stay backward-compatible either way.

## UR-7 — `refresh` reports doc-rebuild failure as a flat `error` even though the analyzer re-index already started — inviting a destructive retry

- **Dimension:** error-recovery
- **Impact:** high (analyst) → medium (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `src/rust_lsp_mcp/tools/refresh.py`

**Problem**

In refresh.py the analyzer restart runs first (`await mgr.restart()`, line 140, setting state to indexing), then if the doc-store rebuild throws it returns `error(f"Analyzer re-index started, but documentation rebuild failed: {exc}")` (line 178). The envelope status is `error`, so an agent applying the natural rule "error means the call failed, retry it" will call `refresh` again — which re-invokes `mgr.restart()` (line 140), tearing down and restarting the analyzer re-index that was mid-flight. Repeated on each doc failure this livelocks the analyzer so it never reaches `ready`. The prose says the analyzer part succeeded, but nothing machine-readable conveys the partial success.

**Proposed solution**

Represent the partial success distinctly: return `ok` with `state:"indexing"` plus a `doc_index:"error"` sub-field and the diagnostic, OR keep `error` but attach `code:"doc_index_unavailable"`, `retriable:false`, and `recovery` text stating the analyzer re-index is already running and must NOT be restarted — the doc index alone needs another refresh once the cause is fixed. Either way the agent learns not to re-tear-down the healthy analyzer half.

**Round-1 verification**

Verdict: `confirmed`

Problem is REAL and unfixed. refresh.py:140 calls `await mgr.restart()` before the doc-store rebuild; analyzer.py:784-852 confirms restart() is destructive on every call (step 4 `_drain_task()` drains the in-flight background _run and bumps _generation; step 7 spawns a fresh task), so a retried refresh() genuinely tears down and restarts an in-flight (or already-completed) analyzer re-index — not a no-op. On doc-rebuild failure, refresh.py:174-178 returns a flat `error(...)`. envelope.py:69-71 shows error() carries ONLY {status,message} — no code/retriable/recovery/state field — so nothing machine-readable conveys the partial success; an agent applying "error means retry" re-invokes the destructive restart(). The livelock is the extreme case (requires persistently-failing doc store + blind retry) but the destructive/wasteful-retry harm holds even for a single retry.\n\nNot already implemented and not tracked: known-issues.md has zero open entries and nothing about the refresh return envelope; the resolved DS-14/DS-12 handle only the search_docs/status side (doc_index_state/doc_index_error, status.py:95-96) and KI-9 the delegate-teardown hang — none address refresh()'s error envelope ergonomics. The refresh docstring (lines 114-119) explicitly reasoned about the doc-side invariant (rebuild sets state=error → search_docs surfaces it) but never considered the retry-tears-down-the-healthy-analyzer interaction, so this surfaces a genuinely un-considered angle.\n\nNo conflict: implementation-plan.md:313 fixes only 'refresh = unconditional teardown + wholesale re-index' — preserved. Proposal option B (keep status:error, add code:doc_index_unavailable + retriable:false + recovery text) respects the fixed status vocabulary (ok/not_ready/not_found/error) and the read-only/stdio/single-host scope. (Option A, returning ok on a doc failure, is the weaker choice — it slightly strains the 'error=a failure' envelope semantics — but the finding offers B as an alternative, so it is non-conflicting.)\n\nImplementable at small effort: extend error() to accept extra kwargs (or build the dict inline in refresh.py) and add the fields at refresh.py:178; a handful of lines plus a doc/test touch.\n\nImpact revised high→medium: the prose message field already tells a message-reading agent 'Analyzer re-index started', partially mitigating; refresh is a rare explicit recovery action, not a hot path; and full livelock needs an already-degraded persistently-failing doc store. Real and worth fixing, but not high-severity.

### Round-2 red-team verdict

**Claim verdict:** weakened

Mechanism is real: refresh.py:140 calls `await mgr.restart()` unconditionally with no in-flight guard, and analyzer.py:725/837-838 confirms restart drains the running task, bumps _generation, and respawns — so a retry does re-tear-down. But the severity ("destructive," "livelocks so it never reaches ready") is inflated: refresh preserves rust-analyzer's on-disk cargo cache (tools.md:471-473), so each re-index is fast and the analyzer converges to ready between retries; a true livelock needs a blind tight-retry loop against a persistently-failing doc store, and in that case (missing embedding model / unwritable chroma — the actual test scenarios) NO retry helps until a human fixes the env. Round 1's medium is generous; this is closer to low.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **blocker** — Option A (return `ok` on doc-rebuild failure) breaks two existing tests that assert status==error — test_refresh.py:315 (test_rebuild_raises_returns_error_envelope) and :361 (test_reinit_failure_returns_error_envelope) — and the documented contract at tools.md:458 which pins doc-failure→error. It also makes refresh return `ok` while search_docs returns `error` for the same broken index (refresh.py:117-119), which is WORSE for observability than status quo. Round 1 called A merely 'weaker'; it is breaking. Drop A.
- **major** — Option B's `code`/`retriable`/`recovery` fields cannot be added without changing the shared `error()` builder (envelope.py:69-71 is `error(message: str)` only) — i.e. it is a dependency of UR-6's medium-effort structured-fields work, exactly as round 1 flagged for UR-9. UR-7's affected-files lists refresh.py ALONE and rates 'small'; honest scope either pulls UR-6 forward (medium, touches envelope.py used by all tools) or emits a one-off field at a single call site no other tool honors. Effort/scope dishonest.
- **major** — Option B's `retriable:false` + 'analyzer re-index must NOT be restarted; the doc index alone needs another refresh' is self-contradictory. refresh is the ONLY doc-index recovery path (refresh.py:169-171, tools.md:446-449) and ALWAYS restarts the analyzer (settled unconditional teardown, implementation-plan.md:313). So recovering the doc index REQUIRES calling refresh again, which necessarily re-tears-down the analyzer. The hint steers the agent away from its one recovery action.
- **minor** — Internal inconsistency: the only genuinely 'small, refresh.py-only' fix is a message-text edit, but that adds NO machine-readable signal — which is the finding's entire complaint ('an LLM must NLP-parse free-text English'). The machine-readable version (B) is not small; the small version (message) doesn't satisfy the finding. Plus tools.md:458 must change under either option (not in affected files).

**Revised proposal:** Drop Option A. Ship the truly self-contained fix: keep status=error and enrich only the message at refresh.py:178 to say the analyzer re-index is already running and will finish on its own (don't retry to fix the analyzer); the doc index failed and, once the underlying cause (embedding model / chroma path) is fixed, calling refresh again rebuilds it — a restart of the healthy analyzer is expected and harmless because cache is preserved. Keep "documentation rebuild failed" + {exc} so tests at test_refresh.py:315/361 stay green. If deterministic machine-readable fields are wanted, fold UR-7 into UR-6 rather than emitting a one-off code/retriable at a single site.

## UR-8 — The most common `not_ready` message names neither which poll tool to call nor which field/value to wait for

- **Dimension:** error-recovery
- **Impact:** medium (analyst) → low (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `src/rust_lsp_mcp/envelope.py`
  - `src/rust_lsp_mcp/analyzer.py`
  - `src/rust_lsp_mcp/tools/search_docs.py`

**Problem**

The everyday indexing case returns the pure default `not_ready()` (core.py:113) whose text is "Server is still indexing. Retry after checking status." (envelope.py:52). Two readiness tools exist — `status` (status.py:20) and `analyzer_status` (diagnostics.py:13) — and this message names neither, nor the field/value to await. Meanwhile TORN_DOWN_RETRY_MESSAGE says "Retry after analyzer_status reports ready" (analyzer.py:118) and search_docs says "Retry after checking doc_index_state via status" (search_docs.py:109). Three phrasings for the same poll-and-retry loop leave an agent guessing which tool and field constitute the self-healing signal.

**Proposed solution**

Make the require_ready() default `not_ready()` as specific as the torn-down message: name the exact tool, field, and target value, e.g. "Analyzer still indexing. Call analyzer_status and retry this request once its `state == 'ready'`." Standardize the doc-index variant to the same shape ("call status; retry when `doc_index_state == 'ready'`"). Combined with a `recovery:"poll_status"` field this turns the not-ready path into an unambiguous loop.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL and unfixed. The default not_ready() message is literally "Server is still indexing. Retry after checking status." (envelope.py:51-55). require_ready() returns this bare default at core.py:113, and it is the guard emitted by all six gated tools in the common indexing case (hover.py:118, goto_definition.py:81, find_symbol.py:62, document_symbols.py:90, find_references.py:121, diagnostics.py probe:42). It does NOT name the field/value to await (state == 'ready'), and its "checking status" is an oblique noun that does not disambiguate between the two readiness tools that both exist and both report state: status (status.py:20) and analyzer_status (diagnostics.py:13). Two other messages for the same poll-and-retry loop DO name specifics: TORN_DOWN_RETRY_MESSAGE = "...Retry after analyzer_status reports ready." (analyzer.py:116-119); search_docs returns "...Retry after checking doc_index_state via status..." (search_docs.py:107-110). Three phrasings, and only the most common path is vague. The proposal is technically sound: it routes the analyzer case to analyzer_status/state and the doc-index case to status/doc_index_state (only status exposes doc_index_state, per status.py:47-51).

NOT ALREADY IMPLEMENTED / NOT TRACKED. The default message is unchanged in envelope.py. known-issues.md has an empty "Open" section — no KI covers this; the closest resolved item (KI-9) fixed the teardown-hang, not message wording.

NON-CONFLICTING. The envelope contract is "{status, ...extra fields...}" (envelope.py:3, implementation-plan.md:231) and already merges arbitrary extra keys, so adding a recovery:"poll_status" field is additive and backward-compatible — not a reopening of the DECIDED status vocabulary. No conflict with the Settled architecture (implementation-plan.md:376-383).

WORTH IT / EFFORT. Effort:small is accurate — editing one default string in envelope.py plus standardizing one search_docs branch is trivial. However I downgrade impact from medium to LOW: this is message-clarity polish, not a correctness or capability gap. The self-healing loop already works today — the tools are discoverable, docstrings document the poll-and-retry loop, and even the vague default says "retry after checking status." The marginal benefit is reducing a possible wrong-tool guess or extra round-trip on the hottest not-ready path. Real and worth doing, but a usability nicety rather than a medium-impact fix.

### Round-2 red-team verdict

**Claim verdict:** weakened

Core defect is real: require_ready() emits bare not_ready() (core.py:113) whose default (envelope.py:52) names no field/value. But the "agent guessing which tool" framing is inflated: both readiness tools — status (status.py:20) and analyzer_status (diagnostics.py:13, a real registered @mcp.tool, contra UR-10 round-1's note #2) — report `state`, and the default already says "checking status," pointing at the real `status` tool, so a wrong-tool guess still self-heals. Actual gap is narrow (missing field/value), harm near-zero. LOW (round-1's downgrade) is right. Round-1 also undercounts scope: search_docs has THREE not_ready branches (search_docs.py:107,113,131), and 113 ("Retry after the store is ready") itself names no tool/field.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — The `recovery:"poll_status"` field is NOT a free additive merge. Round-1 justified it citing that the envelope 'already merges arbitrary extra keys' — but that is only true of ok(**kwargs) (envelope.py:46). not_ready() is `def not_ready(message=...)` (envelope.py:51), a fixed signature; adding `recovery` requires changing the shared builder API (or every caller). To be coherent it also belongs on error() (also needs refresh-polling). It is undocumented in tools.md:22, unneeded to fix the stated defect, and pre-empts the unspecified 'structured-fields' initiative UR-9 references. Drop it.
- **major** — Wrong hook / layering. Editing envelope.py's GENERIC default to hardcode analyzer vocabulary ('Call analyzer_status ... state==ready') bakes domain knowledge into the pure envelope layer (envelope.py:19-20 'Pure Python, no I/O'). not_ready() is also search_docs's builder — any future doc path hitting the default would emit an analyzer message that is WRONG for the doc index (needs status/doc_index_state). Cleaner hook: a shared constant (mirroring TORN_DOWN_RETRY_MESSAGE, analyzer.py:116) passed from the analyzer call sites, leaving envelope.py generic.
- **minor** — Scope undercount → effort:small honest in LOC but the RIGHT fix is broader. There are 7 bare not_ready() default sites (core.py:113 + hover.py:134, goto_definition.py:100, find_references.py:139 & 177, document_symbols.py:104, find_symbol.py:76); localizing via a constant means updating all 7. And search_docs has 3 not_ready branches (107/113/131) with inconsistent specificity — the proposal treats 'the doc-index variant' as singular.
- **minor** — tools.md is absent from the affected-files list. tools.md:22 documents the not_ready contract as 'Always includes a message field'; CLAUDE.md requires docs currency in the same step. A new `recovery` field (if kept) and the reworded messages need documenting there.

**Revised proposal:** Ship the message-text half only. Define INDEXING_RETRY_MESSAGE in analyzer.py next to TORN_DOWN_RETRY_MESSAGE (e.g. "The analyzer is still indexing. Retry after analyzer_status reports state == 'ready'."); use it at core.py:113 require_ready() and the 6 tool `else not_ready()` fallbacks. Standardize search_docs's three not_ready branches (107/113/131) to one field-naming form ("... after status reports doc_index_state == 'ready'"). Drop the recovery field (no envelope-builder signature change, no schema addition, no tools.md contract churn). Update tools.md:22.

## UR-9 — The catch-all LSP/search error passes a raw exception string with zero next-step guidance

- **Dimension:** error-recovery
- **Impact:** medium (analyst) → medium (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `src/rust_lsp_mcp/tools/hover.py`
  - `src/rust_lsp_mcp/tools/goto_definition.py`
  - `src/rust_lsp_mcp/tools/find_references.py`
  - `src/rust_lsp_mcp/tools/find_symbol.py`

**Problem**

Every nav tool's fallback is `return error(f"LSP error: {exc}")` (hover.py:139, goto_definition.py:105, find_references.py:144, find_symbol.py:81) and search_docs uses `error(f"Documentation search error: {exc}")` (search_docs.py:137). The message is just the raw exception text — it tells the agent nothing about what to DO. This is the one error class with NO actionable recovery hint, in contrast to require_ready() and the doc-index errors which explicitly name `refresh` and `RLM_RUST_ANALYZER_BIN`. Faced with "LSP error: <traceback-ish string>" an agent cannot tell whether to retry, refresh, or give up.

**Proposed solution**

Give the catch-all a recovery clause: "Unexpected analyzer error (<exc>). This may be transient — retry the request once; if it persists, call `refresh` and check server logs." Pair with `code:"lsp_failure"`, `retriable:true` from the structured-fields change so the agent gets a bounded, deterministic retry policy instead of parsing an exception string.

**Round-1 verification**

Verdict: `confirmed`

Problem is real and present in current code. The catch-all fallback returns a raw exception string with no recovery guidance in all five named sites: hover.py:139, goto_definition.py:105, find_references.py:144, find_symbol.py:81 (`return error(f"LSP error: {exc}")`) and search_docs.py:137 (`return error(f"Documentation search error: {exc}")`). The contrast the finding relies on is accurate: require_ready() (core.py:106-111) explicitly names `refresh` and `RLM_RUST_ANALYZER_BIN`, and _errored_build_envelope (search_docs.py:28) says "Run the refresh tool to rebuild it." — so the catch-all is genuinely the only error path with zero next-step guidance. Not tracked: the "Open" section of docs/impl/known-issues.md is empty; no KI covers this. Not implemented: the error() builder in envelope.py:69 takes only a message string. No conflict with settled architecture (implementation-plan.md:376-383, all about transport/read-only/RAG topology) or with read-only/stdio/single-host scope — this is a message-text change, and retry advice is safe because nav queries are idempotent reads. Caveats: (1) "traceback-ish string" is overstated — the envelope carries only str(exc), typically one line; the full traceback goes to logs via _log.exception. (2) The proposal is two parts of unequal cost: the recovery-clause message edit is genuinely small and standalone, but the paired code:"lsp_failure"/retriable:true fields depend on a separate structured-fields envelope change that does not yet exist (error() would need new params), so "effort: small" applies only to the text half.

### Round-2 red-team verdict

**Claim verdict:** weakened

The core observation holds: all catch-all sites (hover.py:139, goto_definition.py:105, find_references.py:144 AND :187, find_symbol.py:81, document_symbols.py:109, search_docs.py:137) emit a raw exc string with no next-step guidance, and it is the only error class lacking one. But "medium" is generous. AnalyzerNotReadyError/AnalyzerTornDownError are already carved out to not_ready (per #98), so this branch fires ONLY on genuinely unexpected exceptions while the analyzer is still ready (require_ready passed) — a rare residue path. The message already carries the exception text, giving the agent something. Round 1 accepted the medium framing without weighing how narrow the surviving bucket is.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — The proposed message ('Unexpected analyzer error...') drops the literal 'LSP error' substring that two committed tests assert: tests/test_goto_definition.py:429 and tests/test_document_symbols.py:289 both do `assert "LSP error" in result["message"]`. Verbatim implementation reddens them. Round 1 checked source only, not tests, and called it non-breaking.
- **major** — The recovery text says 'if it persists, call refresh.' But the catch-all only fires after require_ready() passed, so the analyzer is healthy/ready. implementation-plan.md:312 settles refresh as unconditional teardown + wholesale re-index (minutes of downtime, see UR-7). Advising refresh for a one-off idempotent-read glitch trades a failed query for a full destructive re-index — actively harmful guidance.
- **major** — Scope/effort undercount: affected-files lists 4 nav tools; problem text adds search_docs. But document_symbols.py:109 and find_references.py:187 ('LSP error (definition)') are also catch-all sites and are omitted from both the finding AND round 1's 'all five named sites.' Fixing only the listed sites re-creates the very inconsistency the finding attacks.
- **minor** — Inlining the same recovery clause at 7 sites duplicates the string and invites drift. The repo already single-sources such text (search_docs.py:18 _errored_build_envelope; analyzer.py TORN_DOWN_RETRY_MESSAGE constant reused across sites). A one-line-per-site inline ignores this established cleaner hook.
- **minor** — Labeling this bucket retriable:true / 'may be transient' asserts transience for what is, by construction, the unclassified residue after all known-transient states were routed elsewhere. A deterministic multilspy/parse bug will recur identically on retry, so the blanket retry-once default can just waste a round-trip.

**Revised proposal:** Add one shared constant/helper in envelope.py (mirroring TORN_DOWN_RETRY_MESSAGE / _errored_build_envelope), e.g. lsp_failure(exc) that KEEPS the 'LSP error:' prefix (preserving the two tests) and appends a neutral 'unexpected internal error; retry the request once — the analyzer is still running' — WITHOUT advising refresh (analyzer is healthy here; refresh is a destructive full re-index). Apply to all catch-all sites including document_symbols.py:109 and find_references.py:187. Fold the code:'lsp_failure'/retriable fields into UR-6's envelope change rather than editing every site twice. Then it's genuinely small and consistent.

## UR-10 — `find_symbol` not_found — the sole name→position entry point — is a dead end with no recovery hint

- **Dimension:** error-recovery
- **Impact:** medium (analyst) → medium (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `src/rust_lsp_mcp/tools/find_symbol.py`

**Problem**

find_symbol is the only name→symbol bridge (the position tools take positions only), so almost every workflow starts here. Its not_found messages are bare: `not_found(f"No symbol found matching {name!r}.")` (find_symbol.py:85 and 98). It gives no hint that the query is a fuzzy workspace-symbol match indexing only in-workspace symbols (not dependencies or std), nor that a shorter prefix or exact declared name might match. An agent that gets not_found at the entry point has nothing to act on and typically abandons the navigation task.

**Proposed solution**

Enrich the message with concrete recovery options: "No workspace symbol matches 'X'. Try a shorter prefix or the exact declared name; only in-workspace symbols are indexed (dependencies and std are not). If you just started, confirm readiness via analyzer_status (`state == 'ready'`) before retrying." High leverage because it is the funnel for the whole tool surface.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL. find_symbol.py returns bare not_found messages at exactly the two sites claimed: line 85 `return not_found(f"No symbol found matching {name!r}.")` (LSP returned null) and line 98 (all candidates filtered / zero matches). Neither carries any recovery hint.

FUNNEL CLAIM IS CORROBORATED BY SETTLED DOCS. implementation-plan.md:260 states "find_symbol(name) is the **sole** name→symbol bridge"; the position tools (goto_definition, find_references, hover) take positions only. So a bare not_found here genuinely strands the most common entry workflow. Impact:medium is fair.

NOT ALREADY IMPLEMENTED / NOT TRACKED. envelope.py:58 `not_found(message=...)` already accepts a custom message, so enrichment is a pure string change — trivially implementable, effort:small confirmed. known-issues.md "## Open" section is empty; this is not tracked anywhere.

NO CONFLICT with settled architecture (implementation-plan.md:376). Enriching a message string is read-only, adds no new status.

SUPPORTING CLAIMS CHECK OUT: (a) "only in-workspace symbols indexed (deps/std not)" is accurate — find_symbol.py docstring lines 56-60 and core.py symbol_to_external silently drop candidates whose path resolves outside the workspace root; (b) fuzzy workspace-symbol match is real (analyzer.py:491 request_workspace_symbol, docstring line 24 "fuzzy").

TWO REFINEMENTS TO THE PROPOSED MESSAGE (do not refute the finding, but wording should be trimmed before shipping):
1. The readiness sentence ("If you just started, confirm readiness via analyzer_status before retrying") is misleading in the not_found path. find_symbol is gated by require_ready() at line 62, which returns a not_ready envelope when not ready; not_found (lines 85/98) is only reachable AFTER that gate passes — so a caller who got not_found was already ready. The readiness hint should be dropped; the valuable hints are "try a shorter prefix / exact declared name" and "only in-workspace symbols are indexed."
2. Tool-name nuance: the actual MCP tool function is named `status` (tools/status.py:20), not `analyzer_status`. However the codebase already refers to it as `analyzer_status` everywhere in docstrings/messages (hover.py:90, find_references.py:98, goto_definition.py:60, document_symbols.py:69, analyzer.py:118; refresh.py:87 hedges "analyzer_status (or status)"). So the proposal is consistent with the dominant existing convention — but there is a pre-existing name/reality drift worth a separate cleanup.

Net: confirmed — real, unfixed, untracked, non-conflicting, cheap. Ship with the readiness sentence trimmed.

### Round-2 red-team verdict

**Claim verdict:** weakened

Core defect is real: bare not_found at find_symbol.py:85 and :98, and find_symbol is the settled sole name→position bridge (implementation-plan.md:260-261). But the "medium" severity rests entirely on the unsupported behavioral claim "typically abandons the navigation task" (usability-review.md:313) — no evidence cited, and the fix is purely advisory text with zero correctness impact. Round 1 kept medium without scrutinizing this; it reads as low. Defect stands, severity is inflated.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **minor** — Wrong altitude / not the best fix. Lines 85 (LSP null → truly zero matches) and 98 (candidates WERE returned but all dropped by symbol_to_external) are semantically distinct. At line 98 the tool knows candidates existed and could say 'N matched but resolve outside the workspace' — strictly more actionable. One blanket string for both sites discards that signal.
- **minor** — Factual error in proposed wording. 'only in-workspace symbols are indexed (dependencies and std are not)' misdescribes the mechanism: rust-analyzer DOES index/return dep+std symbols; this tool filters them post-hoc via location_to_external containment (core.py symbol_to_external; DS-02 test rationale in test_ds02_output_containment.py). Correct phrasing is 'returned/navigable', not 'indexed' — otherwise it seeds a wrong mental model.
- **minor** — Edge case glossed: line 98 also fires when candidates are dropped for missing name or malformed location (core.py:348,381,400), not only out-of-workspace. The 'dependencies and std are not indexed' hint is simply wrong in those cases.
- **minor** — Round-1's own two refinements still required: drop the readiness sentence (unreachable after require_ready() gate at find_symbol.py:62) and the analyzer_status vs status.py name drift. These are pre-conditions, not done.

**Revised proposal:** Emit two distinct messages: at line 85 (zero matches) advise a shorter prefix/exact declared name; at line 98 say "matches were found but all resolve outside the workspace — only in-workspace symbols are navigable (dependencies and std are not)." Drop the readiness sentence entirely. Fix "indexed"→"navigable/returned." Still a pure string change, still effort:small, but accurate and site-specific instead of a misleading blanket string.

## UR-11 — No cap or truncation on unbounded LSP result lists (find_references / find_symbol / document_symbols)

- **Dimension:** response-economy
- **Impact:** high (analyst) → medium (verifier-revised)
- **Effort:** medium
- **Affected files:**
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/find_references.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/find_symbol.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/document_symbols.py`

**Problem**

Three tools serialize every hit rust-analyzer returns with no limit, no pagination, and no truncation signal. find_references.py:206 returns `references=list(seen.values())` — the full set; find_symbol.py:100 returns `results=results` (every fuzzy workspace-symbol match); document_symbols.py:130 returns `symbols=symbols` (every symbol in the file). A find_references on a popular symbol (e.g. a common trait method or an Error type) in a large crate can return hundreds-to-thousands of {file,line,character} rows, and document_symbols on a large generated/vendored .rs file can return thousands of symbols. Each such call can dump tens of thousands of tokens into the client context in a single response, with no way for the caller to bound it and no signal that the list was large. Only search_docs exposes a `limit`; the LSP tools do not.

**Proposed solution**

Add an optional `limit: int` (and stateless `offset: int = 0`) parameter to find_references, find_symbol, and document_symbols, defaulting to a sane page size (e.g. 50 refs, 50 symbols). When the underlying list exceeds the page, return the page plus `total: <int>` and `truncated: true` fields inside the existing `ok` envelope (no new status vocabulary, no server-side cursor state — offset slicing keeps it stateless per the settled no-handles decision). This lets an agent see 'this symbol has 812 references, here are the first 50' and decide whether to page rather than blindly ingesting all of them.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL (verified in current code):
- find_references.py:206 — `return ok(references=list(seen.values()))` serializes the full deduped set; signature `find_references(file, line, character, include_declaration=False)` — no limit/offset/truncation param.
- find_symbol.py:100 — `return ok(results=results)` returns every mapped workspace-symbol candidate; signature `find_symbol(name)` — no cap.
- document_symbols.py:130 — `return ok(symbols=symbols)` returns every mapped symbol in the file; signature `document_symbols(file)` — no cap.
The asymmetry claim also holds: only search_docs.py:33 exposes `limit: int = 5` (docs/guide/tools.md:315 documents it); the three LSP list tools' guide tables carry no such column. So a hot-symbol find_references or a large-file document_symbols can dump an unbounded, unsignposted list into client context.

NOT ALREADY FIXED / NOT TRACKED: known-issues.md "Open" section is empty; no KI covers result-size/pagination. The 2026-07-01 defect sweep has no response-economy / result-cap entry. Genuinely untracked.

NO CONFLICT WITH SETTLED ARCHITECTURE — in fact it aligns: implementation-plan.md:271 settles "Stateless: no symbol handles/IDs cached; the assistant passes positions back directly." The proposal's stateless offset-slicing (no server-side cursor/handle) is explicitly consistent with this. The `total`/`truncated` fields live inside the existing `ok` envelope, adding no new status vocabulary.

IMPLEMENTABLE / EFFORT: The change is mechanical per tool (add `limit`/`offset`, slice, add `total`+`truncated`), plus docstring + tools.md updates and tests — "medium" is fair. One caveat: find_references dedups into a dict with no defined sort, so cross-page offset slicing needs a stable sort (e.g. by (file,line,character)) to be coherent across pages.

IMPACT revised high→medium: the failure mode (context flood) is real and directly relevant to an LLM-facing server, but it is bounded by workspace size and the rows are compact {file,line,character}; it bites only pathological hot-symbols / huge vendored files. The single most valuable piece — a `total` count + `truncated` signal so an agent can decide before ingesting — justifies doing it, but "high" overstates the average case. Verdict: confirmed at medium impact.

### Round-2 red-team verdict

**Claim verdict:** holds

Real and unfixed: find_references.py:206, find_symbol.py:100, document_symbols.py:130 each serialize the full list with no cap/count/signal; only search_docs has a limit (search_docs.py:33). But the "tens of thousands of tokens" framing is inflated — rows are compact {file,line,character} (~3 keys), so it takes hundreds-to-thousands of hits to matter, and the pinned prototype target is ripgrep (medium crate, effectively always-ready). Round-1's high→medium is generous; low-to-medium is more honest. The single defensible value is a total count + truncated signal, not full pagination. Claim survives at medium.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — Default page size 50 silently truncates the documented full-list contract for EVERY existing caller. implementation-plan.md:294-298 pins these tools as returning the whole list (references/symbols/results); tools.md documents no cap. A find_references returning 60 refs would now return 50+truncated:true by default — a behavioral break to a settled schema, not an opt-in. Round-1's 'no conflict with settled architecture' missed this: the Phase-3 schema block IS the conflict.
- **major** — Offset pagination is incoherent for these tools. Each page re-runs the FULL analyzer query (analyzer.py:517/552/643 return everything; the server only slices) so pagination saves zero analyzer cost, only client tokens. Worse, find_symbol (fuzzy rank) and find_references (delegates apply no sort — order is raw LSP order) have no guaranteed stable cross-call order, and refresh is an explicit agent-driven single-host action; a re-rank or mid-flight refresh between page 1 and page 2 silently skips/duplicates rows. Server-side cursors would fix it but violate the settled no-handles/stateless decision (implementation-plan.md:271). Pagination is the wrong mechanism here.
- **major** — offset past the end returns ok+references=[] / symbols=[], colliding with the load-bearing settled semantic that ok+[] means 'real symbol, zero callers' (find_references.py:45-58; the exact distinction UR-5 exists to protect). Paging past a symbol's last reference is now indistinguishable from a dead function unless the agent cross-checks total. New failure mode the proposal never mentions.
- **minor** — Round-1's own fix for the ordering gap (stable-sort find_references by (file,line,character)) destroys document_symbols' documented + tested declaration-order contract if applied uniformly: tools.md says 'in declaration order' and test_document_symbols.py:196-212 asserts Alpha/beta/GAMMA order. The proposal never reconciles 'stable sort for paging' with 'preserve rust-analyzer's order'.
- **minor** — `total` semantics vs filtering is unspecified. All three tools drop unmappable / out-of-workspace candidates (find_references.py:160-162, find_symbol.py:92-94, symbol_to_external). total must be the post-filter length to match the returned rows; the proposal says 'total: <int>' without defining it, inviting a total that disagrees with what's pageable.

**Revised proposal:** Drop offset/pagination. Keep returning the full list, but always add `total: len(list)` and apply one high safety cap (e.g. 200) that sets `truncated: true` only when exceeded. This delivers round-1's single valuable piece ('812 refs, here are 200') with no default-truncation contract break, no per-page analyzer recompute, no cross-call consistency hazard, and no ok+[] collision. Narrowing (a more specific position/name) replaces paging — natural for these query shapes. Effort drops from medium to small.

## UR-12 — search_docs `limit` is floor-clamped only — no upper bound on response size

- **Dimension:** response-economy
- **Impact:** medium (analyst) → low (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/search_docs.py`

**Problem**

search_docs.py:88 is `limit = max(1, limit)` — a floor with no ceiling. The docstring (lines 41-43) says only 'Clamped to at least 1.' Each returned chunk's `text` is capped at ~200 tokens (BODY_TOKEN_CAP), so a caller passing `limit=500` (or an agent that fills in a large number) can pull ~100k tokens of doc chunks in one response. Position tools strictly reject out-of-range input, but here an oversized limit is silently honored, making search_docs the single largest uncapped payload the server can emit.

**Proposed solution**

Add an upper clamp, e.g. `limit = max(1, min(limit, MAX_DOC_RESULTS))` with MAX_DOC_RESULTS around 20-25, and document the ceiling in the docstring. Optionally surface a `truncated: true` flag when the requested limit was reduced so the caller knows more could be requested via narrower queries.

**Round-1 verification**

Verdict: `confirmed`

Problem is REAL and unfixed. search_docs.py:88 is literally `limit = max(1, limit)` — floor-only, no ceiling; docstring (search_docs.py:42-43) documents only "Clamped to at least 1." BODY_TOKEN_CAP=200 is confirmed (doc_chunking.py:74) with the enforced invariant estimate_tokens(chunk.text) <= BODY_TOKEN_CAP, so each returned chunk's `text` is ~200 body tokens. A caller (or an LLM auto-filling a big number) passing limit=500 is honored silently, whereas position tools strictly validate input — the consistency gap the finding describes is genuine.

Untracked: grep of docs/impl/known-issues.md for limit/cap/payload/economy/token returned nothing. Not implemented, not partially implemented. No conflict with settled architecture: implementation-plan.md:330 specifies "one search_docs(query) tool" and line 230 the response-envelope decision; neither pins limit semantics, and an upper clamp is envelope-compatible and orthogonal to read-only/stdio/single-host scope.

One correction to the finding: it overstates severity by calling this the "single largest uncapped payload." doc_store.py:366 already clamps effective_n = min(n_results, count), so the response is bounded by the indexed corpus size, not by the caller's arbitrary number. limit=500 yields min(500, count) chunks; the ~100k-token scenario only occurs if the corpus actually holds >=500 chunks, for which there is no evidence here. It is uncapped relative to caller input (up to full-corpus dump) but not literally unbounded. Hence revised_impact low: a cheap, consistency-improving defensive hardening rather than an active correctness/context-blowout bug. Proposal (limit = max(1, min(limit, MAX_DOC_RESULTS)) with MAX_DOC_RESULTS ~20-25, plus docstring note and optional truncated flag) is small, implementable, and worth doing.

### Round-2 red-team verdict

**Claim verdict:** weakened

Bare fact is true: search_docs.py:88 is floor-only `max(1, limit)`. But the Problem's framing ("~100k tokens", "single largest uncapped payload") is refuted — doc_store.py:365-366 clamps effective_n=min(n_results,count), so output is corpus-bounded; round 1 already downgraded to low. I add a second deflation round 1 missed: tests/test_search_docs.py:369-374 (`test_limit_large_passed_through`, docstring "Large limit values are not clamped from above.") deliberately enshrines the no-ceiling behavior. So this is not an unfixed oversight but a tested, considered choice — reframing "hardening gap" as "reverse a decided behavior." Net: borderline-drop nit.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — Breaks an existing CI-tier test the proposal never mentions: tests/test_search_docs.py:369-374 asserts limit=100 passes through as n_results=100 with docstring 'Large limit values are not clamped from above.' Adding min(limit,20-25) forces flipping this deliberately-opposite test. 'Effort: small' glosses over reversing a tested, considered behavior.
- **major** — The optional `truncated: true` flag is semantically wrong for semantic search. Chroma returns top-k nearest neighbors by distance — there is no match/non-match boundary and no meaningful 'total'. Firing truncated when the requested limit was reduced conflates 'your number was capped' with 'more relevant results exist', which is usually false (if corpus<cap nothing is cut; if corpus>cap the top-k already ARE the most relevant). The rationale 'more could be requested via narrower queries' is confused: narrowing won't surface better hits than the returned top-k.
- **minor** — MAX_DOC_RESULTS ~20-25 is an arbitrary magic ceiling that silently reduces recall for a legitimate broad-survey query, with no error. The position-tool analogy fails: position tools reject INVALID coordinates; a large doc limit is a VALID request. Silent clamping is arguably worse ergonomics than status quo for the rare power user.
- **minor** — Wrong altitude / duplicate convention. This is the same response-economy concern as UR-11 (limit/offset/total/truncated on the LSP list tools). Implementing a one-off min() plus a bespoke `truncated` flag here independently yields two divergent truncation conventions (UR-11 uses total+truncated with real counts; UR-12's truncated has different, misleading semantics). The cleaner fix designs both together.

**Revised proposal:** If kept, implement only the bare ceiling `limit = max(1, min(limit, MAX_DOC_RESULTS))`, drop the `truncated` flag entirely (semantically wrong for k-NN search), and consciously flip test_limit_large_passed_through with a comment explaining the reversal. Better: fold into UR-11 so all list-returning tools share one cap convention, and pick MAX high enough (or make it configurable) that legitimate broad queries aren't silently clipped. Given corpus-bounded output and a deliberate existing test, dropping UR-12 is also defensible.

## UR-13 — Position-only results force a follow-up round-trip per hit

- **Dimension:** response-economy
- **Impact:** medium (analyst) → medium (verifier-revised)
- **Effort:** medium
- **Affected files:**
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/find_references.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/find_symbol.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/document_symbols.py`

**Problem**

find_references (find_references.py:87-91), find_symbol (find_symbol.py:36-43), and document_symbols (document_symbols.py:45-51) return bare position rows — {file,line,character} (+ name/kind/container for the symbol tools) — with no source text. An agent that gets 30 references cannot tell which are the interesting call sites without either Reading each file or issuing 30 hover calls, so the compact response actually inflates total token spend by forcing N follow-up calls. search_docs, by contrast, inlines the matched `text` and needs no follow-up.

**Proposed solution**

Attach a single-line source snippet per hit — a `snippet: str` field containing the trimmed text of the referenced line (cheap: one line of the file the analyzer already resolved). One line per hit is far smaller than a hover/Read round-trip and lets the agent triage 'which of these references matter' in a single response. Make it opt-out (e.g. `include_snippet: bool = True`) so a caller optimizing purely for size can suppress it.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL AND UNFIXED. Verified by reading current source:
- find_references.py:203-206 returns `ok(references=list(seen.values()))` where each entry is built at lines 156-164 as `location_to_external(loc, repo_root)` → only `{file, line, character}`. No source text. Docstring lines 87-91 confirm the exact three-key shape.
- find_symbol.py:89-100 appends `symbol_to_external(sym, ...)` yielding `{name, kind, file, line, character, container}` (docstring 36-43). No source text.
- document_symbols.py:121-128 explicitly constructs `{name, kind, line, character, container}` (docstring 45-51). No source text.
Contrast confirmed: search_docs.py:53 inlines `"text": str` (the embedded chunk), so the RAG tool needs no follow-up, exactly as the finding states.

The triage argument holds — and is arguably understated. hover at a reference site returns the hovered symbol's type/doc, identical for every reference to the same symbol, so hover is useless for distinguishing call sites; the agent's only recourse for bare position rows is to Read each referenced file. So a 30-hit find_references genuinely forces N file reads to answer "which of these matter."

NOT ALREADY IMPLEMENTED / NOT TRACKED. No `snippet`/`include_snippet` field anywhere in the three tools. known-issues.md "Open" section is empty; nothing about snippets or response-economy. document_symbols' docstring says positions are "suitable for feeding directly into hover/goto_definition/find_references," i.e. the design intent was hover-for-detail, but that intent does not cover reference triage, so this is a genuine gap, not a settled rejection.

NO CONFLICT WITH SETTLED ARCHITECTURE (implementation-plan.md:376-383). Settled points are transport/caching/RAG-shape decisions; none touch response shape. Read-only scope is not violated — a single-line source read is a read. Precedent exists in-repo: doc_store.py:271 already does `filepath.read_text(...)` on workspace files.

IMPLEMENTABLE, effort ~medium is fair. One correction to the finding's framing: the analyzer (multilspy) returns positions, not the line text — the server must itself read the file from disk. That's still cheap (locations are already validated workspace-relative paths; group hits by file to read each once; the snippet is the whole trimmed line so UTF-16/column concerns are moot). Work = a small line-fetch helper + grouping/caching, wiring into 3 tools, the `include_snippet: bool = True` opt-out, docstring updates, and tests. Impact medium is right: it's a real multi-hit agent-usability win with a clear in-repo precedent (search_docs), not a must-fix correctness bug.

All four gates pass: problem real, unfixed, untracked, non-conflicting.

### Round-2 red-team verdict

**Claim verdict:** weakened

Real for find_references only. There the agent triages MANY same-symbol hits (find_references.py:206 → {file,line,character}), so N follow-ups is genuine — round-1 correctly narrowed it. But the finding asserts the same N+1 for find_symbol and document_symbols, which is overstated. find_symbol results already carry name/kind/container (find_symbol.py:36-43); the agent picks ONE candidate and hovers/gotos it, not all N. document_symbols is an outline you scan by name/kind then drill into one symbol (docstring lines 53-58 explicitly position it as feed-into-hover). Neither forces N reads. Round-1 accepted a uniform "3-tool" framing it never tested against actual usage.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **blocker** — Collides with UR-16 in the same audit, also confirmed. UR-16 adds the identical source-line to find_references (+goto_definition) but as field `source`, param `context: bool=False`. UR-13 uses field `snippet`, param `include_snippet: bool=True`. Same data, different name, opposite default, different tool set. Implementing both yields duplicate params and two names for one line on find_references. Round-1 never cross-checked UR-16.
- **major** — On-by-default contradicts the audit's own economy findings (UR-14/UR-15 remove redundant bytes) and UR-13's own thesis. For document_symbols on a large file (thousands of symbols, per docstring 60-63/UR-15), a full source line per symbol ≈ re-sending most of the file — default-on, so every existing caller pays the largest token add in the audit without opting in.
- **major** — IO/perf understated. References for one symbol scatter roughly one-per-file across many files (test_find_references.py:242 spans src/a.rs, src/b.rs), so round-1's "group by file, read once" saves little: a 150-ref hit means ~150 synchronous file opens + line scans in the request path per call. "Cheap: one line the analyzer already resolved" is false — multilspy returns positions only; the server must read+index each file.
- **major** — Test-fixture cost undercounts "medium" effort. Fast unit tests mock the analyzer boundary but not the filesystem: _make_manager sets _repository_root="/fake/repo" with fabricated non-existent hit paths (test_find_references.py:60, _make_location). Snippet reading crosses an unmocked disk boundary, so every existing fast test now exercises the degrade/OSError path; happy-path snippet requires new real-file fixtures across three tools plus per-hit failure handling.
- **minor** — Snippet can lie. The disk line is read live but the position came from the (possibly stale) index — after an edit or mid-refresh the line text won't match the labeled symbol, making the added text misleading in a way bare coordinates were not. Also leaves goto_definition (same bare-position shape, core.py location_to_external) un-enriched, creating an inconsistent surface unless reconciled with UR-16.

**Revised proposal:** Merge UR-13 into UR-16 as one enrichment: a single opt-in param (off by default, preserving economy and the settled compact contracts in tools.md), one field name, scoped to the tools with a true triage problem — find_references and goto_definition (multi-hit position lists). Drop find_symbol/document_symbols from scope (they already carry name/kind/container for triage; snippet there is bloat, worst on large-file outlines UR-15 is trimming). Group by file, cap per-call file reads, and degrade to omitted-key (not null) on read failure so mocked tests and out-of-workspace hits stay clean.

## UR-14 — search_docs ships the breadcrumb twice in every result

- **Dimension:** response-economy
- **Impact:** medium (analyst) → low (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/doc_store.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/search_docs.py`

**Problem**

Each search hit carries a `breadcrumb` field AND a `text` field, but `text` is built as `"{breadcrumb}\n\n{body}"` (doc_chunking.py:101; the DocChunk.text used verbatim as the embedded document) and returned unmodified at doc_store.py:385 (`"text": doc_text`). So the heading trail (e.g. `"GUIDE.md > Configuration > Ignoring files"`) is duplicated in every result — once as `breadcrumb`, once as the prefix of `text`. Across a multi-result response this is pure redundant bytes with zero added information.

**Proposed solution**

In DocStore.search, strip the `"{breadcrumb}\n\n"` prefix from `doc_text` before assigning `text` so `text` carries body-only while `breadcrumb` stays the structured field (the prefix length is known — it is exactly `breadcrumb + "\n\n"`). Update the search_docs docstring shape accordingly. The client can reconstruct the embedded form if ever needed, but the common case stops paying for the breadcrumb twice.

**Round-1 verification**

Verdict: `confirmed`

Problem is REAL and unfixed. doc_chunking builds every chunk's text as f"{breadcrumb}\n\n{body}" (docstring at doc_chunking.py:101; constructed at lines 545/582/674/691/704, etc.). doc_store.py:279 stores chunk.text (=breadcrumb+\n\n+body) as the embedded document, and line 280 stores breadcrumb separately as metadata. doc_store.py:384-385 returns BOTH "breadcrumb": meta[...] AND "text": doc_text (the embedded document, unmodified) — so the heading trail ships twice per hit. Confirmed by reading the code, not a misread.

Not already implemented (line 385 assigns doc_text verbatim, no stripping) and not tracked in known-issues.md (the only doc-adjacent entry, KI-6, is an unrelated ripgrep status-tool docstring issue).

No conflict with settled architecture: the "breadcrumb prepended" decision (implementation-plan.md:343-348) governs what gets EMBEDDED, which the proposal leaves intact (semantic quality unaffected). The proposal changes only the returned response shape. No conflict with read-only/stdio/single-host scope.

Implementable at small effort: the prefix breadcrumb + "\n\n" is deterministic and present on every chunk-text construction path, so a startswith-guarded strip at doc_store.py:385 is safe (empty-body/edge chunks degrade gracefully). Tests won't break structurally — shape assertions are {file, breadcrumb, text, distance} (test_doc_store.py:282, test_phase5_integration.py:169) and no test asserts text starts with the breadcrumb. Docstrings at search_docs.py:53 and doc_store.py:338 ("the chunk text that was embedded") need updating, which the proposal acknowledges; the client can reconstruct the embedded form via breadcrumb + "\n\n" + text, so no information is lost.

Correction: impact should be LOW, not medium. The breadcrumb is a short heading trail (~40-80 chars) versus a ~200-token body; at default limit=5 the redundant bytes total roughly 200-400 bytes per response — a real but minor economy. Verdict remains confirmed (real, unfixed, untracked, non-conflicting, worth the small effort), with impact revised down to low.

### Round-2 red-team verdict

**Claim verdict:** holds

Problem is real and correctly downgraded to low. doc_chunking builds chunk.text as f"{breadcrumb}\n\n{body}" (doc_chunking.py:539,760,674,691,704, DocChunk docstring :101); doc_store.py:279 stores it as the embedded document, :280 stores breadcrumb as metadata, :384-385 returns both verbatim — breadcrumb ships twice. Round-1 MISSED a favorable fact that sharpens the case: docs/guide/tools.md:338-339 and :358-359 ALREADY document `text` as body-only (breadcrumb stripped). So current code contradicts its own published contract; the fix reduces drift rather than creating it, and round-1's "docstrings need updating" is incomplete/misframed (guide already matches target; real drift is code vs guide). ~40-80 char breadcrumb ×5 vs ~200-token bodies → low. Holds at low.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — Glosses over header-only chunks. A headed section with empty body (doc_chunking.py:760 else-branch `... if body else breadcrumb`, and :773-778) is emitted+stored with text==breadcrumb, NO `\n\n`, no body (not skipped — :921 skips only level==0 preamble). The proposal's literal 'prefix length is known — exactly breadcrumb+"\n\n"' slice (doc_text[len(breadcrumb)+2:]) yields "" for these (slice past end) = silent data loss; a startswith-guard instead leaves text==breadcrumb = the very double-ship it targets. Real in ripgrep docs (parent header holding only subsections).
- **minor** — Round-1's 'client can reconstruct embedded form via breadcrumb+"\n\n"+text, no info lost' is false for header-only chunks: reconstruction gives breadcrumb+"\n\n"+breadcrumb, not the stored breadcrumb. Also produces an inconsistent contract — text is body-only for most hits but the whole breadcrumb for header-only hits, so a consumer trusting 'text=body' gets a breadcrumb masquerading as body.
- **minor** — Better-specified fix exists and is equally small: strip only when doc_text.startswith(breadcrumb+"\n\n"), else set text="" (body-only, possibly empty) so redundancy is fully removed and the contract stays consistent. No test blocks this — shape tests assert isinstance str only (test_doc_store.py:285, test_search_docs.py:250), none assert non-empty text or text-starts-with-breadcrumb on SEARCH output (the startswith asserts at test_doc_chunking.py:164-166,192 are on DocChunk, which the proposal doesn't touch). Alternative of storing body in chroma metadata is robust but doubles body storage — worse for a large corpus.

**Revised proposal:** Keep the read-time strip in DocStore.search but guard it: `text = doc_text[len(bc)+2:] if doc_text.startswith(bc+"\n\n") else ""` (empty body for header-only chunks) — fully removes the double-ship AND keeps a consistent 'text = body-only' contract, no test breaks. Update the two stale docstrings (doc_store.py:338, search_docs.py:53); leave DocChunk.text docstring (doc_chunking.py:101) and the settled 'breadcrumb prepended' embedding decision untouched. Note tools.md:338-359 already matches the target shape, so no guide edit needed.

## UR-15 — document_symbols emits `container: null` on every entry

- **Dimension:** response-economy
- **Impact:** low (analyst) → low (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/document_symbols.py`

**Problem**

document_symbols.py:121-127 unconditionally includes `"container": mapped["container"]` on every symbol, and its own docstring (lines 60-63) notes container is 'almost always null' because rust-analyzer rarely populates containerName for the flat document-symbol response. On a large file this ships a redundant `"container": null` key on every one of potentially thousands of symbols — dead bytes that carry no information in the common case.

**Proposed solution**

Omit the `container` key when it is null (include it only when rust-analyzer actually populated it). An absent key is unambiguously 'no container' and is documented, so no caller information is lost; this trims per-entry overhead across the whole outline. (find_symbol, where container is more often meaningful, can keep emitting it, or adopt the same omit-when-null rule for consistency.)

**Round-1 verification**

Verdict: `confirmed`

Problem is real and unfixed: document_symbols.py:121-127 unconditionally emits "container": mapped["container"] (line 126), and its own docstring (lines 60-63) states container is "almost always null" for rust-analyzer's flat document-symbol response. core.py:411 confirms container = sym.get("containerName"), which is None when absent. So a large-file outline ships a dead "container": null pair on every symbol. Not tracked: the Open section of docs/impl/known-issues.md (line 30) is empty; no KI covers this. Non-conflicting: the "Settled architecture" block in implementation-plan.md:376 addresses transport/read-only/RAG/caching, nothing about output-key presence; read-only/stdio/single-host scope is untouched. Strong in-file precedent supports the proposal: the same function already drops the redundant "file" key with identical reasoning (lines 119-120: "repeating it on every entry would be redundant noise"). Implementable and small: guard the one key (add container only when not None) plus a required docstring update (lines 45-51 and 50 document the key and "str | null"; must document absent-key = no container). Note find_symbol.py:95 appends the full mapped dict and does NOT share this path, so it is unaffected — the single affected file claim is correct. Impact is low as claimed: savings are ~18 bytes/symbol, meaningful only on files with thousands of symbols.

### Round-2 red-team verdict

**Claim verdict:** weakened

test

**Solution verdict:** needs-revision

**Recommended action:** revise

- **blocker** — test issue

## UR-16 — Reference/definition results are bare coordinates — every hit forces a follow-up Read or hover

- **Dimension:** workflow-gaps
- **Impact:** high (analyst) → high (verifier-revised)
- **Effort:** medium
- **Affected files:**
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/find_references.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/goto_definition.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/core.py`

**Problem**

find_references returns each hit as only {file, line, character} (find_references.py lines 87-91, 206), and goto_definition/find_symbol are the same via location_to_external/symbol_to_external. For the canonical 'rename impact analysis' or 'who calls X' workflow, the agent gets N naked positions with zero indication of what each call site actually is. To make any decision it must then Read each file (a non-MCP round trip) or fire hover at each position — turning a 1-call question into 1+N calls. Nothing in the payload lets the agent rank, skip, or summarize sites without opening them.

**Proposed solution**

Add an opt-in enrichment param (e.g. context: bool = False) to find_references and goto_definition. When true, each entry gains "source": the trimmed text of that source line (cheap — the server has repo_root + the workspace-relative path and can read one line, staying within read-only scope), and "container": the name of the smallest enclosing symbol (derived by reusing the existing document_symbols data flow for that file). Keep it off by default so token budget is preserved for callers that only need coordinates. This collapses the N+1 pattern into a single call for the highest-frequency navigation workflows.

**Round-1 verification**

Verdict: `confirmed`

Problem verified REAL and unfixed. find_references.py returns each hit via core.py location_to_external (core.py:293-306), which yields exactly {"file","line","character"} — confirmed by the docstring payload contract (find_references.py:87-91) and the return statement (line 206). goto_definition.py is identical: docstring 46-50, mapping at 113-118, return ok(definitions=...) at 124, same helper. So for who-calls-X / rename-impact the agent gets N naked positions and must Read each file or fire hover N times — the N+1 pattern is genuine; nothing in the payload lets it rank/skip/summarize.

Untracked: known-issues.md "Open" section is empty; no KI names reference enrichment. Not already implemented: grep across src/ found no context/source/snippet parameter or any line-text enrichment path.

No conflict with settled architecture (implementation-plan.md:376-383). The only relevant settled rule bans caching cross-file-dependent results and defers invalidation to rust-analyzer/salsa; the proposal caches nothing — it reads live (a one-line filesystem read + a live documentSymbol request) — and stays read-only, stdio, single-host. Path safety already exists: validate_workspace_file + location_to_external containment-check paths and skip out-of-workspace (stdlib) refs, so reading one in-workspace source line is within read-only scope.

Feasibility/effort caveat: the two enrichments differ in cost. "source" (trimmed source line) is trivially cheap and captures most of the value — a near-slam-dunk that collapses N+1 into 1 for the dominant navigation workflow. "container" is heavier than the finding implies: it is NOT free reuse. References are bare Locations with no name/container, and progress.md:105/122 plus document_symbols.py:62 confirm rust-analyzer rarely populates containerName, so container must be derived via an extra request_document_symbols LSP round trip per distinct file plus range-nesting containment logic, across two tools, with tests. "effort: medium" is realistic only if container is the optional heavier half; source alone is closer to low effort. Impact remains high because the source-line half alone eliminates the highest-frequency N+1 round-trip. Confirmed: real, unfixed, untracked, non-conflicting, worth doing.

### Round-2 red-team verdict

**Claim verdict:** holds

Real and unfixed: find_references.py:87-91,206 and goto_definition.py:46-50,124 return bare {file,line,character} via location_to_external (core.py:293-306); no context param exists in src/. But the "1→1+N" magnitude is overstated: one Read covers every hit in a file, so the true follow-up cost is 1 + distinct-files (F), not 1+N hits — round 1 accepted the inflated N framing. Also goto_definition usually returns 1–few sites, so bundling it is low value; the genuine gap is find_references. Contrast: find_symbol/document_symbols already carry name/kind/container (core.py:405-412), so the deficit is specific to the two location-only tools.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — The 'container' half rests on a false 'reuse existing document_symbols data flow' claim. That flow discards the tree (analyzer.py:519-544 keeps element[0] only) and symbol_to_external emits only a START position, no range end (core.py:405-412; document_symbols.py:119-127). Smallest-enclosing-symbol needs each symbol's full range to test containment — net-new range-nesting logic over raw request_document_symbols + one LSP round trip per distinct file, not reuse. containerName is separately near-always null (tools.md:133).
- **major** — New failure mode round 1 missed: live source read against a possibly-stale index. Settled architecture does no file watching and defers invalidation to salsa (implementation-plan 'Settled architecture'). Disk bytes can drift from the indexed coordinates, so the 'source' snippet can show the wrong line / mismatched text — actively misleading, worse than no snippet. No snapshot/consistency guard is proposed.
- **major** — Introduces direct filesystem reads of workspace .rs files — a net-new capability (grep confirms zero open()/read in tools/, core.py, analyzer.py; all source access goes through multilspy). This newly exposes symlink-inside-workspace→outside reads that the lexical containment check explicitly does NOT catch (core.py:196-240 docstring; tools.md goto_definition note). Round 1's 'within read-only scope' glosses this scope expansion.
- **minor** — Hidden cost/altitude: for a hot symbol, one fast references call becomes F file reads (each read-until-line-N; text files aren't line-indexed) plus up to F document_symbols LSP round trips, synchronous, no partial results. Agents will default context=True. Needs per-file grouping, errors='replace' decode, and graceful degradation — document_symbols raises 'error' on unreadable files (analyzer.py), so one bad file must not sink the whole enriched call.

**Revised proposal:** Ship source-only enrichment, scoped to find_references, via a shared enrich helper that groups hits by file, reads each file once (errors='replace'), and documents point-in-time snapshot semantics (snippet may lag the index; no consistency guarantee). Drop or defer 'container' — the source line usually already reveals the enclosing call context, and deriving container is net-new range-containment logic + per-file LSP calls, not the advertised reuse. Leave goto_definition unenriched (low value). This is genuinely low, not medium, effort.

## UR-17 — No call-hierarchy tool — 'trace a call chain' degrades into recursive find_references with no caller identity

- **Dimension:** workflow-gaps
- **Impact:** high (analyst) → high (verifier-revised)
- **Effort:** large
- **Affected files:**
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/find_references.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/analyzer.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/core.py`

**Problem**

find_references returns call-site positions but never says which function contains each call. To walk a call chain upward ('who calls foo, and who calls them'), the agent must, at every hop and for every reference, first resolve the enclosing function itself (an extra document_symbols + range-matching step) before it can find_references again. Depth-D tracing therefore costs O(D x refs) round trips plus manual containment math the server is better positioned to do. This is exactly the LSP callHierarchy/incomingCalls use case, and it is read-only — it does not reopen any settled decision.

**Proposed solution**

Add a read-only incoming_calls(file, line, character) tool returning callers as [{name, kind, file, line, character}] (and optionally outgoing_calls). It reuses the existing position-in / 1-indexed / envelope / require_ready plumbing, so the only new work is plumbing a callHierarchy request through AnalyzerManager. Verify multilspy 0.0.15 exposes call-hierarchy; if not, issue the raw prepareCallHierarchy/incomingCalls LSP requests via the existing manager handle. Even a single incoming_calls hop replaces the find_references + per-ref enclosing-symbol resolution dance.

**Round-1 verification**

Verdict: `confirmed`

1. PROBLEM IS REAL (verified in source). find_references emits only positional refs with no caller identity: tools/find_references.py builds each entry as {file, line, character} (docstring lines 87-91; mapping loop lines 156-164 keyed on (file,line,character) only). The settled schema confirms this is by design (implementation-plan.md:300-301: `references: [{file, line, character}]`). Nothing in the reference payload names the enclosing function, so to walk a call chain upward the agent must, per hop and per ref, separately resolve the containing symbol — exactly the O(D×refs) round-trip degradation the finding describes.

The gap is actually WORSE than the finding states: the only enclosing-symbol tool, document_symbols, emits just a START position, not a span — tools/document_symbols.py:121-127 builds {name, kind, line, character, container} with no end/range. So the "document_symbols + range-matching" workaround the finding assumes isn't even reliably possible: with no function end-line, containment must be heuristic (nearest-preceding symbol), which breaks on nested items, impl blocks, and macros.

2. NOT IMPLEMENTED, NOT TRACKED. No call-hierarchy tool exists (tools/ has diagnostics, document_symbols, find_references, find_symbol, goto_definition, hover, refresh, search_docs, status only). analyzer.py has delegates only for workspace_symbol/document_symbols/definition/references/hover — no call_hierarchy/incoming/outgoing. known-issues.md "## Open" section is empty; no KI covers this.

3. NO CONFLICT with settled architecture (implementation-plan.md:376-383). incoming_calls is a read-only live query, position-based file/line/character (consistent with the DECIDED "Option A strict separation, position-based actions", :259-272), stateless with no cross-file caching (:271-272). It reuses the existing envelope/require_ready/1-indexed/_race_teardown plumbing. It is a non-breaking additive tool — the plan explicitly blesses such adds (:285-286 "a cheap non-breaking add later").

4. IMPLEMENTABLE, and the finding's fallback claim is CORRECT. multilspy 0.0.15's high-level LanguageServer does NOT expose call hierarchy — it has only request_definition/references/document_symbols/hover/workspace_symbol/completions (language_server.py:363-856). BUT the low-level request layer reachable via self._lsp.server.send DOES: lsp_requests.py:103 prepare_call_hierarchy → textDocument/prepareCallHierarchy, :112 incoming_calls → callHierarchy/incomingCalls, :120 outgoing_calls, all over send_request (server.py:460). rust-analyzer advertises callHierarchyProvider (lsp_types.py:3679).

One honest caveat on effort scope: this is a TWO-hop LSP dance (prepareCallHierarchy → incomingCalls), each requiring open_file context and its own _race_teardown wiring (mandatory per KI-9's "every self._lsp await must go through _race_teardown"), plus CallHierarchyItem→{name,kind,file,line,character} mapping and tests — so the finding's claim that "the only new work is plumbing a callHierarchy request" understates it slightly, but "effort: large" is consistent with that true scope. impact:high is defensible because upward call-tracing is a core code-navigation use case that today costs O(D×refs) round trips plus unreliable manual containment math. Minor: the new capability is more naturally a new tools/incoming_calls.py + analyzer.py delegate than an edit to find_references.py (listed as affected), but that's a placement detail.

Problem real + unfixed + untracked + non-conflicting → confirmed.

### Round-2 red-team verdict

**Claim verdict:** weakened

Gap is real: find_references emits only {file,line,character} (find_references.py:87-91,156-164), settled schema fixes that shape, no call-hierarchy tool/delegate exists (analyzer.py delegates: workspace_symbol/document_symbols/definition/references/hover only), callHierarchy is the correct primitive. But the cost model is overstated and round-1 amplified it: the workaround is document_symbols per DISTINCT FILE (reusable across all refs in that file), i.e. O(D×files), not the finding's O(D×refs). The genuine differentiator is unreliable nearest-preceding containment (no end-range), not round-trip count. Capability genuinely absent, so gap holds; quantitative framing inflated.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — Proposal says it reuses location_to_external, but that helper reads range.start (core.py:300-305); CallHierarchyItem.range is the full decl incl comments/attrs, selectionRange is the name (lsp_types.py:1067-1069). Returned positions won't round-trip into the next prepareCallHierarchy hop, breaking the upward walk the tool exists for. Needs symbol_to_external's selectionRange-first logic.
- **major** — Two-level null semantics unspecified: prepareCallHierarchy null -> not_found vs incomingCalls [] -> ok+empty. Raw server.send.* returns JSON-RPC null as Python None, bypassing multilspy's AssertionError quirk, so _is_null_response_assertion is NOT reused. 'Reuses plumbing; only new work is one request' is misleading; new null-handling logic+tests required.
- **minor** — Payload [{name,kind,file,line,character}] drops CallHierarchyIncomingCall.fromRanges (lsp_types.py:1103-1112), the call-site locations find_references returns today. Not a strict superset; multiple calls from one caller collapse.
- **minor** — CallHierarchyItem.data must be threaded verbatim from prepare into incomingCalls (lsp_types.py:1071-1073); dropping/reconstructing it can break resolution. Unmentioned.
- **minor** — KI-9 requires open_file+prepare+incoming be composed into ONE coroutine passed to _race_teardown; proposal doesn't specify this (round-1's 'two _race_teardown wirings' overstates it).

**Revised proposal:** Keep the callHierarchy mechanism (right primitive; enriching find_references would break the DECIDED references schema, server-side containment is unreliable). Tighten the spec first: (1) map positions via selectionRange (name), not location_to_external's range.start, so they round-trip into the next incoming_calls; (2) define prepare=null->not_found vs incoming=[]->ok+empty; (3) include fromRanges to be a true superset of find_references; (4) thread CallHierarchyItem.data verbatim; (5) compose the dance into a single _race_teardown coroutine (KI-9).

## UR-18 — document_symbols throws away rust-analyzer's signature (detail), forcing one hover per symbol to understand a file

- **Dimension:** workflow-gaps
- **Impact:** high (analyst) → high (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/document_symbols.py`
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/core.py`

**Problem**

document_symbols builds each entry from only name/kind/line/character/container (document_symbols.py lines 121-127) and symbol_to_external never reads DocumentSymbol.detail — the field rust-analyzer populates with the item's signature (fn foo(x: u32) -> bool, struct field types, etc.). So for the extremely common 'understand/summarize this file' workflow, the agent gets a bare name+kind outline and must then fire hover at every one of N symbols just to see their signatures. The signature data was already in the response and was discarded.

**Proposed solution**

Surface the LSP DocumentSymbol.detail string as a "detail" (or "signature") field on each document_symbols entry (and thread it through symbol_to_external so find_symbol candidates carry it too when present). This gives every symbol's type signature in the single existing call, eliminating the N follow-up hover round trips for file-overview and symbol-disambiguation tasks. Additive field, read-only, keeps the settled flat-list shape.

**Round-1 verification**

Verdict: `confirmed`

CONFIRMED. All four checks pass.

(1) Problem is real and unfixed. core.py symbol_to_external() returns {"name","kind","file","line","character","container"} (lines 405-412) and never reads sym.get("detail"). document_symbols.py builds each entry from only those five fields (lines 121-127). The signature-bearing detail field is discarded.

(2) The data is genuinely present and thrown away. multilspy 0.0.15 language_server.py:623 constructs UnifiedSymbolInformation(**item) directly from the raw LSP DocumentSymbol dict (**-splat preserves every field including detail). multilspy_types.py:204-205 declares `detail: NotRequired[str]` with doc "More detail for this symbol, e.g the signature of a function." rust-analyzer's documentSymbol provider populates detail with item signatures/types. So sym["detail"] reaches symbol_to_external in core.py and is silently dropped — not a misread.

(3) Not already implemented and not tracked. `grep -rn detail src/` finds no symbol-detail handling anywhere in the tool/core code. known-issues.md Open section is empty (KI-1..KI-9 all Resolved); this is untracked.

(4) No conflict with settled architecture (implementation-plan.md:376-383: read-only, stdio, single-host, flat list). Adding an additive, read-only `detail`/`signature` string to each flat entry preserves the flat-list shape and all scope constraints. tools.md documents the current 5-field shape but nothing forbids an additive field.

Implementability/impact: The fix is small — read sym.get("detail") in symbol_to_external and include it (when present) in the document_symbols entry. It directly serves the very common 'outline/summarize this file' workflow, replacing N per-symbol hover round trips with signatures already in the one existing call. Two nuances that temper but do not defeat it: (a) detail is NotRequired, so it is absent for some symbol kinds (returns None → omit/null, still additive); (b) hover returns strictly more than detail (signature PLUS rustdoc), so hover is not fully eliminated for doc-reading tasks — but for signature-level file overview, detail suffices. Net impact remains high for the named workflow.

### Round-2 red-team verdict

**Claim verdict:** weakened

Mechanism confirmed: multilspy language_server.py:601-623 splats every DocumentSymbol field (incl. `detail`) into UnifiedSymbolInformation, and core.py symbol_to_external (lines 405-412) never reads `sym.get("detail")` — so the data is present and dropped. But two things round 1 accepted uncritically inflate the "high/high" severity. (1) The actual content of rust-analyzer's DocumentSymbol.detail was never verified against source or a live capture — round 1 cited only multilspy's type-doc ("e.g the signature") and asserted from memory that rust-analyzer "populates detail with item signatures," which violates this project's research-policy (prefer source over memory). detail may be a partial signature, weakening "eliminates N hovers." (2) The "forces one hover per symbol to understand a file" framing ignores that the primary consumer is a host agent with /project mounted :ro (implementation-plan.md:382-383) that can simply Read the source — one call yields all signatures+bodies+docs. Defect is real; severity is overstated.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — The proposal to "thread it through symbol_to_external so find_symbol candidates carry it too" is over-scoped and wrong for find_symbol. find_symbol queries workspace/symbol, whose results are SymbolInformation/WorkspaceSymbol — the LSP shape that has NO `detail` field (detail exists only on DocumentSymbol). Threading detail through the shared helper (core.py:405-412, used by both find_symbol.py:91 and document_symbols.py:115) adds an always-null `detail` key to every find_symbol result, changing its documented 6-field contract (tools.md:55-66) for zero benefit.
- **minor** — Cleaner hook already exists: document_symbols.py's loop (lines 114-128) still holds the raw `sym` in scope and builds `entry` by hand, so it can read `sym.get("detail")` locally when present — no change to symbol_to_external, no find_symbol blast radius, no shared-helper regression risk against test_ds09/test_ds02. The proposal picks the more invasive path.
- **minor** — Effort "small" is honest ONLY for the local-to-document_symbols design. As written (threaded), it also forces a find_symbol contract change plus doc updates for BOTH tools in tools.md and touches the shared helper covered by test_ds02/test_ds09 — larger than "small."
- **minor** — detail is raw analyzer text echoed verbatim. The codebase deliberately sanitizes emitted locations (test_ds02_output_containment strips absolute/`..` paths); detail bypasses that path-safety scrutiny. Low risk for type signatures, but the proposal glosses it — output-containment review should explicitly cover the new free-text field.
- **minor** — "Eliminates the N follow-up hovers" overclaims: hover returns signature PLUS rustdoc (hover.py:68-73), while detail is signature-only (and possibly partial). It reduces hovers for signature-level overview but does not eliminate them for doc-reading — round 1 itself flagged this yet the solution still asserts elimination.

**Revised proposal:** Keep the change local to document_symbols.py: in the loop (lines 114-128) read `detail = sym.get("detail")` and add `detail` (null/omitted when absent) to the entry dict. Do NOT touch symbol_to_external or find_symbol — workspace symbols carry no detail, so threading it there only pollutes find_symbol's contract. Before committing to the "saves N hovers" framing, capture a real rust-analyzer documentSymbol response to confirm detail's actual content/completeness, and update only the document_symbols section of tools.md.

## UR-19 — 'Understand this symbol' always costs hover + goto_definition as two separate calls

- **Dimension:** workflow-gaps
- **Impact:** medium (analyst) → medium (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `/home/claudeuser/projects/rust-lsp-mcp/src/rust_lsp_mcp/tools/hover.py`

**Problem**

The most frequent single-symbol question — 'what is this and where is it defined' — requires two round trips: hover (hover.py, returns only contents) for the type/docs, and goto_definition for the definition site. There is no way to get both in one call, even though find_references already establishes the in-tree precedent of folding a second delegate call (request_definition) into one tool response via include_declaration (find_references.py lines 60-71, 168-201).

**Proposed solution**

Add an optional include_definition: bool = False to hover that, when set, unions the goto_definition location(s) for the same position into the response as a "definitions" list — mirroring find_references' include_declaration pattern exactly, so it introduces no new convention and stays within read-only scope. Collapses the ubiquitous hover+goto_definition pair into one call.

**Round-1 verification**

Verdict: `confirmed`

Problem is REAL. hover.py:152 returns `ok(contents=contents_str)` — the envelope carries only the type/doc markdown, never a location. The definition site is only obtainable via the separate goto_definition tool, so "what is this and where is it defined" costs two tool invocations. Confirmed not misread.

Precedent is REAL and accurately cited. find_references.py:60-71 (docstring) and :168-201 (code) fold a second `mgr.request_definition(...)` call into one response under `include_declaration=True`, mapping via location_to_external and deduping by (file,line,character). The delegate request_definition already exists and is used in-tree.

NOT already implemented: hover() signature is `hover(file, line, character)` with no include_definition param (hover.py:67). NOT tracked: known-issues.md "Open" section is empty; every KI (including KI-2, which touched hover) is Resolved.

NO conflict with settled architecture (implementation-plan.md:376-383: MCP-not-skill, read-only, refresh semantics, doc-RAG, stdio/single-host) — none constrain tool params. goto_definition is already read-only so unioning its result into hover stays within read-only scope. The include_declaration precedent proves the "opt-in fold of a second read-only delegate" convention is already accepted, so no new convention is introduced.

Implementable and effort is fairly small: request_definition + location_to_external reuse makes it near copy-paste from find_references:168-201, and hover already has the matching not-ready/torn-down guards (hover.py:128-136). Minor deviation from "exactly" mirroring: find_references unions defs into an existing references list, whereas hover has no list and would add a separate `definitions` key (matching goto_definition's output key) — cosmetic, not a defeater. Impact medium is fair: genuine common workflow, but a convenience fold, not a correctness gap. Two counter-arguments (an MCP client can emit both calls in one turn; not literally provable it's THE most frequent question) weaken the "impact" but not the validity — the project already decided this fold is worthwhile via find_references, so consistency alone justifies it.

### Round-2 red-team verdict

**Claim verdict:** weakened

Problem is real (hover.py:152 returns only contents; def site needs goto_definition) but framing is inflated. (1) Per the settled loop (implementation-plan.md:265) the agent reaches a position via find_symbol, whose result IS the declaration site, so for name-based lookups goto_definition is already redundant and hover alone answers what+where. The genuine two-call slice is use-site-only, narrower than the most-frequent claim. (2) MCP clients emit parallel tool calls in one turn, so hover+goto is already one round-trip. (3) Central justification is a misread: find_references.py:60-71 states include_declaration exists solely because multilspy hardcodes includeDeclaration=False, a library workaround, NOT a blessed fold convention. Round 1 accepted this precedent framing uncritically.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — Undefined precedence for the 4 status combinations. hover returns not_found for no-hover-info (hover.py:142-150) independently of whether a definition exists; goto returns not_found for no-def (goto_definition.py:108-122). Proposal never says what happens when hover=not_found but a def exists (requested def silently discarded under not_found) or hover=ok but def empty (definitions=[] vs omit key). Real design work, not copy-paste.
- **major** — Misapplied precedent erodes a settled decision. include_declaration is a workaround for multilspy hardcoding includeDeclaration=False (find_references.py:60-66), not a general fold convention. implementation-plan.md:259 settles Option A strict separation, position-based actions; hover and goto_definition are deliberately orthogonal. Folding goto behavior into hover contradicts that; the finding claim of no new convention is false.
- **major** — A strictly simpler zero-code alternative preserves the architecture: document that the agent issues hover and goto_definition as parallel calls in one turn (MCP supports this). No new param, no second delegate, no failure surface, no schema change, keeps Option A orthogonality.
- **minor** — New partial-success failure mode: if the analyzer is torn down between the hover delegate and the added request_definition call (concurrent refresh), code must return not_ready and discard hover contents already obtained; wasteful whole-call retry, same hazard class as UR-7.
- **minor** — Effort understated as small. Requires precedence-semantics design, a new test matrix (test_hover.py has no include_definition coverage), extending the settled single-field hover schema (implementation-plan.md:302,306 pin hover to contents), and a tools.md contract row (docs/guide/tools.md:270-298).

**Revised proposal:** Prefer a documentation-only fix: in hover and goto_definition descriptions, note that what-is-this-and-where-is-it-defined is answered by calling both at the same position in one turn (MCP parallel tool calls), and that a position from find_symbol already carries the definition site. Zero code, zero new failure surface, upholds the settled Option A separation. If a code fold is still wanted, host it as goto_definition.include_hover and fully specify the four hover/def status combinations first.

## UR-20 — `status` never echoes the effective config or the doc-corpus size, so the most common first-hour misconfig (wrong root / missing mount / glob matches nothing) reports fully healthy

- **Dimension:** onboarding-observability
- **Impact:** high (analyst) → high (verifier-revised)
- **Effort:** medium
- **Affected files:**
  - `src/rust_lsp_mcp/tools/status.py`
  - `src/rust_lsp_mcp/doc_store.py`

**Problem**

The `status` tool returns only `state`, `analyzer_error`, `indexed_commit`, `current_commit`, `stale`, `doc_index_state`, `doc_index_error` (see status.py `return ok(...)`). It exposes neither the effective `project_root`/`doc_glob_patterns` in force nor the number of indexed markdown chunks. When an agent points at the wrong directory, forgets the `-v <project>:/project:ro` mount, or uses a glob that matches nothing, `doc_store.rebuild()` globs a path with zero hits and still reaches `DOC_STATE_READY` (the adopt gate deliberately drops the `count()>0` requirement — doc_store.py DS-24 comment), so `status` reports `state:"ready", doc_index_state:"ready"`. Nothing in the envelope lets the agent see it is operating on an empty/wrong corpus. The chunk count already exists internally (`rebuild()` returns it; adopt logs `existing.count()`).

**Proposed solution**

Add read-only observability fields to `status`: `project_root` (the resolved absolute path actually in use), `doc_glob_patterns`, `rust_analyzer_bin`, and `doc_index_chunk_count` (surface `collection.count()` via a new `doc_store` accessor alongside `doc_store_state()`). An agent can then assert `doc_index_chunk_count > 0` and confirm `project_root` matches what it mounted — turning a silent empty corpus into an observable number.

**Round-1 verification**

Verdict: `confirmed`

Problem is REAL and unfixed. status.py lines 89-97 (return ok(...)) surface only state, analyzer_error, indexed_commit, current_commit, stale, doc_index_state, doc_index_error. The resolved project_root is already computed as repo_root at line 72 but used only for the git call, never returned; doc_glob_patterns, rust_analyzer_bin, and any chunk count are absent. The silent empty-corpus path is confirmed: doc_store.py _rebuild_locked lines 285-303 handle total==0 by writing the build_complete sentinel and flipping to DOC_STATE_READY (return 0), and _try_adopt lines 454-463/486-505 adopt it via the DS-24 gate that deliberately drops count()>0. So wrong-root/missing-mount/no-match-glob reports state:ready, doc_index_state:ready over zero chunks. The count exists internally (rebuild returns int; _try_adopt logs existing.count(); search calls collection.count()) but is never exposed. Not implemented and not tracked: known-issues.md Open section is empty and no docs/ grep hit tracks this observability gap; doc_store.py exposes only doc_store_state()->(state,error) with no count accessor. No conflict with settled architecture (stdio/single-host/read-only, plain doc-RAG) — this adds read-only observability only, matching the onboarding-observability concern. Implementable: three fields are one-liners (repo_root in hand; get_settings() imported); doc_index_chunk_count needs a new lock-safe Optional[int] accessor taking collection.count() under _read_lock with a None guard to honor the DS-12 contract — justifying medium effort. Impact high is defensible: most common first-hour misconfig currently reports fully healthy.

### Round-2 red-team verdict

**Claim verdict:** weakened

The core defect is real: the DS-24 empty-corpus path (doc_store.py:285-303, 454-505) adopts a ready collection with 0 chunks and no error surface, and the count is never exposed. But the title's "reports fully healthy" overclaims. For wrong-root / missing-mount, rust-analyzer cannot open a nonexistent workspace: the LSP init fails → STATE_ERROR (analyzer.py:404) or hangs in "indexing" — NOT "ready". UR-21's own round-1 verification confirms exactly this ("cryptic LSP init error surfaced verbatim as analyzer_error"). So the analyzer half is visibly unhealthy in those cases; only the doc half is silently ready+0. "Fully healthy" holds solely for the narrow case of a valid Rust project mounted but with no glob-matching .md. Round 1 accepted the framing without checking analyzer-state behavior on a bad root.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **blocker** — The proposed doc_index_chunk_count accessor is synchronous (collection.count() → ChromaDB/SQLite query under _read_lock) but status() is async and runs INLINE on the event loop. This reintroduces exactly the blocking DS-19 forced git into asyncio.to_thread to avoid — a per-poll SQLite query plus a _read_lock acquisition that, if a rebuild holds the lock, stalls the WHOLE event loop, not just the caller. The proposal never offloads it; round-1 called it a one-line accessor.
- **major** — Echoing project_root does NOT diagnose the headline 'missing mount' case: repository_root is the raw settings string (analyzer.py:881, never .resolve()d), identical whether /project is mounted or not. The agent already knows what it configured. The proposal's claimed benefit ('confirm project_root matches what it mounted') is illusory — only chunk_count actually reveals the empty corpus. 3 of the 4 fields are low-value config echoes.
- **major** — Surfacing doc_glob_patterns + rust_analyzer_bin forces an unconditional get_settings() every poll. get_settings() constructs a fresh Settings(), which pydantic-settings loads from .env on disk each call. Today status only calls it in the mgr-is-None branch. This adds per-poll .env file I/O, inline on the event loop, on the hottest path.
- **major** — Wrong/duplicated fix. UR-20/21/22/24 all target the same silent-empty-corpus trap; UR-24's round-1 verification explicitly proposes the simpler 'plain doc_chunk_count field' as superior to a notes array. Implementing UR-20's 4 config-echo fields independently triple-covers the gap. A single consolidated doc_chunk_count (+ optional file_count), designed once across these findings, is strictly simpler and higher-signal.
- **minor** — Affected-files list (status.py, doc_store.py) omits docs/guide/tools.md, which documents the exact 7-field status table and would go stale — plus the 'DECIDED: four fields' schema note. Effort 'medium' is roughly honest but undercounts the doc-contract update and the offload plumbing the count() call actually needs.

**Revised proposal:** Drop the three config-echo fields (project_root/doc_glob_patterns/rust_analyzer_bin) — they don't distinguish mounted-vs-not and duplicate what the agent configured. Add exactly one field, doc_index_chunk_count, via a lock-safe accessor that returns None when not ready and 0 when empty. Because status() is async and DS-19 bars inline blocking, wrap the count() call in asyncio.to_thread like the git call already is. Coordinate this single field with UR-24 so the corpus-empty trap is fixed once, and defer real path/mount validation to UR-21's preflight (existence check), which is what genuinely diagnoses a missing mount.

## UR-21 — No pre-flight validation of project_root / Cargo.toml / analyzer binary — a mistyped path or missing mount surfaces as an opaque rust-analyzer error or a silent empty doc index

- **Dimension:** onboarding-observability
- **Impact:** high (analyst) → medium (verifier-revised)
- **Effort:** medium
- **Affected files:**
  - `src/rust_lsp_mcp/analyzer.py`
  - `src/rust_lsp_mcp/doc_store.py`

**Problem**

Grepping src/ shows no `.exists()`, `is_dir()`, or `Cargo.toml` check on `project_root` before it is handed to rust-analyzer (analyzer.py constructs `PatchedRustAnalyzer(repository_root_path=self._repository_root, ...)` with no validation), and `doc_store` calls `src_root.glob(pattern)` on the path blindly. So the two highest-probability onboarding mistakes degrade badly: a nonexistent/mistyped `RLM_PROJECT_ROOT` (or an un-mounted `/project`) makes rust-analyzer fail with a cryptic LSP init error surfaced verbatim as `analyzer_error`, while for docs `glob()` over a nonexistent path just returns empty and the index goes `ready` with 0 chunks and no error at all. A missing `RLM_RUST_ANALYZER_BIN` likewise only shows up as a constructor exception mapped to `STATE_ERROR`.

**Proposed solution**

Add a cheap pre-flight in `AnalyzerManager.start()` (and doc-store init): if `project_root` does not exist or is not a directory, set `STATE_ERROR` with a crisp message like "RLM_PROJECT_ROOT=/x does not exist in the container — did you mount the project with -v <path>:/project:ro?"; if no `Cargo.toml` is found at the root, record that in `analyzer_error`; if `rust_analyzer_bin` is not on disk, error naming the path and `RLM_RUST_ANALYZER_BIN`. These messages already have a delivery channel (`analyzer_error` in `status`), unlike rust-analyzer's raw output.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL (verified in current code):
1. No preflight validation of project_root anywhere. settings.py defines `project_root` (Field default `/workspaces/ripgrep`, alias RLM_PROJECT_ROOT) and `rust_analyzer_bin` with NO existence/is_dir/Cargo.toml validators — the only model_validator is `_warn_deprecated_ripgrep_src`. grep for `exists|is_dir|Cargo.toml` across src/ finds only the unrelated `validate_file_path` tool and doc_store's `f.is_file()` glob filter — never a check on `project_root` itself.
2. Analyzer path: analyzer.py `_run` (lines 367-406) constructs `PatchedRustAnalyzer(...)` inside a try whose `except Exception` sets `self.state = STATE_ERROR; self._error = f"{type(exc).__name__}: {exc}"`. A mistyped RLM_PROJECT_ROOT or missing RLM_RUST_ANALYZER_BIN surfaces only as rust-analyzer's raw error string via `analyzer_error` (or, if rust-analyzer hangs on a nonexistent root, state stays "indexing" indefinitely) — never a crisp, actionable message.
3. Doc path (strongest, fully confirmed): doc_store.py `_rebuild_locked` line 233-238 does `src_root = pathlib.Path(self._settings.project_root)` then `src_root.glob(pattern)` blindly. On a nonexistent path glob yields nothing → `total == 0` → lines 286-303 write the build-complete sentinel and set `state = DOC_STATE_READY, return 0`. The doc index goes fully "ready" with 0 chunks and NO error surface at all. (Note: a real project with genuinely 0 .md files is a VALID ready+0 case per DS-24, so the proposal correctly keys on path existence, not chunk count.)

NOT ALREADY IMPLEMENTED / NOT TRACKED: known-issues.md "## Open" section is empty; KI-1..KI-9 are all Resolved and none concern project_root/binary preflight validation.

NO CONFLICT: implementation-plan.md "Settled architecture" (line 376-383) covers MCP-not-skill, read-only, refresh=teardown+reindex, plain doc-RAG, stdio/single-host — none touched. The proposal is purely additive preflight using the EXISTING delivery channels (`analyzer_error`/STATE_ERROR surfaced by the status tool; doc_store_state error). It respects init_doc_store_background's "never fail startup" contract.

IMPLEMENTABLE & WORTH IT: trivially implementable — a few pathlib.exists()/is_dir() checks plus a Cargo.toml glob, wired into AnalyzerManager start/_run and doc_store rebuild, emitting to channels that already exist. The two named mistakes (un-mounted /project, wrong RLM_RUST_ANALYZER_BIN) are the highest-probability onboarding errors and currently degrade to opaque errors or silent empty indexes. Impact revised to medium rather than high: the analyzer side is partially observable today (status surfaces analyzer_error, validate_file_path exists), and the failure is recoverable; the genuinely un-surfaced case is the doc-index silent ready+0. Effort is closer to low than the claimed medium.

### Round-2 red-team verdict

**Claim verdict:** weakened

Core fact holds: no preflight exists (settings.py:53-65 has only _warn_deprecated_ripgrep_src; doc_store.py:233-303 globs a bad path → total==0 → writes sentinel, sets DOC_STATE_READY, returns 0 — silent ready+0). But the framing is off two ways round-1 accepted. (1) The analyzer "surfaces as an opaque rust-analyzer error" mechanism is inaccurate for the most common misconfig — an existing-but-wrong dir: rust-analyzer starts and reaches quiescent/ready over an empty workspace, so find_symbol just returns not_found (silent ready-empty, symmetric to the doc side), not a cryptic error. The cheap .exists()/is_dir() check does NOT catch this; only the Cargo.toml gate would. (2) The doc-side silent ready+0 — which round-1 called the "strongest, genuinely un-surfaced case" — is already surfaced by sibling UR-20's proposed doc_index_chunk_count field, so UR-21's highest-value part is subsumed. Real problem, but narrower than stated.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — Test-seam breakage the round-1 verifier never checked. The proposal wires the preflight into AnalyzerManager.start()/_run, but the fast/race suites construct managers with fake paths and a mocked LSP: tests/test_lifecycle_races.py:252 _make_manager()=AnalyzerManager(bin='/fake/rust-analyzer', root='/fake/repo'), imported and driven via mgr.start() by the whole test_analyzer_error_state.py suite (l.93/133/271/318/368/428), and test_phase1_fast.py:205 uses '/nonexistent','/tmp'. A binary-on-disk / root-exists check fires on these before the monkeypatched PatchedRustAnalyzer runs, so start() would set STATE_ERROR and never spawn _task — test_startup_failure_sets_error_state's `assert mgr._task is not None` and its 'boom: initialize failed' assertion break outright. 'Trivially implementable' is false.
- **major** — Cargo.toml-at-root gate is false-positive-prone and fights a settled design point. settings.py:43-46 states project_root is 'repo-agnostic — point it at any Rust project'. rust-analyzer accepts rust-project.json projects and discovers Cargo.toml in subdirectories; requiring Cargo.toml at the literal root and recording it in analyzer_error (the STATE_ERROR channel) would wrongly block valid configurations. The proposal is ambiguous on fatal-vs-warning; as worded it can turn a working project into a hard error.
- **major** — Redundant with sibling UR-20 and self-duplicating. UR-20 already adds project_root/doc_glob_patterns/rust_analyzer_bin echo + doc_index_chunk_count to status.py, which observably exposes the missing-mount/empty-corpus case. UR-21 additionally validates project_root existence in BOTH analyzer.py and doc_store.py for the same root cause, so a missing mount emits two separate error messages (analyzer_error and doc_index_error) for one mistake. The two same-dimension findings should be reconciled, not shipped as overlapping fixes.
- **minor** — The suggested message hardcodes '-v <path>:/project:ro', a mount convention that does not match this project's actual devcontainer bind-mount (default project_root=/workspaces/ripgrep). It would mislead onboarding users about how the mount is actually done.
- **minor** — Effort 'medium' (round-1 even said 'closer to low') is dishonest once the test coupling and the where-to-validate-relative-to-the-mock-seam decision are counted — real cost is at least medium, plus updating multiple fast/race suites to use real tmp dirs + Cargo.toml fixtures.

**Revised proposal:** Land UR-20's observability (echo resolved project_root/rust_analyzer_bin + doc_index_chunk_count) as the primary fix — it makes wrong-root/missing-mount/empty-corpus observable without any gating. Then add only a narrow, NON-fatal preflight: check rust_analyzer_bin on disk and project_root is_dir, surfaced as a distinct advisory field (not STATE_ERROR), computed once in the lifespan (not duplicated in analyzer + doc_store). Drop the Cargo.toml-at-root gate (or make it a soft hint) to preserve the repo-agnostic/rust-project.json contract. Keep validation out of AnalyzerManager.start()/_run so the mocked-LSP test seam (fake paths) stays intact.

## UR-22 — `search_docs` `not_found` deterministically means "doc corpus is empty" (a config bug) but is worded as a soft "no match for your query"

- **Dimension:** onboarding-observability
- **Impact:** medium (analyst) → medium (verifier-revised)
- **Effort:** small
- **Affected files:**
  - `src/rust_lsp_mcp/tools/search_docs.py`
  - `src/rust_lsp_mcp/doc_store.py`

**Problem**

In `doc_store.search()`, a populated collection always returns top-k neighbours (`effective_n = min(n_results, count)`), and `[]` is returned only when `count == 0`. search_docs.py's own docstring confirms: "This only happens when the collection is empty." So a `not_found` from `search_docs` is a reliable signal that indexing produced zero chunks — i.e. wrong root / glob / mount — yet the message is the hedged "No documentation chunks matched {query}. The collection may be empty." An agent reads that as "my query had no hits" and moves on, never realizing the corpus was never built. (The envelope-default `not_found` text is even worse for docs: "The requested symbol or position was not found.")

**Proposed solution**

Since `count == 0` is the only path to this `not_found`, make it explicit and actionable, e.g.: "The documentation index is ready but contains 0 chunks — no Markdown files matched RLM_DOC_GLOB_PATTERNS under RLM_PROJECT_ROOT (<path>). Verify the project mount and glob patterns, then call refresh." This stays within the settled status vocabulary (still `not_found`); only the message changes.

**Round-1 verification**

Verdict: `confirmed`

Problem is real and unfixed. doc_store.py:357-363: DocStore.search() holds _read_lock, requires state==READY and _collection is not None (else raises DocStoreNotReady), then `count = collection.count(); if count == 0: return []`; for count>0, effective_n=min(n_results,count) guarantees >=1 neighbour. So [] is returned ONLY for a ready collection with count==0. search_docs.py:139-140 is the sole not_found path — reached only after the store.is_ready gate (line 112) and search() returning [] — yet the message is hedged: "No documentation chunks matched {query!r}. The collection may be empty." The tool's own docstring (search_docs.py:73-77) confirms "This only happens when the collection is empty." Thus not_found from search_docs deterministically means "ready index, 0 chunks" (empty corpus from wrong root/glob/mount), but the wording reads as a soft per-query miss. Not tracked: known-issues.md Open section is empty. No conflict: implementation-plan.md:235-303 fixes the status MAPPING (zero matches -> not_found); the proposal preserves that mapping and changes only the message string, staying within the settled not_found vocabulary; read-only/stdio/single-host untouched. Implementable at small effort: the call site (search_docs.py:139) has `store` in scope and store._settings exposes project_root and doc_glob_patterns, so a diagnostic message is constructible with no new plumbing; tests only assert message non-empty (test_search_docs.py:285-286), so rewording breaks nothing. Two caveats that do not block: (a) the finding slightly over-claims count==0 as "a config bug" — it can also be a legitimately doc-less project, though the proposed wording is appropriately diagnostic rather than accusatory; (b) the aside that the envelope-default not_found text applies is inaccurate — search_docs.py:140 always passes an explicit message and never uses envelope.not_found's default. Core defect (misleading, under-actionable message on the one deterministic misconfiguration signal in the surface) stands. Impact medium is fair.

### Round-2 red-team verdict

**Claim verdict:** weakened

Mechanism holds: doc_store.py:357-363 returns [] only when count==0 on a READY collection; search_docs.py:139-140 is the sole not_found path, always with an explicit message. But "deterministically means doc corpus is empty (a config bug)" overstates it. count==0 is a legitimate, common state for a Rust project shipping no .md docs (crates keep docs as in-source rustdoc), so it is a diagnostic signal, not deterministically a bug. Also the finding's aside that the envelope-default not_found text ("requested symbol or position...") applies is false — line 140 always passes an explicit string (round-1 caught this). Real under-actionable-message defect stands; severity framing leans on a false "empty==misconfig" equivalence.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — The proposed message hard-asserts one diagnosis ('no Markdown files matched RLM_DOC_GLOB_PATTERNS... Verify the mount and glob, then call refresh'), but count==0 has >=3 causes: (a) glob matched nothing; (b) all matches excluded by doc_exclude_patterns (default '**/CHANGELOG.md', settings.py:85) — files DID match the glob; (c) matched files empty/whitespace-only, chunk_markdown returns [] (doc_chunking.py:895-896). It misdiagnoses (b)/(c) and reintroduces the very misleading-message problem the finding set out to fix.
- **major** — For the common legitimate case (a doc-less Rust project), the new wording gives actively wrong 'your config is broken -> refresh' advice, sending the operator/agent to check mounts and re-run refresh when nothing is wrong. The current neutral 'collection may be empty' is safer for that case; the proposal is a net regression there.
- **major** — Effort 'small' is dishonest for a CORRECT fix. An accurate message needs files-matched / excluded / chunks-produced counts; _rebuild_locked computes before_count/removed/total as locals and discards them (nothing stored on the DocStore instance, no count accessor). Producing an accurate diagnostic requires the same instance-plumbing UR-24 proposes -> medium, and overlaps UR-24. 'Small' holds only for the naive sometimes-wrong string.
- **minor** — Encapsulation/coupling: reaches into private store._settings.project_root/doc_glob_patterns from the tool, and the '0 chunks' wording couples to search()'s internal []<=>count==0 invariant — if search() ever also returns [] for another reason (e.g. a distance filter), the message becomes a lie.
- **minor** — A cleaner hook already exists in the same audit: the count==0 signal belongs on the always-callable status tool (UR-20 corpus size / UR-24 config_warnings) so the misconfig is visible without first running a doomed search. Fixing only the not_found message is the narrower band-aid and risks message/status drift if uncoordinated.

**Revised proposal:** Keep the not_found status; make the message diagnostic-but-neutral instead of prescriptive: enumerate the causes ("index ready but 0 chunks: no .md matched RLM_DOC_GLOB_PATTERNS, all matches excluded by RLM_DOC_EXCLUDE_PATTERNS, matched files were empty, or this project simply ships no Markdown docs") without asserting misconfig or auto-prescribing refresh. For the durable fix, coordinate with UR-20/UR-24: store files-matched/excluded/chunk counts on the DocStore and surface a doc_chunk_count (+config notes) on the always-callable status tool, so the empty-corpus signal is observable without a doomed search. Treat the message tweak as small; the accurate diagnostic as medium.

## UR-23 — During indexing there is no elapsed-time signal, so a polling agent cannot distinguish "still warming up" from "wedged"

- **Dimension:** onboarding-observability
- **Impact:** medium (analyst) → low (verifier-revised)
- **Effort:** medium
- **Affected files:**
  - `src/rust_lsp_mcp/analyzer.py`
  - `src/rust_lsp_mcp/tools/status.py`

**Problem**

`status`/`analyzer_status` expose only the tri-state (`indexing`/`ready`/`error`) and the fact sheet confirms "clients must poll `status` indefinitely." First-run indexing ranges from seconds to minutes, but a stuck or pathologically slow index is indistinguishable from a healthy one that is 20s in — the agent has no basis for a timeout/escalation decision and either gives up too early or polls forever. The manager captures `_indexed_commit` at run start (analyzer.py `_capture_head_commit`, called from `start()`) but never records a start timestamp.

**Proposed solution**

Stamp `indexing_started_at` (wall clock, reset on each `restart()`) next to the existing HEAD-commit capture, and expose a derived `indexing_elapsed_seconds` in `status`. This is additive observability, not a progress bar or ETA (it does not reopen the binary/tri-state readiness model) — it just gives agents a concrete number to key an escalation heuristic on. Optionally also surface rust-analyzer's last workDoneProgress message if multilspy exposes it.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL AND UNFIXED. The tri-state is the only temporal signal exposed. `analyzer_status()` (src/rust_lsp_mcp/tools/diagnostics.py:11-31) returns exactly one field: `ok(state=state)` where state ∈ indexing/ready/error. `status()` (src/rust_lsp_mcp/tools/status.py) returns state, analyzer_error, indexed_commit, current_commit, stale, doc_index_state, doc_index_error — no time-derived field. `_capture_head_commit` (analyzer.py:296-324) writes `self._indexed_commit` but stamps no timestamp, and grep across all of src/rust_lsp_mcp confirms zero `import time`/`datetime`/`monotonic`/`perf_counter`/`started_at`/`elapsed` usage. A poller genuinely cannot tell a 20s-healthy index from a wedged one from the server side.

NOT IMPLEMENTED, NOT TRACKED. The known-issues.md "Open" section is empty; no KI covers elapsed/observability. The nearest neighbor, KI-9, deliberately declines a wall-clock TIMEOUT on delegate awaits, but that is about per-query nav delegates, not indexing observability, and this proposal is explicitly additive observability, not a timeout, so it does not reopen that decision.

NO CONFLICT WITH SETTLED ARCHITECTURE (implementation-plan.md:376+). Settled arch = read-only (of the target repo), refresh=teardown+reindex, stdio/single-host, plain RAG. An additive `indexing_elapsed_seconds` field touches none of these. Implementable in the two named files with a small diff (stamp a monotonic reference in `_capture_head_commit`/`_run`, compute delta in status). restart() already resets state and clears _indexed_commit at the same seam, so the timestamp resets for free.

WHY IMPACT DOWNGRADED TO LOW. Two caveats: (1) A polling client can already measure its own elapsed time locally, so the server-side number is convenience, not a missing capability — the genuine incremental value is narrow: it captures resets the client can't observe (e.g. an internally-triggered restart) and is anchored to actual index start rather than first-poll. (2) The proposal says "wall clock," but a duration should use `time.monotonic()`, not wall clock, to avoid NTP/clock-adjustment skew corrupting the elapsed value — a wall-clock delta can even go negative. Also `indexing_elapsed_seconds` needs defined semantics once state==ready (freeze at total index time, or null). None of these refute the finding; they cap it at a low-value, low-effort nicety rather than the claimed medium/medium. Verdict remains confirmed: real, unfixed, untracked, non-conflicting.

### Round-2 red-team verdict

**Claim verdict:** weakened

Problem (no start timestamp stamped) is real and unfixed. But round 1 overstated impact and kept a wrong justification. The polling client drives its own loop and can trivially time its first-seen indexing locally, so "cannot distinguish warming-up from wedged" is false. Round 1's sole surviving server-only value — "captures resets the client can't observe, e.g. an internally-triggered restart" — does not exist here: restart() is called ONLY by the client-initiated refresh tool (refresh.py:140); grep finds no watchdog/timer/auto-restart in src/. Every clock reset is client-initiated and already observable. So incremental value is essentially nil, not "low." Round 1 missed that restart is client-only.

**Solution verdict:** needs-revision

**Recommended action:** drop

- **major** — Value proposition is refuted. Round 1's only surviving server-only benefit (elapsed captures resets the client can't see) is false: restart() is invoked solely by the client-initiated refresh tool (refresh.py:140) and grep shows no watchdog/timer/auto-restart in src/. A client already times its own poll loop, so the field duplicates information the client has. Near-zero net value.
- **major** — Fix reaches the wrong poll tool. The problem names status/analyzer_status, but the proposal only edits status.py. Every not_ready message directs agents to analyzer_status (analyzer.py:118), which returns just ok(state=state) (diagnostics.py:13-30). Agents following the server's own guidance never see the elapsed field, or are pushed onto the heavier status poll that forks git on every call (status.py:81, DS-19).
- **major** — Proposed stamp seam is buggy. 'Stamp next to the HEAD-commit capture' = _capture_head_commit, which runs at the top of _run (analyzer.py:351) BEFORE the generation check. A superseded/doomed run would overwrite indexing_started_at with a later value, making elapsed read too small. It must be gen-guarded and written in the same synchronous block as the state=READY writes (analyzer.py:389-391), not where the proposal places it.
- **minor** — Undefined post-ready semantics + clock conflation. Proposal says 'wall clock' and names both indexing_started_at and a derived elapsed. A wall-clock delta can skew/go negative under NTP (round 1 noted); it also never freezes at ready, so elapsed keeps growing meaninglessly after indexing completes. Correct design needs a monotonic reference plus a freeze-or-null rule at ready/error - more than 'stamp one value, compute a delta.'
- **minor** — Effort label incoherent. Marked 'medium' yet described (and by round 1) as a trivial few-line diff. The honest scope - both status and analyzer_status, gen-guarded stamp, ready/error semantics, monotonic handling, tools.md updates, new tests - is scattered small work. The mismatch signals the proposal was not costed against a real diff.

**Revised proposal:** Drop it: the client already times its own poll loop and every clock reset is client-initiated via refresh, so a server-side elapsed field adds no capability. If any signal is still wanted, expose only an absolute indexing_started_at ISO wall-clock timestamp (client computes the delta) — sidestepping monotonic-skew, post-ready-freeze, and negative-duration problems. But add it to analyzer_status too, or agents polling the recommended lightweight tool never see it.

## UR-24 — Key startup diagnostics go only to `warnings.warn`/`logging`, which is invisible to an MCP client over stdio

- **Dimension:** onboarding-observability
- **Impact:** medium (analyst) → medium (verifier-revised)
- **Effort:** medium
- **Affected files:**
  - `src/rust_lsp_mcp/settings.py`
  - `src/rust_lsp_mcp/doc_store.py`
  - `src/rust_lsp_mcp/tools/status.py`

**Problem**

The most useful onboarding signals are emitted on channels the driving agent never sees: `settings.py._warn_deprecated_ripgrep_src` uses `warnings.warn(...)` (stderr), and `doc_store` logs "found %d markdown files", "excluded %d files", and per-file "could not read %s: %s" via `_log` at debug/warning level. Over stdio transport the MCP client consumes only tool-response JSON; stderr and logger output are not surfaced to the agent. So an operator using a deprecated `RLM_RIPGREP_SRC`, silently getting 0 files matched, or hitting unreadable doc files has no in-band way to learn any of it.

**Proposed solution**

Capture these at startup into a small structured list and expose it as a `notes` (or `config_warnings`) array field on `status`: deprecated-alias-in-use, "0 markdown files matched glob", and "N files skipped (unreadable)". This gives the diagnostics a stdio-native delivery path that reaches the agent, complementing the config/count fields above.

**Round-1 verification**

Verdict: `confirmed`

PROBLEM IS REAL (verified in current code):
1. Deprecation via warnings.warn — settings.py `_warn_deprecated_ripgrep_src` (model_validator, mode="after") does exactly `warnings.warn("RLM_RIPGREP_SRC is deprecated; rename it to RLM_PROJECT_ROOT...", DeprecationWarning, stacklevel=2)` when `RLM_RIPGREP_SRC in os.environ and RLM_PROJECT_ROOT not in os.environ`. DeprecationWarning → stderr, not tool-response JSON.
2. doc_store.py diagnostics go only to `_log = logging.getLogger(__name__)`: line 261 `_log.debug("doc_store: found %d markdown files to index", ...)`; line 259 `_log.debug("doc_store: excluded %d files matching doc_exclude_patterns", ...)`; line 273 `_log.warning("doc_store: could not read %s: %s", filepath, exc)`. All are logger output, not surfaced in-band.
3. The stdio-invisibility claim holds: over stdio the MCP client consumes JSON-RPC on stdout; stderr/logger output is not fed back to the agent in tool responses. The empty-corpus case is a genuine silent trap: doc_store handles `total == 0` gracefully (writes a completion sentinel and ADOPTS it, doc_store.py ~line 286), so `doc_index_state` reports "ready" — the operator sees ready + gets zero search_docs hits with no in-band signal of misconfiguration.

NOT IMPLEMENTED / NOT TRACKED: status.py's `ok(...)` returns state, analyzer_error, indexed_commit, current_commit, stale, doc_index_state, doc_index_error — no `notes`/`config_warnings` field and no doc file/chunk count. `doc_store_state()` returns only `(state, error_message)`; the file counts and read-failure count are locals in `_rebuild_locked`, not stored on the instance. known-issues.md "## Open" section is EMPTY. No entry covers this (KI-6 is only about a ripgrep-specific string in the status docstring; KI-4 was a different no-op setting).

NO HARD CONFLICT: Settled architecture (implementation-plan.md line 376-383: read-only, stdio, single-host) is untouched — this is read-only observability delivered over stdio, so it actually ALIGNS with the stdio constraint. The "status — DECIDED: report four fields" decision (line 317) is already a superset in practice: the shipped tool returns 7 fields (analyzer_error, doc_index_state, doc_index_error were added post-decision). So adding a notes array does not relitigate a frozen decision.

IMPLEMENTABLE at ~claimed effort (medium): requires capturing three signals into instance/module state (a deprecated-alias flag re-derivable from os.environ, plus doc file-count and skipped-count stored on DocStore during rebuild), a small accessor alongside `doc_store_state()`, and assembling them into status. Three files as claimed, plus tests.

CAVEATS on impact (kept at medium, not high): the empty-corpus / "0 markdown files matched" case is the real value — a legitimate onboarding trap with zero current in-band signal. The deprecated-alias and unreadable-file sub-cases are low-value edge conditions (alias is a rare back-compat path; read_text uses errors="replace" so only true OSError/permission failures reach the warning). A simpler design — exposing a plain `doc_file_count`/`doc_chunk_count` field on status rather than a freeform `notes` array — would capture most of the value at lower cost, but the underlying observability gap the finding names is genuine.

### Round-2 red-team verdict

**Claim verdict:** weakened

Real but overstated at medium. Two of three signals are low-value: doc_store's file/exclude counts are _log.debug (doc_store.py:259,261 — suppressed at default log level, not "the most useful onboarding signals"); the deprecated-alias path (settings.py:98) is rare. The one high-value signal — the empty-corpus trap (doc_store.py:286-303 adopts count=0 as doc_index_state="ready") — duplicates UR-20 (rated high), whose solution already proposes doc_index_chunk_count via collection.count() and names the same DS-24 trap. Round 1's own "simpler doc_file_count" caveat IS UR-20. Stripped of overlap, residual value (alias + unreadable-file notes) is low, not medium.

**Solution verdict:** needs-revision

**Recommended action:** revise

- **major** — Adopt-path bypass: file-count and skipped-count are locals computed only in _rebuild_locked (doc_store.py:261,273). The DS-24 adopt path (_try_adopt -> init_doc_store:596 returns early when store.is_ready) skips rebuild, and an empty corpus adopts on every restart after the first (doc_store.py:286-303). So the '0 markdown files matched glob' note — the highest-value diagnostic — is emitted only on the first build and disappears in steady state. Wrong capture point for the persistent-adopt architecture.
- **major** — Freeform notes/config_warnings array is dominated by UR-20's structured doc_index_chunk_count (collection.count(), which works on BOTH build and adopt paths). A prose array also contradicts the audit's own UR-6/UR-8 direction of moving away from English an LLM must parse toward machine-parseable fields. The genuine value (empty corpus) is a structured-int problem, already owned by UR-20.
- **minor** — New mutable counts on DocStore add surface to the carefully-reasoned DS-12 _read_lock/_build_lock invariants — a status read would race a refresh rebuild writing them; round 1 did not examine this coordination, unlike the existing tuple-only doc_store_state().
- **minor** — Deprecated-alias detection would be duplicated in status.py (re-deriving from os.environ) alongside settings.py:98, a DRY smell, and inherits the same documented .env-not-exported blind spot.

**Revised proposal:** Fold the only durable-value part into UR-20: expose a structured doc_chunk_count via a new doc_store accessor backed by collection.count() (works on both rebuild AND adopt paths, unlike counts captured in _rebuild_locked), plus project_root/doc_glob echo. Drop the freeform notes array. Treat the deprecated-alias and unreadable-file notes as optional low-value extras or defer them — they are rare edge conditions, not a medium-impact gap.

## Cross-cutting findings (round 2)

### Conflicts & sequencing

- **blocker** [UR-13, UR-16] UR-13 and UR-16 are the same feature with clashing names — must merge into one source-line design — Both add the same source-line enrichment to find_references, but with incompatible surfaces: UR-13 uses field `snippet` + param `include_snippet: bool=True`; UR-16 uses field `source` + param `context: bool=False`. Same data, different name, opposite default, different tool set (UR-13: find_references/find_symbol/document_symbols; UR-16: find_references/goto_definition/find_symbol). Implementing both yields duplicate params and two names for one line on find_references (the digest flags this as a confirmed blocker collision). They also share one net-new capability neither owns cleanly: direct filesystem reads of workspace .rs files (grep confirms tools/ currently reads all source via multilspy), plus the stale-index snapshot hazard (settled arch does no file-watching). Merge into a single design: one field, one param+default, one tool set, one degrade/decode/grouping policy, one symlink-scope decision — then decide goto_definition inclusion once.
- **blocker** [UR-6, UR-7, UR-8, UR-9, UR-10] UR-6 owns a shared error-envelope schema change that UR-7/8/9/10 silently depend on — sequence UR-6 first, land the rest as wiring — envelope.py confirms `error(message)` and `not_ready(message)` are fixed single-arg builders (only `ok(**kwargs)` is variadic). So every structured-field proposal — UR-6's code/retriable/recovery, UR-7's refresh code, UR-8's `recovery:poll_status` on not_ready, UR-9/UR-10 recovery hints — requires changing the shared builder signature. Each of UR-7/8/9 independently lists a single file and rates 'small', but each actually depends on the envelope.py change UR-6 owns; landing them independently mints divergent one-off fields no other tool honors. Worse, UR-6's proposed 4-value enum omits real raise sites: refresh.py:138/178, validate_file_path.py:58 (server misconfig, not client input). Resolution: treat UR-6 as the linchpin design (define the field set + enum covering ALL raise sites including refresh partial-success and misconfig), land it first, then UR-7/8/9/10 become per-call-site wiring, not separate schema decisions.
- **major** [UR-5, UR-11] UR-5 and UR-11 double-solve the ok+[] emptiness signal on find_references, and UR-11 regresses the very distinction UR-5 protects — UR-5 adds a find_references-only `message`+`symbol_resolved:true` to the ok+empty case; UR-11 adds numeric `total`/`count` across all three list tools. Both target 'how does the agent read ok+[]'. The digest's own UR-5 note concedes UR-11's numeric count(0) is the cross-tool-uniform alternative that composes better than a status-varying message. Meanwhile UR-11's offset-past-end returns ok+references=[], colliding with the settled load-bearing semantic (implementation-plan:298 'zero → ok+empty = real no-callers') that UR-5 exists to protect — paging past the last ref becomes indistinguishable from a dead function. Reconcile as one design: adopt UR-11's post-filter numeric count as the shared empty signal (drop UR-5's find_references-only message and the constant-true `symbol_resolved`), and forbid offset-past-end from collapsing into ok+[] (it must be distinguishable via total).
- **major** [UR-20, UR-21, UR-22, UR-24] UR-20/21/22/24 quadruple-cover the silent-empty-corpus trap — consolidate on one structured status field — Four onboarding findings attack one root cause (missing mount / glob-matches-nothing reports healthy). UR-20 adds doc_index_chunk_count to status; UR-24 adds a freeform notes/config_warnings array; UR-22 rewords search_docs not_found to guess 'config broken'; UR-21 validates project_root in BOTH analyzer.py and doc_store.py. Shipped independently they conflict: a missing mount would emit two error channels (UR-21), a sometimes-wrong prose diagnosis (UR-22 misdiagnoses exclude-pattern/empty-file cases), and a notes array that is adopt-path-blind (UR-24's counts live only in _rebuild_locked; the DS-24 adopt path skips rebuild, so the highest-value note vanishes in steady state). UR-20's collection.count() works on both build and adopt paths and is machine-parseable — consistent with UR-6/UR-8's move away from English-to-NLP. Consolidate: one structured doc_chunk_count (+optional file_count) on status; UR-22 points at status instead of guessing; UR-24 folds in; UR-21 reconciled to not double-emit.
- **major** [UR-11, UR-12] UR-11 and UR-12 would ship two divergent truncation conventions for the same response-economy problem — UR-11 puts `total`+`truncated` (real post-filter counts) on the LSP list tools; UR-12 adds a bespoke `truncated` flag on search_docs with different, semantically wrong meaning — Chroma returns top-k nearest neighbors, so there is no match/non-match boundary and no meaningful 'total', and firing truncated conflates 'your number was capped' with 'more relevant results exist'. Implemented separately, a client sees two truncation contracts where the same field name means different things. Design one response-economy truncation contract across both surfaces, or (cleaner, per digest) drop UR-12's `truncated` entirely and keep only an explicit upper-bound behavior, since a semantic top-k has nothing to truncate against. Either way UR-12 must not be built as an independent one-off min()+flag.
- **major** [UR-3, UR-11, UR-13, UR-15, UR-16, UR-18] UR-3's list-key rename conflicts with the settled Phase-3 schema and reshapes the same payload surface as UR-11/13/15/16/18 — implementation-plan Phase-3 explicitly pins find_symbol→results, document_symbols→symbols, goto_definition→definitions, find_references→references, hover→contents (confirmed at implementation-plan.md:294-306). UR-3's 'rename find_symbol→symbols' and 'standardize all six on results' options both contradict these settled schemas — a relitigation, not a fix, and its half-applied version leaves the most position-bearing tool (find_symbol) on the least self-describing key. Beyond the settled-decision conflict, UR-3 mutates the very result rows that UR-11 (total/truncated), UR-13/UR-16 (source line), UR-15 (drop container:null), and UR-18 (detail) also rewrite. Drop UR-3 (keys are settled), and treat the surviving payload-shape changes as one partitioned batch per tool so code, tools.md, and the 31 'results' test references don't drift across separate PRs.
- **major** [UR-16, UR-18] UR-18 and UR-16 both reshape the shared symbol_to_external helper feeding find_symbol AND document_symbols — core.py's symbol_to_external (used by find_symbol.py:91 and document_symbols.py:115) is the pinch point both findings pull on. UR-18 threads `detail` through it, but workspace/symbol results (find_symbol) are SymbolInformation/WorkspaceSymbol which have NO detail field — so this adds an always-null key to find_symbol's settled 6-field contract for zero benefit. UR-16 wants smallest-enclosing-symbol `container`, which needs each symbol's full range (the shared helper emits only a start position, discarding the tree). If both are built through the shared helper independently, they each regress find_symbol and fight for the same code. Coordinate: keep detail local to the document_symbols loop (raw `sym` is still in scope at document_symbols.py:114-128), and treat container-range logic as net-new document_symbols work, not a shared-helper change — so find_symbol's contract and test_ds02/test_ds09 stay untouched.
- **major** [UR-1, UR-2, UR-3, UR-5, UR-6, UR-7, UR-8, UR-9, UR-10, UR-11, UR-12, UR-13, UR-15, UR-16, UR-18, UR-20, UR-21, UR-22, UR-24] Dependency-safe implementation order across the four contested surfaces — Recommended sequence. (1) Independent/safe-first: UR-1 (docstrings only). (2) UR-2 needs redesign before build — the `ge=1` constraint breaks the settled envelope contract at the real MCP boundary (pydantic ValidationError → isError, not {status:error}); do the description half now, defer the validation half to a gate, not a schema constraint. (3) Error-envelope: land UR-6's builder+enum change ONCE (envelope.py error()/not_ready() signatures), then wire UR-7/8/9/10 as call-site instances — never before UR-6. (4) Emptiness/economy on list tools: settle UR-11's count/total+truncation contract first, then UR-5 collapses into it and UR-12 aligns or drops. (5) Payload enrichment: merge UR-13+UR-16 into one source-line design; keep UR-15/UR-18 local to document_symbols; drop UR-3 (settled). (6) Onboarding: one structured status field (UR-20) first, then UR-22/24/21 reconcile against it. Each cluster is one PR touching code+tools.md+tests in lockstep to avoid the doc-drift class known-issues.md tracks.

### Contract compatibility

- **blocker** [UR-2] UR-2 breaks the documented error-envelope contract for out-of-range positions — goto_definition.py:62-64 docstring pins the payload contract: 'supplying 0 or negative values returns an error immediately'; tools.md:176/227/289 documents line/character<1 → {status:error,message}; and implementation-plan.md:238-240 settles that malformed input is 'data in the envelope, not via MCP protocol-level errors.' UR-2's Field(ge=1) makes pydantic reject the argument before the function body, yielding an MCP isError CallToolResult (verified path per digest), NOT the documented error envelope. This is a silent breaking violation, not additive hardening. The paired manual-guard retention does not save the contract: pydantic fires first, so guards become dead code on the real MCP path (split-brain vs direct-call tests). The per-parameter description half is additive-safe; the ge=1 constraint must be dropped.
- **blocker** [UR-11, UR-5] UR-11 default page-size silently truncates the settled full-list schema and collides with ok+[] semantics — implementation-plan.md:294-301 pins find_symbol→results, document_symbols→symbols, find_references→references as WHOLE lists; tools.md documents no cap and shows full lists. A default page size of 50 (truncated:true) is a behavioral break to a settled schema for every existing caller, not an opt-in. Separately, offset-past-end returns ok+references=[]/symbols=[], which collides with the load-bearing settled semantic that ok+[] means 'real symbol, zero callers' (tools.md:239-254; envelope.py:23-26; implementation-plan.md:301) — the exact distinction UR-5 exists to protect. A count-only variant (total on the ok envelope, no default truncation) would be additive-safe; the proposed default paging is breaking.
- **major** [UR-7, UR-6] UR-7 Option A (return ok on doc-rebuild failure) violates the documented doc-failure→error contract — tools.md:455-458 pins refresh doc-rebuild/re-init failure to status=error; envelope semantics (implementation-plan.md:238) reserve error for failures. Option A returns ok on a doc-rebuild failure, silently violating that documented row, and also makes refresh return ok while search_docs still returns error for the same broken index (contradictory observability). Option B (keep error, add code/retriable/recovery) is contract-safe re: status vocabulary but requires changing the shared envelope.error(message) signature (envelope.py:69-71) — a dependency on UR-6, not a refresh.py-only change. Either option additionally requires editing tools.md:458, absent from the affected-files list.
- **major** [UR-3] UR-3's offered key-rename menu contradicts the settled Phase-3 schemas — implementation-plan.md:294-303 explicitly pins find_symbol→results, document_symbols→symbols, goto_definition→definitions, find_references→references, hover→contents. UR-3's alternatives 'rename find_symbol to symbols' and the fallback 'standardize all six on results' both contradict these settled schemas. The preferred direction (rename only search_docs's list to chunks) is compatible with the plan (search_docs's key is unpinned there) but still silently changes the tools.md:321/333/355 documented `results` contract — the doc rows must land in lockstep or code/doc drift results. Only the search_docs-only rename with synchronized docs is contract-safe; the rest of the menu is not.
- **major** [UR-15] UR-15 removes the documented `container` key from document_symbols entries — tools.md:123-134 documents each document_symbols entry as {name,kind,line,character,container} with container present-and-null, and implementation-plan.md:296 pins the settled schema as symbols:[{name,kind,line,character,container}]. UR-15's omit-when-null makes the key absent, changing the documented+settled entry shape: a consumer keying on entry['container'] (documented as always present) now KeyErrors. This is a shape change (key removal), not additive. It is defensible only if tools.md:129/133 and the settled schema note are updated in the same step to define absent-key=no-container; as written it silently diverges from both the doc and the settled schema.
- **major** [UR-13, UR-16] UR-13 default-on snippet silently changes the documented result shape for every caller — tools.md:229-237 and implementation-plan.md:300-301 pin find_references entries to exactly {file,line,character}; find_symbol (tools.md:55-66) and document_symbols (tools.md:123-131) have equally fixed shapes. UR-13 adds `snippet` with include_snippet default True — so every existing caller's payload gains an undocumented key without opting in, a shape change to three documented/settled contracts. UR-16 proposes the same enrichment but opt-in (context default False, field `source`) — additive-safe. The two collide (snippet vs source, opposite defaults, different tool sets) so shipping both yields duplicate params and two names for one line. Only the opt-in default-off form is contract-safe; document the added key in tools.md and the settled schema regardless.
- **minor** [UR-18] UR-18's shared-helper threading adds an always-null `detail` to find_symbol's settled 6-field contract — tools.md:55-66 and implementation-plan.md:294 pin find_symbol results to {name,kind,file,line,character,container}. find_symbol queries workspace/symbol (WorkspaceSymbol/SymbolInformation), whose LSP shape has NO detail field (detail exists only on DocumentSymbol). Threading detail through the shared symbol_to_external (core.py:405-412, used by both tools) therefore adds an always-null `detail` key to every find_symbol result, silently changing its documented 6-field contract for zero benefit. The document_symbols-local variant (read sym.get('detail') in that tool's loop) adds a genuine value and only extends document_symbols' settled schema (still requires a tools.md doc update). Keep the enrichment local to document_symbols; do not thread through the shared helper.
- **minor** [UR-19, UR-6, UR-8, UR-9] UR-19 extends the settled single-field hover schema with a `definitions` key — implementation-plan.md:302-303,306-307 pin hover→contents (markdown as-is; 'no parsing for the prototype') and tools.md:284-298 documents hover's ok payload as `contents` only. UR-19 adds a `definitions` list to hover under include_definition. Because it is opt-in (default False) the change is additive-safe at the shape level, but it still extends a deliberately single-field settled schema and folds goto_definition behavior into hover, in tension with the settled Option A strict-separation/orthogonality decision (implementation-plan.md:259-264). If pursued it needs a tools.md contract row and an explicit note that the settled hover schema is being extended; the zero-code parallel-calls alternative avoids the schema change entirely. Note additive error/not_ready field proposals (UR-6/UR-8/UR-9 code/retriable/recovery) are contract-safe since tools.md:22/24 say 'Always includes a message' not 'only message' — but each still requires the stale per-tool doc rows to be updated.

### Completeness — gaps both rounds missed

- **major** [UR-4] No MCP tool annotations — a read-only server never advertises readOnlyHint, and the one destructive tool (refresh) is unflagged — Every tool registers with a bare `@mcp.tool()` (goto_definition.py:27, validate_file_path.py:17, diagnostics.py:12/33, status.py:19, refresh.py:79, plus hover/find_*/document_symbols/search_docs). mcp 1.12.4's ToolAnnotations (readOnlyHint, idempotentHint, destructiveHint, openWorldHint) is supported by FastMCP but used nowhere in src. The whole product thesis is 'read-only Rust code navigation,' yet no nav tool sets readOnlyHint=True, so clients that auto-approve read-only tools must instead prompt for every hover/goto — the exact ergonomic tax the annotation exists to remove. Conversely `refresh` is genuinely destructive (unconditional teardown + wholesale re-index, refresh.py:81-140) but carries no destructiveHint. All 24 findings treat tools as descriptions+envelopes; none touch the protocol-level annotation surface that governs client trust/permission UX.
- **major** [UR-10, UR-16] Std/dependency navigation silently degrades to a misleading not_found, and absolute-path inputs are rejected with no conversion guidance — location_to_external returns None for any out-of-workspace path (core.py:293-306: relativePath containment-checked, out-of-workspace deps/std yield None). goto_definition then skips the mapped-None loc and, with an empty list, returns not_found 'No definition found at ...' (goto_definition.py:114-122). So 'where is Vec/String defined?' — a valid symbol whose definition lives in std/a crate — reports not_found, teaching the agent the symbol doesn't exist rather than 'defined outside the navigable workspace.' find_references (out-of-tree callers dropped) and hover share the boundary but never signal it. UR-10 only rewords find_symbol's not_found; the goto/find_references/hover dependency case is entirely unaddressed. Input side mirrors this: validate_workspace_file rejects even absolute paths pointing inside the workspace (core.py:196-240) with 'must be workspace-relative' but no 'strip the root prefix' hint — agents routinely hold absolute paths.
- **minor** [UR-1] MCP resources and prompts are entirely unused — the server is tools-only — server.py/core.py register only tools (core.py:79-82 constructs FastMCP with no resources/prompts; server.py auto-imports tool modules only). Two protocol features that fit this domain go unexploited: (1) Resources — the effective config, `status` snapshot, and the searchable doc corpus are natural read-only resources a client could subscribe to or attach as context without a tool round-trip; (2) Prompts — the settled find_symbol→goto/hover chain (the exact workflow UR-1 says is undocumented) is a textbook MCP prompt template that would encode the two-step loop once, client-side, instead of duplicating prose across four docstrings. No finding in either round considers whether the tool-only surface is the right protocol shape; the entire resources/prompts dimension is absent from the audit.
- **minor** [UR-7, UR-23] refresh is an unconditional, minutes-long, GLOBAL destructive action whose cost and blast radius are never disclosed up front — refresh (refresh.py:79-140) tears down the single module-level analyzer (`await mgr.restart()`, core.py:45 shows one global `_manager`) and re-indexes wholesale, during which every other gated tool returns not_ready (require_ready, core.py:112-113). Its docstring frames it as routine recovery and gives no latency/cost estimate ('The rebuild is fast' is stated only of the doc store). An agent has no signal that invoking refresh mid-task strands all in-flight navigation for the entire re-index window — a session-wide side effect from a call the schema presents as innocuous. UR-7 covers only the doc-rebuild error envelope and UR-23 only the elapsed-time-during-indexing signal; neither addresses the up-front cost/blast-radius disclosure or the missing destructive framing that would let an agent decide whether refresh is worth triggering.
- **minor** [UR-20, UR-21] No version or capability introspection — rust-analyzer version, multilspy version, and server version are surfaced nowhere — status (status.py:19-97) reports state/commits/doc-index fields but no rust-analyzer binary version, no multilspy version, and no server version; analyzer_status returns only `state` (diagnostics.py:13-30). RLM_RUST_ANALYZER_BIN is user-supplied (referenced in require_ready's error text, core.py:110), so a mismatched, stale, or wrong-arch analyzer is a plausible first-hour failure — yet an agent or operator has no in-band way to read what version is actually running to diagnose it or confirm an upgrade took effect. The onboarding-observability cluster (UR-20/21/24) focuses on config echo and corpus size but never on version/upgrade UX, so a capability/version mismatch stays invisible until it manifests as an opaque LSP error.

## Rejected in round 1

### hover returns uncapped raw rust-analyzer markdown

- **Dimension:** response-economy
- **Verdict:** `conflicts-settled`

PROBLEM IS TECHNICALLY REAL BUT THE FIX CONFLICTS WITH A SETTLED DECISION.

1. Problem real? Partly. hover.py:152 does return `ok(contents=contents_str)` where `contents_str = _contents_to_str(hov["contents"])` (line 146) — the raw normalized markdown with no size bound. The docstring (lines 70-73) confirms "no parsing or reformatting is applied." There is no cap, no signature_only flag, no truncation anywhere in the tools layer (grep for cap/max/limit/truncate finds hits ONLY in doc_chunking.py, which is the RAG embedder path bounded by BODY_TOKEN_CAP=200 for MiniLM's 256-token window — unrelated to hover). So yes, a heavily-documented item can return a large hover verbatim.

2. Tracked/implemented? Not implemented. Not in known-issues.md Open section (currently empty). KI-2 (docs/impl/known-issues.md:79-84) touched hover but was a stale UNVERIFIED marker, already resolved — unrelated to response economy.

3. CONFLICT with settled decisions — decisive. docs/planning/implementation-plan.md:306-307 records: "**`hover` — DECIDED:** return rust-analyzer's hover **markdown string** as-is (carries type signature + docs); **no parsing for the prototype**." Line 302-303 restates the contract: `contents: <rust-analyzer hover markdown string>`. The proposal's primary mode (`signature_only`) requires splitting rust-analyzer's markdown on the `---`/code-fence boundary to isolate the signature block — that is exactly the parsing the team DECIDED to defer ("no parsing for the prototype"). The soft-cap/`truncated` variant likewise contradicts "return ... as-is." Returning hover verbatim is a deliberate, recorded design choice, not an oversight. Per CLAUDE.md, settled decisions "should not be relitigated without new information," and this finding presents none — token-cost of large docstrings was foreseeable when "as-is" was chosen.

4. Worth it? Even setting the conflict aside, impact is low (revised: low): hover is a targeted single-position query the agent issues deliberately, not a fan-out; the large-hover case is a minority of calls; and the agent can already stop reading. The `---`-boundary split is also more fragile than claimed — rust-analyzer's hover markdown structure is version-dependent (the code already keeps defensive normalization for shape variance, lines 102-104), so "cheap to split on" is optimistic. Medium effort for low, conflicting benefit.

Verdict: conflicts-settled — the underlying observation (uncapped verbatim hover) is real, but the proposed remedy reopens the explicit "hover returns markdown as-is; no parsing for the prototype" decision (implementation-plan.md:306-307) with no new information, so it cannot be confirmed.
