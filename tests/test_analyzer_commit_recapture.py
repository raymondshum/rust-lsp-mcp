"""Fast tests for the ``indexed_commit`` re-captures and the progress fields.

**Quiescent-gated re-capture (backlog line 44).**  With rust-analyzer
watching the tree itself (SW-03), a branch switch re-indexes without a
restart.  The ``$/progress`` handler marks a re-capture pending on the
``end`` of a re-index token (``rustAnalyzer/cachePriming``,
``rustAnalyzer/Fetching``, ``rustAnalyzer/Roots Scanned``), never on another
token such as flycheck's, and only while the manager is ``ready`` and the
handler's generation is current.  The ``experimental/serverStatus`` callback
re-captures on the next ``quiescent: true``, re-checking the generation after
the awaited git call; of two overlapping re-captures the later read wins.

**HEAD-move re-capture (backlog line 49).**  ``reconcile_head`` re-captures
when HEAD moved over a tree the index already reflects; each of its
conditions alone blocks it.  The git helpers are patched.

Also covers the progress fields (``progress_message``, ``progress_at``) the
handler records (SW-12), the ``"<title> done"`` text after an ``end``
(backlog line 47), and that ``_run`` passes the handler to
``PatchedRustAnalyzer``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pathlib
import subprocess
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

import rust_lsp_mcp.analyzer as analyzer_mod
from rust_lsp_mcp.analyzer import (
    HEAD_SETTLE_SECONDS,
    STATE_INDEXING,
    STATE_READY,
    AnalyzerManager,
)
from tests.test_status import _call_status, _completed_process

OLD = "a" * 40
NEW = "b" * 40

# A re-index token (rust-analyzer's cache priming).
TOKEN = "rustAnalyzer/cachePriming"
END = {"token": TOKEN, "value": {"kind": "end"}}
BEGIN = {"token": TOKEN, "value": {"kind": "begin", "title": "Indexing"}}
REPORT = {"token": TOKEN, "value": {"kind": "report", "message": "3/10 (core)"}}
FETCH_BEGIN = {"token": "rustAnalyzer/Fetching", "value": {"kind": "begin", "title": "Fetching"}}
FETCH_END = {"token": "rustAnalyzer/Fetching", "value": {"kind": "end"}}

# Flycheck's token and title as logged live in the R-gate falsification.
FLYCHECK_BEGIN = {
    "token": "rust-analyzer/flycheck/0",
    "value": {"kind": "begin", "title": "cargo check"},
}
FLYCHECK_END = {"token": "rust-analyzer/flycheck/0", "value": {"kind": "end"}}


def _manager(state: str = STATE_READY, repository_root: str = "/nonexistent") -> AnalyzerManager:
    mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root=repository_root)
    mgr.state = state
    mgr._indexed_commit = OLD
    return mgr


def _patch_head(
    mgr: AnalyzerManager,
    commit: str | None = NEW,
    before_return: Any = None,
    calls: list[int] | None = None,
) -> Any:
    async def _fake_read_head_commit() -> str | None:
        if calls is not None:
            calls.append(1)
        if before_return is not None:
            before_return()
        await asyncio.sleep(0)
        return commit

    return patch.object(mgr, "_read_head_commit", _fake_read_head_commit)


def _run_events(mgr: AnalyzerManager, events: list[Any], gen: int | None = None) -> None:
    """Feed *events* in order: a dict is a ``$/progress`` param, a bool a quiescent report."""
    g = mgr._generation if gen is None else gen
    progress = mgr._make_progress_handler(g)
    status = mgr._make_status_handler(g)

    async def _scenario() -> None:
        for event in events:
            if isinstance(event, bool):
                await status("ok", None, event)
            else:
                await progress(event)

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# Quiescent-gated re-capture (step 3)
# ---------------------------------------------------------------------------


def test_end_alone_leaves_commit() -> None:
    """Backlog line 44: an early phase end must not re-capture by itself."""
    mgr = _manager()
    calls: list[int] = []
    with _patch_head(mgr, calls=calls):
        _run_events(mgr, [FETCH_BEGIN, FETCH_END])
    assert mgr.indexed_commit == OLD
    assert mgr._recapture_pending is True
    assert calls == []


def test_end_then_quiescent_recaptures() -> None:
    mgr = _manager()
    with _patch_head(mgr):
        _run_events(mgr, [BEGIN, END, True])
    assert mgr.indexed_commit == NEW
    assert mgr._recapture_pending is False


def test_every_reindex_token_end_then_quiescent_recaptures() -> None:
    for token in (
        "rustAnalyzer/cachePriming",
        "rustAnalyzer/Fetching",
        "rustAnalyzer/Roots Scanned",
    ):
        mgr = _manager()
        with _patch_head(mgr):
            _run_events(mgr, [{"token": token, "value": {"kind": "end"}}, True])
        assert mgr.indexed_commit == NEW, token


def test_end_then_more_work_then_quiescent_recaptures_once_with_latest_head() -> None:
    """Fetching ends, cache priming begins and ends, then quiescent: true."""
    mgr = _manager()
    heads = iter(["c" * 40, "d" * 40])
    calls: list[str] = []

    async def _fake_read_head_commit() -> str | None:
        head = next(heads)
        calls.append(head)
        return head

    with patch.object(mgr, "_read_head_commit", _fake_read_head_commit):
        _run_events(mgr, [FETCH_BEGIN, FETCH_END, False, BEGIN, REPORT, END, True])
    assert calls == ["c" * 40]  # one read, at the quiescent: true
    assert mgr.indexed_commit == "c" * 40


def test_quiescent_without_pending_reads_no_head() -> None:
    mgr = _manager()
    calls: list[int] = []
    with _patch_head(mgr, calls=calls):
        _run_events(mgr, [True, False, True])
    assert calls == []
    assert mgr.indexed_commit == OLD


def test_quiescent_false_does_not_recapture() -> None:
    mgr = _manager()
    calls: list[int] = []
    with _patch_head(mgr, calls=calls):
        _run_events(mgr, [END, False])
    assert calls == []
    assert mgr.indexed_commit == OLD
    assert mgr._recapture_pending is True


def test_flycheck_end_does_not_recapture_indexed_commit() -> None:
    """R-gate falsification: with the watcher dead, the startup flycheck's
    end re-captured the new HEAD and status read stale: false while the index
    still held the old branch.  A flycheck end says nothing about the index."""
    mgr = _manager()
    with _patch_head(mgr):
        _run_events(mgr, [FLYCHECK_BEGIN, FLYCHECK_END, True])
    assert mgr.indexed_commit == OLD
    assert mgr._recapture_pending is False


def test_other_token_end_does_not_recapture() -> None:
    mgr = _manager()
    with _patch_head(mgr):
        _run_events(mgr, [{"token": "t", "value": {"kind": "end"}}, True])
    assert mgr.indexed_commit == OLD


def test_begin_and_report_events_do_not_recapture() -> None:
    mgr = _manager()
    with _patch_head(mgr):
        _run_events(mgr, [BEGIN, REPORT, True])
    assert mgr.indexed_commit == OLD


def test_overlapping_recaptures_latest_read_wins() -> None:
    """multilspy runs each notification as its own task, so two quiescent
    re-captures can be in the git call at once; the first read finishing last
    must not overwrite the second's newer value."""
    mgr = _manager()
    progress = mgr._make_progress_handler(mgr._generation)
    status = mgr._make_status_handler(mgr._generation)
    first_release = asyncio.Event()
    second_done = asyncio.Event()
    calls: list[int] = []

    async def _fake_read_head_commit() -> str | None:
        calls.append(len(calls))
        if len(calls) == 1:
            await first_release.wait()
            return "1" * 40  # the older HEAD, returned last
        second_done.set()
        return NEW

    async def _scenario() -> None:
        await progress(END)
        first = asyncio.create_task(status("ok", None, True))
        await asyncio.sleep(0)
        await status("ok", None, False)
        await progress(END)
        second = asyncio.create_task(status("ok", None, True))
        await second_done.wait()
        await second
        first_release.set()
        await first

    with patch.object(mgr, "_read_head_commit", _fake_read_head_commit):
        asyncio.run(_scenario())
    assert calls == [0, 1]
    assert mgr.indexed_commit == NEW


