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
    Doc-store wiring (Phase 5):
        - store present and ready → store.rebuild() is called (after restart()).
        - store None → graceful skip; still returns ok (no crash).
        - rebuild() is called after restart(), not before.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from rust_lsp_mcp.analyzer import STATE_INDEXING
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
    """Patch core._manager with *manager* and call refresh(); return the envelope."""
    import rust_lsp_mcp.core as core
    import rust_lsp_mcp.tools.refresh as refresh_mod

    async def _inner() -> dict[str, Any]:
        with patch.object(core, "_manager", manager):
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
# Doc-store wiring (Phase 5)
# ---------------------------------------------------------------------------


def _run_refresh_with_store(manager: Any, store: Any) -> dict[str, Any]:
    """Patch core._manager and refresh module's get_doc_store; call refresh().

    Monkeypatches both:
      - ``core._manager`` (as the existing helper does) for get_manager().
      - ``rust_lsp_mcp.tools.refresh.get_doc_store`` to return *store*.
    """
    import rust_lsp_mcp.core as core
    import rust_lsp_mcp.tools.refresh as refresh_mod

    async def _inner() -> dict[str, Any]:
        with (
            patch.object(core, "_manager", manager),
            patch.object(refresh_mod, "get_doc_store", return_value=store),
        ):
            return await refresh_mod.refresh()

    return asyncio.run(_inner())


class TestRefreshDocStoreWiring:
    """Phase 5: refresh() triggers a doc-store rebuild when the store is present."""

    def test_store_present_rebuild_called(self) -> None:
        """When get_doc_store() returns a store, rebuild() must be called."""
        mgr = _FakeManager()
        store = MagicMock()
        store.rebuild.return_value = 42  # number of chunks indexed

        _run_refresh_with_store(mgr, store)

        store.rebuild.assert_called_once()

    def test_store_present_returns_ok(self) -> None:
        """Presence of a doc store must not change the ok return status."""
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

    def test_store_none_no_crash(self) -> None:
        """When get_doc_store() returns None, refresh must not crash."""
        mgr = _FakeManager()
        result = _run_refresh_with_store(mgr, None)
        assert result["status"] == STATUS_OK

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
