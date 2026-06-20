[← Back to the README](../../README.md) · [Documentation index](index.md)

# Development setup

This page explains how to get the project running locally, how to run the tests, and how to use the code-quality tools. The development environment is entirely self-contained inside a Docker container — you do not need to install Rust, Python, or any other toolchain on your own machine.

---

## Prerequisites

You need three things on your own machine:

| Tool | Purpose |
|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Runs the container |
| [VS Code](https://code.visualstudio.com/) | The recommended editor |
| [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) | Opens the project inside the container |

Everything else — Rust, rust-analyzer, Python, the `uv` package manager, and all Python dependencies — is installed inside the container automatically.

---

## The development container

The container is defined in `.devcontainer/` (a `Dockerfile` plus `devcontainer.json`). Open the project in VS Code and choose **Reopen in Container** when prompted; VS Code builds the image and drops you into a fully configured environment.

### What is installed

**Base image:** Microsoft's Python 3.12 dev container image (`mcr.microsoft.com/devcontainers/python:3.12`).

**Added in the Dockerfile:** `uv` (version 0.7.13), a fast Python package and dependency manager from Astral, is copied in at a pinned version so the image is reproducible.

**Added via dev container features** (installed on top of the Dockerfile):

| Feature | What it provides |
|---|---|
| Rust toolchain | `rustup`, `cargo`, `rustfmt`, `clippy`, and `rust-src` |
| rust-analyzer | The code-intelligence engine the server talks to |
| GitHub CLI (`gh`) | For opening pull requests from inside the container |

**VS Code extensions installed automatically:**

| Extension | Purpose |
|---|---|
| Ruff (`charliermarsh.ruff`) | Linting, formatting, and import sorting for Python |
| ty (`astral-sh.ty`) | Type checking across the whole project |
| Python (`ms-python.python`) | General Python support |
| GitHub Copilot | AI completions |
| Claude Code (`Anthropic.claude-code`) | Claude Code CLI integration |

### Persistent storage (bind mounts)

Certain folders are stored *outside* the container, under `.devcontainer/cache/`, and mounted in at a fixed path when the container starts. This means large downloads happen only once — they survive container rebuilds and are not fetched again unless you run `teardown.sh`.

| Host folder (under `.devcontainer/cache/`) | Mounted path inside container | Purpose |
|---|---|---|
| `ripgrep-src/` | `/workspaces/ripgrep` | The pinned ripgrep source tree that the server navigates |
| `cargo-target/` | `/workspaces/cargo-target` | Rust build output (compiled ripgrep), reused between runs |
| `cargo-home/` | `/workspaces/cargo-home` | Downloaded Rust crates (the package registry cache) |
| `chroma-model-cache/` | `~/.cache/chroma` | The ~80 MB ONNX embedding model, downloaded once |
| `chroma/` | `/workspaces/chroma` | The documentation-search vector database |

### Automatic first-time setup

When the container is first created, VS Code runs `scripts/setup.sh` automatically (the `postCreateCommand`). You do not need to run it yourself; see the next section for what it does.

---

## Setup and the helper scripts

All scripts live in `scripts/` and are meant to be run from inside the container.

### `setup.sh` — first-time bootstrap (runs automatically)

Runs when the container is created. It is safe to run again at any time; every step is idempotent (it skips work that is already done).

Steps it performs:
1. Clones the pinned ripgrep fixture if it is not already present.
2. Creates a `.env` file from `env.sample` if one does not exist yet.
3. Installs Python dependencies (`uv sync`).
4. Disables git commit signing inside the container (the host signing key is not available in the container).

```bash
bash scripts/setup.sh
```

### `clone-ripgrep.sh` — fetch the sample Rust project

Shallow-clones ripgrep at the pinned version (14.1.1) into the bind-mounted source folder. Skips silently if the folder is already there.

```bash
bash scripts/clone-ripgrep.sh
```

### `init.sh` — create the `.env` file

Copies `env.sample` to `.env`. Skips if `.env` already exists; pass `--force` to overwrite.

```bash
bash scripts/init.sh          # safe: skips if .env exists
bash scripts/init.sh --force  # overwrites existing .env
```

### `teardown.sh` — full reset (destructive)

The only script that deletes things. It removes all bind-mount caches, the `.env`, the Python virtual environment, and build artifacts — giving you a clean slate. It waits three seconds before doing anything so you can press Ctrl-C to cancel.

```bash
bash scripts/teardown.sh
```

After teardown, run `setup.sh` again to restore the environment.

---

## Running the server

```bash
uv run rust-lsp-mcp
```

This is equivalent to `python -m rust_lsp_mcp`. The server communicates over standard input/output (stdio) and is normally launched by an MCP client rather than run directly. See the [README quick start](../../README.md) for how to wire it into a client, and the [Tools reference](tools.md) for the full list of available tools.

On the first run, the server indexes the ripgrep source tree and downloads the documentation-search model if it is not already cached. While that is happening, the `status` tool will report that the server is not yet ready. Call `status` again once indexing is complete.

---

## Running the tests

There are two kinds of tests and it matters which you run.

### Fast tests

Fast tests replace the live rust-analyzer and the documentation database with lightweight stand-ins, so they run in seconds with nothing external required. These are what the automated checks run.

```bash
uv run pytest -m "not integration"
```

### Integration tests

Integration tests run against the real rust-analyzer, the real ripgrep source tree, and the real documentation database. They are slower and are run on demand as a local quality gate. They are deliberately excluded from the automated cloud checks to stay within the free GitHub Actions usage quota.

```bash
uv run pytest -m integration
```

> **Note:** The VS Code Test panel (the beaker icon in the sidebar) is configured to run the fast tests only by default. The integration tests are never triggered accidentally from there.

---

## Code quality tools

Three tools keep the code clean. All are available via `uv run`.

### Ruff — lint and format

Ruff lints the code, formats it (replacing Black), and sorts imports. Format-on-save is enabled automatically by the included VS Code settings.

```bash
uv run ruff check .    # lint: report any issues
uv run ruff format .   # format: rewrite files in place
```

### ty — type checking

ty checks types across the entire project.

```bash
uv run ty check
```

---

## Automated checks (continuous integration)

On every push to `main` or a `phase*` branch, and on pull requests targeting `main`, a single cloud job runs on GitHub Actions. It:

1. Installs `uv` and Python 3.12.
2. Installs all dependencies (`uv sync`).
3. Lints with `ruff check`.
4. Checks formatting with `ruff format --check`.
5. Checks types with `ty check`.
6. Runs the fast tests (`pytest -m "not integration"`).
7. Verifies that the `env.sample` file accounts for every settings variable.

The job runs with no `.env` (server defaults only) and never runs the integration tests. Concurrent runs for the same branch are cancelled automatically to conserve the free usage quota.

---

## Optional: trying the tools by hand

VS Code tasks (run from the Command Palette → **Tasks: Run Task**) launch the **MCP Inspector**, a small tool for exercising the server's tools interactively without a full MCP client.

| Task | Mode | URL / notes |
|---|---|---|
| MCP Inspector (UI) | Web UI | Opens at http://localhost:6274 |
| MCP Inspector (CLI) | Headless command line | Output in the terminal panel |

Both tasks use `npx @modelcontextprotocol/inspector`, so **Node.js must be available inside the container**. Node.js is not part of the development container, so you must install it yourself before using these tasks. The tasks are optional and intended only for manual exploration.

---

## Related pages

- [Configuration](configuration.md) — every environment variable and setting, with defaults.
- [Dependencies](dependencies.md) — the main libraries and external tools the project relies on.
- [Components](components.md) — a module-by-module tour of the source code.