def test_superseded_before_call_leaves_commit() -> None:
    mgr = _manager()
    gen = mgr._generation
    mgr._generation += 1
    with _patch_head(mgr):
        _run_events(mgr, [END, True], gen=gen)
    assert mgr.indexed_commit == OLD
    assert mgr._recapture_pending is False


def test_superseded_during_git_call_leaves_commit() -> None:
    mgr = _manager()

    def _bump() -> None:
        mgr._generation += 1

    with _patch_head(mgr, before_return=_bump):
        _run_events(mgr, [END, True])
    assert mgr.indexed_commit == OLD


def test_end_while_indexing_leaves_commit() -> None:
    mgr = _manager(state=STATE_INDEXING)
    with _patch_head(mgr):
        _run_events(mgr, [END, True])
    assert mgr.indexed_commit == OLD
    assert mgr._recapture_pending is False


def test_quiescent_logged_on_change_only(caplog: pytest.LogCaptureFixture) -> None:
    mgr = _manager()
    with caplog.at_level(logging.INFO, logger="rust_lsp_mcp.analyzer"):
        _run_events(mgr, [False, False, True, True, False])
    lines = [r.getMessage() for r in caplog.records if "quiescent:" in r.getMessage()]
    assert lines == [
        "rust-analyzer quiescent: false",
        "rust-analyzer quiescent: true",
        "rust-analyzer quiescent: false",
    ]
    assert mgr._quiescent is False


