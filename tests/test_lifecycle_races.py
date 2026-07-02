"""Regression tests for AnalyzerManager lifecycle races (DS-03, DS-04, DS-21).

No live analyzer, no network.  All heavy dependencies are stubbed via a fake
``PatchedRustAnalyzer`` that mimics multilspy's ``RustAnalyzer.start_server()``
*exactly* (including the absence of a try/finally around its ``yield`` — the
root cause of DS-04).  Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    DS-03 (generation counter — a restart() mid-index must never let the
    superseded run publish a stale "ready"):
        - A run that reaches quiescence *after* being superseded by restart()
          must exit cleanly without setting state/`_lsp`/`_ready_event`, and
          the caller must observe state == "indexing" (never a stale "ready")
          the instant the draining restart() call returns.
        - The superseded run's gen-check aborts before touching any shared
          event object — the *stale* `_ready_event` reference (captured
          before restart() replaces it) must never be set.

    DS-04 (cancellation-safe teardown — multilspy's start_server() has no
    try/finally around its yield, so a cancelled/failed pre-yield leaks the
    rust-analyzer subprocess unless our own `_run` finally force-stops it):
        - A drain that times out and cancels the task must still terminate
          the live subprocess (via the idempotent `lsp.server.stop()`).
        - `lsp.server.stop()` is idempotent and safe to call unconditionally,
          including on the normal (non-cancelled) exit path where multilspy
          already called it once.
        - A pre-yield exception (e.g. a failed capability assertion) must
          also terminate the spawned subprocess.

    DS-21 (lifecycle lock + closed flag):
        - Two concurrent restart() calls must serialize: exactly one live
          "analyzer-lifecycle" task survives, and every prior instance is
          torn down (never two independently-running background tasks).
        - restart() after shutdown() is a no-op (no new task/instance).

Concurrency-timing note: several tests rely on plain `await asyncio.sleep(0)`
calls to let a freshly created task reach its first blocking await point.
This is deliberate and matches asyncio's documented FIFO `call_soon`
scheduling — see the inline comments at each usage.
"""

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from rust_lsp_mcp import analyzer as analyzer_module
from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager

FAKE_COMMIT = "a" * 40


async def _fake_capture_head_commit(self: AnalyzerManager) -> None:
    """Stand-in for AnalyzerManager._capture_head_commit — no git, no subprocess."""
    self._indexed_commit = FAKE_COMMIT


@pytest.fixture(autouse=True)
def _no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AnalyzerManager, "_capture_head_commit", _fake_capture_head_commit)


class CaptureGate:
    """Controllable stand-in for ``AnalyzerManager._capture_head_commit``.

    Unlike ``_fake_capture_head_commit`` (which always writes the same
    ``FAKE_COMMIT``), each invocation here produces a *distinct* commit value
    (``"commit-1"``, ``"commit-2"``, ...) and blocks until the test releases
    it.  That lets a test drive the exact interleaving restart()'s step-5
    re-clear guards: a superseded run's in-flight capture landing its OWN
    (distinct) value *during* the drain window.  With the same value for both
    runs the re-clear would be invisible; distinct values make the leak
    observable.
    """

    def __init__(self) -> None:
        self.started: list[asyncio.Event] = []
        self.release: list[asyncio.Event] = []
        self.values: list[str] = []

    async def _capture(self, mgr: AnalyzerManager) -> None:
        idx = len(self.started)
        started = asyncio.Event()
        release = asyncio.Event()
        value = f"commit-{idx + 1}"
        self.started.append(started)
        self.release.append(release)
        self.values.append(value)
        started.set()
        await release.wait()
        mgr._indexed_commit = value

    def install(self, mp: pytest.MonkeyPatch) -> None:
        controller = self

        async def _capture_head_commit(self: AnalyzerManager) -> None:
            await controller._capture(self)

        mp.setattr(AnalyzerManager, "_capture_head_commit", _capture_head_commit)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeHandler:
    """Stand-in for multilspy's LanguageServerHandler (``lsp.server``).

    ``stop()`` mirrors the real implementation's idempotence: it swaps
    ``process`` to None and returns early on a second call.  The None-guard
    is load-bearing for ``test_stop_is_idempotent_on_normal_path``.
    """

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1
        process, self.process = self.process, None
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()

    async def shutdown(self) -> None:
        """Recorded no-op — real multilspy shutdown() sends a protocol request."""
        return None


