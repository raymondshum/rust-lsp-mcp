"""Fast-tier tests for the per-project doc-index identity (SW-01).

No model download, no network: a deterministic fake embedding function is
injected.  Covers :func:`rust_lsp_mcp.doc_store.project_identity`, the
identity-derived collection name and fingerprint, and the deployed SW-01
shape: two projects mounted at the SAME path (one after the other) on the
SAME ``chroma_path`` must each search only their own docs.
"""

from __future__ import annotations

import asyncio
import hashlib
import pathlib
import re
import subprocess
from typing import Any

import chromadb
import numpy as np
import pytest

import rust_lsp_mcp.doc_store as doc_store_mod
from rust_lsp_mcp.doc_store import (
    ProjectIdentityUnavailable,
    _project_fingerprint,
    clear_doc_store,
    doc_collection_name,
    doc_store_state,
    init_doc_store,
    project_identity,
)
from rust_lsp_mcp.envelope import RECOVERY_REFRESH, STATUS_ERROR
from rust_lsp_mcp.settings import Settings


def _hash_vec(text: str, dim: int = 8) -> np.ndarray:  # type: ignore[type-arg]
    digest = hashlib.md5(text.encode()).digest()
    floats = [(digest[i % len(digest)] / 255.0) * 2.0 - 1.0 for i in range(dim)]
    return np.array(floats, dtype=np.float32)


class FakeEmbeddingFunction(chromadb.api.types.EmbeddingFunction[chromadb.api.types.Documents]):
    """Deterministic fake EF (mirrors tests/test_doc_store.py)."""

    def __init__(self) -> None:
        pass

    def __call__(self, input: chromadb.api.types.Documents) -> chromadb.api.types.Embeddings:
        return [_hash_vec(doc) for doc in input]

    @staticmethod
    def name() -> str:
        return "fake-deterministic-identity"

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> FakeEmbeddingFunction:
        return FakeEmbeddingFunction()

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        pass


def _settings(project_root: pathlib.Path, chroma_path: pathlib.Path, **kw: Any) -> Settings:
    return Settings(
        chroma_path=str(chroma_path),
        project_root=str(project_root),
        doc_glob_patterns="**/*.md",
        **kw,
    )


@pytest.fixture(autouse=True)
def _no_env_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's exported RLM_PROJECT_ID from leaking into these tests."""
    monkeypatch.delenv("RLM_PROJECT_ID", raising=False)


# (a) explicit ids on one path ------------------------------------------------


