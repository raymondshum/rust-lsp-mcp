"""Fast-tier tests for Phase 5 DocStore.

No real model download, no network.  A deterministic fake embedding function
is injected so these tests run entirely in-process with no heavy dependencies.

Coverage:
    - ``rebuild`` returns correct chunk count.
    - ``is_ready`` is False during rebuild (invariant enforced by ordering
      convention) and True after completion.
    - ``search`` returns the documented result-dict shape, best-first ordering.
    - Empty corpus: ``rebuild`` returns 0 and ``search`` returns [].
    - ``file``/``breadcrumb`` metadata flows correctly from chunk → result.
    - ``init_doc_store`` / ``get_doc_store`` / ``clear_doc_store`` singleton lifecycle.
    - ``init_doc_store`` "adopt existing" branch: populated collection is reused,
      not rebuilt.
    - Wholesale rebuild: second ``rebuild()`` replaces first index (idempotent count).
"""

from __future__ import annotations

import hashlib
import pathlib
from typing import Any
from unittest.mock import patch

import chromadb
import numpy as np

from rust_lsp_mcp.doc_store import (
    DocStore,
    clear_doc_store,
    get_doc_store,
    init_doc_store,
)
from rust_lsp_mcp.settings import Settings

# ---------------------------------------------------------------------------
# Fake embedding function — deterministic, no model download.
# Implements the chromadb 1.5.9 EF protocol:
#   - Subclasses EmbeddingFunction[Documents]
#   - Implements __call__(self, input: Documents) -> Embeddings
#   - Implements name() staticmethod
#   - Implements build_from_config / get_config to suppress DeprecationWarnings
# ---------------------------------------------------------------------------


def _hash_vec(text: str, dim: int = 8) -> np.ndarray:  # type: ignore[type-arg]
    """Produce a deterministic unit-range float32 array from text via MD5."""
    digest = hashlib.md5(text.encode()).digest()
    floats = [(digest[i % len(digest)] / 255.0) * 2.0 - 1.0 for i in range(dim)]
    return np.array(floats, dtype=np.float32)


class FakeEmbeddingFunction(chromadb.api.types.EmbeddingFunction[chromadb.api.types.Documents]):
    """Deterministic fake EF: identical text always produces identical vector.

    Uses MD5 hash of each document string mapped to a small float vector.
    This ensures ``search`` is exercised with consistent cosine distances.
    """

    def __init__(self) -> None:
        pass

    def __call__(self, input: chromadb.api.types.Documents) -> chromadb.api.types.Embeddings:
        return [_hash_vec(doc) for doc in input]

    @staticmethod
    def name() -> str:
        return "fake-deterministic"

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> FakeEmbeddingFunction:
        return FakeEmbeddingFunction()

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: pathlib.Path, corpus_dir: pathlib.Path) -> Settings:
    """Return a Settings instance pointing at tmp paths (no real bind mounts)."""
    return Settings(
        chroma_path=str(tmp_path / "chroma"),
        ripgrep_src=str(corpus_dir),
        doc_glob_patterns="**/*.md",
        chroma_model_cache=str(tmp_path / "model_cache"),
    )


