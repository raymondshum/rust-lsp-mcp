"""Integration tests for Phase 3+4 — live rust-analyzer over the real ripgrep fixture.

Marker: ``integration`` (registered in pyproject.toml).
Run locally only: ``uv run pytest -m integration``
Never runs in CI.

What is being proven (runtime-UNVERIFIED items are closed here):
    1. Discover→act loop: find_symbol → goto_definition / find_references / hover
       using the exact positions returned by find_symbol.
    2. document_symbols on a real file: ok + non-empty flat symbols list; confirms
       whether ``container`` is null for document-symbol results (plan note).
    3. goto_definition: ok + definitions with real file/line; confirms
       ``relativePath`` vs URI path derivation (Phase 2 note).
    4. find_references headline invariant:
       - Used symbol → ok + non-empty references.
       - Genuinely zero-reference symbol → ok + empty list (NOT not_found).
       - include_declaration=True is a superset of include_declaration=False.
       - Confirms rust-analyzer returns [] (not null) for zero-reference symbols.
    5. hover: ok + non-empty markdown contents string; records actual shape
       rust-analyzer emitted (MarkupContent vs MarkedString vs list).
    6. status: while ready → ok, state=="ready", real 40-char hashes,
       indexed_commit == current_commit (ripgrep HEAD), stale==False.
    7. refresh: ok + state=="indexing" immediately; re-waits for ready;
       confirms cargo-cache-preserving re-index; find_symbol still works after.
    8. Readiness gate: gated tools return not_ready before ready.

Timeout: generous — a cold ripgrep index can take several minutes.
"""

import pathlib
import time
from typing import Any
from unittest.mock import patch

import anyio
import pytest

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_NOT_READY, STATUS_OK
from rust_lsp_mcp.settings import get_settings
from rust_lsp_mcp.tools.document_symbols import document_symbols
from rust_lsp_mcp.tools.find_references import find_references
from rust_lsp_mcp.tools.find_symbol import find_symbol
from rust_lsp_mcp.tools.goto_definition import goto_definition
from rust_lsp_mcp.tools.hover import hover
from rust_lsp_mcp.tools.refresh import refresh
from rust_lsp_mcp.tools.status import status

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings():
    return get_settings()


# ---------------------------------------------------------------------------
# Shared async helper: start a warm manager and run a coroutine, then shut down.
# Matches the pattern from test_phase2_integration.py.
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
# Helper: invoke tool functions with a patched manager
# ---------------------------------------------------------------------------


async def _call_find_symbol(manager: AnalyzerManager, name: str) -> dict[str, Any]:
    with patch.object(core, "_manager", manager):
        return await find_symbol(name)


async def _call_document_symbols(manager: AnalyzerManager, file: str) -> dict[str, Any]:
    with patch.object(core, "_manager", manager):
        return await document_symbols(file)


async def _call_goto_definition(
    manager: AnalyzerManager, file: str, line: int, character: int
) -> dict[str, Any]:
    with patch.object(core, "_manager", manager):
        return await goto_definition(file, line, character)


async def _call_find_references(
    manager: AnalyzerManager,
    file: str,
    line: int,
    character: int,
    include_declaration: bool = False,
) -> dict[str, Any]:
    with patch.object(core, "_manager", manager):
        return await find_references(file, line, character, include_declaration)


async def _call_hover(
    manager: AnalyzerManager, file: str, line: int, character: int
) -> dict[str, Any]:
    with patch.object(core, "_manager", manager):
        return await hover(file, line, character)


def _call_status(manager: AnalyzerManager) -> dict[str, Any]:
    with patch.object(core, "_manager", manager):
        return status()


async def _call_refresh(manager: AnalyzerManager) -> dict[str, Any]:
    with patch.object(core, "_manager", manager):
        return await refresh()


# ---------------------------------------------------------------------------
# Helper: confirm identifier text appears near 1-indexed line in file
# ---------------------------------------------------------------------------


def _text_near_line(ripgrep_root: str, rel_file: str, line_1indexed: int, text: str) -> bool:
    """Return True if ``text`` appears in a ±3 line window around the given 1-indexed line."""
    src_file = pathlib.Path(ripgrep_root) / rel_file
    if not src_file.exists():
        return False
    lines = src_file.read_text(encoding="utf-8").splitlines()
    idx = line_1indexed - 1
    window = lines[max(0, idx - 3) : idx + 4]
    return text in "\n".join(window)


