"""Fast tests for the repo-agnostic settings rename + deprecated alias.

Covers:
- RLM_PROJECT_ROOT sets project_root (new primary name).
- RLM_RIPGREP_SRC still sets project_root (deprecated back-compat alias) and
  emits a DeprecationWarning.
- doc_collection has the repo-agnostic default and is overridable.

Run in CI (pytest -m "not integration"); no external services.
"""

import os
import pathlib
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


def test_doc_collection_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """doc_collection defaults to the repo-agnostic name and is overridable."""
    # _env_file=None still reads the environment, and the product image bakes
    # RLM_* variables (RLM_DOC_COLLECTION among them), so clear them all first.
    for name in list(os.environ):
        if name.startswith("RLM_"):
            monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.doc_collection == "project_docs"
    assert Settings(doc_collection="custom", _env_file=None).doc_collection == "custom"  # ty: ignore[unknown-argument]


def test_stale_dotenv_key_is_ignored(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A removed setting left in a .env file must not crash startup (extra=ignore).

    Regression: removing RLM_CHROMA_MODEL_CACHE made an existing .env containing it
    raise extra_forbidden. extra="ignore" must let the rest of the config load.
    """
    # The environment outranks the .env file, and the product image bakes
    # RLM_* variables (RLM_PROJECT_ROOT among them), so clear them all first.
    for name in list(os.environ):
        if name.startswith("RLM_"):
            monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("RLM_PROJECT_ROOT=/tmp/x\nRLM_CHROMA_MODEL_CACHE=/old/no/longer/a/setting\n")
    s = Settings(_env_file=str(env))  # ty: ignore[unknown-argument]
    assert s.project_root == "/tmp/x"
    assert not hasattr(s, "chroma_model_cache")


def _clear_rlm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("RLM_"):
            monkeypatch.delenv(name, raising=False)


def test_stall_and_request_timeout_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stall threshold defaults to 180 s and the request timeout to 120 s."""
    _clear_rlm_env(monkeypatch)
    s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.stall_threshold_seconds == 180
    assert s.request_timeout_seconds == 120


def test_stall_and_request_timeout_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """RLM_STALL_THRESHOLD_SECONDS and RLM_REQUEST_TIMEOUT_SECONDS set the fields."""
    _clear_rlm_env(monkeypatch)
    monkeypatch.setenv("RLM_STALL_THRESHOLD_SECONDS", "240")
    monkeypatch.setenv("RLM_REQUEST_TIMEOUT_SECONDS", "45.5")
    s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.stall_threshold_seconds == 240
    assert s.request_timeout_seconds == 45.5


def test_stall_and_request_timeout_accept_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """0 is accepted for both (it disables the timer signal / the timeout)."""
    _clear_rlm_env(monkeypatch)
    monkeypatch.setenv("RLM_STALL_THRESHOLD_SECONDS", "0")
    monkeypatch.setenv("RLM_REQUEST_TIMEOUT_SECONDS", "0")
    s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.stall_threshold_seconds == 0
    assert s.request_timeout_seconds == 0


@pytest.mark.parametrize("name", ["RLM_STALL_THRESHOLD_SECONDS", "RLM_REQUEST_TIMEOUT_SECONDS"])
def test_stall_and_request_timeout_reject_negative(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """A negative value fails validation."""
    import pydantic

    _clear_rlm_env(monkeypatch)
    monkeypatch.setenv(name, "-1")
    with pytest.raises(pydantic.ValidationError):
        Settings(_env_file=None)  # ty: ignore[unknown-argument]
