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
import shutil
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import FastMCP
from multilspy.multilspy_types import SymbolKind

from rust_lsp_mcp.analyzer import (
    INDEXING_RETRY_MESSAGE,
    STATE_ERROR,
    AnalyzerManager,
    analyzer_lifespan,
)
from rust_lsp_mcp.doc_store import clear_doc_store, init_doc_store_background
from rust_lsp_mcp.envelope import RECOVERY_FIX_INPUT, RECOVERY_REFRESH, error, not_ready
from rust_lsp_mcp.positions import lsp_to_external
from rust_lsp_mcp.settings import Settings, get_settings

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level manager reference — set during lifespan startup, cleared on exit.
# Tools call require_ready() or get_manager() to access it.
# ---------------------------------------------------------------------------

_manager: AnalyzerManager | None = None


# ---------------------------------------------------------------------------
# Advisory startup preflight (UR-21, narrowed) — non-fatal, computed ONCE.
#
# Deliberately lives here (in the lifespan wrapper), NOT inside
# AnalyzerManager.start()/_run: the fast/race test suites (test_phase1_fast.py,
# test_lifecycle_races.py, test_analyzer_error_state.py) construct
# AnalyzerManager directly with fake paths ('/fake/repo', '/nonexistent', ...)
# and a mocked LSP, then drive start()/_run themselves — a preflight wired
# into that seam would fire on every one of those fake-path constructions and
# is not what they exist to test. Computing it here means it only runs when
# the real FastMCP lifespan runs (production, and test_lifespan_startup.py's
# direct `core._lifespan` exercises), never when a test drives the manager in
# isolation.
#
# Holder default is an empty list — this single value does double duty as
# "startup preflight has not run yet" (test contexts that never touch
# core._lifespan) AND "startup ran and found nothing to warn about". Callers
# cannot distinguish the two from the list alone, which is intentional: an
# empty list means "nothing to show the caller" either way, and status() has
# no other field that claims to report whether preflight itself ran.
# ---------------------------------------------------------------------------

_preflight_warnings: list[str] = []


def _compute_preflight_warnings(settings: Settings) -> list[str]:
    """Return advisory (never fatal) warnings about the resolved settings.

    Two cheap, high-value checks only (UR-21 round-2 revision):
        - the configured ``rust_analyzer_bin`` resolves to an existing,
          executable file — ``shutil.which`` handles both a bare command name
          (searched on ``PATH``) and an explicit/relative path (checked
          directly, no ``PATH`` search) with the same call, matching how the
          setting is actually consumed.
        - the configured ``project_root`` exists and is a directory.

    Deliberately does NOT check for a ``Cargo.toml`` at the root — rejected:
    ``project_root`` is documented as repo-agnostic (rust-project.json
    projects, or a Cargo.toml in a subdirectory, are both valid), so that
    check would misfire on legitimate configurations.

    Never raises; never touches analyzer/doc-store state. Pure function of
    ``settings`` so it is trivially unit-testable without the lifespan.
    """
    warnings: list[str] = []
    try:
        if shutil.which(settings.rust_analyzer_bin) is None:
            warnings.append(
                f"rust-analyzer binary {settings.rust_analyzer_bin!r} was not "
                "found or is not executable (checked PATH for a bare name, or "
                "directly for a path) — set RLM_RUST_ANALYZER_BIN to a valid "
                "rust-analyzer executable."
            )
        if not pathlib.Path(settings.project_root).is_dir():
            warnings.append(
                f"project_root {settings.project_root!r} does not exist or is "
                "not a directory — set RLM_PROJECT_ROOT to the target Rust "
                "project."
            )
    except Exception:
        # "Never fatal" must hold unconditionally: a pathological setting
        # value (e.g. an embedded NUL byte making Path() raise) must not
        # kill server startup over an advisory check.
        _log.exception("preflight: advisory check itself failed — skipping")
    return warnings


def get_preflight_warnings() -> list[str]:
    """Return the advisory startup preflight warnings (empty list = all clear).

    See the module-level comment above ``_preflight_warnings`` for what an
    empty list means when the lifespan never ran (e.g. most unit tests).
    """
    return list(_preflight_warnings)


@asynccontextmanager
async def _lifespan(app: FastMCP) -> AsyncIterator[dict[str, Any]]:  # type: ignore[type-arg]
    """Thin wrapper around analyzer_lifespan that also wires the module-level ref."""
    global _manager, _preflight_warnings
    # Advisory preflight (UR-21): computed ONCE per lifespan start, from the
    # same settings the analyzer/doc-store are about to use. Never fatal —
    # failures here must never prevent the analyzer/doc-store from starting,
    # so this runs before anything else and cannot itself raise (see
    # _compute_preflight_warnings's docstring).
    _preflight_warnings = _compute_preflight_warnings(get_settings())
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
            _preflight_warnings = []
            clear_doc_store()


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

_INSTRUCTIONS = (
    "Navigation pattern: resolve a symbol NAME to a position with "
    "find_symbol or document_symbols, then act on that position with "
    "goto_definition, find_references, or hover. All positions "
    "(file, line, character) are 1-indexed."
)


