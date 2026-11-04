"""Fast tests for ``PatchedRustAnalyzer._get_initialize_params`` (SW-03, SW-12).

The override sets three things on multilspy's bundled initialize parameters:
the UTF-32 position encoding (KI-5), rust-analyzer's own file watcher
(``files.watcher == "server"``, SW-03) and the manager's ``$/progress``
handler in place of multilspy's ``do_nothing`` (SW-12).  Constructing the
analyzer launches no process and needs no rust-analyzer binary.
"""

from __future__ import annotations

import pathlib
from typing import Any

from multilspy.multilspy_config import Language, MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

from rust_lsp_mcp.analyzer import AnalyzerManager, PatchedRustAnalyzer

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "unicode_crate"


def _make(progress_handler: Any = None) -> PatchedRustAnalyzer:
    return PatchedRustAnalyzer(
        config=MultilspyConfig(code_language=Language.RUST),
        logger=MultilspyLogger(),
        repository_root_path=str(_FIXTURE),
        rust_analyzer_bin="/nonexistent/rust-analyzer",
        progress_handler=progress_handler,
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
