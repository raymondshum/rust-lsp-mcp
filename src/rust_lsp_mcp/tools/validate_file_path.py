"""validate_file_path tool — check a workspace path exists and report its size.

Registered with the FastMCP app at import time via ``@mcp.tool()``.

This tool is UNGATED: it touches only the filesystem and needs no live
rust-analyzer index, so it never returns ``not_ready``.
"""

import pathlib
from typing import Any

from rust_lsp_mcp.core import get_manager, mcp, validate_workspace_file
from rust_lsp_mcp.envelope import error, ok
from rust_lsp_mcp.settings import get_settings


@mcp.tool()
def validate_file_path(file: str) -> dict[str, Any]:
    """Check whether a workspace path exists and report its absolute path and size.

    A lightweight, analyzer-free filesystem probe.  Resolves ``file`` against the
    workspace root the same way the navigation tools do (manager's
    ``repository_root`` when the analyzer is up, else
    ``get_settings().project_root``), then reports whether it exists on disk.

    Args:
        file: Workspace-relative path to probe (e.g. ``"src/main.rs"``).

    Returns a ``{status, ...}`` envelope:

    - ``ok`` — the probe ran.  Fields:

          {
            "exists":        bool,         # True if the resolved path exists
            "absolute_path": str,          # the resolved absolute path probed
            "size_bytes":    int | null,   # file size when it is a regular file,
                                           # else null (missing path, directory,
                                           # symlink-to-nothing, etc.)
          }

      ``exists: false`` is a valid answer, not an error.

    - ``error`` — a genuine failure: the workspace root is unconfigured, or
      ``file`` fails the workspace containment rule.

    Containment rule: identical to the position tools
    (``core.validate_workspace_file``) so this tool's verdict never
    contradicts theirs — ``file`` must be a workspace-relative path; empty
    strings, NUL-containing paths, absolute paths (even ones pointing inside
    the workspace), and ``..``-escaping paths are all rejected.  The check is
    purely lexical (``os.path.normpath`` — no filesystem access, no symlink
    resolution), and the *normalized* form is what gets probed, so a
    symlink+``..`` combination cannot resolve outside the workspace.
    """
    mgr = get_manager()
    repo_root = mgr.repository_root if mgr is not None else get_settings().project_root
    if not repo_root:
        return error("Workspace root is not configured.")

    # Shared containment rule (core helper) — reject absolute/escaping paths
    # and probe the normalized form so symlink+".." cannot escape the root.
    file, guard = validate_workspace_file(file)
    if guard is not None:
        return guard

    abs_path = pathlib.Path(repo_root) / file

    size_bytes: int | None = abs_path.stat().st_size if abs_path.is_file() else None

    return ok(
        exists=abs_path.exists(),
        absolute_path=str(abs_path),
        size_bytes=size_bytes,
    )
