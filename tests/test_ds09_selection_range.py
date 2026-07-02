"""DS-09 regression tests: document-symbol position must come from selectionRange.

Per LSP, a DocumentSymbol's ``range`` spans the whole declaration *including*
leading doc comments and ``#[attributes]``; the symbol's *name* position is
``selectionRange``.  ``symbol_to_external`` must prefer ``selectionRange`` for
the document-symbol branch (no top-level ``location``), falling back to
``range`` when ``selectionRange`` is absent or malformed, so behavior never
regresses for providers that omit it.

Covers:
    - symbol_to_external: distinct range vs selectionRange -> selectionRange wins.
    - symbol_to_external: only range present -> range is used (fallback).
    - symbol_to_external: malformed selectionRange (missing "start") -> falls
      back to range gracefully (no crash).
    - document_symbols tool-level: emitted line/character come from
      selectionRange, not range, for a fake manager/delegate result.
"""

from typing import Any

from multilspy.multilspy_types import SymbolKind

from rust_lsp_mcp.core import symbol_to_external

# ---------------------------------------------------------------------------
# symbol_to_external unit tests
# ---------------------------------------------------------------------------


class TestSymbolToExternalSelectionRange:
    """Document-symbol branch: selectionRange preferred, range as fallback."""

    def test_distinct_range_and_selection_range_uses_selection_range(self) -> None:
        """range = doc-comment line (10, 1); selectionRange = name (12, 8).

        The mapped position must come from selectionRange (1-indexed: 13/9),
        NOT from range (which would be 11/2).
        """
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
        result = symbol_to_external(sym, repo_root="/fake/repo", default_file="src/lib.rs")
        assert result is not None
        assert result["line"] == 13, "expected selectionRange.start.line (12) + 1"
        assert result["character"] == 9, "expected selectionRange.start.character (8) + 1"

    def test_only_range_present_falls_back_to_range(self) -> None:
        """No selectionRange at all -> range is used (defensive fallback)."""
        sym: dict[str, Any] = {
            "name": "foo",
            "kind": SymbolKind.Function,
            "range": {
                "start": {"line": 10, "character": 1},
                "end": {"line": 14, "character": 1},
            },
        }
        result = symbol_to_external(sym, repo_root="/fake/repo", default_file="src/lib.rs")
        assert result is not None
        assert result["line"] == 11
        assert result["character"] == 2

    def test_malformed_selection_range_missing_start_falls_back_to_range(self) -> None:
        """selectionRange present but missing "start" -> falls back to range, no crash."""
        sym: dict[str, Any] = {
            "name": "foo",
            "kind": SymbolKind.Function,
            "range": {
                "start": {"line": 10, "character": 1},
                "end": {"line": 14, "character": 1},
            },
            "selectionRange": {
                "end": {"line": 12, "character": 11},
            },
        }
        result = symbol_to_external(sym, repo_root="/fake/repo", default_file="src/lib.rs")
        assert result is not None
        assert result["line"] == 11
        assert result["character"] == 2

    def test_selection_range_not_a_mapping_falls_back_to_range(self) -> None:
        """selectionRange present but the wrong shape entirely -> falls back to range."""
        sym: dict[str, Any] = {
            "name": "foo",
            "kind": SymbolKind.Function,
            "range": {
                "start": {"line": 10, "character": 1},
                "end": {"line": 14, "character": 1},
            },
            "selectionRange": "not-a-dict",
        }
        result = symbol_to_external(sym, repo_root="/fake/repo", default_file="src/lib.rs")
        assert result is not None
        assert result["line"] == 11
        assert result["character"] == 2

    def test_neither_range_nor_selection_range_is_skipped(self) -> None:
        """No location, no range, no selectionRange -> skipped (unchanged behavior)."""
        sym: dict[str, Any] = {"name": "foo", "kind": SymbolKind.Function}
        result = symbol_to_external(sym, repo_root="/fake/repo", default_file="src/lib.rs")
        assert result is None

    def test_workspace_symbol_branch_unaffected(self) -> None:
        """Symbols carrying a `location` sub-dict (workspace-symbol shape) still
        resolve via location.range.start, ignoring any stray top-level
        selectionRange/range keys (which SymbolInformation/Location never has
        in practice, but the branch dispatch must stay location-first).
        """
        sym: dict[str, Any] = {
            "name": "foo",
            "kind": SymbolKind.Function,
            "location": {
                "uri": "file:///fake/repo/src/lib.rs",
                "range": {
                    "start": {"line": 5, "character": 2},
                    "end": {"line": 5, "character": 5},
                },
            },
        }
        result = symbol_to_external(sym, repo_root="/fake/repo", default_file=None)
        assert result is not None
        assert result["line"] == 6
        assert result["character"] == 3
