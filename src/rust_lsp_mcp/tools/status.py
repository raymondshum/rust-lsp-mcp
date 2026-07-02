"""Full 4-field status tool — state, indexed_commit, current_commit, stale.

Registered with the FastMCP app at import time via ``@mcp.tool()``.

This tool is UNGATED (it is the readiness check itself) and never returns
``not_ready``.
"""

import asyncio
import subprocess
from typing import Any

from rust_lsp_mcp.core import get_manager, mcp
from rust_lsp_mcp.doc_store import doc_store_state
from rust_lsp_mcp.envelope import ok
from rust_lsp_mcp.settings import get_settings


@mcp.tool()
async def status() -> dict[str, Any]:
    """Return the full status of the rust-analyzer backend and the doc index.

    Returns an ``ok`` envelope — this tool is always ``ok`` (never
    ``not_ready``/``error``, even when the analyzer or doc index has failed;
    failures are reported as *field values*, not as the envelope status):

    - ``state``          — ``"indexing"`` while warming up, ``"ready"`` when
                           the analyzer is live, ``"error"`` if the background
                           indexing run failed (see ``analyzer_error``).  Gated
                           tools return an ``error`` envelope (not
                           ``not_ready``) while ``state == "error"``; call
                           ``refresh`` to retry.
    - ``analyzer_error``  — diagnostic message when ``state == "error"``, else
                           ``null``.
    - ``indexed_commit`` — git HEAD hash captured when indexing began, or
                           ``null`` if not yet captured or git is unavailable.
    - ``current_commit`` — git HEAD hash at call time (``git -C <repo> rev-parse
                           HEAD``), or ``null`` on any failure (non-git directory,
                           git binary missing, subprocess error).  Never raises.
    - ``stale``          — tri-state:
                           ``null``  if either commit hash is unknown (cannot
                                     determine freshness);
                           ``false`` if ``indexed_commit == current_commit``
                                     (no committed changes since indexing);
                           ``true``  if the commits differ (committed changes
                                     have landed since indexing began).
    - ``doc_index_state`` — ``"building"``/``"ready"``/``"error"`` for the
                           documentation search index (independent of
                           ``state`` above — see ``search_docs``).
    - ``doc_index_error`` — diagnostic message when ``doc_index_state ==
                           "error"``, else ``null``.

    .. caution::

        Commit-hash comparison does **not** detect uncommitted working-tree
        edits, so ``stale: false`` means "no *committed* changes since
        indexing," not a freshness guarantee.  For a target project with no
        commits since indexing began, ``stale`` is ``false``; once a newer
        commit lands it flips to ``true``.

    This tool is always callable regardless of analyzer state — it is the
    readiness check itself and therefore never returns ``not_ready``.
    """
    mgr = get_manager()

    # Read all in-process state up front, BEFORE the only suspension point
    # below, so the analyzer/doc-store fields form one point-in-time snapshot
    # that a concurrent restart()/refresh() cannot tear across the await.
    state: str = mgr.state if mgr is not None else "indexing"
    analyzer_error: str | None = mgr.error_message if mgr is not None else None
    indexed_commit: str | None = mgr.indexed_commit if mgr is not None else None
    repo_root: str = mgr.repository_root if mgr is not None else get_settings().project_root
    doc_state, doc_err = doc_store_state()

    # DS-19: the pinned MCP SDK runs non-async tools INLINE on the event loop
    # (no thread offload), so a synchronous subprocess.run here would block
    # every other in-flight request for a git fork+exec on the hottest
    # polling path. Offload to a worker thread; _git_head itself stays
    # synchronous (it's the worker-thread body — mirrors
    # AnalyzerManager._capture_head_commit's identical pattern).
    current_commit: str | None = await asyncio.to_thread(_git_head, repo_root)

    stale: bool | None
    if indexed_commit is None or current_commit is None:
        stale = None
    else:
        stale = indexed_commit != current_commit

    return ok(
        state=state,
        analyzer_error=analyzer_error,
        indexed_commit=indexed_commit,
        current_commit=current_commit,
        stale=stale,
        doc_index_state=doc_state,
        doc_index_error=doc_err,
    )


def _git_head(repo_root: str) -> str | None:
    """Return ``git -C <repo_root> rev-parse HEAD`` output, or ``None`` on any failure.

    Uses ``subprocess.run`` (synchronous) without ``shell=True``.  Never raises.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception:
        return None
