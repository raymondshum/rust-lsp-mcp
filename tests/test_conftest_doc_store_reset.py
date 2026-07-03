"""Regression guard for the conftest doc-store reset (Phase C2 QA rework).

Pins the invariant: **the autouse reset must not clear a live daemon's
store.**  Root cause of the 2026-07-03 QA-gate failure: the original autouse
``_reset_doc_store_singleton`` called ``clear_doc_store()`` unconditionally
at every test's setup and teardown, while C1's session-scoped ``daemon_app``
fixture runs the daemon IN-PROCESS — sharing ``rust_lsp_mcp.doc_store``'s
module globals with the test process.  The first reset after the daemon's
lifespan adopted the doc index destroyed it, and every subsequent
``search_docs`` over HTTP returned ``not_ready``.

These tests drive ``tests.conftest.doc_store_reset_window()`` — the exact
context manager the autouse fixture delegates to — directly, with a fake
ready store standing in for the daemon's adopted one (same trick as the C1
fixture-mechanics smoke scripts: fake the heavy component, exercise the real
seam).  No daemon, no analyzer, no chroma — fast tier.

The second half of the file exercises the REAL autouse fixture end-to-end:
a module-local ``daemon_app`` fixture (deliberately SHADOWING the conftest
re-export for this module only — standard pytest name resolution) adopts a
fake store at setup exactly as the real daemon's lifespan does, so the
autouse fixture's skip branch and the cross-test restore can be proven
without a live analyzer.  The integration sweep
(tests/test_cli_integration.py's ``search-docs`` step) guards the same
symptom against the real daemon in the podman gate.
"""

import asyncio
from typing import Any, cast

import pytest

import rust_lsp_mcp.doc_store as doc_store_mod
from rust_lsp_mcp.tools.search_docs import search_docs
from tests.conftest import doc_store_reset_window


class _FakeReadyStore:
    """Minimal stand-in for a daemon-adopted, fully-built DocStore."""

    state = doc_store_mod.DOC_STATE_READY
    is_ready = True
    error_message: str | None = None

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        return [
            {"file": "GUIDE.md", "breadcrumb": "GUIDE.md > Setup", "text": "hello", "distance": 0.1}
        ]


def _adopt(store: _FakeReadyStore) -> None:
    """Install *store* as the module singleton, as the daemon lifespan does.

    The cast mirrors how these duck-typed fakes are used throughout the
    suite (search_docs itself reads attributes via getattr, tolerant of
    non-DocStore doubles — see its docstring on MagicMock robustness).
    """
    doc_store_mod._doc_store = cast(doc_store_mod.DocStore, store)


def test_reset_window_restores_preexisting_store() -> None:
    """A singleton that existed BEFORE the window (the daemon's adopted
    store) must be cleared only for the window's duration and be back,
    identically, afterwards."""
    adopted = _FakeReadyStore()
    _adopt(adopted)  # as the daemon lifespan's init does

    with doc_store_reset_window():
        # Inside the window (i.e. during a non-daemon test) isolation holds:
        # the test sees a cleared singleton, exactly as before this rework.
        assert doc_store_mod.get_doc_store() is None

    assert doc_store_mod.get_doc_store() is adopted, (
        "the reset window failed to restore the pre-existing (daemon-adopted) store"
    )


def test_reset_window_drops_state_the_test_created() -> None:
    """State set DURING the window (a test's own leaked singleton) must not
    survive it — the original isolation guarantee is unchanged."""
    with doc_store_reset_window():
        _adopt(_FakeReadyStore())
        doc_store_mod._init_error = "leaked error"

    assert doc_store_mod.get_doc_store() is None
    assert doc_store_mod._init_error is None


def test_search_docs_path_survives_reset_window() -> None:
    """The QA-gate failure, reproduced at the unit level: adopt a ready
    store, let a reset window fire (an interleaved non-daemon test), then
    drive the REAL search_docs tool path — it must still find the store and
    return ok, not the not_ready the broken reset produced."""
    _adopt(_FakeReadyStore())

    with doc_store_reset_window():
        pass  # an intervening non-daemon test ran and finished

    result = asyncio.run(search_docs("hello"))
    assert result["status"] == "ok", (
        f"search_docs lost the daemon's store after a reset window: {result!r}"
    )
    assert result["results"][0]["text"] == "hello"


def test_search_docs_regression_shape_of_old_bug() -> None:
    """Documents (and pins) what the OLD behaviour did: an unconditional
    clear with no restore leaves search_docs reporting not_ready.  If this
    test ever starts failing because clear_doc_store() stops clearing, the
    reset design above needs re-review."""
    _adopt(_FakeReadyStore())
    doc_store_mod.clear_doc_store()  # the old fixture's teardown, verbatim

    result = asyncio.run(search_docs("hello"))
    assert result["status"] == "not_ready"


# ---------------------------------------------------------------------------
# End-to-end proof through the REAL autouse fixture (no live daemon).
#
# The module-local fixture below shadows conftest's ``daemon_app`` re-export
# for THIS MODULE ONLY, standing in for the real in-process daemon: it adopts
# a store at setup (as the daemon lifespan does) and holds it across tests
# (module-scoped — higher than the function-scoped autouse fixture, so it is
# always set up first, matching the real session-scoped fixture's ordering;
# module rather than session scope so the fake store does not linger for the
# rest of a mixed-tier run after this module finishes).
#
# The three tests below MUST run in definition order (pytest's default within
# a module) — the middle one is the interleaved non-daemon test the QA gate
# tripped over.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def daemon_app():  # type: ignore[no-untyped-def]  # shadows conftest's re-export, this module only
    adopted = _FakeReadyStore()
    _adopt(adopted)
    yield adopted
    doc_store_mod.clear_doc_store()


def test_daemon_test_sees_live_store(daemon_app: _FakeReadyStore) -> None:
    """The autouse fixture must SKIP the reset for a daemon_app test: the
    store adopted at fixture setup is visible DURING the test (this is what
    search-docs-over-HTTP needs)."""
    assert doc_store_mod.get_doc_store() is daemon_app
    result = asyncio.run(search_docs("hello"))
    assert result["status"] == "ok", result


def test_interleaved_non_daemon_test_is_isolated() -> None:
    """A non-daemon test between two daemon tests still gets a cleared
    singleton (isolation semantics unchanged) ..."""
    assert doc_store_mod.get_doc_store() is None


def test_daemon_test_after_interleaving_still_sees_store(daemon_app: _FakeReadyStore) -> None:
    """... and the daemon's store survived that interleaved test — the exact
    QA-gate scenario: search_docs over the daemon must still find it."""
    assert doc_store_mod.get_doc_store() is daemon_app
    result = asyncio.run(search_docs("hello"))
    assert result["status"] == "ok", result
