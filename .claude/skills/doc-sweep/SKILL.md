---
name: doc-sweep
description: Generate, refresh, or revise the reader-onboarding documentation tree under docs/guide/ (plus the root README.md) with a verification-backed multi-agent sweep — each page written from real code, source-pinned, then accuracy- and adversarially-reviewed. Use when the user says "write the docs", "documentation sweep", "onboarding docs", "regenerate the guide", "check/refresh stale docs", or "revise the docs for voice / de-pitch / concision / flow / structure". Three modes: author (greenfield or expansion), refresh (detect drift via scripts/check_doc_freshness.py, then regenerate only the affected pages), and revise (voice + within-page flow/structure rewrite against writing-style.md — de-pitch, concision, sharpen buried/clunky openings, cut cross-page drill-down redundancy along the reader path, lead-first ordering, one-idea-per-paragraph — changing prose and within-page order but never facts/links/pins, and never moving content between pages). Preserves the human gates — audience/depth, the as-shipped-vs-in-flight boundary, and the page set.
---

# doc-sweep

Build and maintain `docs/guide/` — the tree that takes a reader from **zero knowledge** (root `README.md`)
down through concept pages to a **per-module inventory** of every component. Every page is grounded in
real source, carries OKF `source_pins`, and is guarded by a drift gate. This is the executable form of
the [`docs/conventions/documentation-writing.md`](../../../docs/conventions/documentation-writing.md)
method ("ground → contract → write → verify"), hardened with an adversarial newcomer pass and pins.

The exemplar is already in this repo — match its shape: [`docs/guide/index.md`](../../../docs/guide/index.md)
(reader-path map + page map), [`docs/guide/components.md`](../../../docs/guide/components.md)
(a Tier-B module-by-module inventory), [`docs/guide/cli.md`](../../../docs/guide/cli.md)
(the as-shipped-vs-superseded boundary done right — the daemon+client shape that replaced the old
warm-start `docker exec … stdio` pattern), and [`scripts/check_doc_freshness.py`](../../../scripts/check_doc_freshness.py)
(the gate).

## Two audiences, two depths — the Tier A / Tier B split

The guide serves two readers, and every page is written to one of them:

- **Tier A — operators.** Someone with **no prior knowledge** who wants to *use* the MCP server or the
  `rust-lsp` CLI: wire it into an assistant, learn what functionality exists at a high level, and run it.
  Tier-A pages orient and instruct; they do not inventory internals. Pages: root `README.md`,
  [`tools.md`](../../../docs/guide/tools.md) (the per-tool API reference), [`cli.md`](../../../docs/guide/cli.md),
  [`configuration.md`](../../../docs/guide/configuration.md).
- **Tier B — contributors and engineers.** Someone working *on* the server: the module map, the design
  ideas, the dependency rationale, the dev loop. Tier-B pages go per-module and explain *why*. Pages:
  [`architecture.md`](../../../docs/guide/architecture.md), [`components.md`](../../../docs/guide/components.md),
  [`dependencies.md`](../../../docs/guide/dependencies.md), [`development.md`](../../../docs/guide/development.md),
  [`agentic-coding.md`](../../../docs/guide/agentic-coding.md).

[`index.md`](../../../docs/guide/index.md) is the router that sends each audience to its track.

## Three modes

- **author** — no guide yet, or a new area to cover. Runs the full sweep.
- **refresh** — the guide exists; find drifted/stale pages and regenerate only those. Also the mode for a
  **verify + pin retrofit**: an existing pinless guide gets every claim re-grounded in current source and
  `source_pins` frontmatter added, page by page.
- **revise** — the guide is factually current but the *writing* needs work (too wordy, too pitch-y, or
  poorly ordered). A voice + within-page flow rewrite against [`writing-style.md`](writing-style.md):
  change prose and within-page order (reorder sections, re-paragraph), never facts, links, or pins, and
  never move content between pages.

## Human gates — ask BEFORE fanning out; never auto-decide

1. **Audience & depth.** Tier A (operator: orient + instruct + link) vs Tier B (contributor: per-module
   inventory, every module named, the *why*). Logic-heavy areas (the tools, the analyzer/doc-store core)
   → Tier B on their component/architecture pages; thin dirs (scripts, packaging) → Tier A depth.
2. **As-shipped vs in-flight boundary.** Document the state shipped on `main`. If a milestone is in flight,
   mark its features **not-yet-landed** and describe the current shipped behavior instead. Never write
   unlanded code as if it exists. (Confirm which branch/commit is "as shipped.")
