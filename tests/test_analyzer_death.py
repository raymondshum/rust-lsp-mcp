"""Fast tests for rust-analyzer process-death detection (SW-06, SW-L01).

No live analyzer, no network.  The fakes extend ``FakeLsp`` from
``test_lifecycle_races.py`` (which mimics multilspy's ``start_server()``,
including the absence of a try/finally around its ``yield``) with a process
handle whose ``wait()`` resolves when the test says the process died.

Coverage:
    - A ``ready`` manager whose process exits flips to ``state == "error"``
      with the exit code in ``error_message`` and ``analyzer_exit_code``;
      ``status`` reports both; a gated tool returns the ``refresh`` envelope
      with the dead-analyzer message; ``lsp_failure`` defers to that envelope.
    - A process that dies before quiescence ends in ``state == "error"``
      instead of ``indexing``, and the message carries the process's stderr
      (a real subprocess read by the replacement stderr reader).
    - A normal shutdown is not a death.
    - The death clears ``analyzer_health``, ``analyzer_health_message`` and
      the watcher fields (backlog line 84).
    - ``restart()`` clears ``analyzer_exit_code``, ``analyzer_health`` and
      ``analyzer_health_message``, so a new run that fails before its process
      starts reports "failed to start", not the old exit code.
    - A ``refresh`` right after an undetected death (backlog line 54) skips
      multilspy's ``shutdown`` request, or stops waiting for its reply when
      the process dies under it, without a drain timeout or a recorded death;
      a healthy restart runs multilspy's exit sequence unchanged.
"""

import asyncio
import collections
import contextlib
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from rust_lsp_mcp import analyzer as analyzer_module
from rust_lsp_mcp.analyzer import STATE_ERROR, STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import RECOVERY_REFRESH
from tests.test_lifecycle_races import (
    FakeLsp,
    _await_first_instance,
    _cancel_leaked_tasks,
    _factory,
    _make_manager,
    _no_git,  # noqa: F401 — autouse fixture, must be in this module's namespace
    _wait_until,
)


class FakeProcess:
    """Stand-in for an ``asyncio.subprocess.Process`` the test can kill."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stderr = None
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def die(self, code: int) -> None:
        self.returncode = code
        self._exited.set()

    def terminate(self) -> None:
        if self.returncode is None:
            self.die(-15)


class DyingLsp(FakeLsp):
    """Sets a ``FakeProcess`` on ``server.process`` before waiting for quiescence."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.process = FakeProcess()

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["DyingLsp"]:
        self.server.process = self.process  # ty: ignore[invalid-assignment]
        self.entered.set()
        await self.quiesce_gate.wait()
        yield self
        await self.server.shutdown()
        await self.server.stop()
        self.exited = True


