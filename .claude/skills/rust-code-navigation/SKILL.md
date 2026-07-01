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
