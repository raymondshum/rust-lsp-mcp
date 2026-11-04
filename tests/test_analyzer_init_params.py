"""Fast tests for ``PatchedRustAnalyzer._get_initialize_params`` (SW-03, SW-05, SW-07, SW-12).

The override sets, on multilspy's bundled initialize parameters: the UTF-32
position encoding (KI-5), rust-analyzer's own file watcher
(``files.watcher == "server"``, SW-03), the ``all_symbols`` workspace-symbol
search with a limit one past the tool cap (SW-05) and ``checkOnSave: false``
(SW-09).  It binds the manager's ``$/progress`` handler in place of
multilspy's ``do_nothing`` (SW-12) and its own ``experimental/serverStatus``
handler in place of multilspy's ``check_experimental_status`` (SW-07).
Constructing the analyzer launches no process and needs no rust-analyzer
binary.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from multilspy.multilspy_config import Language, MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

from rust_lsp_mcp.analyzer import STATE_READY, AnalyzerManager, PatchedRustAnalyzer
from rust_lsp_mcp.core import MAX_LIST_RESULTS

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "unicode_crate"


def _make(progress_handler: Any = None, status_handler: Any = None) -> PatchedRustAnalyzer:
    return PatchedRustAnalyzer(
        config=MultilspyConfig(code_language=Language.RUST),
        logger=MultilspyLogger(),
        repository_root_path=str(_FIXTURE),
        rust_analyzer_bin="/nonexistent/rust-analyzer",
        progress_handler=progress_handler,
        status_handler=status_handler,
    )


def test_watcher_is_server() -> None:
    params: Any = _make()._get_initialize_params(str(_FIXTURE))
    assert params["initializationOptions"]["files"]["watcher"] == "server"


def test_position_encoding_unchanged() -> None:
    params: Any = _make()._get_initialize_params(str(_FIXTURE))
    assert params["capabilities"]["general"]["positionEncodings"] == ["utf-32", "utf-16"]


def test_progress_handler_replaces_do_nothing() -> None:
    """start_server() binds $/progress to do_nothing before it calls
    _get_initialize_params; the override's later binding must replace it."""
    mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root=str(_FIXTURE))
    handler = mgr._make_progress_handler(mgr._generation)
    analyzer = _make(progress_handler=handler)

    async def do_nothing(params: Any) -> None:
        return

    # Mirror multilspy's rust_analyzer.py line 152 registration.
    analyzer.server.on_notification("$/progress", do_nothing)
    analyzer._get_initialize_params(str(_FIXTURE))
    assert analyzer.server.on_notification_handlers["$/progress"] is handler


def test_no_handler_leaves_registration_alone() -> None:
    analyzer = _make()

    async def do_nothing(params: Any) -> None:
        return

    analyzer.server.on_notification("$/progress", do_nothing)
    analyzer._get_initialize_params(str(_FIXTURE))
    assert analyzer.server.on_notification_handlers["$/progress"] is do_nothing


def test_symbol_search_all_symbols_with_limit_past_cap() -> None:
    params: Any = _make()._get_initialize_params(str(_FIXTURE))
    search = params["initializationOptions"]["workspace"]["symbol"]["search"]
    assert search["kind"] == "all_symbols"
    assert search["limit"] == 201
    assert search["limit"] == MAX_LIST_RESULTS + 1


def test_check_on_save_off() -> None:
    params: Any = _make()._get_initialize_params(str(_FIXTURE))
    assert params["initializationOptions"]["checkOnSave"] is False


def test_server_status_handler_replaces_multilspy_closure() -> None:
    """start_server() binds experimental/serverStatus to its own closure before
    it calls _get_initialize_params; the override's later binding must replace it."""
    analyzer = _make()

    async def check_experimental_status(params: Any) -> None:
        return

    # Mirror multilspy's rust_analyzer.py line 155 registration.
    analyzer.server.on_notification("experimental/serverStatus", check_experimental_status)
    analyzer._get_initialize_params(str(_FIXTURE))
    handler = analyzer.server.on_notification_handlers["experimental/serverStatus"]
    assert handler == analyzer._on_server_status


def test_server_status_handler_sets_ready_and_forwards_health() -> None:
    calls: list[tuple[Any, Any, Any]] = []

    async def status_handler(health: Any, message: Any, quiescent: Any) -> None:
        calls.append((health, message, quiescent))

    analyzer = _make(status_handler=status_handler)
    analyzer._get_initialize_params(str(_FIXTURE))
    handler = analyzer.server.on_notification_handlers["experimental/serverStatus"]

    async def _scenario() -> None:
        await handler({"health": "warning", "quiescent": False, "message": "loading"})
        assert not analyzer.server_ready.is_set()
        await handler({"health": "error", "quiescent": True, "message": "Failed to load"})
        assert analyzer.server_ready.is_set()

    asyncio.run(_scenario())
    assert calls == [("warning", "loading", False), ("error", "Failed to load", True)]


def test_server_status_handler_without_status_handler_still_sets_ready() -> None:
    analyzer = _make()

    asyncio.run(analyzer._on_server_status({"health": "ok", "quiescent": True}))
    assert analyzer.server_ready.is_set()


