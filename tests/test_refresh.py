"""Fast-tier tests for the refresh tool.

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    refresh:
        - manager is None → error envelope.
        - Happy path → restart() is awaited exactly once; envelope is ok with
          state == "indexing".
        - Non-blocking: refresh returns even though the fake manager's state
          stays "indexing" after restart() (i.e. it does not wait for "ready").
    Doc-store wiring (DS-14 recovery path), on the background worker:
        - store present, healthy → store.rebuild() is called (after restart());
          init_doc_store is NOT called.
        - store None → graceful re-init via init_doc_store; still returns ok.
        - store in "error" state → re-init via init_doc_store (rebuild() is
          NOT called on the known-broken store).
        - rebuild()/init_doc_store is called after restart(), not before.
        - a rebuild or re-init failure is logged at error; the envelope is
          still ok (refresh returned before the work ran).
    Non-blocking doc rebuild (T04, SW-10):
        - refresh returns before a slow stub rebuild finishes, with
          doc_rebuild_started: true.
        - a second refresh during a rebuild reports doc_rebuild_started: false
          and starts nothing.
        - SW-L22 end to end: a failed re-init's new error reaches status.
    End-to-end doc-store recovery (real DocStore, fake embedding function,
    tmp chroma path): a failed rebuild followed by a successful refresh
    recovers search_docs from error to ok/not_found.
"""

import asyncio
import hashlib
import logging
import pathlib
import threading
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import chromadb
import numpy as np
import pytest

import rust_lsp_mcp.doc_store as doc_store_mod
from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY
from rust_lsp_mcp.doc_store import DOC_STATE_ERROR
from rust_lsp_mcp.envelope import STATUS_ERROR, STATUS_OK

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeManager:
    """Minimal fake AnalyzerManager for refresh tests.

    ``restart`` is an async spy: records that it was called and leaves
    ``self.state`` at ``STATE_INDEXING`` — it never flips to "ready".
    This lets tests verify both that restart() was awaited and that the
    tool returns without waiting for "ready".
    """

    def __init__(self) -> None:
        self.state: str = STATE_INDEXING
        self.restart = AsyncMock()


def _join_doc_worker(timeout: float = 10.0) -> None:
    """Wait for the background doc rebuild thread refresh() started, if any."""
    thread = doc_store_mod._rebuild_thread
    if thread is not None:
        thread.join(timeout=timeout)
        assert not thread.is_alive(), "doc rebuild worker did not finish"


@pytest.fixture(autouse=True)
def _no_leaked_doc_worker() -> Iterator[None]:
    """Start each test with no doc rebuild in flight and join any it started."""
    _join_doc_worker()
    doc_store_mod._rebuild_thread = None
    yield
    _join_doc_worker()
    doc_store_mod._rebuild_thread = None


def _run_refresh(manager: Any) -> dict[str, Any]:
    """Patch core._manager with *manager* and call refresh(); return the envelope.

    Also patches ``refresh_mod.get_doc_store`` to return ``None`` and
    ``refresh_mod.init_doc_store`` to a no-op MagicMock, so these tests are
    hermetic regardless of any real doc-store module state left over from
    other test files (module-level singleton) and never touch a real
    ChromaDB client.
    """
    import rust_lsp_mcp.core as core
    import rust_lsp_mcp.tools.refresh as refresh_mod

    async def _inner() -> dict[str, Any]:
        with (
            patch.object(core, "_manager", manager),
            patch.object(refresh_mod, "get_doc_store", return_value=None),
            patch.object(refresh_mod, "init_doc_store", MagicMock()),
        ):
            result = await refresh_mod.refresh()
            _join_doc_worker()
            return result

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRefreshManagerNone:
    """When get_manager() returns None, refresh must return an error envelope."""

    def test_none_manager_returns_error(self) -> None:
        result = _run_refresh(None)
        assert result["status"] == STATUS_ERROR

    def test_none_manager_error_has_message(self) -> None:
        result = _run_refresh(None)
        assert "message" in result
        assert result["message"]  # non-empty


