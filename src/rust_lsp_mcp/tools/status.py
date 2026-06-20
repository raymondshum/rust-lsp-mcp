"""Full 4-field status tool — state, indexed_commit, current_commit, stale.

Registered with the FastMCP app at import time via ``@mcp.tool()``.

This tool is UNGATED (it is the readiness check itself) and never returns
``not_ready``.
"""

import subprocess
from typing import Any

from rust_lsp_mcp.core import get_manager, mcp
from rust_lsp_mcp.envelope import ok
from rust_lsp_mcp.settings import get_settings


@mcp.tool()
def status() -> dict[str, Any]:
    """Return the full 4-field status of the rust-analyzer backend.

    Returns an ``ok`` envelope with four fields:

    - ``state``          — ``"indexing"`` while warming up, ``"ready"`` when
                           the analyzer is live.
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

    .. caution::

        Commit-hash comparison does **not** detect uncommitted working-tree
        edits, so ``stale: false`` means "no *committed* changes since
        indexing," not a freshness guarantee.  For the pinned ripgrep clone
        (no active development commits) this is effectively always ready and
        not stale.

    This tool is always callable regardless of analyzer state — it is the
    readiness check itself and therefore never returns ``not_ready``.
    """
    mgr = get_manager()

    state: str = mgr.state if mgr is not None else "indexing"
    indexed_commit: str | None = mgr.indexed_commit if mgr is not None else None
    repo_root: str = mgr.repository_root if mgr is not None else get_settings().ripgrep_src

    current_commit: str | None = _git_head(repo_root)

    stale: bool | None
    if indexed_commit is None or current_commit is None:
        stale = None
    else:
        stale = indexed_commit != current_commit

    return ok(
        state=state,
        indexed_commit=indexed_commit,
        current_commit=current_commit,
        stale=stale,
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
