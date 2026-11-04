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

import asyncio
import contextlib
import fcntl
import hashlib
import multiprocessing
import os
import pathlib
import threading
import time
from typing import Any
from unittest.mock import patch

import chromadb
import numpy as np
import pytest
from chromadb.config import Settings as ChromaSettings

import rust_lsp_mcp.doc_store as doc_store_mod
from rust_lsp_mcp.doc_store import (
    DOC_STATE_BUILDING,
    DOC_STATE_READY,
    DocStore,
    DocStoreNotReady,
    _project_fingerprint,
    clear_doc_store,
    doc_collection_name,
    doc_index_chunk_count,
    doc_store_digest,
    doc_store_files,
    doc_store_freshness,
    doc_store_state,
    get_doc_store,
    init_doc_store,
    init_doc_store_background,
    project_identity,
)
from rust_lsp_mcp.doc_store import (
    doc_index_progress as doc_index_progress_fn,
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

    def test_is_ready_stays_true_during_rebuild_over_live_index(
        self, tmp_path: pathlib.Path
    ) -> None:
        """SW-10: a rebuild over an existing index keeps ``state == "ready"``.

        The first build on an empty store reads ``"building"`` (captured at
        create_collection time); the second build, over a published live
        collection, stays ready throughout and builds into the staging name.
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        observed: list[tuple[str, bool]] = []
        orig_create_collection = store._client.create_collection

        def _patched_create_collection(name: str, *args: Any, **kwargs: Any) -> Any:
            observed.append((name, store.is_ready))
            return orig_create_collection(name, *args, **kwargs)

        live_name = doc_collection_name(settings)
        with patch.object(store._client, "create_collection", _patched_create_collection):
            store.rebuild()
            assert store.is_ready is True
            store.rebuild()

        assert observed == [(f"{live_name}_staging", False), (f"{live_name}_staging", True)]
        assert store.is_ready is True
        assert store._collection.name == live_name

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


# ---------------------------------------------------------------------------
# chunk_count() — UR-20: lock-safe live count(), None-vs-0 distinction.
# ---------------------------------------------------------------------------


class TestDocStoreChunkCount:
    def test_chunk_count_none_before_rebuild(self, tmp_path: pathlib.Path) -> None:
        """Not yet ready (state="building", never rebuilt) -> None, not 0."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        assert store.chunk_count() is None

    def test_chunk_count_zero_for_empty_ready_corpus(self, tmp_path: pathlib.Path) -> None:
        """DS-24: an intentionally-empty completed corpus reports 0, not None."""
        corpus = tmp_path / "empty_corpus"
        corpus.mkdir()
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        store.rebuild()
        assert store.is_ready is True
        assert store.chunk_count() == 0

    def test_chunk_count_positive_for_populated_corpus(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        count = store.rebuild()
        assert count > 0
        assert store.chunk_count() == count

    def test_chunk_count_none_after_error(self, tmp_path: pathlib.Path) -> None:
        """A failed rebuild leaves state="error" -> chunk_count() must be None."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())

        with (
            patch("rust_lsp_mcp.doc_store.chunk_markdown", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            store.rebuild()

        assert store.chunk_count() is None


class TestDocIndexChunkCountAccessor:
    """Module-level ``doc_index_chunk_count()`` — mirrors ``doc_store_state()``.

    Works on BOTH the rebuild path AND the DS-24 adopt path, since it reads
    ``collection.count()`` live rather than a value captured at build time.
    """

    def test_none_when_no_singleton(self) -> None:
        clear_doc_store()
        assert doc_index_chunk_count() is None

    def test_zero_on_adopted_empty_corpus(self, tmp_path: pathlib.Path) -> None:
        """Reuses the DS-24 empty-corpus fixture pattern: build once (writes the
        sentinel), clear the singleton, then adopt on a fresh init_doc_store —
        the adopt path never calls rebuild(), so this proves the accessor reads
        the live collection rather than a rebuild-time count."""
        corpus = tmp_path / "empty_corpus"
        corpus.mkdir()
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        store1 = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert store1._collection.count() == 0
        clear_doc_store()

        # Second init adopts (no rebuild) — see test_init_doc_store_adopts_
        # intentionally_empty_corpus for the rebuild-call spy proving this.
        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert doc_index_chunk_count() == 0

        clear_doc_store()

    def test_positive_on_populated_corpus(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        clear_doc_store()
        store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        expected = store._collection.count()
        assert expected > 0
        assert doc_index_chunk_count() == expected

        clear_doc_store()


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

    def test_search_text_is_body_only(self, tmp_path: pathlib.Path) -> None:
        """text carries the chunk body only — the breadcrumb prefix is stripped (UR-14).

        The embedded document is ``breadcrumb + "\\n\\n" + body``; search() must
        strip that prefix at read time so the breadcrumb is not shipped twice
        (once as the structured field, once as the prefix of text).
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()

        results = store.search("ignore files", n_results=10)
        assert len(results) > 0
        for result in results:
            bc = result["breadcrumb"]
            # (a) body-only: text must not re-ship the breadcrumb as a prefix.
            assert not result["text"].startswith(bc), (
                f"text still starts with its breadcrumb {bc!r}: {result['text']!r}"
            )
            # (c) the breadcrumb field itself is unchanged (heading trail form).
            assert bc.startswith("intro.md") or bc.startswith("guide.md"), (
                f"Unexpected breadcrumb shape: {bc!r}"
            )

        # The known guide.md body must survive the strip intact.
        bodies = [r["text"] for r in results]
        assert any("Use a .gitignore or --glob pattern to skip files." in b for b in bodies), (
            f"Expected a known body text among results, got: {bodies}"
        )

    def test_search_header_only_section_not_indexed(self, tmp_path: pathlib.Path) -> None:
        """SW-L19: a header-only section is not indexed, so no result has empty text.

        ``# Only A Header`` has no body of its own (the next line is a
        sub-header); it produces no chunk, and its title still reaches the
        sub-section's breadcrumb.  The body-only strip (UR-14) still applies.
        """
        corpus = tmp_path / "corpus"
        corpus.mkdir(parents=True, exist_ok=True)
        (corpus / "hdr.md").write_text(
            "# Only A Header\n\n## Sub With Body\n\nSome body content here.\n",
            encoding="utf-8",
        )
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        count = store.rebuild()
        assert count == 1  # the sub-section only

        results = store.search("anything", n_results=10)
        assert [r["breadcrumb"] for r in results] == ["hdr.md > Only A Header > Sub With Body"]
        assert results[0]["text"] == "Some body content here."
        assert all(r["text"] for r in results)

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

    def test_old_collection_answers_search_during_rebuild(self, tmp_path: pathlib.Path) -> None:
        """SW-10: while a rebuild is paused mid-build, search() answers from the
        old (live) collection with the steady-state results, never
        DocStoreNotReady; after the swap the new collection answers.
        """
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()
        assert store.is_ready is True
        steady = store.search("ignore files", n_results=3)
        assert steady
        old_collection = store._collection

        rebuild_paused = threading.Event()
        release_rebuild = threading.Event()
        orig_chunk = doc_store_mod.chunk_markdown

        def _slow_chunk(text: str, rel_path: str) -> Any:
            rebuild_paused.set()
            assert release_rebuild.wait(timeout=5), "test deadlocked waiting for release"
            return orig_chunk(text, rel_path)

        # A changed file forces re-chunking, so the rebuild reaches _slow_chunk.
        (corpus / "guide.md").write_text(
            "# Guide\n\nThe guide explains how to ignore files, revised.\n", encoding="utf-8"
        )
        rebuild_result: dict[str, Any] = {}

        def _run_rebuild() -> None:
            with patch.object(doc_store_mod, "chunk_markdown", _slow_chunk):
                rebuild_result["count"] = store.rebuild()

        rebuild_thread = threading.Thread(target=_run_rebuild)
        rebuild_thread.start()
        try:
            assert rebuild_paused.wait(timeout=5), "rebuild never reached the paused point"
            assert store.state == DOC_STATE_READY
            during = store.search("ignore files", n_results=3)
            assert during == steady
            assert store._collection is old_collection
            progress = store.progress()
            assert progress["phase"] == "embedding"
        finally:
            release_rebuild.set()
            rebuild_thread.join(timeout=5)
            assert not rebuild_thread.is_alive(), "rebuild thread did not finish"

        assert rebuild_result["count"] > 0
        assert store._collection is not old_collection
        after = store.search("revised", n_results=5)
        assert any("revised" in r["text"] for r in after)
        assert store.progress()["phase"] is None

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

        def _spy_rebuild(self: DocStore, **kwargs: Any) -> int:
            rebuild_calls.append(None)
            return orig_rebuild(self, **kwargs)

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
        # Create it under the LIVE (identity-derived) name, through the same
        # helper doc_store uses, so the adopt check really sees it.
        identity = project_identity(settings)
        partial_col = client.create_collection(
            doc_collection_name(settings, identity),
            embedding_function=fake_ef,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            configuration={"hnsw": {"space": "cosine"}},
        )
        partial_col.add(ids=["partial-1"], documents=["orphaned chunk from interrupted build"])
        # Stamp the CURRENT project's fingerprint but NOT build_complete, so the
        # fingerprint check passes and only the missing sentinel blocks adoption.
        current_fingerprint = f"{pathlib.Path(settings.project_root).resolve()}|{identity}"
        assert current_fingerprint == _project_fingerprint(settings)
        partial_col.modify(
            metadata={"project_root": current_fingerprint, "project_identity": identity}
        )
        # Confirm the fingerprint and identity match but the sentinel is absent.
        partial_meta = partial_col.metadata or {}
        assert partial_meta.get("project_root") == current_fingerprint
        assert partial_meta.get("project_identity") == identity
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
        # Under the LIVE (identity-derived) name, so adoption is really tested.
        empty_col = client.create_collection(
            doc_collection_name(settings),
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

        def _spy_rebuild(self: DocStore, **kwargs: Any) -> int:
            rebuild_calls.append(None)
            return orig_rebuild(self, **kwargs)

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


# ---------------------------------------------------------------------------
# SW-01: project identity in the adopt check.
# ---------------------------------------------------------------------------


class TestAdoptProjectIdentity:
    def _restamp_identity(self, settings: Settings, new_identity: str) -> None:
        """Overwrite the live collection's ``project_identity`` key, keeping the rest."""
        client = chromadb.PersistentClient(
            path=settings.chroma_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        col = client.get_collection(
            doc_collection_name(settings),
            embedding_function=FakeEmbeddingFunction(),  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        )
        meta = dict(col.metadata or {})
        assert meta.get("build_complete") is True
        assert meta.get("project_identity") == project_identity(settings)
        meta["project_identity"] = new_identity
        col.modify(metadata=meta)

    def _init_counting_rebuilds(self, settings: Settings) -> tuple[DocStore, int]:
        rebuild_calls: list[None] = []
        orig_rebuild = DocStore.rebuild

        def _spy_rebuild(self: DocStore, **kwargs: Any) -> int:
            rebuild_calls.append(None)
            return orig_rebuild(self, **kwargs)

        with patch.object(DocStore, "rebuild", _spy_rebuild):
            store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        return store, len(rebuild_calls)

    def test_build_stamps_project_identity(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        meta = store._collection.metadata or {}
        assert meta.get("project_identity") == project_identity(settings)
        assert meta.get("project_root") == _project_fingerprint(settings)
        assert store._collection.name == doc_collection_name(settings)

    def test_matching_identity_is_adopted(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        clear_doc_store()

        store, rebuilds = self._init_counting_rebuilds(settings)
        assert store.is_ready is True
        assert rebuilds == 0, "a collection whose project_identity matches must be adopted"

    def test_differing_identity_is_not_adopted(self, tmp_path: pathlib.Path) -> None:
        """Same name, same fingerprint, build_complete — but a different
        ``project_identity`` key: the identity conjunct alone blocks adoption."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        clear_doc_store()
        self._restamp_identity(settings, "some-other-project")

        store, rebuilds = self._init_counting_rebuilds(settings)
        assert store.is_ready is True
        assert rebuilds == 1, "a collection whose project_identity differs must be rebuilt"
        meta = store._collection.metadata or {}
        assert meta.get("project_identity") == project_identity(settings)


# ---------------------------------------------------------------------------
# SW-04: the commit and digest stamp, the adopt check and the freshness query.
# ---------------------------------------------------------------------------


def _bump_file(path: pathlib.Path, text: str) -> None:
    """Rewrite *path* with *text* and move its mtime forward by one second.

    The explicit mtime step keeps the digest change independent of the
    filesystem's timestamp granularity (the size changes too, but a test must
    not rely on that alone).
    """
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text, encoding="utf-8")
    after = before + 1_000_000_000
    os.utime(path, ns=(after, after))


class TestDocStoreFreshnessStamp:
    def _restamp(self, settings: Settings, key: str, value: str) -> None:
        """Overwrite one metadata key on the live collection, keeping the rest."""
        client = chromadb.PersistentClient(
            path=settings.chroma_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        col = client.get_collection(
            doc_collection_name(settings),
            embedding_function=FakeEmbeddingFunction(),  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        )
        meta = dict(col.metadata or {})
        assert meta.get("build_complete") is True
        assert key in meta
        meta[key] = value
        col.modify(metadata=meta)

    def _init_counting_rebuilds(self, settings: Settings) -> tuple[DocStore, int]:
        rebuild_calls: list[None] = []
        orig_rebuild = DocStore.rebuild

        def _spy_rebuild(self: DocStore, **kwargs: Any) -> int:
            rebuild_calls.append(None)
            return orig_rebuild(self, **kwargs)

        with patch.object(DocStore, "rebuild", _spy_rebuild):
            store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        return store, len(rebuild_calls)

    def _built(self, tmp_path: pathlib.Path) -> Settings:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        clear_doc_store()
        return settings

    def test_build_stamps_commit_and_digest(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        meta = store._collection.metadata or {}
        # The corpus is not a git repository, so git fails and "none" is stamped.
        assert meta.get("indexed_commit") == "none"
        assert meta.get("doc_digest") == doc_store_digest(settings)
        assert len(str(meta.get("doc_digest"))) == 16

    def test_build_stamps_git_head(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        with patch("rust_lsp_mcp.doc_store._doc_head_commit", return_value="abc123"):
            store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert (store._collection.metadata or {}).get("indexed_commit") == "abc123"

    def test_adopt_accepted_when_commit_and_digest_match(self, tmp_path: pathlib.Path) -> None:
        settings = self._built(tmp_path)
        store, rebuilds = self._init_counting_rebuilds(settings)
        assert store.is_ready is True
        assert rebuilds == 0, "a collection whose commit and digest match must be adopted"

    def test_adopt_refused_when_indexed_commit_differs(self, tmp_path: pathlib.Path) -> None:
        settings = self._built(tmp_path)
        self._restamp(settings, "indexed_commit", "0" * 40)

        store, rebuilds = self._init_counting_rebuilds(settings)
        assert rebuilds == 1, "a collection built at another commit must be rebuilt"
        assert store.is_ready is True
        assert (store._collection.metadata or {}).get("indexed_commit") == "none"

    def test_adopt_refused_when_doc_digest_differs(self, tmp_path: pathlib.Path) -> None:
        settings = self._built(tmp_path)
        self._restamp(settings, "doc_digest", "0" * 16)

        store, rebuilds = self._init_counting_rebuilds(settings)
        assert rebuilds == 1, "a collection built from other doc contents must be rebuilt"
        assert (store._collection.metadata or {}).get("doc_digest") == doc_store_digest(settings)

    def test_adopt_refused_after_doc_file_changes(self, tmp_path: pathlib.Path) -> None:
        settings = self._built(tmp_path)
        _bump_file(pathlib.Path(settings.project_root) / "guide.md", "# Guide\n\nChanged.\n")

        _, rebuilds = self._init_counting_rebuilds(settings)
        assert rebuilds == 1

    def test_adopt_refused_for_pre_stamp_collection(self, tmp_path: pathlib.Path) -> None:
        """A collection without the SW-04 keys compares unequal and rebuilds once."""
        settings = self._built(tmp_path)
        client = chromadb.PersistentClient(
            path=settings.chroma_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        col = client.get_collection(
            doc_collection_name(settings),
            embedding_function=FakeEmbeddingFunction(),  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        )
        meta = {
            k: v
            for k, v in (col.metadata or {}).items()
            if k not in ("indexed_commit", "doc_digest")
        }
        col.modify(metadata=meta)

        _, first = self._init_counting_rebuilds(settings)
        clear_doc_store()
        _, second = self._init_counting_rebuilds(settings)
        assert (first, second) == (1, 0)

    def test_digest_stable_without_change(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        assert doc_store_digest(settings) == doc_store_digest(settings)

    def test_digest_changes_when_matched_file_changes(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        before = doc_store_digest(settings)
        _bump_file(corpus / "intro.md", "# Introduction\n\nRewritten.\n")
        assert doc_store_digest(settings) != before

    def test_digest_changes_when_matched_file_added_or_removed(
        self, tmp_path: pathlib.Path
    ) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        before = doc_store_digest(settings)
        (corpus / "new.md").write_text("# New\n\nAdded doc.\n", encoding="utf-8")
        added = doc_store_digest(settings)
        assert added != before
        (corpus / "intro.md").unlink()
        assert doc_store_digest(settings) not in (before, added)

    def test_digest_ignores_excluded_file(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        (corpus / "CHANGELOG.md").write_text("# Changelog\n\n- v1\n", encoding="utf-8")
        settings = Settings(
            chroma_path=str(tmp_path / "chroma"),
            project_root=str(corpus),
            doc_glob_patterns="**/*.md",
            doc_exclude_patterns="**/CHANGELOG.md",
        )
        before = doc_store_digest(settings)
        _bump_file(corpus / "CHANGELOG.md", "# Changelog\n\n- v1\n- v2 with more text\n")
        assert doc_store_digest(settings) == before

    def test_doc_store_files_matches_indexed_files(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        (corpus / "sub").mkdir()
        (corpus / "sub" / "deep.md").write_text("# Deep\n\nNested doc.\n", encoding="utf-8")
        (corpus / "CHANGELOG.md").write_text("# Changelog\n\n- v1\n", encoding="utf-8")
        (corpus / "dir.md").mkdir()  # a directory matching the glob is not a file
        settings = Settings(
            chroma_path=str(tmp_path / "chroma"),
            project_root=str(corpus),
            doc_glob_patterns="**/*.md,*.md",  # overlapping patterns: de-duplicated
            doc_exclude_patterns="**/CHANGELOG.md",
        )
        files = doc_store_files(settings)
        rel = [f.relative_to(corpus).as_posix() for f in files]
        assert rel == ["guide.md", "intro.md", "sub/deep.md"]

        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        count = store.rebuild()
        indexed = {r["file"] for r in store.search("doc", n_results=count)}
        assert indexed == set(rel)

    def test_freshness_none_without_ready_store(self, tmp_path: pathlib.Path) -> None:
        clear_doc_store()
        assert doc_store_freshness() == (None, None)

    def test_freshness_false_then_true_after_doc_change(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert doc_store_freshness() == ("none", False)

        _bump_file(corpus / "guide.md", "# Guide\n\nEdited after the build.\n")
        assert doc_store_freshness() == ("none", True)

    def test_freshness_true_when_head_moves(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        with patch("rust_lsp_mcp.doc_store._doc_head_commit", return_value="aaa"):
            init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
            assert doc_store_freshness() == ("aaa", False)
        with patch("rust_lsp_mcp.doc_store._doc_head_commit", return_value="bbb"):
            assert doc_store_freshness() == ("aaa", True)


# ---------------------------------------------------------------------------
# SW-02: the inter-process doc-store lock.
# ---------------------------------------------------------------------------

# Seconds the spawn-side stub embedding function sleeps per call, so the
# first process's build holds the lock long enough for the second to wait.
_SPAWN_EF_SLEEP_SECONDS = 1.0
_spawn_ef_calls: list[float] = []


class _SpawnCountingEmbeddingFunction(FakeEmbeddingFunction):
    """Module-level (spawn-importable) stub EF that records each call's time."""

    def __call__(self, input: chromadb.api.types.Documents) -> chromadb.api.types.Embeddings:
        _spawn_ef_calls.append(time.monotonic())
        time.sleep(_SPAWN_EF_SLEEP_SECONDS)
        return super().__call__(input)

    @staticmethod
    def name() -> str:
        return "fake-deterministic-spawn-counting"

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> _SpawnCountingEmbeddingFunction:
        return _SpawnCountingEmbeddingFunction()


def _sw02_worker(chroma_path: str, project_root: str, barrier: Any, results: Any) -> None:
    """Child process: start the doc store against a shared chroma_path, report back."""
    try:
        settings = Settings(
            chroma_path=chroma_path, project_root=project_root, doc_glob_patterns="**/*.md"
        )
        barrier.wait(timeout=60)
        started = time.monotonic()
        store = init_doc_store(settings, embedding_function=_SpawnCountingEmbeddingFunction())
        deadline = time.monotonic() + 30
        while store.state != DOC_STATE_READY and time.monotonic() < deadline:
            time.sleep(0.05)
        results.put(
            {
                "pid": os.getpid(),
                "state": store.state,
                "error": store.error_message,
                "chunks": store.chunk_count(),
                "ef_calls": len(_spawn_ef_calls),
                "first_ef_call": _spawn_ef_calls[0] if _spawn_ef_calls else None,
                "started": started,
                "finished": time.monotonic(),
            }
        )
    except BaseException as exc:  # report, never hang the parent
        results.put({"pid": os.getpid(), "state": "crashed", "error": repr(exc)})


def _flock_holder(lock_path: str, ready: Any, release: Any) -> None:
    """Child process: hold the doc-store flock until *release* is set."""
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        ready.set()
        release.wait(timeout=60)
    finally:
        os.close(fd)


class TestDocStoreStartLock:
    def test_two_processes_on_fresh_volume_serialise_and_one_rebuilds(
        self, tmp_path: pathlib.Path
    ) -> None:
        """SW-02 regression: two processes start together on one empty
        chroma_path with the same identity.  Both end ``ready`` with equal
        chunk counts and no error; the second waits on the lock, then adopts
        the first one's collection without rebuilding (exactly one process
        ever calls the embedding function)."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        chroma_path = str(tmp_path / "chroma")  # not created: first start.

        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        results = ctx.Queue()
        procs = [
            ctx.Process(target=_sw02_worker, args=(chroma_path, str(corpus), barrier, results))
            for _ in range(2)
        ]
        for proc in procs:
            proc.start()
        try:
            reports = [results.get(timeout=90) for _ in procs]
        finally:
            for proc in procs:
                proc.join(timeout=30)
                if proc.is_alive():
                    proc.kill()
                    proc.join()

        for report in reports:
            assert report["state"] == DOC_STATE_READY, report
            assert report["error"] is None, report
        assert reports[0]["chunks"] == reports[1]["chunks"]
        assert reports[0]["chunks"] > 0

        builders = [r for r in reports if r["ef_calls"] > 0]
        adopters = [r for r in reports if r["ef_calls"] == 0]
        assert len(builders) == 1 and len(adopters) == 1, (
            f"exactly one process must rebuild; the other must adopt: {reports}"
        )
        builder, adopter = builders[0], adopters[0]
        # The adopter waited on the lock: it finished only after the builder's
        # embedding pass had started (it could not adopt before the build
        # completed and wrote the sentinel).
        assert adopter["finished"] >= builder["first_ef_call"] + _SPAWN_EF_SLEEP_SECONDS * 0.5, (
            reports
        )

    def test_uncreated_chroma_path_is_created_with_lock_file(self, tmp_path: pathlib.Path) -> None:
        """The lock step creates chroma_path itself (the Dockerfile only creates
        /data), so a fresh first start does not fail with ENOENT."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        chroma_path = tmp_path / "fresh" / "chroma"
        assert not chroma_path.exists()
        settings = Settings(
            chroma_path=str(chroma_path), project_root=str(corpus), doc_glob_patterns="**/*.md"
        )

        store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())

        assert store.state == DOC_STATE_READY
        assert chroma_path.is_dir()
        assert (chroma_path / ".rlm-doc-store.lock").exists()

    def test_background_start_does_not_block_on_held_lock(self, tmp_path: pathlib.Path) -> None:
        """While another process holds the doc-store lock, an awaited
        ``init_doc_store_background`` returns at once and the state reads
        ``building``; once the holder releases, the background task finishes
        and the state reaches ``ready``."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        chroma_path = tmp_path / "chroma"
        chroma_path.mkdir()
        settings = Settings(
            chroma_path=str(chroma_path), project_root=str(corpus), doc_glob_patterns="**/*.md"
        )

        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        release = ctx.Event()
        holder = ctx.Process(
            target=_flock_holder,
            args=(str(chroma_path / ".rlm-doc-store.lock"), ready, release),
        )
        holder.start()
        try:
            assert ready.wait(timeout=60), "fake lock holder never acquired the flock"

            async def _scenario() -> None:
                try:
                    started = time.monotonic()
                    await init_doc_store_background(
                        settings, embedding_function=FakeEmbeddingFunction()
                    )
                    assert time.monotonic() - started < 1.0
                    assert doc_store_state() == (DOC_STATE_BUILDING, None)
                    # Still blocked on the held lock a moment later.
                    await asyncio.sleep(0.3)
                    assert doc_store_state() == (DOC_STATE_BUILDING, None)
                    assert get_doc_store() is None
                finally:
                    # Always release, or the worker thread would keep
                    # asyncio.run's executor shutdown waiting.
                    release.set()
                import rust_lsp_mcp.doc_store as doc_store_mod

                task = doc_store_mod._build_task
                assert task is not None
                await asyncio.wait_for(task, timeout=30)
                assert doc_store_state() == (DOC_STATE_READY, None)

            asyncio.run(_scenario())
        finally:
            release.set()
            holder.join(timeout=30)
            if holder.is_alive():
                holder.kill()
                holder.join()


class TestRebuildTakesFlock:
    def test_rebuild_without_lock_held_waits_for_flock(self, tmp_path: pathlib.Path) -> None:
        """The ``refresh`` path: ``rebuild()`` called without ``lock_held`` on a
        live store takes the inter-process flock itself, so it waits while
        another process holds the lock and completes once it is released."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert store.state == DOC_STATE_READY

        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        release = ctx.Event()
        holder = ctx.Process(
            target=_flock_holder,
            args=(str(pathlib.Path(settings.chroma_path) / ".rlm-doc-store.lock"), ready, release),
        )
        holder.start()
        done = threading.Event()
        outcome: dict[str, Any] = {}

        def _refresh_rebuild() -> None:
            try:
                outcome["count"] = store.rebuild()
            except BaseException as exc:  # surfaced by the asserts below
                outcome["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(target=_refresh_rebuild, daemon=True)
        try:
            assert ready.wait(timeout=60), "fake lock holder never acquired the flock"
            worker.start()
            assert not done.wait(timeout=0.5), (
                "rebuild() without lock_held finished while another process held the "
                "doc-store flock — the refresh path must take the flock"
            )
        finally:
            release.set()
            holder.join(timeout=30)
            if holder.is_alive():
                holder.kill()
                holder.join()
        assert done.wait(timeout=30), "rebuild() never completed after the flock was released"
        assert "error" not in outcome, outcome
        assert outcome["count"] > 0
        assert store.state == DOC_STATE_READY


# ---------------------------------------------------------------------------
# 2026-11-03 remediation T04: build-and-swap (SW-10), incremental re-embed,
# shutdown (SW-L14) and the doc-search low rows SW-L13, L15, L21 to L24.
# ---------------------------------------------------------------------------


class _CountingEmbeddingFunction(FakeEmbeddingFunction):
    """FakeEmbeddingFunction that records every document it embeds."""

    def __init__(self) -> None:
        super().__init__()
        self.embedded: list[str] = []

    def __call__(self, input: chromadb.api.types.Documents) -> chromadb.api.types.Embeddings:
        self.embedded.extend(input)
        return super().__call__(input)


class _OtherModelEmbeddingFunction(FakeEmbeddingFunction):
    """Same family (``name()``) as FakeEmbeddingFunction, another model (SW-L13).

    ChromaDB compares only ``name()`` when a collection is reopened, so a
    model swap inside one embedding-function family passes its check; the
    ``embedding_model`` stamp is what refuses it.
    """

    MODEL_NAME = "fake-other-model"

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> _OtherModelEmbeddingFunction:
        return _OtherModelEmbeddingFunction()


def _collection_names(chroma_path: str) -> set[str]:
    client = chromadb.PersistentClient(
        path=chroma_path, settings=ChromaSettings(anonymized_telemetry=False)
    )
    return {c.name for c in client.list_collections()}


def _on_disk_count(chroma_path: str, name: str) -> int:
    client = chromadb.PersistentClient(
        path=chroma_path, settings=ChromaSettings(anonymized_telemetry=False)
    )
    return client.get_collection(name, embedding_function=FakeEmbeddingFunction()).count()  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


class TestBuildAndSwap:
    """SW-10: the rebuild builds into ``<live>_staging`` and swaps on success."""

    def test_failed_rebuild_keeps_old_collection_and_count(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        count1 = store.rebuild()
        old_collection = store._collection
        steady = store.search("ignore files", n_results=3)

        # A changed file is re-chunked, which is where the failure is injected.
        (corpus / "intro.md").write_text("# Intro\n\nChanged.\n", encoding="utf-8")
        with (
            patch.object(doc_store_mod, "chunk_markdown", side_effect=RuntimeError("chunk boom")),
            pytest.raises(RuntimeError, match="chunk boom"),
        ):
            store.rebuild()

        live_name = doc_collection_name(settings)
        assert store.state == DOC_STATE_READY
        assert store.error_message == "RuntimeError: chunk boom"
        assert store.state_snapshot() == (DOC_STATE_READY, "RuntimeError: chunk boom")
        assert store._collection is old_collection
        assert store.chunk_count() == count1
        assert store.search("ignore files", n_results=3) == steady
        assert _collection_names(settings.chroma_path) == {live_name}
        assert _on_disk_count(settings.chroma_path, live_name) == count1

    def test_rename_failure_after_live_delete_reads_error(self, tmp_path: pathlib.Path) -> None:
        """If the rename fails after the live collection was deleted, the store
        stops serving the deleted collection and reads ``error``."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()
        orig_create = store._client.create_collection

        def _create_with_failing_rename(*args: Any, **kwargs: Any) -> Any:
            collection = orig_create(*args, **kwargs)
            orig_modify = collection.modify

            def _modify(*a: Any, **kw: Any) -> Any:
                if "name" in kw:
                    raise RuntimeError("rename failed")
                return orig_modify(*a, **kw)

            collection.modify = _modify  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
            return collection

        with (
            patch.object(store._client, "create_collection", _create_with_failing_rename),
            pytest.raises(RuntimeError, match="rename failed"),
        ):
            store.rebuild()
        assert store.state_snapshot() == ("error", "RuntimeError: rename failed")
        assert store._collection is None
        with pytest.raises(DocStoreNotReady):
            store.search("anything")

    def test_leftover_staging_collection_is_replaced(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        live_name = doc_collection_name(settings)
        leftover = store._client.create_collection(
            f"{live_name}_staging",
            embedding_function=FakeEmbeddingFunction(),  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        )
        leftover.add(ids=["orphan"], documents=["orphan from an interrupted rebuild"])

        count = store.rebuild()

        assert _collection_names(settings.chroma_path) == {live_name}
        assert store._collection.count() == count
        assert store._collection.get(ids=["orphan"])["ids"] == []

    def test_swap_writes_stamps_and_per_chunk_hash(self, tmp_path: pathlib.Path) -> None:
        """The swapped-in collection carries T02's indexed_commit and doc_digest,
        the SW-L13 keys, and every chunk carries its file's SHA-256."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()

        meta = store._collection.metadata or {}
        assert meta["build_complete"] is True
        assert meta["doc_digest"] == doc_store_digest(settings)
        assert "indexed_commit" in meta
        assert meta["doc_glob_patterns"] == settings.doc_glob_patterns
        assert meta["doc_exclude_patterns"] == settings.doc_exclude_patterns
        assert meta["chunker_version"] == doc_store_mod.CHUNKER_VERSION
        assert meta["embedding_model"] == "fake-deterministic"

        rows = store._collection.get(include=["metadatas"])
        expected = {
            "intro.md": hashlib.sha256((corpus / "intro.md").read_bytes()).hexdigest(),
            "guide.md": hashlib.sha256((corpus / "guide.md").read_bytes()).hexdigest(),
        }
        for row in rows["metadatas"]:
            assert row["file_sha256"] == expected[row["file"]]
        # The adopt path (T02) still accepts the swapped-in collection.
        clear_doc_store()
        adopted = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert adopted.is_ready is True
        assert adopted._collection.count() == store._collection.count()
        clear_doc_store()

    def test_progress_idle_shape_and_log_line(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        assert store.progress() == {
            "files_total": 0,
            "files_done": 0,
            "started_at": None,
            "phase": None,
        }
        with caplog.at_level("INFO", logger="rust_lsp_mcp.doc_store"):
            store.rebuild()
        assert store.progress()["phase"] is None
        assert any(
            "rebuild complete, 0 files unchanged, 2 re-embedded," in r.getMessage()
            for r in caplog.records
        ), [r.getMessage() for r in caplog.records]


class TestIncrementalReembed:
    """Only new and changed files are embedded on a rebuild."""

    def test_second_rebuild_embeds_only_the_changed_file(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        ef = _CountingEmbeddingFunction()
        store = DocStore(settings, embedding_function=ef)
        first = store.rebuild()
        assert len(ef.embedded) == first
        guide_rows_before = store._collection.get(
            where={"file": "guide.md"}, include=["embeddings", "documents"]
        )

        (corpus / "intro.md").write_text(
            "# Introduction\n\nRewritten intro.\n\n## Later\n\nMore.\n", encoding="utf-8"
        )
        ef.embedded.clear()
        with caplog.at_level("INFO", logger="rust_lsp_mcp.doc_store"):
            second = store.rebuild()

        intro_chunks = store._collection.get(where={"file": "intro.md"})["ids"]
        assert len(ef.embedded) == len(intro_chunks) == 2
        assert all("intro" in d.lower() or "More." in d for d in ef.embedded), ef.embedded
        assert not any("guide" in d.lower() for d in ef.embedded)
        guide_rows_after = store._collection.get(
            where={"file": "guide.md"}, include=["embeddings", "documents"]
        )
        assert guide_rows_after["ids"] == guide_rows_before["ids"]
        assert guide_rows_after["documents"] == guide_rows_before["documents"]
        assert np.allclose(guide_rows_after["embeddings"], guide_rows_before["embeddings"])
        assert second == store._collection.count()
        assert any(
            "rebuild complete, 1 files unchanged, 1 re-embedded, " + f"{second} chunks"
            in r.getMessage()
            for r in caplog.records
        ), [r.getMessage() for r in caplog.records]

    def test_changed_chunker_version_reembeds_everything(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        ef = _CountingEmbeddingFunction()
        store = DocStore(settings, embedding_function=ef)
        first = store.rebuild()
        ef.embedded.clear()
        with patch.object(doc_store_mod, "CHUNKER_VERSION", "test-next"):
            second = store.rebuild()
        assert len(ef.embedded) == second == first


class TestShutdownCancelsRebuild:
    """SW-L14: clear_doc_store() stops a running rebuild and keeps the live index."""

    def test_clear_doc_store_cancels_slow_rebuild(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        for i in range(5):
            (corpus / f"extra{i}.md").write_text(f"# Extra {i}\n\nBody {i}.\n", encoding="utf-8")
        settings = _make_settings(tmp_path, corpus)
        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        count1 = store.rebuild()
        live_name = doc_collection_name(settings)

        for f in corpus.glob("*.md"):
            f.write_text(f.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")
        in_chunker = threading.Event()
        orig_chunk = doc_store_mod.chunk_markdown

        def _slow_chunk(text: str, rel_path: str) -> Any:
            in_chunker.set()
            time.sleep(0.2)
            return orig_chunk(text, rel_path)

        outcome: dict[str, Any] = {}

        def _run() -> None:
            with patch.object(doc_store_mod, "chunk_markdown", _slow_chunk):
                try:
                    store.rebuild()
                except doc_store_mod.DocRebuildCancelled as exc:
                    outcome["cancelled"] = exc

        thread = threading.Thread(target=_run)
        thread.start()
        try:
            assert in_chunker.wait(timeout=5)
            t0 = time.monotonic()
            clear_doc_store()
            elapsed = time.monotonic() - t0
            # clear_doc_store returned only once the rebuild had stopped.
            assert store._rebuild_idle.is_set()
        finally:
            thread.join(timeout=10)
        assert not thread.is_alive()
        assert elapsed < 5
        assert store._cancel.is_set()
        assert "cancelled" in outcome
        assert store.state == DOC_STATE_READY
        assert _collection_names(settings.chroma_path) == {live_name}
        assert _on_disk_count(settings.chroma_path, live_name) == count1

    def test_shutdown_while_worker_waits_on_flock_publishes_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Hold the flock, start the startup worker, call clear_doc_store(),
        release the flock: get_doc_store() stays None and no rebuild runs."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        lock_dir = pathlib.Path(settings.chroma_path)
        lock_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_dir / ".rlm-doc-store.lock", os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(fd, fcntl.LOCK_EX)

        rebuild_calls: list[None] = []
        orig_rebuild = DocStore.rebuild

        def _spy_rebuild(self: DocStore, **kwargs: Any) -> int:
            rebuild_calls.append(None)
            return orig_rebuild(self, **kwargs)

        async def _scenario() -> None:
            clear_doc_store()
            await init_doc_store_background(settings, embedding_function=FakeEmbeddingFunction())
            task = doc_store_mod._build_task
            assert task is not None
            await asyncio.sleep(0.3)  # the worker is now blocked on the flock
            assert not task.done()
            clear_doc_store()
            fcntl.flock(fd, fcntl.LOCK_UN)
            await asyncio.wait_for(task, timeout=10)

        try:
            with patch.object(DocStore, "rebuild", _spy_rebuild):
                asyncio.run(_scenario())
        finally:
            os.close(fd)
        assert get_doc_store() is None
        assert rebuild_calls == []
        assert doc_store_state() == (DOC_STATE_BUILDING, None)
        assert doc_collection_name(settings) not in _collection_names(settings.chroma_path)


class TestSweepLowRowsDocStore:
    """SW-L13, L15, L21, L22, L23 and L24."""

    @pytest.mark.parametrize(
        ("key", "change"),
        [
            ("doc_glob_patterns", {"doc_glob_patterns": "**/*.md,docs/*.md"}),
            ("doc_exclude_patterns", {"doc_exclude_patterns": "**/NEVER.md"}),
            ("chunker_version", {}),
            ("embedding_model", {}),
        ],
    )
    def test_sw_l13_adopt_refuses_on_each_key(
        self,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
        key: str,
        change: dict[str, str],
    ) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        clear_doc_store()
        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        clear_doc_store()

        probe = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        assert doc_store_mod._try_adopt(probe, settings, FakeEmbeddingFunction()) is True

        changed = settings.model_copy(update=change)
        assert doc_store_files(changed) == doc_store_files(settings)
        ef: Any = FakeEmbeddingFunction()
        if key == "embedding_model":
            ef = _OtherModelEmbeddingFunction()
        probe = DocStore(changed, embedding_function=ef)
        with caplog.at_level("INFO", logger="rust_lsp_mcp.doc_store"):
            if key == "chunker_version":
                with patch.object(doc_store_mod, "CHUNKER_VERSION", "test-next"):
                    adopted = doc_store_mod._try_adopt(probe, changed, ef)
            else:
                adopted = doc_store_mod._try_adopt(probe, changed, ef)
        assert adopted is False
        assert any(f"not adopting: {key} differs" in r.getMessage() for r in caplog.records)

    def test_sw_l15_failing_adopt_logs_warning_and_keeps_collection(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        clear_doc_store()
        built = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        count = built._collection.count()
        clear_doc_store()
        live_name = doc_collection_name(settings)

        probe = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        with (
            patch.object(
                probe._client, "get_collection", side_effect=RuntimeError("transient read")
            ),
            caplog.at_level("WARNING", logger="rust_lsp_mcp.doc_store"),
        ):
            adopted = doc_store_mod._try_adopt(probe, settings, FakeEmbeddingFunction())
        assert adopted is False
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("transient read" in r.getMessage() for r in warnings)
        assert _on_disk_count(settings.chroma_path, live_name) == count

    def test_sw_l21_symlinks_and_outside_root_files_are_skipped(
        self, tmp_path: pathlib.Path
    ) -> None:
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "real.md").write_text("# Real\n\nReal doc.\n", encoding="utf-8")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("# Secret\n\nAPI_KEY=1\n", encoding="utf-8")
        (corpus / "link.md").symlink_to(corpus / "real.md")
        (corpus / "linkdir").symlink_to(outside, target_is_directory=True)
        settings = Settings(
            chroma_path=str(tmp_path / "chroma"),
            project_root=str(corpus),
            doc_glob_patterns="**/*.md,linkdir/*.md,*.md",
            doc_exclude_patterns="",
        )
        # The outside file is reachable through the explicit pattern.
        assert list(corpus.glob("linkdir/*.md"))

        assert doc_store_files(settings) == [corpus / "real.md"]
        digest = doc_store_digest(settings)

        (outside / "secret.md").write_text("# Secret\n\nAPI_KEY=2 longer\n", encoding="utf-8")
        (corpus / "link.md").unlink()
        (corpus / "link.md").symlink_to(outside / "secret.md")
        assert doc_store_files(settings) == [corpus / "real.md"]
        assert doc_store_digest(settings) == digest

        store = DocStore(settings, embedding_function=FakeEmbeddingFunction())
        store.rebuild()
        files = {m["file"] for m in store._collection.get(include=["metadatas"])["metadatas"]}
        assert files == {"real.md"}

    def test_sw_l22_failed_reinit_reports_new_construction_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        clear_doc_store()
        with (
            patch.object(doc_store_mod, "chunk_markdown", side_effect=RuntimeError("old failure")),
            pytest.raises(RuntimeError, match="old failure"),
        ):
            init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert doc_store_state() == ("error", "RuntimeError: old failure")

        with (
            patch.object(doc_store_mod, "DocStore", side_effect=RuntimeError("new failure")),
            pytest.raises(RuntimeError, match="new failure"),
        ):
            init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert get_doc_store() is None
        assert doc_store_state() == ("error", "RuntimeError: new failure")
        clear_doc_store()

    def test_sw_l23_state_and_error_written_under_read_lock(self, tmp_path: pathlib.Path) -> None:
        writes: list[tuple[str, Any, bool]] = []

        class _SpyStore(DocStore):
            def __setattr__(self, name: str, value: Any) -> None:
                if name in ("_state", "_error") and "_read_lock" in self.__dict__:
                    writes.append((name, value, self._read_lock.locked()))
                super().__setattr__(name, value)

        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        store = _SpyStore(settings, embedding_function=FakeEmbeddingFunction())
        with (
            patch.object(doc_store_mod, "chunk_markdown", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError),
        ):
            store.rebuild()
        store.rebuild()

        assert writes, "rebuild wrote no state"
        assert all(locked for _name, _value, locked in writes), writes
        assert ("_error", "RuntimeError: boom", True) in writes
        assert ("_state", "error", True) in writes
        assert store.state_snapshot() == ("ready", None)

    def test_sw_l24_non_ascii_scripts_count_one_token_per_character(self) -> None:
        from rust_lsp_mcp.doc_chunking import estimate_tokens

        for text in ("привет" * 50, "Καλημέρα" * 40, "مرحبا" * 60, "字" * 300):
            assert estimate_tokens(text) >= len(text), text[:10]
        # ASCII is still estimated at two characters per token.
        assert estimate_tokens("abcd" * 100) == 200


# ---------------------------------------------------------------------------
# T04 R-gate round 1: cancel during embedding (step 5a), progress from
# registration (step 3), shutdown during store construction (step 5b).
# ---------------------------------------------------------------------------


class _SlowEmbeddingFunction(FakeEmbeddingFunction):
    """Sleeps per embedded document; signals its first call."""

    def __init__(self, per_doc: float = 0.0) -> None:
        super().__init__()
        self.per_doc = per_doc
        self.first_call = threading.Event()
        self.docs = 0

    def __call__(self, input: chromadb.api.types.Documents) -> chromadb.api.types.Embeddings:
        self.first_call.set()
        self.docs += len(input)
        time.sleep(self.per_doc * len(input))
        return super().__call__(input)


def _many_files(root: pathlib.Path, n_files: int, sections: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        body = "".join(
            f"## Section {j} of file {i}\n\nBody text {i}-{j} about topic {j}.\n\n"
            for j in range(sections)
        )
        (root / f"doc{i:03d}.md").write_text(f"# File {i}\n\n{body}", encoding="utf-8")


def _swallow(fn: Any) -> None:
    with contextlib.suppress(Exception):
        fn()


class TestRGateRound1:
    """Regression tests for the two live breaks and the construction-window race."""

    def test_sw_l14_clear_doc_store_stops_rebuild_during_embedding_promptly(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Step 5a: a shutdown during the embedding of a full rebuild (fewer chunks
        than one add batch) stops it in under 1 s; live intact, no staging left."""
        corpus = tmp_path / "corpus"
        _many_files(corpus, n_files=30, sections=8)  # ~270 chunks, below _ADD_BATCH_SIZE
        settings = _make_settings(tmp_path, corpus)
        ef = _SlowEmbeddingFunction()
        clear_doc_store()
        store = init_doc_store(settings, embedding_function=ef)
        live = doc_collection_name(settings)
        before = store.chunk_count()
        for p in corpus.glob("*.md"):
            p.write_text(p.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
        ef.per_doc = 0.015  # ~4 s to embed the whole corpus
        ef.first_call.clear()
        thread = threading.Thread(target=lambda: _swallow(store.rebuild), daemon=True)
        thread.start()
        assert ef.first_call.wait(10)
        t0 = time.monotonic()
        clear_doc_store()
        elapsed = time.monotonic() - t0
        thread.join(15)
        assert not thread.is_alive()
        assert _collection_names(settings.chroma_path) == {live}
        assert _on_disk_count(settings.chroma_path, live) == before
        assert elapsed < 1.0, f"cancel took {elapsed:.2f}s: not checked during embedding"

    def test_progress_not_null_while_rebuild_in_flight(self, tmp_path: pathlib.Path) -> None:
        """Step 3 amendment: phase is "starting" from registration, before the
        locks and the git identity call, so doc_index_progress is never null
        while a rebuild is in flight."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        clear_doc_store()
        store = init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        gate = threading.Event()
        entered = threading.Event()
        real_identity = doc_store_mod.project_identity

        def _slow_identity(s: Settings) -> str:
            entered.set()
            gate.wait(5)
            return real_identity(s)

        with patch.object(doc_store_mod, "project_identity", _slow_identity):
            thread = threading.Thread(target=store.rebuild, daemon=True)
            thread.start()
            try:
                assert entered.wait(5)
                in_flight = doc_store_mod.doc_rebuild_in_progress()
                progress = doc_index_progress_fn()
            finally:
                gate.set()
                thread.join(10)
        assert in_flight is True
        assert progress is not None, "doc_index_progress is null while a rebuild is running"
        assert progress["phase"] == "starting"
        assert progress["started_at"] is not None
        assert doc_index_progress_fn() is None
        clear_doc_store()

    def test_case5c_shutdown_during_store_construction_publishes_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Step 5b: shutdown lands after the flock, while DocStore(...) is being
        constructed, with an adoptable index on disk: nothing is published."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        clear_doc_store()
        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        clear_doc_store()
        real_init = DocStore.__init__
        entered = threading.Event()
        go = threading.Event()

        def _slow_init(self: Any, *a: Any, **k: Any) -> None:
            entered.set()
            go.wait(10)
            real_init(self, *a, **k)

        async def _scenario() -> None:
            await init_doc_store_background(settings, embedding_function=FakeEmbeddingFunction())
            task = doc_store_mod._build_task
            await asyncio.to_thread(entered.wait, 10)
            clear_doc_store()
            go.set()
            assert task is not None
            await asyncio.wait_for(task, 10)

        with patch.object(DocStore, "__init__", _slow_init):
            asyncio.run(_scenario())
        assert get_doc_store() is None

    def test_case5c_shutdown_during_project_identity_publishes_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Step 5b test as specified: an adoptable collection on disk and
        project_identity patched to call clear_doc_store() first."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        clear_doc_store()
        init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        clear_doc_store()
        real_identity = doc_store_mod.project_identity
        calls: list[None] = []

        def _identity_with_shutdown(s: Settings) -> str:
            if not calls:
                calls.append(None)
                clear_doc_store()
            return real_identity(s)

        with (
            patch.object(doc_store_mod, "project_identity", _identity_with_shutdown),
            pytest.raises(doc_store_mod.DocRebuildCancelled),
        ):
            init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert calls
        assert get_doc_store() is None

    def test_refresh_worker_generation_captured_before_thread_start(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Step 5b: a re-init worker whose generation was captured on the loop
        publishes nothing when shutdown runs before the thread reaches it."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        clear_doc_store()
        generation = doc_store_mod.current_shutdown_generation()
        clear_doc_store()
        with pytest.raises(doc_store_mod.DocRebuildCancelled):
            init_doc_store(
                settings, embedding_function=FakeEmbeddingFunction(), generation=generation
            )
        assert get_doc_store() is None
