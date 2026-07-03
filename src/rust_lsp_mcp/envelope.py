"""Status envelope infrastructure for rust-lsp-mcp tools.

Every tool returns a uniform dict envelope of the form::

    {"status": <status>, ...extra fields...}

Status vocabulary (all phases):
    ok        — query ran; payload may be populated or meaningfully empty.
    not_ready — still indexing (transient); caller should retry later.
    not_found — the named/located thing does not exist.
    error     — malformed input / internal / LSP failure; includes a message.
                Also returned by ``require_ready()`` — NOT ``not_ready`` —
                when the analyzer's background indexing run has permanently
                failed (``state == "error"``); this is only recoverable via
                the ``refresh`` tool.  The analogous doc-store failure
                (``doc_index_state == "error"``) is surfaced the same way by
                ``search_docs``.

Design intent:
    - Pure Python with no I/O — trivially unit-testable.
    - Not-ready / not-found / error are data in the envelope, never
      MCP protocol-level errors (those are reserved for genuine crashes).
    - ``not_found`` vs ``ok``+empty are intentionally distinct: ``not_found``
      means resolution failed (the symbol/position was never found);
      ``ok``+empty means analysis succeeded with a legitimately zero answer
      (e.g. ``find_references`` returning zero callers for a real symbol).
"""

from typing import Any

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_NOT_READY = "not_ready"
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def ok(**kwargs: Any) -> dict[str, Any]:
    """Return an ``ok`` envelope, merging any extra keyword fields."""
    return {"status": STATUS_OK, **kwargs}


def not_ready(
    message: str = "Server is still indexing. Retry after checking status.",
) -> dict[str, Any]:
    """Return a ``not_ready`` envelope with an optional human message."""
    return {"status": STATUS_NOT_READY, "message": message}


def not_found(
    message: str = "The requested symbol or position was not found.",
) -> dict[str, Any]:
    """Return a ``not_found`` envelope with an optional human message.

    Distinct from ``ok``+empty: this means the resolution step itself failed
    (e.g. zero workspace-symbol matches, or a position with no symbol).
    """
    return {"status": STATUS_NOT_FOUND, "message": message}


def error(message: str) -> dict[str, Any]:
    """Return an ``error`` envelope with a human-readable message."""
    return {"status": STATUS_ERROR, "message": message}


def lsp_failure(exc: Exception, context: str = "") -> dict[str, Any]:
    """Return an ``error`` envelope for a catch-all, unexpected LSP/search exception.

    Shared by every tool's catch-all ``except Exception`` fallback (reached
    only after ``require_ready()`` has already passed, i.e. the analyzer is
    healthy). Preserves the existing ``"LSP error: ..."`` prefix — tests
    assert on it (e.g. ``tests/test_goto_definition.py``,
    ``tests/test_document_symbols.py``) — and appends neutral guidance: this
    is an unexpected internal error, the analyzer is still running, retry the
    request once.  Deliberately does NOT advise calling ``refresh`` — that is
    an unconditional, minutes-long wholesale re-index, and this branch only
    fires when the analyzer is already ready, so advising refresh would trade
    a one-off glitch for needless destructive downtime.

    Args:
        exc: The caught exception.
        context: Optional short parenthetical naming which sub-call failed
            (e.g. ``"definition"`` for ``find_references``'s second delegate
            call under ``include_declaration=True``), mirroring the existing
            ``"LSP error (definition): ..."`` distinction.
    """
    prefix = f"LSP error ({context})" if context else "LSP error"
    return error(
        f"{prefix}: {exc}. This is an unexpected internal error; the analyzer "
        "is still running. Retry the request once."
    )
