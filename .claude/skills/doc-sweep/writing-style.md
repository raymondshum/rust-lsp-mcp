# Writing style — the `docs/guide/` register

The house style for `rust-lsp-mcp`'s reader-facing docs (`docs/guide/**` and the root `README.md`).
It exists so every page reads as one voice: **direct, concise, and factual, with no sales pitch.**
`doc-sweep`'s **revise** mode enforces it; authors and reviewers read it before writing.

These are technical docs. The register is plain and voiceless: explain the system, define terms on
first use, lead with the specific fact. Do not sell it, and do not add personality.

## The one rule that outranks the rest: judgment, not find-replace

The rules below target the *pitch register* and *filler*. They are not a lint pass to run blindly.

- **Accuracy beats concision, always.** If a cut drops a fact, a number, a symbol name, a qualifier,
  or a caveat, it is the wrong cut. A shorter sentence that is less true is a regression.
- **Keep an em-dash, a "deliberately", or a negation where it carries real meaning** — a genuine
  design-intent statement, a true parenthetical, a load-bearing technical contrast. The goal is
  *fewer* pitch constructions, not zero at any cost.
- **Do not inject voice.** Removing flourish is the job. Adding a different flourish is the same bug.

## What good looks like — representative samples

Short excerpts from docs whose register we want. Read for the *shape*: plain verbs, terms defined on
first use, specifics before adjectives, no hype.

**SQLite — dense, exact, zero marketing.** One sentence carries five precise qualifiers, each doing work:

