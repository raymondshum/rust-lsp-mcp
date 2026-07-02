"""document_symbols tool — flat outline of all symbols in a single Rust source file.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

import logging
from typing import Any

from rust_lsp_mcp.core import (
    get_manager,
    mcp,
    require_ready,
    symbol_to_external,
    validate_workspace_file,
)
from rust_lsp_mcp.envelope import error, ok

_log = logging.getLogger(__name__)


@mcp.tool()
async def document_symbols(file: str) -> dict[str, Any]:
    """List all symbols declared in a single Rust source file (flat outline).

    Runs a document-symbol query against the live rust-analyzer index and returns
    a flat list of every symbol in the specified file.  The list is ordered as
    rust-analyzer reports it (typically declaration order within the file).

    Args:
        file: Workspace-relative path to the Rust source file
              (e.g. ``"src/main.rs"``).

    Returns a ``{status, ...}`` envelope:

    - ``ok`` + ``symbols`` list — query succeeded.  The list may be empty if the
      file exists but defines no symbols.  **Empty symbols is not an error** — it
      is a valid, meaningful answer (e.g. a file with only comments or macros).
      Each symbol::

          {
            "name":      str,        # symbol name as declared
            "kind":      str,        # human-readable SymbolKind (e.g. "Function")
            "line":      int,        # 1-indexed line number (start of declaration)
            "character": int,        # 1-indexed character offset (start of declaration)
            "container": str | null  # enclosing scope name, or null
          }

      Note: document-symbol results carry a top-level ``range`` rather than a
      ``location`` sub-dict, so ``container`` is almost always ``null`` (the LSP
      spec carries a ``containerName`` field but rust-analyzer rarely populates it
      for the flat document-symbol response).

      Note: there is **no** ``file`` field in each symbol — all symbols belong to
      the queried ``file`` argument.

    - ``not_ready`` — the analyzer is still indexing; retry after
      ``analyzer_status`` reports ``"ready"``.

    - ``error`` — the LSP layer raised an exception.  This happens for
      non-existent or unreadable file paths (multilspy raises rather than
      returning an empty list for unknown paths).  This is deliberately distinct
      from ``ok``+``symbols=[]``, which means the file is valid but has no symbols.
      ``error`` is also returned when ``file`` is not a workspace-relative path
      that stays inside the workspace root (absolute paths and ``..``-escaping
      paths are rejected immediately, without calling the analyzer).

    Positions are 1-indexed (the helper converts from LSP 0-based internally).
    Candidates missing both a ``location`` and a top-level ``range``, or with an
    empty/whitespace name, are silently skipped.
    """
    # Validate the file path (reject absolute/escaping paths before the
    # analyzer ever sees them).  The normalized form is forwarded so a
    # symlink+``..`` combination cannot resolve outside the root at the OS level.
    file, guard = validate_workspace_file(file)
    if guard is not None:
        return guard

    if (guard := require_ready()) is not None:
        return guard

    manager = get_manager()
    assert manager is not None  # guaranteed by require_ready()

    try:
        raw = await manager.request_document_symbols(file)
    except Exception as exc:
        _log.exception("document_symbols: LSP error for file %r", file)
        return error(f"LSP error: {exc}")

    repo_root = manager.repository_root

    symbols: list[dict[str, Any]] = []
    for sym in raw:
        mapped = symbol_to_external(sym, repo_root, default_file=file)
        if mapped is None:
            # symbol_to_external already logged the skip at DEBUG
            continue
        # Drop the "file" key — all symbols belong to the queried file;
        # repeating it on every entry would be redundant noise.
        entry = {
            "name": mapped["name"],
            "kind": mapped["kind"],
            "line": mapped["line"],
            "character": mapped["character"],
            "container": mapped["container"],
        }
        symbols.append(entry)

    return ok(symbols=symbols)
