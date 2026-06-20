"""Fast-tier tests for the hover tool.

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    hover tool:
        - Input validation: line < 1 → error; character < 1 → error.
        - Not-ready gate: indexing → not_ready; manager None → not_ready.
        - Happy path: MarkupContent dict → ok + value string.
        - MarkedString plain str → ok + the string.
        - MarkedString dict ({language, value}) → ok + the value string.
        - List of contents → ok + joined string.
        - request_hover returns None → not_found.
        - Contents value empty/whitespace → not_found.
        - Exception from delegate → error envelope.
        - Boundary round-trip: input (5, 13) → delegate (4, 12).
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_ERROR, STATUS_NOT_FOUND, STATUS_NOT_READY, STATUS_OK
from rust_lsp_mcp.tools.hover import _contents_to_str

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_manager(state: str) -> AnalyzerManager:
    """Create an AnalyzerManager stub with state set directly (no real task).

    Mirrors the idiom from test_phase2_fast.py: when state is STATE_READY,
    ``_lsp`` is set to a non-None sentinel so that ``is_ready`` (which
    requires BOTH state==ready AND _lsp!=None) behaves correctly.
    """
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = state
    mgr._lsp = object() if state == STATE_READY else None  # type: ignore[assignment]
    mgr._repository_root = "/fake/repo"
    return mgr


def _run_hover(
    manager: AnalyzerManager | None,
    file: str,
    line: int,
    character: int,
    hover_result: Any,
    raise_exc: Exception | None = None,
) -> dict[str, Any]:
    """Patch core._manager and call hover; inject hover_result for request_hover."""
    import rust_lsp_mcp.core as core
    import rust_lsp_mcp.tools.hover as hover_mod

    async def _inner() -> dict[str, Any]:
        with patch.object(core, "_manager", manager):
            if manager is not None and manager.state == STATE_READY:
                if raise_exc is not None:
                    mock = AsyncMock(side_effect=raise_exc)
                else:
                    mock = AsyncMock(return_value=hover_result)
                with patch.object(manager, "request_hover", new=mock):
                    return await hover_mod.hover(file, line, character)
            else:
                return await hover_mod.hover(file, line, character)

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# _contents_to_str unit tests
# ---------------------------------------------------------------------------


class TestContentsToStr:
    """Unit tests for the _contents_to_str normalization helper."""

    def test_markup_content_dict_returns_value(self) -> None:
        """MarkupContent dict → returns the 'value' field."""
        result = _contents_to_str({"kind": "markdown", "value": "**Hello**"})
        assert result == "**Hello**"

    def test_marked_string_plain_str(self) -> None:
        """Plain string MarkedString → returned as-is."""
        result = _contents_to_str("some text")
        assert result == "some text"

    def test_marked_string_dict_with_language(self) -> None:
        """MarkedString dict with {language, value} → returns the 'value' field."""
        result = _contents_to_str({"language": "rust", "value": "fn foo() -> i32"})
        assert result == "fn foo() -> i32"

    def test_list_joined_with_double_newline(self) -> None:
        """List of items → each normalized and joined with '\\n\\n'."""
        result = _contents_to_str(
            [
                {"language": "rust", "value": "fn foo()"},
                "some docs",
                {"kind": "markdown", "value": "more **docs**"},
            ]
        )
        assert result == "fn foo()\n\nsome docs\n\nmore **docs**"

    def test_empty_list_returns_empty_string(self) -> None:
        """Empty list → empty string."""
        result = _contents_to_str([])
        assert result == ""

    def test_dict_missing_value_key_returns_empty(self) -> None:
        """Dict without 'value' key → empty string (graceful fallback)."""
        result = _contents_to_str({"kind": "plaintext"})
        assert result == ""

    def test_empty_string_returns_empty(self) -> None:
        """Empty string → empty string."""
        result = _contents_to_str("")
        assert result == ""

    def test_unknown_type_returns_empty(self) -> None:
        """Unexpected type (e.g. int) → empty string, no crash."""
        result = _contents_to_str(42)
        assert result == ""

    def test_nested_list(self) -> None:
        """Nested list (list inside list) — outer join; inner list becomes a joined string."""
        result = _contents_to_str([["a", "b"], "c"])
        assert result == "a\n\nb\n\nc"


# ---------------------------------------------------------------------------
# Input validation tests
# ---------------------------------------------------------------------------


class TestHoverInputValidation:
    """Invalid inputs must return error before calling the analyzer."""

    def test_line_zero_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 0, 1, None)
        assert result["status"] == STATUS_ERROR
        assert "message" in result

    def test_line_negative_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", -1, 1, None)
        assert result["status"] == STATUS_ERROR

    def test_character_zero_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 1, 0, None)
        assert result["status"] == STATUS_ERROR
        assert "message" in result

    def test_character_negative_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 1, -5, None)
        assert result["status"] == STATUS_ERROR

    def test_both_zero_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 0, 0, None)
        assert result["status"] == STATUS_ERROR

    def test_error_message_mentions_position(self) -> None:
        """Error message should mention the offending position values."""
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 0, 3, None)
        assert "0" in result["message"]


# ---------------------------------------------------------------------------
# Readiness gate tests
# ---------------------------------------------------------------------------


class TestHoverNotReady:
    """hover while indexing or manager None → not_ready."""

    def test_returns_not_ready_while_indexing(self) -> None:
        mgr = _make_manager(STATE_INDEXING)
        result = _run_hover(mgr, "src/lib.rs", 1, 1, None)
        assert result["status"] == STATUS_NOT_READY

    def test_returns_not_ready_when_manager_none(self) -> None:
        result = _run_hover(None, "src/lib.rs", 1, 1, None)
        assert result["status"] == STATUS_NOT_READY

    def test_not_ready_has_message(self) -> None:
        mgr = _make_manager(STATE_INDEXING)
        result = _run_hover(mgr, "src/lib.rs", 1, 1, None)
        assert "message" in result

    def test_does_not_call_lsp_when_not_ready(self) -> None:
        """request_hover must never be called when gated."""
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.hover as hover_mod

        mgr = _make_manager(STATE_INDEXING)
        mock_delegate = AsyncMock()

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_hover", new=mock_delegate),
            ):
                return await hover_mod.hover("src/lib.rs", 1, 1)

        asyncio.run(_inner())
        mock_delegate.assert_not_called()


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestHoverHappyPath:
    """Successful hover calls with various contents shapes → ok + contents string."""

    def test_markup_content_dict_returns_ok_with_value(self) -> None:
        """MarkupContent dict {kind, value} → ok + the value string."""
        hov = {"contents": {"kind": "markdown", "value": "```rust\nfn foo() -> i32\n```"}}
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 3, 5, hov)
        assert result["status"] == STATUS_OK
        assert "contents" in result
        assert result["contents"] == "```rust\nfn foo() -> i32\n```"

    def test_plain_string_marked_string_returns_ok(self) -> None:
        """Plain string MarkedString → ok + the string."""
        hov = {"contents": "fn bar() -> ()"}
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 1, 1, hov)
        assert result["status"] == STATUS_OK
        assert result["contents"] == "fn bar() -> ()"

    def test_dict_marked_string_with_language_returns_ok(self) -> None:
        """MarkedString dict {language, value} → ok + the value string."""
        hov = {"contents": {"language": "rust", "value": "fn baz(x: u32) -> bool"}}
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/main.rs", 10, 4, hov)
        assert result["status"] == STATUS_OK
        assert result["contents"] == "fn baz(x: u32) -> bool"

    def test_list_contents_joined(self) -> None:
        """List of MarkedStrings → ok + joined with '\\n\\n'."""
        hov = {
            "contents": [
                {"language": "rust", "value": "fn qux()"},
                "Some documentation here.",
            ]
        }
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 5, 2, hov)
        assert result["status"] == STATUS_OK
        assert result["contents"] == "fn qux()\n\nSome documentation here."

    def test_list_with_three_elements_joined(self) -> None:
        """List of three items → all joined with double newline."""
        hov = {
            "contents": [
                {"kind": "markdown", "value": "**signature**"},
                {"language": "rust", "value": "struct Foo"},
                "Doc line.",
            ]
        }
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 7, 1, hov)
        assert result["status"] == STATUS_OK
        assert result["contents"] == "**signature**\n\nstruct Foo\n\nDoc line."


# ---------------------------------------------------------------------------
# not_found cases
# ---------------------------------------------------------------------------


class TestHoverNotFound:
    """Conditions that should return not_found rather than ok."""

    def test_request_hover_returns_none_is_not_found(self) -> None:
        """request_hover returning None → not_found."""
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 1, 1, None)
        assert result["status"] == STATUS_NOT_FOUND
        assert "message" in result

    def test_contents_empty_string_is_not_found(self) -> None:
        """Hover with empty 'value' → not_found."""
        hov = {"contents": {"kind": "markdown", "value": ""}}
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 2, 3, hov)
        assert result["status"] == STATUS_NOT_FOUND

    def test_contents_whitespace_only_is_not_found(self) -> None:
        """Hover with whitespace-only 'value' → not_found."""
        hov = {"contents": {"kind": "plaintext", "value": "   \n\t  "}}
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 4, 1, hov)
        assert result["status"] == STATUS_NOT_FOUND

    def test_contents_plain_empty_string_is_not_found(self) -> None:
        """Hover with plain empty string → not_found."""
        hov = {"contents": ""}
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 1, 1, hov)
        assert result["status"] == STATUS_NOT_FOUND

    def test_contents_plain_whitespace_is_not_found(self) -> None:
        """Hover with plain whitespace string → not_found."""
        hov = {"contents": "   "}
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 1, 1, hov)
        assert result["status"] == STATUS_NOT_FOUND

    def test_not_found_is_distinct_from_ok(self) -> None:
        """not_found status must differ from ok status."""
        mgr = _make_manager(STATE_READY)
        result = _run_hover(mgr, "src/lib.rs", 1, 1, None)
        assert result["status"] == STATUS_NOT_FOUND
        assert result["status"] != STATUS_OK


# ---------------------------------------------------------------------------
# Error from delegate
# ---------------------------------------------------------------------------


class TestHoverDelegateError:
    """Exceptions from request_hover → error envelope."""

    def test_exception_returns_error_envelope(self) -> None:
        """A RuntimeError from request_hover → error with message."""
        mgr = _make_manager(STATE_READY)
        exc = RuntimeError("connection lost")
        result = _run_hover(mgr, "src/lib.rs", 5, 10, None, raise_exc=exc)
        assert result["status"] == STATUS_ERROR
        assert "message" in result

    def test_error_message_contains_exception_text(self) -> None:
        """The error message should mention the exception text."""
        mgr = _make_manager(STATE_READY)
        exc = RuntimeError("timeout after 5s")
        result = _run_hover(mgr, "src/lib.rs", 5, 10, None, raise_exc=exc)
        assert "timeout after 5s" in result["message"]

    def test_exception_does_not_propagate(self) -> None:
        """Exceptions must be caught and returned as error envelopes, not re-raised."""
        mgr = _make_manager(STATE_READY)
        exc = ValueError("unexpected LSP response")
        # Should not raise
        result = _run_hover(mgr, "src/lib.rs", 1, 1, None, raise_exc=exc)
        assert result["status"] == STATUS_ERROR


# ---------------------------------------------------------------------------
# Position boundary / round-trip
# ---------------------------------------------------------------------------


class TestHoverPositionBoundary:
    """Position conversion: external (1-indexed) → LSP (0-indexed) delegate call."""

    def test_boundary_5_13_converts_to_4_12(self) -> None:
        """External (line=5, character=13) → delegate called with (line=4, character=12)."""
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.hover as hover_mod

        mgr = _make_manager(STATE_READY)
        captured: dict[str, Any] = {}

        async def _fake_hover(rel_path: str, line: int, column: int) -> dict[str, Any]:
            captured["line"] = line
            captured["column"] = column
            return {"contents": "result"}

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_hover", new=AsyncMock(side_effect=_fake_hover)),
            ):
                return await hover_mod.hover("src/lib.rs", 5, 13)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK
        assert captured["line"] == 4, f"Expected LSP line=4, got {captured['line']}"
        assert captured["column"] == 12, f"Expected LSP column=12, got {captured['column']}"

    def test_boundary_1_1_converts_to_0_0(self) -> None:
        """External (line=1, character=1) → delegate called with (line=0, character=0)."""
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.hover as hover_mod

        mgr = _make_manager(STATE_READY)
        captured: dict[str, Any] = {}

        async def _fake_hover(rel_path: str, line: int, column: int) -> dict[str, Any]:
            captured["line"] = line
            captured["column"] = column
            return {"contents": "fn main()"}

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_hover", new=AsyncMock(side_effect=_fake_hover)),
            ):
                return await hover_mod.hover("src/main.rs", 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK
        assert captured["line"] == 0
        assert captured["column"] == 0

    def test_file_passed_through_unchanged(self) -> None:
        """The file argument is passed through to request_hover unchanged."""
        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.hover as hover_mod

        mgr = _make_manager(STATE_READY)
        captured: dict[str, Any] = {}

        async def _fake_hover(rel_path: str, line: int, column: int) -> dict[str, Any]:
            captured["rel_path"] = rel_path
            return {"contents": "struct Foo"}

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_hover", new=AsyncMock(side_effect=_fake_hover)),
            ):
                return await hover_mod.hover("src/nested/bar.rs", 3, 7)

        asyncio.run(_inner())
        assert captured["rel_path"] == "src/nested/bar.rs"
