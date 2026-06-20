# Rust LSP MCP — Implementation Plan

**Status:** Phase 0 fully specified; core service (Phases 1–5) drilled and resolved
(2026-06-19). **Plan-verification pass complete (2026-06-19):** every spec-level
`UNVERIFIED` item has been confirmed against current first-party docs via Context7
(or source inspection) and flipped to `VERIFIED` with a cached `docs/reference/`
entry. The only items still `UNVERIFIED` are explicitly **runtime-only** (confirmable
only against the live analyzer) or **intentionally deferred** (lowest-priority
Dockerfile), each annotated inline. Ready to hand to Claude Code for implementation
in risk-first order.

**Build order is risk-first:** stand up the environment, then prove the two
highest-risk behaviors against a warm analyzer, then layer the remaining tools,
then doc search.

**Verification policy (definition of done for this plan):** every command,
version, flag, and config snippet here must be cross-checked against current docs
via Context7 and cached under `docs/reference/` (version + date stamped) before
the plan is considered final. Each item below carries a verification status:
`VERIFIED` (Context7-checked, with a `docs/reference/` entry) or `UNVERIFIED`
(decided in principle; confirm exact syntax at build).

---

## Target & vision

A rough prototype against **ripgrep** as the one real codebase. "It works" means:
an assistant can name something in ripgrep's code and the server reliably returns
where it is defined, its type, and everywhere it is used — and never returns a
misleading empty answer while indexing is incomplete.

**Scope boundary (resolved 2026-06-19):** this server is **purely the semantic
layer**. It assumes the host assistant already has file-read and text-search
(grep-style) tools and fills broad-orientation gaps itself; we do not ship a
grep/list-files tool. Cold-start exploration from inside the MCP is covered by
`document_symbols` (a file's outline) and `find_symbol` (fuzzy workspace-symbol
search). Goal is to add semantic capability beyond plain text tools, not replace
them.

---

## Phase 0 — Foundation / Dev Environment (SPECIFIED)

### 0.1 Dev container
- VSCode dev container; **Python 3.12** base image.
- **Rust toolchain + rust-analyzer** and **uv** added via dev-container *features*
  (declarative, not a hand-rolled Dockerfile). `VERIFIED (2026-06-19)` /
  **CORRECTION** → [docs/reference/devcontainer-features.md](../reference/devcontainer-features.md):
  - Rust: **`ghcr.io/devcontainers/features/rust:1`** (v1.5.0). Its **default
    `components` already include `rust-analyzer`** (linux-arm64 native) — no extra
    config needed; binary lands at `/usr/local/cargo/bin/rust-analyzer`.
  - uv: **there is no official Astral uv feature. DECIDED (2026-06-19): layer the
    first-party uv binary** via a minimal Dockerfile on top of the features-based
    image — `COPY --from=ghcr.io/astral-sh/uv:<pinned> /uv /uvx /usr/local/bin/`
    (`devcontainer.json` uses `build.dockerfile` + `features`). First-party,
    digest-pinnable, download-once; preferred over the single-maintainer community
    feature (`ghcr.io/va-h/devcontainers-features/uv:1`, kept as the Dockerfile-free
    fallback) and over a `postCreateCommand` install (not layer-cached, least
    reproducible). The minimal `COPY` doesn't reintroduce the "hand-rolled toolchain"
    burden the no-Dockerfile preference guards against. **Build caveat:** keep uv's
    Python consistent with the container's interpreter (let uv manage it, or set
    `UV_PYTHON`) so CI and container resolve the same version.
