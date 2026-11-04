"""Integration tests for the DS-03/04/21 and DS-12 races — LIVE guards (#90).

Marker: ``integration`` (registered in pyproject.toml).
Run locally only: ``uv run pytest -m integration``
Never runs in CI.

Why this file exists:
    ``tests/test_lifecycle_races.py`` (DS-03/04/21) and the DS-12 tests in
    ``tests/test_doc_store.py`` guard the analyzer-restart and doc-store-
    rebuild races entirely with FAKES (``FakeLsp``/``FakeHandler``,
    ``FakeEmbeddingFunction``).  Those fakes are precise and fast, but they
    encode an *assumption* about how the real rust-analyzer subprocess and
    real ChromaDB/ONNX embedding behave under concurrent access.  If a future
    multilspy/chromadb upgrade (or a subtle bug only the real timing exposes)
    breaks that assumption, the fake-tier tests would keep passing while the
    live behaviour regressed.  These tests close that gap by driving the same
    races against the LIVE rust-analyzer binary and a real (on-disk)
    ChromaDB collection.  The fakes remain the PRIMARY guards (they run in
    every CI build and pinpoint exactly which invariant broke); these
    integration tests exist only to catch fake-vs-real drift.

What is reused from existing integration-test conventions (do NOT duplicate):
    - The ``settings`` module-scoped fixture and the "one warm manager, one
      cold index" pattern from ``test_phase2_integration.py`` /
      ``test_phase34_integration.py``.
    - ``test_phase34_integration.py``'s step 7 already proves refresh-to-ready
      END TO END (restart *after* reaching ready).  This file's value-add is
      the RACE TIMING: restart() issued while a REAL rust-analyzer subprocess
      is still mid-indexing (state == "indexing", not yet quiescent) — never
      exercised live anywhere else in the suite.
    - Generous ``anyio.fail_after`` timeouts (cold ripgrep index can take
      several minutes; restart benefits from a warm cargo/target cache).
    - ``test_phase5_integration.py``'s real-``DocStore``-with-real-embedding
      idiom (``DocStore(settings)`` with no ``embedding_function`` override —
      ChromaDB's bundled ONNX all-MiniLM-L6-v2) for the DS-12 / SW-10 rebuild
      case (the old index answers during a rebuild).

Design constraints (deterministic-or-skip): every race-timing test bounds its
wait with a deadline and calls ``pytest.skip`` with a clear reason if the race
window cannot be entered, rather than flaking.
"""

from __future__ import annotations

import asyncio
import pathlib
import time
from typing import Any

import anyio
import psutil
import pytest

from rust_lsp_mcp.analyzer import STATE_INDEXING, STATE_READY, AnalyzerManager
from rust_lsp_mcp.doc_store import DocStore, DocStoreNotReady
from rust_lsp_mcp.settings import Settings, get_settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings() -> Settings:
    return get_settings()


# ---------------------------------------------------------------------------
# Test 1 helpers — live rust-analyzer OS process accounting.
# ---------------------------------------------------------------------------


def _count_rust_analyzer_processes() -> int:
    """Count live rust-analyzer server processes descended from THIS test process.

    The process tree, probed empirically inside the integration container
    (multilspy 0.0.15 launches via ``asyncio.create_subprocess_shell`` with
    ``start_new_session=True``; ``/bin/sh`` is dash and does NOT exec away)::

        pytest (this process)
          └─ sh -c /usr/local/cargo/bin/rust-analyzer     name='sh'
               └─ rust-analyzer                           name='rust-analyzer'
                    ├─ cargo metadata ... (transient)     name='cargo'
                    └─ rust-analyzer-proc-macro-srv       name='rust-analyzer-proc-macro-srv'

    So the discriminator is an EXACT ``name() == "rust-analyzer"`` match over
    ``children(recursive=True)``:

    - recursive, because the analyzer is a grandchild (behind the ``sh``
      wrapper), and a DS-04-orphaned analyzer stays our descendant — its
      wrapper ``sh`` keeps waiting on it — so a leak IS visible here;
    - exact name (not a substring over cmdline/exe), because a substring scan
      also matches the wrapper ``sh`` (its argv contains the binary path),
      the ``rust-analyzer-proc-macro-srv`` helper that a HEALTHY analyzer
      legitimately spawns mid-indexing, and — with a system-wide
      ``process_iter`` — even the container harness's own bash script text.
      All three were observed inflating the count while developing this test.

    Clean teardown removes the whole subtree from this count: multilspy's
    ``stop()`` signals the process TREE (the shell was started with
    ``start_new_session``), so the analyzer, its wrapper, and its helpers all
    disappear together after a healthy drain.
    """
    try:
        me = psutil.Process()
        descendants = me.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0
    count = 0
    for proc in descendants:
        try:
            if proc.name() == "rust-analyzer":
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return count


