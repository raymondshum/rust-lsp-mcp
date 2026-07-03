"""rust-lsp CLI -- argparse surface + dispatch for the rust-lsp-mcp daemon.

Import-light by design (D5, docs/planning/cli-frontend.md): only stdlib
(``argparse``/``json``/``os``/``sys``) at MODULE level. The ``mcp`` client
(``rust_lsp_cli.client``) is imported lazily, inside ``main()``, only once we
know we are about to make a real network call -- never for ``--help`` or a
parity test's introspection of ``COMMANDS``. This module (and every other
module in this package) must NEVER import ``rust_lsp_mcp`` -- that package's
``__init__`` pulls in the full server (chromadb, a second Chroma client) --
enforced by a fast test asserting ``"rust_lsp_mcp" not in sys.modules`` after
driving this CLI.

Exit codes (D8, docs/planning/cli-frontend.md):
    0 - envelope status ``ok`` or ``not_found`` (an answer, not a failure --
        matches the skill's "empty is not an error" doctrine).
    1 - envelope status ``error``.
    2 - envelope status ``not_ready``, including a ``--wait`` window that
        expired while the daemon was reachable but never became ready.
    3 - the daemon was never reachable at all (connection refused, handshake
        failure, timeout), including a ``--wait`` window that expired
        without ever connecting.

Deliberate choice -- argparse usage errors: argparse's own default exit code
for a parse failure (bad/missing arguments, unknown subcommand) is 2, which
collides numerically with "not_ready" above. This is accepted rather than
overridden: both read the same way to a caller ("something you must fix
before retrying" -- D8's actionability framing), a usage error is never
ambiguous with a live envelope (it happens before any network call, with its
own distinct stderr text), and overriding argparse's exit behaviour would
mean hand-rolling error formatting for every malformed invocation. Flagged
per the phase-2 handoff for explicit sign-off.

``--wait SECS`` is a global option and, per argparse's subparser mechanics,
must be given BEFORE the subcommand name (``rust-lsp --wait 180 status``, not
``rust-lsp status --wait 180``) -- documented in its own ``--help`` text.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_READY = 2
EXIT_UNREACHABLE = 3

DEFAULT_HTTP_PORT = 8000

# ---------------------------------------------------------------------------
# Daemon URL resolution (D9): RLM_CLI_URL override, else derived from the
# same RLM_HTTP_PORT the daemon itself reads -- read os.environ directly
# (not pydantic-settings) to keep this package import-light (D5).
# ---------------------------------------------------------------------------


def default_base_url() -> str:
    """Return the daemon's ``/mcp`` endpoint URL.

    ``RLM_CLI_URL`` wins outright if set (explicit override, used verbatim).
    Otherwise: ``http://127.0.0.1:${RLM_HTTP_PORT:-8000}/mcp`` -- the CLI and
    daemon share the container environment, so the port cannot silently
    drift between them.
    """
    override = os.environ.get("RLM_CLI_URL")
    if override:
        return override
    port = os.environ.get("RLM_HTTP_PORT", str(DEFAULT_HTTP_PORT))
    return f"http://127.0.0.1:{port}/mcp"


# ---------------------------------------------------------------------------
# Command table (D6/D7): kebab-case CLI command -> (MCP tool name,
# argparse.Namespace -> tool-arguments mapper). This table is what the
# server-side parity test (tests/test_cli_parity.py) compares against
# ``mcp.list_tools()``, with documented exclusions ``probe`` and
# ``analyzer_status`` (internal/superseded -- see D7). ``version`` is
# intentionally absent: it is not a passthrough tool call (D10), handled
# specially in ``main()``.
# ---------------------------------------------------------------------------


def _args_none(_ns: argparse.Namespace) -> dict[str, Any]:
    return {}


def _args_file(ns: argparse.Namespace) -> dict[str, Any]:
    return {"file": ns.file}


def _args_position(ns: argparse.Namespace) -> dict[str, Any]:
    return {"file": ns.file, "line": ns.line, "character": ns.character}


def _args_find_symbol(ns: argparse.Namespace) -> dict[str, Any]:
    return {"name": ns.name}


def _args_find_references(ns: argparse.Namespace) -> dict[str, Any]:
    return {
        "file": ns.file,
        "line": ns.line,
        "character": ns.character,
        "include_declaration": ns.include_declaration,
        "include_source": ns.include_source,
    }


def _args_search_docs(ns: argparse.Namespace) -> dict[str, Any]:
    return {"query": ns.query, "limit": ns.limit}


CommandSpec = tuple[str, Callable[[argparse.Namespace], dict[str, Any]]]

COMMANDS: dict[str, CommandSpec] = {
    "find-symbol": ("find_symbol", _args_find_symbol),
    "goto-definition": ("goto_definition", _args_position),
    "find-references": ("find_references", _args_find_references),
    "hover": ("hover", _args_position),
    "document-symbols": ("document_symbols", _args_file),
    "search-docs": ("search_docs", _args_search_docs),
    "status": ("status", _args_none),
    "refresh": ("refresh", _args_none),
    "validate-file-path": ("validate_file_path", _args_file),
}

_POSITION_HELP = "1-indexed (the first line/character is 1 -- NOT 0-indexed like raw LSP)."


def _add_position_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("file", help="Workspace-relative path, e.g. src/main.rs.")
    sp.add_argument("line", type=int, help=f"Line number. {_POSITION_HELP}")
    sp.add_argument("character", type=int, help=f"Character offset. {_POSITION_HELP}")


def _finite_float(value: str) -> float:
    """argparse type for --wait: a finite float.

    `nan`/`inf` pass `type=float` but make the poll deadline arithmetic
    never terminate (monotonic() >= now + nan is always False) — a hang is
    the one failure mode worse than a crash (C2 adversarial note N1).
    """
    import math

    parsed = float(value)
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError(f"--wait must be finite, got {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the static argparse parser for every subcommand (D6/D7)."""
    parser = argparse.ArgumentParser(
        prog="rust-lsp",
        description=(
            "Thin CLI client for a warm rust-lsp-mcp daemon -- exposes the same "
            "read-only Rust navigation tools the MCP server does, over the "
            "daemon's streamable-HTTP endpoint. Connects to $RLM_CLI_URL if set, "
            "else http://127.0.0.1:${RLM_HTTP_PORT:-8000}/mcp. Positions "
            f"(LINE/CHARACTER) are {_POSITION_HELP} Prints the tool's JSON "
            "envelope to stdout; diagnostics go to stderr."
        ),
    )
    parser.add_argument(
        "--wait",
        type=_finite_float,
        metavar="SECS",
        default=None,
        help=(
            "Before running the command, poll `status` every 2s until the "
            "daemon reports state=ready (or SECS elapses). Rides through "
            "daemon boot (connection-refused -> not_ready -> ready). Must be "
            "given BEFORE the subcommand, e.g. `rust-lsp --wait 180 status`."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = subparsers.add_parser(
        "find-symbol", help="Resolve a symbol name (or prefix) to its workspace position(s)."
    )
    p.add_argument("name", help="Symbol name or prefix; fuzzy-matched by rust-analyzer.")

    p = subparsers.add_parser(
        "goto-definition", help="Jump to the definition of the symbol at FILE:LINE:CHARACTER."
    )
    _add_position_args(p)

    p = subparsers.add_parser(
        "find-references", help="Find all uses of the symbol at FILE:LINE:CHARACTER."
    )
    _add_position_args(p)
    p.add_argument(
        "--include-declaration",
        action="store_true",
        help="Also include the declaration site (merged from goto-definition).",
    )
    p.add_argument(
        "--include-source",
        action="store_true",
        help="Attach the trimmed source line text to each reference.",
    )

    p = subparsers.add_parser(
        "hover", help="Show the type signature and docs at FILE:LINE:CHARACTER."
    )
    _add_position_args(p)

    p = subparsers.add_parser(
        "document-symbols", help="List all symbols declared in FILE (flat outline)."
    )
    p.add_argument("file", help="Workspace-relative path, e.g. src/main.rs.")

    p = subparsers.add_parser(
        "search-docs", help="Semantic search over the project's indexed Markdown docs."
    )
    p.add_argument("query", help="Natural-language search query.")
    p.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max results to return (server clamps to [1, 50]; default 5).",
    )

    subparsers.add_parser(
        "status", help="Report analyzer/doc-index readiness, staleness, and version info."
    )

    subparsers.add_parser(
        "refresh",
        help=(
            "Tear down and re-index the shared analyzer. WARNING: this affects "
            "every caller of the daemon -- every navigation tool returns "
            "not_ready until re-indexing completes."
        ),
    )

    p = subparsers.add_parser(
        "validate-file-path", help="Check whether FILE exists under the workspace root."
    )
    p.add_argument("file", help="Workspace-relative path to probe, e.g. src/main.rs.")

    subparsers.add_parser(
        "version",
        help=(
            "Print the CLI's version and, if the daemon is reachable, its "
            "component versions (server/multilspy/rust-analyzer). Always "
            "exits 0, even when the daemon is down."
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _unreachable_hint(base_url: str) -> str:
    return (
        f"rust-lsp: daemon not reachable at {base_url}.\n"
        "Start it, e.g.:\n"
        "  RLM_TRANSPORT=streamable-http RLM_HTTP_PORT=8000 uv run rust-lsp-mcp\n"
        "or:\n"
        "  docker compose up -d"
    )


def _exit_code_for_envelope(envelope: dict[str, Any]) -> int:
    status = envelope.get("status")
    if status in ("ok", "not_found"):
        return EXIT_OK
    if status == "not_ready":
        return EXIT_NOT_READY
    # "error" and any unrecognized/malformed status (see
    # client._parse_envelope's synthesized fallback) map to the generic
    # error exit code -- both mean "the daemon answered, but not with an
    # actionable result".
    return EXIT_ERROR


def _client_version() -> str | None:
    import importlib.metadata

    try:
        return importlib.metadata.version("rust-lsp-mcp")
    except importlib.metadata.PackageNotFoundError:
        return None


def _run_version(base_url: str, wait_secs: float | None) -> int:
    """``version`` (D10): always prints the client version and exits 0.

    Best-effort daemon lookup: if ``--wait`` was given, ride the readiness
    window first (ignoring its own ready/not-ready verdict -- version's exit
    code is unconditionally 0); either way, attempt one ``status`` call to
    surface the daemon's component versions, degrading to nulls plus a
    stderr note if the daemon never answers.
    """
    from rust_lsp_cli import client

    if wait_secs is not None:
        client.wait_for_ready(base_url, wait_secs)

    envelope, exc = client.call_tool_sync(base_url, "status", {})
    daemon_reachable = exc is None and envelope is not None and envelope.get("status") == "ok"

    if not daemon_reachable:
        print(
            f"rust-lsp: daemon not reachable at {base_url}; daemon version fields are null.",
            file=sys.stderr,
        )

    result = {
        "client_version": _client_version(),
        "server_version": envelope.get("server_version") if daemon_reachable else None,
        "multilspy_version": envelope.get("multilspy_version") if daemon_reachable else None,
        "rust_analyzer_version": envelope.get("rust_analyzer_version")
        if daemon_reachable
        else None,
    }
    print(json.dumps(result, indent=2))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Parse argv, dispatch to a tool call, print the envelope, return an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    base_url = default_base_url()

    if args.command == "version":
        return _run_version(base_url, args.wait)

    tool_name, build_args = COMMANDS[args.command]
    arguments = build_args(args)

    from rust_lsp_cli import client

    if args.wait is not None:
        ready, ever_connected = client.wait_for_ready(base_url, args.wait)
        if not ready:
            if not ever_connected:
                print(_unreachable_hint(base_url), file=sys.stderr)
                return EXIT_UNREACHABLE
            print(
                f"rust-lsp: daemon at {base_url} was reachable but never became "
                f"ready within {args.wait:g}s.",
                file=sys.stderr,
            )
            return EXIT_NOT_READY

    envelope, exc = client.call_tool_sync(base_url, tool_name, arguments)
    if exc is not None:
        print(_unreachable_hint(base_url), file=sys.stderr)
        print(f"rust-lsp: underlying error: {exc}", file=sys.stderr)
        return EXIT_UNREACHABLE

    assert envelope is not None  # call_tool_sync guarantees envelope xor exc
    print(json.dumps(envelope, indent=2))
    return _exit_code_for_envelope(envelope)