- rust-analyzer must be present for the server to drive it. **RESOLVED
  (2026-06-19):** multilspy *would* download rust-analyzer itself, but its table has
  **no linux-arm64 entry** (our Apple-Silicon dev container) and pins a stale
  `2023-10-09` build. Decision: **container + Option B** — the devcontainer's Rust
  feature installs rust-analyzer **natively** (build time), and we **subclass
  `RustAnalyzer` to override `setup_runtime_dependencies()`** to use that binary
  (multilspy downloads nothing; we control the version). Nothing installed on the
  host OS. `VERIFIED (2026-06-19)`: override returns a **str path** consumed by
  `ProcessLaunchInfo`; **`LanguageServer.create()` hard-codes `RustAnalyzer`, so the
  subclass must be instantiated directly** (not via `create()`). Native path:
  `/usr/local/cargo/bin/rust-analyzer` (`rustup which rust-analyzer`). See
  [docs/reference/multilspy-rust-backend-audit.md](../reference/multilspy-rust-backend-audit.md).
- Claude Code VSCode extension is used inside the container.

### 0.2 Persistence (bind mounts)
- Three **observable bind mounts** inside the project (gitignored), e.g. under
  `.devcontainer/cache/`:
  - ripgrep **source** (the pinned clone),
  - ripgrep **build output** (cargo `target/`),
  - **rust-analyzer cache**.
- Point the build tool and analyzer at these paths so their output lands on the
  persistent mounts. `VERIFIED (2026-06-19)` / **CORRECTION** →
  [docs/reference/devcontainer-features.md](../reference/devcontainer-features.md):
  **rust-analyzer keeps no separate on-disk index cache** (its salsa index is
  in-memory, rebuilt each `start_server`). What persists is the **cargo `target/`**
  and the **cargo registry under `CARGO_HOME`**. The setting that relocates RA's own
  build artifacts is **`rust-analyzer.cargo.targetDir`** (default `null`; set `true`
  or a path). So the "analyzer cache" mount really backs `CARGO_HOME` + the RA target
  dir; the "build output" mount backs the regular `target/` (`CARGO_TARGET_DIR`).
- **Documented fallback:** if Mac file-IO is slow, move the two heavy folders
  (build output, analyzer cache) to named Docker volumes, keeping source
  bind-mounted. Trades observability for speed only where it bites.
