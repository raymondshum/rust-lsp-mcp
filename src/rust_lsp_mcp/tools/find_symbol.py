"""find_symbol tool — name→position resolution via workspace-symbol LSP query.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

import logging
from typing import Any

from rust_lsp_mcp.analyzer import AnalyzerTornDownError
from rust_lsp_mcp.core import get_manager, mcp, require_ready, symbol_to_external
from rust_lsp_mcp.envelope import error, not_found, not_ready, ok

_log = logging.getLogger(__name__)


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
        Candidates missing a ``location``, with a null ``relativePath``, or whose
        path resolves outside the workspace root are silently skipped (logged at
        DEBUG level).  This keeps the tool from crashing on malformed LSP
        responses while still returning all usable in-workspace candidates.
    """
    if (guard := require_ready()) is not None:
        return guard

    manager = get_manager()
    assert manager is not None  # guaranteed by require_ready()

    try:
        raw = await manager.request_workspace_symbol(name)
    except AnalyzerTornDownError:
        return not_ready(
            "The analyzer was restarted or shut down while this request was in flight. "
            "Retry after analyzer_status reports ready."
        )
    except Exception as exc:
        _log.exception("find_symbol: LSP error for query %r", name)
        return error(f"LSP error: {exc}")

    # multilspy returns None when the server returns no result at all
    if raw is None:
        return not_found(f"No symbol found matching {name!r}.")

    repo_root = manager.repository_root

    results: list[dict[str, Any]] = []
    for sym in raw:
        mapped = symbol_to_external(sym, repo_root)
        if mapped is None:
            # symbol_to_external already logged the skip at DEBUG
            continue
        results.append(mapped)

    if not results:
        return not_found(f"No symbol found matching {name!r}.")

    return ok(results=results)