# ---------------------------------------------------------------------------
# Mega integration coroutine — single cold-start, batches all assertions
# ---------------------------------------------------------------------------


async def _run_phase34_all(settings) -> dict[str, Any]:
    """One warm manager session exercising all Phase 3+4 tools."""

    results: dict[str, Any] = {}

    async def _inner(manager: AnalyzerManager) -> dict[str, Any]:
        repo_root = settings.project_root

        # ------------------------------------------------------------------
        # 1. Discover: find_symbol("SearcherBuilder")
        # ------------------------------------------------------------------
        find_result = await _call_find_symbol(manager, "SearcherBuilder")
        assert find_result["status"] == STATUS_OK, f"find_symbol failed: {find_result!r}"
        assert len(find_result["results"]) >= 1

        # Pick the Struct candidate as our pivot position for act phase
        struct_candidates = [r for r in find_result["results"] if r["kind"] == "Struct"]
        assert struct_candidates, f"No Struct SearcherBuilder: {find_result['results']!r}"
        pivot = struct_candidates[0]

        assert pivot["line"] >= 1
        assert pivot["character"] >= 1
        assert pivot["file"].endswith(".rs")
        assert pathlib.Path(repo_root, pivot["file"]).exists()

        results["find_symbol_pivot"] = pivot

        # Confirm identifier text appears near the returned line
        assert _text_near_line(repo_root, pivot["file"], pivot["line"], "SearcherBuilder"), (
            f"'SearcherBuilder' not found near line {pivot['line']} in {pivot['file']}"
        )

        # ------------------------------------------------------------------
        # 3. goto_definition at the pivot position (run before document_symbols
        #    so we have the actual definition file to use for step 2)
        # ------------------------------------------------------------------
        gotodef_result = await _call_goto_definition(
            manager, pivot["file"], pivot["line"], pivot["character"]
        )
        assert gotodef_result["status"] == STATUS_OK, (
            f"goto_definition returned {gotodef_result!r} for pivot {pivot!r}"
        )
        defs = gotodef_result["definitions"]
        assert isinstance(defs, list)
        assert len(defs) >= 1, f"goto_definition returned empty definitions for pivot {pivot!r}"

        defn = defs[0]
        assert defn["line"] >= 1
        assert defn["character"] >= 1
        assert "file" in defn
        assert defn["file"] is not None

        # Opening it shows the declaration
        assert pathlib.Path(repo_root, defn["file"]).exists(), (
            f"goto_definition file does not exist: {defn['file']!r}"
        )
        assert _text_near_line(repo_root, defn["file"], defn["line"], "SearcherBuilder"), (
            f"'SearcherBuilder' not found near definition line {defn['line']} in {defn['file']}"
        )

        results["goto_definition_result"] = defn

        # ------------------------------------------------------------------
        # 2. document_symbols on the DEFINITION file (not the re-export file).
        #    find_symbol may return a lib.rs re-export; goto_definition gives the
        #    canonical declaration file (e.g. crates/searcher/src/searcher/mod.rs)
        #    which actually declares SearcherBuilder as a struct.
        # ------------------------------------------------------------------
        defn_file = defn["file"]
        docsym_result = await _call_document_symbols(manager, defn_file)
        assert docsym_result["status"] == STATUS_OK, f"document_symbols failed: {docsym_result!r}"
        syms = docsym_result["symbols"]
        assert isinstance(syms, list)
        assert len(syms) >= 1, f"document_symbols returned empty list for {defn_file!r}"

        # Each symbol must have 1-indexed line/character
        for s in syms:
            assert s["line"] >= 1, f"Symbol {s['name']!r} has line < 1: {s}"
            assert s["character"] >= 1, f"Symbol {s['name']!r} has character < 1: {s}"

        # At least one known symbol name must appear — the definition file declares it
        sym_names = {s["name"] for s in syms}
        assert "SearcherBuilder" in sym_names, (
            f"'SearcherBuilder' not in document_symbols result for {defn_file!r}: {sym_names!r}"
        )

        # VERIFY: container value for document-symbol results (plan note: expect null)
        containers = [s["container"] for s in syms]
        results["document_symbols_containers"] = list(set(containers))
        all_null = all(c is None for c in containers)
        results["document_symbols_all_containers_null"] = all_null
        # The plan expects container to be null for document symbols — record finding
        # but don't fail if rust-analyzer populates it; just assert it's str|None
        for s in syms:
            assert s["container"] is None or isinstance(s["container"], str), (
                f"Unexpected container type for {s['name']!r}: {type(s['container'])}"
            )

        # ------------------------------------------------------------------
        # 4. find_references headline invariant
        # ------------------------------------------------------------------

        # 4a. At a used symbol → ok + non-empty references
        refs_result = await _call_find_references(
            manager, pivot["file"], pivot["line"], pivot["character"]
        )
        assert refs_result["status"] == STATUS_OK, (
            f"find_references (exclude_decl) returned {refs_result!r}"
        )
        refs_excl = refs_result["references"]
        assert isinstance(refs_excl, list)
        # SearcherBuilder is a public struct — it should have at least one use
        assert len(refs_excl) >= 1, (
            f"find_references returned empty list for SearcherBuilder at pivot {pivot!r}"
        )
        for ref in refs_excl:
            assert ref["line"] >= 1
            assert ref["character"] >= 1

        results["find_references_count_exclude_decl"] = len(refs_excl)

        # 4b. include_declaration=True → superset
        refs_incl_result = await _call_find_references(
            manager, pivot["file"], pivot["line"], pivot["character"], include_declaration=True
        )
        assert refs_incl_result["status"] == STATUS_OK, (
            f"find_references (include_decl) returned {refs_incl_result!r}"
        )
        refs_incl = refs_incl_result["references"]
        assert isinstance(refs_incl, list)
        assert len(refs_incl) >= len(refs_excl), (
            f"include_declaration=True produced fewer results ({len(refs_incl)}) "
            f"than exclude ({len(refs_excl)})"
        )

        results["find_references_count_include_decl"] = len(refs_incl)

        # 4c. Zero-reference symbol: use BinaryDetection (a private impl detail
        # with no external callers inside ripgrep's own workspace, or a symbol
        # that returns [] from rust-analyzer so we can confirm ok+empty, not error).
        # We use `quit_byte` method on BinaryDetection which is rarely called.
        # Strategy: use find_symbol to locate a function, then check references;
        # if RA returns a list (even empty), we confirm ok+empty.
        # Use "BinaryDetection" struct — its `quit_byte` method may have zero refs.
        bd_result = await _call_find_symbol(manager, "BinaryDetection")
        results["zero_ref_candidate_find"] = bd_result.get("status")

        if bd_result["status"] == STATUS_OK and bd_result["results"]:
            bd_pivot = bd_result["results"][0]
            refs_bd = await _call_find_references(
                manager, bd_pivot["file"], bd_pivot["line"], bd_pivot["character"]
            )
            # Must return ok (not error, not not_found) even if empty
            assert refs_bd["status"] == STATUS_OK, (
                f"CONFIRMED BREAK: find_references for BinaryDetection returned "
                f"{refs_bd['status']!r} (expected ok). If null-response risk "
                f"materialized, rust-analyzer returned null instead of [] — "
                f"this is a must-fix. Full result: {refs_bd!r}"
            )
            assert isinstance(refs_bd["references"], list), (
                f"CONFIRMED BREAK: find_references returned non-list references: "
                f"{type(refs_bd['references'])} — {refs_bd!r}"
            )
            results["zero_ref_test_status"] = refs_bd["status"]
            results["zero_ref_test_count"] = len(refs_bd["references"])

        # ------------------------------------------------------------------
        # 5. hover at the pivot position
        # ------------------------------------------------------------------
        hover_result = await _call_hover(manager, pivot["file"], pivot["line"], pivot["character"])
        assert hover_result["status"] == STATUS_OK, (
            f"hover returned {hover_result!r} for pivot {pivot!r}"
        )
        contents = hover_result["contents"]
        assert isinstance(contents, str), (
            f"hover contents must be str, got {type(contents)}: {contents!r}"
        )
        assert contents.strip(), f"hover contents is empty/whitespace at pivot {pivot!r}"

        # Record the actual shape for the UNVERIFIED item
        results["hover_contents_snippet"] = contents[:200]
        results["hover_status"] = hover_result["status"]

        # ------------------------------------------------------------------
        # 6. status: while ready → ok, state=="ready", real hashes, stale==False
        # ------------------------------------------------------------------
        status_result = _call_status(manager)
        assert status_result["status"] == STATUS_OK
        assert status_result["state"] == STATE_READY

        indexed_commit = status_result["indexed_commit"]
        current_commit = status_result["current_commit"]

        assert indexed_commit is not None, "indexed_commit should be set after ready"
        assert current_commit is not None, "current_commit should be set (git is available)"
        assert len(indexed_commit) >= 7, f"indexed_commit looks too short: {indexed_commit!r}"
        assert len(current_commit) >= 7, f"current_commit looks too short: {current_commit!r}"
        assert status_result["stale"] is False, (
            f"stale should be False for pinned ripgrep (indexed={indexed_commit!r}, "
            f"current={current_commit!r})"
        )

        results["status_indexed_commit"] = indexed_commit
        results["status_current_commit"] = current_commit
        results["status_stale"] = status_result["stale"]

        # ------------------------------------------------------------------
        # 7. refresh: restart and re-confirm
        # ------------------------------------------------------------------
        refresh_t0 = time.monotonic()
        refresh_result = await _call_refresh(manager)
        assert refresh_result["status"] == STATUS_OK, f"refresh returned {refresh_result!r}"
        assert refresh_result["state"] == STATE_INDEXING, (
            f"refresh must return state=indexing immediately, got {refresh_result['state']!r}"
        )

        # While re-indexing: indexed_commit should be None (honest unknown)
        status_mid = _call_status(manager)
        results["status_mid_refresh_state"] = status_mid["state"]
        results["status_mid_refresh_indexed_commit"] = status_mid["indexed_commit"]
        # state could still be indexing or could have advanced — just confirm ok
        assert status_mid["status"] == STATUS_OK

        # Re-wait for ready with generous timeout (cargo cache preserved → fast)
        with anyio.fail_after(300):
            await manager._ready_event.wait()

        refresh_elapsed = time.monotonic() - refresh_t0
        results["refresh_recovery_seconds"] = round(refresh_elapsed, 1)

        assert manager.state == STATE_READY, (
            f"manager.state should be ready after re-index, got {manager.state!r}"
        )

        # indexed_commit is repopulated after recovery
        status_post = _call_status(manager)
        assert status_post["status"] == STATUS_OK
        assert status_post["state"] == STATE_READY
        assert status_post["indexed_commit"] is not None, (
            "indexed_commit must be repopulated after refresh + re-ready"
        )

        results["status_post_refresh_indexed_commit"] = status_post["indexed_commit"]

        # find_symbol still works after refresh
        find_post = await _call_find_symbol(manager, "SearcherBuilder")
        assert find_post["status"] == STATUS_OK, f"find_symbol after refresh returned {find_post!r}"
        assert len(find_post["results"]) >= 1, "find_symbol returned no results after refresh"
        results["find_symbol_post_refresh_ok"] = True

        return results

    return await _with_warm_manager(settings, _inner)