- **Two more persistent locations added by Phase 5** (same gitignored
  `.devcontainer/cache/` pattern): the **ChromaDB vector store**
  (`PersistentClient` path) and the **ONNX embedding-model cache** (bind-mount the
  container's `~/.cache/chroma`). See Phase 5 / the chromadb reference.

### 0.3 Fixture (ripgrep)
- One-line **pinned clone** of ripgrep into the gitignored bind-mount folder
  (pin a fixed release; never edited). Fetched by the setup script; idempotent
  (skip if already present).

### 0.4 Packaging & launch
- **src layout**: code under `src/rust_lsp_mcp/`, tests under `tests/`.
- Provide **both** a named console command (`rust-lsp-mcp`) and run-by-name
  (`python -m rust_lsp_mcp`). `VERIFIED (2026-06-19)` →
  [docs/reference/uv-packaging-ci.md](../reference/uv-packaging-ci.md): console script
  via `[project.scripts]\nrust-lsp-mcp = "rust_lsp_mcp:main"`; run-by-name needs
  `src/rust_lsp_mcp/__main__.py` calling `main()`. Scaffold with `uv init --package`.
- Active launch: `uv run --directory <project> rust-lsp-mcp`.

### 0.5 Configuration
- **Defaults live in code** (the settings layer), pointing at the known mount
  paths — so the server runs with no `.env`.
- **pydantic-settings** for the settings layer (defaults + `.env` + env-var
  overrides). `VERIFIED (2026-06-19)` →
  [docs/reference/pydantic-settings.md](../reference/pydantic-settings.md):
  `BaseSettings` + `model_config = SettingsConfigDict(env_prefix=..., env_file=".env")`;
  default source precedence (init > env > .env > secrets) **already matches** our
  required "code defaults < .env < real env vars" — no customization needed.
- `env.sample` committed (template, placeholder values, documents every variable).
- `.env` gitignored and optional; the **server loads it itself** at startup.
- `init.sh` generates `.env` from `env.sample`; **`--force`-gated** (won't
  overwrite an existing `.env` unless forced). Container startup calls it plainly.
- Precedence: code defaults < `.env` < real environment variables.

### 0.6 Tooling (ruff + ty)
- **ruff**: uv dependency + VSCode extension; linting, formatting, import order.
  Config in `pyproject.toml`. `VERIFIED (2026-06-19)` →
  [docs/reference/ruff-config.md](../reference/ruff-config.md): recommended
  `lint.select = ["E","F","UP","B","SIM","I"]` (I = isort); formatter via `ruff format`.
  VSCode (`charliermarsh.ruff`): `editor.formatOnSave` + `codeActionsOnSave`
  `{"source.fixAll.ruff":"explicit","source.organizeImports.ruff":"explicit"}`.
- **ty**: uv dependency + VSCode extension; the extension makes ty the active type
  checker (Pylance steps aside automatically). `VERIFIED` →
  [docs/reference/ty-vscode-setup.md](../reference/ty-vscode-setup.md).
- `"ty.diagnosticMode": "workspace"` in committed VSCode settings → problems for
  all files, not just open ones. `VERIFIED` (same reference).
- Format + organize imports **on save** via committed VSCode settings.

### 0.7 Tests
- Two tiers split by **marker**:
  - **fast** — call tool functions directly (they are plain Python), fake the
    heavy externals (analyzer, search store); no external deps.
  - **integration** — against the live analyzer + real ripgrep fixture; proves the
    two risky behaviors. (External dep = ripgrep.)
- **CI runs fast tests only** (plus lint + type) to respect free-tier quota.
- **Integration tests run locally on demand** as a **named, required QA gate**
  (e.g. a `qa` script) before a feature is "done".
- VSCode test panel shows **both** tiers, with integration **grouped** so it is
  never run by accident.
- `VERIFIED (2026-06-19)` → [docs/reference/pytest-markers.md](../reference/pytest-markers.md):
  register markers in `[tool.pytest.ini_options] markers = ["integration: ..."]`;
  fast tier = unmarked; CI/everyday runs `pytest -m "not integration"`, QA gate runs
  `pytest -m integration`. VSCode: `python.testing.pytestEnabled` + `pytestArgs`.

### 0.8 Run / debug
- **Run** config: `uv run rust-lsp-mcp` (boot/smoke check).
- **Debug** config: run the tests under the debugger; breakpoints land in the
  plain-Python tool functions (the everyday path).
- **Inspector**: optional task to exercise the stdio transport by hand.
  `VERIFIED (2026-06-19)` → [docs/reference/mcp-inspector.md](../reference/mcp-inspector.md):
  `npx @modelcontextprotocol/inspector -- uv run rust-lsp-mcp` (UI :6274) or
  `--cli` for headless. Needs Node ≥ 22.7.5 in the container.

### 0.9 Scripts
- **setup** (idempotent): clone fixture if missing → run `init.sh` → sync deps
  (`uv sync`). Safe to run automatically on every container rebuild.
- **teardown** (sole destructive reset): remove fixture, build output, analyzer
  cache, `.env`, and the Python environment. **Distinct from the product's
  "refresh"**, which must never wipe the analyzer's saved work.

### 0.10 CI (GitHub Actions)
- Steps: ruff, ty, fast tests, and the **`env.sample`-honesty check** (every
  variable the settings layer reads has an `env.sample` entry).
- Gates on all of the above. No `.env` (runs on code defaults). No integration
  tests. `VERIFIED (2026-06-19)` → [docs/reference/uv-packaging-ci.md](../reference/uv-packaging-ci.md):
  `astral-sh/setup-uv@v8.1.0` (`enable-cache: true`, `python-version`), then
  `uv sync` → `uv run ruff check`/`ruff format --check` → `uv run ty check` →
  `uv run --frozen pytest -m "not integration"`.

### 0.11 Deployment
- Active path: `uv run`, launched by the model/client.
- A real but **lowest-priority** server Dockerfile for later. Note: the image must
  carry the Rust toolchain + rust-analyzer, so it is inherently heavy. Built and
  smoke-checked last; gates nothing. `UNVERIFIED — intentionally deferred`
  (lowest priority; the uv-image layering options in
  [docs/reference/devcontainer-features.md](../reference/devcontainer-features.md)
  apply here too).

### Dependencies to add (via `uv add`, in-container, by Claude Code)
Confirm current names/versions via Context7 at add time: official MCP Python SDK
(`mcp`; server shape `VERIFIED (2026-06-19)` →
[docs/reference/mcp-python-sdk-server.md](../reference/mcp-python-sdk-server.md) —
use **FastMCP** `mcp.run()` over stdio; the low-level v2 API is still on `main`),
multilspy, chromadb, pydantic-settings, an embedding library (see §11.4 below),
plus dev tools pytest, ruff, ty. (Versions are intentionally not hand-written
here — `uv add` resolves current.) Versions confirmed during this pass: mcp 1.12.4,
multilspy 0.0.15, chromadb 1.5.9.

---

## Phase 1+ — Core service (NOT YET DRILLED — open decisions)

These carry the project's real risk and must be drilled (grilled) before they are
built. Listed with the open questions to resolve.

### Phase 1 — Warm analyzer + readiness gating ⚠️ highest-risk (handoff §7.1)

**Resolved 2026-06-19** (see [docs/reference/multilspy-readiness.md](../reference/multilspy-readiness.md)):

- **Detection mechanism — DECIDED, provided by the library.** rust-analyzer's
  `experimental/serverStatus` notification with `quiescent: true` is the ready
  signal. multilspy 0.0.15 already advertises the capability, handles the
  notification, and **blocks `start_server()` until quiescent** — so inside the
  context the analyzer is warm by construction. No progress % is available
  (multilspy swallows `$/progress`).
- **Server architecture — DECIDED.** Run multilspy's `start_server()` in a
  **background task** held open for the whole server lifetime; the MCP layer keeps
  its **own readiness flag** ("indexing" until the context is live, then "ready").
- **Gating model — DECIDED: fail-fast.** A tool called before ready returns
  *immediately* with an explicit not-ready status (no blocking, no empty list).
  Never block the MCP request; never return a misleading empty. Pairs with the
  `status` tool (Phase 4) for the assistant's readiness check / retry.

- **Response envelope — DECIDED (applies to all navigation tools).** Every tool
  returns a uniform structured envelope `{"status": ..., ...}`. Status vocabulary:
    - `ok` — query ran; payload may be populated **or meaningfully empty** (empty is
      trustworthy here, e.g. a real symbol with zero references).
    - `not_ready` — still indexing; retry later.
    - `not_found` — the **resolution** step failed: the named thing can't be located
      as a symbol at all (distinct from `ok`+empty, which is the **analysis** step
      succeeding with a zero answer).
    - `error` — malformed input / internal / LSP failure, with a short message.
  - Not-ready / empty / not-found are carried as **data in the envelope**, not via
    MCP protocol-level errors (those are reserved for genuine crashes).
  - `ambiguous` status was **considered and dropped** (see Phase 2, Option A):
    action tools are position-based and never resolve names, so multiple matches
    live inside `find_symbol`'s ordinary result list, not a special status.
  - **Status mapping refinement (DECIDED).** `not_found` = the named/located thing
    isn't there: `find_symbol(name)` with zero matches, or a position-based call that
    resolves to no symbol/definition. `ok` + empty = a valid located target with a
    legitimately zero analytic answer (chiefly `find_references` with zero callers).

### Phase 2 — Name→position resolution (handoff §7.2)

**Resolved 2026-06-19.** multilspy provides the bridge directly:
`request_workspace_symbol(query)` → list of candidates, each a
`UnifiedSymbolInformation` carrying `name`, `kind`, and `location` (file + range);
rust-analyzer typically adds a container (module / `impl`) — `UNVERIFIED —
runtime-only` (the container label can only be confirmed against the live analyzer;
checked in this pass and intentionally left for Phase 2 build). Also available: `request_document_symbols`, `request_definition`,
`request_references`, `request_hover`.

- **Architecture — DECIDED: Option A (strict separation, position-based actions).**
  - `find_symbol(name)` is the **sole** name→symbol bridge: runs `workspace_symbol`,
    returns the candidate list (name, kind, file, line, container). Multiple matches
    is a normal multi-hit result, not a special case. Zero matches → `not_found`.
  - `goto_definition`, `find_references`, `hover` take a precise **position**
    (`file`, `line`, `character`) — never a name. They never guess/auto-pick.
  - The assistant's natural loop: `find_symbol` / `document_symbols` to get a
    position → act with that position (position is already in hand from the
    discovery step, so no redundant round-trip).
  - Overloads / methods-vs-free-functions / generic instantiations are handled for
    free: they surface as distinct candidates in `find_symbol`'s list; the assistant
    picks the right one by its kind/container/location.
  - **Stateless:** no symbol handles/IDs cached; the assistant passes positions back
    directly (consistent with "never cache cross-file-dependent results").

### Phase 3 — Navigation tools
- `document_symbols`, `find_symbol`, `goto_definition`, `find_references`, `hover`.
- **Indexing convention — DECIDED: uniformly 1-indexed at the boundary.** All
  positions the tools emit/accept are 1-indexed (line *and* character); convert
  to/from LSP's 0-indexed in a **single boundary helper** (one auditable spot).
  Self-consistent so the assistant round-trips positions with no arithmetic; legible
  against editors / `grep -n`. Caveat `UNVERIFIED — N/A for prototype`: LSP character
  offsets are UTF-16 by default — irrelevant for ripgrep's all-ASCII source;
  intentionally not solved for the prototype.
- **`document_symbols` shape — DECIDED: flat list** with a `container` label (not a
  nested tree). Smallest/most uniform schema, fast for the dominant "find symbol →
  grab position" use, consistent with `find_symbol`. Tree is a cheap non-breaking
  add later (multilspy returns both) if orientation needs it.
- **Path convention — DECIDED: workspace-relative** on both input and output.
  multilspy takes `relative_file_path` and returns `Location` with `relativePath`
  (relative to repo root) already computed — so relative is natural and portable.

#### Proposed tool schemas (positions 1-indexed; every result wrapped in the
`{status, ...}` envelope)

