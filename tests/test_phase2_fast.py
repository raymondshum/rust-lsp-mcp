"""Fast-tier tests for Phase 2: positions helper + find_symbol tool.

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    positions.py:
        - Round-trip: lsp_to_external → external_to_lsp is identity for (line, char).
        - Explicit known cases: LSP (0,0) ↔ external (1,1), (4,12) ↔ (5,13).
    find_symbol:
        - Correct field mapping from faked UnifiedSymbolInformation.
        - 1-indexed output (line and character).
        - Human-readable kind (e.g. "Function", not raw int).
        - Container present and container-absent cases.
        - Zero matches → not_found (NOT ok, NOT ok+empty).
        - multilspy None return → not_found.
        - Gating: find_symbol while indexing → not_ready, never calls analyzer.
        - Candidate missing location is handled without crashing.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_NOT_FOUND, STATUS_NOT_READY, STATUS_OK
from rust_lsp_mcp.positions import ExternalPosition, LspPosition, external_to_lsp, lsp_to_external

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
    # is_ready requires _lsp to be non-None when state==ready (Fix 1).
    mgr._lsp = object() if state == STATE_READY else None  # type: ignore[assignment]
    mgr._repository_root = "/fake/repo"
    return mgr


def _sym(
    name: str,
    kind: int,
    rel_path: str,
    line: int,
    character: int,
    container: str | None = None,
) -> dict[str, Any]:
    """Build a minimal UnifiedSymbolInformation-like dict."""
    sym: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "location": {
            "relativePath": rel_path,
            "absolutePath": f"/repo/{rel_path}",
            "uri": f"file:///repo/{rel_path}",
            "range": {
                "start": {"line": line, "character": character},
                "end": {"line": line, "character": character + len(name)},
            },
        },
    }
    if container is not None:
        sym["containerName"] = container
    return sym


# ---------------------------------------------------------------------------
# Positions boundary helper tests
# ---------------------------------------------------------------------------


class TestPositionsRoundTrip:
    """Round-trip: converting LSP→external→LSP must return the original values."""

    @pytest.mark.parametrize(
        "lsp_line,lsp_char",
        [
            (0, 0),
            (0, 1),
            (1, 0),
            (4, 12),
            (99, 255),
            (0, 100),
            (1000, 0),
        ],
    )
    def test_lsp_to_external_to_lsp_roundtrip(self, lsp_line: int, lsp_char: int) -> None:
        ext = lsp_to_external(lsp_line, lsp_char)
        back = external_to_lsp(ext.line, ext.character)
        assert back.line == lsp_line, (
            f"round-trip line failed: {lsp_line} → {ext.line} → {back.line}"
        )
        assert back.character == lsp_char, (
            f"round-trip character failed: {lsp_char} → {ext.character} → {back.character}"
        )

    @pytest.mark.parametrize(
        "ext_line,ext_char",
        [
            (1, 1),
            (1, 2),
            (2, 1),
            (5, 13),
            (100, 256),
        ],
    )
    def test_external_to_lsp_to_external_roundtrip(self, ext_line: int, ext_char: int) -> None:
        lsp = external_to_lsp(ext_line, ext_char)
        back = lsp_to_external(lsp.line, lsp.character)
        assert back.line == ext_line
        assert back.character == ext_char


class TestPositionsKnownCases:
    """Explicit known-value cases."""

    def test_lsp_origin_to_external(self) -> None:
        ext = lsp_to_external(0, 0)
        assert ext == ExternalPosition(line=1, character=1)

    def test_lsp_4_12_to_external(self) -> None:
        ext = lsp_to_external(4, 12)
        assert ext == ExternalPosition(line=5, character=13)

    def test_external_1_1_to_lsp(self) -> None:
        lsp = external_to_lsp(1, 1)
        assert lsp == LspPosition(line=0, character=0)

    def test_external_5_13_to_lsp(self) -> None:
        lsp = external_to_lsp(5, 13)
        assert lsp == LspPosition(line=4, character=12)

    def test_lsp_to_external_returns_named_tuple(self) -> None:
        ext = lsp_to_external(2, 7)
        assert isinstance(ext, ExternalPosition)
        assert ext.line == 3
        assert ext.character == 8

    def test_external_to_lsp_returns_named_tuple(self) -> None:
        lsp = external_to_lsp(3, 8)
        assert isinstance(lsp, LspPosition)
        assert lsp.line == 2
        assert lsp.character == 7


# ---------------------------------------------------------------------------
# find_symbol fast tests
# ---------------------------------------------------------------------------


def _run_find_symbol(
    manager: AnalyzerManager | None, query: str, lsp_result: Any
) -> dict[str, Any]:
    """Patch _manager and call find_symbol; inject lsp_result for request_workspace_symbol."""
    import rust_lsp_mcp.server as srv

    async def _inner() -> dict[str, Any]:
        with patch.object(srv, "_manager", manager):
            if manager is not None and manager.state == STATE_READY:
                with patch.object(
                    manager,
                    "request_workspace_symbol",
                    new=AsyncMock(return_value=lsp_result),
                ):
                    return await srv.find_symbol(query)
            else:
                return await srv.find_symbol(query)

    return asyncio.run(_inner())


class TestFindSymbolGating:
    """find_symbol while indexing must return not_ready without calling the analyzer."""

    def test_returns_not_ready_while_indexing(self) -> None:
        mgr = _make_manager(STATE_INDEXING)
        result = _run_find_symbol(mgr, "anything", [])
        assert result["status"] == STATUS_NOT_READY

    def test_returns_not_ready_when_manager_none(self) -> None:
        result = _run_find_symbol(None, "anything", [])
        assert result["status"] == STATUS_NOT_READY

    def test_does_not_call_lsp_when_not_ready(self) -> None:
        """Analyzer must never be called when gated."""
        mgr = _make_manager(STATE_INDEXING)
        mock_lsp = AsyncMock()
        mgr._lsp = mock_lsp

        import rust_lsp_mcp.server as srv

        async def _inner() -> dict[str, Any]:
            with patch.object(srv, "_manager", mgr):
                return await srv.find_symbol("anything")

        asyncio.run(_inner())
        mock_lsp.request_workspace_symbol.assert_not_called()


class TestFindSymbolNotFound:
    """Zero matches and None returns must produce not_found, never ok+empty."""

    def test_empty_list_returns_not_found(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "nonexistent", [])
        assert result["status"] == STATUS_NOT_FOUND
        # Must NOT be ok
        assert result["status"] != STATUS_OK
        # Must NOT be ok+empty (no 'results' key in not_found)
        assert "results" not in result

    def test_none_returns_not_found(self) -> None:
        """multilspy returning None (server returned null) → not_found."""
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "nonexistent", None)
        assert result["status"] == STATUS_NOT_FOUND
        assert result["status"] != STATUS_OK
        assert "results" not in result

    def test_not_found_has_message(self) -> None:
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "xyz", [])
        assert "message" in result

    def test_not_found_is_distinct_from_ok_empty(self) -> None:
        """Critical invariant: not_found ≠ ok+[] — the statuses must differ."""
        mgr = _make_manager(STATE_READY)
        nf = _run_find_symbol(mgr, "xyz", [])
        assert nf["status"] == STATUS_NOT_FOUND
        # An ok+[] would have status=ok; confirm they differ
        from rust_lsp_mcp.envelope import ok as _ok

        ok_empty = _ok(results=[])
        assert nf["status"] != ok_empty["status"]


class TestFindSymbolMapping:
    """Correct field mapping, 1-indexed output, readable kind."""

    def test_single_function_candidate(self) -> None:
        """A Function symbol maps to all expected fields."""
        from multilspy.multilspy_types import SymbolKind

        sym = _sym("my_func", SymbolKind.Function, "src/lib.rs", line=9, character=3)
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "my_func", [sym])

        assert result["status"] == STATUS_OK
        assert "results" in result
        assert len(result["results"]) == 1

        r = result["results"][0]
        assert r["name"] == "my_func"
        assert r["kind"] == "Function"
        assert r["file"] == "src/lib.rs"
        assert r["line"] == 10  # LSP 9 → external 10
        assert r["character"] == 4  # LSP 3 → external 4

    def test_struct_kind_is_readable(self) -> None:
        from multilspy.multilspy_types import SymbolKind

        sym = _sym("MyStruct", SymbolKind.Struct, "src/types.rs", 0, 0)
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "MyStruct", [sym])
        assert result["results"][0]["kind"] == "Struct"

    def test_container_present(self) -> None:
        from multilspy.multilspy_types import SymbolKind

        sym = _sym("new", SymbolKind.Method, "src/foo.rs", 5, 4, container="Foo")
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "new", [sym])
        r = result["results"][0]
        assert r["container"] == "Foo"

    def test_container_absent_is_none(self) -> None:
        """When containerName is missing from the TypedDict, container must be null/None."""
        from multilspy.multilspy_types import SymbolKind

        sym = _sym("standalone_fn", SymbolKind.Function, "src/main.rs", 0, 0)
        # No containerName key in sym (already the case — _sym omits it when None)
        assert "containerName" not in sym
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "standalone_fn", [sym])
        r = result["results"][0]
        assert r["container"] is None

    def test_multiple_candidates_all_returned(self) -> None:
        """Multiple matches are a normal multi-hit list — all are returned."""
        from multilspy.multilspy_types import SymbolKind

        syms = [
            _sym("build", SymbolKind.Method, "src/a.rs", 10, 4, container="Builder"),
            _sym("build", SymbolKind.Function, "src/b.rs", 20, 0),
        ]
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "build", syms)

        assert result["status"] == STATUS_OK
        assert len(result["results"]) == 2
        names = {r["name"] for r in result["results"]}
        assert names == {"build"}
        files = {r["file"] for r in result["results"]}
        assert files == {"src/a.rs", "src/b.rs"}

    def test_1indexed_line_and_character(self) -> None:
        """Lines and characters in results must be 1-indexed (never 0)."""
        from multilspy.multilspy_types import SymbolKind

        # LSP (0, 0) → external (1, 1): the minimum valid output
        sym = _sym("f", SymbolKind.Function, "src/lib.rs", line=0, character=0)
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "f", [sym])
        r = result["results"][0]
        assert r["line"] >= 1, "line must be 1-indexed (>=1)"
        assert r["character"] >= 1, "character must be 1-indexed (>=1)"


class TestFindSymbolMissingLocation:
    """Candidates missing location must not crash the tool."""

    def test_no_location_key_is_skipped(self) -> None:
        """A candidate with no 'location' key is silently skipped."""
        from multilspy.multilspy_types import SymbolKind

        bad = {"name": "bad_sym", "kind": SymbolKind.Function}
        good = _sym("good_sym", SymbolKind.Function, "src/lib.rs", 0, 0)
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "sym", [bad, good])

        # Must not crash — and still returns the good candidate
        assert result["status"] == STATUS_OK
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "good_sym"

    def test_null_relative_path_is_skipped(self) -> None:
        """A candidate with relativePath=None is silently skipped."""
        from multilspy.multilspy_types import SymbolKind

        bad: dict[str, Any] = {
            "name": "no_path",
            "kind": SymbolKind.Function,
            "location": {
                "relativePath": None,
                "absolutePath": "/abs/path.rs",
                "uri": "file:///abs/path.rs",
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}},
            },
        }
        good = _sym("good_sym", SymbolKind.Struct, "src/types.rs", 5, 0)
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "sym", [bad, good])

        assert result["status"] == STATUS_OK
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "good_sym"

    def test_all_bad_candidates_returns_not_found(self) -> None:
        """If every candidate is skipped, the result is not_found."""
        bad = {"name": "bad", "kind": 12}  # no location
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "bad", [bad])
        assert result["status"] == STATUS_NOT_FOUND


# ---------------------------------------------------------------------------
# URI-fallback path tests (production path: multilspy 0.0.15 omits relativePath)
# ---------------------------------------------------------------------------


def _sym_uri_only(
    name: str,
    kind: int,
    uri: str,
    line: int,
    character: int,
    container: str | None = None,
) -> dict[str, Any]:
    """Build a symbol candidate with NO relativePath — only a uri (production path)."""
    sym: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "location": {
            # relativePath deliberately absent — this is the multilspy 0.0.15 shape
            "uri": uri,
            "range": {
                "start": {"line": line, "character": character},
                "end": {"line": line, "character": character + len(name)},
            },
        },
    }
    if container is not None:
        sym["containerName"] = container
    return sym


class TestFindSymbolUriFallback:
    """URI-fallback path: relativePath absent/None, uri is the only path source.

    This is the actual production path for multilspy 0.0.15, which does not
    populate relativePath in workspace_symbol results.
    """

    def test_uri_only_in_repo_returns_ok(self) -> None:
        """Candidate with no relativePath but valid in-repo uri → ok with correct fields."""
        from multilspy.multilspy_types import SymbolKind

        # Manager root is /fake/repo; uri points inside it
        mgr = _make_manager(STATE_READY)
        sym = _sym_uri_only(
            "my_func",
            SymbolKind.Function,
            uri="file:///fake/repo/src/lib.rs",
            line=4,
            character=3,
        )
        result = _run_find_symbol(mgr, "my_func", [sym])

        assert result["status"] == STATUS_OK
        assert len(result["results"]) == 1
        r = result["results"][0]
        assert r["name"] == "my_func"
        assert r["kind"] == "Function"
        assert r["file"] == "src/lib.rs"  # workspace-relative
        assert r["line"] == 5  # LSP 4 → external 5
        assert r["character"] == 4  # LSP 3 → external 4

    def test_uri_only_none_relative_path_falls_back_to_uri(self) -> None:
        """Candidate with relativePath=None (explicit null) also falls back to uri."""
        from multilspy.multilspy_types import SymbolKind

        mgr = _make_manager(STATE_READY)
        sym: dict[str, Any] = {
            "name": "other_fn",
            "kind": SymbolKind.Function,
            "location": {
                "relativePath": None,  # explicit None — not absent, but falsy
                "uri": "file:///fake/repo/src/main.rs",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 8},
                },
            },
        }
        result = _run_find_symbol(mgr, "other_fn", [sym])

        assert result["status"] == STATUS_OK
        r = result["results"][0]
        assert r["file"] == "src/main.rs"
        assert r["line"] == 1
        assert r["character"] == 1

    def test_out_of_repo_uri_is_skipped(self) -> None:
        """A candidate whose uri is outside the repo root is skipped → not_found."""
        from multilspy.multilspy_types import SymbolKind

        mgr = _make_manager(STATE_READY)
        # /usr/lib/rustlib is outside /fake/repo
        sym = _sym_uri_only(
            "std_fn",
            SymbolKind.Function,
            uri="file:///usr/lib/rustlib/x.rs",
            line=0,
            character=0,
        )
        result = _run_find_symbol(mgr, "std_fn", [sym])
        assert result["status"] == STATUS_NOT_FOUND

    def test_uri_with_percent_encoded_path(self) -> None:
        """URI with %20-encoded spaces in the path is decoded correctly (nit 2)."""
        from multilspy.multilspy_types import SymbolKind

        # Repo root contains a space: /fake/my repo
        mgr = _make_manager(STATE_READY)
        mgr._repository_root = "/fake/my repo"
        sym = _sym_uri_only(
            "enc_fn",
            SymbolKind.Function,
            uri="file:///fake/my%20repo/src/lib.rs",
            line=2,
            character=0,
        )
        result = _run_find_symbol(mgr, "enc_fn", [sym])

        assert result["status"] == STATUS_OK
        r = result["results"][0]
        assert r["file"] == "src/lib.rs"
        assert r["line"] == 3
        assert r["character"] == 1


# ---------------------------------------------------------------------------
# Regression tests: Phase 2 adversarial seam bug fixes
# ---------------------------------------------------------------------------


class TestTeardownWindowNotReady:
    """Fix 1 regression: teardown window (state=ready, _lsp=None) → not_ready, not error.

    Simulates the window where ``_run``'s ``finally`` has cleared ``_lsp`` but
    ``state`` has not been reset (Phase 4's job).  Callers must see ``not_ready``
    rather than a ``RuntimeError`` wrapped as an ``error`` envelope.
    """

    def _make_torn_down_manager(self) -> AnalyzerManager:
        """Manager in the teardown window: state=ready, _lsp=None."""
        mgr = AnalyzerManager.__new__(AnalyzerManager)
        mgr.state = STATE_READY
        mgr._lsp = None  # simulates _run's finally clearing _lsp
        mgr._repository_root = "/fake/repo"
        return mgr

    def test_find_symbol_returns_not_ready_in_teardown_window(self) -> None:
        """find_symbol must return not_ready (not error) when _lsp is None and state=ready."""
        import rust_lsp_mcp.server as srv

        mgr = self._make_torn_down_manager()
        mock_delegate = AsyncMock()

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(srv, "_manager", mgr),
                # Do NOT patch request_workspace_symbol — we want to confirm it's not called
                patch.object(mgr, "request_workspace_symbol", new=mock_delegate),
            ):
                return await srv.find_symbol("anything")

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_NOT_READY, (
            f"Expected not_ready in teardown window, got {result!r}"
        )
        mock_delegate.assert_not_called()

    def test_probe_returns_not_ready_in_teardown_window(self) -> None:
        """probe must return not_ready when state=ready but _lsp is None."""
        import rust_lsp_mcp.server as srv

        mgr = self._make_torn_down_manager()

        with patch.object(srv, "_manager", mgr):
            result = srv.probe()

        assert result["status"] == STATUS_NOT_READY, (
            f"Expected not_ready in teardown window, got {result!r}"
        )


class TestMissingNameHandling:
    """Fix 2 regression: candidate missing 'name' key must be skipped, not crash.

    A hard ``sym["name"]`` subscript on a candidate without a ``name`` key was
    outside the try/except → unhandled ``KeyError`` → FastMCP protocol error.
    Now the candidate is skipped (logged at DEBUG) instead.
    """

    def test_no_name_key_is_skipped(self) -> None:
        """Candidate with no 'name' key is silently skipped, does not raise."""
        from multilspy.multilspy_types import SymbolKind

        # No 'name' key at all — previously caused KeyError
        no_name: dict[str, Any] = {
            "kind": SymbolKind.Function,
            "location": {
                "uri": "file:///fake/repo/src/lib.rs",
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
            },
        }
        good = _sym("real_fn", SymbolKind.Function, "src/lib.rs", 0, 0)
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "fn", [no_name, good])

        assert result["status"] == STATUS_OK, f"Expected ok with good candidate, got {result!r}"
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "real_fn"

    def test_empty_name_is_skipped(self) -> None:
        """Candidate with empty string name is silently skipped."""
        from multilspy.multilspy_types import SymbolKind

        empty_name: dict[str, Any] = {
            "name": "",
            "kind": SymbolKind.Function,
            "location": {
                "uri": "file:///fake/repo/src/lib.rs",
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
            },
        }
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "fn", [empty_name])

        assert result["status"] == STATUS_NOT_FOUND, (
            f"Expected not_found for sole empty-name candidate, got {result!r}"
        )

    def test_whitespace_name_is_skipped(self) -> None:
        """Candidate with whitespace-only name is silently skipped."""
        from multilspy.multilspy_types import SymbolKind

        ws_name: dict[str, Any] = {
            "name": "   ",
            "kind": SymbolKind.Function,
            "location": {
                "uri": "file:///fake/repo/src/lib.rs",
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
            },
        }
        good = _sym("real_fn", SymbolKind.Struct, "src/types.rs", 1, 0)
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "fn", [ws_name, good])

        assert result["status"] == STATUS_OK
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "real_fn"

    def test_sole_no_name_candidate_returns_not_found(self) -> None:
        """If the only candidate has no name, result is not_found (not a crash)."""
        from multilspy.multilspy_types import SymbolKind

        no_name: dict[str, Any] = {
            "kind": SymbolKind.Function,
            "location": {
                "uri": "file:///fake/repo/src/lib.rs",
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
            },
        }
        mgr = _make_manager(STATE_READY)
        result = _run_find_symbol(mgr, "fn", [no_name])

        assert result["status"] == STATUS_NOT_FOUND, (
            f"Expected not_found for sole no-name candidate, got {result!r}"
        )


class TestUriDotDotEscapeHardening:
    """Fix 3 regression: ``..``-escape in URI must not leak out-of-repo paths.

    ``Path.relative_to`` is lexical and does not collapse ``..`` sequences, so
    a URI like ``file:///repo/../secret/x.rs`` can resolve to ``../secret/x.rs``
    — an out-of-workspace path.  ``os.path.normpath`` collapses these lexically
    (no filesystem access) before ``relative_to`` is called.
    """

    def test_dotdot_escape_uri_is_skipped(self) -> None:
        """A URI using ``..`` to escape the repo root is skipped → not_found."""
        from multilspy.multilspy_types import SymbolKind

        # /fake/repo/../secret/x.rs normalizes to /fake/secret/x.rs — outside /fake/repo
        mgr = _make_manager(STATE_READY)
        sym = _sym_uri_only(
            "secret_fn",
            SymbolKind.Function,
            uri="file:///fake/repo/../secret/x.rs",
            line=0,
            character=0,
        )
        result = _run_find_symbol(mgr, "secret_fn", [sym])
        assert result["status"] == STATUS_NOT_FOUND, (
            f"Expected not_found for ../-escape URI, got {result!r}"
        )

    def test_normal_in_repo_uri_still_resolves(self) -> None:
        """A normal in-repo URI without .. continues to resolve correctly."""
        from multilspy.multilspy_types import SymbolKind

        mgr = _make_manager(STATE_READY)
        sym = _sym_uri_only(
            "normal_fn",
            SymbolKind.Function,
            uri="file:///fake/repo/src/main.rs",
            line=3,
            character=0,
        )
        result = _run_find_symbol(mgr, "normal_fn", [sym])
        assert result["status"] == STATUS_OK
        r = result["results"][0]
        assert r["file"] == "src/main.rs"
        assert r["line"] == 4  # LSP 3 → external 4
        assert r["character"] == 1

    def test_dotdot_within_repo_resolves_correctly(self) -> None:
        """A ``..`` that stays within the repo root (non-escape) resolves to the real path."""
        from multilspy.multilspy_types import SymbolKind

        # /fake/repo/src/../lib.rs normalizes to /fake/repo/lib.rs — still inside repo
        mgr = _make_manager(STATE_READY)
        sym = _sym_uri_only(
            "inrepo_fn",
            SymbolKind.Function,
            uri="file:///fake/repo/src/../lib.rs",
            line=0,
            character=0,
        )
        result = _run_find_symbol(mgr, "inrepo_fn", [sym])
        assert result["status"] == STATUS_OK
        r = result["results"][0]
        assert r["file"] == "lib.rs"  # normalized path relative to repo root
