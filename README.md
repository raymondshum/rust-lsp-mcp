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
    `-v /absolute/path/to/your/rust/project:/project:ro,z`. Plain `:ro` is
    correct for a standard Docker daemon.
    - **Shared (`z`) vs. private (`Z`):** use the lowercase, SHARED label `z`,
      not the uppercase, private `Z` — this matches `scripts/prime-cache.sh`,
      which mounts the same project tree with `:z`. The private `Z` relabels
      the source tree with a category scoped to one container, which then
      denies access to any OTHER container or tool (including a later
      `prime-cache.sh` run, or `docker`/`podman` used interchangeably) that
      mounts the same directory — the exact cross-container relabel conflict
      `prime-cache.sh` warns about. The shared `z` label is compatible across
      every container that mounts the same source tree, which is the case
      here.
- `-v rust-lsp-mcp-data:/data` — a **named volume** for the documentation index
  and the Rust build/dependency cache, so they are built once and reused across
  sessions ("download once"). The embedding model is **baked into the image** at
  build time, so it needs no download at runtime.

The exact location of the config file depends on the client you are using; this
shape is typical for clients such as Claude Desktop.

**Note on startup:** each session starts a fresh rust-analyzer process, which
re-indexes the project (seconds to a couple of minutes — the build cache on the
`/data` volume keeps the underlying `cargo check` incremental, but the in-memory
index is rebuilt each time). If you want rust-analyzer to stay hot across many
calls, see [CLI access](#cli-access-for-agents-without-mcp) below — that's a
different launch shape (a long-lived daemon), not another MCP-client config.

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

1. **Prime** the dependency cache. `cargo fetch` downloads dependency *sources*
   only — it runs no build scripts and expands no proc-macros, so unlike the
   isolated run it executes no *compiled* project code:

   ```
   scripts/prime-cache.sh /absolute/path/to/your/rust/project
   ```

   (equivalently: `docker run --rm -v /abs/project:/project -v rust-lsp-mcp-data:/data
   --entrypoint cargo rust-lsp-mcp fetch --manifest-path /project/Cargo.toml`.
   The project is mounted **read-write** here so `cargo` can write `Cargo.lock`
   if the project doesn't commit one. `scripts/prime-cache.sh` auto-detects
   `docker` or `podman`; override with `CONTAINER_ENGINE=…`.)

   > **Caveat — priming still makes network calls the project controls.** This
   > step is network-*on*, and `cargo fetch` resolves and downloads every
   > registry **and git** dependency the project declares — so a malicious
   > `Cargo.toml` can cause outbound connections to attacker-chosen hosts (e.g. a
   > `git = "https://attacker/…"` dependency) and check out untrusted repos into
   > the cache. It does not *execute build scripts*, but it is not a hermetic
   > step. Only prime projects whose dependency set you would already let cargo
   > resolve; for fully untrusted code, prime behind the allowlist proxy below or
   > skip priming and accept the degraded first-index (see alternatives).

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
[`rust-code-navigation`](.claude/skills/rust-code-navigation/SKILL.md), that
routes an agent toward semantic navigation (definitions, references, types)
instead of text search, with a per-tool intent map and the common gotchas
(`not_ready` while indexing, 1-based positions, empty-result-is-not-an-error).
For a client that loads skills — such as Claude Code — copy that `SKILL.md`
into your own project's `.claude/skills/rust-code-navigation/` so it travels
with the repo you are exploring.

## CLI access (for agents without MCP)

The two paths above assume your AI assistant can call MCP tools directly. Some
agents — or subagents that don't inherit their parent's MCP tool access — can
only run shell commands. For those, this repo ships a third launch shape: a
long-lived **daemon** container plus a `rust-lsp` command-line client that
talks to it, so a shell-only agent gets the same read-only navigation and doc
search as an MCP client, just invoked as a subprocess instead of a tool call.

This replaces the old "warm-start" `docker exec -i … /app/.venv/bin/rust-lsp-mcp`
pattern some earlier versions of this README described. That pattern started a
second **stdio server** inside a long-lived container; it's gone because
running two servers against one Chroma store is an unguarded cross-process
write hazard (see [known issue KI-13](docs/impl/known-issues.md#ki-13--chromadb-cross-process-single-writer-hazard-on-a-shared-data-volume)).
The daemon replaces it with exactly **one** server process per container,
reached through a client (`rust-lsp`), not a second server.

**1. Start the daemon** (once; it stays warm across calls). Run this from
this repository's own directory — `docker compose` reads `docker-compose.yml`
relative to the current directory — with `RUST_PROJECT` set to the absolute
host path of the Rust project you want to analyze:

```
RUST_PROJECT=/absolute/path/to/your/rust/project docker compose up -d rust-lsp-mcp
```

This builds the image if needed and starts `rust-lsp-mcp` in streamable-HTTP
mode, listening on `127.0.0.1` **inside the container only** — see the
security note below. Use `rust-lsp-mcp-isolated` instead for the no-network
variant (same cache-priming caveats as [Network isolation](#network-isolation-recommended-for-untrusted-code)
above).

**2. Run commands against it** with `docker exec` (or `podman exec` —
identical usage). Use the **full path** `/app/.venv/bin/rust-lsp` — the
image's `PATH` only adds `/usr/local/cargo/bin`, not `/app/.venv/bin`, so a
bare `rust-lsp` is "command not found" here:

```
# First call after starting the daemon: ride out indexing (up to 180s).
# --wait must come BEFORE the subcommand.
docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp --wait 180 status

# Look up a symbol
docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp find-symbol MyStruct

# Client (and, if reachable, daemon) version info
docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp version
```

Every command prints a JSON envelope to stdout (`{"status": ..., ...}`,
matching the [Tools / API reference](docs/guide/tools.md)) and exits with a
code that tells a shell script or agent what happened, without needing to
parse the JSON to decide:

| Exit code | Meaning |
|---|---|
| `0` | `ok` or `not_found` — the daemon answered (an empty/negative result is still an answer, not a failure). |
| `1` | `error` envelope — bad input or an internal failure; see `message` in the JSON. |
| `2` | `not_ready` — the daemon is reachable but still indexing (or a `--wait` window expired while it was still not ready). |
| `3` | The daemon could not be reached at all (not started yet, wrong port, or a `--wait` window expired without ever connecting). |

Each subprocess call is fast once the daemon is warm — a `status` call
measured well under a second (about 0.9s wall-clock, including process
startup) in local testing — plus a one-time indexing wait the first time the
project is analyzed (`--wait` rides through that automatically). Full
subcommand reference, `--wait` semantics, and troubleshooting:
[docs/guide/cli.md](docs/guide/cli.md).

**Security note (loopback-only, not authentication):** the daemon's HTTP
listener binds to `127.0.0.1` and is hard-coded that way — it is not
configurable and this compose file never publishes it with a `ports:` mapping.
That listener has no authentication of its own; loopback-only is the entire
protection. This matters because the same container also executes **untrusted
code from the Rust project it's pointed at** — rust-analyzer runs that
project's `build.rs` build scripts and proc-macros to produce its index (see
[Network isolation](#network-isolation-recommended-for-untrusted-code) above).
Keeping the listener off any published port means that code (or anything else
on the network) cannot reach the MCP tools, which are read-only regardless.
Never add a `ports:` mapping to `docker-compose.yml` for either service.

## Documentation

| Page | Description |
|------|-------------|
| [Documentation index](docs/guide/index.md) | Start here — an overview of all guide pages. |
| [Architecture](docs/guide/architecture.md) | How the pieces fit together and the ideas behind the design. |
| [Tools / API reference](docs/guide/tools.md) | Every tool, its inputs, and its responses. |
| [Configuration](docs/guide/configuration.md) | All settings and environment variables. |
| [CLI reference](docs/guide/cli.md) | The `rust-lsp` command-line client: subcommands, exit codes, `--wait`, and troubleshooting. |
| [Development setup](docs/guide/development.md) | The dev container, running the server, and the tests. |
| [Components](docs/guide/components.md) | A guided tour of the code, module by module. |
| [Dependencies](docs/guide/dependencies.md) | The main libraries and tools and what each is for. |
| [Agentic coding](docs/guide/agentic-coding.md) | How the project is built with Claude Code — the delivery lifecycle, build conventions, and Claude configuration. |
| [Agent skill: `rust-code-navigation`](.claude/skills/rust-code-navigation/SKILL.md) | A skill that helps an AI agent decide *when* to reach for this server — routing it toward semantic navigation instead of grep, with a per-tool intent map and gotchas. |

## Status / scope

This is a working prototype. It is read-only — it never modifies source code.
The server is repo-agnostic: point it at any Rust project via a read-only bind
mount (production image) or explore the dev container's bundled ripgrep sample
out of the box.

## License

Released under the [MIT License](LICENSE).