def test_restart_resets_recapture_fields() -> None:
    mgr = _manager()
    mgr._recapture_pending = True
    mgr._quiescent = True

    async def _noop_start() -> None:
        return None

    mgr.start = _noop_start  # ty: ignore[invalid-assignment]
    asyncio.run(mgr.restart())
    assert mgr._recapture_pending is False
    assert mgr._quiescent is None


def test_run_passes_progress_handler_to_analyzer() -> None:
    """_run hands PatchedRustAnalyzer a callable progress_handler keyword."""
    received: dict[str, Any] = {}

    def _factory(**kwargs: Any) -> Any:
        received.update(kwargs)
        raise RuntimeError("stop after construction")

    async def _scenario() -> None:
        mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root="/nonexistent")
        with contextlib.suppress(RuntimeError):
            await mgr._run(mgr._generation)

    with patch.object(analyzer_mod, "PatchedRustAnalyzer", _factory):
        asyncio.run(_scenario())
    assert "progress_handler" in received
    assert callable(received["progress_handler"])


# ---------------------------------------------------------------------------
# Progress fields (SW-12) and the text after an end (step 4)
# ---------------------------------------------------------------------------


def test_progress_fields_recorded() -> None:
    mgr = _manager(state=STATE_INDEXING)
    assert mgr.progress_message is None and mgr.progress_at is None
    handler = mgr._make_progress_handler(mgr._generation)
    asyncio.run(handler(BEGIN))
    assert mgr.progress_message == "Indexing"
    asyncio.run(handler(REPORT))
    assert mgr.progress_message == "Indexing 3/10 (core)"
    assert mgr.progress_at is not None and mgr.progress_at.endswith("+00:00")


def test_progress_after_end_reads_done() -> None:
    """Backlog line 47: the bare title after an end read as ongoing work."""
    mgr = _manager(state=STATE_INDEXING)
    _run_events(mgr, [BEGIN, REPORT, END])
    assert mgr.progress_message == "Indexing done"


def test_progress_after_end_without_title_is_none() -> None:
    mgr = _manager(state=STATE_INDEXING)
    _run_events(mgr, [BEGIN, END])  # titled token sets "Indexing done"
    _run_events(mgr, [{"token": "untitled", "value": {"kind": "end"}}])
    assert mgr.progress_message is None


def test_superseded_handler_records_no_progress() -> None:
    mgr = _manager(state=STATE_INDEXING)
    handler = mgr._make_progress_handler(mgr._generation)
    mgr._generation += 1
    asyncio.run(handler(BEGIN))
    assert mgr.progress_message is None


