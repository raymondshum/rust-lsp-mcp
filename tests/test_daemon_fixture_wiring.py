"""Fast-tier guard: the daemon integration tests' fixture wiring must resolve.

QA round 1 regression (Phase C1): ``daemon_app`` is defined in
``tests/daemon_fixture.py`` — a plain helper module, which pytest does NOT
scan for fixtures — and the integration test module only imported helper
names from it, so both daemon integration tests errored at setup with
``fixture 'daemon_app' not found``. The fast tier never caught it because
``-m "not integration"`` deselects those tests before fixture resolution, and
the integration gate is the expensive, serialized resource — exactly the
wrong place to discover a wiring typo.

This guard runs pytest's ``--setup-plan`` on the daemon integration test
module in a subprocess. ``--setup-plan`` performs full collection AND fixture
RESOLUTION for every collected test (printing the setup/teardown plan)
without executing any test or fixture bodies — so it proves the
conftest-level ``daemon_app`` re-export works, in ~2s, with no analyzer, no
uvicorn, no network. A missing fixture makes pytest print the
"fixture 'daemon_app' not found" error block and exit nonzero.

Subprocess (not in-process ``pytest.main``) deliberately: an in-process run
would re-enter the already-initialized pytest plugin manager and re-import
test modules into this process's ``sys.modules`` — flaky and stateful; the
subprocess is hermetic. ``sys.executable`` is this venv's interpreter, so the
subprocess sees the exact same environment the suite runs under.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DAEMON_TEST_MODULE = "tests/test_daemon_http_integration.py"


def test_daemon_app_fixture_resolves_in_setup_plan() -> None:
    """`--setup-plan` on the daemon integration module must resolve daemon_app.

    Asserts three things, strongest first:
        1. pytest exits 0 (any collection or fixture-resolution error is
           nonzero);
        2. no "fixture ... not found" error text anywhere in the output
           (belt-and-suspenders — this is the exact QA-round-1 failure mode);
        3. the plan POSITIVELY shows a session-scoped ``daemon_app`` setup
           (``SETUP    S daemon_app``) — guards against the vacuous-pass
           where the module collects zero tests (e.g. after a rename) and
           there is simply nothing to resolve.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            DAEMON_TEST_MODULE,
            "--setup-plan",
            "-q",
            # Don't touch the parent run's .pytest_cache from the subprocess.
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"--setup-plan for {DAEMON_TEST_MODULE} exited "
        f"{result.returncode} — fixture wiring or collection is broken:\n{output}"
    )
    assert "not found" not in output, (
        f"--setup-plan reports an unresolvable fixture (the QA-round-1 regression):\n{output}"
    )
    assert "daemon_app" in output, (
        f"--setup-plan output never mentions daemon_app — the daemon test "
        f"module no longer requests the fixture (vacuous pass?):\n{output}"
    )
