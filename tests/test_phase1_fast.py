"""Fast-tier tests for Phase 1: envelope + readiness gating.

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    - Envelope: each constructor produces the correct shape and ``status`` value.
    - ``not_found`` vs ``ok``+empty are distinct shapes.
    - Gating: with state=``indexing``, ``require_ready`` returns ``not_ready``.
    - Gating: with state=``ready``, ``require_ready`` returns None.
    - Tool ``analyzer_status`` reports the correct state via the ``ok`` envelope.
    - Tool ``probe`` returns ``not_ready`` when indexing, ``ok`` when ready.
"""

from typing import Any
from unittest.mock import patch

from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import (
    STATUS_ERROR,
    STATUS_NOT_FOUND,
    STATUS_NOT_READY,
    STATUS_OK,
    error,
    not_found,
    not_ready,
    ok,
)

# ---------------------------------------------------------------------------
# Envelope shape tests
# ---------------------------------------------------------------------------


class TestEnvelopeBuilders:
    def test_ok_bare(self) -> None:
        result = ok()
        assert result == {"status": STATUS_OK}

    def test_ok_with_extra_fields(self) -> None:
        result = ok(state="ready", count=42)
        assert result["status"] == STATUS_OK
        assert result["state"] == "ready"
        assert result["count"] == 42

    def test_not_ready_default(self) -> None:
        result = not_ready()
        assert result["status"] == STATUS_NOT_READY
        assert "message" in result
        assert isinstance(result["message"], str)

    def test_not_ready_custom_message(self) -> None:
        result = not_ready("custom msg")
        assert result["status"] == STATUS_NOT_READY
        assert result["message"] == "custom msg"

    def test_not_found_default(self) -> None:
        result = not_found()
        assert result["status"] == STATUS_NOT_FOUND
        assert "message" in result

    def test_not_found_custom_message(self) -> None:
        result = not_found("sym X not found")
        assert result["status"] == STATUS_NOT_FOUND
        assert result["message"] == "sym X not found"

    def test_error_message(self) -> None:
        result = error("boom")
        assert result["status"] == STATUS_ERROR
        assert result["message"] == "boom"

    def test_not_found_vs_ok_empty_are_distinct(self) -> None:
        """``not_found`` and ``ok``+empty list must NOT be interchangeable."""
        nf = not_found()
        ok_empty = ok(results=[])
        assert nf["status"] != ok_empty["status"]
        # not_found carries a message; ok+empty carries a results list
        assert "message" in nf
        assert "results" in ok_empty
        assert "message" not in ok_empty


# ---------------------------------------------------------------------------
# Readiness gate tests — drive the manager flag directly, no real analyzer
# ---------------------------------------------------------------------------


def _make_manager(state: str) -> AnalyzerManager:
    """Create an AnalyzerManager and patch its state directly."""
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = state
    return mgr


class TestRequireReady:
    def _call_require_ready(self, manager: AnalyzerManager | None) -> dict[str, Any] | None:
        """Patch server._manager and call require_ready()."""
        import rust_lsp_mcp.server as srv

        with patch.object(srv, "_manager", manager):
            return srv.require_ready()

    def test_returns_not_ready_when_indexing(self) -> None:
        mgr = _make_manager(STATE_INDEXING)
        result = self._call_require_ready(mgr)
        assert result is not None
        assert result["status"] == STATUS_NOT_READY

    def test_returns_none_when_ready(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = self._call_require_ready(mgr)
        assert result is None

    def test_returns_not_ready_when_manager_is_none(self) -> None:
        """If the server hasn't booted yet (_manager is None), gate must block."""
        result = self._call_require_ready(None)
        assert result is not None
        assert result["status"] == STATUS_NOT_READY


# ---------------------------------------------------------------------------
# Tool: analyzer_status
# ---------------------------------------------------------------------------


class TestAnalyzerStatusTool:
    def _call_analyzer_status(self, manager: AnalyzerManager | None) -> dict[str, Any]:
        import rust_lsp_mcp.server as srv

        with patch.object(srv, "_manager", manager):
            return srv.analyzer_status()

    def test_reports_indexing_before_ready(self) -> None:
        mgr = _make_manager(STATE_INDEXING)
        result = self._call_analyzer_status(mgr)
        assert result["status"] == STATUS_OK
        assert result["state"] == STATE_INDEXING

    def test_reports_ready_after_ready(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = self._call_analyzer_status(mgr)
        assert result["status"] == STATUS_OK
        assert result["state"] == STATE_READY

    def test_reports_indexing_when_manager_none(self) -> None:
        """Before lifespan starts, the tool must report indexing, not crash."""
        result = self._call_analyzer_status(None)
        assert result["status"] == STATUS_OK
        assert result["state"] == STATE_INDEXING


# ---------------------------------------------------------------------------
# Tool: probe (gated)
# ---------------------------------------------------------------------------


class TestProbeTool:
    def _call_probe(self, manager: AnalyzerManager | None) -> dict[str, Any]:
        import rust_lsp_mcp.server as srv

        with patch.object(srv, "_manager", manager):
            return srv.probe()

    def test_probe_returns_not_ready_while_indexing(self) -> None:
        mgr = _make_manager(STATE_INDEXING)
        result = self._call_probe(mgr)
        assert result["status"] == STATUS_NOT_READY
        # Critical: must NOT be ok or empty
        assert result["status"] != STATUS_OK

    def test_probe_returns_ok_when_ready(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = self._call_probe(mgr)
        assert result["status"] == STATUS_OK

    def test_probe_not_ready_is_not_empty_ok(self) -> None:
        """Fail-fast invariant: a not-ready probe must never look like ok+empty."""
        mgr = _make_manager(STATE_INDEXING)
        result = self._call_probe(mgr)
        # status must be not_ready, and there must be no 'results' field
        assert result["status"] == STATUS_NOT_READY
        assert "results" not in result
