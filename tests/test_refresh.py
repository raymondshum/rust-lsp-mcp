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
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

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
