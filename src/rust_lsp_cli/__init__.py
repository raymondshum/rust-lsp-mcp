"""rust-lsp -- thin CLI client for the rust-lsp-mcp daemon.

See docs/planning/cli-frontend.md (D5-D10) and
docs/handoff/cli-phase-2-cli.md for the design this package implements.

Import-light by construction (D5): this package and its submodules never
import ``rust_lsp_mcp`` -- the server's top-level package, whose ``__init__``
pulls in chromadb and a second Chroma client -- see ``rust_lsp_cli.cli``'s
module docstring for the full rationale. Console script: ``rust-lsp`` (wired
via ``[project.scripts]`` in pyproject.toml).
"""

from rust_lsp_cli.cli import main

__all__ = ["main"]
