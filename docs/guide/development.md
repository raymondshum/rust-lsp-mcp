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
| `ripgrep-src/` | `/workspaces/ripgrep` | The pinned ripgrep sample project (dev container only) |
| `cargo-target/` | `/workspaces/cargo-target` | Rust build output, reused between runs |
| `cargo-home/` | `/workspaces/cargo-home` | Downloaded Rust crates (the package registry cache) |
| `chroma-model-cache/` | `~/.cache/chroma` | The ~80 MB ONNX embedding model, downloaded once |
| `chroma/` | `/workspaces/chroma` | The documentation-search vector database |

> **Production image:** the same categories of persistent data live on a single named Docker volume mounted at `/data`, rather than these `.devcontainer/cache/` bind mounts. You choose the volume name in the `docker run` command (the examples below use `rust-lsp-mcp-data`); the `docker-compose.yml` warm-start path uses its own volume (`rlm-data`), so the two launch methods keep separate caches.

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

There are two ways to run the server, depending on your goal.

### Inside the dev container (contributor / dev path)

```bash
uv run rust-lsp-mcp
```

This is equivalent to `python -m rust_lsp_mcp`. The server communicates over standard input/output (stdio) and is normally launched by an MCP client rather than run directly. See the [README quick start](../../README.md) for how to wire it into a client, and the [Tools reference](tools.md) for the full list of available tools.

On the first run, the server indexes the configured project root (in the dev container this is the ripgrep sample at `/workspaces/ripgrep`) and downloads the documentation-search model if it is not already cached. While that is happening, the `status` tool will report that the server is not yet ready. Call `status` again once indexing is complete.

### Running the production image (host MCP client, no host install)

If you want to point an MCP client on your host machine at any Rust project — without installing Rust, Python, or rust-analyzer on the host — build and run the production image defined in [`../../Dockerfile`](../../Dockerfile).

**Build once:**

```bash
docker build -t rust-lsp-mcp .
```

**Run per session:**

```bash
docker run -i --rm \
  -v /abs/path/to/your/rust/project:/project:ro \
  -v rust-lsp-mcp-data:/data \
  rust-lsp-mcp
```

- `-v /abs/path/to/your/rust/project:/project:ro` — bind-mounts your Rust project read-only at `/project`. The server navigates whatever project you mount here; it is not tied to ripgrep or any other specific codebase.
- `-v rust-lsp-mcp-data:/data` — a named volume that persists the Chroma vector store, cargo registry, and build cache across `--rm` runs.
- `-i` — keeps stdin open so the MCP client can communicate over stdio.

> **SELinux note:** Under SELinux-enforcing rootless Podman, append `,Z` to the bind mount: `-v /abs/path/to/your/project:/project:ro,Z`. Plain `:ro` is correct for a standard Docker daemon.

The env-var defaults baked into the image (`RLM_PROJECT_ROOT=/project`, `RLM_CHROMA_PATH=/data/chroma`, `RLM_DOC_COLLECTION=project_docs`, and the cargo-cache paths) work out of the box for this invocation. See [Configuration](configuration.md) to override them.

