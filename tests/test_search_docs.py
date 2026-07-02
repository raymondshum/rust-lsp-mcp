"""Fast-tier tests for the search_docs tool.

No live doc store, no ChromaDB, no model downloads.  All heavy dependencies
are stubbed via monkeypatching.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    Readiness gating:
        - store None → not_ready (not a search result), UNLESS the module-level
          doc_store_state() reports "error" (permanent init failure), in which
          case → error.
        - store present but is_ready=False → not_ready; search NOT called.
        - store present with state == "error" → error (not not_ready); search
          NOT called.
    Happy path:
        - store ready, search returns 2 hits → ok with results passed through.
        - result dict shape: each hit has file, breadcrumb, text, distance keys.
    Empty results:
        - store ready, search returns [] → not_found (not ok+empty).
    Error handling:
        - store ready, search raises → error with message.
    limit / clamping:
        - limit is passed to search as n_results.
        - limit <= 0 is clamped to 1 before calling search.
"""

import asyncio
import contextlib
from typing import Any
from unittest.mock import MagicMock, patch

from rust_lsp_mcp.doc_store import DocStoreNotReady
from rust_lsp_mcp.envelope import STATUS_ERROR, STATUS_NOT_FOUND, STATUS_NOT_READY, STATUS_OK

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_HIT: dict[str, Any] = {
    "file": "docs/guide.md",
    "breadcrumb": "guide.md > Installation",
    "text": "Install with cargo install ripgrep.",
    "distance": 0.12,
}

_FAKE_HIT_2: dict[str, Any] = {
    "file": "docs/reference.md",
    "breadcrumb": "reference.md > Flags",
    "text": "Use -n to print line numbers.",
    "distance": 0.25,
}


def _make_ready_store(search_return: Any = None, search_side_effect: Any = None) -> MagicMock:
    """Return a fake DocStore with is_ready=True and a controlled search()."""
    store = MagicMock()
    store.is_ready = True
    if search_side_effect is not None:
        store.search.side_effect = search_side_effect
    else:
        store.search.return_value = search_return if search_return is not None else []
    return store


def _make_not_ready_store() -> MagicMock:
    """Return a fake DocStore with is_ready=False."""
    store = MagicMock()
    store.is_ready = False
    return store


def _make_error_store(error_message: str = "RuntimeError: boom") -> MagicMock:
    """Return a fake DocStore with state="error" (permanent failure)."""
    store = MagicMock()
    store.is_ready = False
    store.state = "error"
    store.error_message = error_message
    return store


def _run_search_docs(
    query: str = "install",
    limit: int = 5,
    store: Any = None,
    doc_store_state_return: Any = None,
) -> dict[str, Any]:
    """Monkeypatch get_doc_store (and optionally doc_store_state) and call search_docs().

    ``doc_store_state_return``, when given, patches
    ``search_mod.doc_store_state`` to return that ``(state, error)`` tuple
    instead of consulting the real module singleton — used to test the
    ``store is None`` branch deterministically regardless of any real
    doc_store module state left over from other test files.
    """
    import rust_lsp_mcp.tools.search_docs as search_mod

    async def _inner() -> dict[str, Any]:
        patches = [patch.object(search_mod, "get_doc_store", return_value=store)]
        if doc_store_state_return is not None:
            patches.append(
                patch.object(search_mod, "doc_store_state", return_value=doc_store_state_return)
            )
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return await search_mod.search_docs(query=query, limit=limit)

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Readiness gating
# ---------------------------------------------------------------------------


class TestReadinessGating:
    """store None or is_ready=False must return not_ready — never a search result."""

    def test_store_none_returns_not_ready(self) -> None:
        result = _run_search_docs(store=None, doc_store_state_return=("building", None))
        assert result["status"] == STATUS_NOT_READY

    def test_store_none_not_ready_has_message(self) -> None:
        result = _run_search_docs(store=None, doc_store_state_return=("building", None))
        assert "message" in result
        assert result["message"]  # non-empty

    def test_store_not_ready_returns_not_ready(self) -> None:
        store = _make_not_ready_store()
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_NOT_READY

    def test_store_not_ready_search_never_called(self) -> None:
        """When is_ready=False, search() must NOT be called — no partial results."""
        store = _make_not_ready_store()
        _run_search_docs(store=store)
        store.search.assert_not_called()

    def test_not_ready_is_not_error(self) -> None:
        """not_ready must not be confused with error."""
        result = _run_search_docs(store=None, doc_store_state_return=("building", None))
        assert result["status"] == STATUS_NOT_READY
        assert result["status"] != STATUS_ERROR


