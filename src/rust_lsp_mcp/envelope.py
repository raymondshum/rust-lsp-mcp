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
                the ``refresh`` tool.  Also returned with ``refresh`` by
                ``require_ready()`` while rust-analyzer is stalled (``status``
                reports ``analyzer_stall``), whatever ``state`` reads: a
                stalled analyzer never finishes its indexing run on its own.
                The analogous doc-store failure
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

Recovery hint (additive, non-``ok`` envelopes only):
    Every ``error``/``not_ready`` envelope also carries a ``recovery`` field —
    a single machine-parseable hint at the next action, so a caller can branch
    deterministically instead of NLP-parsing ``message``. Exactly four values
    (see the ``RECOVERY_*`` constants below); no ``retriable`` bool and no
    ``code`` enum — both were considered and dropped (see
    ``docs/audit/2026-07-02-usability-review.md``, UR-6).
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
# Recovery-hint constants — additive field on error/not_ready envelopes only.
# ---------------------------------------------------------------------------

RECOVERY_FIX_INPUT = "fix_input"
RECOVERY_REFRESH = "refresh"
RECOVERY_POLL_STATUS = "poll_status"
RECOVERY_UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def ok(**kwargs: Any) -> dict[str, Any]:
    """Return an ``ok`` envelope, merging any extra keyword fields."""
    return {"status": STATUS_OK, **kwargs}


def not_ready(
    message: str = "Server is still indexing. Retry after checking status.",
) -> dict[str, Any]:
    """Return a ``not_ready`` envelope with an optional human message.

    Always carries ``recovery: "poll_status"`` — ``not_ready`` is by
    definition the transient/self-healing status, so the recovery action is
    never ambiguous: poll a readiness tool (``status``/``analyzer_status``)
    and retry.
    """
    return {"status": STATUS_NOT_READY, "message": message, "recovery": RECOVERY_POLL_STATUS}


def not_found(
    message: str = "The requested symbol or position was not found.",
) -> dict[str, Any]:
    """Return a ``not_found`` envelope with an optional human message.

    Distinct from ``ok``+empty: this means the resolution step itself failed
    (e.g. zero workspace-symbol matches, or a position with no symbol).
    """
    return {"status": STATUS_NOT_FOUND, "message": message}


def error(message: str, recovery: str = RECOVERY_UNKNOWN) -> dict[str, Any]:
    """Return an ``error`` envelope with a human-readable message and a recovery hint.

    Args:
        message:  Human-readable description of what went wrong.
        recovery: One of ``RECOVERY_FIX_INPUT`` (malformed client input — fix
            the arguments, do not retry as-is), ``RECOVERY_REFRESH`` (a
            permanent analyzer/doc-index failure — recover via the
            ``refresh`` tool), ``RECOVERY_POLL_STATUS`` (rare on ``error``;
            reserved for parity with ``not_ready``), or ``RECOVERY_UNKNOWN``
            (the default — genuinely ambiguous or unclassified failures, e.g.
            the catch-all LSP-exception residue). Always present so callers
            never have to NLP-parse ``message`` to decide the next action.
    """
    return {"status": STATUS_ERROR, "message": message, "recovery": recovery}


# rust-analyzer's text for a position past the end of the file, from
# ``LineIndex`` offset conversion: ``Invalid offset LineCol { line: 99998,
# col: 0 } (line index length: 18464)`` (live probe, rust-analyzer 1.99.1,
# 2026-11-07).  Matched on the text alone: it arrives as JSON-RPC -32603
# (InternalError), the code rust-analyzer also uses for request panics.
INVALID_OFFSET_TEXT = "Invalid offset"


