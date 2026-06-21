[← Back to the README](../../README.md) · [Documentation index](index.md)

# Dependencies

This page lists every library and external tool the project depends on, what each one does, and any version-sensitivity worth knowing before you upgrade.

---

## Main libraries (runtime)

| Library | Version | What it does |
|---|---|---|
| **mcp** | 1.12.4 | The official Python toolkit for the Model Context Protocol — provides the FastMCP server and the standard-input/output connection the AI assistant uses to call tools. |
| **multilspy** | 0.0.15 | Talks to rust-analyzer using the Language Server Protocol so the server can answer code-navigation questions. |
| **chromadb** | 1.5.9 | A local database for meaning-based search — stores documentation pieces and retrieves the ones closest in meaning to a question. |
| **pydantic-settings** | 2.x | The configuration layer — reads settings from defaults, a `.env` file, and environment variables. |

### mcp

The Model Context Protocol (MCP) is the standard that lets AI assistants call external tools. The `mcp` package provides everything needed to build a server: the `FastMCP` class that registers tools and handles the protocol, and the stdio transport that routes messages over standard input/output. The AI assistant connects to the server through that transport and calls tools by name.

### multilspy

Language Server Protocol (LSP) is the standard editors use to get code intelligence — go-to-definition, find-references, hover, and similar features. `multilspy` acts as an LSP client: it starts rust-analyzer as a subprocess and sends it requests using the protocol.

The project pins this exact version and uses a small custom subclass so it runs the container's own rust-analyzer program instead of downloading one. The built-in download list inside multilspy lacks a compatible build for this environment, so the subclass overrides that path. Because the code depends on version-specific behavior, upgrading multilspy should be done carefully — read the changelog and re-verify the subclass before bumping the pin.

### chromadb

ChromaDB is a small local vector database — a database that finds documents by meaning rather than by exact keywords. The server splits documentation files into pieces, encodes each piece as a meaning-vector, and stores those vectors in ChromaDB. When a question arrives, ChromaDB finds the pieces whose meaning is closest to the question's meaning.

The **full** package (not the slimmed-down client-only build) is required because it bundles `all-MiniLM-L6-v2`, the sentence-embedding model that does the encoding. That model runs locally on the CPU and needs no account or API key. It downloads once (about 80 MB) to a cached folder on first use and is reused on every subsequent run.

### pydantic-settings

`pydantic-settings` is the configuration layer for the project. It reads settings in order of priority: built-in defaults, a `.env` file in the project root, and finally environment variables. Every configurable value — repository path, ChromaDB path, embedding-model cache directory — flows through this layer. See the [Configuration](configuration.md) page for the full list of settings and their defaults.

---

## Developer tools

These packages are used during development only; they are not required to run the server.

| Tool | What it does |
|---|---|
| **pytest** | Runs the tests — both the fast unit tests and the slower integration tests. |
| **ruff** | Lints and formats the code, and sorts imports. One tool replacing several older ones. |
| **ty** | Checks types across the project. |

---

## External dependencies (not Python packages)

These are programs the server relies on at runtime. They are not installed by `pip`. Both supported environments provide them: the development container (via its Rust dev-container feature) and the production Docker image (which bakes the full Rust toolchain in via `rustup` — see the [`Dockerfile`](../../Dockerfile)). You do not install them on your host.

**rust-analyzer** is the code-intelligence engine for Rust. It reads and understands Rust source code so the server can answer questions like "where is this symbol defined" and "what are all the references to it." It is supplied by the Rust toolchain (rustup + cargo + rustc + rust-src) in either environment, and the server runs it directly as a subprocess.

**The target Rust project** is whatever you point the server at — it is repo-agnostic. Its source code is what the LSP tools explore, and its Markdown files are what the documentation-search tool indexes; it is treated as read-only and is not a dependency of the server's own code. You supply it via `RLM_PROJECT_ROOT` (a read-only bind mount at `/project` in the production image). **ripgrep (version 14.1.1 source)** is just the convenience sample the development container clones on setup so there is something to explore out of the box; the production image bakes no project.

**Docker** is the one host-side requirement for the production launch path: a host MCP client runs the server as `docker run -i` over stdio, so nothing but Docker needs to exist on the host. (Contributors using the dev container additionally need VS Code and the Dev Containers extension — see [Development setup](development.md).)

---

## A note on versions

The main runtime libraries are pinned to exact versions for predictable behavior. This matters most for `multilspy`, whose version-specific quirks the code actively depends on, but applies to the others too: exact pins prevent unexpected breakage when packages release updates. The full resolved dependency tree — including transitive dependencies — lives in `uv.lock`.

---

## Related pages

- [Architecture](architecture.md) — how a request flows through the system and why these pieces fit together the way they do.
- [Development setup](development.md) — how to install the dependencies and get the server running.
