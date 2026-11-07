"""Fast tests for rust-analyzer stall detection (FW1, backlog line 150).

Two signals mark a stall (``AnalyzerManager.stall_report``):

- the panic signal: a cache-priming worker (``PrimeCaches#<n>``) panic line
  on rust-analyzer's stderr since the last ``quiescent: true``;
- the timer signal: a re-index token in flight with no progress for longer
  than ``stall_threshold_seconds`` while ``quiescent`` reads ``false``.

Panic lines and their backtrace block log at WARNING (P2-7), and every
analyzer request is bounded by ``request_timeout_seconds`` in
``_race_teardown`` (P2-6).

No live analyzer: fake stderr lines go through the real reader, fake
progress and status notifications through the real handlers, and the stall
timer reads the module's ``_monotonic`` seam.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

import pytest

import rust_lsp_mcp.analyzer as analyzer_mod
from rust_lsp_mcp.analyzer import (
    PANIC_LOG_MAX_LINES,
    STATE_READY,
    AnalyzerManager,
    AnalyzerRequestTimeoutError,
    AnalyzerTornDownError,
    _is_prime_caches_panic_line,
)
from tests.test_analyzer_watcher_health import _feed

# The FW1 panic as rust-analyzer 1.99.1 printed it (NOTES.md § 4.1 step 2).
PRIME_PANIC = (
    "thread 'PrimeCaches#1' (9085) panicked at "
    "src/tools/rust-analyzer/crates/vfs/src/file_set.rs:31:34:"
)
WORKER_PANIC = (
    "thread 'Worker' (9100) panicked at src/tools/rust-analyzer/crates/vfs/src/file_set.rs:31:34:"
)
PANIC_MESSAGE = "no entry found for key"
BACKTRACE = [
    "stack backtrace:",
    "   0: rust_begin_unwind",
    "   3: vfs::file_set::FileSet::resolve_path",
    "   4: hir_def::nameres::collector::ModCollector::collect",
]
OTHER_LINE = "2026-11-06T01:30:00.000000Z  INFO some unrelated rust-analyzer line"

CACHE_PRIMING = "rustAnalyzer/cachePriming"
PRIMING_BEGIN = {
    "token": CACHE_PRIMING,
    "value": {"kind": "begin", "title": "Indexing", "message": "0/64"},
}
PRIMING_REPORT = {"token": CACHE_PRIMING, "value": {"kind": "report", "message": "63/64 (rg)"}}
PRIMING_END = {"token": CACHE_PRIMING, "value": {"kind": "end"}}
FLYCHECK_BEGIN = {
    "token": "rust-analyzer/flycheck/0",
    "value": {"kind": "begin", "title": "cargo check"},
}


class _Clock:
    """A settable stand-in for ``time.monotonic``."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Clock]:
    fake = _Clock()
    monkeypatch.setattr(analyzer_mod, "_monotonic", fake)
    yield fake


def _manager() -> AnalyzerManager:
    mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root="/nonexistent")
    mgr.state = STATE_READY
    return mgr


def _events(mgr: AnalyzerManager, events: list[Any]) -> None:
    """Feed *events*: a dict is a ``$/progress`` param, a bool a quiescent report."""
    progress = mgr._make_progress_handler(mgr._generation)
    status = mgr._make_status_handler(mgr._generation)

    async def _scenario() -> None:
        for event in events:
            if isinstance(event, bool):
                await status("ok", None, event)
            else:
                await progress(event)

    asyncio.run(_scenario())


def _panic_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and r.getMessage().startswith("rust-analyzer panic: ")
    ]


def _noop_restart(mgr: AnalyzerManager) -> None:
    async def _noop_start() -> None:
        return None

    mgr.start = _noop_start  # ty: ignore[invalid-assignment]
    asyncio.run(mgr.restart())


# ---------------------------------------------------------------------------
# The panic signal and the panic log (P2-2 first signal, P2-7)
# ---------------------------------------------------------------------------


def test_predicate_tells_priming_panic_apart() -> None:
    assert _is_prime_caches_panic_line(PRIME_PANIC)
    assert not _is_prime_caches_panic_line(WORKER_PANIC)
    assert not _is_prime_caches_panic_line("thread 'PrimeCaches#1' started")
    assert not _is_prime_caches_panic_line(OTHER_LINE)


