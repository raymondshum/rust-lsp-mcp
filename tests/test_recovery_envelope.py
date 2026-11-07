"""Fast-tier tests for the additive `recovery` envelope field (Batch B, UR-6/7/8/9/10).

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Background: ``envelope.error()`` and ``envelope.not_ready()`` now carry a
``recovery`` field — one of ``"fix_input"`` / ``"refresh"`` / ``"poll_status"``
/ ``"unknown"`` — so a caller can branch deterministically on the next action
instead of NLP-parsing ``message``. See
``docs/audit/2026-07-02-usability-review.md`` (UR-6, narrowed) for the design
rationale; there is deliberately no ``retriable`` bool and no ``code`` enum.

Test coverage:
    Envelope builders (pure, no I/O):
        - ``error()`` defaults to ``recovery="unknown"``.
        - ``error()`` accepts each of the four explicit recovery values.
        - ``not_ready()`` always carries ``recovery="poll_status"``, with the
          default message and with a custom message.
        - ``lsp_failure()`` carries ``recovery="unknown"``.
    End-to-end, one real tool call per recovery value:
        - ``fix_input``  — goto_definition's ``line < 1`` guard.
        - ``fix_input``  — goto_definition's file-containment guard
          (``validate_workspace_file``, shared by every gated tool).
        - ``refresh``    — ``require_ready()``'s ``STATE_ERROR`` branch,
          reached live through goto_definition.
        - ``poll_status`` — goto_definition while the analyzer is indexing.
        - ``unknown``    — goto_definition's catch-all LSP-exception fallback
          (``lsp_failure``).
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import STATE_ERROR, STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import (
    RECOVERY_FIX_INPUT,
    RECOVERY_POLL_STATUS,
    RECOVERY_REFRESH,
    RECOVERY_UNKNOWN,
    STATUS_ERROR,
    STATUS_NOT_READY,
    error,
    lsp_failure,
    not_ready,
)
from rust_lsp_mcp.tools.goto_definition import goto_definition

# ---------------------------------------------------------------------------
# Envelope builder tests
# ---------------------------------------------------------------------------


class TestErrorRecoveryDefaults:
    def test_error_default_recovery_is_unknown(self) -> None:
        result = error("boom")
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_UNKNOWN

    def test_error_explicit_recovery_fix_input(self) -> None:
        result = error("bad args", recovery=RECOVERY_FIX_INPUT)
        assert result["recovery"] == RECOVERY_FIX_INPUT

    def test_error_explicit_recovery_refresh(self) -> None:
        result = error("analyzer dead", recovery=RECOVERY_REFRESH)
        assert result["recovery"] == RECOVERY_REFRESH

    def test_error_explicit_recovery_poll_status(self) -> None:
        """Allowed for parity with not_ready, even though no current call site uses it."""
        result = error("transient-ish", recovery=RECOVERY_POLL_STATUS)
        assert result["recovery"] == RECOVERY_POLL_STATUS

    def test_lsp_failure_carries_recovery_unknown(self) -> None:
        result = lsp_failure(RuntimeError("connection lost"))
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_UNKNOWN
        assert "LSP error" in result["message"]


class TestNotReadyRecovery:
    def test_not_ready_default_message_carries_poll_status(self) -> None:
        result = not_ready()
        assert result["status"] == STATUS_NOT_READY
        assert result["recovery"] == RECOVERY_POLL_STATUS

    def test_not_ready_custom_message_still_carries_poll_status(self) -> None:
        result = not_ready("custom indexing message")
        assert result["status"] == STATUS_NOT_READY
        assert result["recovery"] == RECOVERY_POLL_STATUS
        assert result["message"] == "custom indexing message"


# ---------------------------------------------------------------------------
# End-to-end: one real tool call per recovery value.
#
# Reuses the mock-manager / core._manager-patching idiom from
# tests/test_goto_definition.py (goto_definition is a representative gated
# nav tool: it has an input-validation guard, a file-containment guard, the
# shared readiness gate, and the shared catch-all LSP-exception fallback).
# ---------------------------------------------------------------------------


def _make_manager(state: str) -> AnalyzerManager:
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = state
    mgr._lsp = object() if state == STATE_READY else None  # type: ignore[assignment]
    mgr._repository_root = "/fake/repo"
    mgr._error = "rust-analyzer subprocess died" if state == STATE_ERROR else None
    return mgr


def _run_goto_definition(
    manager: AnalyzerManager | None,
    file: str = "src/main.rs",
    line: int = 1,
    character: int = 1,
    lsp_result: Any = None,
    delegate_raises: Exception | None = None,
) -> dict[str, Any]:
    """Patch core._manager and call goto_definition; inject lsp_result or exception."""

    async def _inner() -> dict[str, Any]:
        with patch.object(core, "_manager", manager):
            if manager is not None and manager.state == STATE_READY:
                mock = (
                    AsyncMock(side_effect=delegate_raises)
                    if delegate_raises is not None
                    else AsyncMock(return_value=lsp_result if lsp_result is not None else [])
                )
                with patch.object(manager, "request_definition", new=mock):
                    return await goto_definition(file, line, character)
            else:
                return await goto_definition(file, line, character)

    return asyncio.run(_inner())


class TestRecoveryEndToEnd:
    def test_fix_input_via_position_guard(self) -> None:
        """goto_definition(line=0, ...) fails 1-indexed validation before any gate."""
        result = _run_goto_definition(None, line=0, character=1)
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_FIX_INPUT

    def test_fix_input_via_file_containment_guard(self) -> None:
        """An absolute/escaping `file` is rejected by validate_workspace_file before the gate."""
        result = _run_goto_definition(None, file="/etc/passwd", line=1, character=1)
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_FIX_INPUT

    def test_refresh_via_require_ready_state_error(self) -> None:
        """A permanently-errored analyzer maps to error+recovery='refresh' (call refresh)."""
        mgr = _make_manager(STATE_ERROR)
        result = _run_goto_definition(mgr, line=1, character=1)
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_REFRESH

    def test_poll_status_via_not_ready_tool_call(self) -> None:
        """While indexing, the gate returns not_ready+recovery='poll_status'."""
        mgr = _make_manager(STATE_INDEXING)
        result = _run_goto_definition(mgr, line=1, character=1)
        assert result["status"] == STATUS_NOT_READY
        assert result["recovery"] == RECOVERY_POLL_STATUS

    def test_unknown_via_lsp_failure(self) -> None:
        """An unexpected delegate exception maps to error+recovery='unknown' (lsp_failure)."""
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(
            mgr, line=1, character=1, delegate_raises=RuntimeError("connection lost")
        )
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_UNKNOWN


# ---------------------------------------------------------------------------
# FW1 (backlog line 150): refresh while stalled; the request timeout
# ---------------------------------------------------------------------------

_PANIC_LINE = "thread 'PrimeCaches#1' (9085) panicked at file_set.rs:31:34:"


def _stalled(state: str) -> AnalyzerManager:
    mgr = _make_manager(state)
    mgr._stall_panic_at = "2026-11-06T01:35:00+00:00"
    mgr._stall_panic_thread = "PrimeCaches#1"
    mgr._stall_panic_line = _PANIC_LINE
    return mgr


def _with_manager(manager: AnalyzerManager | None, fn: Any) -> Any:
    with patch.object(core, "_manager", manager):
        return fn()


class TestStallRecovery:
    def test_require_ready_stalled_while_ready_returns_refresh(self) -> None:
        mgr = _stalled(STATE_READY)
        result = _with_manager(mgr, core.require_ready)
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_REFRESH
        assert result["message"] == mgr.stall_report()["message"]  # ty: ignore[not-subscriptable]

    def test_require_ready_stalled_while_indexing_returns_refresh(self) -> None:
        """A stall can form before the run's first quiescent: true (state
        "indexing"); that caller gets refresh, not poll_status."""
        mgr = _stalled(STATE_INDEXING)
        result = _with_manager(mgr, core.require_ready)
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_REFRESH
        assert "PrimeCaches#1" in result["message"]

    def test_require_ready_without_stall_unchanged(self) -> None:
        assert _with_manager(_make_manager(STATE_READY), core.require_ready) is None
        result = _with_manager(_make_manager(STATE_INDEXING), core.require_ready)
        assert result["status"] == STATUS_NOT_READY

    def test_lsp_failure_stalled_returns_refresh_without_retry_text(self) -> None:
        mgr = _stalled(STATE_READY)
        exc = RuntimeError("request handler panicked: no entry found for key (-32603)")
        result = _with_manager(mgr, lambda: lsp_failure(exc))
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_REFRESH
        assert "Retry the request once" not in result["message"]
        assert result == _with_manager(mgr, core.require_ready)

    def test_lsp_failure_without_stall_unchanged(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _with_manager(mgr, lambda: lsp_failure(RuntimeError("boom")))
        assert result["recovery"] == RECOVERY_UNKNOWN
        assert "Retry the request once" in result["message"]

    def test_lsp_failure_request_timeout_not_stalled(self) -> None:
        from rust_lsp_mcp.analyzer import AnalyzerRequestTimeoutError

        mgr = _make_manager(STATE_READY)
        exc = AnalyzerRequestTimeoutError("definition", 120)
        result = _with_manager(mgr, lambda: lsp_failure(exc))
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_UNKNOWN
        assert "did not answer the definition request within 120 seconds" in result["message"]
        assert "Retry the request once" in result["message"]
        assert "analyzer_stall" in result["message"]

    def test_lsp_failure_request_timeout_stalled_returns_refresh(self) -> None:
        from rust_lsp_mcp.analyzer import AnalyzerRequestTimeoutError

        mgr = _stalled(STATE_READY)
        exc = AnalyzerRequestTimeoutError("definition", 120)
        result = _with_manager(mgr, lambda: lsp_failure(exc))
        assert result["recovery"] == RECOVERY_REFRESH
        assert "PrimeCaches#1" in result["message"]


# ---------------------------------------------------------------------------
# Backlog line 172: a position past the end of the file is fix_input
# ---------------------------------------------------------------------------

# The exception step 1a recorded live (rust-analyzer 1.99.1, 2026-11-07):
# multilspy.lsp_protocol_handler.server.Error, code -32603.
_INVALID_OFFSET_TEXT = "Invalid offset LineCol { line: 99998, col: 0 } (line index length: 18464)"


def _invalid_offset_error() -> Exception:
    from multilspy.lsp_protocol_handler.server import Error

    return Error(-32603, _INVALID_OFFSET_TEXT)  # ty: ignore[invalid-argument-type]


class TestInvalidOffset:
    def test_lsp_failure_invalid_offset_is_fix_input(self) -> None:
        mgr = _make_manager(STATE_READY)
        exc = _invalid_offset_error()
        result = _with_manager(
            mgr, lambda: lsp_failure(exc, position="crates/core/main.rs:99999:1")
        )
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == RECOVERY_FIX_INPUT
        assert "crates/core/main.rs:99999:1" in result["message"]
        assert "past the end" in result["message"]
        assert "Invalid offset" in result["message"]
        assert "Retry the request once" not in result["message"]

    def test_lsp_failure_invalid_offset_without_position(self) -> None:
        result = _with_manager(None, lambda: lsp_failure(_invalid_offset_error()))
        assert result["recovery"] == RECOVERY_FIX_INPUT
        assert "past the end" in result["message"]

    def test_other_minus_32603_error_stays_unknown(self) -> None:
        from multilspy.lsp_protocol_handler.server import Error

        exc = Error(-32603, "request handler panicked: no entry found for key")  # ty: ignore[invalid-argument-type]
        result = _with_manager(_make_manager(STATE_READY), lambda: lsp_failure(exc))
        assert result["recovery"] == RECOVERY_UNKNOWN
        assert "Retry the request once" in result["message"]
