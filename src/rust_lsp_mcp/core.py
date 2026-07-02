"""Core module for rust-lsp-mcp — FastMCP app, lifespan, readiness gate, and shared helpers.

This module owns:
    - The ``FastMCP`` application instance ``mcp``.
    - Lifespan wiring (``_lifespan`` wrapping ``analyzer_lifespan``).
    - ``require_ready()`` — the fail-fast readiness gate used by all tools.
    - ``get_manager()`` — accessor for the module-level ``AnalyzerManager`` singleton.
    - ``_uri_to_relative_path()`` — convert ``file://`` URIs to workspace-relative paths.
    - ``validate_workspace_file()`` — reject client-supplied ``file`` arguments that
        are absolute or escape the workspace root, before the analyzer is ever called.
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

from rust_lsp_mcp.analyzer import STATE_ERROR, AnalyzerManager, analyzer_lifespan
from rust_lsp_mcp.doc_store import clear_doc_store, init_doc_store_background
from rust_lsp_mcp.envelope import error, not_ready
from rust_lsp_mcp.positions import lsp_to_external
from rust_lsp_mcp.settings import get_settings

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
            # Doc-store init runs after the analyzer context is up.  The heavy
            # (embedding) part is offloaded to a background task by
            # init_doc_store_background — the lifespan yields immediately
            # rather than blocking server startup on the doc-index build.
            # init_doc_store_background never raises, but the try/except is
            # kept as defense-in-depth (log-and-swallow) so a bug there can
            # never take down the analyzer/nav tools.
            try:
                await init_doc_store_background(get_settings())
            except Exception:
                _log.exception(
                    "doc_store: init failed — search_docs will be unavailable; "
                    "analyzer/nav tools continue normally"
                )
            yield ctx
        finally:
            _manager = None
            clear_doc_store()


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
    """Check whether the analyzer is ready; return a guard envelope or None.

    Usage in tools::

        if (guard := require_ready()) is not None:
            return guard
        # ... proceed with analyzer call ...

    Returns:
        An ``error`` envelope if the analyzer's background run failed
        (``state == "error"`` — permanent until ``refresh`` recovers it), a
        ``not_ready`` envelope if it is still indexing or the manager has not
        started, else ``None`` once ready.
    """
    if _manager is not None and _manager.state == STATE_ERROR:
        return error(
            "The analyzer failed to start and cannot serve navigation queries: "
            f"{_manager.error_message or 'unknown error'}. "
            "Call the refresh tool to retry, or check the server configuration "
            "(e.g. RLM_RUST_ANALYZER_BIN)."
        )
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
# Path containment — pure lexical, no filesystem access, no symlink resolution
# ---------------------------------------------------------------------------


def _is_contained_relpath(path: str) -> bool:
    """Whether ``path`` is a workspace-relative path that cannot escape the root.

    Purely lexical (``os.path.normpath`` only — no filesystem access, no
    symlink resolution), mirroring the reasoning in ``_uri_to_relative_path``.
    A path is rejected when it is empty, contains a NUL byte, is absolute, or
    normalizes to ``".."`` or to a path with a leading ``".."`` segment (i.e.
    it climbs out of the root).

    The escape check compares the normalized path's leading segment rather
    than doing a raw ``str.startswith("..")`` prefix check, so a literal
    in-workspace filename like ``"..hidden.rs"`` is correctly accepted — it
    does not start with a ``".." + os.sep`` segment boundary.

    Args:
        path: A candidate workspace-relative path (untrusted — either
              client-supplied ``file`` input or a delegate-returned
              ``relativePath``).

    Returns:
        ``True`` if ``path`` is safe to join onto the workspace root.
    """
    if not path or "\x00" in path:
        return False
    if pathlib.Path(path).is_absolute():
        return False
    normalized = os.path.normpath(path)
    return normalized != os.pardir and not normalized.startswith(os.pardir + os.sep)


def validate_workspace_file(file: str) -> tuple[str, dict[str, Any] | None]:
    """Validate a client-supplied ``file`` argument before calling the analyzer.

    multilspy 0.0.15 joins ``file`` onto the repository root via
    ``str(PurePath(repository_root_path, relative_file_path))``.  Per
    ``pathlib`` join semantics, an *absolute* ``file`` silently discards the
    root entirely, and a ``..``-escaping ``file`` (e.g.
    ``"../../etc/hostname"``) resolves outside it; multilspy then reads
    whatever that path points to and forwards its contents to rust-analyzer,
    turning ``hover``/``document_symbols``/etc. into an arbitrary-file-read
    primitive.  This guard rejects both cases BEFORE the analyzer delegate is
    ever called.

    Containment is purely lexical — see ``_is_contained_relpath``.

    On acceptance the *normalized* path (``os.path.normpath``) is returned,
    and tools MUST forward that normalized form — never the raw input — to
    the delegate.  POSIX resolves symlinks before ``..``, so a raw accepted
    path like ``"target/../secrets.txt"`` (normalizes to ``"secrets.txt"``
    and passes the lexical check) would still resolve *outside* the root at
    the OS level if ``target`` were a symlink to a directory elsewhere.
    Collapsing the ``..`` lexically before the path ever reaches the
    filesystem closes that symlink+``..`` laundering variant.

    Usage in tools::

        file, guard = validate_workspace_file(file)
        if guard is not None:
            return guard

    Args:
        file: The client-supplied ``file`` argument, intended to be
              workspace-relative (e.g. ``"src/main.rs"``).

    Returns:
        ``(normalized_file, None)`` when ``file`` is valid, else
        ``(file, error_envelope)`` when it is invalid (empty, absolute,
        NUL-containing, or ``..``-escaping).
    """
    if not _is_contained_relpath(file):
        return file, error(
            f"Invalid file path {file!r}: must be a workspace-relative path "
            "that does not resolve outside the workspace root."
        )
    return os.path.normpath(file), None


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
    and ``range``.  Prefers ``relativePath``, but only when it is
    workspace-contained (see ``_is_contained_relpath``); otherwise falls back
    to deriving the path from ``uri`` via ``_uri_to_relative_path`` (which
    containment-checks it too).

    Security: multilspy 0.0.15 *always* populates ``relativePath`` via
    ``os.path.relpath(absolute_path, repository_root_path)`` (see
    ``PathUtils.get_relative_path``), which on POSIX never returns ``None``
    — for a location outside the workspace (e.g. a stdlib/dependency symbol)
    it instead yields a ``..``-prefixed path such as
    ``"../../usr/local/rustup/.../alloc/src/vec/mod.rs"``.  Trusting
    ``relativePath`` unconditionally would let such an out-of-workspace path
    pass through as if it were workspace-relative.  Containment-checking it
    here closes that gap; an out-of-workspace ``relativePath`` is treated the
    same as an absent one (fall back to the URI, or skip entirely).

    Args:
        loc:       An LSP ``Location``-like dict (must contain at least ``range``).
        repo_root: Absolute path to the workspace/repository root.

    Returns:
        ``{"file": <rel>, "line": <1-indexed>, "character": <1-indexed>}``
        or ``None`` if no usable in-workspace file path can be determined.
    """
    rel_path: str | None = loc.get("relativePath")
    if not rel_path or not _is_contained_relpath(rel_path):
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