# ---------------------------------------------------------------------------
# Error-state surfacing (DS-14): a permanently-failed build is NOT not_ready.
# ---------------------------------------------------------------------------


class TestErrorState:
    """A doc index that failed to build/init must surface as error, not not_ready."""

    def test_errored_store_returns_error(self) -> None:
        """store present with state == "error" → error envelope."""
        store = _make_error_store("RuntimeError: embedding model unavailable")
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_ERROR

    def test_errored_store_error_not_not_ready(self) -> None:
        store = _make_error_store()
        result = _run_search_docs(store=store)
        assert result["status"] != STATUS_NOT_READY

    def test_errored_store_message_includes_reason(self) -> None:
        store = _make_error_store("RuntimeError: embedding model unavailable")
        result = _run_search_docs(store=store)
        assert "embedding model unavailable" in result["message"]

    def test_errored_store_message_mentions_refresh(self) -> None:
        store = _make_error_store()
        result = _run_search_docs(store=store)
        assert "refresh" in result["message"].lower()

    def test_errored_store_search_never_called(self) -> None:
        """A permanently-failed store must never have search() invoked on it."""
        store = _make_error_store()
        _run_search_docs(store=store)
        store.search.assert_not_called()

    def test_store_none_with_init_error_returns_error(self) -> None:
        """store is None AND doc_store_state() reports "error" → error envelope."""
        result = _run_search_docs(
            store=None,
            doc_store_state_return=("error", "RuntimeError: chroma path unwritable"),
        )
        assert result["status"] == STATUS_ERROR

    def test_store_none_with_init_error_message_includes_reason(self) -> None:
        result = _run_search_docs(
            store=None,
            doc_store_state_return=("error", "RuntimeError: chroma path unwritable"),
        )
        assert "chroma path unwritable" in result["message"]

    def test_store_none_still_building_returns_not_ready(self) -> None:
        """store is None but doc_store_state() reports "building" → not_ready, not error."""
        result = _run_search_docs(store=None, doc_store_state_return=("building", None))
        assert result["status"] == STATUS_NOT_READY


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    """store ready, search returns hits → ok with results passed through."""

    def test_two_hits_returns_ok(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT, _FAKE_HIT_2])
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_OK

    def test_two_hits_results_field_present(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT, _FAKE_HIT_2])
        result = _run_search_docs(store=store)
        assert "results" in result

    def test_two_hits_count(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT, _FAKE_HIT_2])
        result = _run_search_docs(store=store)
        assert len(result["results"]) == 2

    def test_hits_passed_through_unchanged(self) -> None:
        """Results must be the exact dicts from search(), not copies or transforms."""
        store = _make_ready_store(search_return=[_FAKE_HIT, _FAKE_HIT_2])
        result = _run_search_docs(store=store)
        assert result["results"][0] is _FAKE_HIT
        assert result["results"][1] is _FAKE_HIT_2

    def test_result_dict_shape_file(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(store=store)
        hit = result["results"][0]
        assert "file" in hit
        assert isinstance(hit["file"], str)

    def test_result_dict_shape_breadcrumb(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(store=store)
        hit = result["results"][0]
        assert "breadcrumb" in hit
        assert isinstance(hit["breadcrumb"], str)

    def test_result_dict_shape_text(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(store=store)
        hit = result["results"][0]
        assert "text" in hit
        assert isinstance(hit["text"], str)

    def test_result_dict_shape_distance(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(store=store)
        hit = result["results"][0]
        assert "distance" in hit
        assert isinstance(hit["distance"], float)

    def test_single_hit_returns_ok(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_OK
        assert len(result["results"]) == 1


# ---------------------------------------------------------------------------
# Empty results → not_found
# ---------------------------------------------------------------------------


class TestEmptyResults:
    """search() returning [] must produce not_found, not ok+empty."""

    def test_empty_search_returns_not_found(self) -> None:
        store = _make_ready_store(search_return=[])
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_NOT_FOUND

    def test_empty_search_not_ok(self) -> None:
        store = _make_ready_store(search_return=[])
        result = _run_search_docs(store=store)
        assert result["status"] != STATUS_OK

    def test_not_found_has_message(self) -> None:
        store = _make_ready_store(search_return=[])
        result = _run_search_docs(store=store)
        assert "message" in result
        assert result["message"]

    def test_not_found_has_no_results_key(self) -> None:
        """not_found envelope must NOT carry a 'results' key."""
        store = _make_ready_store(search_return=[])
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_NOT_FOUND
        assert "results" not in result

    def test_empty_and_not_ready_statuses_distinct(self) -> None:
        """not_found (empty store) vs not_ready (rebuilding) must differ."""
        empty_result = _run_search_docs(store=_make_ready_store(search_return=[]))
        not_ready_result = _run_search_docs(store=None, doc_store_state_return=("building", None))
        assert empty_result["status"] != not_ready_result["status"]
        assert empty_result["status"] == STATUS_NOT_FOUND
        assert not_ready_result["status"] == STATUS_NOT_READY


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Exceptions from store.search() produce error envelopes."""

    def test_search_exception_returns_error(self) -> None:
        store = _make_ready_store(search_side_effect=RuntimeError("db boom"))
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_ERROR

    def test_error_has_message(self) -> None:
        store = _make_ready_store(search_side_effect=RuntimeError("db boom"))
        result = _run_search_docs(store=store)
        assert "message" in result

    def test_error_message_includes_exc_text(self) -> None:
        store = _make_ready_store(search_side_effect=RuntimeError("db boom"))
        result = _run_search_docs(store=store)
        assert "db boom" in result["message"]

    def test_error_envelope_has_no_results(self) -> None:
        store = _make_ready_store(search_side_effect=ValueError("embedding failed"))
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_ERROR
        assert "results" not in result


# ---------------------------------------------------------------------------
# limit / clamping
# ---------------------------------------------------------------------------


class TestLimitHandling:
    """limit parameter is passed to search as n_results and clamped to >= 1."""

    def test_limit_passed_as_n_results(self) -> None:
        """search() must be called with n_results=limit when limit >= 1."""
        store = _make_ready_store(search_return=[_FAKE_HIT])
        _run_search_docs(query="foo", limit=3, store=store)
        store.search.assert_called_once_with("foo", n_results=3)

    def test_limit_default_is_5(self) -> None:
        """Default limit of 5 must be passed as n_results=5."""
        store = _make_ready_store(search_return=[_FAKE_HIT])
        _run_search_docs(query="foo", store=store)  # limit defaults to 5
        store.search.assert_called_once_with("foo", n_results=5)

    def test_limit_zero_clamped_to_1(self) -> None:
        """limit=0 must be clamped to 1 before calling search."""
        store = _make_ready_store(search_return=[_FAKE_HIT])
        _run_search_docs(query="foo", limit=0, store=store)
        store.search.assert_called_once_with("foo", n_results=1)

    def test_limit_negative_clamped_to_1(self) -> None:
        """limit=-5 must be clamped to 1 before calling search."""
        store = _make_ready_store(search_return=[_FAKE_HIT])
        _run_search_docs(query="foo", limit=-5, store=store)
        store.search.assert_called_once_with("foo", n_results=1)

    def test_limit_large_passed_through(self) -> None:
        """Large limit values are not clamped from above."""
        store = _make_ready_store(search_return=[_FAKE_HIT])
        _run_search_docs(query="foo", limit=100, store=store)
        store.search.assert_called_once_with("foo", n_results=100)


# ---------------------------------------------------------------------------
# DS-12: DocStoreNotReady from store.search() maps to not_ready, not error.
# ---------------------------------------------------------------------------


class TestDocStoreNotReadySignal:
    """The fast-path is_ready check can pass just before a concurrent rebuild
    flips state — store.search() then raises DocStoreNotReady.  This must map
    to not_ready (transient), distinctly from a genuine exception (-> error).
    """

    def test_doc_store_not_ready_maps_to_not_ready(self) -> None:
        store = _make_ready_store(search_side_effect=DocStoreNotReady())
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_NOT_READY

    def test_doc_store_not_ready_has_message(self) -> None:
        store = _make_ready_store(search_side_effect=DocStoreNotReady())
        result = _run_search_docs(store=store)
        assert "message" in result
        assert result["message"]

    def test_doc_store_not_ready_is_not_error(self) -> None:
        """DocStoreNotReady must not be swallowed by the generic Exception
        handler and surfaced as error — it has its own dedicated branch.
        """
        store = _make_ready_store(search_side_effect=DocStoreNotReady())
        result = _run_search_docs(store=store)
        assert result["status"] != STATUS_ERROR

    def test_genuine_exception_still_maps_to_error(self) -> None:
        """A real exception (not DocStoreNotReady) from search() still maps
        to error — confirms the new except branch didn't swallow the
        pre-existing generic exception handling.
        """
        store = _make_ready_store(search_side_effect=RuntimeError("db boom"))
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_ERROR
        assert "db boom" in result["message"]

    def test_not_ready_rechecks_error_state(self) -> None:
        """Nit 2: if a concurrent rebuild transitions the store to ERROR
        between the fast-path state check and search()'s snapshot, the
        DocStoreNotReady handler re-reads state and returns error (permanent),
        not not_ready — so a now-errored store is not briefly mislabelled.
        """
        store = MagicMock()
        store.is_ready = True
        store.state = "ready"  # passes the fast-path DOC_STATE_ERROR check
        store.error_message = "RuntimeError: embedding model died mid-rebuild"

        def _search_that_errors_then_signals(*_args: Any, **_kwargs: Any) -> Any:
            # Simulate the rebuild flipping the store to ERROR before this
            # call's snapshot decides the store is not queryable.
            store.state = "error"
            raise DocStoreNotReady()

        store.search.side_effect = _search_that_errors_then_signals

        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_ERROR
        assert "embedding model died mid-rebuild" in result["message"]
        assert "refresh" in result["message"].lower()

    def test_not_ready_without_error_state_stays_not_ready(self) -> None:
        """The re-check only escalates to error when state is genuinely ERROR;
        a plain transient DocStoreNotReady (state still non-error) stays
        not_ready.
        """
        store = _make_ready_store(search_side_effect=DocStoreNotReady())
        store.state = "building"
        result = _run_search_docs(store=store)
        assert result["status"] == STATUS_NOT_READY


# ---------------------------------------------------------------------------
# DS-22: empty/whitespace query is rejected before any readiness check.
# ---------------------------------------------------------------------------


class TestDS22EmptyQueryValidation:
    """Empty/whitespace queries must return error immediately, never ok+top-k."""

    def test_empty_string_query_returns_error(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(query="", store=store)
        assert result["status"] == STATUS_ERROR

    def test_whitespace_space_query_returns_error(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(query="   ", store=store)
        assert result["status"] == STATUS_ERROR

    def test_whitespace_newline_tab_query_returns_error(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(query="\n\t", store=store)
        assert result["status"] == STATUS_ERROR

    def test_empty_query_search_not_called(self) -> None:
        """search() must not be invoked at all for an empty query."""
        store = _make_ready_store(search_return=[_FAKE_HIT])
        _run_search_docs(query="", store=store)
        store.search.assert_not_called()

    def test_whitespace_query_search_not_called(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        _run_search_docs(query="\n\t", store=store)
        store.search.assert_not_called()

    def test_empty_query_error_has_message(self) -> None:
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(query="", store=store)
        assert "message" in result
        assert result["message"]

    def test_empty_query_rejected_even_when_store_none(self) -> None:
        """Validation happens FIRST, before the store-None readiness gate."""
        result = _run_search_docs(query="", store=None, doc_store_state_return=("building", None))
        assert result["status"] == STATUS_ERROR

    def test_valid_query_still_works(self) -> None:
        """A non-empty query is unaffected by the DS-22 validation gate."""
        store = _make_ready_store(search_return=[_FAKE_HIT])
        result = _run_search_docs(query="install cargo", store=store)
        assert result["status"] == STATUS_OK
        store.search.assert_called_once_with("install cargo", n_results=5)
