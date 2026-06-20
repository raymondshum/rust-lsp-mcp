# rust-lsp-mcp

A read-only service that lets an AI assistant explore a Rust codebase: navigate
its code, jump to definitions, find references, and search the project's
documentation — all without editing a single file. It is designed for developers
who want to wire a Rust project into an AI assistant for question-answering and
code exploration.

Under the hood it drives **rust-analyzer** — the same engine that powers Rust
support in VS Code and other editors — through a standard interface. It exposes
everything over the **Model Context Protocol (MCP)**, a standard way for AI
assistants to call external tools. The server communicates over standard
input/output (stdio); your client launches it as a subprocess.

## What it can do

**Navigate the Rust code:**

- Find a symbol by name (functions, types, constants, etc.)
- Jump to a definition — given a file position, return where that thing is defined
- Find all references to a symbol across the project
- Show the type and documentation for whatever is at a position ("hover")
- List all symbols defined in a file

**Search the documentation:**

- Ask a natural-language question and get the most relevant documentation
  passages from the project's Markdown files

**Check server state:**

- Check whether the server has finished indexing the project
- Rebuild the index (for example, after source files change)

A full per-tool reference is in the [Tools / API reference](docs/guide/tools.md).

## Quick start

**Prerequisites:** [Docker](https://www.docker.com/get-started/),
[VS Code](https://code.visualstudio.com/), and the
[Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers).

**Steps:**

1. Clone this repository:
   ```
   git clone https://github.com/raymondshum/rust-lsp-mcp.git
   ```

2. Open the cloned folder in VS Code and choose **"Reopen in Container"** when
   prompted (or run it from the command palette). The dev container — a
   preconfigured environment built with Docker — builds automatically. During
   that build it clones a sample Rust project, **ripgrep** version 14.1.1 (a
   popular command-line search tool), and installs all Python dependencies using
   the `uv` package manager. This is the project the server navigates by default.

3. Once inside the container, start the server:
   ```
   uv run rust-lsp-mcp
   ```
   An equivalent form is:
   ```
   python -m rust_lsp_mcp
   ```

**First-run indexing:** when the server starts, rust-analyzer indexes the Rust
project. This takes anywhere from a few seconds to a couple of minutes. Tools
that require the index reply with a `not_ready` status until indexing finishes.
Call the `status` tool to check progress.

## Connect it to an AI assistant

An MCP client launches this server as a subprocess over stdio. Most clients
accept a JSON configuration block similar to this:

```json
{
  "mcpServers": {
    "rust-lsp-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/rust-lsp-mcp", "rust-lsp-mcp"]
    }
  }
}
```

Replace `/absolute/path/to/rust-lsp-mcp` with the actual path to this
repository inside the dev container. This configuration shape is typical for MCP
clients such as Claude Desktop; the exact location of the config file depends on
the client you are using. The server must be run inside the dev container
environment where rust-analyzer and the Python dependencies are installed.

## Documentation

| Page | Description |
|------|-------------|
| [Documentation index](docs/guide/index.md) | Start here — an overview of all guide pages. |
| [Architecture](docs/guide/architecture.md) | How the pieces fit together and the ideas behind the design. |
| [Tools / API reference](docs/guide/tools.md) | Every tool, its inputs, and its responses. |
| [Configuration](docs/guide/configuration.md) | All settings and environment variables. |
| [Development setup](docs/guide/development.md) | The dev container, running the server, and the tests. |
| [Components](docs/guide/components.md) | A guided tour of the code, module by module. |
| [Dependencies](docs/guide/dependencies.md) | The main libraries and tools and what each is for. |

## Status / scope

This is a working prototype. It is read-only — it never modifies source code.
It currently targets the bundled ripgrep sample project that the dev container
clones automatically.
