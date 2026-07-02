"""Regression tests for DS-07 — the analyzer's "error" state.

No live analyzer, no network.  Reuses the fake ``PatchedRustAnalyzer``
infrastructure from ``test_lifecycle_races.py`` (``FakeLsp``,
``PreYieldExceptionLsp``, the ``_no_git`` autouse fixture, etc.) rather than
re-implementing it — the fakes there already mimic multilspy's
``start_server()`` exactly, including the absence of a try/finally around its
``yield``.  Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    - A background run whose ``start_server()`` raises before quiescence sets
      ``state == STATE_ERROR`` and ``error_message`` carries the exception text.
    - ``status`` / ``analyzer_status`` surface the error via their ``state`` /
      ``analyzer_error`` fields.
    - ``core.require_ready()`` returns an ``error`` envelope (not ``not_ready``)
      when the manager is errored, mentioning the reason and the ``refresh``
      recovery path.
    - The gated ``probe`` tool surfaces the same error end-to-end through the
      real ``require_ready`` gate.
    - A superseded run (one that fails *after* a restart() has already bumped
      the generation) must NOT clobber the newer run's state/error.
    - ``restart()`` is the recovery path: it clears both ``state`` (back to
      ``STATE_INDEXING``) and ``error_message`` (back to ``None``).
    - Cancellation (``asyncio.CancelledError``) is explicitly NOT an error —
      it must never set ``state = STATE_ERROR`` / ``error_message``.
    - A single ``restart()`` recovers from an outgoing run that FAILS *during*
      the drain window (``_drain_task``'s ``wait_for`` re-raises the task's own
      exception, which must be swallowed so ``restart()`` still spawns the new
      run) — ONE refresh recovers, not two.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

from rust_lsp_mcp import analyzer as analyzer_module
from rust_lsp_mcp.analyzer import STATE_ERROR, STATE_INDEXING, STATE_READY, AnalyzerManager
from tests.test_lifecycle_races import (
    AutoReadyLsp,
    FakeLsp,
    PreYieldExceptionLsp,
    _await_first_instance,
    _cancel_leaked_tasks,
    _factory,
    _make_manager,
    _no_git,  # noqa: F401 — autouse fixture, must be in this module's namespace
)


class _DelayedFailLsp(FakeLsp):
    """Enters ``start_server``, parks on ``fail_gate``, then RAISES.

    Unlike ``PreYieldExceptionLsp`` (which fails synchronously on entry, before
    any caller can start draining it), this fake stays *running* until the test
    releases ``fail_gate`` — letting the test land the failure precisely inside
    ``_drain_task``'s ``wait_for`` (the window that re-raises the task's own
    exception into ``restart()``).
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_gate = asyncio.Event()

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["_DelayedFailLsp"]:
        self.entered.set()
        await self.fail_gate.wait()
        raise RuntimeError("boom during drain")
        yield self  # pragma: no cover - unreachable, keeps this an async generator


# ---------------------------------------------------------------------------
# 1. test_startup_failure_sets_error_state
# ---------------------------------------------------------------------------