async def _wait_for_new_process(before_count: int, deadline_seconds: float = 60.0) -> bool:
    """Poll until a NEW rust-analyzer child process appears, or the deadline
    elapses.

    Returns True once ``_count_rust_analyzer_processes()`` exceeds
    ``before_count``; False on timeout.  This is how test 1 confirms it is
    racing a REAL, already-spawned subprocess (not just a scheduled-but-not-
    yet-run background task) before issuing restart().
    """
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if _count_rust_analyzer_processes() > before_count:
            return True
        await asyncio.sleep(0.2)
    return False


async def _wait_for_process_count(target_count: int, deadline_seconds: float = 30.0) -> int:
    """Poll until the live rust-analyzer child-process count reaches
    ``target_count`` (or below, for a teardown check), or the deadline
    elapses.  Returns the final observed count."""
    deadline = time.monotonic() + deadline_seconds
    count = _count_rust_analyzer_processes()
    while count > target_count and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        count = _count_rust_analyzer_processes()
    return count


# ---------------------------------------------------------------------------
# Test 1: DS-03/DS-04 — restart() issued during REAL, in-flight indexing.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_refresh_during_live_indexing_recovers(settings: Settings) -> None:
    """restart() issued while a live rust-analyzer subprocess is still
    indexing must recover cleanly: the manager reaches ready, a nav delegate
    call succeeds, and the superseded run's subprocess is never orphaned.

    This is the live counterpart of ``test_lifecycle_races.py``'s
    ``TestRestartDuringIndexing`` (DS-03, generation counter) and
    ``TestDrainCancelTeardown`` (DS-04, cancellation-safe teardown) — those
    tests prove the invariant against a ``FakeLsp`` with a controllable gate;
    this test proves the SAME invariant against the real multilspy +
    rust-analyzer stack, where the "gate" is however long real indexing
    genuinely takes.
    """

    async def _scenario() -> None:
        manager = AnalyzerManager(
            rust_analyzer_bin=settings.rust_analyzer_bin,
            repository_root=settings.project_root,
        )
        before_count = _count_rust_analyzer_processes()
        await manager.start()
        # start() only *schedules* the background task (asyncio.create_task
        # never runs the coroutine body inline) — state is guaranteed
        # STATE_INDEXING here, mirroring the docstring on
        # test_lifecycle_races.py's _await_first_instance helper.
        assert manager.state == STATE_INDEXING

        try:
            spawned = await _wait_for_new_process(before_count)
            if not spawned:
                pytest.skip(
                    "rust-analyzer subprocess never appeared within the "
                    "detection window (60s) — cannot demonstrate a restart-"
                    "during-live-indexing race; check rust_analyzer_bin / "
                    "container fixture setup"
                )
            if manager.state == STATE_READY:
                pytest.skip(
                    "indexing completed before restart() could be issued — "
                    "the race window closed too fast to catch on this run"
                )

            # The old run's real rust-analyzer subprocess is live and still
            # indexing (state == "indexing").  Issue restart() into that
            # in-flight run — the core DS-03/DS-04 race timing.
            with anyio.fail_after(60):
                await manager.restart()

            # Re-wait for ready with a generous timeout: a cold ripgrep index
            # can take several minutes; cargo/target caches are preserved
            # across restart (see test_phase34_integration.py's refresh step),
            # so recovery is typically much faster than a first cold start.
            with anyio.fail_after(300):
                await manager._ready_event.wait()

            assert manager.state == STATE_READY
            assert manager.is_ready

            # A nav delegate call succeeds against the recovered analyzer.
            symbols = await manager.request_workspace_symbol("SearcherBuilder")
            assert isinstance(symbols, list), (
                f"expected request_workspace_symbol to return a list after "
                f"recovery, got {symbols!r}"
            )
            assert len(symbols) >= 1, (
                "expected at least one SearcherBuilder workspace-symbol hit "
                "after recovery from a restart-during-indexing race"
            )

            # Exactly one live rust-analyzer process: the superseded run's
            # subprocess must have been force-stopped (DS-04), never orphaned.
            after_count = _count_rust_analyzer_processes()
            assert after_count == before_count + 1, (
                f"expected exactly one live rust-analyzer process after "
                f"recovery (before={before_count}, after={after_count}) — "
                "a higher count means the superseded run's subprocess was "
                "orphaned instead of force-stopped (DS-04 regression)"
            )
        finally:
            await manager.shutdown()

        # After shutdown(), the process count must return to the baseline —
        # poll briefly since OS process teardown is not instantaneous.
        final_count = await _wait_for_process_count(before_count)
        assert final_count == before_count, (
            f"expected the live rust-analyzer process count to return to "
            f"baseline {before_count} after shutdown(), got {final_count}"
        )

    anyio.run(_scenario)


