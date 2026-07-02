"""Regression tests for DS-08 — non-blocking doc-store startup via the FastMCP lifespan.

No live analyzer, no network, no real embedding model.  ``core._lifespan`` is
exercised directly (not the full ``mcp`` app) with ``analyzer_lifespan``
stubbed out — these tests are only concerned with the doc-store half of
startup.  Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    - The lifespan ``yield``s (server becomes available) while a doc-store
      rebuild is still in flight on a background thread; ``status`` reports
      ``doc_index_state == "building"`` during that window, and the store
      transitions to ``"ready"`` once the background task completes.  A
      lifespan implementation that blocked on the rebuild before yielding
      would deadlock the ``asyncio.wait_for`` wrapper and fail this test.
    - When an on-disk collection can be adopted synchronously (the common
      warm-restart case), the store is ready immediately and no background
      task is spawned at all.
    - A background rebuild that raises is recorded on the store (``state ==
      "error"``, ``error_message`` set) without the exception ever escaping
      the background task.
    - A ``DocStore`` construction failure (before any rebuild) is recorded via
      ``doc_store_state()`` without ``init_doc_store_background`` raising.
    - A doc-store init failure (``init_doc_store_background`` itself raising,
      e.g. a bug in the background-init plumbing rather than a recorded
      rebuild/construction failure) is swallowed by ``core._lifespan``'s own
      ``try/except`` — the lifespan body still runs and ``core._manager`` is
      set, so navigation tools keep working even when the doc index is
      completely broken (DS-18 gap 1).
    - ``analyzer.analyzer_lifespan`` itself wires ``start()`` before the
      ``yield`` and ``shutdown()`` after it, with no live rust-analyzer
      process (``AnalyzerManager.start``/``shutdown`` patched) (DS-18 gap 2).
"""

import asyncio
import pathlib
import threading
from typing import Any
from unittest.mock import AsyncMock, patch

import chromadb
import numpy as np

import rust_lsp_mcp.analyzer as analyzer_mod
import rust_lsp_mcp.core as core_mod
import rust_lsp_mcp.doc_store as doc_store_mod
import rust_lsp_mcp.tools.status as status_mod
from rust_lsp_mcp.doc_store import (
    DOC_STATE_BUILDING,
    DOC_STATE_ERROR,
    DOC_STATE_READY,
    DocStore,
    init_doc_store,
)
from rust_lsp_mcp.settings import Settings

# ---------------------------------------------------------------------------
# Fake embedding function — deterministic, no model download (mirrors
# tests/test_doc_store.py's FakeEmbeddingFunction).
# ---------------------------------------------------------------------------


def _hash_vec(text: str, dim: int = 8) -> "np.ndarray":  # type: ignore[type-arg]
    import hashlib

    digest = hashlib.md5(text.encode()).digest()
    floats = [(digest[i % len(digest)] / 255.0) * 2.0 - 1.0 for i in range(dim)]
    return np.array(floats, dtype=np.float32)


class FakeEmbeddingFunction(chromadb.api.types.EmbeddingFunction[chromadb.api.types.Documents]):
    def __init__(self) -> None:
        pass

    def __call__(self, input: chromadb.api.types.Documents) -> chromadb.api.types.Embeddings:
        return [_hash_vec(doc) for doc in input]

    @staticmethod
    def name() -> str:
        return "fake-deterministic-lifespan"

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "FakeEmbeddingFunction":
        return FakeEmbeddingFunction()

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        pass


def _make_settings(tmp_path: pathlib.Path, corpus_dir: pathlib.Path) -> Settings:
    return Settings(
        chroma_path=str(tmp_path / "chroma"),
        project_root=str(corpus_dir),
        doc_glob_patterns="**/*.md",
    )


def _write_corpus(corpus_dir: pathlib.Path) -> None:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "guide.md").write_text(
        "# Guide\n\nThis explains how to use the tool.\n", encoding="utf-8"
    )


class _FakeManager:
    """Minimal stand-in for AnalyzerManager — only the attributes status() reads."""

    def __init__(self) -> None:
        self.state = "ready"
        self.error_message: str | None = None
        self.indexed_commit: str | None = "deadbeef"
        self.repository_root = "/tmp"


