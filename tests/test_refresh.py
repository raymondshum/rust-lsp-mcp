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
    Doc-store wiring (DS-14 recovery path):
        - store present, healthy → store.rebuild() is called (after restart());
          init_doc_store is NOT called.
        - store None → graceful re-init via init_doc_store; still returns ok.
        - store in "error" state → re-init via init_doc_store (rebuild() is
          NOT called on the known-broken store).
        - rebuild()/init_doc_store is called after restart(), not before.
        - re-init failure → error envelope; restart() still awaited once.
    End-to-end doc-store recovery (real DocStore, fake embedding function,
    tmp chroma path): a failed rebuild followed by a successful refresh
    recovers search_docs from error to ok/not_found.
"""

import asyncio
import hashlib
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import chromadb
import numpy as np
import pytest

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
            return await refresh_mod.refresh()

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
    not reflect a live analyzer that readied *during* the blocking doc rebuild.

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

        def _rebuild_that_readies_analyzer() -> int:
            mgr.state = STATE_READY
            return 0

        store.rebuild = _rebuild_that_readies_analyzer

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(refresh_mod, "get_doc_store", return_value=store),
            ):
                return await refresh_mod.refresh()

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
            return await refresh_mod.refresh()

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
        store.rebuild.side_effect = lambda: call_log.append("rebuild")

        _run_refresh_with_store(mgr, store)

        assert call_log == ["restart", "rebuild"], (
            f"Expected restart before rebuild, got: {call_log}"
        )

    def test_rebuild_raises_returns_error_envelope(self) -> None:
        """When rebuild() raises, refresh must return an error envelope (not propagate).

        The analyzer restart has already fired by the time rebuild() runs, so the
        error message must make clear that re-indexing is underway even though the
        doc-store rebuild failed.  refresh() must NOT raise; the exception must be
        caught and surfaced as a structured error envelope.
        """
        mgr = _FakeManager()
        store = MagicMock()
        store.rebuild.side_effect = RuntimeError("embedding model unavailable")

        result = _run_refresh_with_store(mgr, store)

        assert result["status"] == STATUS_ERROR
        assert "message" in result
        # Message must mention the doc rebuild failure
        assert "documentation rebuild failed" in result["message"].lower() or (
            "doc" in result["message"].lower() and "rebuild" in result["message"].lower()
        )
        # restart() must still have been called before the failure
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

    def test_reinit_failure_returns_error_envelope(self) -> None:
        """init_doc_store raising must produce an error envelope, not propagate.

        restart() must still have been awaited exactly once before the failure —
        the analyzer re-index is not rolled back by a doc-store failure.
        """
        mgr = _FakeManager()
        init_mock = MagicMock(side_effect=RuntimeError("chroma path unwritable"))

        result = _run_refresh_with_store(mgr, None, init_doc_store_mock=init_mock)

        assert result["status"] == STATUS_ERROR
        assert "chroma path unwritable" in result["message"]
        mgr.restart.assert_awaited_once()


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

        def _init_with_fake_ef(s: Settings) -> doc_store_mod.DocStore:
            return doc_store_mod.init_doc_store(s, embedding_function=ef)

        async def _do_refresh() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(refresh_mod, "get_doc_store", return_value=store),
                patch.object(refresh_mod, "get_settings", return_value=settings),
                patch.object(refresh_mod, "init_doc_store", _init_with_fake_ef),
            ):
                return await refresh_mod.refresh()

        refresh_result = asyncio.run(_do_refresh())
        assert refresh_result["status"] == STATUS_OK

        # The refresh path called init_doc_store, which set a NEW singleton in
        # doc_store module — fetch it directly to confirm recovery.
        recovered_store = doc_store_mod.get_doc_store()
        assert recovered_store is not None
        assert recovered_store.is_ready is True

        recovered_result = asyncio.run(_search(recovered_store))
        assert recovered_result["status"] in (STATUS_OK, STATUS_NOT_FOUND)

        doc_store_mod.clear_doc_store()
