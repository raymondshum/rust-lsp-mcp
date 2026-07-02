[← Back to the README](../../README.md) · [Documentation index](index.md)

# Configuration reference

## How settings work

All settings have sensible defaults built into the code, so the server runs with no
configuration at all. The defaults point at the standard locations inside the
development container, which means you can start the server immediately after
container setup without creating any files.

You can override any setting in two ways:

- **`.env` file** — create a file named `.env` in the project root. A template named
  `env.sample` is provided; copy it to `.env` and edit the values you want to change.
  The development container does this copy automatically on first start.
- **Environment variable** — set a variable in the shell before starting the server.

Order of precedence, strongest last:

> built-in defaults &lt; `.env` file &lt; environment variables

Every setting's environment-variable name is the setting name written in capitals with
the prefix `RLM_`. For example, the setting `chroma_path` is set with `RLM_CHROMA_PATH`.

The server loads the `.env` file itself at startup — no external loader or shell
sourcing step is needed. Settings are handled by the
[`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
library.

---

## All settings

| Environment variable | Default | What it does |
|---|---|---|
| `RLM_PROJECT_ROOT` | `/workspaces/ripgrep` | Folder of the Rust project to navigate and whose Markdown files are searched. Repo-agnostic — point it at any Rust project. The default is the bundled ripgrep sample inside the container. *(The old name `RLM_RIPGREP_SRC` still works as a deprecated alias and emits a warning.)* |
| `RLM_DOC_COLLECTION` | `project_docs` | Name of the ChromaDB collection that holds the documentation index. Change it only if you keep multiple projects' indexes under one `RLM_CHROMA_PATH`. |
| `RLM_RUST_ANALYZER_BIN` | `/usr/local/cargo/bin/rust-analyzer` | Path to the rust-analyzer program inside the container. You can confirm the correct path with `rustup which rust-analyzer`. |
| `RLM_CHROMA_PATH` | `/workspaces/chroma` | Folder where the documentation search database is stored. Kept on a persistent mount so the index survives container rebuilds. |
| `RLM_DOC_GLOB_PATTERNS` | `**/*.md` | Which Markdown files to include in documentation search, written as comma-separated path patterns relative to the project folder. The default includes every Markdown file anywhere in the project. |
| `RLM_DOC_EXCLUDE_PATTERNS` | `**/CHANGELOG.md` | Which files to exclude even if they matched the include patterns above. The default leaves out `CHANGELOG.md`, whose long list of version-by-version change notes would otherwise crowd out more useful documentation in search results. |

---

## A note on persistence ("download once")

Several of the folders listed above live on **persistent mounts** — storage that is
attached to the container but lives outside it, so it survives a full container
rebuild. This matters for two expensive one-time operations:

- **The documentation-search embedding model** (about 80 MB) is stored under the
  container user's home cache folder (`~/.cache/chroma`) — this path is fixed and
  not configurable. What happens there differs by deployment:
  - **Dev container** — the model is downloaded the first time the documentation
    index is built and then read from the local cache on every subsequent start.
    Persist `~/.cache/chroma` via a bind mount so it is not re-downloaded after a
    rebuild.
  - **Production image** — the model is baked into the image at build time (under
    the image's own `HOME`, a non-volume path), so there is no runtime download
    and nothing to persist on the `/data` volume. Persisting `~/.cache/chroma` on
    `/data` would be a no-op: the baked copy is what the server actually reads.
- **Rust build output** (potentially hundreds of megabytes) is compiled once and
  reused. It is relocated to a persistent mount via the container-level
  `CARGO_TARGET_DIR` and `CARGO_HOME` environment variables — set by the dev
  container's `containerEnv` and by the production image's `Dockerfile` — not by
  any `RLM_`-prefixed setting.

Neither is re-fetched or recompiled unless you delete the mount. For details on how
the mounts are configured, see the [Development setup](development.md) page.

---

## Example `.env`

The following example points the server at a different Rust project and limits
documentation search to files under a `docs/` folder:

```dotenv
# Point at your own Rust project instead of the bundled ripgrep sample
RLM_PROJECT_ROOT=/home/user/projects/my-rust-app

# Only index Markdown files that live under a docs/ directory
RLM_DOC_GLOB_PATTERNS=docs/**/*.md
```

With these two lines in `.env`, the LSP navigation tools will operate on
`my-rust-app` and documentation search will only surface files from that project's
`docs/` subtree, ignoring any stray Markdown files at the root level.

All other settings continue to use their built-in defaults.

---

## Related pages

- [Development setup](development.md) — container setup, persistent mounts, and first-run steps.
- [Architecture](architecture.md) — how the server components fit together.
