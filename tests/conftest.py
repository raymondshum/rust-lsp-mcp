"""Shared pytest fixtures for the rust-lsp-mcp test suite.

Two responsibilities:

1. Doc-store singleton hygiene (FINDING 5): the doc store keeps
   module-level global state (``_doc_store`` / ``_init_error`` / ``_build_task``
   in :mod:`rust_lsp_mcp.doc_store`).  A test that constructs or errors the store
   and fails partway through — before its own ``clear_doc_store()`` cleanup runs
   — would otherwise leak that state into whatever test happens to run next
   (observably: a later ``search_docs`` / ``status`` / ``doc_store_state`` test
   reading a stale singleton).  This autouse fixture resets it before AND after
   every test so ordering can never matter.

   It is intentionally global (applies to all tests, not just the doc-store
   files): clearing the singleton is a no-op for the ~hundreds of tests that
   never touch it, and strictly safer for the ones that do.  No test in the suite
   relies on the doc-store singleton persisting across test functions (the
   integration tests build ``DocStore`` instances directly and/or clear the
   singleton in their own teardown).

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

import pytest

import rust_lsp_mcp.doc_store as doc_store_mod
from tests.daemon_fixture import daemon_app  # noqa: F401 — fixture re-export (see docstring)


@pytest.fixture(autouse=True)
def _reset_doc_store_singleton():  # type: ignore[no-untyped-def]
    """Reset the doc-store module singleton before and after each test."""
    doc_store_mod.clear_doc_store()
    try:
        yield
    finally:
        # Best-effort: drop any leaked background ``doc-store-build`` task.
        # These tests drive their own event loops via ``asyncio.run(...)``,
        # so by the time this (synchronous) teardown runs the loop that owned
        # the task is already closed and the task is inert — requesting
        # cancellation is a best-effort courtesy that must never raise here.
        task = doc_store_mod._build_task
        if task is not None and not task.done():
            with contextlib.suppress(Exception):
                task.cancel()
        # clear_doc_store() also nulls the _build_task reference, so a leaked
        # task is no longer reachable from module state after this.
        doc_store_mod.clear_doc_store()
