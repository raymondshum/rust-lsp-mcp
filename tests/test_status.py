"""Fast-tier tests for the status tool (Phase 4).

No live analyzer, no network.  All git subprocess calls are monkeypatched.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    - No manager: state="indexing", indexed_commit=None, stale=None.
    - Ready manager, indexed_commit == current_commit: stale=False.
    - Ready manager, indexed_commit != current_commit: stale=True.
    - Git failure (non-zero returncode): current_commit=None, stale=None.
    - Git exception (subprocess raises): current_commit=None, stale=None.
    - indexed_commit=None but current_commit set: stale=None.
    - All cases return status="ok" (tool is always ungated).
"""

import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_OK

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_COMMIT_A = "aabbccdd" * 5  # 40-char hex
_FAKE_COMMIT_B = "11223344" * 5  # different 40-char hex


def _make_manager(
    state: str,
    indexed_commit: str | None = None,
    repository_root: str = "/fake/repo",
) -> AnalyzerManager:
    """Build an AnalyzerManager stub without starting a real task or process.

    Sets ``_lsp`` to a non-None sentinel when state==ready so that
    ``is_ready`` (which checks both ``state`` and ``_lsp``) behaves correctly.
    """
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = state
    mgr._lsp = object() if state == STATE_READY else None  # type: ignore[assignment]
    mgr._indexed_commit = indexed_commit
    mgr._repository_root = repository_root
    return mgr


def _completed_process(returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    """Build a fake CompletedProcess for subprocess.run monkeypatching."""
    cp: subprocess.CompletedProcess[str] = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = ""
    return cp


def _call_status(
    manager: AnalyzerManager | None,
    subprocess_run_return: Any = None,
    subprocess_raises: Exception | None = None,
) -> dict[str, Any]:
    """Invoke the status tool with the given manager and git subprocess stub.

    Patches both ``rust_lsp_mcp.core._manager`` (via ``get_manager``) and
    ``rust_lsp_mcp.tools.status.subprocess.run`` so tests are fully hermetic.
    """
    import rust_lsp_mcp.core as core_mod
    import rust_lsp_mcp.tools.status as status_mod

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if subprocess_raises is not None:
            raise subprocess_raises
        return subprocess_run_return  # type: ignore[return-value]

    with (
        patch.object(core_mod, "_manager", manager),
        patch.object(status_mod.subprocess, "run", side_effect=_fake_run),
    ):
        return status_mod.status()


# ---------------------------------------------------------------------------
# Tests: no manager (pre-lifespan)
# ---------------------------------------------------------------------------


class TestStatusNoManager:
    def test_state_is_indexing(self) -> None:
        result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["state"] == STATE_INDEXING

    def test_indexed_commit_is_none(self) -> None:
        result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["indexed_commit"] is None

    def test_stale_is_none_because_indexed_commit_unknown(self) -> None:
        # indexed_commit=None → stale=None regardless of current_commit.
        result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["stale"] is None

    def test_status_is_ok(self) -> None:
        result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["status"] == STATUS_OK


# ---------------------------------------------------------------------------
# Tests: ready manager, same commit → stale=False
# ---------------------------------------------------------------------------


class TestStatusReadySameCommit:
    def _result(self) -> dict[str, Any]:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        return _call_status(mgr, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A + "\n"))

    def test_status_ok(self) -> None:
        assert self._result()["status"] == STATUS_OK

    def test_state_ready(self) -> None:
        assert self._result()["state"] == STATE_READY

    def test_indexed_commit_set(self) -> None:
        assert self._result()["indexed_commit"] == _FAKE_COMMIT_A

    def test_current_commit_set(self) -> None:
        # subprocess.run returns "aabbccdd...\n"; the tool must strip whitespace.
        assert self._result()["current_commit"] == _FAKE_COMMIT_A

    def test_stale_false(self) -> None:
        assert self._result()["stale"] is False


# ---------------------------------------------------------------------------
# Tests: ready manager, different commit → stale=True
# ---------------------------------------------------------------------------


class TestStatusReadyDifferentCommit:
    def _result(self) -> dict[str, Any]:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        return _call_status(mgr, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_B))

    def test_status_ok(self) -> None:
        assert self._result()["status"] == STATUS_OK

    def test_stale_true(self) -> None:
        assert self._result()["stale"] is True

    def test_commits_differ(self) -> None:
        r = self._result()
        assert r["indexed_commit"] != r["current_commit"]


# ---------------------------------------------------------------------------
# Tests: git failure (non-zero returncode)
# ---------------------------------------------------------------------------


class TestStatusGitFailure:
    def _result(self) -> dict[str, Any]:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        return _call_status(mgr, subprocess_run_return=_completed_process(128, ""))

    def test_status_ok(self) -> None:
        # Tool is ungated; status must still be ok even when git fails.
        assert self._result()["status"] == STATUS_OK

    def test_current_commit_none(self) -> None:
        assert self._result()["current_commit"] is None

    def test_stale_none(self) -> None:
        # current_commit=None → cannot determine staleness.
        assert self._result()["stale"] is None


# ---------------------------------------------------------------------------
# Tests: git subprocess raises (e.g. FileNotFoundError — git not on PATH)
# ---------------------------------------------------------------------------


class TestStatusGitRaises:
    def _result(self) -> dict[str, Any]:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        return _call_status(mgr, subprocess_raises=FileNotFoundError("git not found"))

    def test_status_ok(self) -> None:
        assert self._result()["status"] == STATUS_OK

    def test_current_commit_none(self) -> None:
        assert self._result()["current_commit"] is None

    def test_stale_none(self) -> None:
        assert self._result()["stale"] is None


# ---------------------------------------------------------------------------
# Tests: indexed_commit=None but current_commit set → stale=None
# ---------------------------------------------------------------------------


class TestStatusIndexedCommitNone:
    def _result(self) -> dict[str, Any]:
        mgr = _make_manager(STATE_READY, indexed_commit=None)
        return _call_status(mgr, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_B))

    def test_status_ok(self) -> None:
        assert self._result()["status"] == STATUS_OK

    def test_indexed_commit_none(self) -> None:
        assert self._result()["indexed_commit"] is None

    def test_current_commit_set(self) -> None:
        # current_commit is determined even when indexed_commit is None.
        assert self._result()["current_commit"] == _FAKE_COMMIT_B

    def test_stale_none(self) -> None:
        # Cannot determine staleness when indexed_commit is unknown.
        assert self._result()["stale"] is None
