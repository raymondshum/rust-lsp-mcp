"""1-indexed ↔ 0-indexed position conversion for rust-lsp-mcp.

This is the **single boundary helper** for all line/character conversions
between the MCP tool surface (1-indexed, human-legible) and the LSP protocol
(0-indexed).

Design:
    - External (MCP) convention: 1-indexed lines and characters.
    - Internal (LSP) convention: 0-indexed lines and characters.
    - Both directions are provided here so that:
        * Phase 2 (find_symbol) can emit 1-indexed positions.
        * Phase 3 (goto_definition, find_references, hover) can accept
          1-indexed positions from the assistant and convert inward.

Only line AND character are converted — the helper does not touch file paths
or any other fields.

Character encoding (KI-5):
    The ``character`` axis is a **Unicode codepoint** offset.  ``AnalyzerManager``
    negotiates ``positionEncoding: utf-32`` with rust-analyzer (see
    ``PatchedRustAnalyzer._get_initialize_params`` in analyzer.py), so the LSP
    offsets are already codepoint counts — this helper only adds/removes the
    1-vs-0 index bias.  No UTF-16 surrogate transcoding is needed.
"""

from typing import NamedTuple


class ExternalPosition(NamedTuple):
    """A 1-indexed (line, character) position as seen by MCP tool callers."""

    line: int
    character: int


class LspPosition(NamedTuple):
    """A 0-indexed (line, character) position as used by the LSP protocol."""

    line: int
    character: int


def lsp_to_external(lsp_line: int, lsp_character: int) -> ExternalPosition:
    """Convert a 0-indexed LSP position to a 1-indexed external position.

    Args:
        lsp_line:      0-indexed line number from an LSP response.
        lsp_character: 0-indexed character offset from an LSP response.

    Returns:
        ExternalPosition with both fields incremented by 1.

    Example::

        lsp_to_external(0, 0)   # → ExternalPosition(line=1, character=1)
        lsp_to_external(4, 12)  # → ExternalPosition(line=5, character=13)
    """
    return ExternalPosition(line=lsp_line + 1, character=lsp_character + 1)


def external_to_lsp(ext_line: int, ext_character: int) -> LspPosition:
    """Convert a 1-indexed external position to a 0-indexed LSP position.

    Args:
        ext_line:      1-indexed line number supplied by an MCP tool caller.
        ext_character: 1-indexed character offset supplied by an MCP tool caller.

    Returns:
        LspPosition with both fields decremented by 1.

    Example::

        external_to_lsp(1, 1)   # → LspPosition(line=0, character=0)
        external_to_lsp(5, 13)  # → LspPosition(line=4, character=12)
    """
    return LspPosition(line=ext_line - 1, character=ext_character - 1)