3. **The page set / tree.** The exact list of pages and the cross-link map.

## Author mode — steps

1. **Shallow-map.** List components, existing docs, and the code layout. Sketch the concept tree
   (concept layer + component layer). Do this on the main thread; keep it cheap.
2. **Resolve the gates above** with a few focused questions.
3. **Author a shared contract** (once, on the main thread — this is the coherence backbone). It fixes:
   the exact file tree + filenames; the cross-link map; the frontmatter + pin schema (below); a term
   **glossary** writers must use; the as-shipped rule; the Tier-A/B rule; and per-page scope + the source
   files each writer must read + the pin commit (`git rev-parse` of the as-shipped HEAD). Every writer and
   reviewer reads this first.
4. **Fan out writers — one page per agent, on disjoint files.** Each writer reads the contract + its
   assigned source, then writes the page **grounded in what it read** (never from memory), with
   `source_pins` frontmatter for every file it describes.
5. **Two independent reviews per page:**
   - **Accuracy** — re-read the source, fix every claim by direct Edit, verify each `source_pins` path and
     `cite` anchor resolves, enforce the as-shipped rule.
   - **Adversarial newcomer** — zero-prior-knowledge read: unexplained jargon, funnel breaks (a term used
     before it's introduced), broken drill-down links, and **stale-source-comment traps** (a writer
     trusting a stale header/README note/handoff doc instead of the code + `docs/impl/known-issues.md`).
6. **Orchestrator merges and gatekeeps — do NOT delegate this.** Fix majors yourself (surgical edits);
   add a `docs/guide/glossary.md` for systemic jargon rather than patching every page; run
   `python3 scripts/check_doc_freshness.py`; mechanically check every relative link resolves; run the
   project test suite (`uv run pytest`, or the docs-honesty subset). Echo the target dir before any gate.
7. **Scaffold the drift gate if absent** — ensure `scripts/check_doc_freshness.py` exists (see the gate
   contract below). This repo does **not** wire it into CI; it is run on demand by the orchestrator.
8. **Commit** one Conventional Commit. Do not self-merge; PR per the delivery lifecycle
   ([`docs/conventions/lifecycle.md`](../../../docs/conventions/lifecycle.md)).

## Refresh mode — steps

1. **Run `python3 scripts/check_doc_freshness.py`.** It reports HARD failures (a pinned path/symbol
   vanished — the doc is now wrong) and SOFT advisories (a pinned file changed since the doc was pinned —
   re-review). A **pinless** page is *skipped*, not failed — a first-time pin retrofit therefore starts
   from an all-green, all-skipped run.
2. **Build the work-list** = docs with hard failures + docs with soft drift on load-bearing files
   (+ every pinless page, for a first-time retrofit).
3. **For each, run the author-mode write→accuracy→adversarial pipeline scoped to that one page**,
   re-grounded in current source; **re-pin** — set `source_pins` `commit` to the current HEAD, and
   add/remove pins for modules that were added/renamed/deleted.
4. **Orchestrator merges, re-runs the checker until EXIT=0 + link-check, commits.**

## Revise mode — steps

A voice **and within-page flow** pass, not a re-architecture. Ground the fan-out in the style contract,
then rewrite page-by-page with a two-reviewer guard, exactly as author mode does for facts.

1. **Ground the current state.** Grep the tree for the patterns [`writing-style.md`](writing-style.md)
   bans (em-dash counts, negation-parallelism, minimizing words) **and** scan for flow problems with a
   *hostile-editor* eye — the judgment rule protects facts, not clunk, so assume an opener can be
   sharper and attack it (a conservative pass under-edits openings and misses cross-page redundancy):
   - **buried/clunky openings** — a page or section opening on a self-label, a file-path/attribution, a
     category-label-plus-negative, a symbol/provenance, or a stacked parenthetical instead of its claim
     (see `writing-style.md` "Flow and structure" §1). Openings are the highest-value edit;
   - paragraphs over ~7 sentences, and sections that state *what* but never *why*;
   - **reader-path drill-down redundancy** — a child page whose opening re-pitches the parent that
     linked to it (`README` → guide index → page restating the same "read-only Rust navigation over MCP"
     hook; §5).

   Pick the touch-weight tier per page (heaviest on `README.md`; lightest on component reference).
   **`README.md` is in scope** for a revise: it is repo-root and carries no OKF pins, so it is the
   freest page to restructure and is often the clunkiest opener in the tree.
