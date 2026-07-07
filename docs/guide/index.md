[← Back to the README](../../README.md)

# Documentation guide

The README covers the quick start; these pages go deeper into how the system works and how to extend it. Each page is self-contained and links back here.

## Pages

- [Architecture](architecture.md) — the big picture: how a request flows through the system, and the key design ideas (readiness, the response format, how documentation search works).
- [Tools / API reference](tools.md) — every tool the server offers, its inputs, and the exact responses it returns.
- [Configuration](configuration.md) — every setting and environment variable, with defaults and what each one does.
- [CLI reference](cli.md) — the `rust-lsp` command-line client for agents without MCP tool access: subcommands, exit codes, `--wait`, and troubleshooting.
- [Development setup](development.md) — how to set up the development container, run the server, and run the two kinds of tests.
- [Components](components.md) — a guided, module-by-module tour of the source code.
- [Dependencies](dependencies.md) — the main libraries and external tools the project relies on, and why.
- [Agentic coding](agentic-coding.md) — how this project is built with Claude Code: the delivery lifecycle, the build conventions, and the Claude configuration.

## Where to start

The guide has two tracks. Pick the one that matches what you want to do.

**Operators — you want to *use* the server.** Start with the README quick start, then:

- **Wiring the server into an AI assistant** — [Tools / API reference](tools.md) and [Configuration](configuration.md).
- **Driving it from a shell-only agent (no MCP tools)** — [CLI reference](cli.md).

**Contributors and engineers — you want to work *on* the server.**

- **The design and request flow** — [Architecture](architecture.md).
- **The code, module by module** — [Components](components.md).
- **Setting up to build and test** — [Development setup](development.md).
- **The libraries and tools it relies on** — [Dependencies](dependencies.md).
- **How the project itself is built with Claude Code** — [Agentic coding](agentic-coding.md).

Each guide page (not the README) carries `source_pins` frontmatter naming the source files it
describes, checked by `scripts/check_doc_freshness.py` so the prose stays honest against the code — a
convention that matters when you edit these pages.
