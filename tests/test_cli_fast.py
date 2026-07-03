"""Fast tests for Phase C2 -- `rust-lsp` CLI client (docs/handoff/cli-phase-2-cli.md).

No live daemon, no real network -- runs in CI as part of ``pytest -m "not
integration"``. The MCP transport is mocked by monkeypatching
``rust_lsp_cli.client``'s public functions (``call_tool_sync``,
``wait_for_ready``); ``rust_lsp_cli.cli`` looks them up via
``from rust_lsp_cli import client; client.<name>(...)`` at call time, so
patching the attribute on the (already-imported, shared) module object is
observed by ``cli.main()`` regardless of which module did the importing.

Covers:
    - Exit-code mapping (D8): ok/not_found -> 0, error -> 1, not_ready -> 2,
      transport failure -> 3 (with a stderr hint), argparse usage errors -> 2
      (documented collision, see ``cli.py``'s module docstring).
    - ``--wait`` (D9): ``client.wait_for_ready`` boot-sequence retry behaviour
      in isolation (fake clock, no real sleeping) plus ``cli.main()``'s
      wiring of its three outcomes (ready -> proceed, not-ready-timeout ->
      exit 2, never-connected-timeout -> exit 3).
    - ``version`` (D10): daemon-down degrades to null fields + exit 0;
      daemon-up surfaces the status envelope's version fields.
    - Stdout purity: stdout is exactly ``json.dumps(envelope, indent=2)`` (or
      the version payload) followed by a newline -- nothing else, ever;
      every diagnostic goes to stderr.
    - Command-table argument mapping: each CLI subcommand builds the tool
      arguments dict the server tool actually expects (positions as ints,
      optional flags defaulting off, RLM_CLI_URL/RLM_HTTP_PORT resolution).
    - Import-light guard (D5): importing/running the CLI parser never pulls
      ``rust_lsp_mcp`` into ``sys.modules`` -- checked in a FRESH subprocess,
      since this suite's own ``tests/conftest.py`` already imports
      ``rust_lsp_mcp`` for unrelated fixtures, which would make an in-process
      check meaningless.

What this file does NOT cover: the parity test (D6, ``tests/test_cli_parity.py``
-- it imports the server in-process, so it is kept in its own file to keep
that server import out of this otherwise server-free suite) and the live
daemon sweep (``tests/test_cli_integration.py``, marker ``integration``).
"""

import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

from rust_lsp_cli import cli
from rust_lsp_cli import client as client_mod

# Shared subprocess-code snippet for the import-light guard tests below:
# fails loudly (AssertionError) if the server package ever leaked into
# sys.modules, otherwise prints "OK".
_ASSERT_NO_SERVER_LEAK = (
    "leaked = [m for m in sys.modules if m == 'rust_lsp_mcp' or m.startswith('rust_lsp_mcp.')]\n"
    "assert not leaked, leaked\n"
    "print('OK')\n"
)

# ---------------------------------------------------------------------------
# Exit-code mapping (D8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("envelope", "expected_exit"),
    [
        ({"status": "ok", "state": "ready"}, 0),
        ({"status": "not_found", "message": "nope"}, 0),
        ({"status": "error", "message": "boom", "recovery": "unknown"}, 1),
        ({"status": "not_ready", "message": "indexing", "recovery": "poll_status"}, 2),
    ],
)
def test_exit_code_mapping(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    envelope: dict[str, Any],
    expected_exit: int,
) -> None:
    monkeypatch.setattr(client_mod, "call_tool_sync", lambda *a, **k: (envelope, None))
    code = cli.main(["status"])
    assert code == expected_exit
    captured = capsys.readouterr()
    assert json.loads(captured.out) == envelope
    assert captured.err == ""


def test_transport_failure_exits_3_with_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        client_mod, "call_tool_sync", lambda *a, **k: (None, ConnectionRefusedError("refused"))
    )
    code = cli.main(["status"])
    assert code == 3
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing on stdout when there is no envelope to print
    assert "not reachable" in captured.err
    assert "http://127.0.0.1:8000/mcp" in captured.err


def test_missing_subcommand_exits_2() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])
    assert exc_info.value.code == 2


def test_unknown_subcommand_exits_2() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["bogus-command"])
    assert exc_info.value.code == 2


def test_missing_positional_arg_exits_2() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["find-symbol"])
    assert exc_info.value.code == 2


def test_help_exits_0() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# --wait (D9): client.wait_for_ready in isolation, with a fake clock -- no
# real sleeping, fully deterministic.
# ---------------------------------------------------------------------------


