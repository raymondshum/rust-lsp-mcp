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
    - KI-12 version fields: server_version/multilspy_version resolved via
      importlib.metadata (null-degradation on PackageNotFoundError);
      rust_analyzer_version read from the manager's cached value; capture-once
      semantics on AnalyzerManager.start().
"""

import asyncio
import contextlib
import importlib.metadata
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import rust_lsp_mcp.analyzer as analyzer_mod
from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_OK

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_COMMIT_A = "aabbccdd" * 5  # 40-char hex
_FAKE_COMMIT_B = "11223344" * 5  # different 40-char hex

# Sentinel distinguishing "doc_index_chunk_count_return not passed" from an
# explicitly-passed None (which is itself a meaningful value: "not ready").
_UNSET: Any = object()


def _make_manager(
    state: str,
    indexed_commit: str | None = None,
    repository_root: str = "/fake/repo",
    rust_analyzer_version: str | None = None,
) -> AnalyzerManager:
    """Build an AnalyzerManager stub without starting a real task or process.

    Sets ``_lsp`` to a non-None sentinel when state==ready so that
    ``is_ready`` (which checks both ``state`` and ``_lsp``) behaves correctly.

    ``_error`` is set to ``None`` so ``status()``'s ``error_message`` read
    never raises ``AttributeError`` on this stub (it does not go through
    ``__init__``).

    ``_rust_analyzer_version`` (KI-12) defaults to ``None`` (as if capture
    hadn't run / had failed); pass ``rust_analyzer_version`` to simulate a
    successful capture.
    """
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = state
    mgr._lsp = object() if state == STATE_READY else None  # type: ignore[assignment]
    mgr._indexed_commit = indexed_commit
    mgr._repository_root = repository_root
    mgr._error = None
    mgr._rust_analyzer_version = rust_analyzer_version
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
    doc_store_state_return: tuple[str, str | None] | None = None,
    doc_index_chunk_count_return: Any = _UNSET,
    preflight_warnings_return: list[str] | None = None,
) -> dict[str, Any]:
    """Invoke the status tool with the given manager and git subprocess stub.

    Patches ``rust_lsp_mcp.core._manager`` (via ``get_manager``) and
    ``rust_lsp_mcp.tools.status.subprocess.run`` so tests are fully hermetic.
    When ``doc_store_state_return`` is given, also patches
    ``rust_lsp_mcp.tools.status.doc_store_state`` to that fixed value —
    otherwise the real (module-singleton-backed) ``doc_store_state`` runs,
    which is fine for tests that don't care about the doc-index fields but
    would be nondeterministic (and coupled to other test files' state) for
    tests that do. ``doc_index_chunk_count_return`` (any of ``None``/``0``/a
    positive int counts as "given" — the sentinel default distinguishes "not
    passed" from "explicitly None") and ``preflight_warnings_return`` follow
    the same pattern for the two UR-20/UR-21 fields.

    DS-19: ``status`` is now an ``async def`` tool (it offloads the blocking
    git subprocess call and the doc-store's ``collection.count()`` to worker
    threads via ``asyncio.to_thread``) — drive it with ``asyncio.run`` here so
    every existing synchronous caller/assertion in this module keeps working
    unchanged.
    """
    import rust_lsp_mcp.core as core_mod
    import rust_lsp_mcp.tools.status as status_mod

    def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if subprocess_raises is not None:
            raise subprocess_raises
        return subprocess_run_return  # type: ignore[return-value]

    patches = [
        patch.object(core_mod, "_manager", manager),
        patch.object(status_mod.subprocess, "run", side_effect=_fake_run),
    ]
    if doc_store_state_return is not None:
        patches.append(
            patch.object(status_mod, "doc_store_state", return_value=doc_store_state_return)
        )
    if doc_index_chunk_count_return is not _UNSET:
        patches.append(
            patch.object(
                status_mod, "doc_index_chunk_count", return_value=doc_index_chunk_count_return
            )
        )
    if preflight_warnings_return is not None:
        patches.append(
            patch.object(
                status_mod, "get_preflight_warnings", return_value=preflight_warnings_return
            )
        )
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return asyncio.run(status_mod.status())


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


# ---------------------------------------------------------------------------
# Tests: doc_index_state / doc_index_error (DS-14) and analyzer_error (DS-07)
# ---------------------------------------------------------------------------


class TestStatusDocIndex:
    def test_doc_index_building(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            doc_store_state_return=("building", None),
        )
        assert result["doc_index_state"] == "building"
        assert result["doc_index_error"] is None

    def test_doc_index_ready(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            doc_store_state_return=("ready", None),
        )
        assert result["doc_index_state"] == "ready"
        assert result["doc_index_error"] is None
        # A ready analyzer + ready doc index must report analyzer_error=None.
        assert result["analyzer_error"] is None

    def test_doc_index_error(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            doc_store_state_return=("error", "RuntimeError: embedding model unavailable"),
        )
        assert result["doc_index_state"] == "error"
        assert result["doc_index_error"] == "RuntimeError: embedding model unavailable"

    def test_doc_index_error_does_not_affect_analyzer_error(self) -> None:
        """The two error surfaces (analyzer vs doc index) are independent."""
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            doc_store_state_return=("error", "boom"),
        )
        assert result["analyzer_error"] is None
        assert result["status"] == STATUS_OK

    def test_no_manager_analyzer_error_none(self) -> None:
        result = _call_status(
            None,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            doc_store_state_return=("building", None),
        )
        assert result["analyzer_error"] is None


# ---------------------------------------------------------------------------
# Tests: doc_index_chunk_count (UR-20) and preflight_warnings (UR-21)
# ---------------------------------------------------------------------------


class TestStatusDocIndexChunkCount:
    def test_none_when_not_ready(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            doc_store_state_return=("building", None),
            doc_index_chunk_count_return=None,
        )
        assert result["doc_index_chunk_count"] is None

    def test_zero_for_empty_adopted_corpus(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            doc_store_state_return=("ready", None),
            doc_index_chunk_count_return=0,
        )
        assert result["doc_index_state"] == "ready"
        assert result["doc_index_chunk_count"] == 0

    def test_positive_for_populated_corpus(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            doc_store_state_return=("ready", None),
            doc_index_chunk_count_return=42,
        )
        assert result["doc_index_chunk_count"] == 42

    def test_status_still_ok(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            doc_store_state_return=("ready", None),
            doc_index_chunk_count_return=0,
        )
        assert result["status"] == STATUS_OK


class TestStatusPreflightWarnings:
    def test_empty_list_when_all_clear(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            preflight_warnings_return=[],
        )
        assert result["preflight_warnings"] == []

    def test_surfaces_whatever_the_holder_contains(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        warnings = [
            "rust-analyzer binary '/fake/rust-analyzer' was not found",
            "project_root '/fake/repo' does not exist or is not a directory",
        ]
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            preflight_warnings_return=warnings,
        )
        assert result["preflight_warnings"] == warnings

    def test_status_still_ok_with_warnings_present(self) -> None:
        """preflight_warnings is advisory-only — must never flip envelope status."""
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(
            mgr,
            subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A),
            preflight_warnings_return=["something to warn about"],
        )
        assert result["status"] == STATUS_OK
        assert result["state"] == STATE_READY


# ---------------------------------------------------------------------------
# Tests: KI-12 version fields (server_version, multilspy_version,
# rust_analyzer_version)
# ---------------------------------------------------------------------------


class TestStatusPackageVersions:
    """server_version / multilspy_version: importlib.metadata, null-degrade."""

    def test_server_version_resolved(self) -> None:
        with patch.object(importlib.metadata, "version", return_value="9.9.9"):
            result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["server_version"] == "9.9.9"

    def test_multilspy_version_resolved(self) -> None:
        with patch.object(importlib.metadata, "version", return_value="9.9.9"):
            result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["multilspy_version"] == "9.9.9"

    def test_server_version_null_on_package_not_found(self) -> None:
        def _fake_version(name: str) -> str:
            if name == "rust-lsp-mcp":
                raise importlib.metadata.PackageNotFoundError(name)
            return "0.0.15"

        with patch.object(importlib.metadata, "version", side_effect=_fake_version):
            result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["server_version"] is None
        assert result["multilspy_version"] == "0.0.15"

    def test_multilspy_version_null_on_package_not_found(self) -> None:
        def _fake_version(name: str) -> str:
            if name == "multilspy":
                raise importlib.metadata.PackageNotFoundError(name)
            return "0.1.0"

        with patch.object(importlib.metadata, "version", side_effect=_fake_version):
            result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["multilspy_version"] is None
        assert result["server_version"] == "0.1.0"

    def test_status_still_ok_when_both_package_lookups_fail(self) -> None:
        with patch.object(
            importlib.metadata,
            "version",
            side_effect=importlib.metadata.PackageNotFoundError("x"),
        ):
            result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["status"] == STATUS_OK
        assert result["server_version"] is None
        assert result["multilspy_version"] is None


class TestStatusRustAnalyzerVersion:
    """rust_analyzer_version: read straight off the manager's cached value —
    status() never itself invokes the version subprocess."""

    def test_present_when_manager_has_captured_it(self) -> None:
        mgr = _make_manager(
            STATE_READY,
            indexed_commit=_FAKE_COMMIT_A,
            rust_analyzer_version="rust-analyzer 1.96.0 (ac68faa 2026-05-25)",
        )
        result = _call_status(mgr, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["rust_analyzer_version"] == "rust-analyzer 1.96.0 (ac68faa 2026-05-25)"

    def test_null_when_manager_capture_failed_or_never_ran(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit=_FAKE_COMMIT_A)
        result = _call_status(mgr, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["rust_analyzer_version"] is None

    def test_null_when_no_manager(self) -> None:
        result = _call_status(None, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
        assert result["rust_analyzer_version"] is None

    def test_capture_once_across_start_and_repeated_status_calls(self) -> None:
        """AnalyzerManager.start() captures the version exactly once (KI-12):
        a second start() (as restart() performs) must not re-invoke the
        subprocess, and neither does any number of subsequent status() calls
        (status() only ever reads the cached ``rust_analyzer_version``
        property — it never touches the version subprocess itself)."""
        call_count = 0

        def _fake_version_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            return _completed_process(0, "rust-analyzer 1.96.0 (ac68faa 2026-05-25)\n")

        async def _noop_run(self: AnalyzerManager, gen: int) -> None:
            return None

        async def _scenario() -> AnalyzerManager:
            mgr = analyzer_mod.AnalyzerManager(
                rust_analyzer_bin="/fake/rust-analyzer", repository_root="/fake/repo"
            )
            await mgr.start()
            assert mgr._task is not None
            await mgr._task
            # Simulate restart()'s second call to start() — must not re-capture.
            await mgr.start()
            assert mgr._task is not None
            await mgr._task
            return mgr

        with (
            patch.object(analyzer_mod.subprocess, "run", side_effect=_fake_version_run),
            patch.object(analyzer_mod.AnalyzerManager, "_run", _noop_run),
        ):
            mgr = asyncio.run(_scenario())

        assert call_count == 1
        assert mgr.rust_analyzer_version == "rust-analyzer 1.96.0 (ac68faa 2026-05-25)"

        # Outside the patch (subprocess.run restored): repeated status() calls
        # must keep reading the cached value without any further capture.
        for _ in range(3):
            result = _call_status(mgr, subprocess_run_return=_completed_process(0, _FAKE_COMMIT_A))
            assert result["rust_analyzer_version"] == "rust-analyzer 1.96.0 (ac68faa 2026-05-25)"
        assert call_count == 1

    def test_null_on_missing_binary(self) -> None:
        """A missing binary (FileNotFoundError) must degrade to None, never raise."""

        async def _noop_run(self: AnalyzerManager, gen: int) -> None:
            return None

        async def _scenario() -> AnalyzerManager:
            mgr = analyzer_mod.AnalyzerManager(
                rust_analyzer_bin="/fake/nonexistent", repository_root="/fake/repo"
            )
            await mgr.start()
            assert mgr._task is not None
            await mgr._task
            return mgr

        with (
            patch.object(
                analyzer_mod.subprocess, "run", side_effect=FileNotFoundError("no such file")
            ),
            patch.object(analyzer_mod.AnalyzerManager, "_run", _noop_run),
        ):
            mgr = asyncio.run(_scenario())

        assert mgr.rust_analyzer_version is None

    def test_null_on_nonzero_exit(self) -> None:
        """A non-zero exit (e.g. bad flag) must degrade to None, never raise."""

        async def _noop_run(self: AnalyzerManager, gen: int) -> None:
            return None

        async def _scenario() -> AnalyzerManager:
            mgr = analyzer_mod.AnalyzerManager(
                rust_analyzer_bin="/fake/rust-analyzer", repository_root="/fake/repo"
            )
            await mgr.start()
            assert mgr._task is not None
            await mgr._task
            return mgr

        with (
            patch.object(analyzer_mod.subprocess, "run", return_value=_completed_process(1, "")),
            patch.object(analyzer_mod.AnalyzerManager, "_run", _noop_run),
        ):
            mgr = asyncio.run(_scenario())

        assert mgr.rust_analyzer_version is None

    def test_null_on_timeout(self) -> None:
        """A hung binary (TimeoutExpired) must degrade to None, never raise."""

        async def _noop_run(self: AnalyzerManager, gen: int) -> None:
            return None

        async def _scenario() -> AnalyzerManager:
            mgr = analyzer_mod.AnalyzerManager(
                rust_analyzer_bin="/fake/rust-analyzer", repository_root="/fake/repo"
            )
            await mgr.start()
            assert mgr._task is not None
            await mgr._task
            return mgr

        with (
            patch.object(
                analyzer_mod.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="rust-analyzer --version", timeout=5),
            ),
            patch.object(analyzer_mod.AnalyzerManager, "_run", _noop_run),
        ):
            mgr = asyncio.run(_scenario())

        assert mgr.rust_analyzer_version is None
