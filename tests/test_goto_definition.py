"""Fast-tier tests for goto_definition tool.

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    goto_definition:
        - Input validation: line < 1 → error (before readiness gate).
        - Input validation: character < 1 → error (before readiness gate).
        - Input validation: both < 1 → error.
        - not_ready when analyzer is not ready (gate blocks call).
        - not_ready when manager is None.
        - Happy path: fake request_definition returns 1+ Location dicts with
          relativePath+range → correct 1-indexed definitions in response.
        - Boundary round-trip: input line=5, char=13 reaches the delegate as
          line=4, char=12 (0-indexed), and the returned LSP position (4, 12)
          maps back to external (5, 13) in the output.
        - None return from delegate → not_found.
        - Empty list return from delegate → not_found.
        - All locations skipped (no usable path) → not_found.
        - Exception from delegate → error envelope with LSP error message.
        - Multiple definitions → all returned.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_ERROR, STATUS_NOT_FOUND, STATUS_NOT_READY, STATUS_OK

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(state: str) -> AnalyzerManager:
    """Create an AnalyzerManager stub with state set directly (no real task).

    When state is STATE_READY, ``_lsp`` is set to a non-None sentinel so that
    the ``is_ready`` property (which requires BOTH state==ready AND _lsp!=None)
    behaves correctly.  Indexing fakes leave ``_lsp`` as None — the gate must
    block regardless of ``_lsp`` when ``state != STATE_READY``.
    """
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = state
    mgr._lsp = object() if state == STATE_READY else None  # type: ignore[assignment]
    mgr._repository_root = "/fake/repo"
    return mgr


def _make_location(
    rel_path: str,
    line: int,
    character: int,
    end_line: int | None = None,
    end_character: int | None = None,
) -> dict[str, Any]:
    """Build a minimal multilspy Location dict with relativePath and range.

    ``line`` and ``character`` are 0-indexed LSP values.
    ``relativePath`` is already pre-populated — multilspy normalizes
    Location[] and LocationLink[] to populate this field.
    """
    return {
        "relativePath": rel_path,
        "uri": f"file:///fake/repo/{rel_path}",
        "range": {
            "start": {"line": line, "character": character},
            "end": {
                "line": end_line if end_line is not None else line,
                "character": end_character if end_character is not None else character + 1,
            },
        },
    }


def _run_goto_definition(
    manager: AnalyzerManager | None,
    file: str,
    line: int,
    character: int,
    lsp_result: Any,
    delegate_raises: Exception | None = None,
) -> dict[str, Any]:
    """Patch core._manager and call goto_definition; inject lsp_result or exception."""
    from rust_lsp_mcp.tools.goto_definition import goto_definition

    async def _inner() -> dict[str, Any]:
        with patch.object(core, "_manager", manager):
            if manager is not None and manager.state == STATE_READY:
                if delegate_raises is not None:
                    mock = AsyncMock(side_effect=delegate_raises)
                else:
                    mock = AsyncMock(return_value=lsp_result)
                with patch.object(manager, "request_definition", new=mock):
                    return await goto_definition(file, line, character)
            else:
                return await goto_definition(file, line, character)

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Input validation tests (must fire before the readiness gate)
# ---------------------------------------------------------------------------


class TestInputValidation:
    """Invalid 1-indexed inputs must return error without needing a ready analyzer."""

    def test_line_zero_returns_error(self) -> None:
        """line=0 is invalid (1-indexed); must return error without checking readiness."""
        # No manager at all — proves validation fires before the gate
        result = _run_goto_definition(None, "src/main.rs", 0, 1, lsp_result=[])
        assert result["status"] == STATUS_ERROR
        assert "1-indexed" in result["message"]

    def test_line_negative_returns_error(self) -> None:
        result = _run_goto_definition(None, "src/main.rs", -1, 1, lsp_result=[])
        assert result["status"] == STATUS_ERROR
        assert "1-indexed" in result["message"]

    def test_character_zero_returns_error(self) -> None:
        """character=0 is invalid (1-indexed); must return error without checking readiness."""
        result = _run_goto_definition(None, "src/main.rs", 1, 0, lsp_result=[])
        assert result["status"] == STATUS_ERROR
        assert "1-indexed" in result["message"]

    def test_character_negative_returns_error(self) -> None:
        result = _run_goto_definition(None, "src/main.rs", 1, -5, lsp_result=[])
        assert result["status"] == STATUS_ERROR
        assert "1-indexed" in result["message"]

    def test_both_zero_returns_error(self) -> None:
        result = _run_goto_definition(None, "src/main.rs", 0, 0, lsp_result=[])
        assert result["status"] == STATUS_ERROR

    def test_valid_minimum_inputs_do_not_error_on_validation(self) -> None:
        """line=1, character=1 must pass validation (will hit gate if manager is None)."""
        result = _run_goto_definition(None, "src/main.rs", 1, 1, lsp_result=[])
        # With None manager, should get not_ready (gate), NOT error from validation
        assert result["status"] == STATUS_NOT_READY

    def test_error_returned_even_when_manager_is_none(self) -> None:
        """Validation must run before the gate — no manager needed."""
        result = _run_goto_definition(None, "src/main.rs", 0, 0, lsp_result=[])
        assert result["status"] == STATUS_ERROR


# ---------------------------------------------------------------------------
# Readiness gate tests
# ---------------------------------------------------------------------------


class TestReadinessGate:
    """Gating: goto_definition while indexing must return not_ready."""

    def test_returns_not_ready_while_indexing(self) -> None:
        mgr = _make_manager(STATE_INDEXING)
        result = _run_goto_definition(mgr, "src/main.rs", 1, 1, lsp_result=[])
        assert result["status"] == STATUS_NOT_READY

    def test_returns_not_ready_when_manager_none(self) -> None:
        result = _run_goto_definition(None, "src/main.rs", 1, 1, lsp_result=[])
        assert result["status"] == STATUS_NOT_READY

    def test_does_not_call_lsp_when_not_ready(self) -> None:
        """Analyzer delegate must never be called when gated."""
        mgr = _make_manager(STATE_INDEXING)
        mock_delegate = AsyncMock()
        mgr._lsp = mock_delegate

        from rust_lsp_mcp.tools.goto_definition import goto_definition

        async def _inner() -> dict[str, Any]:
            with patch.object(core, "_manager", mgr):
                return await goto_definition("src/main.rs", 1, 1)

        asyncio.run(_inner())
        mock_delegate.request_definition.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path — correct field mapping and 1-indexed output
# ---------------------------------------------------------------------------


class TestGotoDefinitionHappyPath:
    """Correct field mapping, 1-indexed output, single and multiple definitions."""

    def test_single_definition_correct_fields(self) -> None:
        """A single location maps to all expected fields with 1-indexed positions."""
        # LSP location at line=9, char=3 → external line=10, char=4
        loc = _make_location("src/lib.rs", line=9, character=3)
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(mgr, "src/main.rs", 1, 1, lsp_result=[loc])

        assert result["status"] == STATUS_OK
        assert "definitions" in result
        assert len(result["definitions"]) == 1

        d = result["definitions"][0]
        assert d["file"] == "src/lib.rs"
        assert d["line"] == 10  # LSP 9 → external 10
        assert d["character"] == 4  # LSP 3 → external 4

    def test_output_positions_are_1indexed(self) -> None:
        """Lines and characters in definitions must be 1-indexed (never 0)."""
        # LSP (0, 0) → external (1, 1): the minimum valid output
        loc = _make_location("src/lib.rs", line=0, character=0)
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(mgr, "src/main.rs", 1, 1, lsp_result=[loc])

        assert result["status"] == STATUS_OK
        d = result["definitions"][0]
        assert d["line"] >= 1, "line must be 1-indexed (>=1)"
        assert d["character"] >= 1, "character must be 1-indexed (>=1)"

    def test_multiple_definitions_all_returned(self) -> None:
        """Multiple definition sites (e.g. trait impls) are all returned."""
        locs = [
            _make_location("src/a.rs", line=10, character=4),
            _make_location("src/b.rs", line=20, character=0),
        ]
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(mgr, "src/main.rs", 1, 1, lsp_result=locs)

        assert result["status"] == STATUS_OK
        assert len(result["definitions"]) == 2
        files = {d["file"] for d in result["definitions"]}
        assert files == {"src/a.rs", "src/b.rs"}


# ---------------------------------------------------------------------------
# Boundary round-trip: line=5, char=13 → delegate receives (4, 12)
# ---------------------------------------------------------------------------


class TestPositionRoundTrip:
    """Verify the 1↔0-indexed boundary: input line=5,char=13 reaches delegate as (4,12)."""

    def test_input_5_13_reaches_delegate_as_4_12(self) -> None:
        """External (5, 13) → LSP (4, 12) — the subtract-1 boundary."""
        captured: dict[str, Any] = {}

        async def _capture_delegate(
            file: str, lsp_line: int, lsp_char: int
        ) -> list[dict[str, Any]]:
            captured["file"] = file
            captured["line"] = lsp_line
            captured["character"] = lsp_char
            # Return a location so the tool doesn't short-circuit to not_found
            return [_make_location("src/lib.rs", line=lsp_line, character=lsp_char)]

        mgr = _make_manager(STATE_READY)
        from rust_lsp_mcp.tools.goto_definition import goto_definition

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_definition", side_effect=_capture_delegate),
            ):
                return await goto_definition("src/main.rs", 5, 13)

        asyncio.run(_inner())

        assert captured["line"] == 4, (
            f"Expected delegate to receive LSP line=4 (from external 5), got {captured['line']}"
        )
        assert captured["character"] == 12, (
            "Expected delegate to receive LSP char=12 (from external 13), "
            f"got {captured['character']}"
        )

    def test_lsp_4_12_maps_to_external_5_13_in_output(self) -> None:
        """LSP response (4, 12) must map back to external (5, 13) in the output."""
        # The delegate returns a location at LSP line=4, char=12
        loc = _make_location("src/lib.rs", line=4, character=12)
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(mgr, "src/main.rs", 5, 13, lsp_result=[loc])

        assert result["status"] == STATUS_OK
        d = result["definitions"][0]
        assert d["line"] == 5, f"Expected external line=5 (from LSP 4), got {d['line']}"
        assert d["character"] == 13, (
            f"Expected external char=13 (from LSP 12), got {d['character']}"
        )

    def test_full_round_trip_5_13(self) -> None:
        """End-to-end: external (5,13) in → LSP (4,12) at delegate → external (5,13) out."""
        received: dict[str, Any] = {}

        async def _echo_delegate(file: str, lsp_line: int, lsp_char: int) -> list[dict[str, Any]]:
            received["lsp_line"] = lsp_line
            received["lsp_char"] = lsp_char
            # Echo the same position back as the definition location
            return [_make_location("src/lib.rs", line=lsp_line, character=lsp_char)]

        mgr = _make_manager(STATE_READY)
        from rust_lsp_mcp.tools.goto_definition import goto_definition

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_definition", side_effect=_echo_delegate),
            ):
                return await goto_definition("src/main.rs", 5, 13)

        result = asyncio.run(_inner())

        # Verify delegate received 0-indexed (4, 12)
        assert received["lsp_line"] == 4
        assert received["lsp_char"] == 12

        # Verify output is back to 1-indexed (5, 13)
        assert result["status"] == STATUS_OK
        d = result["definitions"][0]
        assert d["line"] == 5
        assert d["character"] == 13


# ---------------------------------------------------------------------------
# not_found cases
# ---------------------------------------------------------------------------


class TestGotoDefinitionNotFound:
    """Empty list, None, and all-skipped → not_found, never ok+empty.

    The delegate now returns None (not []) when the underlying multilspy call
    raises AssertionError on a null LSP response.  goto_definition treats both
    None and [] as not_found (there is no "zero definitions" meaningful semantic
    distinct from "no symbol here" for this tool).
    """

    def test_empty_list_returns_not_found(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(mgr, "src/main.rs", 1, 1, lsp_result=[])
        assert result["status"] == STATUS_NOT_FOUND
        assert result["status"] != STATUS_OK
        assert "definitions" not in result

    def test_none_returns_not_found(self) -> None:
        """Delegate returning None (null LSP response → no symbol at position) → not_found."""
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(mgr, "src/main.rs", 1, 1, lsp_result=None)
        assert result["status"] == STATUS_NOT_FOUND
        assert result["status"] != STATUS_OK
        assert "definitions" not in result

    def test_none_from_assertion_error_path_returns_not_found(self) -> None:
        """Delegate mapped from AssertionError (null LSP null response) → not_found.

        Simulates the exact multilspy 0.0.15 behaviour: the delegate's
        AssertionError catch returns None, and the tool must return not_found.
        """
        from rust_lsp_mcp.tools.goto_definition import goto_definition

        mgr = _make_manager(STATE_READY)

        async def _inner() -> dict[str, Any]:
            # The delegate returns None (as it would after catching AssertionError).
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_definition", new=AsyncMock(return_value=None)),
            ):
                return await goto_definition("src/main.rs", 42, 10)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_NOT_FOUND, (
            f"Delegate returning None (AssertionError from multilspy) must yield not_found, "
            f"got {result!r}"
        )

    def test_not_found_has_message(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(mgr, "src/main.rs", 1, 1, lsp_result=[])
        assert "message" in result

    def test_not_found_is_distinct_from_ok_empty(self) -> None:
        """Critical invariant: not_found ≠ ok+[] — the statuses must differ."""
        from rust_lsp_mcp.envelope import ok as _ok

        mgr = _make_manager(STATE_READY)
        nf = _run_goto_definition(mgr, "src/main.rs", 1, 1, lsp_result=[])
        assert nf["status"] == STATUS_NOT_FOUND

        ok_empty = _ok(definitions=[])
        assert nf["status"] != ok_empty["status"]

    def test_all_skipped_locations_returns_not_found(self) -> None:
        """If every returned location has no usable path, result is not_found."""
        # A location with no relativePath and an out-of-repo URI
        bad_loc: dict[str, Any] = {
            "relativePath": None,
            "uri": "file:///some/other/place/lib.rs",  # outside /fake/repo
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 1},
            },
        }
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(mgr, "src/main.rs", 1, 1, lsp_result=[bad_loc])
        assert result["status"] == STATUS_NOT_FOUND


def _outside_location(rel_tail: str) -> dict[str, Any]:
    """An out-of-workspace location as multilspy builds it: ``..``-prefixed
    ``relativePath`` and a ``uri`` outside the ``/fake/repo`` root."""
    return {
        "relativePath": f"../../usr/local/rustup/toolchains/x/lib/rustlib/src/{rel_tail}",
        "uri": f"file:///usr/local/rustup/toolchains/x/lib/rustlib/src/{rel_tail}",
        "range": {
            "start": {"line": 10, "character": 4},
            "end": {"line": 10, "character": 7},
        },
    }


class TestOutOfWorkspaceDefinitions:
    """Issue #114: an all-outside answer is told apart from a true miss."""

    def test_single_outside_location_names_workspace_boundary(self) -> None:
        mgr = _make_manager(STATE_READY)
        loc = _outside_location("alloc/src/vec/mod.rs")
        result = _run_goto_definition(mgr, "src/main.rs", 3, 9, lsp_result=[loc])
        assert result["status"] == STATUS_NOT_FOUND
        assert result["message"] == (
            "1 definition location(s) for src/main.rs:3:9 resolve outside the "
            "workspace; only in-workspace definitions are returned (the standard "
            "library and dependencies are not)."
        )
        assert "No definition found" not in result["message"]

    def test_several_outside_locations_report_their_count(self) -> None:
        mgr = _make_manager(STATE_READY)
        locs = [
            _outside_location("alloc/src/vec/mod.rs"),
            _outside_location("core/src/slice/mod.rs"),
        ]
        result = _run_goto_definition(mgr, "src/main.rs", 3, 9, lsp_result=locs)
        assert result["status"] == STATUS_NOT_FOUND
        assert result["message"].startswith("2 definition location(s) for src/main.rs:3:9 ")
        assert "resolve outside the workspace" in result["message"]

    def test_empty_result_keeps_no_definition_message(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(mgr, "src/main.rs", 3, 9, lsp_result=[])
        assert result["status"] == STATUS_NOT_FOUND
        assert result["message"] == "No definition found at src/main.rs:3:9."

    def test_mixed_inside_and_outside_returns_inside_only(self) -> None:
        mgr = _make_manager(STATE_READY)
        inside = _make_location("src/lib.rs", 4, 0)
        outside = _outside_location("alloc/src/vec/mod.rs")
        result = _run_goto_definition(mgr, "src/main.rs", 3, 9, lsp_result=[outside, inside])
        assert result["status"] == STATUS_OK
        assert result["definitions"] == [{"file": "src/lib.rs", "line": 5, "character": 1}]


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestGotoDefinitionError:
    """Exception from delegate → error envelope."""

    def test_lsp_exception_returns_error(self) -> None:
        """RuntimeError from request_definition → error envelope with message."""
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(
            mgr,
            "src/main.rs",
            1,
            1,
            lsp_result=[],
            delegate_raises=RuntimeError("connection lost"),
        )
        assert result["status"] == STATUS_ERROR
        assert "LSP error" in result["message"]
        assert "connection lost" in result["message"]

    def test_lsp_exception_does_not_propagate(self) -> None:
        """The tool must catch delegate exceptions and return an error envelope, not raise."""
        mgr = _make_manager(STATE_READY)
        # Should not raise — must return an error envelope
        result = _run_goto_definition(
            mgr,
            "src/main.rs",
            1,
            1,
            lsp_result=[],
            delegate_raises=ValueError("bad state"),
        )
        assert result["status"] == STATUS_ERROR


# rust-analyzer's out-of-range error as step 1a recorded it live (rust-analyzer
# 1.99.1, 2026-11-07): multilspy.lsp_protocol_handler.server.Error, -32603.
_INVALID_OFFSET = "Invalid offset LineCol { line: 99998, col: 0 } (line index length: 18464)"


def _invalid_offset_error() -> Exception:
    from multilspy.lsp_protocol_handler.server import Error

    return Error(-32603, _INVALID_OFFSET)  # ty: ignore[invalid-argument-type]


class TestOutOfRangePosition:
    """Backlog line 172: a position past the end of the file is fix_input."""

    def test_invalid_offset_returns_fix_input_naming_position(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(
            mgr,
            "crates/core/main.rs",
            99999,
            1,
            lsp_result=[],
            delegate_raises=_invalid_offset_error(),
        )
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == "fix_input"
        assert "crates/core/main.rs:99999:1" in result["message"]
        assert "past the end" in result["message"]

    def test_unrelated_exception_stays_unknown(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_goto_definition(
            mgr, "src/main.rs", 1, 1, lsp_result=[], delegate_raises=RuntimeError("boom")
        )
        assert result["recovery"] == "unknown"
        assert "Retry the request once" in result["message"]


class TestPositionAboveU32:
    """A 1-indexed value whose 0-indexed form does not fit LSP's u32 is fix_input."""

    def _run(self, line: int, character: int) -> tuple[dict[str, Any], AsyncMock]:
        from rust_lsp_mcp.tools.goto_definition import goto_definition

        mgr = _make_manager(STATE_READY)
        delegate = AsyncMock(return_value=[])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_definition", new=delegate),
            ):
                return await goto_definition("crates/core/main.rs", line, character)

        return asyncio.run(_inner()), delegate

    def test_line_above_u32_is_fix_input_without_delegate(self) -> None:
        result, delegate = self._run(4294967297, 1)
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == "fix_input"
        assert "crates/core/main.rs:4294967297:1" in result["message"]
        assert "4294967296" in result["message"]
        delegate.assert_not_called()

    def test_character_above_u32_is_fix_input_without_delegate(self) -> None:
        result, delegate = self._run(1, 4294967297)
        assert result["recovery"] == "fix_input"
        assert "crates/core/main.rs:1:4294967297" in result["message"]
        delegate.assert_not_called()

    def test_line_at_u32_bound_reaches_delegate(self) -> None:
        _result, delegate = self._run(4294967296, 1)
        delegate.assert_called_once()

    def test_huge_and_non_integer_values_are_fix_input(self) -> None:
        """The R-gate's 99999999999999999999 (sent on as 1e+20) and a float."""
        for line in (10**20, 1e20):
            result, delegate = self._run(line, 1)  # ty: ignore[invalid-argument-type]
            assert result["recovery"] == "fix_input", line
            delegate.assert_not_called()


class TestStalledAnalyzer:
    """FW1 (backlog line 150): a stalled analyzer returns refresh, no delegate call."""

    def test_stalled_manager_returns_refresh_without_delegate(self) -> None:
        mgr = _make_manager(STATE_READY)
        mgr._stall_panic_at = "2026-11-06T01:35:00+00:00"
        mgr._stall_panic_thread = "PrimeCaches#1"
        mgr._stall_panic_line = "thread 'PrimeCaches#1' (9085) panicked at file_set.rs:31:34:"
        delegate = AsyncMock(return_value=[])

        from rust_lsp_mcp.tools.goto_definition import goto_definition

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_definition", new=delegate),
            ):
                return await goto_definition("src/main.rs", 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_ERROR
        assert result["recovery"] == "refresh"
        assert "PrimeCaches#1" in result["message"]
        delegate.assert_not_called()
