"""Shared pytest fixtures for the rust-lsp-mcp test suite.

Currently just doc-store singleton hygiene (FINDING 5): the doc store keeps
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
"""

import contextlib

import pytest

import rust_lsp_mcp.doc_store as doc_store_mod


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
