"""Fast tests for the repo-agnostic settings rename + deprecated alias.

Covers:
- RLM_PROJECT_ROOT sets project_root (new primary name).
- RLM_RIPGREP_SRC still sets project_root (deprecated back-compat alias) and
  emits a DeprecationWarning.
- doc_collection has the repo-agnostic default and is overridable.

Run in CI (pytest -m "not integration"); no external services.
"""

import warnings

import pytest

from rust_lsp_mcp.settings import Settings


def test_project_root_primary_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """RLM_PROJECT_ROOT populates project_root with no deprecation warning."""
    monkeypatch.delenv("RLM_RIPGREP_SRC", raising=False)
    monkeypatch.setenv("RLM_PROJECT_ROOT", "/tmp/my-rust-app")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.project_root == "/tmp/my-rust-app"


def test_ripgrep_src_alias_still_works_and_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deprecated RLM_RIPGREP_SRC still maps to project_root and warns."""
    monkeypatch.delenv("RLM_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("RLM_RIPGREP_SRC", "/tmp/legacy-target")
    with pytest.warns(DeprecationWarning, match="RLM_RIPGREP_SRC is deprecated"):
        s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.project_root == "/tmp/legacy-target"


def test_project_root_wins_over_alias_without_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both are set, the new name wins and no deprecation warning fires."""
    monkeypatch.setenv("RLM_PROJECT_ROOT", "/tmp/new")
    monkeypatch.setenv("RLM_RIPGREP_SRC", "/tmp/old")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.project_root == "/tmp/new"


def test_doc_collection_default_and_override() -> None:
    """doc_collection defaults to the repo-agnostic name and is overridable."""
    s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.doc_collection == "project_docs"
    assert Settings(doc_collection="custom", _env_file=None).doc_collection == "custom"  # ty: ignore[unknown-argument]