def test_malformed_params_ignored() -> None:
    mgr = _manager()
    with _patch_head(mgr):
        _run_events(mgr, [None, {"token": "t"}, {"token": "t", "value": "x"}, True])
    assert mgr.indexed_commit == OLD
    assert mgr.progress_message is None


def test_read_head_commit_failure_returns_none() -> None:
    mgr = _manager()
    assert asyncio.run(mgr._read_head_commit()) is None


# ---------------------------------------------------------------------------
# HEAD-move re-capture (step 5, backlog line 49)
# ---------------------------------------------------------------------------

EDITED = "crates/core/main.rs"


class FakeGit:
    """Stand-in for ``analyzer._run_git``: answers the three calls of ``_tree_matches_head``."""

    def __init__(
        self,
        changed: list[str],
        in_head: list[str] | None = None,
        quiet_rc: int = 0,
        diff_rc: int = 0,
        on_call: Callable[[], None] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.changed = changed
        self.in_head = changed if in_head is None else in_head
        self.quiet_rc = quiet_rc
        self.diff_rc = diff_rc
        self.on_call = on_call
        self.raises = raises
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, repo_root: str, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if self.on_call is not None:
            self.on_call()
        if self.raises is not None:
            raise self.raises
        if args[:2] == ("diff", "--name-only"):
            return _completed(self.diff_rc, _sep(args).join(self.changed))
        if args[0] == "ls-tree":
            asked = set(args[args.index("--") + 1 :])
            return _completed(0, _sep(args).join(p for p in self.in_head if p in asked))
        if args[:2] == ("diff", "--quiet"):
            return _completed(self.quiet_rc)
        raise AssertionError(f"unexpected git call {args}")


def _sep(args: tuple[str, ...]) -> str:
    """The record separator git prints for *args*: NUL with ``-z``, else newline."""
    return "\0" if "-z" in args else "\n"


def _completed(rc: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["git"], returncode=rc, stdout=stdout, stderr="")


def _write(root: pathlib.Path, rel: str, age: float) -> None:
    """Create *rel* under *root* with an mtime *age* seconds in the past."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fn main() {}\n", encoding="utf-8")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


def _quiescent_manager(root: pathlib.Path) -> AnalyzerManager:
    """A ready manager whose run reported ``quiescent: true`` once, at startup."""
    mgr = _manager(repository_root=str(root))
    _run_events(mgr, [True])
    return mgr


def _reconcile(mgr: AnalyzerManager, git: FakeGit, gen: int | None = None) -> str | None:
    g = mgr._generation if gen is None else gen
    with patch.object(analyzer_mod, "_run_git", git):
        return asyncio.run(mgr.reconcile_head(NEW, g))


def test_settle_constant_is_two_seconds() -> None:
    assert HEAD_SETTLE_SECONDS == 2.0


def test_all_conditions_met_recaptures(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)
    git = FakeGit([EDITED, "README.md"])
    with caplog.at_level(logging.INFO, logger="rust_lsp_mcp.analyzer"):
        assert _reconcile(mgr, git) == NEW
    assert mgr.indexed_commit == NEW
    assert f"indexed_commit re-captured after HEAD moved over an indexed tree: {OLD} -> {NEW}" in [
        r.getMessage() for r in caplog.records
    ]
    # Only the Rust path is checked against HEAD on disk.
    assert git.calls[-1] == ("diff", "--quiet", NEW, "--", EDITED)


def test_in_place_save_with_no_quiescent_transition_recaptures_once_settled(
    tmp_path: pathlib.Path,
) -> None:
    """The live shape of backlog line 49: an in-place save of an existing .rs
    file sends no quiescent transition, so the only quiescent: true of the
    run is the startup one; the settle test reads the file's mtime alone."""
    _write(tmp_path, EDITED, age=HEAD_SETTLE_SECONDS + 1)
    mgr = _quiescent_manager(tmp_path)
    assert _reconcile(mgr, FakeGit([EDITED])) == NEW


def test_path_saved_within_settle_interval_blocks(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=0)
    mgr = _quiescent_manager(tmp_path)
    assert _reconcile(mgr, FakeGit([EDITED])) is None
    assert mgr.indexed_commit == OLD


def test_empty_rust_and_cargo_diff_recaptures(tmp_path: pathlib.Path) -> None:
    mgr = _quiescent_manager(tmp_path)
    git = FakeGit(["README.md", "docs/guide.md"])
    assert _reconcile(mgr, git) == NEW
    assert len(git.calls) == 1  # no ls-tree, no diff --quiet


def test_cargo_files_count_as_index_inputs(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "Cargo.lock", age=0)
    _write(tmp_path, "crates/core/Cargo.toml", age=10)
    mgr = _quiescent_manager(tmp_path)
    assert _reconcile(mgr, FakeGit(["Cargo.lock", "crates/core/Cargo.toml"])) is None


def test_disk_differs_from_head_blocks(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)
    assert _reconcile(mgr, FakeGit([EDITED], quiet_rc=1)) is None
    assert mgr.indexed_commit == OLD


def test_path_absent_from_head_and_disk_matches(tmp_path: pathlib.Path) -> None:
    mgr = _quiescent_manager(tmp_path)
    assert _reconcile(mgr, FakeGit(["crates/gone.rs"], in_head=[])) == NEW


def test_path_absent_from_head_but_on_disk_blocks(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "crates/untracked.rs", age=10)
    mgr = _quiescent_manager(tmp_path)
    assert _reconcile(mgr, FakeGit(["crates/untracked.rs"], in_head=[])) is None


def test_path_in_head_but_deleted_on_disk_blocks(tmp_path: pathlib.Path) -> None:
    mgr = _quiescent_manager(tmp_path)
    assert _reconcile(mgr, FakeGit([EDITED])) is None


def test_stale_generation_blocks(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)
    git = FakeGit([EDITED])
    assert _reconcile(mgr, git, gen=mgr._generation - 1) is None
    assert git.calls == []


def test_not_ready_blocks(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)
    mgr.state = STATE_INDEXING
    assert _reconcile(mgr, FakeGit([EDITED])) is None
    assert mgr.indexed_commit == OLD


def test_reindex_token_in_flight_blocks(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)
    _run_events(mgr, [BEGIN])
    assert _reconcile(mgr, FakeGit([EDITED])) is None
    _run_events(mgr, [END])
    assert _reconcile(mgr, FakeGit([EDITED])) == NEW


def test_not_quiescent_blocks(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)
    _run_events(mgr, [False])
    assert _reconcile(mgr, FakeGit([EDITED])) is None
    fresh = _manager(repository_root=str(tmp_path))  # no serverStatus yet
    assert _reconcile(fresh, FakeGit([EDITED])) is None


def test_same_commit_blocks(tmp_path: pathlib.Path) -> None:
    mgr = _quiescent_manager(tmp_path)
    mgr._indexed_commit = NEW
    git = FakeGit([])
    assert _reconcile(mgr, git) is None
    assert git.calls == []


def test_unknown_indexed_commit_blocks(tmp_path: pathlib.Path) -> None:
    mgr = _quiescent_manager(tmp_path)
    mgr._indexed_commit = None
    assert _reconcile(mgr, FakeGit([])) is None
    assert mgr.indexed_commit is None


def test_generation_change_during_await_leaves_commit(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)

    def _bump() -> None:
        mgr._generation += 1

    assert _reconcile(mgr, FakeGit([EDITED], on_call=_bump)) is None
    assert mgr.indexed_commit == OLD


def test_git_failure_leaves_commit(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)
    assert _reconcile(mgr, FakeGit([EDITED], diff_rc=128)) is None
    assert mgr.indexed_commit == OLD
    timeout = subprocess.TimeoutExpired(cmd="git", timeout=10)
    assert _reconcile(mgr, FakeGit([EDITED], raises=timeout)) is None
    assert mgr.indexed_commit == OLD


def test_tree_matches_head_against_a_real_repository(tmp_path: pathlib.Path) -> None:
    """End to end over real git: commit a saved, settled edit."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@invalid", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    _write(tmp_path, EDITED, age=10)
    git("add", ".")
    git("commit", "-qm", "base")
    old = git("rev-parse", "HEAD")
    (tmp_path / EDITED).write_text("fn main() { let _x = 1; }\n", encoding="utf-8")
    git("commit", "-qam", "edit")
    new = git("rev-parse", "HEAD")
    now = time.time()
    assert analyzer_mod._tree_matches_head(str(tmp_path), old, new, now) is False  # just saved
    stamp = now - 10
    os.utime(tmp_path / EDITED, (stamp, stamp))
    assert analyzer_mod._tree_matches_head(str(tmp_path), old, new, now) is True
    (tmp_path / EDITED).write_text("fn main() { unsaved_in_head(); }\n", encoding="utf-8")
    os.utime(tmp_path / EDITED, (stamp, stamp))
    assert analyzer_mod._tree_matches_head(str(tmp_path), old, new, now) is False


# ---------------------------------------------------------------------------
# status() and reconcile_head
# ---------------------------------------------------------------------------


def test_status_uses_reconciled_commit(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)
    mgr._lsp = object()  # ty: ignore[invalid-assignment]
    with patch.object(analyzer_mod, "_run_git", FakeGit([EDITED])):
        result = _call_status(mgr, subprocess_run_return=_completed_process(0, NEW))
    assert result["indexed_commit"] == NEW
    assert result["current_commit"] == NEW
    assert result["stale"] is False


def test_status_keeps_stale_when_reconcile_declines(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, EDITED, age=0)
    mgr = _quiescent_manager(tmp_path)
    mgr._lsp = object()  # ty: ignore[invalid-assignment]
    with patch.object(analyzer_mod, "_run_git", FakeGit([EDITED])):
        result = _call_status(mgr, subprocess_run_return=_completed_process(0, NEW))
    assert result["indexed_commit"] == OLD
    assert result["stale"] is True


def test_status_skips_reconcile_when_not_stale() -> None:
    mgr = _manager()
    calls: list[tuple[str, int]] = []

    async def _fake_reconcile(current_commit: str, gen: int) -> str | None:
        calls.append((current_commit, gen))
        return None

    with patch.object(mgr, "reconcile_head", _fake_reconcile):
        result = _call_status(mgr, subprocess_run_return=_completed_process(0, OLD))
    assert result["stale"] is False
    assert calls == []


def test_status_passes_snapshot_generation() -> None:
    mgr = _manager()
    calls: list[tuple[str, int]] = []

    async def _fake_reconcile(current_commit: str, gen: int) -> str | None:
        calls.append((current_commit, gen))
        return None

    with patch.object(mgr, "reconcile_head", _fake_reconcile):
        result = _call_status(mgr, subprocess_run_return=_completed_process(0, NEW))
    assert result["stale"] is True
    assert calls == [(NEW, mgr._generation)]


# ---------------------------------------------------------------------------
# R-gate round 1 regressions
# ---------------------------------------------------------------------------

C1 = "1" * 40
C2 = "2" * 40


def test_failed_watcher_blocks_reconcile(tmp_path: pathlib.Path) -> None:
    """D1: with the watcher failed, a checkout that edits an indexed file sends
    rust-analyzer no event, so quiescence says nothing about the tree."""
    _write(tmp_path, EDITED, age=10)
    mgr = _quiescent_manager(tmp_path)
    mgr.watcher_health = "error"
    git = FakeGit([EDITED])
    assert _reconcile(mgr, git) is None
    assert mgr.indexed_commit == OLD
    assert git.calls == []


def test_reconcile_does_not_drop_a_newer_quiescent_recapture(tmp_path: pathlib.Path) -> None:
    """D2: reconcile_head(C1) is in its git check when a re-index of C2 ends
    and the quiescent re-capture starts reading C2; the C2 read must win."""
    import threading

    mgr = _quiescent_manager(tmp_path)
    progress = mgr._make_progress_handler(mgr._generation)
    status = mgr._make_status_handler(mgr._generation)
    entered, gate = threading.Event(), threading.Event()

    def _blocking_match(root: str, old: str, new: str, now: float) -> bool:
        entered.set()
        gate.wait(5)
        return True

    async def _scenario() -> str | None:
        read_release = asyncio.Event()

        async def _read_c2() -> str | None:
            await read_release.wait()
            return C2

        with (
            patch.object(analyzer_mod, "_tree_matches_head", _blocking_match),
            patch.object(mgr, "_read_head_commit", _read_c2),
        ):
            reconcile = asyncio.create_task(mgr.reconcile_head(C1, mgr._generation))
            await asyncio.to_thread(entered.wait, 5)
            await progress(BEGIN)
            await status("ok", None, False)
            await progress(END)
            recapture = asyncio.create_task(status("ok", None, True))
            await asyncio.sleep(0)  # the quiescent re-capture is now reading C2
            gate.set()
            result = await reconcile
            read_release.set()
            await recapture
        return result

    result = asyncio.run(_scenario())
    assert result is None
    assert mgr.indexed_commit == C2


def test_reconcile_skipped_while_a_quiescent_read_is_in_flight(tmp_path: pathlib.Path) -> None:
    """D2: a quiescent HEAD read already in flight reads the newer HEAD."""
    mgr = _quiescent_manager(tmp_path)
    progress = mgr._make_progress_handler(mgr._generation)
    status = mgr._make_status_handler(mgr._generation)
    git = FakeGit([])

    async def _scenario() -> str | None:
        read_release = asyncio.Event()

        async def _read_c2() -> str | None:
            await read_release.wait()
            return C2

        with (
            patch.object(analyzer_mod, "_run_git", git),
            patch.object(mgr, "_read_head_commit", _read_c2),
        ):
            await status("ok", None, False)
            await progress(END)
            recapture = asyncio.create_task(status("ok", None, True))
            await asyncio.sleep(0)
            result = await mgr.reconcile_head(C1, mgr._generation)
            read_release.set()
            await recapture
        return result

    assert asyncio.run(_scenario()) is None
    assert git.calls == []
    assert mgr.indexed_commit == C2


def test_non_ascii_rust_path_is_an_index_input(tmp_path: pathlib.Path) -> None:
    """D3: with core.quotePath on, git prints ``"na\\303\\257ve.rs"``; the path
    must still be checked against the disk and the settle interval."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@invalid", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    rel = "crates/x/src/naïve.rs"
    git("init", "-q")
    _write(tmp_path, rel, age=10)
    git("add", ".")
    git("commit", "-qm", "base")
    old = git("rev-parse", "HEAD")
    (tmp_path / rel).write_text("fn naive() {}\n", encoding="utf-8")
    git("commit", "-qam", "edit")
    new = git("rev-parse", "HEAD")
    now = time.time()
    assert analyzer_mod._tree_matches_head(str(tmp_path), old, new, now) is False  # just saved
    stamp = now - 10
    os.utime(tmp_path / rel, (stamp, stamp))
    assert analyzer_mod._tree_matches_head(str(tmp_path), old, new, now) is True
    (tmp_path / rel).write_text("fn naive() { dirty(); }\n", encoding="utf-8")
    os.utime(tmp_path / rel, (stamp, stamp))
    assert analyzer_mod._tree_matches_head(str(tmp_path), old, new, now) is False


def test_run_resets_reindex_inflight() -> None:
    """T-a: a token left in flight by a run torn down mid cache priming must
    not block reconcile_head for the whole next run."""

    def _factory(**kwargs: Any) -> Any:
        raise RuntimeError("stop after construction")

    async def _scenario() -> AnalyzerManager:
        mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root="/nonexistent")
        mgr._reindex_inflight = frozenset({TOKEN})
        with contextlib.suppress(RuntimeError):
            await mgr._run(mgr._generation)
        return mgr

    with patch.object(analyzer_mod, "PatchedRustAnalyzer", _factory):
        mgr = asyncio.run(_scenario())
    assert mgr._reindex_inflight == frozenset()
