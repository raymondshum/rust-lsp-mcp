"""Fast-tier tests for the find_references tool.

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    Input validation:
        - line < 1 → error envelope.
        - character < 1 → error envelope.
        - line=0, character=0 (both invalid) → error envelope.
        - line=1, character=0 → error envelope (character still invalid).
        - Minimum valid input (line=1, character=1) passes validation.
    Readiness gating:
        - Manager None → not_ready without calling delegate.
        - Manager indexing → not_ready without calling delegate.
    Happy path (uses-only, include_declaration=False):
        - N references → ok + N mapped 1-indexed entries.
        - Boundary round-trip: input (5, 13) → delegate (4, 12) → output (5, 13).
        - request_definition is NOT called when include_declaration=False.
    Zero references (headline semantic test):
        - Empty list from delegate → ok + references == [] (NOT not_found).
        - Confirms ok+[] is distinct from not_found.
    include_declaration=True (synthesized from definition):
        - Definition location added to references.
        - Declaration NOT double-counted when it already appears in refs list.
        - request_definition IS called when include_declaration=True.
        - include_declaration=False does NOT call request_definition.
    Error handling:
        - Exception from request_references → error envelope.
        - Exception from request_definition → error envelope.
    Mapping / deduplication:
        - Unmappable locations (None from location_to_external) are skipped.
        - Multiple references from different files all appear in output.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_ERROR, STATUS_NOT_READY, STATUS_OK
from rust_lsp_mcp.tools.find_references import find_references

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(state: str) -> AnalyzerManager:
    """Create an AnalyzerManager stub with state set directly (no real task).

    When state is STATE_READY, ``_lsp`` is set to a non-None sentinel so that
    the ``is_ready`` property (which requires BOTH state==ready AND _lsp!=None)
    behaves correctly.  Indexing fakes leave ``_lsp`` as None.
    """
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = state
    mgr._lsp = object() if state == STATE_READY else None  # type: ignore[assignment]
    mgr._repository_root = "/fake/repo"
    return mgr


def _make_location(rel_path: str, lsp_line: int, lsp_character: int) -> dict[str, Any]:
    """Build a minimal LSP Location-like dict (uri + range only, no relativePath).

    multilspy 0.0.15 returns Location dicts with only ``uri`` and ``range``; the
    ``relativePath`` field is absent.  location_to_external falls back to the uri
    path when relativePath is absent/falsy.
    """
    return {
        "uri": f"file:///fake/repo/{rel_path}",
        "range": {
            "start": {"line": lsp_line, "character": lsp_character},
            "end": {"line": lsp_line, "character": lsp_character + 1},
        },
    }


def _run_find_references(
    manager: AnalyzerManager | None,
    file: str,
    line: int,
    character: int,
    include_declaration: bool,
    refs_result: list[Any],
    defs_result: list[Any] | None = None,
) -> dict[str, Any]:
    """Patch core._manager and call find_references; inject fake LSP results.

    ``refs_result`` is injected as the return value of manager.request_references.
    ``defs_result``, if given, is injected as the return value of
    manager.request_definition.  When None, the definition mock is still set up
    (as an uncalled mock) so tests can assert it was not called.
    """

    async def _inner() -> dict[str, Any]:
        with patch.object(core, "_manager", manager):
            if manager is not None and manager.state == STATE_READY:
                refs_mock = AsyncMock(return_value=refs_result)
                defs_mock = AsyncMock(return_value=defs_result if defs_result is not None else [])
                with (
                    patch.object(manager, "request_references", new=refs_mock),
                    patch.object(manager, "request_definition", new=defs_mock),
                ):
                    result = await find_references(
                        file=file,
                        line=line,
                        character=character,
                        include_declaration=include_declaration,
                    )
                    # Expose mocks for test introspection via a side-channel.
                    # We stash them on the result dict under a private key so
                    # tests can retrieve them without re-running the coroutine.
                    result["_refs_mock"] = refs_mock
                    result["_defs_mock"] = defs_mock
                    return result
            else:
                # Gate-blocked path — no LSP calls; call directly without mocks.
                return await find_references(
                    file=file,
                    line=line,
                    character=character,
                    include_declaration=include_declaration,
                )

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Input validation tests
# ---------------------------------------------------------------------------


class TestInputValidation:
    """Positions < 1 must produce an error envelope before any LSP call."""

    def test_line_zero_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 0, 1, False, [])
        assert result["status"] == STATUS_ERROR
        assert "message" in result

    def test_character_zero_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, 0, False, [])
        assert result["status"] == STATUS_ERROR
        assert "message" in result

    def test_both_zero_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 0, 0, False, [])
        assert result["status"] == STATUS_ERROR

    def test_negative_line_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", -1, 1, False, [])
        assert result["status"] == STATUS_ERROR

    def test_negative_character_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, -1, False, [])
        assert result["status"] == STATUS_ERROR

    def test_line_one_character_zero_returns_error(self) -> None:
        """Line valid but character invalid → error."""
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, 0, False, [])
        assert result["status"] == STATUS_ERROR

    def test_minimum_valid_position_passes_validation(self) -> None:
        """line=1, character=1 is the minimum valid input — must not error."""
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, [])
        # Validation passes → gate check → ok+[] (not an error).
        assert result["status"] == STATUS_OK

    def test_validation_fires_before_gate(self) -> None:
        """Validation must short-circuit even when the manager is None."""
        result = _run_find_references(None, "src/lib.rs", 0, 0, False, [])
        assert result["status"] == STATUS_ERROR


# ---------------------------------------------------------------------------
# Readiness gating tests
# ---------------------------------------------------------------------------


class TestReadinessGating:
    """Manager None or indexing must return not_ready without calling the LSP."""

    def test_returns_not_ready_when_manager_none(self) -> None:
        result = _run_find_references(None, "src/lib.rs", 1, 1, False, [])
        assert result["status"] == STATUS_NOT_READY

    def test_returns_not_ready_while_indexing(self) -> None:
        mgr = _make_manager(STATE_INDEXING)
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, [])
        assert result["status"] == STATUS_NOT_READY

    def test_not_ready_is_not_error(self) -> None:
        """not_ready must not be confused with error."""
        mgr = _make_manager(STATE_INDEXING)
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, [])
        assert result["status"] == STATUS_NOT_READY
        assert result["status"] != STATUS_ERROR


# ---------------------------------------------------------------------------
# Happy path: uses-only (include_declaration=False)
# ---------------------------------------------------------------------------


class TestHappyPathUsesOnly:
    """Standard uses-only path — include_declaration=False (default)."""

    def test_single_reference_returns_ok_with_one_entry(self) -> None:
        mgr = _make_manager(STATE_READY)
        refs = [_make_location("src/main.rs", 9, 3)]
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, refs)

        assert result["status"] == STATUS_OK
        assert "references" in result
        assert len(result["references"]) == 1

        ref = result["references"][0]
        assert ref["file"] == "src/main.rs"
        assert ref["line"] == 10  # LSP 9 → external 10
        assert ref["character"] == 4  # LSP 3 → external 4

    def test_multiple_references_all_returned(self) -> None:
        mgr = _make_manager(STATE_READY)
        refs = [
            _make_location("src/a.rs", 0, 0),
            _make_location("src/b.rs", 4, 12),
        ]
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, refs)

        assert result["status"] == STATUS_OK
        assert len(result["references"]) == 2
        files = {r["file"] for r in result["references"]}
        assert files == {"src/a.rs", "src/b.rs"}

    def test_boundary_round_trip_5_13(self) -> None:
        """Input position (5, 13) → LSP delegate (4, 12) → output (5, 13)."""
        mgr = _make_manager(STATE_READY)
        # The reference is at LSP (4, 12) — maps back to external (5, 13).
        refs = [_make_location("src/lib.rs", 4, 12)]
        result = _run_find_references(mgr, "src/lib.rs", 5, 13, False, refs)

        assert result["status"] == STATUS_OK
        ref = result["references"][0]
        assert ref["line"] == 5
        assert ref["character"] == 13

    def test_delegate_called_with_0indexed_position(self) -> None:
        """The delegate must receive (4, 12) for input position (5, 13)."""
        mgr = _make_manager(STATE_READY)

        async def _inner() -> None:
            refs_mock = AsyncMock(return_value=[])
            defs_mock = AsyncMock(return_value=[])
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=refs_mock),
                patch.object(mgr, "request_definition", new=defs_mock),
            ):
                await find_references(
                    file="src/lib.rs",
                    line=5,
                    character=13,
                    include_declaration=False,
                )
            refs_mock.assert_called_once_with("src/lib.rs", 4, 12)

        asyncio.run(_inner())

    def test_request_definition_not_called_when_include_declaration_false(self) -> None:
        """With include_declaration=False, request_definition must never be called."""
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, [])

        defs_mock = result.get("_defs_mock")
        assert defs_mock is not None
        defs_mock.assert_not_called()


# ---------------------------------------------------------------------------
# HEADLINE SEMANTIC TEST: zero references → ok + empty list
# ---------------------------------------------------------------------------


class TestZeroReferences:
    """Zero references is a legitimate answer — ok + references=[] (NEVER not_found)."""

    def test_zero_references_is_ok_with_empty_list(self) -> None:
        """This is the headline semantic: no callers → ok+[] (not not_found)."""
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, [])

        assert result["status"] == STATUS_OK
        assert "references" in result
        assert result["references"] == []

    def test_zero_references_is_not_not_found(self) -> None:
        """Explicitly confirm zero refs is NOT not_found — statuses must differ."""
        from rust_lsp_mcp.envelope import STATUS_NOT_FOUND, not_found

        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, [])

        assert result["status"] != STATUS_NOT_FOUND

        # Double-check shape: ok+[] has a references key; not_found has message key.
        nf = not_found("no callers")
        assert result["status"] != nf["status"]
        assert "references" in result
        assert "references" not in nf

    def test_zero_references_result_shape(self) -> None:
        """ok + references=[] envelope has exactly the expected keys."""
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, [])

        assert result["status"] == STATUS_OK
        assert result["references"] == []
        # Must NOT have a 'message' field (that belongs to not_found / error).
        assert "message" not in result


# ---------------------------------------------------------------------------
# include_declaration=True: synthesized from request_definition
# ---------------------------------------------------------------------------


class TestIncludeDeclaration:
    """include_declaration=True merges the definition into the reference set."""

    def test_declaration_added_to_references(self) -> None:
        """The definition location appears in references when include_declaration=True."""
        mgr = _make_manager(STATE_READY)
        refs = [_make_location("src/main.rs", 9, 3)]
        decl = [_make_location("src/lib.rs", 0, 0)]  # definition = declaration

        result = _run_find_references(mgr, "src/lib.rs", 1, 1, True, refs, defs_result=decl)

        assert result["status"] == STATUS_OK
        assert len(result["references"]) == 2
        files = {r["file"] for r in result["references"]}
        assert "src/lib.rs" in files  # declaration
        assert "src/main.rs" in files  # use

    def test_declaration_not_double_counted_if_in_refs(self) -> None:
        """If the declaration already appears in the refs list, it must not be duplicated."""
        mgr = _make_manager(STATE_READY)
        decl_loc = _make_location("src/lib.rs", 0, 0)
        # Same location in both refs and definition result.
        refs = [decl_loc, _make_location("src/main.rs", 5, 0)]
        defs = [decl_loc]

        result = _run_find_references(mgr, "src/lib.rs", 1, 1, True, refs, defs_result=defs)

        assert result["status"] == STATUS_OK
        # Declaration was already in refs — total should be 2, not 3.
        assert len(result["references"]) == 2

    def test_declaration_only_no_refs(self) -> None:
        """When refs=[] but definition exists, include_declaration=True yields the decl."""
        mgr = _make_manager(STATE_READY)
        decl = [_make_location("src/lib.rs", 2, 4)]

        result = _run_find_references(mgr, "src/lib.rs", 1, 1, True, [], defs_result=decl)

        assert result["status"] == STATUS_OK
        assert len(result["references"]) == 1
        ref = result["references"][0]
        assert ref["file"] == "src/lib.rs"
        assert ref["line"] == 3  # LSP 2 → external 3
        assert ref["character"] == 5  # LSP 4 → external 5

    def test_include_declaration_true_calls_request_definition(self) -> None:
        """With include_declaration=True, request_definition must be called."""
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, True, [], defs_result=[])

        defs_mock = result.get("_defs_mock")
        assert defs_mock is not None
        defs_mock.assert_called_once()

    def test_include_declaration_false_does_not_call_request_definition(self) -> None:
        """With include_declaration=False, request_definition must NOT be called."""
        mgr = _make_manager(STATE_READY)
        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, [])

        defs_mock = result.get("_defs_mock")
        assert defs_mock is not None
        defs_mock.assert_not_called()

    def test_zero_refs_with_declaration_returns_just_declaration(self) -> None:
        """Zero uses + 1 definition = ok + [declaration] when include_declaration=True."""
        mgr = _make_manager(STATE_READY)
        decl = [_make_location("src/lib.rs", 10, 3)]

        result = _run_find_references(mgr, "src/lib.rs", 11, 4, True, [], defs_result=decl)

        assert result["status"] == STATUS_OK
        assert len(result["references"]) == 1

    def test_empty_definition_with_refs(self) -> None:
        """Definition returns [] (no known location) — refs are still returned."""
        mgr = _make_manager(STATE_READY)
        refs = [_make_location("src/main.rs", 0, 0)]

        result = _run_find_references(mgr, "src/lib.rs", 1, 1, True, refs, defs_result=[])

        assert result["status"] == STATUS_OK
        assert len(result["references"]) == 1

    def test_multiple_definitions_all_merged(self) -> None:
        """Multiple definition sites (e.g. trait impl) are all added."""
        mgr = _make_manager(STATE_READY)
        refs: list[Any] = []
        defs = [
            _make_location("src/lib.rs", 0, 0),
            _make_location("src/other.rs", 5, 0),
        ]

        result = _run_find_references(mgr, "src/lib.rs", 1, 1, True, refs, defs_result=defs)

        assert result["status"] == STATUS_OK
        assert len(result["references"]) == 2


# ---------------------------------------------------------------------------
# None-vs-empty distinction: delegate None → not_found; [] → ok+empty
# ---------------------------------------------------------------------------
#
# Critical contract: these two outcomes must map to DIFFERENT statuses.
#   - delegate returns None  → no symbol at this position → not_found.
#   - delegate returns []    → real symbol with zero callers → ok + references=[].
#
# This distinction cannot be collapsed without losing information.


class TestNoneVsEmptyDistinction:
    """delegate None → not_found (no symbol); [] → ok+empty (zero callers)."""

    def test_delegate_none_returns_not_found(self) -> None:
        """When request_references returns None, find_references must return not_found."""
        from rust_lsp_mcp.envelope import STATUS_NOT_FOUND

        mgr = _make_manager(STATE_READY)

        async def _inner() -> dict[str, Any]:
            refs_mock = AsyncMock(return_value=None)
            defs_mock = AsyncMock(return_value=[])
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=refs_mock),
                patch.object(mgr, "request_definition", new=defs_mock),
            ):
                return await find_references(
                    file="src/lib.rs", line=5, character=3, include_declaration=False
                )

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_NOT_FOUND, (
            f"delegate None must map to not_found (no symbol at position), got {result!r}"
        )
        assert "message" in result
        assert "references" not in result

    def test_delegate_empty_list_returns_ok_empty(self) -> None:
        """When request_references returns [], find_references must return ok + references=[]."""
        mgr = _make_manager(STATE_READY)

        async def _inner() -> dict[str, Any]:
            refs_mock = AsyncMock(return_value=[])
            defs_mock = AsyncMock(return_value=[])
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=refs_mock),
                patch.object(mgr, "request_definition", new=defs_mock),
            ):
                return await find_references(
                    file="src/lib.rs", line=5, character=3, include_declaration=False
                )

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK, (
            f"delegate [] must map to ok+empty (zero callers), got {result!r}"
        )
        assert result["references"] == []

    def test_none_and_empty_statuses_are_different(self) -> None:
        """Explicitly confirm None→not_found and []→ok have distinct statuses."""
        from rust_lsp_mcp.envelope import STATUS_NOT_FOUND

        mgr = _make_manager(STATE_READY)

        async def _run_with(return_value: Any) -> dict[str, Any]:
            refs_mock = AsyncMock(return_value=return_value)
            defs_mock = AsyncMock(return_value=[])
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=refs_mock),
                patch.object(mgr, "request_definition", new=defs_mock),
            ):
                return await find_references(
                    file="src/lib.rs", line=1, character=1, include_declaration=False
                )

        null_result = asyncio.run(_run_with(None))
        empty_result = asyncio.run(_run_with([]))

        assert null_result["status"] == STATUS_NOT_FOUND
        assert empty_result["status"] == STATUS_OK
        assert null_result["status"] != empty_result["status"], (
            "None and [] must produce different statuses — cannot collapse them"
        )

    def test_delegate_none_not_found_has_no_references_key(self) -> None:
        """not_found envelope must NOT contain a 'references' key."""
        from rust_lsp_mcp.envelope import STATUS_NOT_FOUND

        mgr = _make_manager(STATE_READY)

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=AsyncMock(return_value=None)),
                patch.object(mgr, "request_definition", new=AsyncMock(return_value=[])),
            ):
                return await find_references(
                    file="src/lib.rs", line=1, character=1, include_declaration=False
                )

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_NOT_FOUND
        assert "references" not in result, (
            "not_found must not carry a 'references' key (that belongs to ok envelopes)"
        )

    def test_none_from_references_skips_definition_call_when_include_declaration(self) -> None:
        """When refs is None (no symbol), request_definition must NOT be called."""
        mgr = _make_manager(STATE_READY)

        async def _inner() -> dict[str, Any]:
            refs_mock = AsyncMock(return_value=None)
            defs_mock = AsyncMock(return_value=[])
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=refs_mock),
                patch.object(mgr, "request_definition", new=defs_mock),
            ):
                result = await find_references(
                    file="src/lib.rs", line=1, character=1, include_declaration=True
                )
            defs_mock.assert_not_called()
            return result

        result = asyncio.run(_inner())
        from rust_lsp_mcp.envelope import STATUS_NOT_FOUND

        assert result["status"] == STATUS_NOT_FOUND

    def test_list_result_with_refs_is_ok(self) -> None:
        """When request_references returns a non-empty list, result is ok with refs."""
        mgr = _make_manager(STATE_READY)
        loc = _make_location("src/main.rs", 9, 3)

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=AsyncMock(return_value=[loc])),
                patch.object(mgr, "request_definition", new=AsyncMock(return_value=[])),
            ):
                return await find_references(
                    file="src/lib.rs", line=1, character=1, include_declaration=False
                )

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK
        assert len(result["references"]) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Exceptions from LSP delegates produce error envelopes."""

    def test_exception_from_request_references_returns_error(self) -> None:
        mgr = _make_manager(STATE_READY)

        async def _inner() -> dict[str, Any]:
            refs_mock = AsyncMock(side_effect=RuntimeError("LSP boom"))
            defs_mock = AsyncMock(return_value=[])
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=refs_mock),
                patch.object(mgr, "request_definition", new=defs_mock),
            ):
                return await find_references(
                    file="src/lib.rs", line=1, character=1, include_declaration=False
                )

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_ERROR
        assert "message" in result
        assert "LSP boom" in result["message"]

    def test_exception_from_request_definition_returns_error(self) -> None:
        """If include_declaration=True and request_definition raises, error envelope."""
        mgr = _make_manager(STATE_READY)

        async def _inner() -> dict[str, Any]:
            refs_mock = AsyncMock(return_value=[])
            defs_mock = AsyncMock(side_effect=RuntimeError("def boom"))
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=refs_mock),
                patch.object(mgr, "request_definition", new=defs_mock),
            ):
                return await find_references(
                    file="src/lib.rs", line=1, character=1, include_declaration=True
                )

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_ERROR
        assert "message" in result
        assert "def boom" in result["message"]


