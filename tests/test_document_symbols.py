"""Fast-tier tests for document_symbols tool.

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    document_symbols:
        - not_ready when gate fails (manager None or indexing).
        - Happy path: flat list of range-only symbols → correct 1-indexed
          {name, kind, line, character, container} with no "file" key.
        - ok+empty when the delegate returns [] (valid file, no symbols).
        - error envelope when the delegate raises (e.g. non-existent file).
        - container is None for range-only symbols (containerName absent).
        - A symbol with no usable position (no location, no range) is skipped.
        - 1-indexed output: LSP (0, 0) → external (1, 1).
        - Human-readable kind string (e.g. "Struct", not raw int).
        - Multiple symbols — all returned in order.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_ERROR, STATUS_NOT_READY, STATUS_OK

# ---------------------------------------------------------------------------
# Helpers (mirroring test_phase2_fast.py idiom exactly)
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


def _doc_sym(
    name: str,
    kind: int,
    line: int,
    character: int,
    container: str | None = None,
) -> dict[str, Any]:
    """Build a minimal document-symbol dict (range-only, no location sub-dict).

    This is the document-symbol shape: a top-level ``range`` instead of a
    ``location`` sub-dict.  rust-analyzer returns this shape for
    ``textDocument/documentSymbol``.
    """
    sym: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "range": {
            "start": {"line": line, "character": character},
            "end": {"line": line, "character": character + len(name)},
        },
        "selectionRange": {
            "start": {"line": line, "character": character},
            "end": {"line": line, "character": character + len(name)},
        },
    }
    if container is not None:
        sym["containerName"] = container
    return sym


def _run_document_symbols(
    manager: AnalyzerManager | None,
    file: str,
    lsp_result: Any,
    *,
    raises: Exception | None = None,
) -> dict[str, Any]:
    """Patch core._manager and call document_symbols; inject lsp_result.

    If ``raises`` is set, the fake delegate raises that exception instead of
    returning lsp_result (used to test the error envelope path).
    """
    import rust_lsp_mcp.core as core
    import rust_lsp_mcp.tools.document_symbols as ds_mod

    async def _inner() -> dict[str, Any]:
        with patch.object(core, "_manager", manager):
            if manager is not None and manager.state == STATE_READY:
                if raises is not None:

                    async def _raising(*_a: Any, **_kw: Any) -> Any:
                        raise raises

                    with patch.object(manager, "request_document_symbols", new=_raising):
                        return await ds_mod.document_symbols(file)
                else:
                    with patch.object(
                        manager,
                        "request_document_symbols",
                        new=AsyncMock(return_value=lsp_result),
                    ):
                        return await ds_mod.document_symbols(file)
            else:
                return await ds_mod.document_symbols(file)

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Gating tests
# ---------------------------------------------------------------------------


class TestDocumentSymbolsGating:
    """document_symbols while indexing must return not_ready without calling the analyzer."""

    def test_returns_not_ready_while_indexing(self) -> None:
        mgr = _make_manager(STATE_INDEXING)
        result = _run_document_symbols(mgr, "src/lib.rs", [])
        assert result["status"] == STATUS_NOT_READY

    def test_returns_not_ready_when_manager_none(self) -> None:
        result = _run_document_symbols(None, "src/lib.rs", [])
        assert result["status"] == STATUS_NOT_READY

    def test_does_not_call_lsp_when_not_ready(self) -> None:
        """Analyzer must never be called when gated."""
        mgr = _make_manager(STATE_INDEXING)
        mock_delegate = AsyncMock()

        import rust_lsp_mcp.core as core
        import rust_lsp_mcp.tools.document_symbols as ds_mod

        async def _inner() -> dict[str, Any]:
            with patch.object(core, "_manager", mgr):
                return await ds_mod.document_symbols("src/lib.rs")

        asyncio.run(_inner())
        mock_delegate.assert_not_called()


# ---------------------------------------------------------------------------
# Happy-path mapping tests
# ---------------------------------------------------------------------------


class TestDocumentSymbolsMapping:
    """Correct field mapping, 1-indexed output, readable kind, no 'file' key."""

    def test_single_function_symbol(self) -> None:
        """A Function symbol maps to all expected fields, no file key."""
        from multilspy.multilspy_types import SymbolKind

        sym = _doc_sym("my_func", SymbolKind.Function, line=9, character=3)
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [sym])

        assert result["status"] == STATUS_OK
        assert "symbols" in result
        assert len(result["symbols"]) == 1

        s = result["symbols"][0]
        assert s["name"] == "my_func"
        assert s["kind"] == "Function"
        assert s["line"] == 10  # LSP 9 → external 10
        assert s["character"] == 4  # LSP 3 → external 4
        assert s["container"] is None
        assert "file" not in s, "document_symbols must NOT emit a 'file' key per symbol"

    def test_struct_kind_is_readable(self) -> None:
        from multilspy.multilspy_types import SymbolKind

        sym = _doc_sym("MyStruct", SymbolKind.Struct, 0, 0)
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/types.rs", [sym])
        assert result["symbols"][0]["kind"] == "Struct"

    def test_1indexed_line_and_character(self) -> None:
        """Lines and characters in results must be 1-indexed (never 0)."""
        from multilspy.multilspy_types import SymbolKind

        # LSP (0, 0) → external (1, 1): the minimum valid output
        sym = _doc_sym("f", SymbolKind.Function, line=0, character=0)
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [sym])
        s = result["symbols"][0]
        assert s["line"] >= 1, "line must be 1-indexed (>=1)"
        assert s["character"] >= 1, "character must be 1-indexed (>=1)"

    def test_multiple_symbols_all_returned(self) -> None:
        """Multiple symbols are returned in order."""
        from multilspy.multilspy_types import SymbolKind

        syms = [
            _doc_sym("Alpha", SymbolKind.Struct, 0, 0),
            _doc_sym("beta", SymbolKind.Function, 10, 4),
            _doc_sym("GAMMA", SymbolKind.Constant, 20, 0),
        ]
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", syms)

        assert result["status"] == STATUS_OK
        assert len(result["symbols"]) == 3
        assert result["symbols"][0]["name"] == "Alpha"
        assert result["symbols"][1]["name"] == "beta"
        assert result["symbols"][2]["name"] == "GAMMA"

    def test_no_file_key_on_any_symbol(self) -> None:
        """No symbol in the list should carry a 'file' key."""
        from multilspy.multilspy_types import SymbolKind

        syms = [
            _doc_sym("a", SymbolKind.Function, 0, 0),
            _doc_sym("b", SymbolKind.Struct, 5, 0),
        ]
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", syms)
        for s in result["symbols"]:
            assert "file" not in s, f"symbol {s['name']!r} must not have 'file' key"


# ---------------------------------------------------------------------------
# Container tests
# ---------------------------------------------------------------------------


class TestDocumentSymbolsContainer:
    """container is None for range-only symbols unless containerName is set."""

    def test_container_absent_is_none(self) -> None:
        """When containerName is missing, container must be null/None."""
        from multilspy.multilspy_types import SymbolKind

        sym = _doc_sym("standalone_fn", SymbolKind.Function, 0, 0)
        assert "containerName" not in sym
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/main.rs", [sym])
        s = result["symbols"][0]
        assert s["container"] is None

    def test_container_present_when_set(self) -> None:
        """When containerName is populated, container reflects it."""
        from multilspy.multilspy_types import SymbolKind

        sym = _doc_sym("new", SymbolKind.Method, 5, 4, container="MyStruct")
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/foo.rs", [sym])
        assert result["symbols"][0]["container"] == "MyStruct"


# ---------------------------------------------------------------------------
# ok+empty vs error distinction
# ---------------------------------------------------------------------------


class TestDocumentSymbolsEmptyAndError:
    """ok+empty when delegate returns []; error envelope when delegate raises."""

    def test_empty_list_returns_ok_empty(self) -> None:
        """Delegate returns [] → ok with symbols=[] (NOT not_found, NOT error)."""
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [])

        assert result["status"] == STATUS_OK, f"Expected ok for empty symbol list, got {result!r}"
        assert "symbols" in result
        assert result["symbols"] == []

    def test_ok_empty_is_not_not_found(self) -> None:
        """ok+empty is semantically distinct from not_found."""
        from rust_lsp_mcp.envelope import STATUS_NOT_FOUND

        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [])
        assert result["status"] != STATUS_NOT_FOUND

    def test_delegate_raises_returns_error(self) -> None:
        """When the delegate raises (e.g. bad path), return error envelope."""
        mgr = _make_manager(STATE_READY)
        exc = RuntimeError("file not found by LSP")
        result = _run_document_symbols(mgr, "nonexistent.rs", [], raises=exc)

        assert result["status"] == STATUS_ERROR
        assert "LSP error" in result["message"]

    def test_error_message_contains_exception_text(self) -> None:
        """The error envelope message includes the exception description."""
        mgr = _make_manager(STATE_READY)
        exc = ValueError("path does not exist: /fake/repo/nope.rs")
        result = _run_document_symbols(mgr, "nope.rs", [], raises=exc)

        assert result["status"] == STATUS_ERROR
        assert "path does not exist" in result["message"]


# ---------------------------------------------------------------------------
# Skip tests — symbols with no usable position
# ---------------------------------------------------------------------------


class TestDocumentSymbolsSkipping:
    """Symbols missing a usable position or name must be silently skipped."""

    def test_no_range_no_location_is_skipped(self) -> None:
        """A symbol with neither 'range' nor 'location' is skipped without crashing."""
        from multilspy.multilspy_types import SymbolKind

        bad: dict[str, Any] = {"name": "no_range", "kind": SymbolKind.Function}
        good = _doc_sym("good_sym", SymbolKind.Struct, 0, 0)
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [bad, good])

        assert result["status"] == STATUS_OK
        assert len(result["symbols"]) == 1
        assert result["symbols"][0]["name"] == "good_sym"

    def test_all_bad_symbols_returns_ok_empty(self) -> None:
        """If every symbol is skipped, the result is ok+[] (NOT not_found).

        document_symbols does not use not_found — the distinction is:
          - empty-because-no-symbols-in-file → ok+[]
          - bad-path/unreadable → error
        There is no not_found case for document_symbols.
        """
        bad: dict[str, Any] = {"name": "bad", "kind": 12}  # no range, no location
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [bad])

        assert result["status"] == STATUS_OK
        assert result["symbols"] == []

    def test_empty_name_is_skipped(self) -> None:
        """Symbol with empty name is skipped."""
        from multilspy.multilspy_types import SymbolKind

        bad: dict[str, Any] = {
            "name": "",
            "kind": SymbolKind.Function,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 0},
            },
        }
        good = _doc_sym("real_fn", SymbolKind.Function, 2, 0)
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [bad, good])

        assert result["status"] == STATUS_OK
        assert len(result["symbols"]) == 1
        assert result["symbols"][0]["name"] == "real_fn"

    def test_whitespace_name_is_skipped(self) -> None:
        """Symbol with whitespace-only name is skipped."""
        from multilspy.multilspy_types import SymbolKind

        bad: dict[str, Any] = {
            "name": "   ",
            "kind": SymbolKind.Function,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 0},
            },
        }
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [bad])

        assert result["status"] == STATUS_OK
        assert result["symbols"] == []


# ---------------------------------------------------------------------------
# DS-09: position comes from selectionRange (the name), not range
# ---------------------------------------------------------------------------


class TestDocumentSymbolsSelectionRange:
    """document_symbols must report the symbol NAME position (selectionRange),
    not the start of the full declaration (range) — see DS-09 (#53).
    """

    def test_position_comes_from_selection_range_not_range(self) -> None:
        """range = doc-comment/attribute line (10, 1); selectionRange = name (12, 8).

        Simulates `/// docs\n#[attr]\npub fn foo` — range starts on the doc
        comment, selectionRange starts on `foo`.  The emitted line/character
        must reflect selectionRange (1-indexed: 13/9), not range (11/2).
        """
        from multilspy.multilspy_types import SymbolKind

        sym: dict[str, Any] = {
            "name": "foo",
            "kind": SymbolKind.Function,
            "range": {
                "start": {"line": 10, "character": 1},
                "end": {"line": 14, "character": 1},
            },
            "selectionRange": {
                "start": {"line": 12, "character": 8},
                "end": {"line": 12, "character": 11},
            },
        }
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [sym])

        assert result["status"] == STATUS_OK
        s = result["symbols"][0]
        assert s["line"] == 13, "expected selectionRange.start.line (12) + 1"
        assert s["character"] == 9, "expected selectionRange.start.character (8) + 1"

    def test_missing_selection_range_falls_back_to_range(self) -> None:
        """No selectionRange at all -> falls back to range (defensive, unchanged)."""
        from multilspy.multilspy_types import SymbolKind

        sym: dict[str, Any] = {
            "name": "foo",
            "kind": SymbolKind.Function,
            "range": {
                "start": {"line": 10, "character": 1},
                "end": {"line": 14, "character": 1},
            },
        }
        mgr = _make_manager(STATE_READY)
        result = _run_document_symbols(mgr, "src/lib.rs", [sym])

        assert result["status"] == STATUS_OK
        s = result["symbols"][0]
        assert s["line"] == 11
        assert s["character"] == 2
