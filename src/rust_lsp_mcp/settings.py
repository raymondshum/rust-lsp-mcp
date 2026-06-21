"""Application settings via pydantic-settings.

Precedence (highest → lowest): real env vars > .env file > code defaults.
All variables are prefixed with RLM_ in the environment.

The server loads .env itself at startup via env_file — no external loader needed.
Defaults point at the known bind-mount paths so the server runs with no .env.
"""

import os
import warnings

from pydantic import AliasChoices, Field, model_validator
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
        # Allow construction by field name (e.g. Settings(project_root=...)) in
        # addition to the env aliases — tests and callers rely on this.
        populate_by_name=True,
    )

    # -----------------------------------------------------------------------
    # Target Rust project
    # -----------------------------------------------------------------------

    # Absolute path to the target Rust project: the workspace rust-analyzer
    # opens and the root whose Markdown files are ingested for doc search.
    # Repo-agnostic — point it at any Rust project (default = the bundled
    # ripgrep sample inside the devcontainer).
    #
    # Env: RLM_PROJECT_ROOT (preferred).  RLM_RIPGREP_SRC is a DEPRECATED alias
    # kept for back-compat; setting it emits a DeprecationWarning (see below).
    # NOTE: because a validation_alias is set, env_prefix is NOT auto-applied to
    # these names (env_prefix_target defaults to 'variable'), so the RLM_ prefix
    # is spelled out explicitly in the AliasChoices.
    project_root: str = Field(
        default="/workspaces/ripgrep",
        validation_alias=AliasChoices("RLM_PROJECT_ROOT", "RLM_RIPGREP_SRC"),
    )

    # -----------------------------------------------------------------------
    # Rust / cargo caches (§0.2)
    # -----------------------------------------------------------------------

    # Cargo build output directory for target compilation (CARGO_TARGET_DIR).
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

    # ChromaDB collection name for the doc-RAG store.  Repo-agnostic default;
    # override per project if hosting multiple stores on one chroma_path.
    doc_collection: str = "project_docs"

    # Glob patterns for markdown files to index (comma-separated).
    # Default indexes all *.md in the target project.
    doc_glob_patterns: str = "**/*.md"

    # Comma-separated glob patterns (relative to project_root) to EXCLUDE from the doc
    # index, even if matched by doc_glob_patterns.  Default excludes CHANGELOG.md, whose
    # hundreds of changelog bullets otherwise flood semantic search (plan-decided remedy).
    doc_exclude_patterns: str = "**/CHANGELOG.md"

    @model_validator(mode="after")
    def _warn_deprecated_ripgrep_src(self) -> "Settings":
        """Emit a DeprecationWarning when the legacy RLM_RIPGREP_SRC env var is
        used instead of RLM_PROJECT_ROOT.

        Detection reads os.environ directly: by the time this runs the value has
        already been mapped onto ``project_root`` via the alias, so the only way
        to know *which* name was set is to inspect the environment.  (A value set
        only in a .env file, not exported to the environment, won't trigger the
        warning — documented behaviour.)
        """
        if "RLM_RIPGREP_SRC" in os.environ and "RLM_PROJECT_ROOT" not in os.environ:
            warnings.warn(
                "RLM_RIPGREP_SRC is deprecated; rename it to RLM_PROJECT_ROOT. "
                "The old name still works for now but will be removed in a future release.",
                DeprecationWarning,
                stacklevel=2,
            )
        return self


def get_settings() -> Settings:
    """Return a Settings instance (loads .env + env vars on first call)."""
    return Settings()
