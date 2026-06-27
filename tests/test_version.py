"""Unit tests for the static server version helper."""

from rust_lsp_mcp.version import SERVER_VERSION, server_version


def test_server_version_returns_constant() -> None:
    assert server_version() == SERVER_VERSION


def test_server_version_is_nonempty_string() -> None:
    assert isinstance(SERVER_VERSION, str)
    assert SERVER_VERSION
