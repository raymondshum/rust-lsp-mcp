"""search_docs tool — semantic search over the documentation RAG store.

Registered with the FastMCP app at import time via ``@mcp.tool()``.
"""

import logging
from typing import Any

from anyio.to_thread import run_sync
from mcp.types import ToolAnnotations

from rust_lsp_mcp.core import mcp
from rust_lsp_mcp.doc_store import DOC_STATE_ERROR, DocStoreNotReady, doc_store_state, get_doc_store
from rust_lsp_mcp.envelope import (
    RECOVERY_FIX_INPUT,
    RECOVERY_REFRESH,
    RECOVERY_UNKNOWN,
    error,
    not_found,
    not_ready,
    ok,
)

_log = logging.getLogger(__name__)

# Canonical not_ready message for the doc-index-still-building case — shared by
# all three not_ready branches below so they name the same poll tool/field
# (mirrors analyzer.py's INDEXING_RETRY_MESSAGE for the analyzer side).
DOC_INDEXING_RETRY_MESSAGE = (
    "The documentation index is still building. Retry after status reports "
    "doc_index_state == 'ready'."
)

# Upper clamp on `limit` (UR-12, folded into UR-11's list-tool cap convention).
# Bare ceiling only — deliberately NO `truncated` flag here: semantic (k-NN)
# search has no meaningful "more results exist" signal the way a full-list
# tool does (see docs/audit/2026-07-02-usability-review.md, UR-12 round-2).
MAX_DOC_RESULTS = 50


def _errored_build_envelope(reason: str | None) -> dict[str, Any]:
    """Return the ``error`` envelope for a permanently-failed doc-index build.

    Shared by the fast-path ``state == DOC_STATE_ERROR`` check and the
    ``DocStoreNotReady`` handler's re-check (so a store that transitions to
    ERROR mid-search is labelled ``error``, not a misleading ``not_ready``),
    keeping the message string single-sourced.
    """
    return error(
        "The documentation index failed to build and is unavailable: "
        f"{reason or 'unknown error'}. Run the refresh tool to rebuild it.",
        recovery=RECOVERY_REFRESH,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def search_docs(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the documentation index for chunks relevant to *query*.

    Runs a semantic (embedding) search over the Markdown documentation that was
    indexed at startup (and rebuilt on ``refresh``).  Results are returned
    best-first (lowest cosine distance first).

    Args:
        query: Natural-language search query.
        limit: Maximum number of results to return.  Clamped to
               ``[1, MAX_DOC_RESULTS]`` (currently 50).  Defaults to 5.

    Returns a ``{status, ...}`` envelope:

    - ``ok`` + ``results`` list — one or more matching chunks found.  Each
      chunk dict has EXACTLY this shape::

          {
            "file":       str,    # workspace-relative path of the source .md
            "breadcrumb": str,    # heading trail, e.g. "GUIDE.md > Config > Foo"
            "text":       str,    # the chunk body (breadcrumb prefix stripped)
            "distance":   float,  # cosine distance (0 = identical, lower = closer)
          }

      Results are ordered best-first (ascending distance).

    - ``not_ready`` — the doc index is absent or is currently being built
      (``doc_index_state == "building"``, see ``status``).  This is a
      *transient* state.  The caller **must not** interpret this as "no
      matching docs"; the store may be mid-build.  Retry after ``status``
      reports ``doc_index_state == "ready"``, or after ``refresh`` returns.
      Carries ``recovery: "poll_status"``.

    - ``error`` — (a) ``query`` is empty or whitespace-only (rejected before
      any readiness check or search, mirroring the position tools'
      input-validation style; carries ``recovery: "fix_input"``), (b) the doc
      index failed to build or initialise (``doc_index_state == "error"``) —
      this is a *permanent* condition until ``refresh`` rebuilds it, unlike
      the transient ``not_ready`` case above (carries ``recovery:
      "refresh"``) — or (c) an unexpected exception from the search layer
      itself (carries ``recovery: "unknown"``).  Either way the message
      includes the underlying reason.

    - ``not_found`` — the store is ready and the search returned zero results.
      This only happens when the collection is empty (semantic search over a
      populated collection always returns the top-k nearest neighbours).  It is
      semantically distinct from ``not_ready``: here the store *is* ready and
      genuinely found nothing.  The message enumerates the possible causes of
      an empty corpus (no Markdown matched, all matched files excluded, matched
      files empty, or the project simply ships no docs) without asserting
      misconfiguration.

    Invariant (load-bearing):
        ``not_ready``/``error`` is returned whenever ``is_ready`` is ``False``,
        so callers **never** receive a misleading empty-or-partial answer
        while a build is in flight or has permanently failed.  An empty ``ok``
        result is impossible: zero matches map to ``not_found``, not ``ok``.
    """
    if not query or not query.strip():
        return error("query must be a non-empty string", recovery=RECOVERY_FIX_INPUT)

    # Floor AND ceiling: a bare floor-only clamp let an oversized limit (or an
    # LLM auto-filling a large number) pull an unbounded number of chunks in
    # one response (UR-12, folded into UR-11's cap convention). No `truncated`
    # flag: semantic top-k search has no "more results exist" signal to report.
    limit = max(1, min(limit, MAX_DOC_RESULTS))

    store = get_doc_store()

    # Surface a permanently-failed build distinctly from "still building".
    # `state` is read via getattr for robustness against test doubles: a plain
    # MagicMock auto-creates `.state` on access (so the getattr default is not
    # what saves us) — but that auto-created attribute is a Mock, which is
    # ``!= DOC_STATE_ERROR``, so such fakes correctly fall through to the
    # is_ready check below rather than being misread as errored.
    if store is not None and getattr(store, "state", None) == DOC_STATE_ERROR:
        return _errored_build_envelope(store.error_message)
    if store is None:
        doc_state, doc_err = doc_store_state()
        if doc_state == DOC_STATE_ERROR:
            return error(
                "The documentation index failed to initialise and is unavailable: "
                f"{doc_err or 'unknown error'}. Run the refresh tool to rebuild it.",
                recovery=RECOVERY_REFRESH,
            )
        return not_ready(DOC_INDEXING_RETRY_MESSAGE)

    if not store.is_ready:
        return not_ready(DOC_INDEXING_RETRY_MESSAGE)

    try:
        hits = await run_sync(lambda: store.search(query, n_results=limit))
    except DocStoreNotReady:
        # The fast-path is_ready check above passed, but a concurrent rebuild
        # (DS-12) flipped state/collection before store.search() took its
        # atomic snapshot.  This is the race window closing correctly — it
        # normally maps to not_ready (transient), never a misleading empty.
        # BUT if that concurrent rebuild transitioned the store to ERROR
        # (permanent) between the fast-path check and the snapshot, re-read the
        # state now and surface the same permanent error the fast-path uses,
        # rather than briefly mislabelling a now-errored store as not_ready.
        if getattr(store, "state", None) == DOC_STATE_ERROR:
            return _errored_build_envelope(store.error_message)
        return not_ready(DOC_INDEXING_RETRY_MESSAGE)
    except Exception as exc:
        _log.exception("search_docs: store.search raised for query %r", query)
        return error(f"Documentation search error: {exc}", recovery=RECOVERY_UNKNOWN)

    if not hits:
        return not_found(
            "The documentation index is ready but contains 0 chunks. Possible causes: "
            "no Markdown files matched RLM_DOC_GLOB_PATTERNS; all matches were excluded "
            "by RLM_DOC_EXCLUDE_PATTERNS; the matched files were empty; or this project "
            "simply ships no Markdown docs."
        )

    return ok(results=hits)