class ReadyDyingLsp(DyingLsp):
    """``DyingLsp`` that reaches quiescence at once."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.quiesce_gate.set()


class RealStderrDyingLsp(FakeLsp):
    """Spawns a real process that writes to stderr and exits 101 before quiescence.

    Mirrors multilspy's ``LanguageServerHandler.start()``: assigns
    ``server.process`` and then spawns ``server.run_forever_stderr()`` as a
    task (``lsp_protocol_handler/server.py`` lines 217 and 230), which
    ``_run`` has rebound to its own reader.
    """

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["RealStderrDyingLsp"]:
        script = (
            "import sys\n"
            "for i in range(30):\n"
            "    print(f'line {i}', file=sys.stderr)\n"
            "print('thread main panicked: fatal', file=sys.stderr)\n"
            "sys.exit(101)\n"
        )
        self.spawned_process = self.server.process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script, stderr=asyncio.subprocess.PIPE
        )
        self.stderr_task = asyncio.create_task(self.server.run_forever_stderr())  # ty: ignore[unresolved-attribute]
        self.entered.set()
        await self.quiesce_gate.wait()  # never set: the process dies first
        yield self


class StartFailureLsp(FakeLsp):
    """Fails before any process exists: an exception out of the entry before
    ``server.start()`` assigns ``process``.  (A missing binary is not this case:
    multilspy launches through ``/bin/sh -c``, so it is a shell exiting 127.)"""

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["StartFailureLsp"]:
        self.entered.set()
        raise FileNotFoundError("rust-analyzer binary not found")
        yield self  # pragma: no cover - unreachable, keeps this an async generator


async def _settle(mgr: Any) -> None:
    """Await the background task, swallowing its own exception."""
    assert mgr._task is not None
    with contextlib.suppress(Exception):
        await asyncio.wait_for(mgr._task, 5)


def _status(mgr: Any) -> dict[str, Any]:
    import rust_lsp_mcp.core as core_mod
    import rust_lsp_mcp.tools.status as status_mod

    with (
        patch.object(core_mod, "_manager", mgr),
        patch.object(status_mod, "doc_store_state", return_value=("building", None)),
    ):
        return asyncio.run(status_mod.status())


def _find_symbol(mgr: Any) -> dict[str, Any]:
    import rust_lsp_mcp.core as core_mod
    from rust_lsp_mcp.tools.find_symbol import find_symbol

    with patch.object(core_mod, "_manager", mgr):
        return asyncio.run(find_symbol("search"))


@pytest.fixture(autouse=True)
def _fast_stderr_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyzer_module, "STDERR_DRAIN_SECONDS", 0.5)
    monkeypatch.setattr(analyzer_module, "PROCESS_POLL_SECONDS", 0.01)


class TestDeathAfterReady:
    def test_ready_manager_flips_to_error_on_process_exit(self) -> None:
        instances: list[ReadyDyingLsp] = []

        async def _scenario() -> Any:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module, "PatchedRustAnalyzer", _factory(ReadyDyingLsp, instances)
                )
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await _wait_until(lambda: mgr.state == STATE_READY)
                assert mgr.analyzer_exit_code is None
                await mgr._make_status_handler(mgr._generation)("ok", None, True)
                assert mgr.analyzer_health == "ok"
                assert mgr.watcher_health == "ok"

                lsp.process.die(137)
                await _settle(mgr)

                assert mgr.state == STATE_ERROR
                assert mgr.analyzer_exit_code == 137
                # Backlog line 84: a dead process has no health report.
                assert mgr.analyzer_health is None
                assert mgr.analyzer_health_message is None
                assert mgr.watcher_health is None
                assert mgr.watcher_message is None
                assert mgr.error_message is not None
                assert mgr.error_message.startswith("rust-analyzer exited with code 137")
                assert "last stderr:" in mgr.error_message
                assert mgr._lsp is None
                assert not mgr.is_ready
                # Left the context by exception: no shutdown request to a
                # dead process, but the force-stop still ran.
                assert lsp.exited is False
                assert lsp.server.stop_calls >= 1
                await _cancel_leaked_tasks()
                return mgr

        mgr = asyncio.run(_scenario())

        status = _status(mgr)
        assert status["status"] == "ok"
        assert status["state"] == "error"
        assert status["analyzer_exit_code"] == 137
        assert "exited with code 137" in status["analyzer_error"]

        result = _find_symbol(mgr)
        assert result["status"] == "error"
        assert result["recovery"] == RECOVERY_REFRESH
        assert result["message"] == (
            "rust-analyzer is not running (exited with code 137); call refresh"
        )

    def test_lsp_failure_defers_to_dead_analyzer_envelope(self) -> None:
        import rust_lsp_mcp.core as core_mod
        from rust_lsp_mcp.envelope import lsp_failure

        mgr = _make_manager()
        mgr.state = STATE_ERROR
        mgr.analyzer_exit_code = 9
        mgr._error = "rust-analyzer exited with code 9; last stderr: (none)"
        with patch.object(core_mod, "_manager", mgr):
            result = lsp_failure(RuntimeError("broken pipe"))
        assert result["recovery"] == RECOVERY_REFRESH
        assert "exited with code 9" in result["message"]
        assert "still running" not in result["message"]

    def test_lsp_failure_keeps_text_for_live_analyzer(self) -> None:
        import rust_lsp_mcp.core as core_mod
        from rust_lsp_mcp.envelope import lsp_failure

        mgr = _make_manager()
        mgr.state = STATE_READY
        with patch.object(core_mod, "_manager", mgr):
            result = lsp_failure(RuntimeError("glitch"))
        assert "the analyzer is still running. Retry the request once." in result["message"]

    def test_in_flight_request_ends_on_death(self) -> None:
        """A request awaiting a reply from the dead process must not hang."""
        instances: list[ReadyDyingLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module, "PatchedRustAnalyzer", _factory(ReadyDyingLsp, instances)
                )
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await _wait_until(lambda: mgr.state == STATE_READY)

                never: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

                async def _hang(*args: Any) -> Any:
                    return await never

                lsp.request_hover = _hang
                request = asyncio.create_task(mgr.request_hover("src/lib.rs", 0, 0))
                await asyncio.sleep(0)
                lsp.process.die(1)
                with pytest.raises(analyzer_module.AnalyzerTornDownError):
                    await asyncio.wait_for(request, 5)
                await _settle(mgr)
                assert mgr.state == STATE_ERROR
                await _cancel_leaked_tasks()

        asyncio.run(_scenario())

    @pytest.mark.parametrize(
        ("tool", "lsp_method", "args"),
        [
            ("find_symbol", "request_workspace_symbol", {"name": "search"}),
            ("hover", "request_hover", {"file": "src/lib.rs", "line": 1, "character": 1}),
            (
                "goto_definition",
                "request_definition",
                {"file": "src/lib.rs", "line": 1, "character": 1},
            ),
            (
                "find_references",
                "request_references",
                {"file": "src/lib.rs", "line": 1, "character": 1},
            ),
            ("document_symbols", "request_document_symbols", {"file": "src/lib.rs"}),
        ],
    )
    def test_in_flight_tool_call_at_death_points_to_refresh(
        self, tool: str, lsp_method: str, args: dict[str, Any]
    ) -> None:
        """R-gate break 2: a tool call in flight when rust-analyzer dies must
        return the refresh envelope, not not_ready/poll_status."""
        import importlib

        import rust_lsp_mcp.core as core_mod

        tool_fn = getattr(importlib.import_module(f"rust_lsp_mcp.tools.{tool}"), tool)
        instances: list[ReadyDyingLsp] = []

        async def _scenario() -> tuple[dict[str, Any], str]:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module, "PatchedRustAnalyzer", _factory(ReadyDyingLsp, instances)
                )
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await _wait_until(lambda: mgr.state == STATE_READY)

                never: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

                async def _hang(*a: Any) -> Any:
                    return await never

                setattr(lsp, lsp_method, _hang)
                with patch.object(core_mod, "_manager", mgr):
                    call = asyncio.create_task(tool_fn(**args))
                    await asyncio.sleep(0.05)
                    lsp.process.die(137)
                    result = await asyncio.wait_for(call, 5)
                await _settle(mgr)
                state = mgr.state
                await _cancel_leaked_tasks()
                return result, state

        result, state = asyncio.run(_scenario())
        assert state == STATE_ERROR
        assert result["status"] == "error", result
        assert result["recovery"] == RECOVERY_REFRESH, result
        assert result["message"] == (
            "rust-analyzer is not running (exited with code 137); call refresh"
        )

    def test_normal_shutdown_is_not_a_death(self) -> None:
        instances: list[ReadyDyingLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module, "PatchedRustAnalyzer", _factory(ReadyDyingLsp, instances)
                )
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await _wait_until(lambda: mgr.state == STATE_READY)
                await mgr.shutdown()
                assert lsp.exited is True
                assert mgr.analyzer_exit_code is None
                assert mgr.error_message is None
                assert mgr.state != STATE_ERROR

        asyncio.run(_scenario())


class TestDeathBeforeReady:
    def test_death_before_quiescence_ends_in_error(self, caplog: pytest.LogCaptureFixture) -> None:
        instances: list[RealStderrDyingLsp] = []
        caplog.set_level(logging.DEBUG, logger="rust_lsp_mcp.analyzer")

        async def _scenario() -> Any:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module,
                    "PatchedRustAnalyzer",
                    _factory(RealStderrDyingLsp, instances),
                )
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await asyncio.wait_for(lsp.entered.wait(), 2)
                assert mgr.state == STATE_INDEXING
                await _settle(mgr)
                with contextlib.suppress(Exception):
                    await lsp.stderr_task
                await _cancel_leaked_tasks()
                return mgr

        mgr = asyncio.run(_scenario())

        assert mgr.state == STATE_ERROR
        assert mgr.analyzer_exit_code == 101
        message = mgr.error_message
        assert message is not None
        assert message.startswith("rust-analyzer exited with code 101; last stderr: ")
        assert "thread main panicked: fatal" in message
        # Only the last 20 of the 31 stderr lines go into the message.
        assert "line 10\n" not in message
        assert "line 11" in message
        assert len(mgr._stderr_lines) == 31
        stderr_records = [
            r for r in caplog.records if r.getMessage().startswith("rust-analyzer stderr: ")
        ]
        assert any(
            r.getMessage() == "rust-analyzer stderr: thread main panicked: fatal"
            for r in stderr_records
        )
        # R-gate item 5: stderr lines log at debug (RA_LOG=info floods info).
        assert stderr_records
        assert {r.levelno for r in stderr_records} == {logging.DEBUG}
        assert _status(mgr)["state"] == "error"

    def test_death_before_quiescence_with_fake_process(self) -> None:
        instances: list[DyingLsp] = []

        async def _scenario() -> Any:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(DyingLsp, instances))
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await asyncio.wait_for(lsp.entered.wait(), 2)
                lsp.process.die(2)
                await _settle(mgr)
                await _cancel_leaked_tasks()
                return mgr

        mgr = asyncio.run(_scenario())
        assert mgr.state == STATE_ERROR
        assert mgr.analyzer_exit_code == 2
        assert mgr.error_message == "rust-analyzer exited with code 2; last stderr: (none)"


class TestRestartClearsDeathFields:
    def test_restart_resets_exit_code_and_health(self) -> None:
        dying: list[ReadyDyingLsp] = []
        failing: list[StartFailureLsp] = []

        async def _scenario() -> Any:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(ReadyDyingLsp, dying))
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(dying)
                await _wait_until(lambda: mgr.state == STATE_READY)
                await mgr._make_status_handler(mgr._generation)(
                    "error", "workspace load failed", True
                )
                assert mgr.analyzer_health == "error"
                lsp.process.die(137)
                await _settle(mgr)
                assert mgr.analyzer_exit_code == 137
                # The death clears the health of the dead process.
                assert mgr.analyzer_health is None
                assert mgr.analyzer_health_message is None

                mp.setattr(
                    analyzer_module, "PatchedRustAnalyzer", _factory(StartFailureLsp, failing)
                )
                await mgr.restart()
                await _settle(mgr)
                await _cancel_leaked_tasks()
                return mgr

        mgr = asyncio.run(_scenario())
        assert mgr.state == STATE_ERROR
        assert mgr.analyzer_exit_code is None
        assert mgr.analyzer_health is None
        assert mgr.analyzer_health_message is None
        assert mgr.error_message is not None
        assert "binary not found" in mgr.error_message

        result = _find_symbol(mgr)
        assert result["status"] == "error"
        assert result["recovery"] == RECOVERY_REFRESH
        assert "failed to start" in result["message"]
        assert "is not running" not in result["message"]


class HangingShutdownHandler:
    """``lsp.server`` whose ``shutdown`` request never gets a reply.

    Mirrors multilspy's ``shutdown()`` sent to a dead rust-analyzer: nothing
    answers, so the request waits until the drain cancels it.
    """

    def __init__(self) -> None:
        self.process: FakeProcess | None = None
        self.stop_calls = 0
        self.shutdown_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1
        self.process = None

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        await asyncio.Event().wait()


class ReadyHangingShutdownLsp(ReadyDyingLsp):
    """``ReadyDyingLsp`` whose ``server.shutdown()`` hangs."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.server = HangingShutdownHandler()


