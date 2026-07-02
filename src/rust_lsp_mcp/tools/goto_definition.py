"""goto_definition tool — jump to the definition of the symbol at a given position.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

import logging
from typing import Any

from rust_lsp_mcp.analyzer import AnalyzerTornDownError
from rust_lsp_mcp.core import (
    get_manager,
    location_to_external,
    mcp,
    require_ready,
    validate_workspace_file,
)
from rust_lsp_mcp.envelope import error, not_found, not_ready, ok
from rust_lsp_mcp.positions import external_to_lsp

_log = logging.getLogger(__name__)


@mcp.tool()
async def goto_definition(file: str, line: int, character: int) -> dict[str, Any]:
    """Jump to the definition of the symbol at the given 1-indexed position.

    Sends an LSP ``textDocument/definition`` request for the specified position
    and returns all definition sites.  Positions are 1-indexed on both input
    and output (line 1, character 1 is the first character of the file).

    Args:
        file:      Workspace-relative path to the Rust source file
                   (e.g. ``"src/main.rs"``).
        line:      1-indexed line number of the cursor position.
        character: 1-indexed character offset of the cursor position.

    Returns a ``{status, ...}`` envelope:

    - ``ok`` + ``definitions`` list — one or more definition sites found.
      Each entry::

          {
            "file":      str,  # workspace-relative path
            "line":      int,  # 1-indexed line number
            "character": int,  # 1-indexed character offset
          }

      Multiple definitions are a normal multi-site list (e.g. for trait
      methods with multiple impls).  The caller picks the right one.

    - ``not_found`` — the LSP returned no definition for that position.  This
      is a normal outcome for whitespace, comments, or unknown symbols; it is
      NOT the same as ``ok``+empty.

    - ``not_ready`` — the analyzer is still indexing; retry after
      ``analyzer_status`` reports ``"ready"``.

    - ``error`` — input validation failure or unexpected LSP exception; includes
      a message.  Positions must be >= 1 (1-indexed); supplying 0 or negative
      values returns an ``error`` immediately without calling the analyzer.
      ``file`` must be a workspace-relative path that does not resolve outside
      the workspace root (absolute paths and ``..``-escaping paths are
      rejected immediately, without calling the analyzer).
    """
    # Step 1: validate 1-indexed inputs
    if line < 1 or character < 1:
        return error("line and character are 1-indexed; must be >= 1")

    # Step 2: validate the file path (reject absolute/escaping paths before
    # the analyzer ever sees them).  The normalized form is forwarded so a
    # symlink+``..`` combination cannot resolve outside the root at the OS level.
    file, guard = validate_workspace_file(file)
    if guard is not None:
        return guard

    # Step 3: readiness gate
    if (guard := require_ready()) is not None:
        return guard

    # Step 4: get manager
    mgr = get_manager()
    assert mgr is not None  # guaranteed by require_ready()

    # Step 5: convert 1-indexed external positions to 0-indexed LSP positions
    pos = external_to_lsp(line, character)

    # Step 6: call the LSP delegate
    try:
        locs = await mgr.request_definition(file, pos.line, pos.character)
    except AnalyzerTornDownError:
        return not_ready(
            "The analyzer was restarted or shut down while this request was in flight. "
            "Retry after analyzer_status reports ready."
        )
    except Exception as exc:
        _log.exception("goto_definition: LSP error for %r line=%d char=%d", file, line, character)
        return error(f"LSP error: {exc}")

    # Step 7: map results to external (1-indexed) positions
    if not locs:
        return not_found(f"No definition found at {file}:{line}:{character}.")

    repo_root = mgr.repository_root
    definitions: list[dict[str, Any]] = []
    for loc in locs:
        mapped = location_to_external(loc, repo_root)
        if mapped is None:
            _log.debug("goto_definition: skipping location with no usable path: %r", loc)
            continue
        definitions.append(mapped)

    # Step 8: empty list / all-skipped → not_found
    if not definitions:
        return not_found(f"No definition found at {file}:{line}:{character}.")

    return ok(definitions=definitions)