class TestRefreshHappyPath:
    """Happy path: restart() is awaited exactly once and the envelope is ok+indexing."""

    def test_returns_ok_status(self) -> None:
        mgr = _FakeManager()
        result = _run_refresh(mgr)
        assert result["status"] == STATUS_OK

    def test_state_is_indexing(self) -> None:
        mgr = _FakeManager()
        result = _run_refresh(mgr)
        assert result["state"] == STATE_INDEXING

    def test_restart_called_exactly_once(self) -> None:
        mgr = _FakeManager()
        _run_refresh(mgr)
        mgr.restart.assert_awaited_once()

    def test_envelope_has_message(self) -> None:
        mgr = _FakeManager()
        result = _run_refresh(mgr)
        assert "message" in result
        assert result["message"]  # non-empty polling hint


class TestRefreshReturnedStateIsSnapshot:
    """The returned ``state`` is snapshotted right after ``restart()`` — it must
    not reflect a live analyzer that readied *during* the doc rebuild.

    Regression for the integration flakiness where a fast warm analyzer re-index
    reached ``"ready"`` while a cold doc-store embed was still running, so the
    old ``ok(state=mgr.state)`` at return time yielded a misleading ``"ready"``.
    """

    def test_state_stays_indexing_when_analyzer_readies_during_doc_rebuild(self) -> None:
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.refresh as refresh_mod

        mgr = _FakeManager()  # state == STATE_INDEXING after restart()

        # A fake healthy store whose rebuild() flips the manager to "ready"
        # mid-call — simulating the analyzer finishing its warm re-index while
        # the (blocking) doc-store rebuild runs.
        store = MagicMock()
        store.state = "ready"

        def _rebuild_that_readies_analyzer(**_kw: Any) -> int:
            mgr.state = STATE_READY
            return 0

        store.rebuild = _rebuild_that_readies_analyzer

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(refresh_mod, "get_doc_store", return_value=store),
            ):
                result = await refresh_mod.refresh()
                _join_doc_worker()
                return result

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK
        # Snapshot at restart time, NOT the post-rebuild live state.
        assert result["state"] == STATE_INDEXING
        assert mgr.state == STATE_READY  # the analyzer really did ready mid-rebuild


class TestRefreshNonBlocking:
    """refresh() must return while the analyzer is still indexing (non-blocking).

    The fake manager's restart() never flips state to "ready", so if refresh
    were to block waiting for "ready" it would hang forever.  The fact that
    the call returns at all proves it is non-blocking.
    """

    def test_returns_before_ready(self) -> None:
        """refresh returns even though fake state stays "indexing" after restart."""
        mgr = _FakeManager()
        # State is and remains STATE_INDEXING throughout — if refresh blocked on
        # "ready" this test would time out rather than assert.
        result = _run_refresh(mgr)
        # Tool returned; confirm state was still indexing when it did so.
        assert result["state"] == STATE_INDEXING
        assert mgr.state == STATE_INDEXING  # fake never flipped to ready


# ---------------------------------------------------------------------------
# Doc-store wiring (DS-14 recovery path)
# ---------------------------------------------------------------------------


def _run_refresh_with_store(
    manager: Any, store: Any, init_doc_store_mock: Any = None
) -> dict[str, Any]:
    """Patch core._manager and refresh module's get_doc_store/init_doc_store.

    Monkeypatches:
      - ``core._manager`` (as the existing helper does) for get_manager().
      - ``rust_lsp_mcp.tools.refresh.get_doc_store`` to return *store*.
      - ``rust_lsp_mcp.tools.refresh.init_doc_store`` to *init_doc_store_mock*
        (defaulting to a no-op MagicMock) so a ``store is None``/errored path
        never touches a real ChromaDB client.
    """
    import rust_lsp_mcp.core as core
    import rust_lsp_mcp.tools.refresh as refresh_mod

    if init_doc_store_mock is None:
        init_doc_store_mock = MagicMock()

    async def _inner() -> dict[str, Any]:
        with (
            patch.object(core, "_manager", manager),
            patch.object(refresh_mod, "get_doc_store", return_value=store),
            patch.object(refresh_mod, "init_doc_store", init_doc_store_mock),
        ):
            result = await refresh_mod.refresh()
            _join_doc_worker()
            return result

    return asyncio.run(_inner())


