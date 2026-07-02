"""Regression tests for #98 — mid-flight not-ready guard misclassification.

Every tool call site that reaches an ``AnalyzerManager`` delegate does so only
after passing ``require_ready()`` at the top of the tool.  But there is a gap
between that check and the delegate call: nothing prevents a concurrent
``restart()`` (or the analyzer transitioning to ``"error"``) from landing in
that window.  When it does, the delegate's own defensive guard —

    if lsp is None or self.state != STATE_READY:
        raise RuntimeError("request_* called before analyzer is ready — "
                           "call require_ready() first")

— trips.  Pre-fix, this is a bare ``RuntimeError``, indistinguishable (to the
tool's ``except Exception`` fallback) from any other LSP failure, so the tool
returns a generic ``error("LSP error: request_* called before analyzer is
ready...")`` envelope instead of the honest ``not_ready`` (or, when the
analyzer has permanently failed, ``error`` with the *canonical* require_ready
message) that the caller needs to decide whether to retry.

``find_references(include_declaration=True)`` is the canonical trigger (issue
#98): it makes two sequential delegate calls (references, then definition);
a ``restart()`` landing between them hits this guard on the second call.  The
same TOCTOU exists at every require_ready() -> delegate window (recorded as a
residual by the KI-9 review) — this file exercises all 6 call sites.

The fix (analyzer.py): the guard now raises ``AnalyzerNotReadyError``, a
``RuntimeError`` subclass, instead of a bare ``RuntimeError``.  Each of the 6
tool call sites gets a new, narrow ``except AnalyzerNotReadyError`` clause
(before the generic ``except Exception``) that RE-CONSULTS
``require_ready()`` rather than assuming ``not_ready`` — because the very
same guard also trips when ``state == "error"`` (a *permanent* failure).
Blindly mapping ``AnalyzerNotReadyError`` -> ``not_ready()`` would misclassify
that permanent failure as transient.  ``require_ready()`` is the one
canonical classifier (error for state=="error", not_ready otherwise); the
``guard is None`` fallback (manager recovered to ready between the exception
and the re-check) falls back to ``not_ready()`` as the honest retry signal —
the call itself was not served either way.

No live analyzer, no network.  Reuses the mock-manager / tool-envelope idiom
from ``tests/test_ki9_delegate_teardown.py`` (test iii) and the real-manager
guard idiom from ``tests/test_phase34_delegates.py``.  Runs in CI as part of
``pytest -m "not integration"``.

Test coverage:
    1. Parametrized over all 6 call sites: delegate raises
       ``AnalyzerNotReadyError`` while the mock manager has (by the time of
       the re-check) transitioned to a non-ready, non-error state (mirrors a
       ``restart()`` landing mid-flight) -> tool returns ``not_ready``, not
       ``error``.
    2. The contract case, same 6 call sites: delegate raises
       ``AnalyzerNotReadyError`` AND by the time of the re-check the manager
       is in ``state == "error"`` with ``error_message`` set -> tool returns
       the CANONICAL ``require_ready()`` error envelope (the
       "failed to start" message), never ``not_ready`` and never the raw
       "LSP error: request_* ..." text.  This is the truth-preservation test.
    3. The find_references two-call scenario, explicitly: the first
       (references) call succeeds, the second (definition, reached only via
       ``include_declaration=True``) raises ``AnalyzerNotReadyError`` -> the
       tool returns a clean ``not_ready`` envelope, with no partial ``ok`` /
       ``references`` payload leaking through.
    4. Backward compatibility: ``AnalyzerNotReadyError`` is a ``RuntimeError``
       subclass, and each of the 5 delegate guards (on a real
       ``AnalyzerManager``, no mocks, ``_lsp is None``) raises it with the
       byte-identical message text the pre-fix ``RuntimeError`` used —
       existing ``pytest.raises(RuntimeError, match=...)`` callers keep
       working unchanged.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import (
    STATE_ERROR,
    STATE_INDEXING,
    STATE_READY,
    AnalyzerManager,
    AnalyzerNotReadyError,
)
from rust_lsp_mcp.envelope import STATUS_ERROR, STATUS_NOT_READY
from tests.test_phase34_delegates import _make_ready_manager as _make_real_lsp_none_manager

# ---------------------------------------------------------------------------
# Shared mock-manager helper (mirrors test_ki9_delegate_teardown.py's
# _ready_manager, plus an ``_error`` slot for the state=="error" scenario).
# ---------------------------------------------------------------------------


def _ready_manager() -> AnalyzerManager:
    """A bare AnalyzerManager stub in the ready state, with require_ready()'s
    dependencies (``state``, ``_lsp``, ``_error`` via the ``error_message``
    property) all settable directly — mirrors test_ki9_delegate_teardown.py's
    ``_ready_manager``.
    """
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = STATE_READY
    mgr._lsp = object()  # type: ignore[assignment]
    mgr._repository_root = "/fake/repo"
    mgr._error = None
    return mgr


def _toctou_to_indexing(mgr: AnalyzerManager) -> Any:
    """Side effect: simulate a restart() landing mid-flight — the manager
    drops to "indexing" (not ready, not errored) by the time the delegate's
    guard (and thus the tool's re-check of require_ready()) observes it."""

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        mgr.state = STATE_INDEXING
        mgr._lsp = None
        raise AnalyzerNotReadyError(
            "request_whatever called before analyzer is ready — call require_ready() first"
        )

    return _raise


def _toctou_to_error(mgr: AnalyzerManager) -> Any:
    """Side effect: simulate the background run failing permanently mid-flight
    — the manager lands in state=="error" with error_message set by the time
    the tool re-consults require_ready()."""

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        mgr.state = STATE_ERROR
        mgr._lsp = None
        mgr._error = "RuntimeError: rust-analyzer subprocess died mid-refresh"
        raise AnalyzerNotReadyError(
            "request_whatever called before analyzer is ready — call require_ready() first"
        )

    return _raise


# ---------------------------------------------------------------------------
# Per-call-site factories — each patches core._manager with a mock manager
# whose relevant delegate raises AnalyzerNotReadyError via the given side
# effect (either _toctou_to_indexing or _toctou_to_error).
# ---------------------------------------------------------------------------


async def _call_goto_definition(make_side_effect: Any) -> dict[str, Any]:
    from rust_lsp_mcp.tools.goto_definition import goto_definition

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(mgr, "request_definition", new=AsyncMock(side_effect=make_side_effect(mgr))),
    ):
        return await goto_definition("src/main.rs", 1, 1)