def _fake_clock() -> tuple[list[float], Callable[[], float], Callable[[float], None]]:
    """Return (ticks, monotonic_fn, sleep_fn) sharing one mutable clock."""
    clock = [0.0]

    def monotonic() -> float:
        return clock[0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    return clock, monotonic, sleep


def test_wait_for_ready_rides_through_boot_sequence() -> None:
    """Connection-refused -> connection-refused -> not_ready -> ready must
    all be retried within the window, matching real daemon-boot behaviour."""
    responses = [
        (None, ConnectionRefusedError("refused")),
        (None, ConnectionRefusedError("refused")),
        ({"status": "ok", "state": "indexing"}, None),
        ({"status": "ok", "state": "ready"}, None),
    ]
    calls = {"n": 0}

    def fake_check(_url: str) -> tuple[dict[str, Any] | None, Exception | None]:
        result = responses[calls["n"]]
        calls["n"] += 1
        return result

    _clock, monotonic, sleep = _fake_clock()
    ready, ever_connected = client_mod.wait_for_ready(
        "http://x/mcp",
        timeout_seconds=30,
        status_check=fake_check,
        sleep=sleep,
        monotonic=monotonic,
    )
    assert ready is True
    assert ever_connected is True
    assert calls["n"] == 4


def test_wait_for_ready_timeout_never_connected() -> None:
    def fake_check(_url: str) -> tuple[dict[str, Any] | None, Exception | None]:
        return (None, ConnectionRefusedError("refused"))

    _clock, monotonic, sleep = _fake_clock()
    ready, ever_connected = client_mod.wait_for_ready(
        "http://x/mcp",
        timeout_seconds=5,
        status_check=fake_check,
        sleep=sleep,
        monotonic=monotonic,
    )
    assert ready is False
    assert ever_connected is False


def test_wait_for_ready_timeout_reachable_but_not_ready() -> None:
    def fake_check(_url: str) -> tuple[dict[str, Any] | None, Exception | None]:
        return ({"status": "ok", "state": "indexing"}, None)

    _clock, monotonic, sleep = _fake_clock()
    ready, ever_connected = client_mod.wait_for_ready(
        "http://x/mcp",
        timeout_seconds=5,
        status_check=fake_check,
        sleep=sleep,
        monotonic=monotonic,
    )
    assert ready is False
    assert ever_connected is True


# ---------------------------------------------------------------------------
# --wait (D9): cli.main()'s wiring of wait_for_ready's three outcomes.
# ---------------------------------------------------------------------------


def test_wait_flag_never_connected_exits_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(client_mod, "wait_for_ready", lambda *a, **k: (False, False))
    code = cli.main(["--wait", "10", "status"])
    assert code == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not reachable" in captured.err


def test_wait_flag_reachable_but_not_ready_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(client_mod, "wait_for_ready", lambda *a, **k: (False, True))
    code = cli.main(["--wait", "10", "status"])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "never became ready" in captured.err


def test_wait_flag_ready_proceeds_to_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(client_mod, "wait_for_ready", lambda *a, **k: (True, True))
    envelope = {"status": "ok", "state": "ready"}
    monkeypatch.setattr(client_mod, "call_tool_sync", lambda *a, **k: (envelope, None))
    code = cli.main(["--wait", "10", "status"])
    assert code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == envelope


# ---------------------------------------------------------------------------
# version (D10)
# ---------------------------------------------------------------------------


def test_version_daemon_down_exits_0_with_null_daemon_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        client_mod, "call_tool_sync", lambda *a, **k: (None, TimeoutError("timed out"))
    )
    code = cli.main(["version"])
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["client_version"]  # resolvable in this venv, non-null
    assert payload["server_version"] is None
    assert payload["multilspy_version"] is None
    assert payload["rust_analyzer_version"] is None
    assert "not reachable" in captured.err


def test_version_daemon_up_surfaces_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    envelope = {
        "status": "ok",
        "state": "ready",
        "server_version": "0.1.0",
        "multilspy_version": "0.0.15",
        "rust_analyzer_version": "rust-analyzer 1.96.0 (abc 2026-01-01)",
    }
    monkeypatch.setattr(client_mod, "call_tool_sync", lambda *a, **k: (envelope, None))
    code = cli.main(["version"])
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["server_version"] == "0.1.0"
    assert payload["multilspy_version"] == "0.0.15"
    assert payload["rust_analyzer_version"] == envelope["rust_analyzer_version"]
    assert captured.err == ""


# ---------------------------------------------------------------------------
# Stdout purity
# ---------------------------------------------------------------------------


def test_stdout_is_exactly_the_json_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    envelope = {"status": "ok", "symbols": [], "total": 0, "truncated": False}
    monkeypatch.setattr(client_mod, "call_tool_sync", lambda *a, **k: (envelope, None))
    code = cli.main(["document-symbols", "src/main.rs"])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == json.dumps(envelope, indent=2) + "\n"
    assert captured.err == ""


# ---------------------------------------------------------------------------
# Command-table argument mapping (D6/D7)
# ---------------------------------------------------------------------------