- `find_symbol(name)` → `results: [{name, kind, file, line, character, container}]`;
  zero matches → `not_found`.
- `document_symbols(file)` → `symbols: [{name, kind, line, character, container}]`
  (flat).
- `goto_definition(file, line, character)` → `definitions: [{file, line, character}]`
  (usually one; can be several); none → `not_found`.
- `find_references(file, line, character[, include_declaration])` →
  `references: [{file, line, character}]`; zero → `ok` + empty (real "no callers").
- `hover(file, line, character)` → `contents: <rust-analyzer hover markdown string>`
  (carries the type signature + docs); nothing there → `not_found`.
- **`find_references` — DECIDED:** optional `include_declaration` flag, default
  `false` (uses-only); the declaration is `goto_definition`'s job.
- **`hover` — DECIDED:** return rust-analyzer's hover **markdown string** as-is
  (carries type signature + docs); no parsing for the prototype.

### Phase 4 — Operational tools

**Resolved 2026-06-19.**

- **`refresh` — DECIDED: unconditional.** Every call does teardown + wholesale
  re-index, no hash-gating. Simple and predictable; the assistant decides *when* to
  call it, informed by `status`. (Hash-gating rejected as a premature optimization
  that also can't see uncommitted edits — a potential "looks fresh but isn't" trap.)
- **`status` — DECIDED: report four fields.**
  - `state`: `"indexing"` | `"ready"` (the Phase 1 readiness flag — essential, the
    partner to fail-fast gating).
  - `indexed_commit`: git hash the current index was built from.
  - `current_commit`: working tree `HEAD`.
  - `stale`: `indexed_commit != current_commit`.
  - **Caveat (state in the tool description):** commit-hash comparison does **not**
    catch uncommitted edits, so `stale: false` means "no committed changes," not a
    freshness guarantee. For the pinned ripgrep clone this is effectively always
    `ready` + not stale, but it's the correct shape for real edited repos.

### Phase 5 — Documentation RAG
- Header-tree chunking + breadcrumb paths; preserve backticked identifiers; plain
  semantic search; one `search_docs(query)` tool. ChromaDB embedded/local.
- **Doc target — CONFIRMED:** ripgrep's own markdown (same project we navigate).
- **Doc sources — DECIDED:** index **all `*.md` in the repo (recursive)**, driven by
  a **configurable glob list** in settings (default = whole repo). `CHANGELOG.md` is
  the obvious first exclusion if doc-search quality suffers.
- **Persistence — DECIDED:** ChromaDB `PersistentClient` writing to a **bind-mount**
  folder (settings path; gitignored, under `.devcontainer/cache/`). Built once,
  rebuilt by `refresh` (wholesale, same model as the code index).
- **Embedding model — DECIDED (see cross-cutting below).** Local ChromaDB default
  (`all-MiniLM-L6-v2`, ONNX, 256-token window, cosine distance).
- **Chunking — DECIDED.** Structure-aware, two-stage (the pattern ChromaDB's docs
  steer toward; ChromaDB itself prescribes no chunker — it recommends "recursive or
  semantic chunking" via text splitters and gives no size/overlap numbers):
  1. Split on markdown headers → one chunk per leaf section, breadcrumb prepended
     (e.g. `GUIDE.md > Configuration > Ignoring files`), backticked identifiers
     preserved.
  2. Size-split any section whose breadcrumb+body exceeds the cap, on paragraph
     boundaries. **Cap kept under the embedder's 256-token window** (target ~200
     body tokens + breadcrumb) — more conservative than generic 512-token splitter
     defaults, to avoid silent truncation by MiniLM.
  - **Overlap:** none across header sections (natural boundaries); a **small overlap
    (~10–15%, ~1 sentence) only on intra-section size-splits**, matching the
    recommended recursive pattern. Cheap because splits are rare.

### Cross-cutting decision — embedding model (handoff §11.4) — DECIDED
- **Local**, via ChromaDB's bundled `DefaultEmbeddingFunction` =
  `ONNXMiniLM_L6_V2` (all-MiniLM-L6-v2 on ONNX Runtime): CPU, **no torch, no API
  key**, defaults-only works, CI needs no secret. Requires the **full `chromadb`**
  package (not `chromadb-client`). Set collection distance to **cosine**.
  See [docs/reference/chromadb-default-embedder.md](../reference/chromadb-default-embedder.md).
- **Model cache — DECIDED:** the model (~80 MB, hardcoded to
  `~/.cache/chroma/onnx_models/<model>`, no env override) is **bind-mounted** so it
  downloads once and survives rebuilds. Fallback: subclass `ONNXMiniLM_L6_V2` with an
  overridden `DOWNLOAD_PATH`.

### Pre-implementation audit (handoff §9) — DONE 2026-06-19
- Audited multilspy 0.0.15's rust backend by source inspection. Findings + decisions
  in [docs/reference/multilspy-rust-backend-audit.md](../reference/multilspy-rust-backend-audit.md):
  - Readiness, the five nav primitives, and the workspace-symbol bridge are all
    present and usable.
  - Binary-provisioning gap (no linux-arm64, stale pin) → **container + Option B**
    (subclass override to use the devcontainer's native rust-analyzer). See Phase 0.1.
- Remaining build-time `UNVERIFIED` items are listed in that reference doc.

---

## Settled architecture (from handoff §3 — do not reopen without new info)

MCP server (not skill); read-only (agent performs edits); refresh = teardown +
wholesale re-index (never cache cross-file-dependent results — defer invalidation
to rust-analyzer/salsa); rely on rust-analyzer's on-disk cache (don't wipe it);
plain doc-RAG (no GraphRAG); no precomputed code↔doc links; no file watching; RAG
co-located in the same server process; ChromaDB local/embedded; stdio transport,
single host.