async def _call_hover(make_side_effect: Any) -> dict[str, Any]:
    from rust_lsp_mcp.tools.hover import hover

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(mgr, "request_hover", new=AsyncMock(side_effect=make_side_effect(mgr))),
    ):
        return await hover("src/main.rs", 1, 1)


async def _call_document_symbols(make_side_effect: Any) -> dict[str, Any]:
    from rust_lsp_mcp.tools.document_symbols import document_symbols

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(
            mgr, "request_document_symbols", new=AsyncMock(side_effect=make_side_effect(mgr))
        ),
    ):
        return await document_symbols("src/main.rs")


async def _call_find_symbol(make_side_effect: Any) -> dict[str, Any]:
    from rust_lsp_mcp.tools.find_symbol import find_symbol

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(
            mgr, "request_workspace_symbol", new=AsyncMock(side_effect=make_side_effect(mgr))
        ),
    ):
        return await find_symbol("foo")


async def _call_find_references(make_side_effect: Any) -> dict[str, Any]:
    """First (and only, since include_declaration defaults False) call site."""
    from rust_lsp_mcp.tools.find_references import find_references

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(mgr, "request_references", new=AsyncMock(side_effect=make_side_effect(mgr))),
    ):
        return await find_references("src/main.rs", 1, 1)


async def _call_find_references_declaration_definition_raises(
    make_side_effect: Any,
) -> dict[str, Any]:
    """The SECOND call site (issue #98's exact trigger): the references call
    succeeds, and the definition call — only reached because
    include_declaration=True — raises."""
    from rust_lsp_mcp.tools.find_references import find_references

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(mgr, "request_references", new=AsyncMock(return_value=[])),
        patch.object(mgr, "request_definition", new=AsyncMock(side_effect=make_side_effect(mgr))),
    ):
        return await find_references("src/main.rs", 1, 1, include_declaration=True)


_CALL_SITES = [
    _call_goto_definition,
    _call_hover,
    _call_document_symbols,
    _call_find_symbol,
    _call_find_references,
    _call_find_references_declaration_definition_raises,
]
_CALL_SITE_IDS = [
    "goto_definition",
    "hover",
    "document_symbols",
    "find_symbol",
    "find_references",
    "find_references_include_declaration",
]


