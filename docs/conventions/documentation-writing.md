# Documentation writing methodology (ground → contract → write → verify)

Read before writing or substantially revising human-facing documentation — the
root `README.md`, the `docs/guide/` pages, or any prose meant to explain the
project to a person (as opposed to the build-handoff and planning docs).

Where external-library facts come from: [research-policy.md](research-policy.md)
(Context7-first; read source when docs are silent). Where learned patterns are
cached: [caching.md](caching.md).

## 1. Scope it first

Pin **audience**, **placement**, and the **page set** before doing anything
else. When any of these is genuinely ambiguous — who the reader is, where the
pages live, which pages exist — ask the user a few focused questions first; these
choices reshape everything downstream and are expensive to redo. See
[working-style.md](working-style.md).

## 2. Ground (don't write from memory)

Never describe the codebase from recollection. **Fan out parallel read-only
exploration agents**, each owning one domain (tools, configuration, components,
dependencies, dev setup), to produce **structured fact-sheets**: exact names,
parameters, defaults, versions, commands — each with a `file:line` citation.
Writers turn fact-sheets into prose; they do not invent.

## 3. Set a shared contract before fan-out

Define once and hand to every writer, so independently-written pages stay
consistent:

- the **file list** with exact filenames;
- the **link map** — breadcrumb/backlink to the README and the section index,
  sibling-page links, and source-file links (relative paths that resolve from
  each file's own location);
- the **style guide** (section 6 below).

## 4. Write (fan out, one page per agent)

One writer agent per page, on **disjoint files**, each fed only the relevant
fact-sheet(s) plus the shared contract. File-partitioning lets the pages compose
without conflict.

## 5. Verify (the orchestrator gatekeeps)

- **Independent review** of every page against the source: factual accuracy (no
  invented fields, correct defaults), the required style, internal consistency
  (no two pages disagreeing), and completeness. This is the doc-flavored form of
  the build's [adversarial review](../handoff/adversarial-review.md) — don't ship
  a producing agent's self-report.
- **Mechanically check every relative link resolves.**
- **Keep indexes current:** when adding or moving a file under `docs/`, update the
  `index.md` at that level in the same step.
- Apply review fixes yourself (small, surgical edits) rather than another full
  round-trip.

## 6. House style for docs

- **Consumer-first and plain.** Write for a smart non-specialist. Explain any
  necessary technical term in one plain sentence on first use; avoid shorthand
  and unexplained abbreviations.
- **Lead with the easiest path** (a quick start); let detail fan out into linked
  pages. Keep the front door skimmable — detail lives one level down.
- **Connect the pages.** Every guide page opens with a backlink to the README and
  the section index, and ends with a short "Related pages" list.
- Be honest about scope and status; don't overpromise.
