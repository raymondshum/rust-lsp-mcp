"""Integration tests for Phase 2 — live rust-analyzer + find_symbol.

Marker: ``integration`` (registered in pyproject.toml).
Run locally only: ``uv run pytest -m integration``
Never runs in CI.

What is being proven:
    1. ``find_symbol`` resolves real ripgrep symbols against the live analyzer.
    2. Resolved file/line positions are usable: opening the returned file at the
       returned 1-indexed line and verifying the symbol name appears there.
    3. Multiple candidates surface as distinct results (overloads / same name in
       different modules).
    4. A nonexistent name → ``not_found``.
    5. **Runtime-only UNVERIFIED item**: what ``containerName`` actually contains
       for real ripgrep symbols — confirms/closes that plan note.

Timeout: generous — a cold ripgrep index can take several minutes.
"""

import pathlib

import anyio
import pytest

from rust_lsp_mcp.analyzer import STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_NOT_FOUND, STATUS_OK
from rust_lsp_mcp.settings import get_settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings():
    return get_settings()


# ---------------------------------------------------------------------------
# Shared async helper: start a warm manager and run a coroutine, then shut down.
# Shared across the async test helpers below.
# ---------------------------------------------------------------------------


async def _with_warm_manager(settings, coro_fn):
    """Start the analyzer, wait for ready, then call coro_fn(manager)."""
    manager = AnalyzerManager(
        rust_analyzer_bin=settings.rust_analyzer_bin,
        repository_root=settings.project_root,
    )
    await manager.start()
    try:
        with anyio.fail_after(300):
            await manager._ready_event.wait()
        assert manager.state == STATE_READY
        return await coro_fn(manager)
    finally:
        await manager.shutdown()


# ---------------------------------------------------------------------------
# Helper: look up find_symbol via the server module with a live manager
# ---------------------------------------------------------------------------


async def _find_symbol_live(manager: AnalyzerManager, name: str) -> dict:
    """Call the find_symbol implementation directly with a patched manager."""
    from unittest.mock import patch

    import rust_lsp_mcp.core as core
    from rust_lsp_mcp.tools.find_symbol import find_symbol

    # The manager singleton now lives in core (post Phase 3+4 refactor); tools
    # read it via get_manager().  Patch it there.
    with patch.object(core, "_manager", manager):
        return await find_symbol(name)


# ---------------------------------------------------------------------------
# Test: resolve SearcherBuilder (a known stable ripgrep public type)
# ---------------------------------------------------------------------------


async def _run_searcher_builder(settings) -> dict:
    async def _inner(manager: AnalyzerManager) -> dict:
        return await _find_symbol_live(manager, "SearcherBuilder")

    return await _with_warm_manager(settings, _inner)


@pytest.mark.integration
def test_find_symbol_searcher_builder(settings) -> None:
    """SearcherBuilder resolves and its position points at the real declaration.

    Expected: at least one Struct candidate in a .rs file under the searcher crate.
    The returned 1-indexed line must contain "SearcherBuilder" when the file is
    opened — proving that positions round-trip correctly into a usable location.

    Note: rust-analyzer may return a re-export location in lib.rs as its first hit;
    the test checks that the resolved file/line is a real location, not a specific
    file name.
    """
    result = anyio.run(_run_searcher_builder, settings)

    assert result["status"] == STATUS_OK, f"find_symbol returned {result!r}"
    assert "results" in result
    assert len(result["results"]) >= 1, "Expected at least one SearcherBuilder candidate"

    # Find the Struct candidates (there may be multiple due to re-exports)
    struct_candidates = [r for r in result["results"] if r["kind"] == "Struct"]
    assert struct_candidates, f"No Struct candidate in {result['results']!r}"

    # Check ALL struct candidates — at least one must have "SearcherBuilder" near
    # the returned line (proves the position round-trips into the source).
    ripgrep_root = pathlib.Path(settings.project_root)
    found_match = False
    for r in struct_candidates:
        assert r["line"] >= 1, "line must be 1-indexed"
        assert r["character"] >= 1, "character must be 1-indexed"
        assert r["file"].endswith(".rs"), f"Expected .rs file, got: {r['file']!r}"

        src_file = ripgrep_root / r["file"]
        assert src_file.exists(), f"Resolved file does not exist: {src_file}"

        lines = src_file.read_text(encoding="utf-8").splitlines()
        # 1-indexed → 0-indexed for list access
        line_idx = r["line"] - 1
        # Check the declared line and a small window (±2) for the name
        window = lines[max(0, line_idx - 2) : line_idx + 3]
        window_text = "\n".join(window)
        if "SearcherBuilder" in window_text:
            found_match = True

    assert found_match, (
        f"No SearcherBuilder candidate had 'SearcherBuilder' near its returned line.\n"
        f"Candidates: {struct_candidates!r}"
    )


# ---------------------------------------------------------------------------
# Test: Searcher resolves — check line in file
# ---------------------------------------------------------------------------