class TestStartupFailureSetsErrorState:
    def test_startup_failure_sets_error_state(self) -> None:
        instances: list[PreYieldExceptionLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module,
                    "PatchedRustAnalyzer",
                    _factory(PreYieldExceptionLsp, instances),
                )
                mgr = _make_manager()
                await mgr.start()
                first = await _await_first_instance(instances)
                await asyncio.wait_for(first.entered.wait(), 2)

                # The run fails on its own (PreYieldExceptionLsp raises right
                # after entered.set()).  Await the task directly — suppressing
                # its exception — to observe the resulting error state; the
                # run was NOT superseded (no restart bumped the generation), so
                # its gen-guarded except-branch DOES publish state=error.
                assert mgr._task is not None
                with contextlib.suppress(Exception):
                    await mgr._task

                assert mgr.state == STATE_ERROR
                assert mgr.error_message is not None
                assert "boom: initialize failed" in mgr.error_message
                assert mgr.error_message.startswith("RuntimeError:")

        try:
            asyncio.run(_scenario())
        finally:
            for inst in instances:
                proc = inst.server.process
                if proc is not None and proc.returncode is None:
                    proc.kill()

    def test_constructor_failure_sets_error_state(self) -> None:
        """FINDING 2: a PatchedRustAnalyzer *constructor* failure (inside the
        widened try) must also set state=error, not leave the manager stuck in
        "indexing" forever.  There is no LSP object to clean up on this path
        (lsp stays None), so the finally must be a no-op.
        """

        def _raising_ctor(**kwargs: Any) -> Any:
            raise RuntimeError("ctor boom")

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _raising_ctor)
                mgr = _make_manager()
                await mgr.start()
                # The run fails synchronously at construction; await the task
                # (suppressing the exception) to observe the resulting state.
                assert mgr._task is not None
                with contextlib.suppress(Exception):
                    await mgr._task

                assert mgr.state == STATE_ERROR
                assert mgr.error_message is not None
                assert "ctor boom" in mgr.error_message
                assert mgr._lsp is None

        asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# 2. status / analyzer_status surface the error
# ---------------------------------------------------------------------------


def _errored_manager_stub() -> AnalyzerManager:
    """Stub manager via __new__ with state=STATE_ERROR, _error="boom", _lsp=None."""
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = STATE_ERROR
    mgr._error = "RuntimeError: boom"
    mgr._lsp = None
    mgr._indexed_commit = None
    mgr._repository_root = "/fake/repo"
    return mgr


class TestStatusReportsAnalyzerError:
    def test_status_reports_analyzer_error(self) -> None:
        import rust_lsp_mcp.core as core_mod
        import rust_lsp_mcp.tools.status as status_mod

        mgr = _errored_manager_stub()
        with (
            patch.object(core_mod, "_manager", mgr),
            patch.object(status_mod, "doc_store_state", return_value=("building", None)),
        ):
            result = status_mod.status()

        assert result["status"] == "ok"
        assert result["state"] == STATE_ERROR
        assert result["analyzer_error"] == "RuntimeError: boom"


class TestAnalyzerStatusReportsError:
    def test_analyzer_status_reports_error(self) -> None:
        import rust_lsp_mcp.core as core_mod
        import rust_lsp_mcp.tools.diagnostics as diagnostics_mod

        mgr = _errored_manager_stub()
        with patch.object(core_mod, "_manager", mgr):
            result = diagnostics_mod.analyzer_status()

        assert result["status"] == "ok"
        assert result["state"] == STATE_ERROR


# ---------------------------------------------------------------------------
# 3. require_ready() returns an error envelope, not not_ready
# ---------------------------------------------------------------------------


class TestRequireReadyReturnsErrorEnvelope:
    def test_require_ready_returns_error_envelope(self) -> None:
        import rust_lsp_mcp.core as core_mod

        mgr = _errored_manager_stub()
        with patch.object(core_mod, "_manager", mgr):
            guard = core_mod.require_ready()

        assert guard is not None
        assert guard["status"] == "error"
        assert guard["status"] != "not_ready"
        assert "RuntimeError: boom" in guard["message"]
        assert "refresh" in guard["message"].lower()


# ---------------------------------------------------------------------------
# 4. probe surfaces the error end-to-end through the real gate
# ---------------------------------------------------------------------------


class TestProbeSurfacesErrorEndToEnd:
    def test_probe_surfaces_error_end_to_end(self) -> None:
        import rust_lsp_mcp.core as core_mod
        import rust_lsp_mcp.tools.diagnostics as diagnostics_mod

        mgr = _errored_manager_stub()
        with patch.object(core_mod, "_manager", mgr):
            result = diagnostics_mod.probe()

        assert result["status"] == "error"
        assert "RuntimeError: boom" in result["message"]