def _fake_analyzer_lifespan_factory(manager: _FakeManager) -> Any:
    """Return a stub replacement for ``analyzer.analyzer_lifespan`` yielding *manager*."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_analyzer_lifespan(app: object) -> Any:
        yield {"manager": manager}

    return _fake_analyzer_lifespan


# ---------------------------------------------------------------------------
# 1. The lifespan yields while the background build is still in flight.
# ---------------------------------------------------------------------------


class TestLifespanYieldsWhileBuildInFlight:
    def test_lifespan_yields_while_build_in_flight(self) -> None:
        build_event = threading.Event()

        def _lightweight_init(
            self: DocStore, settings: Settings, embedding_function: Any | None = None
        ) -> None:
            # No real ChromaDB client — this test only cares about the
            # yield-before-build-completes timing, not chroma mechanics.
            self._settings = settings
            self._ef = embedding_function
            self._collection = None
            self._state = DOC_STATE_BUILDING
            self._error = None
            self._build_lock = threading.Lock()

        def _patched_rebuild(self: DocStore) -> int:
            # Blocks the WORKER THREAD (asyncio.to_thread) until the test
            # releases it — proves the lifespan does not wait for this.
            build_event.wait(timeout=5)
            self._state = DOC_STATE_READY
            return 0

        async def _scenario() -> None:
            doc_store_mod.clear_doc_store()
            fake_manager = _FakeManager()
            with (
                patch.object(
                    core_mod, "analyzer_lifespan", _fake_analyzer_lifespan_factory(fake_manager)
                ),
                patch.object(DocStore, "__init__", _lightweight_init),
                patch.object(doc_store_mod, "_try_adopt", return_value=False),
                patch.object(DocStore, "rebuild", _patched_rebuild),
            ):
                async with core_mod._lifespan(object()) as ctx:  # ty: ignore[invalid-argument-type]
                    assert ctx["manager"] is fake_manager

                    # Server is "up" (we've reached the yield) while the
                    # rebuild is still blocked on build_event — a blocking
                    # implementation would never have reached this point
                    # inside the outer asyncio.wait_for, so it would deadlock
                    # rather than let us observe "building" here.
                    result = status_mod.status()
                    assert result["doc_index_state"] == DOC_STATE_BUILDING

                    assert doc_store_mod._build_task is not None
                    build_event.set()
                    await doc_store_mod._build_task

                    store = doc_store_mod.get_doc_store()
                    assert store is not None
                    assert store.state == DOC_STATE_READY

                # Lifespan teardown must have cleared the singleton.
                assert doc_store_mod.get_doc_store() is None

        asyncio.run(asyncio.wait_for(_scenario(), timeout=5))


# ---------------------------------------------------------------------------
# 2. Adopt path: ready immediately, no background task spawned.
# ---------------------------------------------------------------------------


class TestAdoptPathReadyBeforeYield:
    def test_adopt_path_ready_before_yield(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)
        ef = FakeEmbeddingFunction()

        async def _scenario() -> None:
            doc_store_mod.clear_doc_store()
            # Pre-populate a completed, adoptable collection on disk.
            pre_store = init_doc_store(settings, embedding_function=ef)
            assert pre_store.is_ready is True
            doc_store_mod.clear_doc_store()

            await doc_store_mod.init_doc_store_background(settings, embedding_function=ef)

            store = doc_store_mod.get_doc_store()
            assert store is not None
            assert store.is_ready is True
            assert doc_store_mod._build_task is None

            doc_store_mod.clear_doc_store()

        asyncio.run(asyncio.wait_for(_scenario(), timeout=5))


# ---------------------------------------------------------------------------
# 3. Background build failure is recorded; never escapes the task.
# ---------------------------------------------------------------------------


class TestBackgroundBuildFailureRecorded:
    def test_background_build_failure_recorded(self, tmp_path: pathlib.Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus)
        settings = _make_settings(tmp_path, corpus)

        async def _scenario() -> None:
            doc_store_mod.clear_doc_store()
            with patch.object(
                doc_store_mod, "chunk_markdown", side_effect=RuntimeError("thread boom")
            ):
                await doc_store_mod.init_doc_store_background(
                    settings, embedding_function=FakeEmbeddingFunction()
                )
                assert doc_store_mod._build_task is not None
                # Must not raise — the background task's own try/except
                # swallows the rebuild failure after logging it.
                await doc_store_mod._build_task

            store = doc_store_mod.get_doc_store()
            assert store is not None
            state, err = doc_store_mod.doc_store_state()
            assert state == DOC_STATE_ERROR
            assert err is not None
            assert "thread boom" in err

            doc_store_mod.clear_doc_store()

        asyncio.run(asyncio.wait_for(_scenario(), timeout=5))


# ---------------------------------------------------------------------------
# 4. DocStore construction failure is recorded via _init_error.
# ---------------------------------------------------------------------------


class TestConstructionFailureRecordsInitError:
    def test_construction_failure_records_init_error(self) -> None:
        settings = Settings(chroma_path="/tmp/does-not-matter", project_root="/tmp")

        async def _scenario() -> None:
            doc_store_mod.clear_doc_store()
            with patch.object(DocStore, "__init__", side_effect=RuntimeError("construction boom")):
                # Must not raise.
                await doc_store_mod.init_doc_store_background(settings)

            assert doc_store_mod.get_doc_store() is None
            state, err = doc_store_mod.doc_store_state()
            assert state == DOC_STATE_ERROR
            assert err is not None
            assert "construction boom" in err

            doc_store_mod.clear_doc_store()

        asyncio.run(asyncio.wait_for(_scenario(), timeout=5))


# ---------------------------------------------------------------------------
# 5. DS-18 gap 1 — a doc-store init failure is swallowed by core._lifespan;
#    navigation tools (gated on core._manager) keep working regardless.
# ---------------------------------------------------------------------------


class TestLifespanSwallowsDocStoreInitFailure:
    def test_doc_store_init_failure_does_not_break_nav(self) -> None:
        """``init_doc_store_background`` raising must not propagate out of
        ``core._lifespan``, and ``core._manager`` must still be set inside the
        ``async with`` body (i.e. nav tools relying on ``require_ready()`` /
        ``get_manager()`` are unaffected by a broken doc index)."""

        async def _scenario() -> None:
            doc_store_mod.clear_doc_store()
            fake_manager = _FakeManager()
            with (
                patch.object(
                    core_mod, "analyzer_lifespan", _fake_analyzer_lifespan_factory(fake_manager)
                ),
                patch.object(
                    core_mod,
                    "init_doc_store_background",
                    AsyncMock(side_effect=RuntimeError("doc-store init boom")),
                ),
            ):
                # Must not raise — core._lifespan's own try/except around the
                # init_doc_store_background call must swallow this.
                async with core_mod._lifespan(object()) as ctx:  # ty: ignore[invalid-argument-type]
                    assert ctx["manager"] is fake_manager
                    # The readiness gate / get_manager() that nav tools rely
                    # on must be wired up despite the doc-store failure.
                    assert core_mod._manager is fake_manager

            # Teardown must still clear the singleton.
            assert core_mod._manager is None

        asyncio.run(asyncio.wait_for(_scenario(), timeout=5))


# ---------------------------------------------------------------------------
# 6. DS-18 gap 2 — analyzer.analyzer_lifespan wires start() before the yield
#    and shutdown() after it, with no live rust-analyzer process.
# ---------------------------------------------------------------------------


class TestAnalyzerLifespanStartShutdownWiring:
    def test_start_then_shutdown_wiring(self) -> None:
        """Drive the REAL ``analyzer_lifespan`` with ``AnalyzerManager.start``/
        ``shutdown`` patched to async no-ops — proves the start -> yield ->
        shutdown wiring without spawning a real rust-analyzer process."""

        async def _scenario() -> None:
            start_mock = AsyncMock()
            shutdown_mock = AsyncMock()
            with (
                patch.object(analyzer_mod.AnalyzerManager, "start", start_mock),
                patch.object(analyzer_mod.AnalyzerManager, "shutdown", shutdown_mock),
            ):
                async with analyzer_mod.analyzer_lifespan(object()) as ctx:
                    assert isinstance(ctx["manager"], analyzer_mod.AnalyzerManager)
                    start_mock.assert_awaited_once()
                    shutdown_mock.assert_not_awaited()

                shutdown_mock.assert_awaited_once()

        asyncio.run(asyncio.wait_for(_scenario(), timeout=5))