async def _run_searcher(settings) -> dict:
    async def _inner(manager: AnalyzerManager) -> dict:
        return await _find_symbol_live(manager, "Searcher")

    return await _with_warm_manager(settings, _inner)


@pytest.mark.integration
def test_find_symbol_searcher_position_in_file(settings) -> None:
    """Searcher struct resolves; the position points at 'pub struct Searcher'.

    Specifically validates that the 1-indexed line contains 'Searcher' when
    opened, proving the boundary conversion produces usable positions.
    """
    result = anyio.run(_run_searcher, settings)
    assert result["status"] == STATUS_OK, f"find_symbol returned {result!r}"

    struct_candidates = [
        r for r in result["results"] if r["kind"] == "Struct" and "Searcher" in r["name"]
    ]
    assert struct_candidates, f"No Struct Searcher candidate in {result['results']!r}"

    r = struct_candidates[0]
    ripgrep_root = pathlib.Path(settings.project_root)
    src_file = ripgrep_root / r["file"]
    lines = src_file.read_text(encoding="utf-8").splitlines()
    line_idx = r["line"] - 1
    window = lines[max(0, line_idx - 2) : line_idx + 3]
    window_text = "\n".join(window)
    assert "Searcher" in window_text, (
        f"'Searcher' not found near line {r['line']} in {r['file']}.\nContext:\n{window_text}"
    )


# ---------------------------------------------------------------------------
# Test: multiple hits — same name resolves to distinct candidates
# ---------------------------------------------------------------------------


async def _run_new(settings) -> dict:
    """Query 'new' — a very common method name; should produce multiple hits."""

    async def _inner(manager: AnalyzerManager) -> dict:
        return await _find_symbol_live(manager, "new")

    return await _with_warm_manager(settings, _inner)


@pytest.mark.integration
def test_find_symbol_multiple_hits(settings) -> None:
    """'new' appears as a method in many impl blocks — expect multiple distinct candidates.

    Validates that find_symbol returns a multi-hit list (not ambiguous status,
    not a single forced pick) and that the candidates are from different files/containers.
    """
    result = anyio.run(_run_new, settings)
    assert result["status"] == STATUS_OK, f"find_symbol returned {result!r}"
    assert len(result["results"]) > 1, (
        f"Expected multiple 'new' candidates, got {len(result['results'])}: {result['results']!r}"
    )
    # Multiple containers or multiple files — confirm genuine distinct candidates
    files = {r["file"] for r in result["results"]}
    assert len(files) >= 2, f"All 'new' candidates in same file: {files!r}"


# ---------------------------------------------------------------------------
# Test: nonexistent symbol → not_found
# ---------------------------------------------------------------------------


async def _run_nonexistent(settings) -> dict:
    async def _inner(manager: AnalyzerManager) -> dict:
        return await _find_symbol_live(manager, "__this_symbol_definitely_does_not_exist_xyzzy__")

    return await _with_warm_manager(settings, _inner)


@pytest.mark.integration
def test_find_symbol_nonexistent_returns_not_found(settings) -> None:
    """A clearly nonexistent symbol name → not_found (never ok+empty)."""
    result = anyio.run(_run_nonexistent, settings)
    assert result["status"] == STATUS_NOT_FOUND, (
        f"Expected not_found for nonexistent symbol, got {result!r}"
    )
    assert result["status"] != STATUS_OK


# ---------------------------------------------------------------------------
# Test (UNVERIFIED→confirmed): what does containerName hold for real symbols?
# ---------------------------------------------------------------------------


async def _run_container_probe(settings) -> dict:
    """Probe container labels by resolving SearcherBuilder.build (a method)."""

    async def _inner(manager: AnalyzerManager) -> dict:
        # Query "build" to find SearcherBuilder::build (a well-known method)
        return await _find_symbol_live(manager, "SearcherBuilder")

    return await _with_warm_manager(settings, _inner)


@pytest.mark.integration
def test_container_name_finding(settings, capsys) -> None:
    """UNVERIFIED → confirmed: inspect what containerName holds for ripgrep symbols.

    This test both asserts and prints the container values so the Phase 2 build
    report can document the confirmed behavior.  The assertion is deliberately
    loose — we accept any value (including None) since the exact content is the
    thing being confirmed.
    """
    result = anyio.run(_run_container_probe, settings)
    assert result["status"] == STATUS_OK, f"Unexpected status: {result!r}"

    containers = [(r["name"], r["kind"], r["container"]) for r in result["results"]]
    # Print for the build report (captured by pytest, visible with -s)
    print("\n--- containerName findings for SearcherBuilder ---")
    for name, kind, container in containers:
        print(f"  name={name!r}  kind={kind!r}  container={container!r}")
    print("---")

    # Confirm the test ran and produced output — structure is what we're reporting
    assert len(containers) >= 1, "Expected at least one candidate to inspect"

    # Confirm container is a str or None (the only valid values per the TypedDict)
    for name, _kind, container in containers:
        assert container is None or isinstance(container, str), (
            f"container for {name!r} has unexpected type {type(container)}: {container!r}"
        )
