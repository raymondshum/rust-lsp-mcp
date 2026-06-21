"""Adversarial regression tests for Phase 3+4 (navigation + operational tools).

Marker: ``integration`` (needs the live rust-analyzer + ripgrep fixture).
Run locally only: ``uv run pytest -m integration tests/test_phase34_adversarial.py``
Never runs in CI.

These tests encode CONFIRMED contract breaks found during the adversarial review.
They are EXPECTED TO FAIL against the current product code — they are falsifiers,
not green tests.  Do NOT "fix" them by relaxing the assertion; the fix belongs in
the product (map the analyzer's "no resolution here" signal to ``not_found``,
not ``error``).

Contract being attacked (envelope discipline):
    - ``not_found`` = resolution failed (no symbol/definition/hover at that position).
    - ``error``     = bad input / internal / LSP failure.

CONFIRMED BREAK
---------------
When ``goto_definition`` / ``find_references`` are called at a position that does
not resolve to any symbol (blank line, comment text, doc-comment), the live
rust-analyzer returns JSON-RPC ``null`` for ``textDocument/definition`` /
``textDocument/references``.  multilspy then raises:

    - references:  ``assert isinstance(response, list)``  -> AssertionError
    - definition:  ``assert False, f"...{response}"``      -> AssertionError

The tool catches the exception and returns ``error`` ("Unexpected response from
Language Server: None").  Per the contract this MUST be ``not_found`` — the
position simply has nothing to resolve; that is a normal "nothing here" outcome,
not an LSP failure.  Returning ``error`` misleads the assistant into believing
the analyzer is broken rather than that there is no symbol at that spot.

Note: the delegate methods' ``if result is None: return []`` normalization is
dead code for references/definition — multilspy asserts on ``null`` *before*
returning, so the delegate never observes ``None`` for these two calls.
"""

import anyio
import pytest

from rust_lsp_mcp.analyzer import STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_NOT_FOUND
from rust_lsp_mcp.settings import get_settings


@pytest.fixture(scope="module")
def settings():
    return get_settings()


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


# Positions verified against the live analyzer over /workspaces/ripgrep.
# crates/core/main.rs:
#   line 41 (1-idx) is a blank line.
#   line 42 (1-idx) is a doc comment: "/// Then, as it was, then again it will be."
_NONRESOLVING = "crates/core/main.rs"
_BLANK_LINE = 41
_COMMENT_LINE = 42
_COMMENT_CHAR = 10  # inside the comment text


async def _run(settings, coro_factory):
    async def _inner(manager):
        from unittest.mock import patch

        import rust_lsp_mcp.core as core

        with patch.object(core, "_manager", manager):
            return await coro_factory()

    return await _with_warm_manager(settings, _inner)


@pytest.mark.integration
def test_goto_definition_on_comment_is_not_found_not_error(settings) -> None:
    """goto_definition on comment text must be not_found, not error (CONFIRMED BREAK).

    The analyzer returns null -> multilspy raises -> tool currently returns error.
    Contract requires not_found.
    """
    from rust_lsp_mcp.tools.goto_definition import goto_definition

    result = anyio.run(
        _run, settings, lambda: goto_definition(_NONRESOLVING, _COMMENT_LINE, _COMMENT_CHAR)
    )
    assert result["status"] == STATUS_NOT_FOUND, (
        f"goto_definition on a comment must be not_found (no symbol there), "
        f"but got {result!r}.  Returning 'error' misleads the assistant that the "
        f"LSP failed when the true answer is 'nothing to resolve here'."
    )


@pytest.mark.integration
def test_find_references_on_blank_line_is_not_found_not_error(settings) -> None:
    """find_references on a blank line must be not_found, not error (CONFIRMED BREAK).

    rust-analyzer returns null for textDocument/references at a non-symbol
    position; multilspy's ``assert isinstance(response, list)`` raises; the tool
    returns error.  Contract requires not_found (resolution failed).  Note: this
    is distinct from a *real* zero-reference symbol, which correctly returns
    ok+[] (see test_find_references_zero_callers_is_ok_empty).
    """
    from rust_lsp_mcp.tools.find_references import find_references

    result = anyio.run(_run, settings, lambda: find_references(_NONRESOLVING, _BLANK_LINE, 1))
    assert result["status"] == STATUS_NOT_FOUND, (
        f"find_references on a blank line must be not_found (no symbol there), "
        f"but got {result!r}.  An AssertionError from multilspy on a null LSP "
        f"response is being surfaced as 'error', violating the envelope contract."
    )


@pytest.mark.integration
def test_find_references_on_comment_is_not_found_not_error(settings) -> None:
    """find_references on comment text must be not_found, not error (CONFIRMED BREAK)."""
    from rust_lsp_mcp.tools.find_references import find_references

    result = anyio.run(
        _run, settings, lambda: find_references(_NONRESOLVING, _COMMENT_LINE, _COMMENT_CHAR)
    )
    assert result["status"] == STATUS_NOT_FOUND, (
        f"find_references on a comment must be not_found, but got {result!r}."
    )


