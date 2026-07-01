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

> **Just want to wire the server into an AI assistant?** Skip to
> [Connect it to an AI assistant](#connect-it-to-an-ai-assistant) — that path
> uses a pre-built Docker image and does not require VS Code or the dev
> container.

This path is for **contributors and developers** who want to work on the server
itself inside a fully configured environment.

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
   preconfigured environment built with Docker — builds automatically. After
   the container is created, a setup script clones **ripgrep** version 14.1.1
   (a popular command-line search tool) as a sample Rust project — so you have
   something to explore out of the box — and installs all Python dependencies
   using the `uv` package manager.

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

An MCP client launches this server as a subprocess over stdio. The server needs
rust-analyzer, the Python dependencies, and the full Rust toolchain — all of
which live **inside a container**, not on your host. So rather than asking the
client to run `uv` directly (which only works from *inside* the dev container),
you build a self-contained image once and have the client launch it with
`docker run`. This keeps your host clean and works for host-side clients like
Claude Desktop.

**1. Build the image** (once, from this repository):

```
docker build -t rust-lsp-mcp .
```

**2. Point your MCP client at the image.** Most clients accept a JSON
configuration block similar to this:

```json
{
  "mcpServers": {
    "rust-lsp-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/absolute/path/to/your/rust/project:/project:ro",
        "-v", "rust-lsp-mcp-data:/data",
        "rust-lsp-mcp"
      ]
    }
  }
}
```

What the pieces do:

- `run -i --rm` — start a fresh container per session, attached over stdio
  (`-i`), and remove it when the session ends (`--rm`). No long-running
  container to manage.
- `-v /absolute/path/to/your/rust/project:/project:ro` — **bind-mount the Rust
  project you want to explore**, read-only, at the path the server expects
  (`/project`). Replace the left side with your project's absolute path. The
  server is repo-agnostic — point it at any Rust project.
  - **SELinux (rootless Podman):** if your host enforces SELinux with rootless
    Podman, add a relabel suffix so the container can read the mount:
    `-v /absolute/path/to/your/rust/project:/project:ro,Z`. Plain `:ro` is
    correct for a standard Docker daemon.
- `-v rust-lsp-mcp-data:/data` — a **named volume** for the documentation index
  and the Rust build/dependency cache, so they are built once and reused across
  sessions ("download once"). The embedding model is **baked into the image** at
  build time, so it needs no download at runtime.

The exact location of the config file depends on the client you are using; this
shape is typical for clients such as Claude Desktop.

**Note on startup:** each session starts a fresh rust-analyzer process, which
re-indexes the project (seconds to a couple of minutes — the build cache on the
`/data` volume keeps the underlying `cargo check` incremental, but the in-memory
index is rebuilt each time). If you want rust-analyzer to stay hot between
sessions, keep one container running and use `docker exec` instead — see
[`docker-compose.yml`](docker-compose.yml).

### Network isolation (recommended for untrusted code)

Indexing a Rust project runs **untrusted code from that project on your host**:
rust-analyzer compiles and executes the project's `build.rs` build scripts and
proc-macros (this is how rust-analyzer works, not a flaw here). Combined with an
un-isolated container, that code has read access to everything under `/project`
and unrestricted outbound network — so a malicious project (or a malicious
transitive crate) could exfiltrate your source. See
[`docs/security/privacy-egress-audit.md`](docs/security/privacy-egress-audit.md)
for the full analysis; no dependency of this server transmits your code on its
own, but this build-script vector is real.

The fix is to run the server with **no network**. Because the embedding model is
baked into the image, the only remaining runtime network use is cargo fetching
the scanned project's crates.io dependencies — which you warm **once** up front:

**Default: warm the cache once, then run isolated (zero degradation).**

1. **Prime** the dependency cache (downloads dependency *sources* only — runs no
   build scripts, so this network-on step executes no project code):

   ```
   scripts/prime-cache.sh /absolute/path/to/your/rust/project
   ```

   (equivalently: `docker run --rm -v /abs/project:/project -v rust-lsp-mcp-data:/data
   --entrypoint cargo rust-lsp-mcp fetch --manifest-path /project/Cargo.toml`.
   Note the project is mounted **read-write** here so `cargo` can write
   `Cargo.lock` if the project doesn't commit one; this step runs no project code.)

2. **Point your MCP client at the isolated config** — `--network none` plus
   `CARGO_NET_OFFLINE=true`, with the project back to `:ro`:

   ```json
   {
     "mcpServers": {
       "rust-lsp-mcp": {
         "command": "docker",
         "args": [
           "run", "-i", "--rm", "--network", "none",
           "-e", "CARGO_NET_OFFLINE=true",
           "-v", "/absolute/path/to/your/rust/project:/project:ro",
           "-v", "rust-lsp-mcp-data:/data",
           "rust-lsp-mcp"
         ]
       }
     }
   }
   ```

   With the cache warmed, rust-analyzer compiles proc-macros and runs build
   scripts **from the cached sources, offline** — full analysis, and the executed
   code has no network to exfiltrate through.

**Alternatives:**

- **Maximum caution (skip priming):** run the isolated config from the very first
  index. rust-analyzer still resolves the standard library and all first-party
  code, but resolution *into un-cached external crates* is degraded (missing
  hover/goto for dependency types, and proc-macro-generated items won't appear)
  until the cache is warmed. `search_docs` and `document_symbols` are unaffected.
- **Convenience over isolation:** use the plain config above (no `--network none`)
  — full analysis with no priming step, but no egress protection. Only appropriate
  for projects you trust.
- **Allowlist proxy:** if you must let cargo reach the network during analysis,
  route egress through a proxy that permits only `static.crates.io` (and denies
  arbitrary hosts) rather than opening the network wholesale.

**Help your agent know when to use the server.** Wiring in the tools is not
enough — an agent will often grep a Rust repo and never think to reach for the
LSP. This repo ships a drop-in skill,
[`rust-code-navigation`](.bob/skills/rust-code-navigation/SKILL.md), that
routes an agent toward semantic navigation (definitions, references, types)
instead of text search, with a per-tool intent map and the common gotchas
(`not_ready` while indexing, 1-based positions, empty-result-is-not-an-error).
For a client that loads skills — such as IBM Bob — copy that `SKILL.md`
into your own project's `.bob/skills/rust-code-navigation/` so it travels
with the repo you are exploring.

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
| [Agentic coding](docs/guide/agentic-coding.md) | How the project is built with IBM Bob — the delivery lifecycle, build conventions, and Bob configuration. |
| [Agent skill: `rust-code-navigation`](.bob/skills/rust-code-navigation/SKILL.md) | A skill that helps an AI agent decide *when* to reach for this server — routing it toward semantic navigation instead of grep, with a per-tool intent map and gotchas. |

## Status / scope

This is a working prototype. It is read-only — it never modifies source code.
The server is repo-agnostic: point it at any Rust project via a read-only bind
mount (production image) or explore the dev container's bundled ripgrep sample
out of the box.

## License

Released under the [MIT License](LICENSE).
