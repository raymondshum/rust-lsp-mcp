"""Integration test for SW-03: rust-analyzer watches the project tree itself.

Marker: ``integration`` (live rust-analyzer + the ripgrep fixture; local QA
gate only, never CI).  The fixture is made by running
``RLM_PROJECT_ROOT=<path> bash scripts/clone-ripgrep.sh`` when absent.

After ``ready``, the test writes a new ``.rs`` file holding a uniquely named
function under ``crates/core/`` (ripgrep's binary crate root is
``crates/core/main.rs``), adds its ``mod`` line, and polls ``find_symbol``
every two seconds for up to 60 seconds.  The symbol must be found without
``refresh``: with ``files.watcher == "server"`` rust-analyzer sees the change
through its own watcher.  The fixture is restored afterwards.

A second case (backlog line 49) saves an existing ``.rs`` file in place,
commits it in the fixture clone with an explicit git identity (the product
image sets none), and expects ``status`` to read ``stale: false`` within 60
seconds without ``refresh``.
"""

import asyncio
import pathlib
import subprocess
import time
import uuid
from typing import Any
from unittest.mock import patch

import anyio
import pytest

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import HEAD_SETTLE_SECONDS, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_OK
from rust_lsp_mcp.settings import get_settings
from rust_lsp_mcp.tools.find_symbol import find_symbol
from rust_lsp_mcp.tools.status import status

_READY_TIMEOUT_S = 300
_POLL_INTERVAL_S = 2.0
_POLL_DEADLINE_S = 60.0


async def _find(manager: AnalyzerManager, name: str) -> dict[str, Any]:
    with patch.object(core, "_manager", manager):
        return await find_symbol(name)


@pytest.mark.integration
def test_new_file_found_without_refresh() -> None:
    settings = get_settings()
    root = pathlib.Path(settings.project_root)
    main_rs = root / "crates" / "core" / "main.rs"
    assert main_rs.is_file(), f"ripgrep fixture missing at {root}"
    module = f"rlm_freshness_{uuid.uuid4().hex[:8]}"
    symbol = f"{module}_probe_fn"
    new_rs = root / "crates" / "core" / f"{module}.rs"
    original_main = main_rs.read_text(encoding="utf-8")

    async def _scenario() -> float:
        manager = AnalyzerManager(
            rust_analyzer_bin=settings.rust_analyzer_bin,
            repository_root=settings.project_root,
        )
        await manager.start()
        try:
            with anyio.fail_after(_READY_TIMEOUT_S):
                await manager._ready_event.wait()
            assert manager.state == STATE_READY

            before = await _find(manager, symbol)
            assert before["status"] != STATUS_OK or not before.get("results"), before

            new_rs.write_text(
                f"/// Freshness probe.\npub(crate) fn {symbol}() -> u32 {{\n    7\n}}\n",
                encoding="utf-8",
            )
            main_rs.write_text(f"{original_main.rstrip()}\nmod {module};\n", encoding="utf-8")

            start = time.monotonic()
            last: dict[str, Any] = {}
            while time.monotonic() - start < _POLL_DEADLINE_S:
                last = await _find(manager, symbol)
                names = [r.get("name") for r in last.get("results") or []]
                if last["status"] == STATUS_OK and symbol in names:
                    return time.monotonic() - start
                await asyncio.sleep(_POLL_INTERVAL_S)
            pytest.fail(f"{symbol} not found within {_POLL_DEADLINE_S}s without refresh: {last}")
        finally:
            await manager.shutdown()

    try:
        elapsed = asyncio.run(_scenario())
    finally:
        main_rs.write_text(original_main, encoding="utf-8")
        new_rs.unlink(missing_ok=True)
    assert elapsed < _POLL_DEADLINE_S


def _git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


async def _status(manager: AnalyzerManager) -> dict[str, Any]:
    with patch.object(core, "_manager", manager):
        return await status()


@pytest.mark.integration
def test_commit_of_saved_edit_clears_stale() -> None:
    """Backlog line 49: committing an already-saved edit must not leave ``stale: true``.

    The edit saves an existing ``.rs`` file in place (no new file), so
    rust-analyzer sends no quiescent transition and starts no re-index; the
    commit then moves HEAD over a tree the index already reflects, and
    ``status`` must re-capture ``indexed_commit`` itself (``reconcile_head``).
    The fixture is restored afterwards: the commit is dropped and the edit
    reverted.
    """
    settings = get_settings()
    root = pathlib.Path(settings.project_root)
    edited = root / "crates" / "searcher" / "src" / "lib.rs"
    assert edited.is_file(), f"ripgrep fixture missing at {root}"
    original_head = _git(root, "rev-parse", "HEAD")
    original_text = edited.read_text(encoding="utf-8")

    async def _scenario() -> float:
        manager = AnalyzerManager(
            rust_analyzer_bin=settings.rust_analyzer_bin,
            repository_root=settings.project_root,
        )
        await manager.start()
        try:
            with anyio.fail_after(_READY_TIMEOUT_S):
                await manager._ready_event.wait()
            assert manager.state == STATE_READY

            edited.write_text(f"{original_text.rstrip()}\n// rlm freshness probe\n", "utf-8")
            start = time.monotonic()
            while (await _status(manager))["stale"] is not False:
                if time.monotonic() - start > _POLL_DEADLINE_S:
                    pytest.fail("stale never read false after the in-place save")
                await asyncio.sleep(_POLL_INTERVAL_S)
            await asyncio.sleep(HEAD_SETTLE_SECONDS + 1)

            _git(
                root,
                "-c",
                "user.name=rlm-test",
                "-c",
                "user.email=rlm-test@invalid",
                "commit",
                "-qam",
                "rlm freshness probe",
            )
            start = time.monotonic()
            last: dict[str, Any] = {}
            while time.monotonic() - start < _POLL_DEADLINE_S:
                last = await _status(manager)
                if last["stale"] is False:
                    assert last["indexed_commit"] == last["current_commit"] != original_head
                    return time.monotonic() - start
                await asyncio.sleep(_POLL_INTERVAL_S)
            pytest.fail(f"stale still true {_POLL_DEADLINE_S}s after the commit: {last}")
        finally:
            await manager.shutdown()

    try:
        elapsed = asyncio.run(_scenario())
    finally:
        if _git(root, "rev-parse", "HEAD") != original_head:
            _git(root, "reset", "-q", "--mixed", original_head)
        edited.write_text(original_text, encoding="utf-8")
    assert elapsed < _POLL_DEADLINE_S
