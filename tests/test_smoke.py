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
    assert s.ripgrep_src == "/workspaces/ripgrep"
    assert s.cargo_target_dir == "/workspaces/cargo-target"
    assert s.cargo_home == "/workspaces/cargo-home"
    assert s.rust_analyzer_target_dir == "/workspaces/cargo-target/rust-analyzer"
    assert s.rust_analyzer_bin == "/usr/local/cargo/bin/rust-analyzer"
    assert s.chroma_path == "/workspaces/chroma"
    assert s.chroma_model_cache == "/home/vscode/.cache/chroma"
    assert s.doc_glob_patterns == "**/*.md"
