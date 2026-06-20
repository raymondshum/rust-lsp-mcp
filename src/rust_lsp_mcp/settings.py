"""Application settings via pydantic-settings.

Precedence (highest → lowest): real env vars > .env file > code defaults.
All variables are prefixed with RLM_ in the environment.

The server loads .env itself at startup via env_file — no external loader needed.
Defaults point at the known bind-mount paths so the server runs with no .env.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for rust-lsp-mcp.

    All fields have defaults pointing at the devcontainer bind-mount paths so
    the server starts cleanly with no .env present.  Override via .env or real
    environment variables (prefixed RLM_).
    """

    model_config = SettingsConfigDict(
        env_prefix="RLM_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # -----------------------------------------------------------------------
    # Ripgrep fixture (§0.2 / §0.3)
    # -----------------------------------------------------------------------

    # Absolute path to the pinned ripgrep source clone (bind mount).
    ripgrep_src: str = "/workspaces/ripgrep"

    # -----------------------------------------------------------------------
    # Rust / cargo caches (§0.2)
    # -----------------------------------------------------------------------

    # Cargo build output directory for ripgrep compilation (CARGO_TARGET_DIR).
    cargo_target_dir: str = "/workspaces/cargo-target"

    # CARGO_HOME bind mount (registry + git caches).
    cargo_home: str = "/workspaces/cargo-home"

    # rust-analyzer targetDir (relocates RA's own cargo-check output).
    rust_analyzer_target_dir: str = "/workspaces/cargo-target/rust-analyzer"

    # Path to the rust-analyzer binary inside the container.
    # Default matches the devcontainer rust:1 feature install location.
    # Confirmed at runtime via: rustup which rust-analyzer  (Beat B task).
    rust_analyzer_bin: str = "/usr/local/cargo/bin/rust-analyzer"

    # -----------------------------------------------------------------------
    # ChromaDB / doc RAG (§0.2 / Phase 5)
    # -----------------------------------------------------------------------

    # ChromaDB PersistentClient storage path (bind mount).
    chroma_path: str = "/workspaces/chroma"

    # ONNX embedding-model cache bind-mount target (informational only).
    # ChromaDB hardcodes the model path to Path.home()/.cache/chroma and does NOT
    # read this value.  This field documents the bind-mount target so the path is
    # visible here for devcontainer / docker-compose configuration reference.
    chroma_model_cache: str = "/home/vscode/.cache/chroma"

    # Glob patterns for markdown files to index (comma-separated).
    # Default indexes all *.md in the ripgrep source repo.
    doc_glob_patterns: str = "**/*.md"


def get_settings() -> Settings:
    """Return a Settings instance (loads .env + env vars on first call)."""
    return Settings()
