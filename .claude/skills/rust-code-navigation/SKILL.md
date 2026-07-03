---
name: rust-code-navigation
description: >
  Use rust-analyzer-backed semantic navigation to answer questions about a
  Rust codebase — find where a symbol is DEFINED, find every REFERENCE/caller
  across the project, get the TYPE and doc-comment at a position (hover), list
  the symbols in a file, or search the project's Markdown docs. Prefer this
  over grep/ripgrep/Read whenever the question is semantic ("where is X
  defined?", "who calls X?", "what type is this?", "what does this do?")
  because it returns rust-analyzer's ground truth, not text matches. Rust
  projects only; read-only (cannot edit); requires the index to be ready.
---

# When to use rust-lsp-mcp

Reach for this MCP server when ALL of these hold:
- The codebase is **Rust** (has `Cargo.toml` / `.rs` files).
- The question is about **meaning**, not raw text: definitions, references,
  callers, types, signatures, doc comments, or "what symbols live here."
- You want an **accurate, cross-file** answer. grep finds the string `parse`
  in comments, strings, and unrelated identifiers; `find_references` finds the
  actual uses of *that* symbol. This is the main reason to prefer it.

## Pick the tool by intent

| The user/agent wants…                    | Tool                       |
|------------------------------------------|----------------------------|
| Locate a symbol by (partial) name        | `find_symbol`              |
| "Where is this defined?" (from a spot)   | `goto_definition`          |
| "Who uses / calls this?"                 | `find_references`          |
| Type + doc for the thing at a position   | `hover`                    |
| Everything defined in one file           | `document_symbols`         |
| NL question over the project's docs      | `search_docs`              |
| Is the index ready? / rebuild it         | `status`, `refresh`        |

## Typical flow
1. Call `status` first if you haven't — tools return `not_ready` while
   rust-analyzer indexes (seconds to a couple minutes). Wait and retry, don't
   fall back to grep and report a wrong answer.
2. `find_symbol` by name to get a `file` + 1-based `line`/`character`.
3. Feed that position into `goto_definition` / `find_references` / `hover`.
   **Positions are 1-based; `character` counts Unicode codepoints.**

## How to invoke

Two ways to reach these tools — check your own tool list first.

- **MCP tools available** (`find_symbol`, `goto_definition`, etc. appear in
  your tool list): use them exactly as described above.
- **MCP tools NOT available** (e.g. a Bash-only subagent): use the `rust-lsp`
  CLI over a shell instead. Same server, same semantics, same position
  protocol — just a different transport.

### Reaching the CLI

The CLI is a thin client for a warm daemon that must already be running.
Where your shell sits relative to that daemon decides the prefix:

- **Inside the daemon's own environment** (a shell sharing its
  container/env — e.g. this repo's dev container running the daemon
  directly): call it with no prefix — `rust-lsp <cmd> …`.
- **From the host, or any other shell:** `exec` into the daemon container.
  Auto-detect the engine the way `scripts/prime-cache.sh` does — prefer
  `docker` if its daemon answers, else fall back to `podman`:

  ```sh
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ENGINE=docker
  else
    ENGINE=podman
  fi
  "$ENGINE" exec rust-lsp-mcp rust-lsp <cmd> …
  ```

  The container name is `rust-lsp-mcp` by default, or `rust-lsp-mcp-isolated`
  for the network-isolated compose variant — substitute accordingly.

### First call after the daemon starts

rust-analyzer indexing takes seconds to a couple of minutes after container
start. Make your first call in a session ride that out with `--wait`, given
**before** the subcommand:

```
rust-lsp --wait 180 status
```

Once `status` reports readiness, later calls don't need `--wait`.

### Command reference

Same intents as the table above, in CLI form. Positions are still 1-indexed
with `character` counting Unicode codepoints.

| Intent                                  | CLI command                                                                                |
|------------------------------------------|---------------------------------------------------------------------------------------------|
| Locate a symbol by (partial) name        | `rust-lsp find-symbol NAME`                                                                  |
| "Where is this defined?"                 | `rust-lsp goto-definition FILE LINE CHARACTER`                                               |
| "Who uses / calls this?"                 | `rust-lsp find-references FILE LINE CHARACTER [--include-declaration] [--include-source]`    |
| Type + doc for the thing at a position   | `rust-lsp hover FILE LINE CHARACTER`                                                          |
| Everything defined in one file           | `rust-lsp document-symbols FILE`                                                              |
| NL question over the project's docs      | `rust-lsp search-docs QUERY [--limit N]`                                                      |
| Is the index ready?                      | `rust-lsp status`                                                                             |
| Rebuild the index                        | `rust-lsp refresh` — **see the warning below first**                                          |
| Does this path exist in the workspace?   | `rust-lsp validate-file-path FILE`                                                            |
| Client/server version info               | `rust-lsp version`                                                                            |

Full reference, including every flag: `docs/guide/cli.md`.

### Exit codes

| Code | Meaning                              | What to do                                                                                   |
|------|---------------------------------------|-----------------------------------------------------------------------------------------------|
| 0    | `ok` **or** `not_found`               | `not_found` is an ANSWER, not a failure (same empty≠error doctrine as above) — don't retry it. |
| 1    | tool `error`                          | Read the JSON envelope's `message`/`recovery` field on stdout.                                |
| 2    | daemon reachable but `not_ready`      | Retry with `--wait`. (Argparse usage errors — bad/missing arguments — also exit 2; tell them apart by stderr usage text plus empty stdout: a real envelope always prints JSON to stdout.) |
| 3    | daemon unreachable                    | Start it: `docker compose up -d`.                                                              |

The envelope JSON always goes to stdout; diagnostics/hints go to stderr.

### `refresh` is shared — use it carefully

The daemon is one process shared by every caller of this project's tools.
`refresh` tears down and rebuilds the whole index — every other caller's
navigation calls will see `not_ready` until it finishes. Only run it when
you have a specific reason to believe the index is stale (e.g. the project's
source changed underneath it); don't call it speculatively or to "fix" an
unrelated error.

## When NOT to use it
- **Not a Rust project** → the server can't help; use normal search.
- **You need to edit code** → it's strictly read-only; navigate here, edit
  with your own tools.
- **You already have the file open and the answer is trivially local** → just
  Read it; don't round-trip the LSP.
- **Empty result ≠ error.** `ok` with an empty list means "no references /
  no symbols," which is a real answer — don't retry it as a failure.
- `not_found` means the name/position doesn't resolve; `error` means bad input
  or an LSP failure — re-check the path/position rather than retrying blindly.
