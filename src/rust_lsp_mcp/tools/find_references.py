"""find_references tool — find all uses of a symbol at a given position.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

import logging
from typing import Any

from rust_lsp_mcp.core import get_manager, location_to_external, mcp, require_ready
from rust_lsp_mcp.envelope import error, ok
from rust_lsp_mcp.positions import external_to_lsp

_log = logging.getLogger(__name__)


@mcp.tool()
async def find_references(
    file: str,
    line: int,
    character: int,
    include_declaration: bool = False,
) -> dict[str, Any]:
    """Find all references to the symbol at the given position.

    Queries the live rust-analyzer index for uses of the symbol at
    ``(line, character)`` in ``file`` and returns their locations.  Positions
    are 1-indexed (line and character), consistent with the rest of the tool
    surface.

    Default behaviour (``include_declaration=False``) returns uses only.  The
    declaration itself is goto_definition's job; callers who also want it can
    pass ``include_declaration=True``.

    **Key semantic: zero references → ok + empty list (not not_found).**
    An empty ``references`` list is a legitimate answer — it means the symbol
    has no callers in the indexed workspace (e.g. a dead function, a private
    item used only at its definition site, or a public API with no in-tree
    users).  ``not_found`` is NOT returned here; it is reserved for tools where
    resolution itself fails (e.g. find_symbol with zero workspace-symbol hits).

    **include_declaration synthesis:**
    multilspy's ``request_references`` always sends
    ``context.includeDeclaration = False`` (hardcoded in multilspy 0.0.15).
    When ``include_declaration=True`` is requested the declaration cannot come
    from the references call; instead this tool makes a second
    ``request_definition`` call and unions the definition location(s) into the
    reference set.  After mapping both sets to external positions the merged
    list is deduplicated by ``(file, line, character)`` so a definition site
    that also appears in the reference list is not double-counted.

    When ``include_declaration=False`` (the default), ``request_definition`` is
    never called — no extra round-trip to the analyzer.

    Args:
        file:                Workspace-relative path to the file
                             (e.g. ``"src/main.rs"``).
        line:                1-indexed line number of the symbol.
        character:           1-indexed character offset of the symbol.
        include_declaration: If ``True``, synthesize the declaration by merging
                             the go-to-definition result into the reference list
                             (deduped).  Default ``False`` (uses-only).

    Returns a ``{status, ...}`` envelope:

    - ``ok`` + ``references`` list — analysis succeeded.  The list may be empty
      (zero callers is a valid result, not an error).  Each reference::

          {
            "file":      str,  # workspace-relative path (e.g. "src/lib.rs")
            "line":      int,  # 1-indexed line number
            "character": int,  # 1-indexed character offset
          }

    - ``not_ready`` — the analyzer is still indexing; retry after
      ``analyzer_status`` reports ``"ready"``.

    - ``error`` — input validation failed (line/character < 1) or an unexpected
      exception from the LSP layer; includes a message.

    UNVERIFIED (integration gate required):
        multilspy asserts the LSP response for references is a list; if
        rust-analyzer ever returns ``null`` for zero references the delegate
        already normalises this to ``[]`` (``request_references`` returns
        ``[]`` on ``None``), so ``ok``+empty is still the correct outcome.
        The integration gate confirms rust-analyzer emits ``[]`` (not ``null``)
        for genuinely-zero-reference symbols — this fast path relies on that.
    """
    # Step 1: validate input positions (must be 1-indexed, i.e. >= 1).
    if line < 1 or character < 1:
        return error(
            f"Invalid position: line and character must be >= 1"
            f" (got line={line}, character={character})."
        )

    # Step 2: gate on analyzer readiness.
    if (guard := require_ready()) is not None:
        return guard

    # Step 3: get manager and convert to LSP (0-indexed) coordinates.
    mgr = get_manager()
    assert mgr is not None  # guaranteed by require_ready()

    pos = external_to_lsp(line, character)

    # Step 4: request references from the live analyzer.
    try:
        refs = await mgr.request_references(file, pos.line, pos.character)
    except Exception as exc:
        _log.exception("find_references: LSP error for %r at (%d, %d)", file, line, character)
        return error(f"LSP error: {exc}")

    # refs is list[Location]; request_references normalises None → [].

    repo_root = mgr.repository_root

    # Step 5: map reference locations to external (1-indexed) positions.
    # Use a dict keyed by (file, line, character) to deduplicate.
    seen: dict[tuple[str, int, int], dict[str, Any]] = {}

    for loc in refs:
        mapped = location_to_external(loc, repo_root)
        if mapped is None:
            _log.debug("find_references: skipping unmappable location %r", loc)
            continue
        key = (mapped["file"], mapped["line"], mapped["character"])
        seen[key] = mapped

    # Step 5 (continued): if include_declaration, synthesize by merging the
    # definition location(s).  Only call request_definition when needed.
    if include_declaration:
        try:
            defs = await mgr.request_definition(file, pos.line, pos.character)
        except Exception as exc:
            _log.exception(
                "find_references: LSP error fetching definition for %r at (%d, %d)",
                file,
                line,
                character,
            )
            return error(f"LSP error (definition): {exc}")

        for loc in defs:
            mapped = location_to_external(loc, repo_root)
            if mapped is None:
                _log.debug("find_references: skipping unmappable definition location %r", loc)
                continue
            key = (mapped["file"], mapped["line"], mapped["character"])
            # Only insert if not already present (declaration already in refs list).
            seen.setdefault(key, mapped)

    # Step 6/7: return ok envelope with the (possibly empty) reference list.
    # Zero references is a valid "no callers" answer — never return not_found here.
    return ok(references=list(seen.values()))