def test_explicit_ids_on_same_path_give_different_names_and_fingerprints(
    tmp_path: pathlib.Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    one = _settings(project, tmp_path / "chroma", project_id="alpha")
    two = _settings(project, tmp_path / "chroma", project_id="bravo")

    assert project_identity(one) == "alpha"
    assert project_identity(two) == "bravo"
    assert doc_collection_name(one) != doc_collection_name(two)
    assert _project_fingerprint(one) != _project_fingerprint(two)
    assert _project_fingerprint(one) == f"{project.resolve()}|alpha"


def test_collection_name_is_valid_whatever_the_id(tmp_path: pathlib.Path) -> None:
    """The name is prefix + "_" + 12 hex chars, so odd ids never break ChromaDB."""
    s = _settings(tmp_path, tmp_path / "chroma", project_id="My Project / ünïcode!")
    name = doc_collection_name(s)
    assert re.fullmatch(r"project_docs_[0-9a-f]{12}", name), name
    # ChromaDB accepts it.
    client = chromadb.EphemeralClient()
    client.create_collection(name)


# (b) derived identity follows the root package name only ---------------------


def _write_cargo(project: pathlib.Path, text: str) -> None:
    (project / "Cargo.toml").write_text(text, encoding="utf-8")


def test_derived_identity_tracks_package_name(tmp_path: pathlib.Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    s = _settings(project, tmp_path / "chroma")

    no_cargo = project_identity(s)
    assert re.fullmatch(r"[0-9a-f]{16}", no_cargo)

    _write_cargo(project, '[package]\nname = "alpha"\nversion = "0.1.0"\n')
    alpha = project_identity(s)
    assert alpha != no_cargo
    assert project_identity(s) == alpha  # stable while unchanged

    _write_cargo(project, '[package]\nname = "bravo"\nversion = "0.1.0"\n')
    bravo = project_identity(s)
    assert bravo != alpha
    assert doc_collection_name(s, bravo) != doc_collection_name(s, alpha)


def test_derived_identity_ignores_version_bump(tmp_path: pathlib.Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    s = _settings(project, tmp_path / "chroma")
    _write_cargo(project, '[package]\nname = "alpha"\nversion = "0.1.0"\n')
    before = project_identity(s)
    _write_cargo(project, '[package]\nname = "alpha"\nversion = "0.2.0"\n')
    assert project_identity(s) == before


def test_derived_identity_ignores_added_dependency(tmp_path: pathlib.Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    s = _settings(project, tmp_path / "chroma")
    _write_cargo(project, '[package]\nname = "alpha"\nversion = "0.1.0"\n')
    before = project_identity(s)
    _write_cargo(
        project,
        '[package]\nname = "alpha"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1"\n',
    )
    assert project_identity(s) == before


@pytest.mark.parametrize(
    "cargo_text",
    [
        "this is = = not toml [[[\n",  # unparsable
        '[workspace]\nmembers = ["crates/a"]\n',  # workspace-only root, no [package]
    ],
)
def test_unusable_cargo_toml_counts_as_no_package_name(
    tmp_path: pathlib.Path, cargo_text: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    s = _settings(project, tmp_path / "chroma")
    absent = project_identity(s)
    _write_cargo(project, cargo_text)
    assert project_identity(s) == absent


# (c) empty or whitespace-only RLM_PROJECT_ID equals unset ---------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t \n"])
def test_blank_project_id_behaves_as_unset(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_cargo(project, '[package]\nname = "x"\n')

    unset = _settings(project, tmp_path / "chroma")
    assert unset.project_id is None
    explicit_blank = _settings(project, tmp_path / "chroma", project_id=blank)
    monkeypatch.setenv("RLM_PROJECT_ID", blank)
    env_blank = _settings(project, tmp_path / "chroma")
    assert env_blank.project_id == blank

    assert project_identity(explicit_blank) == project_identity(unset)
    assert project_identity(env_blank) == project_identity(unset)
    assert doc_collection_name(env_blank) == doc_collection_name(unset)


def test_explicit_project_id_is_stripped(tmp_path: pathlib.Path) -> None:
    s = _settings(tmp_path, tmp_path / "chroma", project_id="  alpha  ")
    assert project_identity(s) == "alpha"


def test_env_project_id_is_read(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RLM_PROJECT_ID", "from-env")
    assert project_identity(_settings(tmp_path, tmp_path / "chroma")) == "from-env"


# (d) SW-01 regression: one path, two contents, one chroma_path ---------------


def _write_docs(project: pathlib.Path, token: str) -> None:
    for old in project.glob("*.md"):
        old.unlink()
    (project / "README.md").write_text(
        f"# Project docs\n\nThis project mentions {token} in its docs.\n", encoding="utf-8"
    )


def test_two_projects_on_same_path_and_volume_search_only_their_own_docs(
    tmp_path: pathlib.Path,
) -> None:
    """The deployed SW-01 shape: every container mounts its project at the same
    path (``/project``) and shares one ``/data``.  Project ``alpha`` builds
    first; then the SAME directory holds project ``bravo``'s docs and a server
    starts with ``RLM_PROJECT_ID=bravo`` on the same ``chroma_path``.  Each
    project's store must find only its own token."""
    project = tmp_path / "project"  # one mount path for both projects
    project.mkdir()
    chroma = tmp_path / "chroma"  # one shared volume
    alpha_token = "zzzunique_alpha_sw01_token"
    bravo_token = "zzzunique_bravo_sw01_token"

    _write_docs(project, alpha_token)
    alpha_settings = _settings(project, chroma, project_id="alpha")
    clear_doc_store()
    alpha_store = init_doc_store(alpha_settings, embedding_function=FakeEmbeddingFunction())
    assert alpha_store.is_ready is True
    clear_doc_store()

    _write_docs(project, bravo_token)
    bravo_settings = _settings(project, chroma, project_id="bravo")
    bravo_store = init_doc_store(bravo_settings, embedding_function=FakeEmbeddingFunction())
    assert bravo_store.is_ready is True

    def _texts(store: Any, token: str) -> list[str]:
        return [r["text"] for r in store.search(token, n_results=10)]

    # bravo sees only bravo's docs, never alpha's (the SW-01 defect).
    assert any(bravo_token in t for t in _texts(bravo_store, bravo_token))
    assert not any(alpha_token in t for t in _texts(bravo_store, alpha_token))
    assert not any(alpha_token in t for t in _texts(bravo_store, bravo_token))

    # alpha's collection survives beside bravo's and still holds only alpha.
    assert any(alpha_token in t for t in _texts(alpha_store, alpha_token))
    assert not any(bravo_token in t for t in _texts(alpha_store, bravo_token))
    assert alpha_store._collection.name != bravo_store._collection.name
    clear_doc_store()


# (e) backlog line 29: a git timeout never derives a different identity --------


def _git_ok(remote: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=remote + "\n", stderr="")


def _timeout() -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(cmd=["git"], timeout=5.0)


class _FakeRun:
    """Stand-in for ``subprocess.run`` that replays *outcomes* in order."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _identity_with(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, *outcomes: Any
) -> tuple[str, _FakeRun]:
    fake = _FakeRun(*outcomes)
    monkeypatch.setattr(doc_store_mod.subprocess, "run", fake)
    return project_identity(settings), fake


def test_git_timeout_once_then_remote_gives_same_identity(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, tmp_path / "chroma")
    remote = "https://example.invalid/repo.git"
    clean, clean_run = _identity_with(monkeypatch, settings, _git_ok(remote))
    retried, retried_run = _identity_with(monkeypatch, settings, _timeout(), _git_ok(remote))
    assert retried == clean
    assert clean_run.calls == 1
    assert retried_run.calls == 2


def test_git_timeout_twice_raises_project_identity_unavailable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, tmp_path / "chroma")
    fake = _FakeRun(_timeout(), _timeout())
    monkeypatch.setattr(doc_store_mod.subprocess, "run", fake)
    with pytest.raises(ProjectIdentityUnavailable) as excinfo:
        project_identity(settings)
    assert fake.calls == 2
    root = tmp_path.resolve()
    assert str(excinfo.value) == (
        f"could not read the git remote of {root} (git timed out); set RLM_PROJECT_ID"
        " to a fixed name, or call refresh to retry"
    )


def test_other_oserror_twice_raises_project_identity_unavailable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, tmp_path / "chroma")
    fake = _FakeRun(PermissionError("denied"), OSError("busy"))
    monkeypatch.setattr(doc_store_mod.subprocess, "run", fake)
    with pytest.raises(ProjectIdentityUnavailable):
        project_identity(settings)
    assert fake.calls == 2


def _no_remote_identity(settings: Settings) -> str:
    root = pathlib.Path(settings.project_root).resolve()
    return hashlib.sha256("\n".join([str(root), "", ""]).encode()).hexdigest()[:16]


def test_git_nonzero_exit_gives_no_remote_identity_without_retry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, tmp_path / "chroma")
    absent = subprocess.CompletedProcess(args=["git"], returncode=1, stdout="", stderr="")
    identity, fake = _identity_with(monkeypatch, settings, absent)
    assert fake.calls == 1
    assert identity == _no_remote_identity(settings)


def test_git_binary_absent_gives_no_remote_identity(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, tmp_path / "chroma")
    identity, fake = _identity_with(monkeypatch, settings, FileNotFoundError("git"))
    assert fake.calls == 1
    assert identity == _no_remote_identity(settings)


def test_explicit_project_id_makes_no_git_call(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, tmp_path / "chroma", project_id="fixed")
    identity, fake = _identity_with(monkeypatch, settings)
    assert identity == "fixed"
    assert fake.calls == 0


_UNAVAILABLE = (
    "could not read the git remote of /x (git timed out); set RLM_PROJECT_ID"
    " to a fixed name, or call refresh to retry"
)


def _raise_unavailable(settings: Settings) -> str:
    raise ProjectIdentityUnavailable(_UNAVAILABLE)


def test_identity_unavailable_at_startup_reads_as_doc_index_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At startup the raise lands in ``_init_error`` before a store exists:
    ``doc_store_state()`` reads ``error`` with the message, ``status`` reports
    it with no rebuild in flight, and ``search_docs`` answers ``error`` with
    ``recovery: "refresh"``."""
    import rust_lsp_mcp.tools.search_docs as search_mod
    import rust_lsp_mcp.tools.status as status_mod

    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Docs\n\nSome text.\n", encoding="utf-8")
    settings = _settings(project, tmp_path / "chroma")
    clear_doc_store()
    monkeypatch.setattr(doc_store_mod, "project_identity", _raise_unavailable)
    with pytest.raises(ProjectIdentityUnavailable):
        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
    expected = f"ProjectIdentityUnavailable: {_UNAVAILABLE}"
    assert doc_store_mod.get_doc_store() is None
    assert doc_store_state() == ("error", expected)
    reply = asyncio.run(status_mod.status())
    assert (reply["doc_index_state"], reply["doc_index_error"]) == ("error", expected)

    result = asyncio.run(search_mod.search_docs(query="text", limit=3))
    assert result["status"] == STATUS_ERROR
    assert result["recovery"] == RECOVERY_REFRESH
    assert _UNAVAILABLE in result["message"]
    clear_doc_store()


def test_identity_unavailable_in_refresh_rebuild_keeps_old_index_serving(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside a ``refresh`` rebuild on a ready store the raise fails the
    rebuild only: ``doc_index_state`` stays ``ready`` with ``doc_index_error``
    set, and the old index keeps answering."""
    import rust_lsp_mcp.tools.status as status_mod
    from rust_lsp_mcp.tools.refresh import _doc_rebuild_work

    project = tmp_path / "project"
    project.mkdir()
    token = "zzzunique_identity_refresh_token"
    (project / "README.md").write_text(f"# Docs\n\nMentions {token}.\n", encoding="utf-8")
    settings = _settings(project, tmp_path / "chroma", project_id="before")
    clear_doc_store()
    store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
    assert doc_store_state() == ("ready", None)

    monkeypatch.setattr(doc_store_mod, "project_identity", _raise_unavailable)
    work = _doc_rebuild_work(
        store, settings, init_doc_store, doc_store_mod.current_shutdown_generation()
    )
    work()  # the refresh worker body; never raises

    expected = f"ProjectIdentityUnavailable: {_UNAVAILABLE}"
    assert doc_store_state() == ("ready", expected)
    reply = asyncio.run(status_mod.status())
    assert (reply["doc_index_state"], reply["doc_index_error"]) == ("ready", expected)
    assert any(token in r["text"] for r in store.search(token, n_results=3))
    clear_doc_store()


def _killed() -> subprocess.CompletedProcess[str]:
    """A git run killed by a signal (SIGKILL): ``returncode`` -9."""
    return subprocess.CompletedProcess(args=["git"], returncode=-9, stdout="", stderr="")


def test_git_killed_by_signal_once_then_remote_gives_same_identity(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, tmp_path / "chroma")
    remote = "https://example.invalid/repo.git"
    clean, _ = _identity_with(monkeypatch, settings, _git_ok(remote))
    retried, fake = _identity_with(monkeypatch, settings, _killed(), _git_ok(remote))
    assert retried == clean
    assert fake.calls == 2


def test_git_killed_by_signal_twice_raises_project_identity_unavailable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, tmp_path / "chroma")
    fake = _FakeRun(_killed(), _killed())
    monkeypatch.setattr(doc_store_mod.subprocess, "run", fake)
    with pytest.raises(ProjectIdentityUnavailable):
        project_identity(settings)
    assert fake.calls == 2
