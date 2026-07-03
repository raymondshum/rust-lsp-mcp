"""Parity test (D6, docs/planning/cli-frontend.md): the CLI's command table
must expose exactly the server's read-only tool surface, with two documented
exclusions (D7).

This file imports ``rust_lsp_mcp`` in-process -- it runs in the SERVER's test
suite, not the CLI's. ``rust_lsp_cli`` itself must NEVER import
``rust_lsp_mcp`` (see ``rust_lsp_cli.cli``'s module docstring and the
import-light guard in ``tests/test_cli_fast.py``); importing both packages
from a THIRD module (this test file) does not violate that rule.

Runtime ``list_tools()`` generation was rejected (D6) because it would make
``--help`` require a live daemon; this test gives the same drift protection
in CI instead -- it fails the moment a tool is added, renamed, or removed on
the server without a matching change to ``rust_lsp_cli.cli.COMMANDS``.
"""

import asyncio

import rust_lsp_mcp.server as server_mod
from rust_lsp_cli.cli import COMMANDS

# D7: tools deliberately NOT exposed as CLI commands.
#   - probe: internal gate demo, no semantic value beyond testing require_ready.
#   - analyzer_status: superseded by `status`'s full report (same readiness
#     field plus indexed/current commit, staleness, doc-index, versions).
EXCLUDED_TOOLS = {"probe", "analyzer_status"}


def test_cli_command_table_matches_server_tools() -> None:
    tools = asyncio.run(server_mod.mcp.list_tools())
    server_tool_names = {t.name for t in tools}
    cli_tool_names = {tool_name for tool_name, _ in COMMANDS.values()}

    expected = server_tool_names - EXCLUDED_TOOLS
    assert cli_tool_names == expected, (
        "CLI command table (rust_lsp_cli.cli.COMMANDS) drifted from the "
        "server's tool surface (rust_lsp_mcp.server.mcp.list_tools()).\n"
        f"On the server but missing a CLI command: {sorted(expected - cli_tool_names)}\n"
        f"In the CLI but not a real server tool (stale/renamed): "
        f"{sorted(cli_tool_names - expected)}"
    )

    # Guards the exclusion list itself against going stale (e.g. a tool
    # renamed away from "probe" without updating EXCLUDED_TOOLS above, which
    # would otherwise silently widen the parity check's blind spot).
    assert server_tool_names >= EXCLUDED_TOOLS, (
        f"EXCLUDED_TOOLS names a tool that no longer exists on the server: "
        f"{sorted(EXCLUDED_TOOLS - server_tool_names)}"
    )


def test_cli_has_no_duplicate_tool_mappings() -> None:
    """Every CLI command must map to a DISTINCT tool -- a copy-paste error
    mapping two commands to the same tool name would slip past the set-based
    parity check above."""
    tool_names = [tool_name for tool_name, _ in COMMANDS.values()]
    assert len(tool_names) == len(set(tool_names)), tool_names
