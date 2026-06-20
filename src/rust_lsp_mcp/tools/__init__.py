"""Auto-discovery package for rust-lsp-mcp tools.

Importing this package iterates all submodules and imports each one.
Every submodule that contains ``@mcp.tool()`` decorators registers its tools
with the FastMCP application at import time.

Convention:
    - Each tool lives in its own ``tools/<name>.py`` module.
    - Modules whose name starts with ``_`` are skipped (private helpers).
    - No edits to this file are needed when adding a new tool — just drop a new
      ``tools/<name>.py`` that calls ``@mcp.tool()`` at module level.
"""

import importlib
import pkgutil


def _register_all() -> None:
    for module_info in pkgutil.iter_modules(__path__):
        name = module_info.name
        if name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{name}")


_register_all()
