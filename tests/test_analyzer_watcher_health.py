"""Fast tests for rust-analyzer file-watcher health (backlog line 42).

rust-analyzer reports every file-watch error on stderr as ``notify error:
<display>`` (``vfs-notify`` ``log_notify_error``); the inotify limit displays
as ``OS file watch limit reached.``.  The manager's stderr reader turns such a
line into ``watcher_health == "error"`` with the line as ``watcher_message``,
for the current generation only; ``No path was found`` stays benign (backlog
line 86).  ``status`` returns both fields.

No live analyzer: fake stderr lines are fed through the real reader.
"""

from __future__ import annotations

import asyncio
import collections
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from rust_lsp_mcp.analyzer import STATE_ERROR, STATE_READY, AnalyzerManager
from tests.test_status import _call_status, _completed_process, _make_manager

# The line rust-analyzer writes when the host inotify watch limit is reached
# (tracing's WARN prefix and target before the notify error display).
LIMIT_LINE = (
    "2026-11-04T21:00:00.000000Z  WARN vfs_notify: notify error: OS file watch limit "
    'reached. about ["/project/crates/core"]'
)
NO_PATH_LINE = "2026-11-04T21:00:00.000000Z  WARN notify error: No path was found."
OTHER_LINE = "2026-11-04T21:00:00.000000Z  INFO some unrelated rust-analyzer line"


class _FakeStderr:
    """An ``asyncio.StreamReader`` stand-in that yields the given lines, then EOF."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = [f"{line}\n".encode() for line in lines]

    def at_eof(self) -> bool:
        return not self._lines

    async def readline(self) -> bytes:
        await asyncio.sleep(0)
        return self._lines.pop(0) if self._lines else b""


def _feed(mgr: AnalyzerManager, gen: int, lines: list[str], bump_after: int | None = None) -> None:
    """Run the manager's stderr reader for *gen* over *lines* to EOF.

    With *bump_after*, the manager's generation is bumped once that many lines
    have been read (a restart superseding the run mid-stream).
    """
    stderr = _FakeStderr(lines)
    if bump_after is not None:
        inner = stderr.readline
        count = 0

        async def _readline() -> bytes:
            nonlocal count
            if count == bump_after:
                mgr._generation += 1
            count += 1
            return await inner()

        stderr.readline = _readline  # ty: ignore[invalid-assignment]
    lsp = SimpleNamespace(server=SimpleNamespace(process=SimpleNamespace(stderr=stderr)))
    deque: collections.deque[str] = collections.deque(maxlen=200)
    started, done = asyncio.Event(), asyncio.Event()

    async def _scenario() -> None:
        reader = mgr._make_stderr_reader(gen, lsp, deque, started, done)  # ty: ignore[invalid-argument-type]
        await reader()

    asyncio.run(_scenario())
    assert done.is_set()


def _ready_manager() -> AnalyzerManager:
    mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root="/nonexistent")
    mgr.state = STATE_READY
    mgr.watcher_health = "ok"
    return mgr


def test_limit_line_sets_error_and_message(caplog: pytest.LogCaptureFixture) -> None:
    mgr = _ready_manager()
    with caplog.at_level(logging.WARNING, logger="rust_lsp_mcp.analyzer"):
        _feed(mgr, mgr._generation, [OTHER_LINE, LIMIT_LINE, LIMIT_LINE.replace("core", "cli")])
    assert mgr.watcher_health == "error"
    assert mgr.watcher_message == LIMIT_LINE  # the run's first failure line
    warnings = [r.getMessage() for r in caplog.records if "file watcher failed" in r.getMessage()]
    assert warnings == [f"rust-analyzer file watcher failed: {LIMIT_LINE}"]  # once per run


def test_io_error_from_watcher_creation_counts_as_failure() -> None:
    """An exhausted max_user_instances fails RecommendedWatcher::new with an Io error."""
    mgr = _ready_manager()
    line = "WARN notify error: Too many open files (os error 24)"
    _feed(mgr, mgr._generation, [line])
    assert mgr.watcher_health == "error"
    assert mgr.watcher_message == line


def test_no_path_was_found_stays_ok() -> None:
    mgr = _ready_manager()
    _feed(mgr, mgr._generation, [NO_PATH_LINE, OTHER_LINE])
    assert mgr.watcher_health == "ok"
    assert mgr.watcher_message is None


def test_failure_before_ready_is_reported() -> None:
    mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root="/nonexistent")
    assert mgr.watcher_health is None
    _feed(mgr, mgr._generation, [LIMIT_LINE])
    assert mgr.watcher_health == "error"


def test_superseded_generation_records_nothing() -> None:
    mgr = _ready_manager()
    _feed(mgr, mgr._generation - 1, [LIMIT_LINE])
    assert mgr.watcher_health == "ok"
    assert mgr.watcher_message is None


def test_restart_mid_stream_drops_later_lines() -> None:
    mgr = _ready_manager()
    _feed(mgr, mgr._generation, [OTHER_LINE, LIMIT_LINE], bump_after=1)
    assert mgr.watcher_health == "ok"
    assert mgr.watcher_message is None


def test_restart_resets_watcher_fields() -> None:
    mgr = _ready_manager()
    mgr.watcher_health = "error"
    mgr.watcher_message = LIMIT_LINE

    async def _noop_start() -> None:
        return None

    mgr.start = _noop_start  # ty: ignore[invalid-assignment]
    asyncio.run(mgr.restart())
    assert mgr.watcher_health is None
    assert mgr.watcher_message is None


def test_new_stub_reads_class_defaults() -> None:
    mgr: Any = AnalyzerManager.__new__(AnalyzerManager)
    assert mgr.watcher_health is None
    assert mgr.watcher_message is None


class TestStatusWatcherFields:
    def test_no_manager_reads_null(self) -> None:
        result = _call_status(None, subprocess_run_return=_completed_process(0, "a" * 40))
        assert result["watcher_health"] is None
        assert result["watcher_message"] is None

    def test_ok_watcher(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit="a" * 40)
        mgr.watcher_health = "ok"
        result = _call_status(mgr, subprocess_run_return=_completed_process(0, "a" * 40))
        assert result["watcher_health"] == "ok"
        assert result["watcher_message"] is None

    def test_failed_watcher(self) -> None:
        mgr = _make_manager(STATE_READY, indexed_commit="a" * 40)
        mgr.watcher_health = "error"
        mgr.watcher_message = LIMIT_LINE
        result = _call_status(mgr, subprocess_run_return=_completed_process(0, "a" * 40))
        assert result["watcher_health"] == "error"
        assert result["watcher_message"] == LIMIT_LINE

    def test_errored_stub_reads_null(self) -> None:
        mgr = _make_manager(STATE_ERROR)
        result = _call_status(mgr, subprocess_run_return=_completed_process(0, "a" * 40))
        assert result["watcher_health"] is None
