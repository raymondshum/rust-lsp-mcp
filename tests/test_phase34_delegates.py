"""Fast-tier tests for Phase 3-4 analyzer delegates and restart().

No live analyzer, no network.  All heavy dependencies are stubbed.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    Delegate guards:
        - Each of the 4 new delegates raises RuntimeError when _lsp is None.
        - Each raises RuntimeError when state != STATE_READY.
    Delegate forwarding (fake _lsp, state=ready, methods patched via patch.object):
        - request_document_symbols returns the flat list (element [0] of tuple).
        - request_document_symbols returns [] when underlying returns None.
        - request_definition forwards (rel, line, column) and returns result.
        - request_definition returns [] when underlying returns None.
        - request_references forwards (rel, line, column) and returns result.
        - request_references returns [] when underlying returns None.
        - request_hover forwards (rel, line, column) and returns hover / None.
    restart():
        - Sets state == STATE_INDEXING synchronously as its first effect.
        - Completes without error when _task is None.
        - Re-creates _shutdown_event and _ready_event (fresh objects).
        - Re-spawns a new _task via start().
    indexed_commit:
        - Is None initially (property reflects _indexed_commit=None from __init__).
        - Is exposed via the property (not a raw attribute access).
    _capture_head_commit:
        - Stores stripped hash when git succeeds (monkeypatched).
        - Sets None when git returns non-zero exit code (monkeypatched).
        - Sets None when subprocess.run raises (monkeypatched).
"""

