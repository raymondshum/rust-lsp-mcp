"""Core module for rust-lsp-mcp — FastMCP app, lifespan, readiness gate, and shared helpers.

This module owns:
    - The ``FastMCP`` application instance ``mcp``.
    - Lifespan wiring (``_lifespan`` wrapping ``analyzer_lifespan``).
    - ``require_ready()`` — the fail-fast readiness gate used by all tools.
    - ``get_manager()`` — accessor for the module-level ``AnalyzerManager`` singleton.
    - ``_uri_to_relative_path()`` — convert ``file://`` URIs to workspace-relative paths.
    - Shared symbol/location mapping helpers reused across navigation tools:
        ``kind_name``, ``location_to_external``, ``symbol_to_external``.

Tool modules import ``mcp`` and ``require_ready``/``get_manager`` from here; they
register themselves by decorating functions with ``@mcp.tool()`` at import time.
The ``rust_lsp_mcp.tools`` package auto-imports all submodules, so each new tool
file self-registers with zero edits to a central registry.
"""

import logging
import os
import pathlib
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import FastMCP
from multilspy.multilspy_types import SymbolKind

from rust_lsp_mcp.analyzer import AnalyzerManager, analyzer_lifespan
from rust_lsp_mcp.envelope import not_ready
from rust_lsp_mcp.positions import lsp_to_external

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level manager reference — set during lifespan startup, cleared on exit.
# Tools call require_ready() or get_manager() to access it.
# ---------------------------------------------------------------------------

_manager: AnalyzerManager | None = None


@asynccontextmanager
async def _lifespan(app: FastMCP) -> AsyncIterator[dict[str, Any]]:  # type: ignore[type-arg]
    """Thin wrapper around analyzer_lifespan that also wires the module-level ref."""
    global _manager
    async with analyzer_lifespan(app) as ctx:
        _manager = ctx["manager"]
        try:
            yield ctx
        finally:
            _manager = None


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