def test_prime_caches_panic_sets_signal_and_logs_block(caplog: pytest.LogCaptureFixture) -> None:
    mgr = _manager()
    lines = [OTHER_LINE, PRIME_PANIC, PANIC_MESSAGE, *BACKTRACE, "", OTHER_LINE]
    with caplog.at_level(logging.DEBUG, logger="rust_lsp_mcp.analyzer"):
        _feed(mgr, mgr._generation, lines)
    report = mgr.stall_report()
    assert report is not None
    assert report["signal"] == "panic"
    assert report["since"] == mgr._stall_panic_at
    assert "PrimeCaches#1" in report["message"]
    assert "refresh" in report["message"]
    assert len(report["message"]) < 400
    assert mgr._stall_panic_thread == "PrimeCaches#1"
    assert mgr._stall_panic_line == PRIME_PANIC
    # The panic line and its block up to the blank line log at WARNING.
    assert _panic_warnings(caplog) == [
        f"rust-analyzer panic: {line}" for line in [PRIME_PANIC, PANIC_MESSAGE, *BACKTRACE]
    ]
    # Block lines do not also log at DEBUG; the lines outside it do.
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert f"rust-analyzer stderr: {PRIME_PANIC}" not in debug
    assert f"rust-analyzer stderr: {PANIC_MESSAGE}" not in debug
    assert debug.count(f"rust-analyzer stderr: {OTHER_LINE}") == 2


def test_panic_block_capped_at_max_lines(caplog: pytest.LogCaptureFixture) -> None:
    mgr = _manager()
    frames = [f"  {n}: frame" for n in range(PANIC_LOG_MAX_LINES + 10)]
    with caplog.at_level(logging.DEBUG, logger="rust_lsp_mcp.analyzer"):
        _feed(mgr, mgr._generation, [PRIME_PANIC, *frames])
    warnings = _panic_warnings(caplog)
    assert len(warnings) == PANIC_LOG_MAX_LINES  # the panic line counts
    assert warnings[0] == f"rust-analyzer panic: {PRIME_PANIC}"
    assert warnings[-1] == f"rust-analyzer panic: {frames[PANIC_LOG_MAX_LINES - 2]}"
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert f"rust-analyzer stderr: {frames[-1]}" in debug


def test_every_line_still_reaches_stderr_tail() -> None:
    mgr = _manager()
    lines = [PRIME_PANIC, PANIC_MESSAGE, *BACKTRACE]
    from collections import deque
    from types import SimpleNamespace

    from tests.test_analyzer_watcher_health import _FakeStderr

    tail: deque[str] = deque(maxlen=200)
    process = SimpleNamespace(stderr=_FakeStderr(lines))
    lsp = SimpleNamespace(server=SimpleNamespace(process=process))

    async def _scenario() -> None:
        reader = mgr._make_stderr_reader(
            mgr._generation,
            lsp,  # ty: ignore[invalid-argument-type]
            tail,
            asyncio.Event(),
            asyncio.Event(),
        )
        await reader()

    asyncio.run(_scenario())
    assert list(tail) == lines


def test_worker_panic_logs_warning_and_sets_no_signal(caplog: pytest.LogCaptureFixture) -> None:
    mgr = _manager()
    with caplog.at_level(logging.WARNING, logger="rust_lsp_mcp.analyzer"):
        _feed(mgr, mgr._generation, [WORKER_PANIC, PANIC_MESSAGE, ""])
    assert _panic_warnings(caplog) == [
        f"rust-analyzer panic: {WORKER_PANIC}",
        f"rust-analyzer panic: {PANIC_MESSAGE}",
    ]
    assert mgr.stall_report() is None
    assert mgr._stall_panic_at is None


def test_first_priming_panic_is_kept() -> None:
    mgr = _manager()
    second = PRIME_PANIC.replace("PrimeCaches#1", "PrimeCaches#5")
    _feed(mgr, mgr._generation, [PRIME_PANIC, "", second, ""])
    assert mgr._stall_panic_thread == "PrimeCaches#1"
    assert mgr._stall_panic_line == PRIME_PANIC


def test_quiescent_true_clears_panic_signal() -> None:
    mgr = _manager()
    _feed(mgr, mgr._generation, [PRIME_PANIC])
    assert mgr.stall_report() is not None
    _events(mgr, [False])
    assert mgr.stall_report() is not None  # quiescent: false does not clear it
    _events(mgr, [True])
    assert mgr.stall_report() is None