# ---------------------------------------------------------------------------
# Test 2: DS-12 — search() during a REAL, in-flight ChromaDB rebuild.
# ---------------------------------------------------------------------------

_RACE_CORPUS_SIZE = 32


def _write_race_corpus(corpus_dir: pathlib.Path, n: int = _RACE_CORPUS_SIZE) -> None:
    """Write ``n`` tiny markdown files — small enough that embedding is quick,
    but large enough (real ONNX model, not a fake) that the rebuild's
    unlocked embed/add phase takes measurably longer than a single event-loop
    tick, giving concurrent search() calls a real window to land mid-rebuild.
    """
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (corpus_dir / f"doc_{i:02d}.md").write_text(
            f"# Document {i}\n\n"
            f"This document discusses topic number {i} about rust "
            "programming, concurrency, and search-index rebuild races.\n\n"
            f"## Section {i}\n\n"
            f"More detail about race condition handling scenario {i}, "
            "covering read/write coordination under concurrent rebuilds.\n",
            encoding="utf-8",
        )


def _real_race_settings(tmp_chroma: pathlib.Path, corpus: pathlib.Path) -> Settings:
    return Settings(
        chroma_path=str(tmp_chroma),
        project_root=str(corpus),
        doc_glob_patterns="**/*.md",
    )


def _same_result_set(a: list[dict[str, Any]], b: list[dict[str, Any]], tol: float = 1e-4) -> bool:
    """True if two ``DocStore.search`` result lists are the SAME steady-state
    answer: identical file/breadcrumb/text in the same order, distances equal
    within a small floating-point tolerance (real ONNX inference is
    deterministic for identical input text, but exact bit-for-bit float
    equality across two independently-computed embedding batches is not
    guaranteed, e.g. multi-threaded reduction order).

    Safety margin: strict order/membership equality across two INDEPENDENT
    chroma builds is deterministic only because the total chunk count (~64
    from the 32-doc corpus) stays under the default HNSW ef_search=100, so
    the search is effectively exact — chromadb 1.5.9 builds the HNSW graph
    multi-threaded and can reorder near-neighbours otherwise.  If
    ``_RACE_CORPUS_SIZE`` or chunks-per-doc ever push past ef_search, this
    check becomes approximate/flaky."""
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b, strict=True):
        if ra["file"] != rb["file"] or ra["breadcrumb"] != rb["breadcrumb"]:
            return False
        if ra["text"] != rb["text"]:
            return False
        if abs(ra["distance"] - rb["distance"]) > tol:
            return False
    return True