# ---------------------------------------------------------------------------
# 1. AnalyzerNotReadyError + non-error TOCTOU state -> not_ready (not error)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", _CALL_SITES, ids=_CALL_SITE_IDS)
def test_not_ready_guard_maps_to_not_ready(factory: Any) -> None:
    """Pre-fix RED: the tool's generic ``except Exception`` catches the bare
    ``RuntimeError`` and returns
    ``error("LSP error: request_whatever called before analyzer is ready — "
    "call require_ready() first")`` — status ``"error"``, not ``"not_ready"``.
    Recorded verbatim from the pre-fix run.
    """
    result = asyncio.run(factory(_toctou_to_indexing))
    assert result["status"] == STATUS_NOT_READY, result
    assert result["status"] != STATUS_ERROR, result


# ---------------------------------------------------------------------------
# 2. AnalyzerNotReadyError + state=="error" TOCTOU -> canonical error (not
#    not_ready, and not the raw "LSP error: request_*" text either)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", _CALL_SITES, ids=_CALL_SITE_IDS)
def test_not_ready_guard_with_error_state_maps_to_canonical_error(factory: Any) -> None:
    """The truth-preservation test: the delegate guard trips for BOTH
    "still indexing" and "permanently errored" — collapsing it to
    ``not_ready()`` would misreport a permanent failure as transient.

    Pre-fix, this is genuinely red: the tool returns
    ``error("LSP error: request_whatever called before analyzer is ready — "
    "call require_ready() first")`` — the right STATUS (error) but the WRONG
    message/mechanism (it never consults require_ready()'s canonical
    "failed to start" text or the real error_message).  We assert on the
    canonical require_ready() message content so this is red pre-fix and
    green only once the tool re-consults require_ready().
    """
    result = asyncio.run(factory(_toctou_to_error))
    assert result["status"] == STATUS_ERROR, result
    assert "failed to start" in result["message"], result
    assert "rust-analyzer subprocess died mid-refresh" in result["message"], result
    # And explicitly NOT the raw guard message leaking through unmapped.
    assert "call require_ready() first" not in result["message"], result


# ---------------------------------------------------------------------------
# 3. find_references two-call scenario: no partial ok leaks through
# ---------------------------------------------------------------------------


def test_find_references_second_call_not_ready_no_partial_ok() -> None:
    """references succeeds, definition (include_declaration=True) raises
    AnalyzerNotReadyError mid-flight -> clean not_ready, no partial ok."""
    result = asyncio.run(_call_find_references_declaration_definition_raises(_toctou_to_indexing))
    assert result["status"] == STATUS_NOT_READY, result
    assert "references" not in result, result
    assert result["status"] != "ok", result


# ---------------------------------------------------------------------------
# 4. Backward compatibility: AnalyzerNotReadyError is a RuntimeError subclass;
#    the real delegate guards raise it with byte-identical message text.
# ---------------------------------------------------------------------------


def test_analyzer_not_ready_error_is_runtime_error_subclass() -> None:
    assert issubclass(AnalyzerNotReadyError, RuntimeError)


@pytest.mark.parametrize(
    "method_name,args",
    [
        ("request_workspace_symbol", ("foo",)),
        ("request_document_symbols", ("src/lib.rs",)),
        ("request_definition", ("src/lib.rs", 0, 0)),
        ("request_references", ("src/lib.rs", 0, 0)),
        ("request_hover", ("src/lib.rs", 0, 0)),
    ],
)
def test_real_delegate_guard_raises_analyzer_not_ready_error_verbatim(
    method_name: str, args: tuple[Any, ...]
) -> None:
    """No mocks: a real AnalyzerManager with _lsp is None (the guard-trips
    fixture from test_phase34_delegates.py).  Asserts both the subclass
    relationship and that the message text is unchanged from the pre-fix
    RuntimeError wording, so existing
    ``pytest.raises(RuntimeError, match="require_ready")`` callers keep
    passing untouched.
    """
    mgr = _make_real_lsp_none_manager()
    method = getattr(mgr, method_name)
    with pytest.raises(AnalyzerNotReadyError) as exc_info:
        asyncio.run(method(*args))
    assert isinstance(exc_info.value, RuntimeError)
    assert str(exc_info.value) == (
        f"{method_name} called before analyzer is ready — call require_ready() first"
    )
