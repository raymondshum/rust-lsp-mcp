"""Full 4-field status tool — state, indexed_commit, current_commit, stale.

Registered with the FastMCP app at import time via ``@mcp.tool()``.

This tool is UNGATED (it is the readiness check itself) and never returns
``not_ready``.
"""

import asyncio
import importlib.metadata
import subprocess
from typing import Any

from mcp.types import ToolAnnotations

from rust_lsp_mcp.core import get_manager, get_preflight_warnings, mcp
from rust_lsp_mcp.doc_store import doc_index_chunk_count, doc_store_state
from rust_lsp_mcp.envelope import ok
from rust_lsp_mcp.settings import get_settings


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
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
    - ``doc_index_chunk_count`` — number of indexed Markdown chunks
                           (``collection.count()``), or ``null`` when
                           ``doc_index_state != "ready"``. ``0`` is a valid
                           reading (a completed corpus with zero matching
                           Markdown files) and is distinct from ``null``
                           (index not ready yet / no store) — see UR-20.
    - ``preflight_warnings`` — list of advisory strings computed once at
                           server startup (empty list = all clear, or the
                           preflight never ran — e.g. most unit tests). Never
                           fatal and never affects ``state``/``doc_index_state``
                           — see UR-21.
    - ``server_version``  — installed ``rust-lsp-mcp`` package version
                           (``importlib.metadata.version``, computed fresh per
                           call), or ``null`` if the distribution metadata is
                           not resolvable (``PackageNotFoundError``) — see KI-12.
    - ``multilspy_version`` — installed ``multilspy`` package version, same
                           mechanism and null-degradation as ``server_version``.
    - ``rust_analyzer_version`` — the analyzer binary's ``--version`` output
                           (``rust-analyzer <semver> (<sha> <date>)``),
                           captured **once** at analyzer-manager start and
                           cached for the process lifetime (never re-run per
                           ``status`` call). ``null`` before a manager exists,
                           or if the binary was missing/failed/timed out at
                           capture time.

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

    # Read all in-process state up front, BEFORE the suspension points below,
    # so the analyzer/doc-store fields form one point-in-time snapshot that a
    # concurrent restart()/refresh() cannot tear across the awaits.
    # (Exception: doc_index_chunk_count is read in a thread hop below, so
    # during an active rebuild it can momentarily disagree with the
    # doc_index_state snapshotted here — a stale count, never corruption.)
    state: str = mgr.state if mgr is not None else "indexing"
    analyzer_error: str | None = mgr.error_message if mgr is not None else None
    indexed_commit: str | None = mgr.indexed_commit if mgr is not None else None
    repo_root: str = mgr.repository_root if mgr is not None else get_settings().project_root
    doc_state, doc_err = doc_store_state()
    preflight_warnings = get_preflight_warnings()
    rust_analyzer_version: str | None = mgr.rust_analyzer_version if mgr is not None else None
    # KI-12: package versions are cheap to resolve (importlib.metadata reads
    # installed distribution metadata, no I/O beyond that) — compute fresh
    # per call rather than caching, unlike rust_analyzer_version's subprocess.
    server_version = _package_version("rust-lsp-mcp")
    multilspy_version = _package_version("multilspy")

    # DS-19: the pinned MCP SDK runs non-async tools INLINE on the event loop
    # (no thread offload), so synchronous I/O here would block every other
    # in-flight request on the hottest polling path. Offload both the git
    # fork+exec and the doc-store's collection.count() (a ChromaDB/SQLite
    # query) to worker threads; each helper stays synchronous (it's the
    # worker-thread body — mirrors AnalyzerManager._capture_head_commit's
    # identical pattern).
    current_commit: str | None = await asyncio.to_thread(_git_head, repo_root)
    doc_chunk_count: int | None = await asyncio.to_thread(doc_index_chunk_count)

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
        doc_index_chunk_count=doc_chunk_count,
        preflight_warnings=preflight_warnings,
        server_version=server_version,
        multilspy_version=multilspy_version,
        rust_analyzer_version=rust_analyzer_version,
    )


def _package_version(name: str) -> str | None:
    """Return the installed version of the *name* distribution, or ``None``.

    Wraps ``importlib.metadata.version`` (KI-12); degrades to ``None`` on
    ``PackageNotFoundError`` (e.g. an editable/unusual install where the
    distribution metadata isn't resolvable). Never raises.
    """
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


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
