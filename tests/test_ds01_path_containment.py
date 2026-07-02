"""Fast-tier regression tests for DS-01 (issue #45): input-side path containment.

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Background: the four position tools (``goto_definition``, ``hover``,
``find_references``, ``document_symbols``) forward the client-supplied
``file`` argument to the multilspy delegate with no path validation.
multilspy 0.0.15 joins it via
``str(PurePath(repository_root_path, relative_file_path))`` — per ``pathlib``
join semantics, an *absolute* ``file`` (e.g. ``"/etc/hostname"``) silently
discards the repository root, and a ``..``-escaping ``file`` (e.g.
``"../../etc/hostname"``) resolves outside it.  multilspy then reads that
path and forwards its contents to rust-analyzer, turning the position tools
into an arbitrary-file-read primitive.

The fix: ``rust_lsp_mcp.core.validate_workspace_file`` rejects such ``file``
values with an ``error`` envelope BEFORE the analyzer delegate is ever
called.  These tests assert both the envelope and (crucially) that the fake
delegate is never invoked for the reject cases, and that a path which merely
normalizes to something inside the root (``"src/../src/main.rs"``) is
accepted and reaches the delegate in *normalized* form (``"src/main.rs"``).

Normalized forwarding matters (symlink+``..`` laundering): POSIX resolves
symlinks before ``..``, so a raw ``"target/../secrets.txt"`` — which
normalizes inside the root and passes the lexical check — would resolve
outside the root at the OS level if ``target`` were a symlink to a directory
elsewhere.  Forwarding ``os.path.normpath(file)`` collapses the ``..``
before the path ever reaches the filesystem.

Test coverage (per tool: goto_definition, hover, find_references,
document_symbols):
    - Absolute file path ("/etc/hostname") -> error, delegate not called.
    - Traversal file path ("../../etc/hostname") -> error, delegate not called.
    - NUL byte in file path -> error, delegate not called.
    - "src/../src/main.rs" (normalizes inside the root) -> reaches the
      delegate as "src/main.rs" (normalized, not raw).
    - Symlink+``..`` laundering regression: "target/../src/main.rs" reaches
      the delegate as "src/main.rs" (goto_definition, representative).
Plus direct unit coverage of ``core.validate_workspace_file`` /
``core._is_contained_relpath``.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_ERROR, STATUS_OK

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_manager(state: str = STATE_READY) -> AnalyzerManager:
    """Create a ready AnalyzerManager stub (no real background task)."""
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = state
    mgr._lsp = object() if state == STATE_READY else None  # type: ignore[assignment]
    mgr._repository_root = "/fake/repo"
    return mgr


REJECT_CASES = [
    pytest.param("/etc/hostname", id="absolute"),
    pytest.param("../../etc/hostname", id="traversal"),
    pytest.param("src/\x00/main.rs", id="nul-byte"),
]


# ---------------------------------------------------------------------------
# goto_definition
# ---------------------------------------------------------------------------


class TestGotoDefinitionPathContainment:
    @pytest.mark.parametrize("file", REJECT_CASES)
    def test_rejected_file_returns_error_without_calling_delegate(self, file: str) -> None:
        from rust_lsp_mcp.tools.goto_definition import goto_definition

        mgr = _make_manager()
        mock_delegate = AsyncMock(return_value=[])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_definition", new=mock_delegate),
            ):
                return await goto_definition(file, 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_ERROR, f"file={file!r} got {result!r}"
        mock_delegate.assert_not_called()

    def test_dotdot_that_normalizes_inside_root_reaches_delegate(self) -> None:
        """ "src/../src/main.rs" normalizes to "src/main.rs" — must NOT be rejected."""
        from rust_lsp_mcp.tools.goto_definition import goto_definition

        mgr = _make_manager()
        loc = {
            "relativePath": "src/main.rs",
            "uri": "file:///fake/repo/src/main.rs",
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
        }
        mock_delegate = AsyncMock(return_value=[loc])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_definition", new=mock_delegate),
            ):
                return await goto_definition("src/../src/main.rs", 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK, f"got {result!r}"
        mock_delegate.assert_called_once()
        # The normalized form (not the raw string) is forwarded to the delegate.
        assert mock_delegate.call_args.args[0] == "src/main.rs"

    def test_symlink_dotdot_laundering_is_defused_by_normalized_forwarding(self) -> None:
        """Symlink+``..`` laundering regression: "target/../src/main.rs" -> "src/main.rs".

        The raw path passes the lexical containment check (it normalizes
        inside the root), but if it were forwarded raw and ``target`` were a
        symlink to a directory outside the root, POSIX would resolve the
        symlink BEFORE the ``..`` and escape.  The delegate must therefore
        receive the normalized path, with the ``..`` already collapsed.
        """
        from rust_lsp_mcp.tools.goto_definition import goto_definition

        mgr = _make_manager()
        loc = {
            "relativePath": "src/main.rs",
            "uri": "file:///fake/repo/src/main.rs",
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
        }
        mock_delegate = AsyncMock(return_value=[loc])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_definition", new=mock_delegate),
            ):
                return await goto_definition("target/../src/main.rs", 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK, f"got {result!r}"
        mock_delegate.assert_called_once()
        assert mock_delegate.call_args.args[0] == "src/main.rs"


# ---------------------------------------------------------------------------
# hover
# ---------------------------------------------------------------------------


class TestHoverPathContainment:
    @pytest.mark.parametrize("file", REJECT_CASES)
    def test_rejected_file_returns_error_without_calling_delegate(self, file: str) -> None:
        from rust_lsp_mcp.tools.hover import hover

        mgr = _make_manager()
        mock_delegate = AsyncMock(return_value=None)

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_hover", new=mock_delegate),
            ):
                return await hover(file, 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_ERROR, f"file={file!r} got {result!r}"
        mock_delegate.assert_not_called()

    def test_dotdot_that_normalizes_inside_root_reaches_delegate(self) -> None:
        from rust_lsp_mcp.tools.hover import hover

        mgr = _make_manager()
        hov = {"contents": {"kind": "markdown", "value": "hello"}}
        mock_delegate = AsyncMock(return_value=hov)

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_hover", new=mock_delegate),
            ):
                return await hover("src/../src/main.rs", 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK, f"got {result!r}"
        mock_delegate.assert_called_once()
        # The normalized form (not the raw string) is forwarded to the delegate.
        assert mock_delegate.call_args.args[0] == "src/main.rs"


# ---------------------------------------------------------------------------
# find_references
# ---------------------------------------------------------------------------


class TestFindReferencesPathContainment:
    @pytest.mark.parametrize("file", REJECT_CASES)
    def test_rejected_file_returns_error_without_calling_delegate(self, file: str) -> None:
        from rust_lsp_mcp.tools.find_references import find_references

        mgr = _make_manager()
        mock_delegate = AsyncMock(return_value=[])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=mock_delegate),
            ):
                return await find_references(file, 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_ERROR, f"file={file!r} got {result!r}"
        mock_delegate.assert_not_called()

    def test_dotdot_that_normalizes_inside_root_reaches_delegate(self) -> None:
        from rust_lsp_mcp.tools.find_references import find_references

        mgr = _make_manager()
        mock_delegate = AsyncMock(return_value=[])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_references", new=mock_delegate),
            ):
                return await find_references("src/../src/main.rs", 1, 1)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK, f"got {result!r}"
        mock_delegate.assert_called_once()
        # The normalized form (not the raw string) is forwarded to the delegate.
        assert mock_delegate.call_args.args[0] == "src/main.rs"


# ---------------------------------------------------------------------------
# document_symbols
# ---------------------------------------------------------------------------


class TestDocumentSymbolsPathContainment:
    @pytest.mark.parametrize("file", REJECT_CASES)
    def test_rejected_file_returns_error_without_calling_delegate(self, file: str) -> None:
        from rust_lsp_mcp.tools.document_symbols import document_symbols

        mgr = _make_manager()
        mock_delegate = AsyncMock(return_value=[])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_document_symbols", new=mock_delegate),
            ):
                return await document_symbols(file)

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_ERROR, f"file={file!r} got {result!r}"
        mock_delegate.assert_not_called()

    def test_dotdot_that_normalizes_inside_root_reaches_delegate(self) -> None:
        from rust_lsp_mcp.tools.document_symbols import document_symbols

        mgr = _make_manager()
        mock_delegate = AsyncMock(return_value=[])

        async def _inner() -> dict[str, Any]:
            with (
                patch.object(core, "_manager", mgr),
                patch.object(mgr, "request_document_symbols", new=mock_delegate),
            ):
                return await document_symbols("src/../src/main.rs")

        result = asyncio.run(_inner())
        assert result["status"] == STATUS_OK, f"got {result!r}"
        mock_delegate.assert_called_once()
        # The normalized form (not the raw string) is forwarded to the delegate.
        assert mock_delegate.call_args.args[0] == "src/main.rs"


# ---------------------------------------------------------------------------
# Direct unit coverage of the shared helper
# ---------------------------------------------------------------------------


class TestValidateWorkspaceFileUnit:
    """Direct unit tests of core.validate_workspace_file / _is_contained_relpath."""

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/hostname",
            "../../etc/hostname",
            "..",
            "../secret.rs",
            "",
            "a\x00b",
        ],
    )
    def test_rejects(self, path: str) -> None:
        _, guard = core.validate_workspace_file(path)
        assert guard is not None

    @pytest.mark.parametrize(
        ("path", "expected_normalized"),
        [
            ("src/main.rs", "src/main.rs"),
            ("src/../src/main.rs", "src/main.rs"),
            ("target/../src/main.rs", "src/main.rs"),
            ("..hidden.rs", "..hidden.rs"),
            ("a/..b/c.rs", "a/..b/c.rs"),
        ],
    )
    def test_accepts_and_normalizes(self, path: str, expected_normalized: str) -> None:
        normalized, guard = core.validate_workspace_file(path)
        assert guard is None
        assert normalized == expected_normalized