import asyncio
import subprocess
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rust_lsp_mcp.analyzer import (
    STATE_INDEXING,
    STATE_READY,
    AnalyzerManager,
    PatchedRustAnalyzer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ready_manager() -> AnalyzerManager:
    """Return a bare AnalyzerManager with state=ready and _lsp=None.

    Does NOT start a real background task — suitable for fast-tier tests.
    """
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr._rust_analyzer_bin = "/fake/rust-analyzer"
    mgr._repository_root = "/fake/repo"
    mgr.state = STATE_READY
    mgr._task = None
    mgr._ready_event = asyncio.Event()
    mgr._shutdown_event = asyncio.Event()
    mgr._indexed_commit = None
    mgr._lsp = None
    return mgr


def _make_indexing_manager() -> AnalyzerManager:
    """Return a bare AnalyzerManager with state=indexing and _lsp=None."""
    mgr = _make_ready_manager()
    mgr.state = STATE_INDEXING
    return mgr


# ---------------------------------------------------------------------------
# Guard tests: each delegate raises RuntimeError when not ready
# ---------------------------------------------------------------------------


class TestDelegateGuards:
    """Each delegate must raise RuntimeError when _lsp is None or state != READY."""

    # --- _lsp is None (state=ready but no context) ---

    def test_document_symbols_raises_when_lsp_none(self) -> None:
        mgr = _make_ready_manager()
        # _lsp is already None from _make_ready_manager
        with pytest.raises(RuntimeError, match="require_ready"):
            asyncio.run(mgr.request_document_symbols("src/lib.rs"))

    def test_definition_raises_when_lsp_none(self) -> None:
        mgr = _make_ready_manager()
        with pytest.raises(RuntimeError, match="require_ready"):
            asyncio.run(mgr.request_definition("src/lib.rs", 0, 0))

    def test_references_raises_when_lsp_none(self) -> None:
        mgr = _make_ready_manager()
        with pytest.raises(RuntimeError, match="require_ready"):
            asyncio.run(mgr.request_references("src/lib.rs", 0, 0))

    def test_hover_raises_when_lsp_none(self) -> None:
        mgr = _make_ready_manager()
        with pytest.raises(RuntimeError, match="require_ready"):
            asyncio.run(mgr.request_hover("src/lib.rs", 0, 0))

    # --- state != STATE_READY (indexing) ---

    def test_document_symbols_raises_when_indexing(self) -> None:
        mgr = _make_indexing_manager()
        with pytest.raises(RuntimeError, match="require_ready"):
            asyncio.run(mgr.request_document_symbols("src/lib.rs"))

    def test_definition_raises_when_indexing(self) -> None:
        mgr = _make_indexing_manager()
        with pytest.raises(RuntimeError, match="require_ready"):
            asyncio.run(mgr.request_definition("src/lib.rs", 5, 10))

    def test_references_raises_when_indexing(self) -> None:
        mgr = _make_indexing_manager()
        with pytest.raises(RuntimeError, match="require_ready"):
            asyncio.run(mgr.request_references("src/lib.rs", 5, 10))

    def test_hover_raises_when_indexing(self) -> None:
        mgr = _make_indexing_manager()
        with pytest.raises(RuntimeError, match="require_ready"):
            asyncio.run(mgr.request_hover("src/lib.rs", 5, 10))


# ---------------------------------------------------------------------------
# Forwarding tests: delegates pass args through and return correct values
#
# Strategy: set mgr._lsp to a real (but unstarted) PatchedRustAnalyzer instance
# and then patch its individual methods via patch.object so we can inject fake
# return values without touching the network or filesystem.
# ---------------------------------------------------------------------------


def _make_unstarted_lsp() -> PatchedRustAnalyzer:
    """Construct a PatchedRustAnalyzer that has never been started.

    We only use it as the identity-carrier for patch.object; none of its real
    methods are called — they are all replaced by AsyncMock in each test.
    """
    from multilspy.multilspy_config import Language, MultilspyConfig
    from multilspy.multilspy_logger import MultilspyLogger

    config = MultilspyConfig(code_language=Language.RUST)
    logger = MultilspyLogger()
    # PatchedRustAnalyzer.__init__ calls setup_runtime_dependencies, which reads
    # _rust_analyzer_bin.  We pass a dummy path — no file access happens here.
    return PatchedRustAnalyzer(
        config=config,
        logger=logger,
        repository_root_path="/fake/repo",
        rust_analyzer_bin="/fake/rust-analyzer",
    )


class TestDocumentSymbolsDelegate:
    """request_document_symbols returns the flat list (tuple element [0])."""

    def test_returns_flat_list(self) -> None:
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp
        flat: list[Any] = [{"name": "sym_a"}, {"name": "sym_b"}]

        async def _run() -> None:
            with patch.object(
                lsp, "request_document_symbols", new=AsyncMock(return_value=(flat, None))
            ):
                result = await mgr.request_document_symbols("src/lib.rs")
            assert result == flat
            assert len(result) == 2
            assert result[0]["name"] == "sym_a"

        asyncio.run(_run())

    def test_discards_tree_element(self) -> None:
        """The second tuple element (tree) must NOT be returned."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp
        flat: list[Any] = [{"name": "sym_x"}]
        tree: list[Any] = [{"children": []}]

        async def _run() -> None:
            with patch.object(
                lsp, "request_document_symbols", new=AsyncMock(return_value=(flat, tree))
            ):
                result = await mgr.request_document_symbols("src/lib.rs")
            assert isinstance(result, list)
            assert result == flat
            assert result != tree

        asyncio.run(_run())

    def test_returns_empty_list_when_none(self) -> None:
        """If the LSP returns None, delegate must return []."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(lsp, "request_document_symbols", new=AsyncMock(return_value=None)):
                result = await mgr.request_document_symbols("src/lib.rs")
            assert result == []

        asyncio.run(_run())

    def test_forwards_file_path(self) -> None:
        """The file path argument is forwarded to the underlying LSP call."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp
        mock_method = AsyncMock(return_value=([], None))

        async def _run() -> None:
            with patch.object(lsp, "request_document_symbols", new=mock_method):
                await mgr.request_document_symbols("src/lib.rs")
            mock_method.assert_called_once_with("src/lib.rs")

        asyncio.run(_run())


class TestDefinitionDelegate:
    """request_definition forwards (rel, line, col) and returns result or None.

    Contract (multilspy 0.0.15):
        - Underlying returns a list (possibly []) → delegate returns that list.
        - Underlying raises AssertionError (null LSP response) → delegate returns None.
        - Underlying returns None cleanly (hypothetical) → delegate returns None.
    """

    def test_forwards_args_and_returns_result(self) -> None:
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp
        expected: list[Any] = [{"uri": "file:///repo/src/lib.rs"}]
        mock_method = AsyncMock(return_value=expected)

        async def _run() -> None:
            with patch.object(lsp, "request_definition", new=mock_method):
                result = await mgr.request_definition("src/lib.rs", 4, 12)
            assert result == expected
            mock_method.assert_called_once_with("src/lib.rs", 4, 12)

        asyncio.run(_run())

    def test_returns_none_when_underlying_raises_assertion_error(self) -> None:
        """AssertionError from multilspy (null LSP response) → delegate returns None."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(
                lsp,
                "request_definition",
                new=AsyncMock(side_effect=AssertionError("Unexpected response: None")),
            ):
                result = await mgr.request_definition("src/lib.rs", 0, 0)
            assert result is None

        asyncio.run(_run())

    def test_returns_none_when_underlying_returns_none(self) -> None:
        """Underlying returning None cleanly (hypothetical) → delegate returns None."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(lsp, "request_definition", new=AsyncMock(return_value=None)):
                result = await mgr.request_definition("src/lib.rs", 0, 0)
            assert result is None

        asyncio.run(_run())

    def test_returns_empty_list_when_underlying_returns_empty(self) -> None:
        """Underlying returning [] (zero results, NOT null) → delegate returns []."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(lsp, "request_definition", new=AsyncMock(return_value=[])):
                result = await mgr.request_definition("src/lib.rs", 0, 0)
            assert result == []

        asyncio.run(_run())

    def test_none_and_empty_list_are_distinguishable(self) -> None:
        """AssertionError path yields None; empty-list path yields [] — must differ."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(
                lsp,
                "request_definition",
                new=AsyncMock(side_effect=AssertionError("null")),
            ):
                null_result = await mgr.request_definition("src/lib.rs", 0, 0)
            with patch.object(lsp, "request_definition", new=AsyncMock(return_value=[])):
                empty_result = await mgr.request_definition("src/lib.rs", 0, 0)

            assert null_result is None
            assert empty_result == []
            assert null_result != empty_result

        asyncio.run(_run())

    def test_other_exceptions_propagate(self) -> None:
        """Non-AssertionError exceptions (genuine failures) must propagate uncaught."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with (
                patch.object(
                    lsp,
                    "request_definition",
                    new=AsyncMock(side_effect=RuntimeError("transport failure")),
                ),
                pytest.raises(RuntimeError, match="transport failure"),
            ):
                await mgr.request_definition("src/lib.rs", 0, 0)

        asyncio.run(_run())

    def test_passes_zero_indexed_line_and_column(self) -> None:
        """line=0, column=0 must be forwarded unchanged (0-indexed)."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp
        mock_method = AsyncMock(return_value=[])

        async def _run() -> None:
            with patch.object(lsp, "request_definition", new=mock_method):
                await mgr.request_definition("src/main.rs", 0, 0)
            mock_method.assert_called_once_with("src/main.rs", 0, 0)

        asyncio.run(_run())


class TestReferencesDelegate:
    """request_references forwards (rel, line, col) and returns result or None.

    Contract (multilspy 0.0.15):
        - Underlying returns a list (possibly []) → delegate returns that list.
        - Underlying raises AssertionError (null LSP response) → delegate returns None.
        - Underlying returns None cleanly (hypothetical) → delegate returns None.
    """

    def test_forwards_args_and_returns_result(self) -> None:
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp
        expected: list[Any] = [
            {"uri": "file:///repo/src/lib.rs"},
            {"uri": "file:///repo/src/main.rs"},
        ]
        mock_method = AsyncMock(return_value=expected)

        async def _run() -> None:
            with patch.object(lsp, "request_references", new=mock_method):
                result = await mgr.request_references("src/lib.rs", 9, 3)
            assert result is not None
            assert result == expected
            assert len(result) == 2
            mock_method.assert_called_once_with("src/lib.rs", 9, 3)

        asyncio.run(_run())

    def test_returns_none_when_underlying_raises_assertion_error(self) -> None:
        """AssertionError from multilspy (null LSP response) → delegate returns None."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(
                lsp,
                "request_references",
                new=AsyncMock(side_effect=AssertionError("Unexpected response: None")),
            ):
                result = await mgr.request_references("src/lib.rs", 0, 0)
            assert result is None

        asyncio.run(_run())

    def test_returns_none_when_underlying_returns_none(self) -> None:
        """Underlying returning None cleanly (hypothetical) → delegate returns None."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(lsp, "request_references", new=AsyncMock(return_value=None)):
                result = await mgr.request_references("src/lib.rs", 0, 0)
            assert result is None

        asyncio.run(_run())

    def test_returns_empty_list_when_underlying_returns_empty(self) -> None:
        """Underlying returning [] (zero callers, NOT null) → delegate returns []."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(lsp, "request_references", new=AsyncMock(return_value=[])):
                result = await mgr.request_references("src/lib.rs", 0, 0)
            assert result == []

        asyncio.run(_run())

    def test_none_and_empty_list_are_distinguishable(self) -> None:
        """AssertionError path yields None; empty-list path yields [] — must differ."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(
                lsp,
                "request_references",
                new=AsyncMock(side_effect=AssertionError("null")),
            ):
                null_result = await mgr.request_references("src/lib.rs", 0, 0)
            with patch.object(lsp, "request_references", new=AsyncMock(return_value=[])):
                empty_result = await mgr.request_references("src/lib.rs", 0, 0)

            assert null_result is None
            assert empty_result == []
            assert null_result != empty_result

        asyncio.run(_run())

    def test_other_exceptions_propagate(self) -> None:
        """Non-AssertionError exceptions (genuine failures) must propagate uncaught."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with (
                patch.object(
                    lsp,
                    "request_references",
                    new=AsyncMock(side_effect=RuntimeError("transport failure")),
                ),
                pytest.raises(RuntimeError, match="transport failure"),
            ):
                await mgr.request_references("src/lib.rs", 0, 0)

        asyncio.run(_run())


