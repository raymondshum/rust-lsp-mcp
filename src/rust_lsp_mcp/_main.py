"""Entry point for the rust-lsp-mcp MCP server.

Configures logging by transport, then delegates to ``server.main()``.
Both launch paths call this module:
    - ``uv run rust-lsp-mcp``   (console script via [project.scripts])
    - ``python -m rust_lsp_mcp``  (via __main__.py)

Logging by transport (stdio streams carry MCP messages only):
    - ``stdio``: every log record goes to stderr, because stdout carries the
      MCP JSON-RPC stream to the client.
    - ``streamable-http``: every log record goes to stdout. The daemon speaks
      MCP over HTTP, so its stdout is free, and ``docker logs`` shows it.

``force=True`` replaces any handler installed before ``main()`` runs, such as
the one the ``mcp`` SDK's ``FastMCP`` constructor adds at import.
"""

import logging
import sys

from rust_lsp_mcp.server import main as _server_main
from rust_lsp_mcp.settings import get_settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(transport: str) -> None:
    """Send the root logger's records to stderr for stdio, to stdout for the HTTP daemon."""
    stream = sys.stdout if transport == "streamable-http" else sys.stderr
    logging.basicConfig(stream=stream, level=logging.INFO, format=LOG_FORMAT, force=True)


def main() -> None:
    """Configure logging for the configured transport, then run the server."""
    configure_logging(get_settings().transport)
    _server_main()


__all__ = ["configure_logging", "main"]
