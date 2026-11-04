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

> **Just connecting to an AI assistant?** Skip to
> [Connect it to an AI assistant](#connect-it-to-an-ai-assistant). That path
> uses a pre-built Docker image and does not require VS Code or the dev
> container.

This path is for **contributors and developers** modifying the server itself.

### Prerequisites
- [Docker](https://www.docker.com/get-started/)
- [VS Code](https://code.visualstudio.com/)
- [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)

### Step 1: Clone the repository
```bash
git clone https://github.com/raymondshum/rust-lsp-mcp.git
cd rust-lsp-mcp
```

### Step 2: Open in Dev Container
1. Open the repository folder in VS Code.
2. Select **Reopen in Container** when prompted (or open the Command Palette and run `Dev Containers: Reopen in Container`).

> **Automated setup**: The container automatically builds, installs dependencies via `uv`, and clones a sample Rust repository (**ripgrep** 14.1.1) for immediate testing.

### Step 3: Start the server
Run the server in the integrated VS Code terminal:
```bash
uv run rust-lsp-mcp
```
*(Equivalent command: `python -m rust_lsp_mcp`)*

> **Indexing note**: rust-analyzer indexes the sample project on startup. This takes a few seconds to two minutes. Tools return a `not_ready` envelope until indexing finishes. Call the `status` tool to check readiness.

## Connect it to an AI assistant

MCP clients launch this server as a subprocess over standard I/O (stdio). The server requires rust-analyzer, Python dependencies, and the Rust toolchain. Running the server inside Docker keeps your host system clean.

### Prerequisites
- [Docker](https://www.docker.com/get-started/) on your host machine.

### Step 1: Build the image
Build the container image from the repository root:
```bash
docker build -t rust-lsp-mcp .
```

### Step 2: Configure your MCP client
Add the server to your client configuration (such as Claude Desktop):

```json
{
  "mcpServers": {
    "rust-lsp-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/absolute/path/to/your/rust/project:/project:ro",
        "-e", "RLM_PROJECT_ID=<project-name>",
        "-v", "rlm-<project-name>-data:/data",
        "rust-lsp-mcp"
      ]
    }
  }
}
```

#### Configuration details
- `run -i --rm`: Creates a temporary container per session connected via stdio. The container removes itself on exit.
- `-v /absolute/path/to/your/rust/project:/project:ro`: Mounts your target Rust project read-only. Replace `/absolute/path/...` with your project's local path.
  - **SELinux hosts**: On systems enforcing SELinux (such as Fedora with rootless Podman), append `,z` to share the mount across containers: `-v /path:/project:ro,z`. Use lowercase `z` for shared access, not uppercase `Z`.
- `-e RLM_PROJECT_ID=<project-name>`: Names the project for the documentation index. Replace `<project-name>` with a short name for your project, such as `ripgrep`. Without it, the server derives a name from the project path, its git remote, and the package name in its root `Cargo.toml`. Every project is mounted at the same path (`/project`), so two different projects that share the git remote (or both have none) and have the same root package name (or both have none) would get the same derived name; setting `RLM_PROJECT_ID` keeps them apart.
- `-v rlm-<project-name>-data:/data`: Persists the ChromaDB documentation index and Cargo caches across restarts, in a volume named after the project. The text embedding model is pre-baked into the image.
- **More than one IDE window**: Two windows on different projects need different volume names, so give each project its own `rlm-<project-name>-data`. Two windows on the same project can share one volume; their first builds take turns, and the second window reuses the index the first one built. One limit remains for now: a `refresh` in one window still breaks documentation search in the other window on that shared volume, until it restarts ([KI-13](docs/impl/known-issues.md)).

> **Process lifecycle**: Each client session launches a fresh rust-analyzer process. While the `/data` volume preserves Cargo's build cache, the in-memory index rebuilds on each start. For a long-lived process across calls, see [CLI access (daemon mode)](#cli-access-for-agents-without-mcp).

### Network isolation (for untrusted code)

Rust build scripts (`build.rs`) and procedural macros run host code during indexing. When analyzing untrusted code, run the container without network access.

1. **Prime the cache once** (fetches dependencies without running build scripts):
   ```bash
   scripts/prime-cache.sh /absolute/path/to/your/rust/project rlm-<project-name>-data
   ```
   Pass the same volume name the isolated config below mounts; without the second argument the script primes the default `rust-lsp-mcp-data` volume instead.

2. **Run in isolated mode** (`--network none`):
   ```json
   {
     "mcpServers": {
       "rust-lsp-mcp": {
         "command": "docker",
         "args": [
           "run", "-i", "--rm", "--network", "none",
           "-e", "CARGO_NET_OFFLINE=true",
           "-v", "/absolute/path/to/your/rust/project:/project:ro",
           "-e", "RLM_PROJECT_ID=<project-name>",
           "-v", "rlm-<project-name>-data:/data",
           "rust-lsp-mcp"
         ]
       }
     }
   }
   ```

For detailed security threat models and proxy configurations, see [Privacy & egress audit](docs/security/privacy-egress-audit.md).

### Guiding AI agents

AI assistants often default to file grep rather than semantic navigation. This repository includes a drop-in skill: [`rust-code-navigation`](.claude/skills/rust-code-navigation/SKILL.md).

For clients supporting skills (such as Claude Code), copy that file into your project's `.claude/skills/rust-code-navigation/SKILL.md`. It teaches the agent when to use definitions, references, and hover tools.

## CLI access (for agents without MCP)

Some agents can only run shell commands. For those environments, run the server as a persistent **daemon** and use the companion `rust-lsp` CLI client.

### Step 1: Start the daemon
Run this command from the repository root, pointing `RUST_PROJECT` to your Rust project:

```bash
RUST_PROJECT=/absolute/path/to/your/rust/project docker compose up -d rust-lsp-mcp
```
*(For the network-isolated variant, use service `rust-lsp-mcp-isolated`)*

The daemon listens on `127.0.0.1` inside the container. It does not publish host ports. This loopback constraint protects the service from unauthorized network access.

### Step 2: Run CLI commands
Execute commands via `docker exec` (or `podman exec`). Specify the full executable path `/app/.venv/bin/rust-lsp`:

```bash
# Check daemon status and wait for indexing (up to 180 seconds)
docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp --wait 180 status

# Search for a symbol
docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp find-symbol MyStruct

# Inspect client and daemon versions
docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp version
```

### Exit codes
Subcommands output JSON to stdout and return explicit exit codes:

| Exit code | Meaning | Action |
|---|---|---|
| `0` | Success (`ok` or `not_found`) | Result received. Empty results are valid answers. |
| `1` | Error (`error` envelope) | Bad input or server error. Check JSON `message`. |
| `2` | Not ready (`not_ready`) | Indexing in progress. Retry or pass `--wait`. |
| `3` | Connection failure | Daemon unreachable. Check if the container is running. |

For complete subcommands, options, and troubleshooting, see the [CLI reference](docs/guide/cli.md).

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
| [Privacy & egress audit](docs/security/privacy-egress-audit.md) | What code runs during indexing, what it can reach, and how to isolate it. |
| [Known issues](docs/impl/known-issues.md) | Open design/doc issues and troubleshooting notes (SELinux mounts, `refresh` scope, benign log noise). |

## Status / scope

This is a working prototype. It is read-only — it never modifies source code.
The server is repo-agnostic: point it at any Rust project via a read-only bind
mount (production image) or explore the dev container's bundled ripgrep sample
out of the box.

## License

Released under the [MIT License](LICENSE).
