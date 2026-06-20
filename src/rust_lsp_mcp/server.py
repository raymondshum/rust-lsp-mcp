"""FastMCP server for rust-lsp-mcp — Phase 2: find_symbol + readiness gating.

This module wires together:
    - FastMCP application with lifespan management (starts/stops the analyzer).
    - ``require_ready`` gate — returns ``not_ready`` immediately if the analyzer
      is still indexing.  Never blocks a request; never returns a misleading empty.
    - Phase 1 tool surface (``analyzer_status``, ``probe``).
    - Phase 2 tool: ``find_symbol`` — name→position resolution via workspace-symbol.

Tool surface (Phase 2):
    analyzer_status  — Reports the current readiness state (``indexing``|``ready``)
                       wrapped in an ``ok`` envelope.  The full 4-field Phase 4
                       ``status`` tool (with ``indexed_commit``, ``current_commit``,
                       ``stale``) extends this later.
    probe            — A gated no-op that returns ``ok`` only once the analyzer is
                       ready; returns ``not_ready`` while indexing.  Proves the
                       fail-fast gate via an actual tool call.
    find_symbol      — Name→position resolution.  Runs ``workspace_symbol``, returns
                       all candidates.  Zero matches → ``not_found`` (not ``ok``+empty).
                       Multiple matches are a normal multi-hit list (no ``ambiguous``).

Entry point:
    ``main()`` calls ``mcp.run()``, which is synchronous (wraps anyio.run) and
    uses stdio transport by default.  It is importable from ``rust_lsp_mcp``
    so both launch paths work:
        - ``uv run rust-lsp-mcp``   (console script)
        - ``python -m rust_lsp_mcp``
"""

import logging
import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import FastMCP
from multilspy.multilspy_types import SymbolKind

from rust_lsp_mcp.analyzer import STATE_READY, AnalyzerManager, analyzer_lifespan
from rust_lsp_mcp.envelope import error, not_found, not_ready, ok
from rust_lsp_mcp.positions import lsp_to_external

_log = logging.getLogger(__name__)


