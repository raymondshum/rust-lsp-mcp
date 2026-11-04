"""Fast tests for the MCP ``serverInfo.version`` the server reports (backlog line 82).

FastMCP 1.12.4 takes no version argument, so without ``_build_mcp`` setting
the low-level server's ``version`` the initialization options fall back to
the ``mcp`` SDK's own version.
"""

from __future__ import annotations

import importlib.metadata
from unittest.mock import patch

import pytest

from rust_lsp_mcp import core as core_mod
from rust_lsp_mcp.core import _build_mcp
from rust_lsp_mcp.settings import Settings


def _settings(transport: str) -> Settings:
    return Settings(transport=transport, http_port=9100, _env_file=None)  # ty: ignore[unknown-argument, invalid-argument-type]


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
def test_server_info_version_names_the_package(transport: str) -> None:
    app = _build_mcp(_settings(transport))
    version = app._mcp_server.create_initialization_options().server_version
    assert version == importlib.metadata.version("rust-lsp-mcp")
    assert version != importlib.metadata.version("mcp")


def test_server_info_version_unknown_without_distribution_metadata() -> None:
    def _missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    with patch.object(core_mod.importlib.metadata, "version", _missing):
        app = _build_mcp(_settings("stdio"))
    assert app._mcp_server.version == "unknown"