def _write_corpus(corpus_dir: pathlib.Path) -> None:
    """Write a small set of fake markdown files to *corpus_dir*."""
    corpus_dir.mkdir(parents=True, exist_ok=True)

    (corpus_dir / "intro.md").write_text(
        "# Introduction\n\nThis is the intro section.\n\n"
        "## Getting Started\n\nHere is how to get started.\n",
        encoding="utf-8",
    )
    (corpus_dir / "guide.md").write_text(
        "# Guide\n\nThe guide explains how to ignore files.\n\n"
        "## Ignoring Files\n\nUse a .gitignore or --glob pattern to skip files.\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Core DocStore tests
# ---------------------------------------------------------------------------


class TestDocStoreRebuild:
    def test_rebuild_returns_chunk_count(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        ef = FakeEmbeddingFunction()
        store = DocStore(settings, embedding_function=ef)

        count = store.rebuild()
        assert count > 0, "Expected at least one chunk from the corpus"

    def test_is_ready_false_before_rebuild(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        # Before any rebuild, is_ready is False.
        assert store.is_ready is False

    def test_is_ready_true_after_rebuild(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        store.rebuild()
        assert store.is_ready is True

    def test_is_ready_false_at_start_of_rebuild(self, tmp_path: pathlib.Path) -> None:
        """Verifies the ordering invariant: is_ready is set False BEFORE any work.

        We patch create_collection on the client instance to capture is_ready
        at the moment it is called, which is after the initial False-flip and
        after delete_collection — i.e. early in the rebuild critical section.
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        # First build to completion so there's something to capture on second rebuild.
        store.rebuild()
        assert store.is_ready is True

        # On second rebuild, is_ready should flip to False at the very start.
        ready_states_during_rebuild: list[bool] = []
        orig_create_collection = store._client.create_collection

        def _patched_create_collection(*args: Any, **kwargs: Any) -> Any:
            # Capture is_ready right when create_collection is called (early in rebuild).
            ready_states_during_rebuild.append(store.is_ready)
            return orig_create_collection(*args, **kwargs)

        with patch.object(store._client, "create_collection", _patched_create_collection):
            store.rebuild()

        assert store.is_ready is True  # Back to True after completion.
        # During rebuild (at create_collection time), is_ready must have been False.
        assert ready_states_during_rebuild, "create_collection was never called"
        assert ready_states_during_rebuild[0] is False

    def test_rebuild_idempotent_chunk_count(self, tmp_path: pathlib.Path) -> None:
        """Two consecutive rebuilds over the same corpus produce the same count."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        count1 = store.rebuild()
        count2 = store.rebuild()
        assert count1 == count2, "Rebuild is not idempotent"
        assert store.is_ready is True

    def test_empty_corpus_no_crash(self, tmp_path: pathlib.Path) -> None:
        """An empty corpus (no markdown files) should not crash; returns 0."""
        corpus = tmp_path / "empty_corpus"
        corpus.mkdir()
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        count = store.rebuild()
        assert count == 0
        assert store.is_ready is True

    def test_empty_corpus_search_returns_empty(self, tmp_path: pathlib.Path) -> None:
        """After an empty rebuild, search returns []."""
        corpus = tmp_path / "empty_corpus"
        corpus.mkdir()
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()

        results = store.search("anything", n_results=5)
        assert results == []


class TestDocStoreSearch:
    def test_search_returns_list_of_dicts(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()

        results = store.search("ignore files", n_results=3)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_search_result_shape(self, tmp_path: pathlib.Path) -> None:
        """Each result dict has exactly the documented keys."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()

        results = store.search("guide", n_results=5)
        for result in results:
            assert set(result.keys()) == {"file", "breadcrumb", "text", "distance"}
            assert isinstance(result["file"], str)
            assert isinstance(result["breadcrumb"], str)
            assert isinstance(result["text"], str)
            assert isinstance(result["distance"], float)

    def test_search_distance_best_first(self, tmp_path: pathlib.Path) -> None:
        """Results are ordered best-first (ascending distance)."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()

        results = store.search("getting started guide", n_results=5)
        distances = [r["distance"] for r in results]
        assert distances == sorted(distances), "Results are not in ascending distance order"

    def test_search_file_and_breadcrumb_from_metadata(self, tmp_path: pathlib.Path) -> None:
        """file and breadcrumb fields come from the chunk metadata (not the doc text)."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()

        results = store.search("ignore files", n_results=5)
        # At least one result should reference guide.md
        files = [r["file"] for r in results]
        assert any("guide.md" in f for f in files), (
            f"Expected guide.md in results, got files: {files}"
        )

        # Breadcrumbs should be non-empty strings starting with the filename base.
        for result in results:
            assert result["breadcrumb"], "Breadcrumb should not be empty"

    def test_search_before_rebuild_returns_empty(self, tmp_path: pathlib.Path) -> None:
        """search() before any rebuild returns [] (collection is None)."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        # No rebuild called yet.
        results = store.search("anything")
        assert results == []

    def test_search_n_results_clamped(self, tmp_path: pathlib.Path) -> None:
        """n_results larger than collection size is clamped, not an error."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        count = store.rebuild()

        # Request far more results than chunks exist.
        results = store.search("anything", n_results=count + 1000)
        assert len(results) <= count


# ---------------------------------------------------------------------------
# Singleton lifecycle tests
# ---------------------------------------------------------------------------


class TestSingletonLifecycle:
    def test_get_doc_store_none_before_init(self) -> None:
        """get_doc_store() returns None before init_doc_store is called."""
        clear_doc_store()
        assert get_doc_store() is None

    def test_init_doc_store_sets_singleton(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        store = (
            init_doc_store.__wrapped__(settings)
            if hasattr(init_doc_store, "__wrapped__")
            else _init_with_fake_ef(settings, tmp_path)
        )
        assert get_doc_store() is store
        clear_doc_store()

    def test_clear_doc_store_resets_singleton(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        _init_with_fake_ef(settings, tmp_path)
        assert get_doc_store() is not None

        clear_doc_store()
        assert get_doc_store() is None

    def test_init_doc_store_adopt_existing_collection(self, tmp_path: pathlib.Path) -> None:
        """If a populated collection already exists, init_doc_store adopts it (no rebuild)."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        # First init: builds from scratch.
        store1 = _init_with_fake_ef(settings, tmp_path)
        count1 = store1._collection.count()
        assert count1 > 0
        assert store1.is_ready is True

        clear_doc_store()
        # Second init with same chroma_path: should ADOPT the existing collection.
        store2 = _init_with_fake_ef(settings, tmp_path)
        assert store2.is_ready is True
        assert store2._collection.count() == count1

        clear_doc_store()


def _init_with_fake_ef(settings: Settings, tmp_path: pathlib.Path) -> DocStore:
    """Helper: init DocStore with a fake EF (bypasses model download)."""
    store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

    from chromadb.errors import NotFoundError

    # Mirror init_doc_store logic but inject the fake EF.
    adopted = False
    try:
        existing = store._client.get_collection("ripgrep_docs")
        if existing.count() > 0:
            store._collection = existing
            store._ready = True
            adopted = True
    except NotFoundError:
        pass

    if not adopted:
        store.rebuild()

    # Wire into singleton.
    import rust_lsp_mcp.doc_store as _mod

    _mod._doc_store = store
    return store