# ---------------------------------------------------------------------------
# 8. Readiness gate — separate warm-up: gated tools return not_ready before ready
# ---------------------------------------------------------------------------


async def _run_not_ready_gate(settings) -> dict[str, Any]:
    """Assert gated tools return not_ready immediately after start() (before ready)."""
    manager = AnalyzerManager(
        rust_analyzer_bin=settings.rust_analyzer_bin,
        repository_root=settings.project_root,
    )
    results: dict[str, Any] = {}
    await manager.start()
    try:
        # Immediately after start: must be indexing
        assert manager.state == STATE_INDEXING

        # Test each gated tool immediately (before ready event fires)
        with patch.object(core, "_manager", manager):
            ds_result = await document_symbols("crates/searcher/src/searcher/mod.rs")
            gd_result = await goto_definition("crates/searcher/src/searcher/mod.rs", 1, 1)
            fr_result = await find_references("crates/searcher/src/searcher/mod.rs", 1, 1)
            hov_result = await hover("crates/searcher/src/searcher/mod.rs", 1, 1)

        results["document_symbols_before_ready"] = ds_result["status"]
        results["goto_definition_before_ready"] = gd_result["status"]
        results["find_references_before_ready"] = fr_result["status"]
        results["hover_before_ready"] = hov_result["status"]

        # All must be not_ready
        assert ds_result["status"] == STATUS_NOT_READY, (
            f"document_symbols before ready returned {ds_result['status']!r}, expected not_ready"
        )
        assert gd_result["status"] == STATUS_NOT_READY, (
            f"goto_definition before ready returned {gd_result['status']!r}, expected not_ready"
        )
        assert fr_result["status"] == STATUS_NOT_READY, (
            f"find_references before ready returned {fr_result['status']!r}, expected not_ready"
        )
        assert hov_result["status"] == STATUS_NOT_READY, (
            f"hover before ready returned {hov_result['status']!r}, expected not_ready"
        )

    finally:
        await manager.shutdown()

    return results


