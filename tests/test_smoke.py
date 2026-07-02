"""Fast smoke tests — no external dependencies.

These tests run in CI (pytest -m "not integration") and as the everyday fast tier.
They confirm the package is importable and the settings layer initialises cleanly.
"""

import importlib


def test_package_importable() -> None:
    """The rust_lsp_mcp package must be importable (src layout wired correctly)."""
    mod = importlib.import_module("rust_lsp_mcp")
    assert hasattr(mod, "main"), "rust_lsp_mcp must export main()"


def test_main_callable() -> None:
    """main() must be a callable (console-script entry point is wired)."""
    from rust_lsp_mcp import main

    assert callable(main)


def test_settings_defaults() -> None:
    """Settings must initialise from code defaults with no .env present."""
    from rust_lsp_mcp.settings import Settings

    # Instantiate with no env_file to avoid picking up any local .env.
    s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.project_root == "/workspaces/ripgrep"
    assert s.rust_analyzer_bin == "/usr/local/cargo/bin/rust-analyzer"
    assert s.chroma_path == "/workspaces/chroma"
    assert s.doc_glob_patterns == "**/*.md"


def test_dead_cargo_knobs_removed() -> None:
    """cargo_target_dir/cargo_home/rust_analyzer_target_dir were removed as dead
    knobs: no code path ever read them (analyzer.py only passes rust_analyzer_bin
    + project_root to multilspy). Real cache relocation happens via the
    unprefixed CARGO_TARGET_DIR/CARGO_HOME env vars, set at the container level
    (devcontainer containerEnv / production Dockerfile), which cargo reads
    directly. Regression guard: don't let these come back as unused settings.
    """
    from rust_lsp_mcp.settings import Settings

    for removed in ("cargo_target_dir", "cargo_home", "rust_analyzer_target_dir"):
        assert removed not in Settings.model_fields