> "SQLite is an in-process library that implements a self-contained, serverless, zero-configuration,
> transactional SQL database engine."
> — [sqlite.org/about.html](https://www.sqlite.org/about.html)

**The Rust Book — defines the term on first use, states costs flatly, no hype.** Note the last sentence:
a performance claim made as a plain fact, not a boast:

> "*Ownership* is a set of rules that govern how a Rust program manages memory. […] Rust uses a third
> approach: Memory is managed through a system of ownership with a set of rules that the compiler
> checks. If any of the rules are violated, the program won't compile. None of the features of
> ownership will slow down your program while it's running."
> — [doc.rust-lang.org/book, ch. 4](https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html)

**Stripe API — reference writing that leads with structure, not adjectives:**

> "The Stripe API is organized around REST. Our API has predictable resource-oriented URLs, accepts
> form-encoded request bodies, returns JSON-encoded responses, and uses standard HTTP response codes,
> authentication, and verbs."
> — [docs.stripe.com/api](https://docs.stripe.com/api)

**Julia Evans (jvns.ca) — plain, concrete, short sentences.** Concrete tool names over abstraction:

> "I got a bunch of replies with tools I hadn't heard of, so I thought I'd make a list here."
> — [jvns.ca](https://jvns.ca)

### Two rulebooks worth reading in full

- **Google developer documentation style guide** — [developers.google.com/style](https://developers.google.com/style).
  The load-bearing rules: *use active voice; make clear who's performing the action*; *use second
  person — "you" rather than "we"*; *put conditions before instructions, not after*.
- **Django's own "Writing documentation"** — [docs.djangoproject.com/…/writing-documentation](https://docs.djangoproject.com/en/dev/internals/contributing/writing-documentation/).
  Its sharpest rule, worth internalizing:

  > "Try to avoid using words that minimize the difficulty involved in a task or operation, such as
  > 'easily', 'simply', 'just', 'merely', 'straightforward', and so on."

## Explaining a hard concept

The guide carries genuinely hard ideas: readiness (a tool answering `not_ready` while rust-analyzer is
still indexing), the 1-indexed↔0-indexed position boundary, the single-writer Chroma constraint that
forced the daemon+client shape, and the cache-priming security trade-off (running untrusted `build.rs`
offline). The failure mode is a wall of accurate but impenetrable prose. The good sources share three
techniques for making a hard concept consumable without losing precision.

**1. State the problem before the mechanism.** Say what breaks without this, then how it's solved.
Stripe's idempotency-key docs open on the risk, not the API:

> "When creating or updating an object, use an idempotency key. Then, if a connection error occurs, you
> can safely repeat the request without risk of creating a second object or performing the update
> twice."
> — [docs.stripe.com/api/idempotent_requests](https://docs.stripe.com/api/idempotent_requests)

Our own README "Network isolation" section already does this — it names the threat first, then the fix:

> "Indexing a Rust project runs **untrusted code from that project on your host**: rust-analyzer
> compiles and executes the project's `build.rs` build scripts and proc-macros […]. The fix is to run
> the server with **no network**."
> — [`README.md`](../../../README.md), opening the section on the risk before any `--network none` flag.

**2. Use a concrete analogy, then state the rule exactly.** The Rust Book teaches borrowing — a hard
idea — with an everyday analogy, then pins it down with a precise rule so the analogy doesn't carry the
weight:

> "We call the action of creating a reference *borrowing*. As in real life, if a person owns something,
> you can borrow it from them. When you're done, you have to give it back. You don't own it. […] At any
> given time, you can have *either* one mutable reference *or* any number of immutable references."
> — [doc.rust-lang.org/book, ch. 4.2](https://doc.rust-lang.org/book/ch04-02-references-and-borrowing.html)

An analogy earns its place only when it's followed by the exact rule. Drop it once the rule is stated.

**3. Reduce to the essence, and kill the common misconception.** Julia Evans explains Linux containers
by refusing the mystique and correcting the wrong mental model head-on:

> "The word 'container' doesn't mean anything super precise. […] it's not a virtual machine at all,
> it's just processes running in the same Linux kernel."
> — [jvns.ca, "What even is a container?"](https://jvns.ca/blog/2016/10/10/what-even-is-a-container/)

If readers arrive with a wrong model ("I can warm-start a second `rust-lsp-mcp` server with `docker
exec` for speed", "the server needs network to embed docs", "positions are 0-based like LSP"), name and
correct it in one sentence rather than describing around it. The README's CLI section does exactly this
for the warm-start misconception: it states the old pattern is gone because two servers against one
Chroma store is a cross-process single-writer hazard, then gives the one-server-plus-client replacement.

## Flow and structure (within a page)

Voice fixes a sentence; flow fixes the *order*. Per [Diátaxis](https://diataxis.fr/), the concept pages
(architecture) are **explanation** (understanding-oriented); the reference pages (tools, cli,
configuration, components) are **reference** (a lookup surface). The two shapes differ — an explanation
builds an argument, a reference is a lookup table — so don't blend them: don't turn the tool reference
into an essay, or the architecture page into an API dump.

**1. Lead with the point; readers skim openers.** The first sentence of a page, a section, and a
paragraph must carry its claim — not warm up to it. Unbury the lead.

> "The opening sentence is the most important sentence of any paragraph. Busy readers focus on opening
> sentences and sometimes skip over subsequent sentences."
> — [Google Technical Writing](https://developers.google.com/tech-writing/one/paragraphs)

An opening is the highest-value edit on a page, and the one a conservative pass most often skips: the
judgment rule protects *facts*, not *clunk*, so on an opener assume it can be sharper and attack it.
These buried-lead shapes each warm up for a full sentence before the claim lands:

- **Self-label first:** "This page is the tool reference. Below you will find every tool …" → lead with
  the payoff: "Every MCP tool the server exposes, with its inputs and exact responses, is below."
- **Attribution / file-path first:** "`src/rust_lsp_mcp/positions.py` handles position conversion. The
  problem it solves: LSP is 0-indexed …" → lead with the claim, attribute after: "The MCP tool surface
  is 1-indexed while LSP is 0-indexed, so every position is converted at the boundary
  (`positions.py`)."
- **Category-label + negative:** "`rust-lsp` is a **client**: it is not a second server. It talks to the
  daemon …" → "`rust-lsp` is the command-line **client** that talks to the daemon. It runs no server of
  its own."
- **Symbol / provenance before the claim:** "`EXIT_NOT_READY` (`= 2`) is returned when …" → "A command
  exits `2` when the daemon is reachable but still indexing (`EXIT_NOT_READY`)."
- **Stacked parentheticals:** "A **read-only** (never edits source) **MCP** (Model Context Protocol)
  service exposing **LSP** (Language Server Protocol) navigation …" → lead with what it does, then gloss
  each acronym once the hook has landed.

**2. One idea per paragraph; split the walls.** Give each claim its own paragraph, and move any sentence
that drifts off-topic.

> "Restrict each paragraph to the current topic. […] Readers generally welcome paragraphs containing
> three to five sentences, but will avoid paragraphs containing more than about seven sentences."
> — [Google Technical Writing](https://developers.google.com/tech-writing/one/paragraphs)

**3. Answer What / Why / How, in that order.** A section that only says *what* leaves the reader without
the design intent. Explanation is where the *why* lives.

> "Provide background and context in your explanation: explain why things are so — design decisions,
> historical reasons, technical constraints."
> — [diataxis.fr/explanation](https://diataxis.fr/explanation/)

**4. The page-level funnel (our applied shape).** State the problem the concept exists to solve *before*
the machinery, so the mechanism reads as an answer, not a specification dropped on the reader. The
README's isolation and CLI sections model this: threat/limitation first, launch shape second.

**5. Drill-down: a child page must not re-pitch its parent.** The reader's path is a funnel — the front
door (`README.md`) hooks and routes; the guide index routes and maps; a reference page inventories.
When a page's opening restates what the reader just read on the page that linked here — the failure mode
where `README.md`, `index.md`, and `architecture.md` all re-define "read-only Rust navigation over MCP"
in near-identical words — drilling in adds nothing. Each page's opening should carry only what *that*
page uniquely owns and assume the parent was read. This is a **cross-page** check no single-page reviewer
can see: it needs a reader-path pass over the spine (`README` → index → the first drill-down pages)
reading them together. Fix it by cutting or tightening the restatement **in place** — deletion and
rewording are within-page and pin-safe; never *relocate* pinned content across pages.

A within-page flow pass reorders and re-paragraphs; it does **not** move content between pages (that
would move which page pins which source and break the freshness contract). Keep every fact, link, and
`source_pins` entry; change only the order and the paragraphing.

## Banned patterns — with before/after

The taxonomy is the point; the examples are illustrative (some domain-adapted, some carried from the
sibling `ansible-service` sweep the gate was ported from).

**1. Meta-narration / self-labeling.** Don't narrate the document's own honesty or significance.

> Before: "**That's the whole design, and each piece of it is built the way you'd build it for real.**"
> After: (cut) — keep the content the sentence framed; drop the framing.

Also in this family: "read this before you judge", "so you're not surprised", "the honest part",
"Honesty caveat". Delete the label; keep the content under it.

**2. Negation-parallelism ("not X, it's Y" / "not just X but Y").** State what it *is*, once.

> Before: "The loopback bind is not a convenience — it is the entire security boundary."
> After: "The loopback bind is the entire security boundary."

> Before: "`--wait` isn't just a timeout; it rides out first-run indexing."
> After: "`--wait` rides out first-run indexing."

**3. Em-dashes as a habit.** Most guide em-dashes are a `label — description` tic, not a real aside.
Use a colon, period, comma, or parentheses. Keep an em-dash only for a genuine mid-sentence aside
(aim for ≤1–2 per page).

> Before: "**`status`** — reports whether the index is ready."
> After: "**`status`**: reports whether the index is ready."

**4. Minimizing words** (per Django): `easily`, `simply`, `just`, `merely`, `straightforward`. Cut them.

**5. Hollow intensifiers:** `genuinely`, `truly`, `really`, `honestly`, and `deliberately` *when it is
filler*. Cut, don't replace. Keep `deliberately` when it names real design intent ("the listener binds
to `127.0.0.1` deliberately, and is not configurable").

**6. Rule-of-three padding.** Trim "fast, reliable, and efficient" triads to the load-bearing items or
one exact claim. Don't keep a triad for rhythm.

**7. Transition padding:** `Moreover`, `Furthermore`, `Additionally`, `It's worth noting`, `Notably`,
`That said`. Usually deletable with no loss.

**8. Fancy verbs → plain:** serves as / features / boasts / presents / represents → **is** / **has**.
leverage / utilize → use. comprehensive → complete. robust → reliable. seamless → smooth.

**9. Over-bolding.** Bold genuine first-use terms and key vocabulary. Unbold decorative emphasis on
whole clauses. (Light touch — structure still helps the reader.)

## Preserve exactly (never trade for concision)

Facts, numbers, symbol names, caveats; headings and section order; tables and list structure; links
(text and target); code spans; and all YAML frontmatter (`source_pins`, `cite`, `okf_version`,
`timestamp`). Don't rename files, move sections, or add/remove links. A voice pass changes prose, not
the source-pin contract — rewording doesn't move source, so pins stay valid (re-run
`python3 scripts/check_doc_freshness.py` anyway to catch a dropped cited symbol).

### Repo accuracy landmines

Grounded against `main` at the as-shipped commit. Never phrase around these; verify each against the
code and `docs/impl/known-issues.md`.

- **The daemon+client shape replaced the old warm-start pattern.** The current CLI path is a long-lived
  **daemon** container plus the `rust-lsp` **client**; there is exactly **one** server process per
  container. The old `docker exec -i … rust-lsp-mcp` (starting a *second* stdio server inside a running
  container) is **gone** because two servers against one Chroma store is an unguarded cross-process
  single-writer hazard (see known issue **KI-13**). Never document the warm-start pattern as current.
- **Positions are 1-indexed on the MCP tool surface, 0-indexed in LSP.** The tool API takes and returns
  **1-indexed** line/character; conversion to LSP's 0-indexed happens at the boundary
  (`src/rust_lsp_mcp/positions.py`). Never call the tool-facing positions 0-based.
- **Two distinct status vocabularies — don't conflate them.** The `status` tool reports *index* state as
  `ready` / `indexing` (with an `error` state). Individual tools that need the index return an *envelope*
  `status` of `ok` / `not_ready` / `not_found` / `error`. An empty/negative result (`not_found`) is a
  valid answer, not a failure.
- **CLI exit codes are exact:** `0` ok/not_found, `1` error, `2` not_ready, `3` daemon unreachable
  (`EXIT_OK` / `EXIT_ERROR` / `EXIT_NOT_READY` / `EXIT_UNREACHABLE` in `src/rust_lsp_cli/cli.py`). Keep
  the mapping verbatim — a shell caller branches on it.
- **The embedding model is baked into the image** (`all-MiniLM-L6-v2`, warmed at build time in the
  `Dockerfile`), so **runtime needs zero network for embeddings**. The only runtime network use is cargo
  fetching the scanned project's crates.io dependencies. Don't imply the model downloads at runtime.
- **The server is read-only and repo-agnostic.** It never edits source; it analyzes whatever Rust
  project is bind-mounted read-only at `/project`. Keep "read-only" and "repo-agnostic" exact.
- **Loopback-only is not authentication.** The daemon's HTTP listener binds `127.0.0.1` inside the
  container and is hard-coded that way; that is the whole protection, with no auth of its own. Never
  describe it as authenticated, and never suggest adding a `ports:` mapping.
- **SELinux relabel is lowercase `z` (shared), not uppercase `Z` (private).** The shared label keeps the
  bind compatible with `scripts/prime-cache.sh`; the private label scopes the source tree to one
  container and breaks later mounts.
- Keep **cited symbol names present in prose** (e.g. `EXIT_NOT_READY`, `external_to_lsp`); the freshness
  gate checks they still appear.

## Per-tier touch weight (for a revise sweep)

- **`README.md`** — heaviest. It is the front door and carries any pitch; de-pitch hard, and it is the
  freest to restructure (no OKF pins).
- **Explanation pages** (`architecture.md`) — medium. Dense, correct content; mostly em-dash reduction
  and breaking a few negations. Protect the detail and the *why*.
- **Reference pages** (`tools.md`, `cli.md`, `configuration.md`, `components.md`, `dependencies.md`),
  the guide `index.md` — lightest. Already factual reference. Em-dashes only, and only where the rewrite
  is clean. When in doubt, leave the sentence alone.
