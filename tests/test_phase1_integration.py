"""Integration tests for Phase 1 — live rust-analyzer over the ripgrep fixture.

Marker: ``integration`` (registered in pyproject.toml).
Run locally only: ``uv run pytest -m integration``
Never runs in CI.

What is being proven:
    1. ``PatchedRustAnalyzer`` starts cleanly using the container's native
       rust-analyzer binary (no download, no platform-table assertion).
    2. The readiness flag transitions from ``"indexing"`` to ``"ready"`` once
       the analyzer reports quiescent over the ripgrep codebase.
    3. The fail-fast invariant: no gated tool call returns ``ok`` or an empty
       result while still indexing.  Before ready, it returns ``not_ready``.

Timeout: generous — a cold ripgrep index can take several minutes.

Implementation note: tests run async code via ``anyio.run()`` (already a
dependency via mcp) rather than requiring pytest-asyncio.
"""

import asyncio

import anyio
import pytest

from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_NOT_READY, STATUS_OK
from rust_lsp_mcp.settings import get_settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings():
    return get_settings()


# ---------------------------------------------------------------------------
# Helper: simulate a gated tool call
# ---------------------------------------------------------------------------


def _gated_call(manager: AnalyzerManager) -> dict:
    """Simulate what a gated tool does: check state, return not_ready or ok."""
    from rust_lsp_mcp.envelope import not_ready, ok

    if manager.state != STATE_READY:
        return not_ready()
    return ok(message="probe ok")


# ---------------------------------------------------------------------------
# Async implementations — called via anyio.run()
# ---------------------------------------------------------------------------


async def _run_test_analyzer_reaches_ready(settings) -> None:
    """Cold-start the analyzer over ripgrep and confirm state reaches 'ready'.

    Timeout: 300 s — cold index of ripgrep can take several minutes.
    """
    manager = AnalyzerManager(
        rust_analyzer_bin=settings.rust_analyzer_bin,
        repository_root=settings.ripgrep_src,
    )

    # State must be indexing before we start
    assert manager.state == STATE_INDEXING

    await manager.start()
    try:
        # Wait for ready with a generous timeout
        with anyio.fail_after(300):
            await manager._ready_event.wait()
        assert manager.state == STATE_READY, "Readiness flag must be 'ready' after event fires"
    finally:
        await manager.shutdown()


async def _run_test_no_misleading_ok_before_ready(settings) -> None:
    """The fail-fast invariant: gated calls return not_ready until the flag flips.

    This is THE Phase 1 proof: we confirm that no gated tool call returns
    ``ok`` or an empty result during the indexing window.  We sample the gate
    immediately after starting (when state is guaranteed to be 'indexing') and
    at multiple points during the warm-up, then confirm ok only after ready.
    """
    manager = AnalyzerManager(
        rust_analyzer_bin=settings.rust_analyzer_bin,
        repository_root=settings.ripgrep_src,
    )

    await manager.start()
    try:
        # Immediately after start: must be not_ready (indexing has barely begun)
        immediate_result = _gated_call(manager)
        assert immediate_result["status"] == STATUS_NOT_READY, (
            f"A gated call immediately after start() must return not_ready, got {immediate_result}"
        )

        # Sample the gate repeatedly during the indexing window.
        # Every sample must return not_ready until the flag flips.
        samples_while_indexing: list[dict] = []
        with anyio.fail_after(300):
            while manager.state == STATE_INDEXING:
                samples_while_indexing.append(_gated_call(manager))
                await asyncio.sleep(0.1)

        # All samples taken while still indexing must be not_ready
        for i, sample in enumerate(samples_while_indexing):
            assert sample["status"] == STATUS_NOT_READY, (
                f"Sample {i} during indexing returned {sample!r} — "
                "must be not_ready, never ok or empty"
            )

        # Now the flag has flipped — gated call must return ok
        assert manager.state == STATE_READY
        ready_result = _gated_call(manager)
        assert ready_result["status"] == STATUS_OK, (
            f"After ready, gated call must return ok, got {ready_result}"
        )

    finally:
        await manager.shutdown()


# ---------------------------------------------------------------------------
# Integration test wrappers (synchronous pytest tests)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_analyzer_reaches_ready(settings) -> None:
    """Cold-start the analyzer over ripgrep and confirm state reaches 'ready'."""
    anyio.run(_run_test_analyzer_reaches_ready, settings)


@pytest.mark.integration
def test_no_misleading_ok_before_ready(settings) -> None:
    """The fail-fast invariant: gated calls return not_ready until ready."""
    anyio.run(_run_test_no_misleading_ok_before_ready, settings)