# ---------------------------------------------------------------------------
# Mapping and deduplication edge cases
# ---------------------------------------------------------------------------


class TestMappingAndDedup:
    """location_to_external returning None skips the entry; dedup by (file,line,char)."""

    def test_unmappable_location_skipped(self) -> None:
        """A location with a URI outside the repo root is skipped (location_to_external → None)."""
        mgr = _make_manager(STATE_READY)
        # Out-of-repo URI: location_to_external cannot make a relative path → None.
        bad_loc: dict[str, Any] = {
            "uri": "file:///totally/different/path/x.rs",
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
        }
        good_loc = _make_location("src/main.rs", 3, 5)
        refs = [bad_loc, good_loc]

        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, refs)

        assert result["status"] == STATUS_OK
        assert len(result["references"]) == 1
        assert result["references"][0]["file"] == "src/main.rs"

    def test_all_unmappable_locations_returns_ok_empty(self) -> None:
        """All refs unmappable → ok + references=[] (still not not_found)."""
        mgr = _make_manager(STATE_READY)
        bad_loc: dict[str, Any] = {
            "uri": "file:///out/of/repo.rs",
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
        }

        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, [bad_loc])

        assert result["status"] == STATUS_OK
        assert result["references"] == []

    def test_duplicate_refs_deduped(self) -> None:
        """Two identical locations in refs list → deduplicated to one entry."""
        mgr = _make_manager(STATE_READY)
        loc = _make_location("src/lib.rs", 4, 12)
        refs = [loc, loc]  # same location twice

        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, refs)

        assert result["status"] == STATUS_OK
        assert len(result["references"]) == 1

    def test_output_positions_are_1indexed(self) -> None:
        """All output line and character values must be >= 1 (1-indexed)."""
        mgr = _make_manager(STATE_READY)
        # LSP (0, 0) → external (1, 1)
        refs = [_make_location("src/lib.rs", 0, 0)]

        result = _run_find_references(mgr, "src/lib.rs", 1, 1, False, refs)

        assert result["status"] == STATUS_OK
        ref = result["references"][0]
        assert ref["line"] >= 1, "line must be 1-indexed"
        assert ref["character"] >= 1, "character must be 1-indexed"
