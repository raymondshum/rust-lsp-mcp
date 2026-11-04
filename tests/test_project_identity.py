"""Fast-tier tests for the per-project doc-index identity (SW-01).

No model download, no network: a deterministic fake embedding function is
injected.  Covers :func:`rust_lsp_mcp.doc_store.project_identity`, the
identity-derived collection name and fingerprint, and the deployed SW-01
shape: two projects mounted at the SAME path (one after the other) on the
SAME ``chroma_path`` must each search only their own docs.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
from typing import Any

import chromadb
import numpy as np
import pytest

from rust_lsp_mcp.doc_store import (
    _project_fingerprint,
    clear_doc_store,
    doc_collection_name,
    init_doc_store,
    project_identity,
)
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


# (b) derived identity follows Cargo.toml content ------------------------------


def test_derived_identity_tracks_cargo_toml_content(tmp_path: pathlib.Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    s = _settings(project, tmp_path / "chroma")

    no_cargo = project_identity(s)
    assert re.fullmatch(r"[0-9a-f]{16}", no_cargo)

    (project / "Cargo.toml").write_text('[package]\nname = "alpha"\n', encoding="utf-8")
    first = project_identity(s)
    assert first != no_cargo
    # Stable while the content is unchanged.
    assert project_identity(s) == first
    (project / "Cargo.toml").write_text('[package]\nname = "alpha"\n', encoding="utf-8")
    assert project_identity(s) == first

    (project / "Cargo.toml").write_text('[package]\nname = "bravo"\n', encoding="utf-8")
    second = project_identity(s)
    assert second != first
    assert doc_collection_name(s, second) != doc_collection_name(s, first)


# (c) empty RLM_PROJECT_ID equals unset ----------------------------------------


def test_empty_project_id_behaves_as_unset(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")

    unset = _settings(project, tmp_path / "chroma")
    assert unset.project_id is None
    explicit_empty = _settings(project, tmp_path / "chroma", project_id="")
    monkeypatch.setenv("RLM_PROJECT_ID", "")
    env_empty = _settings(project, tmp_path / "chroma")
    assert env_empty.project_id == ""

    assert project_identity(explicit_empty) == project_identity(unset)
    assert project_identity(env_empty) == project_identity(unset)
    assert doc_collection_name(env_empty) == doc_collection_name(unset)


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