def _uri_to_relative_path(uri: str, repository_root: str) -> str | None:
    """Convert a ``file://`` URI to a workspace-relative path.

    multilspy's ``request_workspace_symbol`` returns ``Location`` dicts with
    ``uri`` and ``range`` only — it does not populate ``relativePath``
    (confirmed against multilspy 0.0.15 at runtime).  We derive the relative
    path from the URI by stripping the ``file://`` prefix and computing the
    path relative to the repository root.

    Returns ``None`` if the URI is not under the repository root.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    abs_path = pathlib.Path(unquote(parsed.path))
    repo_root = pathlib.Path(repository_root)
    try:
        return str(abs_path.relative_to(repo_root))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Module-level manager reference — set during lifespan startup, cleared on exit.
# Tools call require_ready() which reads this.
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
# Readiness gate
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
    if _manager is None or _manager.state != STATE_READY:
        return not_ready()
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def analyzer_status() -> dict[str, Any]:
    """Return the current readiness state of the rust-analyzer backend.

    Returns an ``ok`` envelope with a ``state`` field:
        - ``"indexing"`` — still warming up; gated tools return ``not_ready``.
        - ``"ready"``    — indexing complete; all tools are available.

    Phase 4 will extend this with ``indexed_commit``, ``current_commit``, ``stale``.
    """
    state = _manager.state if _manager is not None else "indexing"
    return ok(state=state)


@mcp.tool()
def probe() -> dict[str, Any]:
    """Gated no-op probe — proves the fail-fast gate works end-to-end.

    Returns ``not_ready`` while the analyzer is indexing, ``ok`` once ready.
    This tool has no semantic value beyond demonstrating and testing the
    ``require_ready`` invariant; navigation tools (Phase 3) will use the same gate.
    """
    if (guard := require_ready()) is not None:
        return guard
    return ok(message="Analyzer is ready.")


@mcp.tool()
async def find_symbol(name: str) -> dict[str, Any]:
    """Resolve a Rust symbol name to its workspace position(s).

    Runs a workspace-symbol query (fuzzy) against the live rust-analyzer index
    and returns all matching candidates.  Positions are 1-indexed (line and
    character), workspace-relative file paths.

    Args:
        name: Symbol name (or prefix) to search for.  Rust-analyzer performs a
              fuzzy match, so partial names are supported.

    Returns a ``{status, ...}`` envelope:

    - ``ok`` + ``results`` list — one or more candidates found.  Each candidate::

          {
            "name":      str,          # symbol name as declared
            "kind":      str,          # human-readable SymbolKind (e.g. "Function")
            "file":      str,          # workspace-relative path (e.g. "src/main.rs")
            "line":      int,          # 1-indexed line number
            "character": int,          # 1-indexed character offset
            "container": str | null    # enclosing module/impl name, or null
          }

      Multiple candidates are a normal multi-hit list — the caller picks the
      right one by kind/container/location.  There is no ``ambiguous`` status.

    - ``not_found`` — zero matches (or the LSP returned null).  This means
      name resolution failed; it is NOT the same as ``ok``+empty.

    - ``not_ready`` — the analyzer is still indexing; retry after
      ``analyzer_status`` reports ``"ready"``.

    - ``error`` — unexpected exception from the LSP layer; includes a message.

    Defensive handling:
        Candidates missing a ``location`` or a null ``relativePath`` are silently
        skipped (logged at DEBUG level).  This keeps the tool from crashing on
        malformed LSP responses while still returning all usable candidates.
    """
    if (guard := require_ready()) is not None:
        return guard

    assert _manager is not None  # guaranteed by require_ready()

    try:
        raw = await _manager.request_workspace_symbol(name)
    except Exception as exc:
        _log.exception("find_symbol: LSP error for query %r", name)
        return error(f"LSP error: {exc}")

    # multilspy returns None when the server returns no result at all
    if raw is None:
        return not_found(f"No symbol found matching {name!r}.")

    repo_root = _manager.repository_root

    results: list[dict[str, Any]] = []
    for sym in raw:
        # Defensive: skip candidates without a location or a usable relative path
        loc = sym.get("location")
        if loc is None:
            _log.debug("find_symbol: candidate %r has no location — skipped", sym.get("name"))
            continue

        # multilspy 0.0.15 does NOT populate relativePath in workspace_symbol results
        # (confirmed at runtime: location only contains 'uri' and 'range').
        # Derive the workspace-relative path from the uri ourselves.
        rel_path: str | None = loc.get("relativePath")
        if not rel_path:
            uri = loc.get("uri", "")
            rel_path = _uri_to_relative_path(uri, repo_root) if uri else None
        if not rel_path:
            _log.debug(
                "find_symbol: candidate %r has no usable path (uri=%r) — skipped",
                sym.get("name"),
                loc.get("uri"),
            )
            continue

        rng = loc.get("range", {})
        start = rng.get("start", {})
        ext = lsp_to_external(
            lsp_line=start.get("line", 0),
            lsp_character=start.get("character", 0),
        )

        kind_raw = sym.get("kind")
        try:
            kind_str = SymbolKind(kind_raw).name
        except (ValueError, KeyError):
            kind_str = str(kind_raw)

        container = sym.get("containerName")  # NotRequired — may be absent → None

        results.append(
            {
                "name": sym["name"],
                "kind": kind_str,
                "file": rel_path,
                "line": ext.line,
                "character": ext.character,
                "container": container,
            }
        )

    if not results:
        return not_found(f"No symbol found matching {name!r}.")

    return ok(results=results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the MCP server over stdio (synchronous — wraps anyio.run internally).

    This is the console-script entry point (``rust-lsp-mcp``) and is also
    called by ``__main__.py`` for ``python -m rust_lsp_mcp``.
    """
    mcp.run()