def _spy_call_tool_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake(
        url: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> tuple[dict, None]:
        captured["url"] = url
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return ({"status": "ok"}, None)

    monkeypatch.setattr(client_mod, "call_tool_sync", fake)
    return captured


def test_find_symbol_maps_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(["find-symbol", "SearcherBuilder"])
    assert captured["tool_name"] == "find_symbol"
    assert captured["arguments"] == {"name": "SearcherBuilder"}


def test_goto_definition_maps_position_as_ints(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(["goto-definition", "src/lib.rs", "10", "5"])
    assert captured["tool_name"] == "goto_definition"
    args = captured["arguments"]
    assert isinstance(args, dict)
    assert args == {"file": "src/lib.rs", "line": 10, "character": 5}
    assert isinstance(args["line"], int)
    assert isinstance(args["character"], int)


def test_hover_maps_position(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(["hover", "src/lib.rs", "3", "7"])
    assert captured["tool_name"] == "hover"
    assert captured["arguments"] == {"file": "src/lib.rs", "line": 3, "character": 7}


def test_find_references_defaults_flags_off(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(["find-references", "src/lib.rs", "10", "5"])
    assert captured["tool_name"] == "find_references"
    assert captured["arguments"] == {
        "file": "src/lib.rs",
        "line": 10,
        "character": 5,
        "include_declaration": False,
        "include_source": False,
    }


def test_find_references_maps_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(
        [
            "find-references",
            "src/lib.rs",
            "10",
            "5",
            "--include-declaration",
            "--include-source",
        ]
    )
    assert captured["arguments"] == {
        "file": "src/lib.rs",
        "line": 10,
        "character": 5,
        "include_declaration": True,
        "include_source": True,
    }


def test_document_symbols_maps_file(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(["document-symbols", "src/main.rs"])
    assert captured["tool_name"] == "document_symbols"
    assert captured["arguments"] == {"file": "src/main.rs"}


def test_search_docs_default_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(["search-docs", "how do I configure this"])
    assert captured["tool_name"] == "search_docs"
    assert captured["arguments"] == {"query": "how do I configure this", "limit": 5}


def test_search_docs_custom_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(["search-docs", "query", "--limit", "20"])
    assert captured["arguments"] == {"query": "query", "limit": 20}


def test_validate_file_path_maps_file(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(["validate-file-path", "src/main.rs"])
    assert captured["tool_name"] == "validate_file_path"
    assert captured["arguments"] == {"file": "src/main.rs"}


def test_status_and_refresh_take_no_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _spy_call_tool_sync(monkeypatch)
    cli.main(["status"])
    assert captured["tool_name"] == "status"
    assert captured["arguments"] == {}

    captured2 = _spy_call_tool_sync(monkeypatch)
    cli.main(["refresh"])
    assert captured2["tool_name"] == "refresh"
    assert captured2["arguments"] == {}


# ---------------------------------------------------------------------------
# Daemon URL resolution (D9)
# ---------------------------------------------------------------------------


def test_default_base_url_uses_rlm_cli_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RLM_CLI_URL", "http://example.invalid:1234/mcp")
    assert cli.default_base_url() == "http://example.invalid:1234/mcp"


def test_default_base_url_derives_from_rlm_http_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RLM_CLI_URL", raising=False)
    monkeypatch.setenv("RLM_HTTP_PORT", "9999")
    assert cli.default_base_url() == "http://127.0.0.1:9999/mcp"


def test_default_base_url_falls_back_to_8000(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RLM_CLI_URL", raising=False)
    monkeypatch.delenv("RLM_HTTP_PORT", raising=False)
    assert cli.default_base_url() == "http://127.0.0.1:8000/mcp"


# ---------------------------------------------------------------------------
# Import-light guard (D5) -- must run in a FRESH subprocess: this suite's own
# tests/conftest.py already imports rust_lsp_mcp for unrelated fixtures, so
# an in-process sys.modules check would be checking the wrong process.
# ---------------------------------------------------------------------------


def test_importing_cli_package_never_imports_server_package() -> None:
    code = "import sys\nimport rust_lsp_cli\n" + _ASSERT_NO_SERVER_LEAK
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_running_cli_help_never_imports_server_package() -> None:
    code = (
        "import sys\n"
        "from rust_lsp_cli import main\n"
        "try:\n"
        "    main(['--help'])\n"
        "except SystemExit:\n"
        "    pass\n"
    ) + _ASSERT_NO_SERVER_LEAK
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    # --help itself prints usage text to stdout before the leak-check's "OK"
    # -- only the trailing marker matters here, not stdout's full content.
    assert result.stdout.rstrip().endswith("OK")


def test_running_cli_subcommand_never_imports_server_package() -> None:
    """A real (mocked-transport) dispatch also never imports rust_lsp_mcp --
    belt-and-suspenders alongside the --help-only check above, since a
    subcommand exercises a different code path (client.py's lazy import)."""
    code = (
        "import sys\n"
        "from rust_lsp_cli import cli, client\n"
        "client.call_tool_sync = lambda *a, **k: ({'status': 'ok'}, None)\n"
        "cli.main(['status'])\n"
    ) + _ASSERT_NO_SERVER_LEAK
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    # cli.main(['status']) prints the JSON envelope to stdout before the
    # leak-check's "OK" -- only the trailing marker matters here.
    assert result.stdout.rstrip().endswith("OK")
