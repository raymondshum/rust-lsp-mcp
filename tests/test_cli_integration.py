"""Integration tests for Phase C2 -- `rust-lsp` CLI client (real daemon, real
analyzer). Marker: ``integration`` (registered in pyproject.toml).

Run locally only, via the podman harness: ``uv run pytest -m integration``.
Never runs in CI, and is not run by the build agent -- the QA gate runs this.

Unlike Phase C1's tests (which drive the daemon in-process via
``tests/daemon_fixture.py``'s raw ``call_tool`` driver, because they need to
observe process-level Python state -- singleton identity, session-manager
internals), these tests exercise the CLI the way a real caller would: a
SEPARATE PROCESS, invoking the actually-installed ``rust-lsp`` console
script, pointed at the fixture's daemon via ``RLM_CLI_URL``. This is the only
way to prove the packaging (pyproject.toml's `[project.scripts]` entry, the
two-package wheel) and the CLI's own process boundary (argv parsing, stdout/
stderr separation, real exit codes) all work end-to-end -- none of that is
observable by importing ``rust_lsp_cli`` in-process.

Reuses the daemon_app fixture (session-scoped -- the daemon pays the cold
ripgrep-index cost once for this whole file, same as
tests/test_daemon_http_integration.py) via tests/daemon_fixture.py.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import pytest

from tests.daemon_fixture import DaemonHandle, call_tool, wait_until_ready


def _rust_lsp_executable() -> str:
    """Locate the installed `rust-lsp` console script.

    Prefers PATH (the normal `uv run pytest` / activated-venv case); falls
    back to the sibling of the running interpreter (a venv's bin/ dir always
    holds python and its console scripts side by side) so this still works
    if PATH was stripped for some reason. Skips (not fails) if genuinely
    absent -- that means `uv sync` was not re-run after this phase's
    packaging change, which is an environment problem, not a CLI bug.
    """
    exe = shutil.which("rust-lsp")
    if exe:
        return exe
    candidate = Path(sys.executable).parent / "rust-lsp"
    if candidate.exists():
        return str(candidate)
    pytest.skip("rust-lsp console script not found on PATH -- run `uv sync`")


def _run_cli(base_url: str, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    """Invoke the installed `rust-lsp` script as a real subprocess."""
    exe = _rust_lsp_executable()
    # Inherit the ambient environment (PATH, HOME, ...) and only set/override
    # RLM_CLI_URL -- matches real usage (an operator exports RLM_CLI_URL in
    # their existing shell) rather than scrubbing everything the subprocess
    # might otherwise need.
    env = {**os.environ, "RLM_CLI_URL": base_url}
    return subprocess.run(
        [exe, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _discover_position(base_url: str, symbol_name: str) -> dict[str, Any]:
    """Resolve a real (file, line, character) via the raw driver -- the CLI
    sweep below then re-uses that position through the actual CLI subprocess,
    matching the discover-then-act pattern the rest of the suite uses (e.g.
    tests/test_phase34_integration.py) rather than hardcoding brittle
    line/character numbers against the ripgrep fixture."""
    result = anyio.run(call_tool, base_url, "find_symbol", {"name": symbol_name})
    assert result["status"] == "ok", f"find_symbol({symbol_name!r}) failed: {result!r}"
    return result["results"][0]


@pytest.mark.integration
def test_cli_full_subcommand_sweep(daemon_app: DaemonHandle) -> None:
    """One warm daemon, the full CLI subcommand surface, via real subprocess
    invocations of the installed `rust-lsp` script -- asserts exit codes and
    parseable JSON per docs/handoff/cli-phase-2-cli.md's DoD."""
    anyio.run(wait_until_ready, daemon_app.base_url)
    base_url = daemon_app.base_url

    # status (ready)
    proc = _run_cli(base_url, "status")
    assert proc.returncode == 0, proc.stderr
    status_payload = json.loads(proc.stdout)
    assert status_payload["status"] == "ok"
    assert status_payload["state"] == "ready"

    # find-symbol hit
    proc = _run_cli(base_url, "find-symbol", "main")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["results"], "expected at least one match for 'main' in the ripgrep fixture"

    # find-symbol miss -- not_found is an answer, not a failure (D8)
    proc = _run_cli(base_url, "find-symbol", "definitely_not_a_real_symbol_xyz123")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "not_found"

    # Discover a real position to drive goto-definition/hover/document-symbols/find-references.
    pivot = _discover_position(base_url, "main")
    file_arg, line_arg, char_arg = pivot["file"], str(pivot["line"]), str(pivot["character"])

    proc = _run_cli(base_url, "goto-definition", file_arg, line_arg, char_arg)
    assert proc.returncode in (0, 1), (
        proc.stderr
    )  # not_found(0)/ok(0)/error(1) are all "the CLI ran"
    payload = json.loads(proc.stdout)
    assert payload["status"] in ("ok", "not_found")

    proc = _run_cli(base_url, "hover", file_arg, line_arg, char_arg)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] in ("ok", "not_found")

    proc = _run_cli(base_url, "document-symbols", file_arg)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["symbols"], f"expected a non-empty outline for {file_arg}"

    proc = _run_cli(base_url, "find-references", file_arg, line_arg, char_arg)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] in ("ok", "not_found")

    # search-docs
    proc = _run_cli(base_url, "search-docs", "configuration")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] in ("ok", "not_found")

    # validate-file-path
    proc = _run_cli(base_url, "validate-file-path", file_arg)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["exists"] is True

    # version -- daemon reachable, every field must be non-null (D10)
    proc = _run_cli(base_url, "version")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["client_version"]
    assert payload["server_version"]
    assert payload["multilspy_version"]
    assert payload["rust_analyzer_version"]

    # refresh -- ok, kicks off a re-index in the background. Run LAST in this
    # sweep (mirrors test_daemon_http_integration.py's ordering rationale):
    # every call above needs a stable, already-warm "ready" analyzer, and
    # refresh's blast radius (KI-9) would otherwise make the rest of this
    # sweep race an in-flight re-index.
    proc = _run_cli(base_url, "refresh")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"

    # Let the daemon settle back to ready so later tests in this file (or a
    # shared-daemon podman gate) don't inherit an in-flight re-index.
    anyio.run(wait_until_ready, daemon_app.base_url)


@pytest.mark.integration
def test_cli_latency_u4(daemon_app: DaemonHandle) -> None:
    """U4 (docs/planning/cli-frontend.md): measure one warm `rust-lsp status`
    subprocess call's wall time. Budget is generous (~1-2s target, asserted
    under 5s) -- the point is to RECORD the actual number for the tracker,
    not just pass/fail."""
    anyio.run(wait_until_ready, daemon_app.base_url)

    start = time.monotonic()
    proc = _run_cli(daemon_app.base_url, "status")
    elapsed = time.monotonic() - start

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["state"] == "ready"

    print(f"\nU4: warm `rust-lsp status` subprocess call took {elapsed:.3f}s")
    assert elapsed < 5.0, f"warm status call took {elapsed:.3f}s -- exceeds the 5s generous bound"