def lsp_failure(
    exc: Exception, context: str = "", *, position: str | None = None
) -> dict[str, Any]:
    """Return an ``error`` envelope for a catch-all, unexpected LSP/search exception.

    Shared by every tool's catch-all ``except Exception`` fallback (reached
    only after ``require_ready()`` has already passed, i.e. the analyzer is
    healthy). When the analyzer is in ``state == "error"`` by the time the
    exception arrives (rust-analyzer died during the request), it returns
    ``require_ready()``'s ``refresh`` envelope instead, because the text below
    would claim a running analyzer. Preserves the existing ``"LSP error: ..."`` prefix — tests
    assert on it (e.g. ``tests/test_goto_definition.py``,
    ``tests/test_document_symbols.py``) — and appends neutral guidance: this
    is an unexpected internal error, the analyzer is still running, retry the
    request once.  Deliberately does NOT advise calling ``refresh`` — that is
    an unconditional, minutes-long wholesale re-index, and this branch only
    fires when the analyzer is already ready, so advising refresh would trade
    a one-off glitch for needless destructive downtime.

    Three cases come before that text, in this order:

    - a stalled analyzer (``AnalyzerManager.stall_report()`` is not None, FW1):
      ``require_ready()``'s ``refresh`` envelope, because a request that
      fails during a stall (a re-panic on a request thread) fails again;
    - an ``AnalyzerRequestTimeoutError`` (the server-side request timeout):
      ``recovery: "unknown"`` with a message that rust-analyzer did not
      answer in time, that the caller may retry once, and that repeated
      timeouts are worth a ``status`` check for ``analyzer_stall``;
    - rust-analyzer's "Invalid offset" error (a position past the end of the
      file, backlog line 172): ``recovery: "fix_input"``.

    Args:
        exc: The caught exception.
        context: Optional short parenthetical naming which sub-call failed
            (e.g. ``"definition"`` for ``find_references``'s second delegate
            call under ``include_declaration=True``), mirroring the existing
            ``"LSP error (definition): ..."`` distinction.
        position: The ``file:line:character`` the caller sent (1-indexed),
            named in the out-of-range message when given.
    """
    # The analyzer can fail between require_ready() and here (the process
    # died mid-request, SW-06): then the "still running, retry" text would be
    # false, so return require_ready()'s refresh envelope instead.  Deferred
    # import: core imports this module at load time.
    from rust_lsp_mcp import core
    from rust_lsp_mcp.analyzer import STATE_ERROR, AnalyzerRequestTimeoutError

    manager = core.get_manager()
    if manager is not None and manager.state == STATE_ERROR:
        guard = core.require_ready()
        if guard is not None:
            return guard
    if manager is not None and isinstance(manager.stall_report(), dict):
        guard = core.require_ready()
        if guard is not None:
            return guard
    if isinstance(exc, AnalyzerRequestTimeoutError):
        return error(
            f"rust-analyzer did not answer the {exc.op} request within "
            f"{exc.timeout_seconds:g} seconds (RLM_REQUEST_TIMEOUT_SECONDS); the "
            "analyzer is still running. Retry the request once; if requests keep "
            "timing out, check status for analyzer_stall.",
            recovery=RECOVERY_UNKNOWN,
        )
    if INVALID_OFFSET_TEXT in str(exc):
        where = f"The position {position}" if position else "The position"
        return error(
            f"{where} is past the end of the file (rust-analyzer: {exc}). "
            "Check the line and character against the file's current contents.",
            recovery=RECOVERY_FIX_INPUT,
        )
    prefix = f"LSP error ({context})" if context else "LSP error"
    return error(
        f"{prefix}: {exc}. This is an unexpected internal error; the analyzer "
        "is still running. Retry the request once.",
        recovery=RECOVERY_UNKNOWN,
    )


def analyzer_not_running(exit_code: int) -> dict[str, Any]:
    """Return the ``error`` envelope for a rust-analyzer process that exited (SW-06).

    ``require_ready()`` returns it while ``state == "error"`` and the manager
    recorded an ``analyzer_exit_code``: the process died without a shutdown
    request, before or after readiness. Recovery is ``refresh``.
    """
    if exit_code == 127:
        # multilspy launches through /bin/sh -c: 127 is the shell's "command
        # not found", so refresh alone cannot help.
        return error(
            "rust-analyzer binary not found (shell exit code 127); check "
            "RLM_RUST_ANALYZER_BIN, then call refresh",
            recovery=RECOVERY_REFRESH,
        )
    return error(
        f"rust-analyzer is not running (exited with code {exit_code}); call refresh",
        recovery=RECOVERY_REFRESH,
    )