class DiesOnShutdownHandler(HangingShutdownHandler):
    """The process dies once the ``shutdown`` request is sent; no reply ever comes.

    The live case of backlog line 54: the ``refresh`` lands before the
    ``/bin/sh -c`` wrapper has exited, so ``returncode`` reads None at the
    ``_hold_ready`` check and the request goes to a dying process.
    """

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        assert self.process is not None
        self.process.die(137)
        await asyncio.Event().wait()


class ReadyDiesOnShutdownLsp(ReadyDyingLsp):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.server = DiesOnShutdownHandler()


class HealthyExitHandler:
    """multilspy's healthy shutdown: the reply arrives, then the process exits.

    ``shutdown`` sets ``_received_shutdown`` (as multilspy does after the
    reply) and the process exits after the ``exit`` notification, before
    ``stop()`` finishes its cleanup sleep, so the exit watch fires while
    the context exit is still running.
    """

    def __init__(self) -> None:
        self.process: FakeProcess | None = None
        self.stop_calls = 0
        self.shutdown_calls = 0
        self._received_shutdown = False

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._received_shutdown = True
        assert self.process is not None
        self.process.die(0)
        await asyncio.sleep(0)

    async def stop(self) -> None:
        self.stop_calls += 1
        self.process = None
        await asyncio.sleep(0.05)


