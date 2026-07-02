"""hover tool — returns rust-analyzer hover markdown at a given position.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

import logging
from typing import Any

from rust_lsp_mcp.core import get_manager, mcp, require_ready, validate_workspace_file
from rust_lsp_mcp.envelope import error, not_found, ok
from rust_lsp_mcp.positions import external_to_lsp

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helper: normalize Hover.contents to a single markdown string
# ---------------------------------------------------------------------------


def _contents_to_str(contents: Any) -> str:
    """Normalize a Hover ``contents`` value to a single markdown string.

    Handles all shapes that multilspy / rust-analyzer may return:

    - ``MarkupContent`` — a dict with a ``"value"`` key (and ``"kind"``).
      Returns the ``"value"`` string directly.

    - ``MarkedString`` (plain str) — returned as-is.

    - ``MarkedString`` (dict with ``"language"`` and ``"value"``) — returns
      the ``"value"`` string.  (Optionally the caller could fence it with the
      language, but the raw value is simpler and sufficient.)

    - ``list`` of any of the above — each element is normalized recursively
      and joined with ``"\\n\\n"``.

    Args:
        contents: The raw ``contents`` field from a ``Hover`` TypedDict.

    Returns:
        A single (possibly empty) string.
    """
    if isinstance(contents, list):
        parts = [_contents_to_str(item) for item in contents]
        return "\n\n".join(parts)
    if isinstance(contents, dict):
        # Both MarkupContent and MarkedString-as-dict expose a "value" key.
        return contents.get("value", "")
    # Plain string (MarkedString or legacy plain-str MarkupContent).
    if isinstance(contents, str):
        return contents
    return ""


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@mcp.tool()
async def hover(file: str, line: int, character: int) -> dict[str, Any]:
    """Return rust-analyzer hover markdown at the given position (type signature + docs).

    Queries rust-analyzer for hover information at the specified 1-indexed
    position and returns the hover markdown string as-is — no parsing or
    reformatting is applied.  The string typically contains the item's type
    signature followed by any rustdoc documentation.

    Args:
        file:      Workspace-relative path to the Rust source file
                   (e.g. ``"src/main.rs"``).
        line:      1-indexed line number.
        character: 1-indexed character (column) offset.

    Returns a ``{status, ...}`` envelope:

    - ``ok`` + ``contents`` (str) — hover markdown returned by rust-analyzer.

    - ``not_found`` — the analyzer has no hover info at this position (e.g.
      the token is whitespace, a comment, or an unsupported construct).
      This is NOT the same as ``ok``+empty.

    - ``not_ready`` — the analyzer is still indexing; retry after
      ``analyzer_status`` reports ``"ready"``.

    - ``error`` — invalid input (line/character < 1) or an unexpected
      exception from the LSP layer; includes a message.  ``file`` must be a
      workspace-relative path that does not resolve outside the workspace
      root (absolute paths and ``..``-escaping paths are rejected
      immediately, without calling the analyzer).

    Positions are 1-indexed (same convention as ``find_symbol`` output).
    Nothing to hover → ``not_found``; rust-analyzer returns the info as
    markdown → ``ok`` + ``contents``.

    rust-analyzer emits ``contents`` as ``MarkupContent`` (confirmed live in the
    Phase 3+4 integration gate). The helper still defensively normalizes the other
    documented shapes (``MarkedString`` / list) in case a future version differs.
    """
    # 1. Input validation.
    if line < 1 or character < 1:
        return error(f"line and character must be >= 1 (got line={line}, character={character})")

    # 2. Validate the file path (reject absolute/escaping paths before the
    # analyzer ever sees them).  The normalized form is forwarded so a
    # symlink+``..`` combination cannot resolve outside the root at the OS level.
    file, guard = validate_workspace_file(file)
    if guard is not None:
        return guard

    # 3. Readiness gate.
    if (guard := require_ready()) is not None:
        return guard

    manager = get_manager()
    assert manager is not None  # guaranteed by require_ready()

    # 4. Convert to 0-indexed LSP position and call the delegate.
    pos = external_to_lsp(line, character)
    try:
        hov = await manager.request_hover(file, pos.line, pos.character)
    except Exception as exc:
        _log.exception("hover: LSP error at %s:%d:%d", file, line, character)
        return error(f"LSP error: {exc}")

    # 5. Handle None (no hover info).
    if hov is None:
        return not_found(f"No hover information at {file}:{line}:{character}.")

    # 6. Normalize contents to a string.
    contents_str = _contents_to_str(hov["contents"])

    # 7. Empty/whitespace contents → not_found.
    if not contents_str or not contents_str.strip():
        return not_found(f"No hover information at {file}:{line}:{character}.")

    return ok(contents=contents_str)
