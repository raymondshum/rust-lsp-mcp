"""Fast tests for the ``indexed_commit`` re-capture after a watcher re-index.

With rust-analyzer watching the tree itself (SW-03), a branch switch
re-indexes without a restart.  The manager's ``$/progress`` handler
re-captures ``indexed_commit`` on the ``end`` event of a re-index token
(``rustAnalyzer/cachePriming``, ``rustAnalyzer/Fetching``,
``rustAnalyzer/Roots Scanned``), never on another token such as flycheck's,
and only while the manager is ``ready`` and the handler's generation is
current, both before and after the awaited git call; of two overlapping
re-captures the later read wins.  Also covers the progress fields
(``progress_message``, ``progress_at``) the handler records (SW-12) and that
``_run`` passes the handler to ``PatchedRustAnalyzer``.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import patch

import rust_lsp_mcp.analyzer as analyzer_mod
from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager

OLD = "a" * 40
NEW = "b" * 40

# A re-index token (rust-analyzer's cache priming); its end re-captures.
TOKEN = "rustAnalyzer/cachePriming"
END = {"token": TOKEN, "value": {"kind": "end"}}
BEGIN = {"token": TOKEN, "value": {"kind": "begin", "title": "Indexing"}}
REPORT = {"token": TOKEN, "value": {"kind": "report", "message": "3/10 (core)"}}

# Flycheck's token and title as logged live in the R-gate falsification.
FLYCHECK_BEGIN = {
    "token": "rust-analyzer/flycheck/0",
    "value": {"kind": "begin", "title": "cargo check"},
}
FLYCHECK_END = {"token": "rust-analyzer/flycheck/0", "value": {"kind": "end"}}


def _manager(state: str = STATE_READY) -> AnalyzerManager:
    mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root="/nonexistent")
    mgr.state = state
    mgr._indexed_commit = OLD
    return mgr


def _patch_head(mgr: AnalyzerManager, commit: str | None = NEW, before_return: Any = None) -> Any:
    async def _fake_read_head_commit() -> str | None:
        if before_return is not None:
            before_return()
        await asyncio.sleep(0)
        return commit

    return patch.object(mgr, "_read_head_commit", _fake_read_head_commit)


def test_end_event_recaptures_commit() -> None:
    mgr = _manager()
    handler = mgr._make_progress_handler(mgr._generation)
    with _patch_head(mgr):
        asyncio.run(handler(END))
    assert mgr.indexed_commit == NEW


def test_every_reindex_token_end_recaptures() -> None:
    for token in (
        "rustAnalyzer/cachePriming",
        "rustAnalyzer/Fetching",
        "rustAnalyzer/Roots Scanned",
    ):
        mgr = _manager()
        handler = mgr._make_progress_handler(mgr._generation)
        with _patch_head(mgr):
            asyncio.run(handler({"token": token, "value": {"kind": "end"}}))
        assert mgr.indexed_commit == NEW, token


def test_flycheck_end_does_not_recapture_indexed_commit() -> None:
    """R-gate falsification: with the watcher dead, the startup flycheck's
    end re-captured the new HEAD and status read stale: false while the index
    still held the old branch.  A flycheck end says nothing about the index."""
    mgr = _manager()
    handler = mgr._make_progress_handler(mgr._generation)
    with _patch_head(mgr):
        asyncio.run(handler(FLYCHECK_BEGIN))
        asyncio.run(handler(FLYCHECK_END))
    assert mgr.indexed_commit == OLD


def test_other_token_end_does_not_recapture() -> None:
    mgr = _manager()
    handler = mgr._make_progress_handler(mgr._generation)
    with _patch_head(mgr):
        asyncio.run(handler({"token": "t", "value": {"kind": "end"}}))
    assert mgr.indexed_commit == OLD


def test_overlapping_recaptures_latest_read_wins() -> None:
    """multilspy runs each notification as its own task, so two end handlers
    can be in the git call at once; the first read finishing last must not
    overwrite the second's newer value."""
    mgr = _manager()
    handler = mgr._make_progress_handler(mgr._generation)
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
        first = asyncio.create_task(handler(END))
        await asyncio.sleep(0)
        second = asyncio.create_task(handler(END))
        await second_done.wait()
        await second
        first_release.set()
        await first

    with patch.object(mgr, "_read_head_commit", _fake_read_head_commit):
        asyncio.run(_scenario())
    assert calls == [0, 1]
    assert mgr.indexed_commit == NEW


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


def test_begin_and_report_events_do_not_recapture() -> None:
    mgr = _manager()
    handler = mgr._make_progress_handler(mgr._generation)
    with _patch_head(mgr):
        asyncio.run(handler(BEGIN))
        asyncio.run(handler(REPORT))
    assert mgr.indexed_commit == OLD


def test_superseded_before_call_leaves_commit() -> None:
    mgr = _manager()
    handler = mgr._make_progress_handler(mgr._generation)
    mgr._generation += 1
    with _patch_head(mgr):
        asyncio.run(handler(END))
    assert mgr.indexed_commit == OLD


def test_superseded_during_git_call_leaves_commit() -> None:
    mgr = _manager()
    handler = mgr._make_progress_handler(mgr._generation)

    def _bump() -> None:
        mgr._generation += 1

    with _patch_head(mgr, before_return=_bump):
        asyncio.run(handler(END))
    assert mgr.indexed_commit == OLD


def test_end_while_indexing_leaves_commit() -> None:
    mgr = _manager(state=STATE_INDEXING)
    handler = mgr._make_progress_handler(mgr._generation)
    with _patch_head(mgr):
        asyncio.run(handler(END))
    assert mgr.indexed_commit == OLD


def test_progress_fields_recorded() -> None:
    mgr = _manager(state=STATE_INDEXING)
    assert mgr.progress_message is None and mgr.progress_at is None
    handler = mgr._make_progress_handler(mgr._generation)
    asyncio.run(handler(BEGIN))
    assert mgr.progress_message == "Indexing"
    asyncio.run(handler(REPORT))
    assert mgr.progress_message == "Indexing 3/10 (core)"
    assert mgr.progress_at is not None and mgr.progress_at.endswith("+00:00")


def test_superseded_handler_records_no_progress() -> None:
    mgr = _manager(state=STATE_INDEXING)
    handler = mgr._make_progress_handler(mgr._generation)
    mgr._generation += 1
    asyncio.run(handler(BEGIN))
    assert mgr.progress_message is None


def test_malformed_params_ignored() -> None:
    mgr = _manager()
    handler = mgr._make_progress_handler(mgr._generation)
    with _patch_head(mgr):
        asyncio.run(handler(None))
        asyncio.run(handler({"token": "t"}))
        asyncio.run(handler({"token": "t", "value": "x"}))
    assert mgr.indexed_commit == OLD
    assert mgr.progress_message is None


def test_read_head_commit_failure_returns_none() -> None:
    mgr = _manager()
    assert asyncio.run(mgr._read_head_commit()) is None