def test_superseded_generation_records_nothing(caplog: pytest.LogCaptureFixture) -> None:
    mgr = _manager()
    old_gen = mgr._generation
    mgr._generation += 1
    with caplog.at_level(logging.DEBUG, logger="rust_lsp_mcp.analyzer"):
        _feed(mgr, old_gen, [PRIME_PANIC, PANIC_MESSAGE])
    assert mgr.stall_report() is None
    assert _panic_warnings(caplog) == []
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert f"rust-analyzer stderr: {PRIME_PANIC}" in debug


def test_restart_clears_both_signals(clock: _Clock) -> None:
    mgr = _manager()
    _feed(mgr, mgr._generation, [PRIME_PANIC])
    _events(mgr, [PRIMING_BEGIN, False])
    clock.now += mgr.stall_threshold_seconds + 1
    assert mgr._progress_timed_out()
    assert mgr.stall_report() is not None
    _noop_restart(mgr)
    assert mgr._stall_panic_at is None
    assert not mgr._progress_timed_out()  # restart reset quiescent to None
    assert mgr.stall_report() is None


def test_run_resets_stall_fields() -> None:
    """``_run``'s top resets the panic signal and the progress clock."""
    mgr = _manager()
    _feed(mgr, mgr._generation, [PRIME_PANIC])
    mgr._progress_monotonic = 5.0

    async def _scenario() -> None:
        async def _stop() -> None:
            mgr._generation += 1  # supersede so _run returns before spawning

        mgr._capture_head_commit = _stop  # ty: ignore[invalid-assignment]
        await mgr._run(mgr._generation)

    asyncio.run(_scenario())
    assert mgr._stall_panic_at is None
    assert mgr._progress_monotonic is None


def test_new_stub_reads_stall_class_defaults() -> None:
    mgr: Any = AnalyzerManager.__new__(AnalyzerManager)
    assert mgr.stall_report() is None
    assert mgr.quiescent is None
    assert mgr.reindex_inflight == []
    assert mgr.stall_threshold_seconds == 180
    assert mgr.request_timeout_seconds == 120


# ---------------------------------------------------------------------------
# The timer signal (P2-2 second signal)
# ---------------------------------------------------------------------------


def test_progress_timeout_fires_past_threshold(clock: _Clock) -> None:
    mgr = _manager()
    _events(mgr, [PRIMING_BEGIN, PRIMING_REPORT])
    mgr._quiescent = False
    clock.now += mgr.stall_threshold_seconds  # not past yet
    assert mgr.stall_report() is None
    clock.now += 1
    report = mgr.stall_report()
    assert report is not None
    assert report["signal"] == "progress_timeout"
    assert report["since"] == mgr.progress_at
    assert "Indexing 63/64 (rg)" in report["message"]
    assert "refresh" in report["message"]
    assert len(report["message"]) < 400


def test_further_progress_clears_timer(clock: _Clock) -> None:
    mgr = _manager()
    _events(mgr, [PRIMING_BEGIN])
    mgr._quiescent = False
    clock.now += mgr.stall_threshold_seconds + 1
    assert mgr.stall_report() is not None
    _events(mgr, [PRIMING_REPORT])
    assert mgr.stall_report() is None


def test_quiescent_true_clears_timer(clock: _Clock) -> None:
    mgr = _manager()
    _events(mgr, [PRIMING_BEGIN, False])
    clock.now += mgr.stall_threshold_seconds + 1
    assert mgr.stall_report() is not None
    _events(mgr, [True])
    assert mgr.stall_report() is None


def test_threshold_zero_never_fires_timer(clock: _Clock) -> None:
    mgr = _manager()
    mgr.stall_threshold_seconds = 0
    _events(mgr, [PRIMING_BEGIN, False])
    clock.now += 100_000
    assert mgr.stall_report() is None


def test_non_reindex_token_alone_never_fires_timer(clock: _Clock) -> None:
    mgr = _manager()
    _events(mgr, [FLYCHECK_BEGIN, False])
    clock.now += mgr.stall_threshold_seconds + 1
    assert mgr.reindex_inflight == []
    assert mgr.stall_report() is None


