"""search_docs tool — semantic search over the documentation RAG store.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

import logging
from typing import Any

from anyio.to_thread import run_sync

from rust_lsp_mcp.core import mcp
from rust_lsp_mcp.doc_store import get_doc_store
from rust_lsp_mcp.envelope import error, not_found, not_ready, ok

_log = logging.getLogger(__name__)


@mcp.tool()
async def search_docs(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the documentation index for chunks relevant to *query*.

    Runs a semantic (embedding) search over the Markdown documentation that was
    indexed at startup (and rebuilt on ``refresh``).  Results are returned
    best-first (lowest cosine distance first).

    Args:
        query: Natural-language search query.
        limit: Maximum number of results to return.  Clamped to at least 1.
               Defaults to 5.

    Returns a ``{status, ...}`` envelope:

    - ``ok`` + ``results`` list — one or more matching chunks found.  Each
      chunk dict has EXACTLY this shape::

          {
            "file":       str,    # workspace-relative path of the source .md
            "breadcrumb": str,    # heading trail, e.g. "GUIDE.md > Config > Foo"
            "text":       str,    # the chunk text that was embedded
            "distance":   float,  # cosine distance (0 = identical, lower = closer)
          }

      Results are ordered best-first (ascending distance).

    - ``not_ready`` — the doc index is absent or is currently being rebuilt.
      The caller **must not** interpret this as "no matching docs"; the store
      may be mid-rebuild.  Retry after ``status`` reports ``"ready"``, or after
      ``refresh`` returns.

    - ``not_found`` — the store is ready and the search returned zero results.
      This only happens when the collection is empty (semantic search over a
      populated collection always returns the top-k nearest neighbours).  It is
      semantically distinct from ``not_ready``: here the store *is* ready and
      genuinely found nothing.

    - ``error`` — unexpected exception from the search layer; includes a
      diagnostic message.

    Invariant (load-bearing):
        ``not_ready`` is returned whenever ``is_ready`` is ``False``, so callers
        **never** receive a misleading empty-or-partial answer while a rebuild is
        in flight.  An empty ``ok`` result is impossible: zero matches map to
        ``not_found``, not ``ok``.
    """
    limit = max(1, limit)

    store = get_doc_store()
    if store is None or not store.is_ready:
        return not_ready(
            "The documentation index is not available or is currently rebuilding. "
            "Retry after the store is ready."
        )

    try:
        hits = await run_sync(lambda: store.search(query, n_results=limit))
    except Exception as exc:
        _log.exception("search_docs: store.search raised for query %r", query)
        return error(f"Documentation search error: {exc}")

    if not hits:
        return not_found(f"No documentation chunks matched {query!r}. The collection may be empty.")

    return ok(results=hits)
