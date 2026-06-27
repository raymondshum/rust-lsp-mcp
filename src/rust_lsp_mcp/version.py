"""Static server version metadata for rust-lsp-mcp.

A standalone, side-effect-free helper. Nothing else in the package imports it,
so exposing it cannot regress the existing tool surface.
"""

SERVER_VERSION = "0.1.0"


def server_version() -> str:
    """Return the static rust-lsp-mcp server version string."""
    return SERVER_VERSION
