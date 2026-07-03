"""Shared pytest fixtures for the rust-lsp-mcp test suite.

Two responsibilities:

1. Doc-store singleton hygiene (FINDING 5, revised for Phase C2): the doc
   store keeps module-level global state (``_doc_store`` / ``_init_error`` /
   ``_build_task`` in :mod:`rust_lsp_mcp.doc_store`).  A test that constructs
   or errors the store and fails partway through — before its own
   ``clear_doc_store()`` cleanup runs — would otherwise leak that state into
   whatever test happens to run next (observably: a later ``search_docs`` /
   ``status`` / ``doc_store_state`` test reading a stale singleton).  The
   autouse fixture below gives every non-daemon test a CLEARED singleton for
   the duration of its run, so ordering can never matter.

   **One test group DOES rely on the singleton persisting across test
   functions** (this corrects this docstring's original claim that none
   did): the in-process daemon fixture (``daemon_app``,
   tests/daemon_fixture.py) is session-scoped and shares this pytest
   process's ``rust_lsp_mcp.doc_store`` module globals — the daemon's
   lifespan initializes the doc store ONCE at fixture setup, and every
   daemon test's ``search_docs``-over-HTTP call reads that same singleton.
   The original reset (unconditional ``clear_doc_store()`` at every test's
   setup AND teardown) therefore nuked the live daemon's adopted index the
   moment the next test started, and every subsequent ``search_docs`` over
   HTTP returned ``not_ready`` (Phase C2 QA-gate failure, 2026-07-03).
   Invariant, pinned by tests/test_conftest_doc_store_reset.py: **the
   autouse reset must not clear a live daemon's store.**  Two mechanisms
   enforce it:

   - Tests that request ``daemon_app`` skip the reset entirely — they need
     the daemon's store live DURING the test (that is what they exercise).
   - All other tests run inside ``doc_store_reset_window()``: the pre-test
     module state is SNAPSHOTTED, the singleton is cleared for the test's
     duration (identical isolation to the original design), and the snapshot
     is RESTORED at teardown instead of cleared.  When no daemon is live the
     snapshot is all-``None`` and restore is indistinguishable from the old
     clear; when the session-scoped daemon is live, its store survives every
     interleaved non-daemon test, in any test ordering.  Restore never needs
     to know whether a daemon exists — which is exactly what makes this
     immune to the session-scope trap (a session-scoped ``daemon_app`` stays
     alive for the REST of the pytest session after first use, so any
     "skip reset while daemon exists" flag would have disabled isolation for
     every later test in a mixed-tier run).

2. Registering the ``daemon_app`` fixture (Phase C1, QA round 1): pytest only
   discovers fixtures defined in conftest files, registered plugins, or the
   test module itself — ``tests/daemon_fixture.py`` is a plain helper module,
   so its ``@pytest.fixture`` decoration alone registers NOTHING (the daemon
   integration tests errored at setup with "fixture 'daemon_app' not found").
   The re-export below places the fixture function in this conftest's
   namespace, which IS scanned, making it resolvable suite-wide.  A
   ``pytest_plugins = ["tests.daemon_fixture"]`` entry was rejected: modern
   pytest only honors ``pytest_plugins`` in the ROOTDIR conftest, and this
   repo's rootdir is the repo root (pyproject.toml), not ``tests/`` — the
   import re-export works regardless of conftest depth.  Guarded by the fast
   test ``tests/test_daemon_fixture_wiring.py``.
"""

import contextlib
from collections.abc import Iterator

import pytest

import rust_lsp_mcp.doc_store as doc_store_mod
from tests.daemon_fixture import daemon_app  # noqa: F401 — fixture re-export (see docstring)


@contextlib.contextmanager
def doc_store_reset_window() -> Iterator[None]:
    """Isolate the doc-store singleton for one test, then restore what was there.

    Snapshot -> clear -> (test runs) -> cancel any test-leaked build task ->
    clear -> restore the snapshot.  See the module docstring (responsibility
    1) for why restore-instead-of-clear is load-bearing: a live in-process
    daemon's adopted store must survive interleaved non-daemon tests.

    A plain context manager (not baked into the fixture) so the regression
    guard tests/test_conftest_doc_store_reset.py can drive these exact
    mechanics directly, without spinning up a daemon.
    """
    saved_store = doc_store_mod._doc_store
    saved_init_error = doc_store_mod._init_error
    saved_build_task = doc_store_mod._build_task
    doc_store_mod.clear_doc_store()
    try:
        yield
    finally:
        # Best-effort: drop any background ``doc-store-build`` task the TEST
        # itself leaked (never a pre-existing one from the snapshot — e.g.
        # the daemon's — which must keep running).  These tests drive their
        # own event loops via ``asyncio.run(...)``, so by the time this
        # (synchronous) teardown runs the loop that owned the task is
        # already closed and the task is inert — requesting cancellation is
        # a best-effort courtesy that must never raise here.
        task = doc_store_mod._build_task
        if task is not None and task is not saved_build_task and not task.done():
            with contextlib.suppress(Exception):
                task.cancel()
        # Drop whatever the test left behind, then restore the pre-test
        # state.  With an all-None snapshot (no daemon live) this is exactly
        # the original clear-at-teardown behaviour.
        doc_store_mod.clear_doc_store()
        doc_store_mod._doc_store = saved_store
        doc_store_mod._init_error = saved_init_error
        doc_store_mod._build_task = saved_build_task


@pytest.fixture(autouse=True)
def _reset_doc_store_singleton(request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    """Isolate the doc-store singleton per test — except for daemon tests.

    INVARIANT (Phase C2 QA rework): the autouse reset must not clear a live
    daemon's store.  Tests that request ``daemon_app`` are exercising the
    daemon's own doc store over HTTP — clearing (even with restore-after)
    would make every ``search_docs`` call during the test return
    ``not_ready``.  ``daemon_app`` is session-scoped and therefore always
    set up BEFORE this function-scoped fixture, so by the time this check
    runs the daemon (and its store) already exist.
    """
    if "daemon_app" in request.fixturenames:
        yield
        return
    with doc_store_reset_window():
        yield