class FakeLsp:
    """Mimics ``PatchedRustAnalyzer`` / multilspy's ``start_server()`` EXACTLY.

    Crucially there is NO try/finally around the ``yield`` — matching
    multilspy 0.0.15's ``RustAnalyzer.start_server()`` — so a cancellation
    (or an exception) anywhere before the yield skips both
    ``self.server.shutdown()`` and ``self.server.stop()``.  This is precisely
    the hazard DS-04 defends against from the *caller* side (`_run`'s own
    finally), since we cannot change multilspy's source.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.entered = asyncio.Event()
        self.quiesce_gate = asyncio.Event()
        self.exited = False
        self.server = FakeHandler()
        # Permanent handle for test assertions — unlike `self.server.process`
        # (which the fix nils out via FakeHandler.stop()'s swap-to-None),
        # this is never cleared, so tests can reliably `.wait()`/`.kill()` a
        # sentinel even after DS-04's finally has already force-stopped it.
        self.spawned_process: asyncio.subprocess.Process | None = None

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["FakeLsp"]:
        self.entered.set()
        await self.quiesce_gate.wait()
        yield self
        await self.server.shutdown()
        await self.server.stop()
        self.exited = True


class AutoReadyLsp(FakeLsp):
    """FakeLsp whose quiescence gate is already set — reaches "ready" ASAP."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.quiesce_gate.set()


class HangingSentinelLsp(FakeLsp):
    """Spawns a REAL sentinel subprocess pre-yield, then hangs forever.

    Simulates rust-analyzer stuck mid-initialize/quiescence: the process is
    live but the gate that would let start_server() reach its yield is never
    set by the test.  Used to prove DS-04's finally force-stop actually
    reaps a real OS process on the cancel path.
    """

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["HangingSentinelLsp"]:
        self.spawned_process = self.server.process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(3600)"
        )
        self.entered.set()
        await self.quiesce_gate.wait()  # never set by the test -> hangs
        yield self
        await self.server.shutdown()  # pragma: no cover - unreachable in these tests
        await self.server.stop()
        self.exited = True


class PreYieldExceptionLsp(FakeLsp):
    """Spawns a REAL sentinel subprocess, then fails before reaching yield.

    Simulates a failed capability assertion / initialize response mid
    pre-yield setup (multilspy has no try/finally there either).
    """

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["PreYieldExceptionLsp"]:
        self.spawned_process = self.server.process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(3600)"
        )
        self.entered.set()
        raise RuntimeError("boom: initialize failed")
        yield self  # pragma: no cover - unreachable, keeps this an async generator


def _factory(cls: type, instances: list) -> Any:
    """Return a callable that constructs `cls(**kwargs)`, recording each instance."""

    def _make(**kwargs: Any) -> Any:
        inst = cls(**kwargs)
        instances.append(inst)
        return inst

    return _make


async def _await_first_instance(instances: list, timeout: float = 2.0) -> Any:
    """Wait until `instances` has at least one entry, then return instances[0].

    `mgr.start()` only *schedules* the background task (asyncio.create_task
    never runs the coroutine body inline) so `instances` can still be empty
    immediately after `await mgr.start()` returns.  Poll with sleep(0) until
    the task has had its first turn.
    """

    async def _poll() -> Any:
        while not instances:
            await asyncio.sleep(0)
        return instances[0]

    return await asyncio.wait_for(_poll(), timeout)


async def _wait_until(predicate: Any, timeout: float = 2.0) -> None:
    """Yield control (sleep(0)) until `predicate()` is truthy, or time out."""

    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(_poll(), timeout)


def _make_manager() -> AnalyzerManager:
    return AnalyzerManager(rust_analyzer_bin="/fake/rust-analyzer", repository_root="/fake/repo")


def _live_lifecycle_tasks() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == "analyzer-lifecycle" and not t.done()]


async def _cancel_leaked_tasks() -> None:
    """Cancel and drain any orphaned "analyzer-lifecycle" tasks left by a test.

    Mandatory cleanup for the DS-21/DS-04 red-phase runs, which can leave a
    task (and, worse, a real OS subprocess) leaked with no reference on the
    manager.
    """
    leaked = _live_lifecycle_tasks()
    for t in leaked:
        t.cancel()
    for t in leaked:
        with contextlib.suppress(BaseException):
            await t


# ---------------------------------------------------------------------------
# DS-03 — generation counter
# ---------------------------------------------------------------------------


