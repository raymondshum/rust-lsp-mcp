"""Fast-tier regression tests for DS-02 (issue #46): output-side path containment.

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Background: ``rust_lsp_mcp.core.location_to_external`` trusted multilspy's
pre-populated ``relativePath`` unconditionally and only fell back to the
containment-checking ``_uri_to_relative_path`` when ``relativePath`` was
falsy.  multilspy 0.0.15's ``PathUtils.get_relative_path`` (used by
``request_definition`` / ``request_references``) ALWAYS populates
``relativePath`` via ``os.path.relpath``, which on Linux never returns
``None`` — for a location outside the workspace (e.g. a standard-library or
dependency symbol) it instead yields a ``..``-prefixed path such as
``"../../usr/local/rustup/.../alloc/src/vec/mod.rs"``.  So ``goto_definition``
/ ``find_references`` would return ``ok`` with a ``..``-prefixed
"workspace-relative" path, defeating the documented containment guard.

The fix: containment-check ``relativePath`` too (purely lexically, via
``core._is_contained_relpath`` / ``os.path.normpath`` — no filesystem access,
no symlink resolution).  An out-of-workspace ``relativePath`` no longer
passes through; the function falls back to URI derivation (which
containment-checks), and if nothing usable remains, returns ``None`` so the
caller skips that location — consistent with ``find_symbol``'s existing
"skip out-of-workspace results" behaviour and the "all-skipped -> not_found"
logic in the tools.

Test coverage:
    location_to_external unit tests:
        - relativePath = "../../usr/local/rustup/.../mod.rs" -> None
          (out-of-workspace relativePath, no usable uri fallback either).
        - relativePath = absolute path -> None.
        - relativePath = "..hidden.rs" (literal in-root filename) -> passes through.
        - relativePath = "src/lib.rs" (normal) -> passes through.
        - Out-of-workspace relativePath + in-workspace uri -> falls back to
          the uri-derived path.
    Tool-level tests:
        - goto_definition: fake delegate returns a single location with an
          out-of-workspace relativePath AND an out-of-workspace uri ->
          not_found (the only candidate is skipped).
        - find_references: same out-of-workspace location -> ok + [] (skipped;
          ok+[] not not_found, per the tool's refs-is-None contract), and the
          include_declaration merge path skips an out-of-workspace definition.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import STATE_READY, AnalyzerManager
from rust_lsp_mcp.core import location_to_external
from rust_lsp_mcp.envelope import STATUS_NOT_FOUND, STATUS_OK

_REPO_ROOT = "/fake/repo"


def _loc(**kwargs: Any) -> dict[str, Any]:
    """Build a minimal Location-ish dict; range defaults to (0, 0)."""
    base: dict[str, Any] = {
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# location_to_external unit tests
# ---------------------------------------------------------------------------


class TestLocationToExternalRelativePathContainment:
    def test_outofworkspace_relative_path_no_uri_fallback_returns_none(self) -> None:
        """Stdlib-style relativePath with no usable uri fallback -> None."""
        loc = _loc(
            relativePath="../../usr/local/rustup/toolchains/x/lib/rustlib/src/alloc/src/vec/mod.rs"
        )
        result = location_to_external(loc, _REPO_ROOT)
        assert result is None

    def test_absolute_relative_path_returns_none(self) -> None:
        """A (malformed/adversarial) absolute relativePath must not pass through."""
        loc = _loc(relativePath="/etc/hostname")
        result = location_to_external(loc, _REPO_ROOT)
        assert result is None

    def test_literal_dotdot_prefixed_filename_passes_through(self) -> None:
        """A file literally named "..hidden.rs" in the root must NOT be rejected."""
        loc = _loc(relativePath="..hidden.rs")
        result = location_to_external(loc, _REPO_ROOT)
        assert result is not None
        assert result["file"] == "..hidden.rs"

    def test_normal_relative_path_passes_through(self) -> None:
        loc = _loc(relativePath="src/lib.rs")
        result = location_to_external(loc, _REPO_ROOT)
        assert result is not None
        assert result["file"] == "src/lib.rs"

    def test_outofworkspace_relative_path_falls_back_to_inworkspace_uri(self) -> None:
        """When relativePath escapes but the uri is in-workspace, use the uri-derived path.

        This models a case where the two multilspy-populated fields disagree;
        containment-checking relativePath must not simply return None when a
        usable, contained uri is available.
        """
        loc = _loc(
            relativePath="../../usr/local/rustup/lib/mod.rs",
            uri="file:///fake/repo/src/lib.rs",
        )
        result = location_to_external(loc, _REPO_ROOT)
        assert result is not None
        assert result["file"] == "src/lib.rs"

    def test_outofworkspace_relative_path_and_outofworkspace_uri_returns_none(self) -> None:
        loc = _loc(
            relativePath="../../usr/local/rustup/lib/mod.rs",
            uri="file:///usr/local/rustup/lib/mod.rs",
        )
        result = location_to_external(loc, _REPO_ROOT)
        assert result is None

    @pytest.mark.parametrize(
        "rel_path",
        [
            "..",
            "../x.rs",
            "a/../../b.rs",  # normalizes to "../b.rs" — escapes
        ],
    )
    def test_various_escaping_relative_paths_return_none(self, rel_path: str) -> None:
        loc = _loc(relativePath=rel_path)
        result = location_to_external(loc, _REPO_ROOT)
        assert result is None


# ---------------------------------------------------------------------------
# Tool-level: goto_definition skips an out-of-workspace-only location
# ---------------------------------------------------------------------------


def _make_manager(state: str = STATE_READY) -> AnalyzerManager:
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = state
    mgr._lsp = object() if state == STATE_READY else None  # type: ignore[assignment]
    mgr._repository_root = _REPO_ROOT
    return mgr


class TestGotoDefinitionSkipsOutOfWorkspaceLocation:
    def test_stdlib_style_location_yields_not_found(self) -> None:
        """A single out-of-workspace location (relativePath AND uri) -> not_found.

        Before the fix, the pre-populated relativePath would have been trusted
        blindly and returned as an ``ok`` result with a ``..``-prefixed path.
        """
        from rust_lsp_mcp.tools.goto_definition import goto_definition

        mgr = _make_manager()
        stdlib_loc = _loc(
            relativePath="../../usr/local/rustup/toolchains/x/lib/rustlib/src/alloc/src/vec/mod.rs",
            uri="file:///usr/local/rustup/toolchains/x/lib/rustlib/src/alloc/src/vec/mod.rs",
        )
        mock_delegate = AsyncMock(return_value=[stdlib_loc])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_definition", new=mock_delegate),
            ):
                return await goto_definition("src/main.rs", 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_NOT_FOUND, f"got {result!r}"


# ---------------------------------------------------------------------------
# Tool-level: find_references skips out-of-workspace locations
# ---------------------------------------------------------------------------


class TestFindReferencesSkipsOutOfWorkspaceLocation:
    """Out-of-workspace reference/definition locations are skipped, never leaked.

    Contract note: find_references reserves ``not_found`` for the refs-is-None
    case (no symbol at the position).  A non-None list whose entries are ALL
    skipped yields ``ok`` + ``references=[]`` — the "zero in-workspace callers"
    answer — consistent with the tool's documented ok+[] vs not_found split.
    The security property under test is that the ``..``-prefixed path never
    appears in the output.
    """

    _STDLIB_REL = "../../usr/local/rustup/toolchains/x/lib/rustlib/src/alloc/src/vec/mod.rs"
    _STDLIB_URI = "file:///usr/local/rustup/toolchains/x/lib/rustlib/src/alloc/src/vec/mod.rs"

    def test_stdlib_style_reference_is_skipped_yields_ok_empty(self) -> None:
        """A single out-of-workspace reference (relativePath AND uri) -> ok + [].

        Before the fix, the pre-populated relativePath would have been trusted
        blindly and returned as a ``..``-prefixed "workspace-relative" path.
        """
        from rust_lsp_mcp.tools.find_references import find_references

        mgr = _make_manager()
        stdlib_loc = _loc(relativePath=self._STDLIB_REL, uri=self._STDLIB_URI)
        mock_delegate = AsyncMock(return_value=[stdlib_loc])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=mock_delegate),
            ):
                return await find_references("src/main.rs", 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK, f"got {result!r}"
        assert result["references"] == []

    def test_stdlib_style_declaration_is_skipped_in_include_declaration_merge(self) -> None:
        """include_declaration merge path: an out-of-workspace definition is skipped.

        The in-workspace reference survives; the stdlib-style definition
        location returned by request_definition must not be merged in.
        """
        from rust_lsp_mcp.tools.find_references import find_references

        mgr = _make_manager()
        in_repo_ref = _loc(
            relativePath="src/lib.rs",
            uri="file:///fake/repo/src/lib.rs",
        )
        stdlib_def = _loc(relativePath=self._STDLIB_REL, uri=self._STDLIB_URI)
        refs_mock = AsyncMock(return_value=[in_repo_ref])
        defs_mock = AsyncMock(return_value=[stdlib_def])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=refs_mock),
                patch.object(mgr, "request_definition", new=defs_mock),
            ):
                return await find_references("src/main.rs", 1, 1, include_declaration=True)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK, f"got {result!r}"
        files = [r["file"] for r in result["references"]]
        assert files == ["src/lib.rs"], f"stdlib declaration leaked: {files!r}"