def test_manager_status_handler_records_health() -> None:
    mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root=str(_FIXTURE))
    handler = mgr._make_status_handler(mgr._generation)
    assert mgr.analyzer_health is None
    asyncio.run(handler("error", "cargo metadata failed", True))
    assert mgr.analyzer_health == "error"
    assert mgr.analyzer_health_message == "cargo metadata failed"

    stale = mgr._make_status_handler(mgr._generation - 1)
    asyncio.run(stale("ok", None, True))
    assert mgr.analyzer_health == "error"


def test_cargo_cfgs_not_sent_as_a_map() -> None:
    """R-gate break 4: multilspy bundles ``cargo.cfgs = {}``; rust-analyzer
    wants a list and reports health "warning" on every load, masking a real
    workspace-load warning or error."""
    params: Any = _make()._get_initialize_params(str(_FIXTURE))
    cfgs = params["initializationOptions"].get("cargo", {}).get("cfgs", [])
    assert isinstance(cfgs, list), f"cargo.cfgs is {cfgs!r}"


def test_health_logging_first_ok_silent_then_changes_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Deviation 4 (R-gate item 9): the first report of a run is logged only
    when it is not "ok"; every later change of health is logged at warning."""
    caplog.set_level(logging.DEBUG, logger="rust_lsp_mcp.analyzer")
    mgr = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root=str(_FIXTURE))
    handler = mgr._make_status_handler(mgr._generation)

    def _health_logs() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.getMessage().startswith("rust-analyzer health")]

    asyncio.run(handler("ok", None, True))
    assert _health_logs() == []
    asyncio.run(handler("ok", None, True))
    assert _health_logs() == []
    asyncio.run(handler("error", "Failed to load workspaces", True))
    asyncio.run(handler("error", "Failed to load workspaces", True))
    asyncio.run(handler("ok", None, True))
    records = _health_logs()
    assert [r.getMessage() for r in records] == [
        "rust-analyzer health error: Failed to load workspaces",
        "rust-analyzer health ok: ",
    ]
    assert {r.levelno for r in records} == {logging.WARNING}

    caplog.clear()
    fresh = AnalyzerManager(rust_analyzer_bin="/nonexistent", repository_root=str(_FIXTURE))
    asyncio.run(fresh._make_status_handler(fresh._generation)("warning", "proc-macro", True))
    assert [r.getMessage() for r in _health_logs()] == ["rust-analyzer health warning: proc-macro"]


def _sym(name: str, uri: str) -> dict[str, Any]:
    return {
        "name": name,
        "kind": 12,
        "location": {
            "uri": uri,
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
        },
    }


def test_find_symbol_truncated_counts_server_items_before_workspace_filter() -> None:
    """R-gate break 1: live on ripgrep, rust-analyzer returned 201 items for
    ``find_symbol("s")`` and 2 were dependency crate roots outside the
    workspace; the tool capped 199 and reported ``truncated: false``."""
    import rust_lsp_mcp.core as core_mod
    from rust_lsp_mcp.tools.find_symbol import find_symbol

    root = "/fake/repo"
    raw = [_sym("aho_corasick", "file:///data/cargo-home/registry/src/x/aho-corasick/src/lib.rs")]
    raw += [_sym(f"s_fn_{i}", f"file://{root}/src/lib.rs") for i in range(MAX_LIST_RESULTS - 1)]
    raw += [_sym("bstr", "file:///data/cargo-home/registry/src/x/bstr/src/lib.rs")]
    assert len(raw) == MAX_LIST_RESULTS + 1

    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = STATE_READY
    mgr._lsp = object()  # type: ignore[assignment]
    mgr._repository_root = root

    async def _inner() -> dict[str, Any]:
        with (
            patch.object(core_mod, "_manager", mgr),
            patch.object(mgr, "request_workspace_symbol", new=AsyncMock(return_value=raw)),
        ):
            return await find_symbol("s")

    result = asyncio.run(_inner())
    assert result["status"] == "ok"
    assert len(result["results"]) == MAX_LIST_RESULTS - 1
    assert result["truncated"] is True
    assert result["total"] == MAX_LIST_RESULTS - 1


def test_find_symbol_not_truncated_at_exactly_cap() -> None:
    import rust_lsp_mcp.core as core_mod
    from rust_lsp_mcp.tools.find_symbol import find_symbol

    root = "/fake/repo"
    raw = [_sym(f"s_fn_{i}", f"file://{root}/src/lib.rs") for i in range(MAX_LIST_RESULTS)]
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = STATE_READY
    mgr._lsp = object()  # type: ignore[assignment]
    mgr._repository_root = root

    async def _inner() -> dict[str, Any]:
        with (
            patch.object(core_mod, "_manager", mgr),
            patch.object(mgr, "request_workspace_symbol", new=AsyncMock(return_value=raw)),
        ):
            return await find_symbol("s")

    result = asyncio.run(_inner())
    assert result["truncated"] is False
    assert result["total"] == MAX_LIST_RESULTS