# ---------------------------------------------------------------------------
# Positive control: the zero-reference contract DOES hold (attack #1 closed).
# This test should PASS — it documents that a genuine zero-caller symbol
# (fn main) returns ok+[] because the live analyzer emits [] (not null).
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_find_references_zero_callers_is_ok_empty(settings) -> None:
    """fn main has zero in-tree callers; analyzer returns [] (not null) -> ok+[].

    This closes the previously-UNPROVEN attack #1: live rust-analyzer emits an
    empty list (not JSON null) for a genuinely zero-reference symbol, so the
    contract's ok+empty path is correct.  fn main is at crates/core/main.rs
    line 43 (1-idx), 'main' identifier at char 4.
    """
    from rust_lsp_mcp.envelope import STATUS_OK
    from rust_lsp_mcp.tools.find_references import find_references

    result = anyio.run(_run, settings, lambda: find_references("crates/core/main.rs", 43, 4))
    assert result["status"] == STATUS_OK, f"Expected ok for zero-caller fn main, got {result!r}"
    assert result["references"] == [], (
        f"fn main has no in-tree callers; expected empty list, got {result['references']!r}"
    )


# ---------------------------------------------------------------------------
# NEW BREAK (introduced by the AssertionError fix): masking of malformed
# LSP responses.
#
# The fix added a blanket ``except AssertionError: return None`` to
# ``AnalyzerManager.request_references`` / ``request_definition``.  multilspy
# 0.0.15 uses ``AssertionError`` for TWO distinct conditions:
#
#   1. A JSON-RPC ``null`` response  -> "Unexpected response ...: None"
#      (the legitimate "no symbol here" case -> not_found, correct).
#   2. A malformed-but-non-null response shape, e.g. a NON-empty references
#      list whose item is missing the ``uri``/``range`` keys
#      (``assert LSPConstants.URI in item`` at language_server.py:477), or a
#      definition item that matches neither Location nor LocationLink
#      (``assert False, f"Unexpected response ...: {item}"`` at line 424).
#      This is a GENUINE LSP/protocol failure that the envelope contract
#      classifies as ``error`` ("malformed input / internal / LSP failure"),
#      NOT ``not_found``.
#
# Because the catch does not discriminate on the assertion message, condition
# (2) is now swallowed and reported as ``not_found`` — a misleading "nothing
# here" when the analyzer actually returned something broken.  The fix should
# have been narrow (re-raise unless the assertion is the null-response one,
# which carries the message suffix "None").
#
# These tests need no live analyzer: they inject a fake ``_lsp`` whose delegate
# raises the SAME malformed-shape AssertionError multilspy raises, then assert
# the tool surfaces ``error`` (NOT not_found).  They FAIL against current code.
# ---------------------------------------------------------------------------


class _MalformedLSP:
    """Stand-in for the live LSP whose calls raise multilspy's malformed-shape
    AssertionError (a non-``None`` assertion — a genuine protocol failure, not a
    null response)."""

    async def request_references(self, *_a, **_k):
        # Mirrors language_server.py:477 `assert LSPConstants.URI in item`
        # (a bare assertion with no message) for a non-empty, malformed list.
        raise AssertionError

    async def request_definition(self, *_a, **_k):
        # Mirrors language_server.py:424 `assert False, "Unexpected response ...: {item}"`.
        raise AssertionError("Unexpected response from Language Server: {'garbage': 1}")


def _run_with_fake_lsp(settings, coro_factory):
    """Warm-start a manager, then swap _lsp for the malformed fake before calling."""

    async def _inner(manager):
        from unittest.mock import patch

        import rust_lsp_mcp.core as core

        manager._lsp = _MalformedLSP()
        with patch.object(core, "_manager", manager):
            return await coro_factory()

    return anyio.run(_with_warm_manager, settings, _inner)


@pytest.mark.integration
def test_find_references_malformed_response_is_error_not_found(settings) -> None:
    """A malformed (non-null) references response is an LSP failure -> error.

    NEW BREAK: the blanket `except AssertionError: return None` collapses this
    genuine protocol failure into `not_found`, telling the assistant "no symbol
    here" when the analyzer actually returned a broken payload.
    """
    from rust_lsp_mcp.envelope import STATUS_ERROR
    from rust_lsp_mcp.tools.find_references import find_references

    result = _run_with_fake_lsp(settings, lambda: find_references("crates/core/main.rs", 43, 4))
    assert result["status"] == STATUS_ERROR, (
        f"A malformed (non-null) LSP references response is an LSP failure and must "
        f"be 'error' per the envelope contract, but got {result!r}.  The blanket "
        f"AssertionError catch masks a real protocol failure as 'not_found'."
    )


@pytest.mark.integration
def test_goto_definition_malformed_response_is_error_not_found(settings) -> None:
    """A malformed (non-null) definition response is an LSP failure -> error.

    NEW BREAK: same masking as above via request_definition's blanket catch.
    """
    from rust_lsp_mcp.envelope import STATUS_ERROR
    from rust_lsp_mcp.tools.goto_definition import goto_definition

    result = _run_with_fake_lsp(settings, lambda: goto_definition("crates/core/main.rs", 43, 4))
    assert result["status"] == STATUS_ERROR, (
        f"A malformed (non-null) LSP definition response is an LSP failure and must "
        f"be 'error', but got {result!r}.  The blanket AssertionError catch in "
        f"request_definition masks a real protocol failure as 'not_found'."
    )
