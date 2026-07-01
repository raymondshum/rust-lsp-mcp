# Production image for rust-lsp-mcp — a self-contained, host-launchable MCP
# server. Unlike the dev container (.devcontainer/Dockerfile, build deps only),
# this image bakes the full toolchain + Python deps so an MCP client on the
# HOST can launch it with `docker run -i` over stdio. The target Rust project is
# NOT baked in — it is bind-mounted at runtime (see README "Connect it to an AI
# assistant").
#
# Why the full Rust toolchain and not just the rust-analyzer binary: rust-analyzer
# shells out to `cargo check`, runs build scripts, and expands proc-macros, and
# uses `sysroot: "discover"` — so it needs rustup + rustc + cargo + rust-std +
# rust-src, plus a C toolchain to link build-script / proc-macro crates.
# (Verified — see docs/planning/repo-agnostic-and-docker-launch.md, claim U2.)

FROM python:3.12-slim-bookworm

# --- System deps -----------------------------------------------------------
# git: status/staleness check + cargo fetching git deps.
# build-essential + pkg-config: C linker/headers for cargo check (build scripts,
#   proc-macros, and -sys crates). Some target projects may need additional
#   system libraries; add them by extending this image.
# libgomp1: OpenMP runtime required by onnxruntime, which ChromaDB's default
#   embedding model (all-MiniLM-L6-v2) uses to build the doc index.
# curl + ca-certificates: fetch rustup.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        pkg-config \
        libgomp1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- uv (pinned, matches the dev container) --------------------------------
COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /usr/local/bin/uv

# --- Rust toolchain (full minimal profile + rust-analyzer + rust-src) ------
# rustup installs the cargo/rustc/rust-analyzer proxies into CARGO_HOME/bin and
# the toolchain into RUSTUP_HOME. These live at image-build paths under
# /usr/local; the *runtime* cargo cache (registry/git) is redirected to a /data
# volume below via CARGO_HOME, while RUSTUP_HOME stays fixed so the proxies
# always resolve the toolchain.
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --no-modify-path --profile minimal \
            --default-toolchain stable \
            --component rust-analyzer,rust-src \
    && rustup --version \
    && rust-analyzer --version

# --- Python project --------------------------------------------------------
WORKDIR /app
# Copy only what uv needs to resolve + install first, for layer caching.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# --frozen asserts the lockfile is current; --no-dev skips test/lint deps.
RUN uv sync --frozen --no-dev

# --- Bake the embedding model (offline-ready) ------------------------------
# ChromaDB derives its ONNX model cache from $HOME (Path.home()/.cache/chroma,
# evaluated at import). Point HOME at a NON-volume path and warm the model at
# build time so it is frozen into the image layer: a bind/named mount on /data
# cannot shadow it, and runtime needs ZERO network for embeddings. HOME here MUST
# match the runtime HOME (re-declared in the runtime block below) or the runtime
# lookup would miss the baked files and try to download. The tar is SHA256-pinned
# by chromadb, so the build-time fetch is integrity-checked; the archive is
# removed after extraction (runtime only validates the 6 extracted files).
ENV HOME=/opt/rlm
RUN mkdir -p /opt/rlm \
 && /app/.venv/bin/python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as D; D()(['warmup'])" \
 && rm -f /opt/rlm/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz

# --- Runtime configuration -------------------------------------------------
# Target project is bind-mounted at /project (read-only) at runtime.
# Caches live under /data so they persist across `--rm` runs via a named volume.
# HOME=/opt/rlm (matching the build-time warmup above) keeps ChromaDB's baked
# ~/.cache/chroma model cache on the non-volume image path, not on /data.
ENV HOME=/opt/rlm \
    RLM_PROJECT_ROOT=/project \
    RLM_CHROMA_PATH=/data/chroma \
    RLM_CARGO_HOME=/data/cargo-home \
    RLM_CARGO_TARGET_DIR=/data/cargo-target \
    RLM_RUST_ANALYZER_TARGET_DIR=/data/cargo-target/rust-analyzer \
    RLM_RUST_ANALYZER_BIN=/usr/local/cargo/bin/rust-analyzer \
    RLM_DOC_COLLECTION=project_docs
# Backstop for ChromaDB's anonymized product telemetry. The app already passes
# Settings(anonymized_telemetry=False) when it builds its client (see
# doc_store.py), but this env var disables telemetry for any client construction
# regardless of code path — no output, no network call from a stdio service.
ENV ANONYMIZED_TELEMETRY=FALSE
# Blanket opt-out for Scarf-based analytics. NOTE: chromadb 1.5.9 ships no Scarf
# agent, so these are inert for it today; they are a standing policy posture so
# any future Scarf-instrumented dependency's client-side telemetry stays off by
# default. They do NOT affect server-side Scarf Gateway download pixels.
ENV SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=1
# rust-analyzer / cargo read these from the environment (the dev container sets
# the same three as containerEnv); redirect the cargo cache + RA target to /data.
ENV CARGO_HOME=/data/cargo-home \
    CARGO_TARGET_DIR=/data/cargo-target \
    RA_TARGET_DIR=/data/cargo-target/rust-analyzer

RUN mkdir -p /project /data
VOLUME ["/data"]

# The server speaks MCP JSON-RPC over stdio: stdin/stdout only, no TTY, no port.
# Launch with `docker run -i` (or `docker exec -i`).
ENTRYPOINT ["/app/.venv/bin/rust-lsp-mcp"]