class TestHoverDelegate:
    """request_hover forwards (rel, line, col) and returns Hover | None."""

    def test_forwards_args_and_returns_hover(self) -> None:
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp
        hover_data: dict[str, Any] = {"contents": {"value": "fn foo() -> i32"}}
        mock_method = AsyncMock(return_value=hover_data)

        async def _run() -> None:
            with patch.object(lsp, "request_hover", new=mock_method):
                result = await mgr.request_hover("src/lib.rs", 7, 5)
            assert result == hover_data
            mock_method.assert_called_once_with("src/lib.rs", 7, 5)

        asyncio.run(_run())

    def test_returns_none_when_lsp_returns_none(self) -> None:
        """hover returning None (no info at cursor) must pass None through."""
        mgr = _make_ready_manager()
        lsp = _make_unstarted_lsp()
        mgr._lsp = lsp

        async def _run() -> None:
            with patch.object(lsp, "request_hover", new=AsyncMock(return_value=None)):
                result = await mgr.request_hover("src/lib.rs", 0, 0)
            assert result is None

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# restart() tests
# ---------------------------------------------------------------------------


class TestRestart:
    """restart() sets state=indexing first, tears down old task, re-spawns."""

    def test_sets_state_indexing_as_first_effect(self) -> None:
        """state must flip to indexing before _drain_task is awaited."""
        mgr = _make_ready_manager()
        mgr.state = STATE_READY

        state_seen: list[str] = []

        async def _spy_drain() -> None:
            # Record state at the moment drain is called
            state_seen.append(mgr.state)

        async def _run() -> None:
            with (
                patch.object(mgr, "_drain_task", side_effect=_spy_drain),
                patch.object(mgr, "start", new_callable=AsyncMock),
            ):
                await mgr.restart()

        asyncio.run(_run())

        assert STATE_INDEXING in state_seen, (
            f"Expected state=indexing when _drain_task was called; got {state_seen}"
        )

    def test_state_is_indexing_before_first_await(self) -> None:
        """Direct check: _drain_task sees state=indexing when called."""
        mgr = _make_ready_manager()
        mgr.state = STATE_READY

        captured: list[str] = []

        async def _fake_drain() -> None:
            captured.append(mgr.state)

        async def _run() -> None:
            with (
                patch.object(mgr, "_drain_task", side_effect=_fake_drain),
                patch.object(mgr, "start", new_callable=AsyncMock),
            ):
                await mgr.restart()

        asyncio.run(_run())
        assert captured == [STATE_INDEXING]

    def test_safe_when_task_is_none(self) -> None:
        """restart() must not raise when _task is None."""
        mgr = _make_ready_manager()
        mgr._task = None
        mgr.state = STATE_READY

        async def _run() -> None:
            with patch.object(mgr, "start", new_callable=AsyncMock):
                await mgr.restart()

        # Should complete without error
        asyncio.run(_run())

    def test_replaces_shutdown_event(self) -> None:
        """After restart(), _shutdown_event must be a new (unset) Event object."""
        mgr = _make_ready_manager()
        old_shutdown = mgr._shutdown_event
        old_shutdown.set()  # simulate consumed event

        async def _run() -> None:
            with patch.object(mgr, "start", new_callable=AsyncMock):
                await mgr.restart()

        asyncio.run(_run())

        assert mgr._shutdown_event is not old_shutdown
        assert not mgr._shutdown_event.is_set()

    def test_replaces_ready_event(self) -> None:
        """After restart(), _ready_event must be a new (unset) Event object."""
        mgr = _make_ready_manager()
        old_ready = mgr._ready_event
        old_ready.set()  # simulate consumed event

        async def _run() -> None:
            with patch.object(mgr, "start", new_callable=AsyncMock):
                await mgr.restart()

        asyncio.run(_run())

        assert mgr._ready_event is not old_ready
        assert not mgr._ready_event.is_set()

    def test_calls_start_to_respawn_task(self) -> None:
        """restart() must call start() to spawn a new background task."""
        mgr = _make_ready_manager()
        start_mock = AsyncMock()

        async def _run() -> None:
            with patch.object(mgr, "start", start_mock):
                await mgr.restart()

        asyncio.run(_run())

        start_mock.assert_called_once()