def _build_mcp(settings: Settings) -> FastMCP[dict[str, Any]]:  # type: ignore[type-arg]
    """Construct the FastMCP app, keyed on ``settings.transport``.

    Two mutually exclusive shapes (docs/planning/cli-frontend.md D2/D3):
        - stdio (default): ``lifespan=_lifespan``, byte-identical to the
          server's pre-Phase-1 wiring. ``MCPServer.run()`` enters this once
          for the single stdio session.
        - streamable-http: NO ``lifespan=`` kwarg — under this SDK version
          FastMCP's own ``lifespan=`` runs per MCP session (per request when
          ``stateless_http=True``), which would cold-spawn/tear down the
          analyzer on every call. Instead ``server.main()`` composes a
          process-level lifespan that nests ``_lifespan`` wholesale around the
          SDK's session-manager lifespan (see the reference doc). ``host`` is
          hard-coded to ``"127.0.0.1"`` here — never taken from settings, so
          no configuration path can bind a non-loopback address (D3/D4).

    Split out from module scope so the branch is unit-testable without an
    ``importlib.reload`` dance: call this directly with a constructed
    ``Settings`` and inspect the resulting ``FastMCP.settings``.
    """
    if settings.transport == "streamable-http":
        return FastMCP(  # type: ignore[type-arg]
            "rust-lsp-mcp",
            host="127.0.0.1",
            port=settings.http_port,
            stateless_http=True,
            json_response=True,
            instructions=_INSTRUCTIONS,
        )
    return FastMCP(  # type: ignore[type-arg]
        "rust-lsp-mcp",
        lifespan=_lifespan,
        instructions=_INSTRUCTIONS,
    )


# Transport is read once at import time — the module-level ``mcp`` app is
# built exactly once per process, matching the "process-level" contract the
# daemon relies on. There is no supported way to flip transport within a
# running process; a different transport means a different process (see the
# reference doc's testability note).
mcp: FastMCP[dict[str, Any]] = _build_mcp(get_settings())  # type: ignore[type-arg]


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
        An ``error`` envelope (``recovery: "refresh"``) if the analyzer's
        background run failed (``state == "error"`` — permanent until
        ``refresh`` recovers it), a ``not_ready`` envelope
        (``recovery: "poll_status"``) if it is still indexing or the manager
        has not started, else ``None`` once ready.
    """
    if _manager is not None and _manager.state == STATE_ERROR:
        return error(
            "The analyzer failed to start and cannot serve navigation queries: "
            f"{_manager.error_message or 'unknown error'}. "
            "Call the refresh tool to retry, or check the server configuration "
            "(e.g. RLM_RUST_ANALYZER_BIN).",
            recovery=RECOVERY_REFRESH,
        )
    if _manager is None or not _manager.is_ready:
        return not_ready(INDEXING_RETRY_MESSAGE)
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
        NUL-containing, or ``..``-escaping).  The error envelope carries
        ``recovery: "fix_input"``.
    """
    if not _is_contained_relpath(file):
        return file, error(
            f"Invalid file path {file!r}: must be a workspace-relative path "
            "that does not resolve outside the workspace root.",
            recovery=RECOVERY_FIX_INPUT,
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
        2. ``sym["selectionRange"]["start"]`` (document-symbol shape, no
           location) — the symbol's *name* position, suitable for feeding
           back into hover/goto_definition/find_references.
        3. ``sym["range"]["start"]`` (document-symbol shape) — fallback used
           when ``selectionRange`` is absent or malformed.

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
        #
        # Per LSP, a DocumentSymbol's `range` spans the whole declaration
        # *including* leading doc comments and `#[attributes]`, while
        # `selectionRange` covers just the symbol's name.  Prefer
        # `selectionRange` so the returned position lands on the name and can
        # be fed straight back into hover/goto_definition/find_references;
        # fall back to `range` when `selectionRange` is absent or malformed
        # (defensive — keep working if a provider omits it).
        pos_range = sym.get("selectionRange")
        if not isinstance(pos_range, Mapping) or "start" not in pos_range:
            pos_range = sym.get("range")
        if pos_range is None:
            _log.debug(
                "symbol_to_external: candidate %r has no location or range — skipped",
                sym_name,
            )
            return None
        start = pos_range.get("start", {})
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


# ---------------------------------------------------------------------------
# Response-size cap — shared by every list-returning navigation tool
# ---------------------------------------------------------------------------

# High safety cap on unbounded LSP result lists (find_references, find_symbol,
# document_symbols). No pagination/offset — narrowing the query (a more
# specific position/name) is the intended way to get past this, not paging.
# See docs/audit/2026-07-02-usability-review.md, UR-11 (revised, absorbs UR-5
# and UR-12): always report `total`, and only set `truncated: true` when the
# full list actually exceeds the cap.
MAX_LIST_RESULTS = 200


def cap_list_results(items: list[Any]) -> tuple[list[Any], int, bool]:
    """Cap a result list at ``MAX_LIST_RESULTS``, preserving existing order.

    Args:
        items: The full, already-ordered result list.

    Returns:
        A 3-tuple ``(page, total, truncated)`` where ``page`` is ``items``
        unchanged when ``len(items) <= MAX_LIST_RESULTS``, else the first
        ``MAX_LIST_RESULTS`` items; ``total`` is ``len(items)`` (the full,
        pre-cap count); and ``truncated`` is ``True`` only when the cap
        actually cut the list.
    """
    total = len(items)
    if total > MAX_LIST_RESULTS:
        return items[:MAX_LIST_RESULTS], total, True
    return items, total, False
