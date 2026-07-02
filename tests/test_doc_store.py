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
import threading
from typing import Any
from unittest.mock import patch

import chromadb
import numpy as np
import pytest
from chromadb.config import Settings as ChromaSettings

from rust_lsp_mcp.doc_store import (
    DOC_STATE_BUILDING,
    DocStore,
    DocStoreNotReady,
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
        project_root=str(corpus_dir),
        doc_glob_patterns="**/*.md",
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

    def test_rebuild_failure_sets_error_state(self, tmp_path: pathlib.Path) -> None:
        """A rebuild() that raises must set state="error" (not leave it "building").

        error_message must carry the exception text; the exception itself must
        still propagate (rebuild() re-raises after recording it).
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        with (
            patch("rust_lsp_mcp.doc_store.chunk_markdown", side_effect=RuntimeError("chunk boom")),
            pytest.raises(RuntimeError, match="chunk boom"),
        ):
            store.rebuild()

        assert store.state == "error"
        assert store.is_ready is False
        assert store.error_message == "RuntimeError: chunk boom"

    def test_rebuild_failure_then_success_clears_error(self, tmp_path: pathlib.Path) -> None:
        """A subsequent successful rebuild() must clear the error state."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        with (
            patch("rust_lsp_mcp.doc_store.chunk_markdown", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            store.rebuild()
        assert store.state == "error"

        store.rebuild()
        assert store.state == "ready"
        assert store.is_ready is True
        assert store.error_message is None

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

    def test_search_before_rebuild_raises_not_ready(self, tmp_path: pathlib.Path) -> None:
        """search() before any rebuild raises DocStoreNotReady (collection is None).

        Contract change (DS-12): previously this returned [] (a misleading
        "empty" answer indistinguishable from a genuinely empty collection).
        Now it raises DocStoreNotReady so callers (search_docs) can
        distinguish "not ready yet" from "ready and genuinely empty".
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        # No rebuild called yet.
        with pytest.raises(DocStoreNotReady):
            store.search("anything")

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
# DS-12: search()/rebuild() atomic-snapshot race regression tests.
# ---------------------------------------------------------------------------


class TestDS12ReadWriteRace:
    """Regression tests for the DS-12 refresh/search race.

    A fast-path ``is_ready`` check on the event loop followed by an
    ``await run_sync(store.search)`` on a worker thread left a window where a
    concurrent ``rebuild()`` could flip ``state``/``_collection`` mid-search,
    producing a misleading empty/partial/errored result.  These tests exercise
    the fix: ``search()`` takes an atomic snapshot under ``_read_lock`` and
    raises ``DocStoreNotReady`` instead.
    """

    def test_search_raises_when_state_building(self, tmp_path: pathlib.Path) -> None:
        """search() on a store with _state=BUILDING raises DocStoreNotReady."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()
        assert store.is_ready is True

        # Simulate "mid-rebuild": force state back to BUILDING without
        # touching _collection (mirrors the moment right after the start
        # transition, before _collection is cleared is irrelevant here — the
        # state check alone must be sufficient to reject).
        store._state = DOC_STATE_BUILDING

        with pytest.raises(DocStoreNotReady):
            store.search("anything")

    def test_search_raises_when_collection_none(self, tmp_path: pathlib.Path) -> None:
        """search() with _collection=None raises DocStoreNotReady even if
        _state were (incorrectly) READY — belt-and-suspenders on the guard.
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()
        assert store.is_ready is True

        store._collection = None

        with pytest.raises(DocStoreNotReady):
            store.search("anything")

    def test_search_on_ready_store_returns_results(self, tmp_path: pathlib.Path) -> None:
        """search() on a genuinely ready store still returns results normally."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()

        results = store.search("ignore files", n_results=3)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_rebuild_keeps_collection_none_until_publish(self, tmp_path: pathlib.Path) -> None:
        """_rebuild_locked must not assign self._collection until the end
        transition — a patched create_collection/add observes self._collection
        is None throughout the entire build body.
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        observed_during_create: list[Any] = []
        observed_during_add: list[Any] = []

        orig_create_collection = store._client.create_collection

        def _patched_create_collection(*args: Any, **kwargs: Any) -> Any:
            observed_during_create.append(store._collection)
            collection = orig_create_collection(*args, **kwargs)
            orig_add = collection.add

            def _patched_add(*a: Any, **kw: Any) -> Any:
                observed_during_add.append(store._collection)
                return orig_add(*a, **kw)

            collection.add = _patched_add  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
            return collection

        with patch.object(store._client, "create_collection", _patched_create_collection):
            store.rebuild()

        assert observed_during_create == [None]
        assert observed_during_add, "add() was never called (corpus should be non-empty)"
        assert all(c is None for c in observed_during_add)
        # After the rebuild completes, the collection is published.
        assert store._collection is not None
        assert store.is_ready is True

    def test_concurrent_rebuild_pauses_search_sees_not_ready(self, tmp_path: pathlib.Path) -> None:
        """Real 2-thread transition-visibility test: once a rebuild() has
        completed its start transition (state=BUILDING, collection=None) and
        released _read_lock, a concurrent search() OBSERVES that transition and
        raises DocStoreNotReady rather than returning a misleading
        empty/partial result.  (The complementary lock-mutual-exclusion
        property — that search holding _read_lock blocks the start transition —
        is covered by test_concurrent_search_blocks_rebuild_start_transition.)
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()
        assert store.is_ready is True

        rebuild_paused = threading.Event()
        release_rebuild = threading.Event()

        orig_create_collection = store._client.create_collection

        def _patched_create_collection(*args: Any, **kwargs: Any) -> Any:
            # By the time create_collection is called, the start transition
            # (state=BUILDING, collection=None, delete_collection) has already
            # completed and _read_lock has been released.
            rebuild_paused.set()
            assert release_rebuild.wait(timeout=5), "test deadlocked waiting for release"
            return orig_create_collection(*args, **kwargs)

        rebuild_result: dict[str, Any] = {}

        def _run_rebuild() -> None:
            with patch.object(store._client, "create_collection", _patched_create_collection):
                rebuild_result["count"] = store.rebuild()

        rebuild_thread = threading.Thread(target=_run_rebuild)
        rebuild_thread.start()
        try:
            assert rebuild_paused.wait(timeout=5), "rebuild never reached the paused point"

            with pytest.raises(DocStoreNotReady):
                store.search("anything")
        finally:
            release_rebuild.set()
            rebuild_thread.join(timeout=5)
            assert not rebuild_thread.is_alive(), "rebuild thread did not finish"

        assert store.is_ready is True
        assert rebuild_result["count"] > 0

    def test_concurrent_search_blocks_rebuild_start_transition(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A search() holding _read_lock mid-query blocks a concurrent
        rebuild's start transition until the search finishes — the search
        must see a consistent (not half-deleted) collection and return
        results, never a NotFoundError from a collection deleted underneath it.
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()
        assert store.is_ready is True

        query_started = threading.Event()
        release_query = threading.Event()

        collection = store._collection
        orig_query = collection.query

        def _patched_query(*args: Any, **kwargs: Any) -> Any:
            query_started.set()
            assert release_query.wait(timeout=5), "test deadlocked waiting for release"
            return orig_query(*args, **kwargs)

        search_result: dict[str, Any] = {}

        def _run_search() -> None:
            with patch.object(collection, "query", _patched_query):
                search_result["results"] = store.search("ignore files", n_results=3)

        search_thread = threading.Thread(target=_run_search)
        search_thread.start()
        try:
            assert query_started.wait(timeout=5), "search never reached the paused point"

            # Start a rebuild concurrently — its start transition (which
            # deletes the collection) must block until the search above
            # releases _read_lock.
            rebuild_result: dict[str, Any] = {}
            rebuild_thread = threading.Thread(
                target=lambda: rebuild_result.__setitem__("count", store.rebuild())
            )
            rebuild_thread.start()
            try:
                # Give the rebuild thread a moment to attempt (and block on)
                # _read_lock; the search must still be holding it.
                rebuild_thread.join(timeout=0.3)
                assert rebuild_thread.is_alive(), (
                    "rebuild should still be blocked on _read_lock while search holds it"
                )
            finally:
                release_query.set()
                rebuild_thread.join(timeout=5)
                assert not rebuild_thread.is_alive(), "rebuild thread did not finish"
        finally:
            search_thread.join(timeout=5)
            assert not search_thread.is_alive(), "search thread did not finish"

        # The search that was in flight during the rebuild's attempted start
        # must have returned consistent, real results — not an exception from
        # a collection deleted underneath it.
        assert search_result["results"], "search must have returned real results"
        assert rebuild_result["count"] > 0
        assert store.is_ready is True


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
        store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert get_doc_store() is store
        clear_doc_store()

    def test_clear_doc_store_resets_singleton(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert get_doc_store() is not None

        clear_doc_store()
        assert get_doc_store() is None

    def test_init_doc_store_adopt_existing_collection(self, tmp_path: pathlib.Path) -> None:
        """Populated collection WITH build_complete sentinel is adopted (no rebuild)."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        # First init: builds from scratch, writes build_complete sentinel.
        store1 = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        count1 = store1._collection.count()
        assert count1 > 0
        assert store1.is_ready is True
        # Verify the sentinel was written.
        meta = store1._collection.metadata or {}
        assert meta.get("build_complete") is True, (
            "rebuild() must write build_complete sentinel before marking ready"
        )

        clear_doc_store()
        # Second init with same chroma_path: should ADOPT (sentinel present).
        store2 = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert store2.is_ready is True
        assert store2._collection.count() == count1

        clear_doc_store()

    def test_init_doc_store_adopts_intentionally_empty_corpus(self, tmp_path: pathlib.Path) -> None:
        """DS-24: an intentionally-empty completed corpus (0 markdown files) is
        ADOPTED on the second init, not rebuilt every startup.

        Before the fix, the adopt gate required ``count() > 0`` — a count-0
        collection can never satisfy that, so ``rebuild()`` (writing nothing,
        just the sentinel) ran on EVERY startup even though the empty corpus
        never changes. The gate now relies solely on the ``build_complete``
        sentinel + project_root fingerprint, matching what the empty-corpus
        path in ``_rebuild_locked`` actually writes.
        """
        corpus = tmp_path / "empty_corpus"
        corpus.mkdir()
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        # First init: rebuild() runs, finds 0 markdown files, writes the
        # build_complete sentinel + project_root fingerprint anyway.
        store1 = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert store1.is_ready is True
        assert store1._collection.count() == 0
        meta = store1._collection.metadata or {}
        assert meta.get("build_complete") is True, (
            "empty-corpus rebuild() must write the build_complete sentinel"
        )
        # search on an empty, ready store returns [] (-> the tool maps this to
        # not_found, per doc_store.search's documented contract).
        assert store1.search("anything", n_results=5) == []

        clear_doc_store()

        # Second init with the SAME settings: must ADOPT, not rebuild — spy on
        # rebuild() (mirrors test_same_project_root_adopts_without_rebuild).
        rebuild_calls: list[None] = []
        orig_rebuild = DocStore.rebuild

        def _spy_rebuild(self: DocStore) -> int:
            rebuild_calls.append(None)
            return orig_rebuild(self)

        with patch.object(DocStore, "rebuild", _spy_rebuild):
            store2 = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())

        assert store2.is_ready is True
        assert store2._collection.count() == 0
        assert rebuild_calls == [], (
            "An intentionally-empty completed corpus must be ADOPTED on the "
            "second init (DS-24) — rebuild() must not run again."
        )
        assert store2.search("anything", n_results=5) == []

        clear_doc_store()

    def test_interrupted_build_triggers_rebuild(self, tmp_path: pathlib.Path) -> None:
        """Regression: collection with rows but NO build_complete sentinel is NOT adopted.

        Simulates a hard-killed mid-build: rows exist but sentinel was never
        written.  init_doc_store must rebuild rather than serve partial results.
        This exercises the readiness invariant (no misleading partial answers).

        The partial collection is stamped with the CURRENT project's
        ``project_root`` fingerprint but WITHOUT ``build_complete`` — this
        isolates the sentinel conjunct in the adopt gate: the fingerprint
        matches, so the ONLY thing preventing adoption is the missing sentinel.
        Removing the ``and meta.get("build_complete")`` conjunct from
        init_doc_store would make this test fail.
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        # Simulate an interrupted build: create collection and add rows, but
        # deliberately skip the build_complete sentinel.
        fake_ef = FakeEmbeddingFunction()
        # Match DocStore's client settings (telemetry off) — ChromaDB caches one
        # system per path and requires subsequent clients to use identical settings.
        client = chromadb.PersistentClient(
            path=str(tmp_path / "chroma"),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        partial_col = client.create_collection(
            settings.doc_collection,
            embedding_function=fake_ef,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            configuration={"hnsw": {"space": "cosine"}},
        )
        partial_col.add(ids=["partial-1"], documents=["orphaned chunk from interrupted build"])
        # Stamp the CURRENT project's fingerprint but NOT build_complete, so the
        # fingerprint check passes and only the missing sentinel blocks adoption.
        current_fingerprint = str(pathlib.Path(settings.project_root).resolve())
        partial_col.modify(metadata={"project_root": current_fingerprint})
        # Confirm the fingerprint matches but the sentinel is absent.
        partial_meta = partial_col.metadata or {}
        assert partial_meta.get("project_root") == current_fingerprint
        assert partial_meta.get("build_complete") is None

        # Now call the real init_doc_store.
        clear_doc_store()
        store = init_doc_store(settings, embedding_function=fake_ef)

        # Must be ready (rebuild ran to completion).
        assert store.is_ready is True

        # The partial orphan document should NOT appear — collection was rebuilt.
        # After rebuild the corpus chunks should be present (not the "orphaned chunk").
        results = store.search("orphaned chunk from interrupted build", n_results=5)
        orphan_texts = [r["text"] for r in results if "orphaned chunk" in r["text"]]
        assert orphan_texts == [], (
            "Interrupted-build orphan survived; init_doc_store adopted instead of rebuilding"
        )

        # The rebuilt collection should have the sentinel.
        meta = store._collection.metadata or {}
        assert meta.get("build_complete") is True, (
            "Sentinel must be present after rebuild triggered by interrupted-build detection"
        )

        clear_doc_store()

    def test_zero_row_collection_without_sentinel_triggers_rebuild(
        self, tmp_path: pathlib.Path
    ) -> None:
        """DS-24 edge case: a 0-row collection with NO sentinel is NOT adopted.

        Distinct from ``test_init_doc_store_adopts_intentionally_empty_corpus``
        (0 rows WITH the sentinel, from a completed empty-corpus rebuild): this
        simulates a build interrupted before it wrote *any* rows (or before it
        reached the empty-corpus sentinel write) — the count-0 collection must
        still fall through to rebuild, exactly like the rows-but-no-sentinel
        case above. Confirms dropping the ``count() > 0`` conjunct did not
        accidentally make an unstamped, never-completed collection adoptable.
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)  # non-empty corpus — rebuild must actually index it.
        settings = _make_settings(tmp_path, corpus)

        fake_ef = FakeEmbeddingFunction()
        client = chromadb.PersistentClient(
            path=str(tmp_path / "chroma"),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # Create the collection but add no rows and no sentinel at all.
        empty_col = client.create_collection(
            settings.doc_collection,
            embedding_function=fake_ef,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            configuration={"hnsw": {"space": "cosine"}},
        )
        empty_meta = empty_col.metadata or {}
        assert empty_col.count() == 0
        assert empty_meta.get("build_complete") is None

        clear_doc_store()
        store = init_doc_store(settings, embedding_function=fake_ef)

        assert store.is_ready is True
        # Real corpus content must have been indexed — proves rebuild() ran
        # rather than silently adopting the empty, unstamped collection.
        assert store._collection.count() > 0
        meta = store._collection.metadata or {}
        assert meta.get("build_complete") is True

        clear_doc_store()

    def test_init_doc_store_assigns_ready_singleton(self, tmp_path: pathlib.Path) -> None:
        """The singleton is assigned AND the returned store is ready.

        Distinct from test_init_doc_store_sets_singleton: that only checks the
        identity assignment; this additionally guards that init_doc_store does
        NOT publish a not-ready store as the singleton (a mutant that assigned
        the singleton before rebuild completed, leaving is_ready False, would
        pass the plain identity check but fail here).
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        published = get_doc_store()
        assert published is store
        assert published is not None
        assert published.is_ready is True
        clear_doc_store()

    def test_init_doc_store_sets_singleton_even_when_rebuild_raises(
        self, tmp_path: pathlib.Path
    ) -> None:
        """DS-08/DS-14: the singleton is set BEFORE rebuild(), so a failing rebuild
        still leaves a (now-errored) store reachable via get_doc_store() —
        never silently None.  init_doc_store itself must propagate the
        exception (it does not swallow rebuild failures).
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        with (
            patch("rust_lsp_mcp.doc_store.chunk_markdown", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            init_doc_store(settings, embedding_function=FakeEmbeddingFunction())

        published = get_doc_store()
        assert published is not None, (
            "Singleton must be set before rebuild() runs, even if rebuild() raises"
        )
        assert published.state == "error"
        assert published.error_message == "RuntimeError: boom"
        clear_doc_store()


# ---------------------------------------------------------------------------
# DS-05: cross-project contamination — collection identity checks.
# ---------------------------------------------------------------------------


class TestDocStoreCrossProjectIdentity:
    def test_repointing_project_root_does_not_adopt_other_project(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Repointing project_root at a DIFFERENT project must not adopt the
        previous project's collection, even with the same chroma_path/doc_collection.

        Regression for DS-05: pre-fix, init_doc_store adopted any collection
        with count() > 0 and build_complete set, with no identity check —
        searching project B's store silently returned project A's docs.
        """
        chroma_path = tmp_path / "chroma"

        corpus_a = tmp_path / "project_a"
        corpus_a.mkdir(parents=True, exist_ok=True)
        (corpus_a / "a.md").write_text(
            "# Project A\n\nThis mentions zzzunique_alpha_token in its docs.\n",
            encoding="utf-8",
        )
        settings_a = Settings(
            chroma_path=str(chroma_path),
            project_root=str(corpus_a),
            doc_glob_patterns="**/*.md",
        )

        corpus_b = tmp_path / "project_b"
        corpus_b.mkdir(parents=True, exist_ok=True)
        (corpus_b / "b.md").write_text(
            "# Project B\n\nThis mentions zzzunique_beta_token in its docs.\n",
            encoding="utf-8",
        )
        settings_b = Settings(
            chroma_path=str(chroma_path),
            project_root=str(corpus_b),
            doc_glob_patterns="**/*.md",
            doc_collection=settings_a.doc_collection,  # SAME collection name.
        )

        clear_doc_store()
        store_a = init_doc_store(settings_a, embedding_function=FakeEmbeddingFunction())
        assert store_a.is_ready is True
        results_a = store_a.search("zzzunique_alpha_token", n_results=5)
        assert any("zzzunique_alpha_token" in r["text"] for r in results_a), (
            "Project A's own store must find A's content"
        )
        clear_doc_store()

        # Repoint at a DIFFERENT project_root, same chroma_path + doc_collection.
        store_b = init_doc_store(settings_b, embedding_function=FakeEmbeddingFunction())
        assert store_b.is_ready is True

        results_b_for_beta = store_b.search("zzzunique_beta_token", n_results=5)
        assert any("zzzunique_beta_token" in r["text"] for r in results_b_for_beta), (
            "Project B's store must be rebuilt for B and find B's content"
        )

        results_b_for_alpha = store_b.search("zzzunique_alpha_token", n_results=5)
        alpha_hits = [r for r in results_b_for_alpha if "zzzunique_alpha_token" in r["text"]]
        assert alpha_hits == [], (
            "Project B's store must NOT contain project A's content — "
            "cross-project contamination (DS-05)"
        )

        clear_doc_store()

    def test_same_project_root_adopts_without_rebuild(self, tmp_path: pathlib.Path) -> None:
        """Re-init with the SAME project_root + chroma_path adopts (build-once
        persistence is preserved) — rebuild() must NOT be called on the second init.
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        store1 = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert store1.is_ready is True
        clear_doc_store()

        rebuild_calls: list[None] = []
        orig_rebuild = DocStore.rebuild

        def _spy_rebuild(self: DocStore) -> int:
            rebuild_calls.append(None)
            return orig_rebuild(self)

        with patch.object(DocStore, "rebuild", _spy_rebuild):
            store2 = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())

        assert store2.is_ready is True
        assert rebuild_calls == [], (
            "Same project_root + chroma_path must adopt, not rebuild "
            "(build-once persistence, DS-05 scope)"
        )

        clear_doc_store()


# ---------------------------------------------------------------------------
# Exclude-patterns tests
# ---------------------------------------------------------------------------


class TestDocStoreExcludePatterns:
    def test_excluded_file_chunks_not_in_store(self, tmp_path: pathlib.Path) -> None:
        """Chunks from a file matching doc_exclude_patterns are NOT indexed."""
        corpus = tmp_path / "corpus"
        corpus.mkdir(parents=True, exist_ok=True)

        (corpus / "CHANGELOG.md").write_text(
            "# Changelog\n\n## v1.0.0\n\n- ignore files support added\n",
            encoding="utf-8",
        )
        (corpus / "guide.md").write_text(
            "# Guide\n\nHow to use ripgrep to ignore files.\n",
            encoding="utf-8",
        )

        settings = Settings(
            chroma_path=str(tmp_path / "chroma"),
            project_root=str(corpus),
            doc_glob_patterns="**/*.md",
            doc_exclude_patterns="**/CHANGELOG.md",
        )
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        count = store.rebuild()

        assert count > 0, "Expected chunks from guide.md"

        # None of the indexed chunks should reference CHANGELOG.md.
        all_results = store.search("ignore files", n_results=count + 10)
        changelog_chunks = [r for r in all_results if "CHANGELOG.md" in r["file"]]
        assert changelog_chunks == [], (
            f"Expected no CHANGELOG.md chunks in store, got: {changelog_chunks}"
        )

        # guide.md chunks should be present.
        guide_chunks = [r for r in all_results if "guide.md" in r["file"]]
        assert guide_chunks, "Expected guide.md chunks to be indexed"

    def test_empty_exclude_patterns_indexes_everything(self, tmp_path: pathlib.Path) -> None:
        """Empty doc_exclude_patterns means nothing is excluded — all files indexed."""
        corpus = tmp_path / "corpus"
        corpus.mkdir(parents=True, exist_ok=True)

        (corpus / "CHANGELOG.md").write_text(
            "# Changelog\n\n## v1.0.0\n\n- ignore files support added\n",
            encoding="utf-8",
        )
        (corpus / "guide.md").write_text(
            "# Guide\n\nHow to use ripgrep to ignore files.\n",
            encoding="utf-8",
        )

        settings = Settings(
            chroma_path=str(tmp_path / "chroma"),
            project_root=str(corpus),
            doc_glob_patterns="**/*.md",
            doc_exclude_patterns="",  # No exclusions.
        )
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        count = store.rebuild()

        # Both files should contribute chunks.
        all_results = store.search("ignore files", n_results=count + 10)
        files_found = {r["file"] for r in all_results}
        assert any("CHANGELOG.md" in f for f in files_found), (
            "Expected CHANGELOG.md to be indexed when doc_exclude_patterns is empty"
        )
        assert any("guide.md" in f for f in files_found), "Expected guide.md to be indexed"


def test_two_doc_stores_same_path_do_not_raise(tmp_path: pathlib.Path) -> None:
    """Two DocStores at the same chroma_path must not raise (adversarial regression).

    ChromaDB caches one System per path and rejects a second client opened with
    DIFFERENT settings ("An instance of Chroma already exists ... with different
    settings"). DocStore opens its PersistentClient with
    ChromaSettings(anonymized_telemetry=False); this guards against a future
    change that constructs a client at the same path with mismatched settings,
    which would crash lifespan startup. (Adversarial review of PR #12, attack 4.)
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    settings = _make_settings(tmp_path, corpus)

    store1 = DocStore(settings, embedding_function=FakeEmbeddingFunction())
    store2 = DocStore(settings, embedding_function=FakeEmbeddingFunction())
    assert store1._client is not None
    assert store2._client is not None