# ---------------------------------------------------------------------------
# indexed_commit property tests
# ---------------------------------------------------------------------------


class TestIndexedCommit:
    """indexed_commit is None initially and exposed via the property."""

    def test_none_initially(self) -> None:
        """A freshly constructed manager has indexed_commit == None."""
        mgr = AnalyzerManager(rust_analyzer_bin="/fake/ra", repository_root="/tmp")
        assert mgr.indexed_commit is None

    def test_exposed_via_property(self) -> None:
        """indexed_commit property reflects _indexed_commit attribute."""
        mgr = AnalyzerManager.__new__(AnalyzerManager)
        mgr._indexed_commit = None
        assert mgr.indexed_commit is None

        mgr._indexed_commit = "abc123"
        assert mgr.indexed_commit == "abc123"

    def test_property_not_raw_attribute(self) -> None:
        """Accessing indexed_commit goes through the property (not __dict__ directly)."""
        # Confirm it's a property on the class
        assert isinstance(AnalyzerManager.indexed_commit, property)


# ---------------------------------------------------------------------------
# _capture_head_commit tests (monkeypatched subprocess)
# ---------------------------------------------------------------------------


class TestCaptureHeadCommit:
    """_capture_head_commit stores hash on success, None on failure."""

    def test_stores_stripped_hash_on_success(self) -> None:
        """When git returns 0, _indexed_commit is set to the stripped hash."""
        mgr = AnalyzerManager.__new__(AnalyzerManager)
        mgr._repository_root = "/fake/repo"
        mgr._indexed_commit = None

        fake_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="deadbeef1234567890abcdef\n", stderr=""
        )

        async def _run() -> None:
            with patch("rust_lsp_mcp.analyzer.subprocess.run", return_value=fake_result):
                await mgr._capture_head_commit()

        asyncio.run(_run())

        assert mgr._indexed_commit == "deadbeef1234567890abcdef"

    def test_sets_none_on_nonzero_exit(self) -> None:
        """When git returns non-zero, _indexed_commit is set to None."""
        mgr = AnalyzerManager.__new__(AnalyzerManager)
        mgr._repository_root = "/not-a-git-repo"
        mgr._indexed_commit = "old-value"

        fake_result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=[],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository\n",
        )

        async def _run() -> None:
            with patch("rust_lsp_mcp.analyzer.subprocess.run", return_value=fake_result):
                await mgr._capture_head_commit()

        asyncio.run(_run())

        assert mgr._indexed_commit is None

    def test_sets_none_when_subprocess_raises(self) -> None:
        """When subprocess.run raises (e.g. git not found), _indexed_commit is None."""
        mgr = AnalyzerManager.__new__(AnalyzerManager)
        mgr._repository_root = "/fake/repo"
        mgr._indexed_commit = "old-value"

        async def _run() -> None:
            with patch(
                "rust_lsp_mcp.analyzer.subprocess.run",
                side_effect=FileNotFoundError("git not found"),
            ):
                await mgr._capture_head_commit()

        asyncio.run(_run())

        assert mgr._indexed_commit is None

    def test_does_not_raise_on_failure(self) -> None:
        """_capture_head_commit must never raise — errors are swallowed."""
        mgr = AnalyzerManager.__new__(AnalyzerManager)
        mgr._repository_root = "/fake/repo"
        mgr._indexed_commit = None

        async def _run() -> None:
            with patch(
                "rust_lsp_mcp.analyzer.subprocess.run",
                side_effect=OSError("something went wrong"),
            ):
                # Must not raise
                await mgr._capture_head_commit()

        asyncio.run(_run())  # no exception expected