@pytest.mark.integration
def test_search_during_live_rebuild_serves_old_index_never_not_ready(
    tmp_path: pathlib.Path,
) -> None:
    """search() concurrent with a REAL ChromaDB rebuild() after one file
    changed never raises ``DocStoreNotReady`` and always returns the steady
    state (SW-10, T04): the rebuild writes a staging collection while the old
    index keeps answering, then swaps.

    The live counterpart of ``test_doc_store.py``'s
    ``test_old_collection_answers_search_during_rebuild``, with the REAL
    default ONNX embedding function against an on-disk collection.  The
    changed file gains an unrelated section at its end, so its existing
    chunks keep their text and the steady-state answer is unchanged after
    the swap.  Deterministic-or-skip: when no search lands while the rebuild
    is in flight, the test skips rather than flakes.
    """
    corpus = tmp_path / "race_corpus"
    _write_race_corpus(corpus)
    settings = _real_race_settings(tmp_path / "chroma", corpus)

    # Real ChromaDB + real default embedding function (ChromaDB's bundled
    # ONNX all-MiniLM-L6-v2) — no embedding_function override, matching
    # test_phase5_integration.py's real-corpus idiom.
    store = DocStore(settings)
    store.rebuild()
    assert store.is_ready is True
    query = "race condition handling concurrency"
    steady_state = store.search(query, n_results=5)
    assert steady_state, "expected non-empty steady-state results for the race query"

    # Change one file that is not in the steady-state answer.
    hit_files = {r["file"] for r in steady_state}
    changed = next(p for p in sorted(corpus.glob("*.md")) if p.name not in hit_files)
    changed.write_text(
        changed.read_text(encoding="utf-8")
        + "\n## Baking\n\nKnead the dough, let it rise overnight, bake at 220 degrees.\n",
        encoding="utf-8",
    )

    async def _scenario() -> int:
        rebuild_task: asyncio.Task[int] = asyncio.create_task(asyncio.to_thread(store.rebuild))
        mid_rebuild_ok = 0
        deadline = time.monotonic() + 120
        try:
            while not rebuild_task.done() and time.monotonic() < deadline:
                in_flight = store.progress()["phase"] is not None
                try:
                    result = await asyncio.to_thread(store.search, query, 5)
                except DocStoreNotReady:
                    pytest.fail(
                        "search() raised DocStoreNotReady during a rebuild over a live index"
                    )
                assert _same_result_set(result, steady_state), (
                    f"search() during a live rebuild differs from the steady state: {result!r}"
                )
                if in_flight:
                    mid_rebuild_ok += 1
        finally:
            await rebuild_task
        return mid_rebuild_ok

    mid_rebuild_ok = anyio.run(_scenario)

    final_results = store.search(query, n_results=5)
    assert _same_result_set(final_results, steady_state), (
        f"post-rebuild search differs from the steady state: {final_results!r} vs {steady_state!r}"
    )
    assert store.is_ready is True
    if mid_rebuild_ok == 0:
        pytest.skip(
            "the rebuild finished before any search() landed while it was in flight; "
            "SW-10 is still guarded by the fake-tier test in test_doc_store.py"
        )


# ---------------------------------------------------------------------------
# Test 3 (bonus) — concurrent restart() serialization, live.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_concurrent_refreshes_serialize_live() -> None:
    """DS-21 (concurrent restart() calls serialize behind the lifecycle lock)
    against the live analyzer.

    SKIPPED by design: a live version of this test needs a SECOND full
    rust-analyzer index cycle beyond the one
    ``test_refresh_during_live_indexing_recovers`` already pays for in this
    same integration gate (either its own cold start, or reusing that test's
    already-shut-down manager, which would require a cross-test-function
    shared fixture and re-introduce a full re-index anyway once shut down).
    Paying that cost would push this unit over its runtime budget for a
    "cheap bonus" test.  Coverage split: the single-restart survivor
    invariant (one live analyzer process after a restart-during-indexing,
    the DS-03/04 half) is covered live by
    ``test_refresh_during_live_indexing_recovers`` above; the
    concurrent-serialization case — DS-21's actual point (two overlapping
    restart() calls queueing behind the lifecycle lock) — stays fake-guarded
    by ``test_lifecycle_races.py::TestConcurrentRestart``, which is where the
    drift risk for the locking/serialization logic itself — as opposed to
    real subprocess timing — would show up first.
    """
    pytest.skip(
        "skipped: a live concurrent-restart test needs a second full "
        "rust-analyzer index cycle beyond what "
        "test_refresh_during_live_indexing_recovers already pays for in "
        "this gate; the single-restart survivor invariant is covered live "
        "above, and the concurrent-serialization case (DS-21's point) stays "
        "fake-guarded by test_lifecycle_races.py::TestConcurrentRestart. "
        "See this test's docstring for the full rationale."
    )