def test_ended_token_never_fires_timer(clock: _Clock) -> None:
    mgr = _manager()
    _events(mgr, [PRIMING_BEGIN, PRIMING_END, False])
    clock.now += mgr.stall_threshold_seconds + 1
    assert mgr.stall_report() is None


def test_panic_wins_over_timer(clock: _Clock) -> None:
    mgr = _manager()
    _events(mgr, [PRIMING_BEGIN, False])
    clock.now += mgr.stall_threshold_seconds + 1
    _feed(mgr, mgr._generation, [PRIME_PANIC])
    report = mgr.stall_report()
    assert report is not None
    assert report["signal"] == "panic"


def test_reindex_inflight_and_quiescent_properties() -> None:
    mgr = _manager()
    _events(mgr, [PRIMING_BEGIN, {"token": "rustAnalyzer/Fetching", "value": {"kind": "begin"}}])
    assert mgr.reindex_inflight == ["rustAnalyzer/Fetching", CACHE_PRIMING]
    assert mgr.quiescent is None
    _events(mgr, [False])
    assert mgr.quiescent is False


# ---------------------------------------------------------------------------
# The server-side request timeout (P2-6)
# ---------------------------------------------------------------------------


async def _never() -> None:
    await asyncio.Event().wait()


def _other_tasks() -> set[asyncio.Task[Any]]:
    return {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}


def test_request_timeout_raises_and_leaves_no_task() -> None:
    mgr = _manager()
    mgr.request_timeout_seconds = 0.05

    async def _scenario() -> None:
        with pytest.raises(AnalyzerRequestTimeoutError) as info:
            await mgr._race_teardown(_never(), "definition")
        assert info.value.op == "definition"
        assert info.value.timeout_seconds == 0.05
        assert "definition" in str(info.value)
        await asyncio.sleep(0)
        assert _other_tasks() == set()

    asyncio.run(_scenario())


def test_request_timeout_zero_disables_bound() -> None:
    mgr = _manager()
    mgr.request_timeout_seconds = 0

    async def _scenario() -> None:
        task = asyncio.create_task(mgr._race_teardown(_never(), "hover"))
        await asyncio.sleep(0.2)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_scenario())


def test_teardown_still_wins_before_timeout() -> None:
    mgr = _manager()
    mgr.request_timeout_seconds = 5

    async def _scenario() -> None:
        task = asyncio.create_task(mgr._race_teardown(_never(), "references"))
        await asyncio.sleep(0.01)
        mgr._shutdown_event.set()
        with pytest.raises(AnalyzerTornDownError):
            await task

    asyncio.run(_scenario())


def test_fast_request_returns_within_timeout() -> None:
    mgr = _manager()
    mgr.request_timeout_seconds = 5

    async def _answer() -> str:
        await asyncio.sleep(0)
        return "ok"

    async def _scenario() -> str:
        return await mgr._race_teardown(_answer(), "hover")

    assert asyncio.run(_scenario()) == "ok"


def test_timeout_error_is_not_a_not_ready_error() -> None:
    """The tools map these two to not_ready; a timeout must reach the catch-all."""
    exc = AnalyzerRequestTimeoutError("hover", 1)
    assert not isinstance(exc, AnalyzerTornDownError)
    assert not isinstance(exc, analyzer_mod.AnalyzerNotReadyError)
    assert isinstance(exc, RuntimeError)


def test_lifespan_reads_both_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """``analyzer_lifespan`` hands the two settings to the manager."""
    import rust_lsp_mcp.settings as settings_mod

    real = settings_mod.get_settings()
    fake = real.model_copy(update={"stall_threshold_seconds": 7.0, "request_timeout_seconds": 9.0})
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fake)
    seen: dict[str, AnalyzerManager] = {}

    async def _noop_start(self: AnalyzerManager) -> None:
        seen["mgr"] = self

    async def _noop_shutdown(self: AnalyzerManager) -> None:
        return None

    monkeypatch.setattr(AnalyzerManager, "start", _noop_start)
    monkeypatch.setattr(AnalyzerManager, "shutdown", _noop_shutdown)

    async def _scenario() -> None:
        async with analyzer_mod.analyzer_lifespan(object()):
            pass

    asyncio.run(_scenario())
    assert seen["mgr"].stall_threshold_seconds == 7.0
    assert seen["mgr"].request_timeout_seconds == 9.0