# ---------------------------------------------------------------------------
# 5. A superseded failing run must not clobber a newer run's state/error.
# ---------------------------------------------------------------------------


class TestSupersededFailingRunDoesNotClobber:
    def test_superseded_failing_run_does_not_clobber(self) -> None:
        """A run that fails AFTER its generation has been superseded must not
        publish state=ERROR/error_message — mirrors the gen-guard that already
        protects state/_lsp/_ready_event on the success path (DS-03).

        Bumps ``_generation`` directly (rather than going through the full
        ``restart()``) to isolate the gen-guard in isolation from
        ``_drain_task``'s pre-existing, DS-07-unrelated behaviour of
        propagating a non-timeout task exception to its own caller (see
        ``test_lifecycle_races.test_preyield_exception_also_terminates_process``).

        Timing note: ``PreYieldExceptionLsp.start_server()`` calls
        ``self.entered.set()`` immediately followed by ``raise`` with NO
        ``await`` in between, so by the time our own ``await
        first.entered.wait()`` resumes, the background task has *already* run
        its ``except Exception`` gen-check synchronously (it is then merely
        parked in ``_run``'s ``finally`` awaiting the sentinel subprocess's
        ``terminate()``/``wait()``, which is why ``mgr._task.done()`` is still
        ``False`` at that point despite the gen-check having already fired).
        The generation bump must therefore happen BEFORE awaiting
        ``entered.wait()`` — immediately after the instance is constructed —
        to reliably land before that synchronous gen-check.
        """
        instances: list[PreYieldExceptionLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module,
                    "PatchedRustAnalyzer",
                    _factory(PreYieldExceptionLsp, instances),
                )
                mgr = _make_manager()
                await mgr.start()
                first = await _await_first_instance(instances)

                # Simulate a concurrent restart() having already bumped the
                # generation (its own state-reset step 2 already ran) BEFORE
                # the outgoing run reaches its (synchronous) failure point, so
                # its except-branch gen-check must see gen != self._generation
                # and skip the state/error write.
                mgr._generation += 1

                await asyncio.wait_for(first.entered.wait(), 2)
                assert mgr._task is not None
                with pytest.raises(RuntimeError, match="boom"):
                    await mgr._task

                # Must NOT have been clobbered by the superseded run's failure.
                assert mgr.state == STATE_INDEXING
                assert mgr.error_message is None

                await _cancel_leaked_tasks()

        try:
            asyncio.run(_scenario())
        finally:
            for inst in instances:
                proc = inst.server.process
                if proc is not None and proc.returncode is None:
                    proc.kill()


# ---------------------------------------------------------------------------
# 6. restart() clears the error — the recovery path.
# ---------------------------------------------------------------------------


class TestRestartClearsError:
    def test_restart_clears_error(self) -> None:
        instances: list[Any] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module,
                    "PatchedRustAnalyzer",
                    _factory(PreYieldExceptionLsp, instances),
                )
                mgr = _make_manager()
                await mgr.start()
                first = await _await_first_instance(instances)
                await asyncio.wait_for(first.entered.wait(), 2)

                # Drive to error: draining the failing task surfaces its
                # exception through shutdown()'s wait_for.  Use restart()
                # instead so the manager isn't closed — but restart() itself
                # drains the (about-to-fail) old task first. To reliably land
                # in STATE_ERROR before testing the *next* restart's recovery,
                # wait directly on the task's completion via the manager's
                # internal task handle, suppressing its exception.
                assert mgr._task is not None
                with contextlib.suppress(Exception):
                    await mgr._task
                assert mgr.state == STATE_ERROR
                assert mgr.error_message is not None

                # Now install a hanging fake for the recovery restart() and
                # confirm state/error reset to the indexing baseline.
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(FakeLsp, instances))
                await mgr.restart()

                assert mgr.state == STATE_INDEXING
                assert mgr.error_message is None

                with contextlib.suppress(Exception):
                    await mgr.shutdown()
                await _cancel_leaked_tasks()

        asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# 7. Cancellation is not an error.