2. **[`writing-style.md`](writing-style.md) is the shared contract** — every rewrite and review agent
   reads it first, especially the "Flow and structure (within a page)" and "Explaining a hard concept"
   sections. It carries the judgment rule (accuracy > concision), the banned patterns with before/after,
   the representative samples, and the repo accuracy landmines.
3. **Fan out — one page per agent, disjoint files.** Each agent edits its page in place for voice and
   **within-page** flow: de-pitch the prose, and reorder sections / re-paragraph so the page reads
   lead-first, one idea per paragraph, problem before mechanism. Preserve every fact, table, link, code
   span, and `source_pins`/frontmatter. **Within-page only — never move content between pages** (that
   moves which page pins which source and breaks the freshness contract).
4. **Two independent reviews per page, reading that page's `git diff`:**
   - **Accuracy-preservation** — did any fact, number, symbol name, caveat, or link change? Was any
     content **moved to another page**? Do all `source_pins`/`cite` anchors still resolve? Enforce the
     landmines (daemon-not-warm-start; 1-based positions; `not_ready`; embedding baked into the image).
     Any changed fact, dropped cite, or cross-page move is a critical finding.
   - **De-pitch & flow** — is the pitch register gone (meta-narration, negation, intensifiers, habitual
     em-dashes), *and* does the page now read lead-first (its opening carries the claim, not a
     self-label/attribution/negation/parenthetical-stack), one-idea-per-paragraph,
     problem-before-mechanism, with explanation and reference shapes kept distinct? Flag only genuine
     issues, never a real technical em-dash or fact.

   Both reviewers above are **per-page**, so neither can see cross-page redundancy. When the sweep
   touches the **reader-path spine** (`README` + the guide index + the first drill-down pages like
   `architecture`/`tools`), add one **reader-path-spine reviewer** that reads those pages *together* and
   flags where a child's opening re-pitches its parent, plus any front-door list that links the same
   destination twice. Its fixes are cuts/rewrites in place, never cross-page moves.
5. **Orchestrator quality pass — do NOT delegate.** Fix the flagged residue by surgical edit (agents
   told to "preserve structure" often leave pitch inside *headings* and under-reorder — fix those
   yourself). Confirm no heading rename or reorder breaks an anchor link before landing it.
6. **Gatekeep:** re-run `python3 scripts/check_doc_freshness.py` (0 hard failures — catches a cited symbol
   a reorder dropped), mechanically check every relative link resolves, run the test suite. No re-pinning
   — a within-page pass doesn't move source. Echo the target dir first.
7. **Commit** one Conventional Commit. Do not self-merge; PR per the delivery lifecycle.

## The drift gate — `scripts/check_doc_freshness.py`

Frontmatter each guide doc carries (repo-root-relative paths; `commit` = the as-shipped HEAD):

```yaml
source_pins:
  - path: src/rust_lsp_mcp/tools/find_symbol.py
    commit: <HEAD>
cite:                                    # optional load-bearing anchors
  - src/rust_lsp_mcp/positions.py:to_lsp_position
```

- **HARD (fails the gate, exit 1):** every `source_pins.path` exists; every `cite` anchor resolves (file
  present; for `path:symbol`, the symbol is grep-findable). This catches the drift that makes a doc lie.
- **SOFT (advisory, never fails):** `git log <commit>..HEAD -- <path>` — if changed, print a re-review line.
  A byte change ≠ the prose is wrong, so a human/agent confirms and re-pins.
- Definitional/router pages (the guide `index.md`, a glossary) may carry no pins — the checker skips them
  gracefully. The **root `README.md` gets no OKF frontmatter** and lives outside `docs/guide/`, so the
  gate never scans it; its accuracy is guarded by review, not pins.

The gate is **not wired into CI** in this repo (by design). Run it on demand from the repo root:
`python3 scripts/check_doc_freshness.py` (add `--guide-dir DIR` to point elsewhere).

## Boundaries

- **Ground every claim in real code.** Never write from memory. Distrust stale in-repo prose (old handoff
  docs, README notes, module headers) — verify against the code and `docs/impl/known-issues.md`.
- **Orchestrator owns the merge, the glossary, and all gate runs.** Never delegate status/gate writes.
- **Writers touch disjoint files only** — no two agents on one file.
- **Document the shipped state on `main`;** flag in-flight-milestone features as not-yet-landed.
- **Repo-root `README.md` gets no OKF frontmatter;** `docs/guide/**` pages do.
- **Scale the fan-out to the ask** — a few pages for a small area, the full tree for a first build.