mcp: FastMCP[dict[str, Any]] = FastMCP(  # type: ignore[type-arg]
    "rust-lsp-mcp",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Readiness gate and manager accessor
# ---------------------------------------------------------------------------


def require_ready() -> dict[str, Any] | None:
    """Check whether the analyzer is ready; return a ``not_ready`` envelope or None.

    Usage in tools::

        if (guard := require_ready()) is not None:
            return guard
        # ... proceed with analyzer call ...

    Returns:
        ``not_ready`` envelope dict if the analyzer is not yet ready, else ``None``.
    """
    if _manager is None or not _manager.is_ready:
        return not_ready()
    return None


def get_manager() -> AnalyzerManager | None:
    """Return the current ``AnalyzerManager`` singleton, or ``None`` if not started.

    Tool modules should call ``require_ready()`` first; if that returns ``None``
    the manager is guaranteed to be non-None and in the ready state.
    """
    return _manager


# ---------------------------------------------------------------------------
# URI → workspace-relative path helper
# ---------------------------------------------------------------------------


def _uri_to_relative_path(uri: str, repository_root: str) -> str | None:
    """Convert a ``file://`` URI to a workspace-relative path.

    multilspy's ``request_workspace_symbol`` returns ``Location`` dicts with
    ``uri`` and ``range`` only — it does not populate ``relativePath``
    (confirmed against multilspy 0.0.15 at runtime).  We derive the relative
    path from the URI by stripping the ``file://`` prefix and computing the
    path relative to the repository root.

    Security: the derived path is normalized via ``os.path.normpath`` before
    computing ``relative_to`` so that ``..``-escape sequences (e.g.
    ``file:///repo/../secret/x.rs``) are collapsed lexically and never produce
    a relative path starting with ``..``.  ``os.path.normpath`` is a
    purely-lexical operation (no filesystem access / symlink resolution).

    Returns ``None`` if the URI is not under the repository root.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    # Normalize lexically before relative_to so ".." sequences cannot escape
    # the repo root.  os.path.normpath is pure lexical — no filesystem access.
    abs_path = pathlib.Path(os.path.normpath(unquote(parsed.path)))
    repo_root = pathlib.Path(repository_root)
    try:
        return str(abs_path.relative_to(repo_root))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Shared mapping helpers — reused by navigation tool modules
# ---------------------------------------------------------------------------


def kind_name(kind_raw: Any) -> str:
    """Convert a raw ``SymbolKind`` integer to a human-readable name.

    Args:
        kind_raw: The raw integer value from an LSP SymbolInformation dict.

    Returns:
        The enum member name (e.g. ``"Function"``, ``"Struct"``), or the
        ``str()`` representation of the raw value if the integer is not a
        known ``SymbolKind``.
    """
    try:
        return SymbolKind(kind_raw).name
    except (ValueError, KeyError):
        return str(kind_raw)


def location_to_external(loc: Mapping[str, Any], repo_root: str) -> dict[str, Any] | None:
    """Convert an LSP Location-ish dict to an external position dict.

    Accepts a ``Location`` dict that may contain ``relativePath``, ``uri``,
    and ``range``.  Prefers ``relativePath``; falls back to deriving the path
    from ``uri`` via ``_uri_to_relative_path``.

    Args:
        loc:       An LSP ``Location``-like dict (must contain at least ``range``).
        repo_root: Absolute path to the workspace/repository root.

    Returns:
        ``{"file": <rel>, "line": <1-indexed>, "character": <1-indexed>}``
        or ``None`` if no usable file path can be determined.
    """
    rel_path: str | None = loc.get("relativePath")
    if not rel_path:
        uri = loc.get("uri", "")
        rel_path = _uri_to_relative_path(uri, repo_root) if uri else None
    if not rel_path:
        return None

    rng = loc.get("range", {})
    start = rng.get("start", {})
    ext = lsp_to_external(
        lsp_line=start.get("line", 0),
        lsp_character=start.get("character", 0),
    )
    return {"file": rel_path, "line": ext.line, "character": ext.character}


def symbol_to_external(
    sym: Mapping[str, Any],
    repo_root: str,
    default_file: str | None = None,
) -> dict[str, Any] | None:
    """Convert a symbol info dict to an external representation dict.

    Handles both workspace-symbol results (which carry a ``location`` sub-dict)
    and document-symbol results (which carry a top-level ``range`` without a
    ``location``).

    Args:
        sym:          An LSP ``SymbolInformation`` or ``DocumentSymbol``-like dict.
        repo_root:    Absolute path to the workspace/repository root.
        default_file: Workspace-relative path to use when the symbol carries no
                      location path (document-symbol case).  If ``None`` and no
                      path can be derived, the returned dict will have
                      ``"file": None``.

    Returns:
        ``{"name", "kind", "file", "line", "character", "container"}`` with
        1-indexed ``line`` and ``character``, or ``None`` if the symbol is
        unusable (missing/empty name, no resolvable position).

    Position resolution order:
        1. ``sym["location"]["range"]["start"]`` (workspace-symbol shape).
        2. ``sym["range"]["start"]`` (document-symbol shape, no location).

    File path resolution order:
        1. ``sym["location"]["relativePath"]`` or ``sym["location"]["uri"]``
           (via ``_uri_to_relative_path``).
        2. ``default_file`` when only a top-level ``range`` is present.
    """
    sym_name: str | None = sym.get("name")
    if not sym_name or not sym_name.strip():
        _log.debug("symbol_to_external: candidate has no usable name (name=%r) — skipped", sym_name)
        return None

    loc = sym.get("location")

    if loc is not None:
        # Workspace-symbol shape: position is inside location.
        pos_info = location_to_external(loc, repo_root)
        if pos_info is None:
            _log.debug(
                "symbol_to_external: candidate %r has no usable path (location=%r) — skipped",
                sym_name,
                loc,
            )
            return None
        file_path: str | None = pos_info["file"]
        line: int = pos_info["line"]
        character: int = pos_info["character"]
    else:
        # Document-symbol shape: top-level range, no location.
        top_range = sym.get("range")
        if top_range is None:
            _log.debug(
                "symbol_to_external: candidate %r has no location or range — skipped",
                sym_name,
            )
            return None
        start = top_range.get("start", {})
        ext = lsp_to_external(
            lsp_line=start.get("line", 0),
            lsp_character=start.get("character", 0),
        )
        file_path = default_file
        line = ext.line
        character = ext.character

    # No usable file path (no location path derivable and no default_file given):
    # skip rather than emit a misleading file=None entry.  This preserves
    # find_symbol's original "no location → skip" behavior; document_symbols
    # always passes a default_file so it is unaffected.
    if not file_path:
        _log.debug(
            "symbol_to_external: candidate %r has no usable file path — skipped",
            sym_name,
        )
        return None

    return {
        "name": sym_name,
        "kind": kind_name(sym.get("kind")),
        "file": file_path,
        "line": line,
        "character": character,
        "container": sym.get("containerName"),
    }