**Staying hot across many calls (the CLI daemon):** MCP clients stay on the ephemeral `docker run -i --rm` invocation above — that is the supported MCP path. If you want rust-analyzer to stay warm across many calls, the answer is the **CLI daemon**: [`../../docker-compose.yml`](../../docker-compose.yml) runs one long-lived server in streamable-HTTP mode, and the `rust-lsp` command-line client talks to it. Start the daemon naming the service explicitly (`RUST_PROJECT=/abs/path docker compose up -d rust-lsp-mcp` — a bare `up -d` would also start the isolated variant, putting two servers on one volume), then run client commands with the full path: `docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp <cmd>`. See the [CLI reference](cli.md) and the README ["CLI access"](../../README.md#cli-access-for-agents-without-mcp) section. An earlier revision of this page taught pointing an MCP client at `docker exec -i rust-lsp-mcp /app/.venv/bin/rust-lsp-mcp` — that pattern is retired: it starts a second stdio *server* inside the daemon's container, an unguarded cross-process writer against the shared Chroma store (see [known issue KI-13](../impl/known-issues.md#ki-13--chromadb-cross-process-single-writer-hazard-on-a-shared-data-volume)).

---

## Running the tests

There are two kinds of tests and it matters which you run.

### Fast tests

Fast tests replace the live rust-analyzer and the documentation database with lightweight stand-ins, so they run in seconds with nothing external required. These are what the automated checks run.

```bash
uv run pytest -m "not integration"
```

### Integration tests

Integration tests run against the real rust-analyzer, the real project root (the ripgrep sample in the dev container), and the real documentation database. They are slower and are run on demand as a local quality gate. They are deliberately excluded from the automated cloud checks to stay within the free GitHub Actions usage quota.

```bash
uv run pytest -m integration
```

**When to run it.** CI only runs the fast tier, so the integration gate is *your*
responsibility before merging. Run it locally for any change that touches the
LSP / position / doc-store / settings surface — in practice when you edit any of:

- `src/rust_lsp_mcp/analyzer.py` (rust-analyzer lifecycle, LSP delegates, the
  initialize params / position-encoding negotiation),
- `src/rust_lsp_mcp/positions.py` (the 1↔0-index / encoding boundary),
- `src/rust_lsp_mcp/doc_store.py` or `doc_chunking.py` (the Chroma index and chunker),
- `src/rust_lsp_mcp/settings.py` or the tool layer in `src/rust_lsp_mcp/tools/`.

The fast tier uses a faked analyzer and an in-memory store, so it cannot catch a
regression in how the *live* analyzer or the *real* embedding model behaves —
only this gate can. It takes roughly ten minutes (several cold rust-analyzer
starts plus the embedding model).

> **Note:** The VS Code Test panel (the beaker icon in the sidebar) is configured to run the fast tests only by default. The integration tests are never triggered accidentally from there.

### Production-image smoke gate

There is a third, separate tier that neither the fast nor the integration
tests exercise: the *production image itself*, built from the real
`Dockerfile` at the repo root and run as a black box, the way a host MCP
client actually launches it. It is opt-in and local-only:

```bash
RLM_IMAGE_SMOKE=1 uv run --frozen pytest -m image
```

Without `RLM_IMAGE_SMOKE=1` (or without podman/docker on `PATH`), every test
in `tests/test_image_smoke.py` skips instantly with a clear reason — it never
runs as part of the fast tier, the integration tier, or CI.

**What it proves**, by building the image fresh (tagged `rust-lsp-mcp:smoke`
— a pre-existing local `:latest` is never trusted) and running it as a real
container:

- **DS-16** — the target project is always a host-owned bind mount and the
  image runs as root, so git's "dubious ownership" protection would
  otherwise leave `indexed_commit`/`current_commit`/`stale` permanently
  `null`. The gate forces a genuinely *foreign-owned* bind mount and checks
  that the Dockerfile's baked `git config --system --add safe.directory
  /project` line rescues `git -C /project rev-parse HEAD`.
- **DS-25** — the embedding model is baked into the image at build time so
  the documentation index can build with zero network access. The gate runs
  the real embedding function inside the container under `--network none`
  and checks it returns a real (384-dim) embedding without touching the
  network.
- An **end-to-end `status` call over real MCP stdio** — the same
  `podman/docker run --rm -i --network none ...` invocation a host client
  would use, driven with the actual `mcp` client library (via `anyio`, since
  this repo has no `pytest-asyncio`), asserting the full stack comes up
  cleanly offline.

Each of the two container-level checks (DS-16, DS-25) includes an in-test
*mutation* alongside its positive assertion — e.g. unsetting `safe.directory`
before re-running `rev-parse`, or redirecting `HOME` before re-running the
embedding call under `--network none` — that must independently fail with
the real, specific error (git's "dubious ownership" message; a
connect/DNS-resolution error). This is what makes each positive assertion
falsifiable rather than true by construction.

> **Rootless Podman fidelity note:** on this host's rootless Podman, the
> default user namespace maps container uid 0 to the invoking host user, so
> an *ordinary* bind mount is not actually foreign from the container's
> point of view — the DS-16 check would be vacuous without deliberately
> re-owning the fixture repo first (via `podman unshare chown`). Rootful
> Docker mounts are natively foreign (no uid remapping), so this step is a
> no-op there — Docker gets DS-16 fidelity for free.

**Cost.** A cold build (first run on a host, or after a toolchain/dependency
bump) takes several minutes — the full Rust toolchain via rustup, `uv sync`,
and the ~80 MB embedding model download all happen from scratch. A warm
build (no relevant Dockerfile/`src`/lockfile changes since the last build)
reuses layer cache and the whole gate — build plus all three tests — finishes
in well under a minute.

**Deliberately not in CI.** Building a full production image on every push
would burn through the free GitHub Actions quota for a check that only
matters immediately before a release; this gate is a local pre-release
responsibility, not an automated one — there is no corresponding CI workflow
file, by design.

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

Both tasks use `npx @modelcontextprotocol/inspector`, which needs Node.js. The development container includes Node.js (the `node` dev-container feature), so the tasks work out of the box — no manual install. They are optional and intended only for manual exploration.

---

## Related pages

- [Configuration](configuration.md) — every environment variable and setting, with defaults.
- [Dependencies](dependencies.md) — the main libraries and external tools the project relies on.
- [Components](components.md) — a module-by-module tour of the source code.