class TestRefreshDocStoreWiring:
    """DS-14: refresh() re-initialises an absent/errored store, rebuilds a healthy one."""

    def test_store_present_rebuild_called(self) -> None:
        """When get_doc_store() returns a healthy store, rebuild() must be called."""
        mgr = _FakeManager()
        store = MagicMock()
        store.rebuild.return_value = 42  # number of chunks indexed

        _run_refresh_with_store(mgr, store)

        store.rebuild.assert_called_once()

    def test_store_present_returns_ok(self) -> None:
        """Presence of a healthy doc store must not change the ok return status."""
        mgr = _FakeManager()
        store = MagicMock()
        store.rebuild.return_value = 10

        result = _run_refresh_with_store(mgr, store)

        assert result["status"] == STATUS_OK

    def test_store_present_state_is_indexing(self) -> None:
        """ok envelope still carries state=indexing even with doc rebuild."""
        mgr = _FakeManager()
        store = MagicMock()
        store.rebuild.return_value = 0

        result = _run_refresh_with_store(mgr, store)

        assert result["state"] == STATE_INDEXING

    def test_store_none_triggers_reinit(self) -> None:
        """When get_doc_store() returns None, init_doc_store must be called once."""
        mgr = _FakeManager()
        init_mock = MagicMock()

        result = _run_refresh_with_store(mgr, None, init_doc_store_mock=init_mock)

        assert result["status"] == STATUS_OK
        init_mock.assert_called_once()

    def test_store_none_still_returns_ok(self) -> None:
        """store=None must not degrade the ok envelope — analyzer restart still worked."""
        mgr = _FakeManager()
        result = _run_refresh_with_store(mgr, None)
        assert result["status"] == STATUS_OK
        assert result["state"] == STATE_INDEXING

    def test_restart_called_even_with_store(self) -> None:
        """analyzer restart() must still be awaited when a doc store is present."""
        mgr = _FakeManager()
        store = MagicMock()
        store.rebuild.return_value = 5

        _run_refresh_with_store(mgr, store)

        mgr.restart.assert_awaited_once()

    def test_rebuild_called_after_restart(self) -> None:
        """rebuild() must be called AFTER restart(), not before.

        We track call order by recording the sequence of events on a shared log
        using side effects on both mocks.
        """
        mgr = _FakeManager()
        store = MagicMock()
        call_log: list[str] = []

        async def _fake_restart() -> None:
            call_log.append("restart")

        mgr.restart = AsyncMock(side_effect=_fake_restart)
        store.rebuild.side_effect = lambda **_kw: call_log.append("rebuild")

        _run_refresh_with_store(mgr, store)

        assert call_log == ["restart", "rebuild"], (
            f"Expected restart before rebuild, got: {call_log}"
        )

    def test_rebuild_raises_is_logged_and_envelope_stays_ok(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When rebuild() raises on the worker, refresh has already returned ok;
        the failure is logged at error (``status`` reports it on the store)."""
        mgr = _FakeManager()
        store = MagicMock()
        store.rebuild.side_effect = RuntimeError("embedding model unavailable")

        with caplog.at_level(logging.ERROR, logger="rust_lsp_mcp.tools.refresh"):
            result = _run_refresh_with_store(mgr, store)

        assert result["status"] == STATUS_OK
        assert result["doc_rebuild_started"] is True
        store.rebuild.assert_called_once()
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("doc-store rebuild failed" in r.getMessage() for r in errors)
        assert any("embedding model unavailable" in (r.exc_text or "") for r in errors)
        mgr.restart.assert_awaited_once()

    def test_errored_store_triggers_reinit(self) -> None:
        """A store in DOC_STATE_ERROR must be re-initialised, not rebuilt in place."""
        mgr = _FakeManager()
        store = MagicMock()
        store.state = DOC_STATE_ERROR
        init_mock = MagicMock()

        result = _run_refresh_with_store(mgr, store, init_doc_store_mock=init_mock)

        assert result["status"] == STATUS_OK
        init_mock.assert_called_once()
        store.rebuild.assert_not_called()

    def test_healthy_store_does_not_reinit(self) -> None:
        """A healthy store must be rebuilt in place — init_doc_store must NOT be called."""
        mgr = _FakeManager()
        store = MagicMock()
        store.state = "ready"
        init_mock = MagicMock()

        result = _run_refresh_with_store(mgr, store, init_doc_store_mock=init_mock)

        assert result["status"] == STATUS_OK
        store.rebuild.assert_called_once()
        init_mock.assert_not_called()

    def test_reinit_failure_is_logged_and_envelope_stays_ok(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """init_doc_store raising on the worker is logged at error, never propagated.

        restart() must still have been awaited exactly once — the analyzer
        re-index is not rolled back by a doc-store failure.
        """
        mgr = _FakeManager()
        init_mock = MagicMock(side_effect=RuntimeError("chroma path unwritable"))

        with caplog.at_level(logging.ERROR, logger="rust_lsp_mcp.tools.refresh"):
            result = _run_refresh_with_store(mgr, None, init_doc_store_mock=init_mock)

        assert result["status"] == STATUS_OK
        init_mock.assert_called_once()
        assert any(
            "chroma path unwritable" in (r.exc_text or "")
            for r in caplog.records
            if r.levelno == logging.ERROR
        )
        mgr.restart.assert_awaited_once()


# ---------------------------------------------------------------------------
# T04 (SW-10): refresh returns once the doc rebuild has started.
# ---------------------------------------------------------------------------


class TestRefreshDocRebuildNonBlocking:
    """refresh() starts the doc rebuild on a worker thread and returns."""

    def test_refresh_returns_before_slow_rebuild_finishes(self) -> None:
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.refresh as refresh_mod

        mgr = _FakeManager()
        store = MagicMock()
        store.state = "ready"
        rebuild_started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def _slow_rebuild(**_kw: Any) -> int:
            rebuild_started.set()
            assert release.wait(timeout=5), "test deadlocked waiting for release"
            finished.set()
            return 3

        store.rebuild = _slow_rebuild

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(refresh_mod, "get_doc_store", return_value=store),
            ):
                return await asyncio.wait_for(refresh_mod.refresh(), timeout=5)

        try:
            result = asyncio.run(_inner())
            assert result["status"] == STATUS_OK
            assert result["doc_rebuild_started"] is True
            assert "doc_index_state" in result
            assert "doc_index_progress" in result
            assert rebuild_started.wait(timeout=5)
            assert not finished.is_set(), "refresh waited for the doc rebuild"
        finally:
            release.set()
            _join_doc_worker()
        assert finished.is_set()

    def test_second_refresh_during_rebuild_does_not_start_another(self) -> None:
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.refresh as refresh_mod

        mgr = _FakeManager()
        store = MagicMock()
        store.state = "ready"
        release = threading.Event()
        rebuild_calls: list[None] = []

        def _slow_rebuild(**_kw: Any) -> int:
            rebuild_calls.append(None)
            assert release.wait(timeout=5), "test deadlocked waiting for release"
            return 1

        store.rebuild = _slow_rebuild
        progress = {
            "files_total": 10,
            "files_done": 4,
            "started_at": "2026-11-04T10:00:00+00:00",
            "phase": "embedding",
        }

        async def _inner() -> tuple[dict[str, Any], dict[str, Any]]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(refresh_mod, "get_doc_store", return_value=store),
                patch.object(refresh_mod, "doc_index_progress", return_value=progress),
            ):
                first = await refresh_mod.refresh()
                second = await refresh_mod.refresh()
                return first, second

        try:
            first, second = asyncio.run(_inner())
        finally:
            release.set()
            _join_doc_worker()

        assert first["doc_rebuild_started"] is True
        assert second["status"] == STATUS_OK
        assert second["doc_rebuild_started"] is False
        assert second["doc_index_progress"] == progress
        assert len(rebuild_calls) == 1
        assert mgr.restart.await_count == 2

    def test_sw_l22_failed_reinit_error_reaches_status(self, tmp_path: pathlib.Path) -> None:
        """SW-L22 end to end: with the store in ``error`` and ``DocStore``
        construction patched to raise a new message, ``refresh`` starts the
        re-init worker; once it finishes, ``status`` reports the NEW error."""
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.refresh as refresh_mod
        from rust_lsp_mcp.settings import Settings
        from rust_lsp_mcp.tools.status import status

        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "guide.md").write_text("# Guide\n\nBody.\n", encoding="utf-8")
        settings = Settings(
            chroma_path=str(tmp_path / "chroma"),
            project_root=str(corpus),
            doc_glob_patterns="**/*.md",
        )
        doc_store_mod.clear_doc_store()
        with (
            patch.object(doc_store_mod, "chunk_markdown", side_effect=RuntimeError("old failure")),
            pytest.raises(RuntimeError, match="old failure"),
        ):
            doc_store_mod.init_doc_store(settings, embedding_function=FakeEmbeddingFunction())
        assert doc_store_mod.doc_store_state() == (DOC_STATE_ERROR, "RuntimeError: old failure")

        mgr = _FakeManager()

        async def _scenario() -> tuple[dict[str, Any], dict[str, Any]]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(refresh_mod, "get_settings", return_value=settings),
                patch.object(doc_store_mod, "DocStore", side_effect=RuntimeError("new failure")),
            ):
                refreshed = await refresh_mod.refresh()
                _join_doc_worker()
            with patch.object(core, "_manager", None):
                reported = await status()
            return refreshed, reported

        try:
            refreshed, reported = asyncio.run(_scenario())
        finally:
            doc_store_mod.clear_doc_store()

        assert refreshed["status"] == STATUS_OK
        assert refreshed["doc_rebuild_started"] is True
        assert reported["doc_index_state"] == DOC_STATE_ERROR
        assert reported["doc_index_error"] == "RuntimeError: new failure"


# ---------------------------------------------------------------------------
# End-to-end doc-store recovery: real DocStore, fake embedding function.
# ---------------------------------------------------------------------------


def _hash_vec(text: str, dim: int = 8) -> "np.ndarray":  # type: ignore[type-arg]
    """Produce a deterministic unit-range float32 array from text via MD5."""
    digest = hashlib.md5(text.encode()).digest()
    floats = [(digest[i % len(digest)] / 255.0) * 2.0 - 1.0 for i in range(dim)]
    return np.array(floats, dtype=np.float32)


class FakeEmbeddingFunction(chromadb.api.types.EmbeddingFunction[chromadb.api.types.Documents]):
    """Deterministic fake EF: identical text always produces identical vector.

    Mirrors tests/test_doc_store.py's FakeEmbeddingFunction — implements the
    full chromadb 1.5.9 EF protocol so it works with both ``add`` and
    ``query`` (unlike a bare callable, which chromadb's query path requires
    ``embed_query`` on).
    """

    def __init__(self) -> None:
        pass

    def __call__(self, input: chromadb.api.types.Documents) -> chromadb.api.types.Embeddings:
        return [_hash_vec(doc) for doc in input]

    @staticmethod
    def name() -> str:
        return "fake-deterministic-refresh"

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "FakeEmbeddingFunction":
        return FakeEmbeddingFunction()

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        pass


class TestRefreshEndToEndDocStoreRecovery:
    """A failed doc-store build followed by refresh() recovers to a working store."""

    def test_failed_build_then_refresh_recovers(self, tmp_path: pathlib.Path) -> None:
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.doc_store as doc_store_mod
        import rust_lsp_mcp.tools.refresh as refresh_mod
        import rust_lsp_mcp.tools.search_docs as search_mod
        from rust_lsp_mcp.envelope import STATUS_NOT_FOUND
        from rust_lsp_mcp.settings import Settings

        corpus = tmp_path / "corpus"
        corpus.mkdir(parents=True, exist_ok=True)
        (corpus / "guide.md").write_text(
            "# Guide\n\nThis explains how to use the tool.\n", encoding="utf-8"
        )
        settings = Settings(
            chroma_path=str(tmp_path / "chroma"),
            project_root=str(corpus),
            doc_glob_patterns="**/*.md",
        )
        ef = FakeEmbeddingFunction()

        doc_store_mod.clear_doc_store()
        store = doc_store_mod.DocStore(settings, embedding_function=ef)

        # First rebuild fails (monkeypatch chunk_markdown to raise).
        with (
            patch.object(doc_store_mod, "chunk_markdown", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            store.rebuild()

        assert store.state == DOC_STATE_ERROR

        async def _search(target_store: Any) -> dict[str, Any]:
            with patch.object(search_mod, "get_doc_store", return_value=target_store):
                return await search_mod.search_docs(query="guide")

        result = asyncio.run(_search(store))
        assert result["status"] == STATUS_ERROR

        # Now refresh — patched manager/settings; init_doc_store is bound to
        # inject the offline fake embedding function so the errored-store
        # recovery branch runs for real against the same tmp chroma/corpus.
        mgr = _FakeManager()

        def _init_with_fake_ef(s: Settings, **kw: Any) -> doc_store_mod.DocStore:
            return doc_store_mod.init_doc_store(s, embedding_function=ef, **kw)

        async def _do_refresh() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(refresh_mod, "get_doc_store", return_value=store),
                patch.object(refresh_mod, "get_settings", return_value=settings),
                patch.object(refresh_mod, "init_doc_store", _init_with_fake_ef),
            ):
                result = await refresh_mod.refresh()
                _join_doc_worker()
                return result

        refresh_result = asyncio.run(_do_refresh())
        assert refresh_result["status"] == STATUS_OK
        assert refresh_result["doc_rebuild_started"] is True

        # The refresh path called init_doc_store, which set a NEW singleton in
        # doc_store module — fetch it directly to confirm recovery.
        recovered_store = doc_store_mod.get_doc_store()
        assert recovered_store is not None
        assert recovered_store.is_ready is True

        recovered_result = asyncio.run(_search(recovered_store))
        assert recovered_result["status"] in (STATUS_OK, STATUS_NOT_FOUND)

        doc_store_mod.clear_doc_store()


# ---------------------------------------------------------------------------
# finding-3: concurrent refresh() calls on an absent/errored store must not
# race two DocStore re-inits against the same on-disk collection.
# ---------------------------------------------------------------------------


class TestFinding3ConcurrentRefreshSerialized:
    """Concurrent refresh() calls on an absent/errored store start ONE re-init:
    the others report ``doc_rebuild_started: false`` and never overlap it.
    """

    def test_concurrent_refresh_starts_one_reinit(self) -> None:
        """Three concurrent refresh() calls on an absent store: init_doc_store
        runs exactly once (it is still running when the others decide), and
        exactly one envelope reports ``doc_rebuild_started: true``.

        All patches are applied ONCE, OUTSIDE the gather — applying
        ``unittest.mock.patch.object`` independently from racing coroutines
        on the same target is itself racy.  No lock-swapping needed (#91):
        the refresh lock is obtained per-loop.
        """
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.refresh as refresh_mod

        release = threading.Event()
        calls: list[None] = []

        def _slow_init(settings: Any, **_kw: Any) -> MagicMock:
            calls.append(None)
            assert release.wait(timeout=5), "test deadlocked waiting for release"
            return MagicMock()

        mgr = _FakeManager()

        async def _run_all() -> list[dict[str, Any]]:
            results = list(
                await asyncio.gather(
                    refresh_mod.refresh(), refresh_mod.refresh(), refresh_mod.refresh()
                )
            )
            release.set()
            _join_doc_worker()
            return results

        with (
            patch.object(core, "_manager", mgr),
            patch.object(refresh_mod, "get_doc_store", return_value=None),
            patch.object(refresh_mod, "init_doc_store", _slow_init),
        ):
            results = asyncio.run(_run_all())

        assert all(r["status"] == STATUS_OK for r in results)
        assert sorted(r["doc_rebuild_started"] for r in results) == [False, False, True]
        assert len(calls) == 1
        assert mgr.restart.await_count == 3


# ---------------------------------------------------------------------------
# #91: the doc-store refresh lock must be usable across separate event loops
# without any lock-swapping/patching -- this test suite's own style (a fresh
# asyncio.run() per test) is exactly the scenario that trips the loop-binding
# hazard described on the lock itself.
# ---------------------------------------------------------------------------


class TestRefreshLockCrossEventLoop:
    """Regression for #91: contending the doc-store refresh lock across two
    separate ``asyncio.run()`` invocations (i.e. two different event loops)
    must not raise, and serialization within a single loop must be preserved.

    NOTE: no lock-swapping/patching here on purpose -- that workaround is
    exactly what #91 says future tests shouldn't need.
    """

    def test_refresh_lock_usable_across_event_loops(self) -> None:
        import rust_lsp_mcp.tools.refresh as refresh_mod

        async def _contend(lock: asyncio.Lock) -> None:
            """Force REAL contention (a holder plus a queued waiter), not just
            an uncontended acquire.  asyncio's lazy loop-binding (``_get_loop()``
            in ``Lock.acquire()``) is only reached on the contended path — an
            uncontended acquire short-circuits before ever calling it — so an
            honest repro of #91 needs two tasks actually queuing on the lock.
            """

            async def _holder() -> None:
                async with lock:
                    await asyncio.sleep(0.02)

            async def _contender() -> None:
                await asyncio.sleep(0)  # yield so _holder acquires first
                async with lock:
                    pass

            await asyncio.gather(_holder(), _contender())

        async def _contend_and_return_lock() -> asyncio.Lock:
            lock = refresh_mod._get_doc_store_refresh_lock()
            await _contend(lock)
            return lock

        async def _contend_twice_same_loop() -> tuple[asyncio.Lock, asyncio.Lock]:
            first = await _contend_and_return_lock()
            second = await _contend_and_return_lock()
            return first, second

        # Same loop, two contended acquisitions -> same lock object
        # (serialization within one loop is preserved).
        first, second = asyncio.run(_contend_twice_same_loop())
        assert first is second

        # A SEPARATE event loop contending the SAME lock must not blow up
        # with "... is bound to a different event loop" -- this is the crux
        # of #91.  Pre-fix, this second asyncio.run() raises RuntimeError
        # because the module-level lock lazily bound to the FIRST loop
        # above.  Post-fix, this loop gets its own (different) lock object.
        third = asyncio.run(_contend_and_return_lock())
        assert third is not first


# ---------------------------------------------------------------------------
# Backlog lines 115 and 117: the reply reports the masked doc state status
# reports, and its message follows that state.
# ---------------------------------------------------------------------------


def _errored_store(message: str = "RuntimeError: boom") -> MagicMock:
    store = MagicMock()
    store.state = DOC_STATE_ERROR
    store.error_message = message
    store.state_snapshot.return_value = (DOC_STATE_ERROR, message)
    return store


class TestRefreshReplyDocState:
    def test_errored_store_reply_reads_building(self) -> None:
        """Line 117: a refresh that starts the re-init of an errored store
        reports "building", as status does, and a second refresh while that
        re-init runs reports "building" with doc_rebuild_started false."""
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.refresh as refresh_mod

        mgr = _FakeManager()
        store = _errored_store()
        release = threading.Event()
        reinit_calls: list[None] = []

        def _held_reinit(*_a: Any, **_kw: Any) -> None:
            reinit_calls.append(None)
            assert release.wait(timeout=5), "test deadlocked waiting for release"

        async def _inner() -> tuple[dict[str, Any], dict[str, Any]]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(doc_store_mod, "_doc_store", store),
                patch.object(refresh_mod, "get_doc_store", return_value=store),
                patch.object(refresh_mod, "init_doc_store", _held_reinit),
            ):
                first = await refresh_mod.refresh()
                second = await refresh_mod.refresh()
                return first, second

        try:
            first, second = asyncio.run(_inner())
        finally:
            release.set()
            _join_doc_worker()

        assert first["doc_rebuild_started"] is True
        assert first["doc_index_state"] == "building"
        assert second["doc_rebuild_started"] is False
        assert second["doc_index_state"] == "building"
        assert len(reinit_calls) == 1

    def test_errored_store_message_names_not_ready(self) -> None:
        """Line 115: the started-rebuild message over an errored store says
        search_docs answers not_ready, not that the old index keeps
        answering."""
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.refresh as refresh_mod

        mgr = _FakeManager()
        store = _errored_store()
        release = threading.Event()

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(doc_store_mod, "_doc_store", store),
                patch.object(refresh_mod, "get_doc_store", return_value=store),
                patch.object(
                    refresh_mod, "init_doc_store", lambda *_a, **_k: release.wait(timeout=5)
                ),
            ):
                return await refresh_mod.refresh()

        try:
            result = asyncio.run(_inner())
        finally:
            release.set()
            _join_doc_worker()

        message = result["message"]
        assert result["doc_rebuild_started"] is True
        assert "old index" not in message
        assert "keeps answering" not in message
        assert "not_ready" in message
        assert message.startswith("Re-index started; poll status until state is 'ready'.")


class TestRefreshReplyMessage:
    """Line 115: the reply message for each doc state."""

    def test_ready_started_keeps_old_index_message(self) -> None:
        from rust_lsp_mcp.tools.refresh import _reply_message

        message = _reply_message("ready", True)
        assert message == (
            "Re-index started; poll status until state is 'ready'. The doc index "
            "rebuilds in the background (status.doc_index_progress); search_docs "
            "keeps answering from the old index meanwhile."
        )

    def test_ready_not_started_keeps_already_running_message(self) -> None:
        from rust_lsp_mcp.tools.refresh import _reply_message

        message = _reply_message("ready", False)
        assert message == (
            "Re-index started; poll status until state is 'ready'. A doc index "
            "rebuild was already running, so no new one was started "
            "(status.doc_index_progress)."
        )

    @pytest.mark.parametrize("started", [True, False])
    def test_building_names_not_ready(self, started: bool) -> None:
        from rust_lsp_mcp.tools.refresh import _reply_message

        message = _reply_message("building", started)
        assert message.startswith("Re-index started; poll status until state is 'ready'.")
        assert "being built in the background" in message
        assert "status.doc_index_progress" in message
        assert "not_ready" in message
        assert "doc_index_state 'ready'" in message
        assert "old index" not in message

    @pytest.mark.parametrize("started", [True, False])
    def test_error_names_error_envelope(self, started: bool) -> None:
        from rust_lsp_mcp.tools.refresh import _reply_message

        message = _reply_message("error", started)
        assert message.startswith("Re-index started; poll status until state is 'ready'.")
        assert "error envelope until a rebuild succeeds" in message
        assert "old index" not in message
        assert "keeps answering" not in message

    def test_error_reply_when_no_rebuild_masks(self) -> None:
        """An errored store whose rebuild could not be seen in flight reads
        "error" and carries the error message (the reply follows the state)."""
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.refresh as refresh_mod

        mgr = _FakeManager()
        store = _errored_store()

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(doc_store_mod, "_doc_store", store),
                patch.object(refresh_mod, "get_doc_store", return_value=store),
                patch.object(refresh_mod, "start_doc_rebuild", return_value=False),
                patch.object(doc_store_mod, "doc_rebuild_in_progress", return_value=False),
            ):
                return await refresh_mod.refresh()

        result = asyncio.run(_inner())
        assert result["doc_index_state"] == "error"
        assert "error envelope until a rebuild succeeds" in result["message"]