class ReadyHealthyExitLsp(ReadyDyingLsp):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.server = HealthyExitHandler()


def _pending_tasks() -> list[asyncio.Task]:
    current = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not current and not t.done()]


class TestRefreshAfterUndetectedDeath:
    def test_restart_skips_shutdown_request_to_dead_process(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Backlog line 54: a ``refresh`` inside the ``EXIT_POLL_SECONDS``
        window after a death must not send multilspy's ``shutdown`` to the
        dead process and wait out the drain timeout."""
        instances: list[ReadyHangingShutdownLsp] = []

        async def _scenario() -> tuple[Any, float]:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "DRAIN_TIMEOUT_SECONDS", 2.0)
                # The exit watch reads returncode only every 30 s, so it has
                # not fired when the restart below arrives.
                mp.setattr(analyzer_module, "EXIT_POLL_SECONDS", 30.0)
                mp.setattr(
                    analyzer_module,
                    "PatchedRustAnalyzer",
                    _factory(ReadyHangingShutdownLsp, instances),
                )
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await _wait_until(lambda: mgr.state == STATE_READY)
                # Dead, but wait() has not resolved (a child still holds the
                # pipe) and the exit watch has not polled yet.
                lsp.process.returncode = 137
                started = asyncio.get_running_loop().time()
                await mgr.restart()
                elapsed = asyncio.get_running_loop().time() - started
                assert lsp.server.shutdown_calls == 0
                assert lsp.server.stop_calls >= 1
                # A requested shutdown is not a death.
                assert mgr.analyzer_exit_code is None
                assert mgr.state != STATE_ERROR
                await mgr.shutdown()
                await _cancel_leaked_tasks()
                return mgr, elapsed

        with caplog.at_level(logging.INFO, logger="rust_lsp_mcp.analyzer"):
            _mgr, elapsed = asyncio.run(_scenario())
        assert elapsed < 1.0, f"restart took {elapsed:.2f} s"
        messages = [r.getMessage() for r in caplog.records]
        assert not any("drain timed out" in m for m in messages)
        assert any(
            "rust-analyzer had already exited (code 137) at shutdown; "
            "skipping the shutdown request" in m
            for m in messages
        )

    def test_restart_after_death_during_shutdown_request(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The process dies after the ``_hold_ready`` check, under multilspy's
        ``shutdown`` request: the exit watch ends the wait for the reply."""
        instances: list[ReadyDiesOnShutdownLsp] = []

        async def _scenario() -> tuple[Any, float, list[asyncio.Task]]:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "DRAIN_TIMEOUT_SECONDS", 2.0)
                mp.setattr(
                    analyzer_module,
                    "PatchedRustAnalyzer",
                    _factory(ReadyDiesOnShutdownLsp, instances),
                )
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await _wait_until(lambda: mgr.state == STATE_READY)
                started = asyncio.get_running_loop().time()
                await mgr.restart()
                elapsed = asyncio.get_running_loop().time() - started
                assert lsp.server.shutdown_calls == 1
                # The finally force-stop still ran after the cancelled exit.
                assert lsp.server.stop_calls >= 1
                assert lsp.exited is False
                assert mgr.analyzer_exit_code is None
                assert mgr.state != STATE_ERROR
                await mgr.shutdown()
                pending = _pending_tasks()
                await _cancel_leaked_tasks()
                return mgr, elapsed, pending

        with caplog.at_level(logging.INFO, logger="rust_lsp_mcp.analyzer"):
            _mgr, elapsed, pending = asyncio.run(_scenario())
        assert elapsed < 1.0, f"restart took {elapsed:.2f} s"
        assert pending == []
        messages = [r.getMessage() for r in caplog.records]
        assert not any("drain timed out" in m for m in messages)
        assert any(
            "rust-analyzer exited (code 137) during the shutdown request" in m for m in messages
        )
        assert not any(m.startswith("AnalyzerManager: rust-analyzer exited with") for m in messages)

    def test_healthy_restart_is_unchanged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Healthy path: rust-analyzer exits on its own after the reply, so the
        exit watch can fire first; the context exit still runs to completion."""
        instances: list[ReadyHealthyExitLsp] = []

        async def _scenario() -> tuple[float, list[asyncio.Task], Any]:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "DRAIN_TIMEOUT_SECONDS", 2.0)
                mp.setattr(
                    analyzer_module,
                    "PatchedRustAnalyzer",
                    _factory(ReadyHealthyExitLsp, instances),
                )
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await _wait_until(lambda: mgr.state == STATE_READY)
                started = asyncio.get_running_loop().time()
                await mgr.restart()
                elapsed = asyncio.get_running_loop().time() - started
                assert lsp.server.shutdown_calls == 1
                # multilspy's own exit sequence completed (not cancelled).
                assert lsp.exited is True
                assert lsp.server.stop_calls >= 1
                assert mgr.analyzer_exit_code is None
                await mgr.shutdown()
                pending = _pending_tasks()
                await _cancel_leaked_tasks()
                return elapsed, pending, mgr

        with caplog.at_level(logging.INFO, logger="rust_lsp_mcp.analyzer"):
            elapsed, pending, _mgr = asyncio.run(_scenario())
        assert elapsed < 1.0, f"restart took {elapsed:.2f} s"
        assert pending == []
        messages = [r.getMessage() for r in caplog.records]
        assert not any("drain timed out" in m for m in messages)
        assert not any("during the shutdown request" in m for m in messages)
        assert not any("had already exited" in m for m in messages)


class TestMultilspyShutdownFlag:
    def test_received_shutdown_is_set_by_the_shutdown_reply_before_exit(self) -> None:
        """``_exit_server_context`` reads multilspy's private
        ``LanguageServerHandler._received_shutdown`` (through ``getattr``,
        default False) to tell a healthy exit from a death under the
        ``shutdown`` request.  A rename in a multilspy upgrade would send every
        healthy restart down the cancel path, so pin the attribute on a real
        handler (built without starting a process): it starts False, and
        ``shutdown()`` sets it once the request's reply returns and before the
        ``exit`` notification goes out."""
        from multilspy.lsp_protocol_handler.server import (
            LanguageServerHandler,
            ProcessLaunchInfo,
        )

        handler = LanguageServerHandler(ProcessLaunchInfo(cmd="true"))
        assert handler.process is None
        assert handler._received_shutdown is False
        flag_at_exit: list[bool] = []

        async def _reply() -> None:
            assert handler._received_shutdown is False
            return None

        def _exit() -> None:
            flag_at_exit.append(handler._received_shutdown)

        handler.send.shutdown = _reply  # ty: ignore[invalid-assignment]
        handler.notify.exit = _exit  # ty: ignore[invalid-assignment]
        asyncio.run(handler.shutdown())
        assert handler._received_shutdown is True
        assert flag_at_exit == [True]


class TestExitDetection:
    def test_process_exit_is_seen_while_a_child_still_holds_stderr(self) -> None:
        """R-gate break 3: rust-analyzer's proc-macro-srv child inherits its
        stderr pipe.  On CPython 3.12 ``Process.wait()`` resolves only after
        every pipe closes, so a child that outlives rust-analyzer hid the death
        (``status`` read ``ready`` 25 s after a kill).  The shell here exits 3
        while a background ``sleep`` keeps the pipes open."""

        async def _scenario() -> int | None:
            proc = await asyncio.create_subprocess_shell(
                "sleep 30 & exit 3",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            lsp = SimpleNamespace(server=SimpleNamespace(process=proc))
            try:
                return await asyncio.wait_for(
                    AnalyzerManager._await_process_exit(lsp),  # ty: ignore[invalid-argument-type]
                    3,
                )
            except TimeoutError:
                return None
            finally:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)

        code = asyncio.run(_scenario())
        assert code == 3, "process exit not reported while a child still held the stderr pipe"

    def test_shell_exit_127_reads_as_binary_not_found(self) -> None:
        """R-gate item 6: multilspy launches via ``/bin/sh -c``, so a missing
        binary is the shell exiting 127 and takes the death path."""
        instances: list[DyingLsp] = []

        async def _scenario() -> Any:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(DyingLsp, instances))
                mgr = _make_manager()
                await mgr.start()
                lsp = await _await_first_instance(instances)
                await asyncio.wait_for(lsp.entered.wait(), 2)
                lsp.process.die(127)
                await _settle(mgr)
                await _cancel_leaked_tasks()
                return mgr

        mgr = asyncio.run(_scenario())
        assert mgr.state == STATE_ERROR
        assert mgr.analyzer_exit_code == 127
        assert mgr.error_message is not None
        assert mgr.error_message.startswith("rust-analyzer binary not found")
        result = _find_symbol(mgr)
        assert result["status"] == "error"
        assert "binary not found" in result["message"]
        assert "RLM_RUST_ANALYZER_BIN" in result["message"]


class TestStderrReader:
    def test_stderr_capture_survives_a_line_longer_than_the_stream_limit(self) -> None:
        """R-gate item 5: ``readline()`` raises ValueError on a line past the
        64 KiB stream limit; the reader must keep draining."""
        script = (
            "import sys\n"
            "sys.stderr.write('x' * 70000 + '\\n')\n"
            "for i in range(5):\n"
            "    sys.stderr.write(f'after {i}\\n')\n"
            "sys.stderr.flush()\n"
        )

        async def _scenario() -> list[str]:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", script, stderr=asyncio.subprocess.PIPE
            )
            lsp = SimpleNamespace(server=SimpleNamespace(process=proc))
            mgr = AnalyzerManager(rust_analyzer_bin="/fake", repository_root="/fake")
            lines: collections.deque[str] = collections.deque(maxlen=200)
            started, done = asyncio.Event(), asyncio.Event()
            reader = mgr._make_stderr_reader(mgr._generation, lsp, lines, started, done)  # ty: ignore[invalid-argument-type]
            task = asyncio.create_task(reader())
            await proc.wait()
            await asyncio.wait_for(done.wait(), 5)
            exc = task.exception() if task.done() and not task.cancelled() else None
            assert exc is None, f"stderr reader died with {exc!r}"
            return list(lines)

        lines = asyncio.run(_scenario())
        assert [ln for ln in lines if ln.startswith("after ")] == [f"after {i}" for i in range(5)]