class TestRestartDuringIndexing:
    """A restart() issued mid-index must never let the old run go "ready"."""

    def test_restart_mid_index_never_reports_stale_ready(self) -> None:
        instances: list[FakeLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(FakeLsp, instances))
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(first.entered.wait(), 2)

                    restart_task = asyncio.create_task(mgr.restart())
                    # Let restart() run until it parks inside _drain_task's
                    # wait_for (the old task is still blocked on its gate).
                    for _ in range(3):
                        await asyncio.sleep(0)
                    assert mgr.state == STATE_INDEXING

                    # Old run reaches quiescence *during* the drain window.
                    first.quiesce_gate.set()

                    await asyncio.wait_for(restart_task, 2)

                    assert mgr.state == STATE_INDEXING
                    assert mgr.is_ready is False
                    # indexed_commit is either None (re-clear from step 5 of
                    # restart(), if the new run's own _capture_head_commit()
                    # hasn't executed yet) or already FAKE_COMMIT (the new
                    # run's *own* correct capture, not a stale leftover from
                    # the superseded run) — asyncio's call_soon FIFO ordering
                    # means the freshly created task can legitimately reach
                    # this far before control returns here.  What must NEVER
                    # happen is exposing a stale value from the old run.
                    assert mgr.indexed_commit in (None, FAKE_COMMIT)
                    assert not mgr._ready_event.is_set()
                    assert first.exited is True
                    assert len(instances) == 2

                    second = instances[1]
                    await asyncio.wait_for(second.entered.wait(), 2)
                    second.quiesce_gate.set()
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)

                    assert mgr.is_ready
                    assert mgr._lsp is second
                    assert mgr.state == STATE_READY
                    assert mgr.indexed_commit == FAKE_COMMIT
                finally:
                    await mgr.shutdown()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())

    def test_old_run_never_sets_new_ready_event(self) -> None:
        """The superseded run's gen-check must abort before touching any event."""
        instances: list[FakeLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(FakeLsp, instances))
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(first.entered.wait(), 2)

                    stale_ready_event = mgr._ready_event

                    restart_task = asyncio.create_task(mgr.restart())
                    for _ in range(3):
                        await asyncio.sleep(0)

                    first.quiesce_gate.set()
                    await asyncio.wait_for(restart_task, 2)

                    # The pre-existing event object must never have been set
                    # by the superseded run, even transiently.
                    assert stale_ready_event.is_set() is False

                    second = instances[1]
                    await asyncio.wait_for(second.entered.wait(), 2)
                    second.quiesce_gate.set()
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)

                    assert mgr._ready_event is not stale_ready_event
                    assert stale_ready_event.is_set() is False
                finally:
                    await mgr.shutdown()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())

    def test_indexed_commit_recleared_after_drain(self) -> None:
        """restart() step 5 must wipe a superseded run's late commit capture.

        Drives the exact interleaving the post-drain re-clear guards: the
        outgoing run is parked inside ``_capture_head_commit`` when restart()
        begins, so restart()'s step-2 clear runs first, and then the old
        capture lands its OWN (distinct) commit value *during* the drain
        window (generation already bumped; the old run is about to exit on its
        gen-check).  Without step 5's ``self._indexed_commit = None`` re-clear
        that stale ``"commit-1"`` would survive into the fresh indexing window
        and be reported by ``status`` as if it were current.

        Distinct per-run commit values (via ``CaptureGate``) are what make the
        leak observable — with a shared ``FAKE_COMMIT`` the re-clear is a
        no-op and the test could not falsify a reverted step 5.
        """
        instances: list[FakeLsp] = []
        cap = CaptureGate()

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(FakeLsp, instances))
                cap.install(mp)
                mgr = _make_manager()
                try:
                    await mgr.start()
                    # Old run parks inside its _capture_head_commit (call 0),
                    # BEFORE reaching start_server — so no FakeLsp yet.
                    await _wait_until(lambda: len(cap.started) >= 1)

                    restart_task = asyncio.create_task(mgr.restart())
                    # Let restart() reach _drain_task's wait_for on the old task.
                    for _ in range(3):
                        await asyncio.sleep(0)

                    # Old capture lands its DISTINCT value mid-drain, then the
                    # old run exits on its gen-check (superseded).
                    cap.release[0].set()

                    await asyncio.wait_for(restart_task, 2)

                    # Step 5 must have re-cleared it: the superseded run's
                    # "commit-1" must NOT survive; indexed_commit is the honest
                    # "unknown" (None) during the new indexing window.
                    assert mgr.indexed_commit != "commit-1"
                    assert mgr.indexed_commit is None

                    # The new run parks inside its OWN capture (call 1); release
                    # it and let it reach ready with its own distinct value.
                    await _wait_until(lambda: len(cap.started) >= 2)
                    cap.release[1].set()
                    second = await _await_first_instance(instances)
                    await asyncio.wait_for(second.entered.wait(), 2)
                    second.quiesce_gate.set()
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)

                    assert mgr.is_ready
                    assert mgr.indexed_commit == "commit-2"
                finally:
                    await mgr.shutdown()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# DS-04 — cancellation-safe teardown
# ---------------------------------------------------------------------------