# ---------------------------------------------------------------------------


class TestCancelledRunDoesNotSetError:
    def test_cancelled_run_does_not_set_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.test_lifecycle_races import HangingSentinelLsp

        monkeypatch.setattr(analyzer_module, "DRAIN_TIMEOUT_SECONDS", 0.05)
        instances: list[Any] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module, "PatchedRustAnalyzer", _factory(HangingSentinelLsp, instances)
                )
                mgr = _make_manager()
                await mgr.start()
                first = await _await_first_instance(instances)
                await asyncio.wait_for(first.entered.wait(), 2)
                proc = first.spawned_process
                assert proc is not None

                # A drain timeout cancels the outstanding task — this must
                # NOT be recorded as an error (cancellation is not a failure).
                await mgr.restart()

                assert mgr.error_message is None
                assert mgr.state == STATE_INDEXING

                for _ in range(5):
                    await asyncio.sleep(0)

        try:
            asyncio.run(_scenario())
        finally:
            for inst in instances:
                proc = inst.spawned_process
                if proc is not None and proc.returncode is None:
                    proc.kill()


# ---------------------------------------------------------------------------
# 8. ONE refresh recovers from a run that fails DURING the drain window.
# ---------------------------------------------------------------------------


class TestOneRefreshRecoversFromDrainWindowFailure:
    """The FINDING-1 regression: a failure landing inside ``_drain_task``'s
    ``wait_for`` must NOT abort ``restart()``.

    Before the fix, ``asyncio.wait_for(self._task, ...)`` re-raised the
    outgoing run's own exception, which propagated out of ``restart()`` after
    step 4 — no replacement run was ever spawned, ``state`` stuck at
    ``"indexing"`` forever, and it took a SECOND ``refresh`` to recover.  This
    test drives exactly that interleaving and asserts a SINGLE ``restart()``
    both completes (does not raise) and reaches a live ``"ready"`` run.
    """

    def test_one_refresh_recovers_from_drain_window_failure(self) -> None:
        instances: list[Any] = []
        # First run: _DelayedFailLsp (fails on release, during the drain).
        # Second run (post-restart): AutoReadyLsp (reaches ready immediately).
        classes = [_DelayedFailLsp, AutoReadyLsp]
        call_count = {"n": 0}

        def _make(**kwargs: Any) -> Any:
            cls = classes[min(call_count["n"], len(classes) - 1)]
            call_count["n"] += 1
            inst = cls(**kwargs)
            instances.append(inst)
            return inst

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _make)
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(first.entered.wait(), 2)

                    # Kick off restart() and let it park inside _drain_task's
                    # wait_for on the still-running first task.
                    restart_task = asyncio.create_task(mgr.restart())
                    for _ in range(3):
                        await asyncio.sleep(0)
                    assert mgr.state == STATE_INDEXING

                    # Now make the outgoing run FAIL — its exception lands
                    # inside _drain_task's wait_for.  Without the fix this
                    # re-raises out of restart(); with the fix it is swallowed.
                    first.fail_gate.set()

                    # ONE restart() must complete without raising.
                    await asyncio.wait_for(restart_task, 2)

                    # A replacement run was spawned (2 instances total) and the
                    # manager is back to a healthy indexing→ready path — no
                    # second refresh needed, no lingering error.
                    assert len(instances) == 2
                    assert mgr.error_message is None
                    assert mgr.state in (STATE_INDEXING, STATE_READY)

                    second = instances[1]
                    await asyncio.wait_for(second.entered.wait(), 2)
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready
                    assert mgr.state == STATE_READY
                    assert mgr._lsp is second
                    assert mgr.error_message is None
                finally:
                    with contextlib.suppress(Exception):
                        await mgr.shutdown()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())
