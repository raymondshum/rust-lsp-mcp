"""Fast tests for ``PatchedRustAnalyzer.open_file`` cleanup after a failed request (SW-08).

multilspy's ``open_file`` has no try/finally around its ``yield``, so a
request that raised left the file open with a stale buffer.  The override
decrements the entry's ``ref_count`` on any exception from the body and sends
``didClose`` only once the count reaches zero.  Constructing the analyzer
launches no process; ``server.notify`` is replaced with a recorder.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

import pytest
from multilspy.multilspy_config import Language, MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

from rust_lsp_mcp.analyzer import PatchedRustAnalyzer

_REL = "src/lib.rs"


class _Notify:
    """Records the didOpen/didClose notifications multilspy would send."""

    def __init__(self) -> None:
        self.opened: list[Any] = []
        self.closed: list[Any] = []

    def did_open_text_document(self, params: Any) -> None:
        self.opened.append(params)

    def did_close_text_document(self, params: Any) -> None:
        self.closed.append(params)


def _make(root: pathlib.Path) -> tuple[PatchedRustAnalyzer, _Notify, str]:
    (root / "src").mkdir()
    (root / _REL).write_text("pub fn f() {}\n")
    analyzer = PatchedRustAnalyzer(
        config=MultilspyConfig(code_language=Language.RUST),
        logger=MultilspyLogger(),
        repository_root_path=str(root),
        rust_analyzer_bin="/nonexistent/rust-analyzer",
    )
    notify = _Notify()
    analyzer.server.notify = notify  # ty: ignore[invalid-assignment]
    analyzer.server_started = True
    uri = (root / _REL).as_uri()
    return analyzer, notify, uri


def test_single_failed_user_closes_the_file(tmp_path: pathlib.Path) -> None:
    analyzer, notify, uri = _make(tmp_path)

    with pytest.raises(RuntimeError, match="request failed"), analyzer.open_file(_REL):
        assert analyzer.open_file_buffers[uri].ref_count == 1
        raise RuntimeError("request failed")

    assert uri not in analyzer.open_file_buffers
    assert len(notify.opened) == 1
    assert notify.closed == [{"textDocument": {"uri": uri}}]


def test_cancelled_user_closes_the_file(tmp_path: pathlib.Path) -> None:
    analyzer, notify, uri = _make(tmp_path)

    with pytest.raises(asyncio.CancelledError), analyzer.open_file(_REL):
        raise asyncio.CancelledError

    assert uri not in analyzer.open_file_buffers
    assert len(notify.closed) == 1


def test_overlapping_users_inner_fails(tmp_path: pathlib.Path) -> None:
    analyzer, notify, uri = _make(tmp_path)

    with analyzer.open_file(_REL):
        with pytest.raises(RuntimeError, match="inner failed"), analyzer.open_file(_REL):
            assert analyzer.open_file_buffers[uri].ref_count == 2
            raise RuntimeError("inner failed")
        # The outer user still holds the buffer.
        assert uri in analyzer.open_file_buffers
        assert analyzer.open_file_buffers[uri].ref_count == 1
        assert notify.closed == []
    # The outer exit runs multilspy's own decrement without KeyError.
    assert uri not in analyzer.open_file_buffers
    assert notify.closed == [{"textDocument": {"uri": uri}}]
    assert len(notify.opened) == 1


def test_success_path_unchanged(tmp_path: pathlib.Path) -> None:
    analyzer, notify, uri = _make(tmp_path)

    with analyzer.open_file(_REL):
        with analyzer.open_file(_REL):
            assert analyzer.open_file_buffers[uri].ref_count == 2
        assert analyzer.open_file_buffers[uri].ref_count == 1
        assert notify.closed == []
    assert uri not in analyzer.open_file_buffers
    assert len(notify.closed) == 1


def test_reopen_after_failure_reads_disk_again(tmp_path: pathlib.Path) -> None:
    """The point of SW-08: after a failed request the next open sends the new text."""
    analyzer, notify, _uri = _make(tmp_path)

    with pytest.raises(RuntimeError), analyzer.open_file(_REL):
        raise RuntimeError("request failed")
    (tmp_path / _REL).write_text("pub fn g() {}\n")
    with analyzer.open_file(_REL):
        pass

    assert len(notify.opened) == 2
    assert notify.opened[1]["textDocument"]["text"] == "pub fn g() {}\n"


def test_raising_did_close_does_not_strand_the_entry(tmp_path: pathlib.Path) -> None:
    """R-gate item 8: the entry is removed before ``didClose`` is sent, so a
    close that raises (e.g. a broken pipe to a dead process) cannot leave a
    ``ref_count == 0`` entry behind."""
    analyzer, notify, uri = _make(tmp_path)

    def _raise(params: Any) -> None:
        raise BrokenPipeError("dead process")

    notify.did_close_text_document = _raise  # ty: ignore[invalid-assignment]
    with pytest.raises((RuntimeError, BrokenPipeError)), analyzer.open_file(_REL):
        raise RuntimeError("request failed")
    assert uri not in analyzer.open_file_buffers