class TestDrainCancelTeardown:
    """finally must force-stop the subprocess whenever start_server() is abandoned."""

    def test_drain_timeout_cancel_terminates_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(analyzer_module, "DRAIN_TIMEOUT_SECONDS", 0.05)
        instances: list[HangingSentinelLsp] = []

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

                await mgr.restart()

                assert first.server.stop_calls >= 1
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except TimeoutError:
                    pytest.fail(
                        "sentinel subprocess was not terminated after a drain "
                        "timeout/cancel (DS-04 regression)"
                    )
                assert proc.returncode is not None
                assert mgr._lsp is None
                assert mgr.state == STATE_INDEXING

                # restart() also respawned a *replacement* background task
                # (also a HangingSentinelLsp, also doomed to hang forever —
                # nothing in this test ever sets its gate).  Give it a couple
                # of turns so it constructs itself — and spawns its own
                # sentinel — while `mp` is still active; otherwise it would
                # only get its first turn during asyncio.run()'s teardown,
                # by which point the monkeypatch has been undone and it
                # would construct a REAL (unpatched) PatchedRustAnalyzer.
                # This is purely test-isolation bookkeeping — the replacement
                # task's own eventual cleanup is exercised by the other tests
                # in this class, not here.
                for _ in range(5):
                    await asyncio.sleep(0)

        try:
            asyncio.run(_scenario())
        finally:
            for inst in instances:
                proc = inst.spawned_process
                if proc is not None and proc.returncode is None:
                    proc.kill()

    def test_stop_is_idempotent_on_normal_path(self) -> None:
        instances: list[FakeLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(FakeLsp, instances))
                mgr = _make_manager()
                await mgr.start()
                first = await _await_first_instance(instances)
                await asyncio.wait_for(first.entered.wait(), 2)
                first.quiesce_gate.set()
                await asyncio.wait_for(mgr._ready_event.wait(), 2)
                assert mgr.is_ready

                await mgr.shutdown()

                # multilspy's own post-yield teardown calls stop() once; our
                # finally's force-stop calls it a second, harmless time.
                assert first.server.stop_calls == 2
                assert first.exited is True

        asyncio.run(_scenario())

    def test_preyield_exception_also_terminates_process(self) -> None:
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
                proc = first.spawned_process
                assert proc is not None

                # The task finishes (with an exception) on its own; no
                # timeout is needed to observe DS-04's force-stop here.
                # _drain_task SWALLOWS a non-timeout outgoing-run failure
                # (log-and-continue) rather than propagating it — the whole
                # point of a drain is to tear the old run down, so its failure
                # must not abort the caller (see the _drain_task docstring and
                # the DS-07 recovery-path fix).  shutdown() therefore returns
                # cleanly; the important thing for this test is that _run's
                # finally still ran (and force-stopped the subprocess).
                await mgr.shutdown()

                assert first.server.stop_calls >= 1
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except TimeoutError:
                    pytest.fail(
                        "sentinel subprocess was not terminated after a "
                        "pre-yield exception (DS-04 regression)"
                    )
                assert proc.returncode is not None

        try:
            asyncio.run(_scenario())
        finally:
            for inst in instances:
                proc = inst.server.process
                if proc is not None and proc.returncode is None:
                    proc.kill()


# ---------------------------------------------------------------------------
# DS-21 — lifecycle lock + closed flag
# ---------------------------------------------------------------------------


class TestConcurrentRestart:
    """Concurrent restart() calls must serialize behind the lifecycle lock."""

    def test_concurrent_restarts_leave_single_task_and_process(self) -> None:
        instances: list[AutoReadyLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module, "PatchedRustAnalyzer", _factory(AutoReadyLsp, instances)
                )
                mgr = _make_manager()
                try:
                    await mgr.start()
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready

                    await asyncio.gather(mgr.restart(), mgr.restart())

                    live = _live_lifecycle_tasks()
                    assert live == [mgr._task], f"expected exactly [mgr._task], got {live}"
                    assert len(live) == 1

                    assert len(instances) == 3
                    for inst in instances[:-1]:
                        assert inst.exited or inst.server.stop_calls > 0

                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr._lsp is instances[-1]
                finally:
                    await _cancel_leaked_tasks()
                    await mgr.shutdown()

        asyncio.run(_scenario())


class TestShutdownInteraction:
    """restart() after shutdown() must be a no-op."""

    def test_restart_after_shutdown_is_noop(self) -> None:
        instances: list[AutoReadyLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module, "PatchedRustAnalyzer", _factory(AutoReadyLsp, instances)
                )
                mgr = _make_manager()
                try:
                    await mgr.start()
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)

                    await mgr.shutdown()
                    assert mgr._closed is True

                    task_before = mgr._task
                    await mgr.restart()

                    assert mgr._task is task_before
                    assert len(instances) == 1
                finally:
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())
