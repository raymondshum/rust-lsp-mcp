# Documentation & research policy

Read this before trusting memory on any library/API/SDK/tool detail.

**Prefer Context7 over training knowledge.** My training priors may be stale.
For any library/API/SDK/tool detail — especially the official Python `mcp` SDK,
ChromaDB, multilspy, rust-analyzer/LSP — query the Context7 MCP
(`resolve-library-id` → `query-docs`) before relying on memory. Use it whenever
I am not fully confident a detail reflects the current implementation, need to
confirm an API shape, or hit a strange edge case.

**Prefer up-to-date documentation generally.** When researching anything,
favor current first-party docs over recollection. For MCP server construction,
use the `mcp-builder` skill first; fall to Context7 for what the skill doesn't
cover or to verify recency.

**When docs are silent, read the source.** If Context7/first-party docs don't
cover a detail, inspect the installed or downloaded package source (e.g.
`pip download <pkg> --no-deps --no-binary :all:` and unpack, or read the wheel)
before speculating — then cache the finding in `docs/reference/` like any other.

**ChromaDB is a component, not a knowledge source.** ChromaDB is the local
vector store we build over *this project's* markdown docs. It is never a source
of truth about external libraries (including the ChromaDB API itself — that's
Context7's job).