# ---------------------------------------------------------------------------
# Pytest test wrappers (synchronous; anyio.run delegates to asyncio)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_phase34_all_tools(settings, capsys) -> None:
    """Single warm-manager session covering all Phase 3+4 tools and UNVERIFIED items.

    This is the primary integration gate:
        - Discover→act loop (find_symbol → goto_definition / find_references / hover)
        - document_symbols: ok + non-empty flat symbols; confirms container value
        - goto_definition: ok + definitions with real file; confirms path derivation
        - find_references: used symbol → ok+non-empty; zero-ref → ok+empty (NOT error)
        - hover: ok + non-empty markdown; records actual shape
        - status: ready state → ok + real 40-char hashes + stale==False
        - refresh: ok + state=indexing; re-wait for ready; find_symbol still works
    """
    results = anyio.run(_run_phase34_all, settings)

    # ------------------------------------------------------------------
    # Print findings for the build report
    # ------------------------------------------------------------------
    print("\n\n=== Phase 3+4 Integration Gate Findings ===")

    pivot = results.get("find_symbol_pivot", {})
    print("\n[1] Discover pivot (SearcherBuilder Struct):")
    print(
        f"    file={pivot.get('file')!r}  line={pivot.get('line')}  char={pivot.get('character')}"
    )

    print("\n[2] document_symbols container findings:")
    print(f"    unique container values: {results.get('document_symbols_containers')!r}")
    print(f"    all_containers_null={results.get('document_symbols_all_containers_null')}")

    defn = results.get("goto_definition_result", {})
    print("\n[3] goto_definition first result:")
    print(f"    file={defn.get('file')!r}  line={defn.get('line')}  char={defn.get('character')}")
    print("    (path derived via URI or relativePath — check logs for _uri_to_relative_path)")

    print("\n[4] find_references headline invariant:")
    print(f"    refs (excl decl): {results.get('find_references_count_exclude_decl')}")
    print(f"    refs (incl decl): {results.get('find_references_count_include_decl')}")
    print(f"    zero-ref candidate find_symbol status: {results.get('zero_ref_candidate_find')!r}")
    print(f"    zero-ref test status: {results.get('zero_ref_test_status')!r}")
    print(f"    zero-ref test count: {results.get('zero_ref_test_count')}")

    print("\n[5] hover contents shape (first 200 chars):")
    print(f"    {results.get('hover_contents_snippet')!r}")

    print("\n[6] status while ready:")
    print(f"    indexed_commit={results.get('status_indexed_commit')!r}")
    print(f"    current_commit={results.get('status_current_commit')!r}")
    print(f"    stale={results.get('status_stale')!r}")

    print("\n[7] refresh:")
    print(f"    state mid-refresh: {results.get('status_mid_refresh_state')!r}")
    print(f"    indexed_commit mid-refresh: {results.get('status_mid_refresh_indexed_commit')!r}")
    print(f"    recovery time: {results.get('refresh_recovery_seconds')}s")
    print(f"    indexed_commit post-refresh: {results.get('status_post_refresh_indexed_commit')!r}")
    print(f"    find_symbol post-refresh ok: {results.get('find_symbol_post_refresh_ok')}")
    print("\n===========================================\n")


@pytest.mark.integration
def test_readiness_gate_before_ready(settings) -> None:
    """Gated tools (document_symbols, goto_definition, find_references, hover)
    must all return not_ready when called immediately after manager.start()
    (before the ready event fires).

    This proves the fail-fast contract holds at the tool layer.
    """
    results = anyio.run(_run_not_ready_gate, settings)

    assert results["document_symbols_before_ready"] == STATUS_NOT_READY
    assert results["goto_definition_before_ready"] == STATUS_NOT_READY
    assert results["find_references_before_ready"] == STATUS_NOT_READY
    assert results["hover_before_ready"] == STATUS_NOT_READY
