"""Regression tests for KI-9 / #87 — delegate hang across a teardown drain.

multilspy 0.0.15's ``send_request`` (``lsp_protocol_handler/server.py``) registers
the pending request and then does a bare ``await request.cv.wait()`` — no
timeout, no teardown hook.  ``stop()`` never touches the pending-request table,
so a request in flight when ``restart()``/``shutdown()`` tears the run down
would otherwise await forever.  ``AnalyzerManager._race_teardown`` races each
delegate's raw LSP coroutine against ``self._shutdown_event`` and raises
``AnalyzerTornDownError`` (mapped to a ``not_ready`` envelope at the tool
layer) instead of hanging.

No live analyzer, no network.  Reuses the fake ``PatchedRustAnalyzer``
infrastructure from ``test_lifecycle_races.py`` (``AutoReadyLsp``, ``FakeLsp``,
the ``_no_git`` autouse fixture, ``_factory``, ``_await_first_instance``,
``_make_manager``, ``_cancel_leaked_tasks``) rather than re-implementing it.
Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    i.    restart() unblocks a delegate hung on a never-resolving request —
          the request raises ``AnalyzerTornDownError`` and the manager
          recovers once the replacement run reaches ready.
    ii.   shutdown() unblocks a hung delegate the same way.
    ii-b. If ``_shutdown_event`` is already set by the time a delegate's
          guard passes (the drain window where state is still "ready" and
          ``_lsp`` is still set), the delegate must fail fast — never even
          issue the request.
    iii.  Every one of the 6 tool call sites (goto_definition, hover,
          document_symbols, find_symbol, find_references x2) maps
          ``AnalyzerTornDownError`` to a ``not_ready`` envelope, not ``error``.
    iv.   The fast (non-teardown) path returns the request's result and
          leaves no extra tasks behind (the teardown-waiter task is fully
          reaped before the delegate returns).
    v.    Cancelling the delegate's own caller task propagates
          ``CancelledError`` and leaves no extra tasks behind.
    vi.   ``_race_teardown`` is exception-transparent: a null-response
          ``AssertionError`` is still visible to ``_is_null_response_assertion``
          (→ ``None``); a malformed-payload ``AssertionError`` still
          propagates unchanged.
    vii.  Invariant-pinning (this one was GREEN pre-fix): an EXTERNAL
          cancellation of restart() while its drain is wedged on an undead
          task (one that swallows its own cancellation) propagates
          ``CancelledError`` without restart() ever swapping
          ``_shutdown_event``.  On 3.12+ this is guaranteed by ``wait_for``
          itself — its timeout cancels-and-awaits, DELEGATING the cancellation
          to the drained task, so the drain stays parked inside ``wait_for``
          and can only get past it once the drained task is provably done.
          ``_drain_task``'s ``done()``-checked re-raise additionally guards
          the razor-thin ``_must_cancel`` race (timeout firing between the
          drain's suspension points).  Together they preserve the event/_lsp
          pairing invariant ``_race_teardown`` depends on.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import rust_lsp_mcp.core as core
from rust_lsp_mcp import analyzer as analyzer_module
from rust_lsp_mcp.analyzer import (
    STATE_READY,
    TORN_DOWN_RETRY_MESSAGE,
    AnalyzerManager,
    AnalyzerTornDownError,
)
from rust_lsp_mcp.envelope import STATUS_NOT_READY
from tests.test_lifecycle_races import (
    AutoReadyLsp,
    FakeLsp,
    _await_first_instance,
    _cancel_leaked_tasks,
    _factory,
    _make_manager,
    _no_git,  # noqa: F401 — autouse fixture, must be in this module's namespace
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class NavLsp(AutoReadyLsp):
    """AutoReadyLsp with a controllable ``request_definition``.

    ``request_gate`` is never set by default, so a caller awaiting
    ``request_definition`` hangs until the test releases it (mirroring
    multilspy's un-timed-out ``cv.wait()``) or until it is cancelled.
    """

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.request_started = asyncio.Event()
        self.request_gate = asyncio.Event()
        self.request_cancelled = asyncio.Event()
        self.result: Any = None
        self.exc: BaseException | None = None

    async def request_definition(self, *a: Any) -> Any:
        self.request_started.set()
        try:
            await self.request_gate.wait()
        except asyncio.CancelledError:
            self.request_cancelled.set()
            raise
        if self.exc is not None:
            raise self.exc
        return self.result


class TeardownDrainLsp(NavLsp):
    """NavLsp whose ``start_server`` blocks AFTER the ``yield`` returns.

    Holds open the exact window ``_race_teardown``'s fail-fast guard defends:
    ``shutdown()``/``restart()`` has already set ``_shutdown_event``, but
    ``_run``'s ``finally`` (which clears ``_lsp`` and would flip ``state``
    away from ready) cannot run until this post-yield ``teardown_gate`` is
    released — so a delegate's ordinary ``lsp is None or state != READY``
    guard would incorrectly pass.
    """

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.teardown_gate = asyncio.Event()

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["TeardownDrainLsp"]:
        self.entered.set()
        await self.quiesce_gate.wait()
        yield self
        await self.teardown_gate.wait()
        await self.server.shutdown()
        await self.server.stop()
        self.exited = True


class UndeadLsp(FakeLsp):
    """FakeLsp whose ``start_server`` swallows a cancellation pre-yield.

    Simulates an outgoing run that refuses to die within
    ``DRAIN_TIMEOUT_SECONDS``.  On 3.12+ ``wait_for``'s timeout does NOT raise
    ``TimeoutError`` while the awaited task is pending — it cancels the DRAIN
    task, and since the drain's ``_fut_waiter`` is the drained task,
    ``Task.cancel()`` DELEGATES that cancellation to it.  This fake catches
    that delegated cancellation and re-parks on ``second_gate`` (never set by
    the test) instead of completing, so the drain stays parked inside
    ``wait_for`` — it never enters the ``except TimeoutError`` branch.
    ``cancel_seen`` is an event-driven signal for the test: it fires the
    instant the delegated cancellation lands, so the test never has to guess
    with a sleep.
    """

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.second_gate = asyncio.Event()
        self.cancel_seen = asyncio.Event()

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["UndeadLsp"]:
        self.entered.set()
        try:
            await self.quiesce_gate.wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await self.second_gate.wait()
            raise
        yield self
        await self.server.shutdown()  # pragma: no cover - unreachable in this test
        await self.server.stop()
        self.exited = True


# ---------------------------------------------------------------------------
# i — restart() unblocks a hung delegate
# ---------------------------------------------------------------------------


class TestRestartUnblocksHungDelegate:
    def test_restart_unblocks_hung_delegate(self) -> None:
        instances: list[NavLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(NavLsp, instances))
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready

                    d = asyncio.create_task(mgr.request_definition("src/main.rs", 0, 0))
                    await asyncio.wait_for(first.request_started.wait(), 2)

                    await mgr.restart()

                    with pytest.raises(AnalyzerTornDownError):
                        await asyncio.wait_for(d, 2)
                    assert first.request_cancelled.is_set()

                    # Recovery: the replacement run reaches ready normally.
                    # (Instance count asserted only AFTER the ready event —
                    # the replacement task is not guaranteed to have had a
                    # turn, i.e. constructed its NavLsp, any earlier.)
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready
                    assert len(instances) == 2
                    assert mgr._lsp is instances[1]
                finally:
                    await mgr.shutdown()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# ii — shutdown() unblocks a hung delegate
# ---------------------------------------------------------------------------


class TestShutdownUnblocksHungDelegate:
    def test_shutdown_unblocks_hung_delegate(self) -> None:
        instances: list[NavLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(NavLsp, instances))
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready

                    d = asyncio.create_task(mgr.request_definition("src/main.rs", 0, 0))
                    await asyncio.wait_for(first.request_started.wait(), 2)

                    await mgr.shutdown()

                    with pytest.raises(AnalyzerTornDownError):
                        await asyncio.wait_for(d, 2)
                    assert first.request_cancelled.is_set()
                    assert mgr._closed is True
                finally:
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# ii-b — event already set at guard time => fail fast, no request issued
# ---------------------------------------------------------------------------


class TestEventAlreadySetFailsFast:
    def test_event_already_set_fails_fast(self) -> None:
        instances: list[TeardownDrainLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    analyzer_module, "PatchedRustAnalyzer", _factory(TeardownDrainLsp, instances)
                )
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready

                    shutdown_task = asyncio.create_task(mgr.shutdown())
                    # Let shutdown() acquire the lock, set _closed, bump the
                    # generation + set _shutdown_event (both synchronous, the
                    # first thing _drain_task does), and park inside its
                    # wait_for on the still-live old task — which itself is
                    # parked on teardown_gate inside start_server's post-yield
                    # code.  FIFO call_soon scheduling: a handful of sleep(0)
                    # turns reaches that steady state deterministically.
                    for _ in range(5):
                        await asyncio.sleep(0)

                    assert mgr._shutdown_event.is_set()
                    assert mgr.state == STATE_READY
                    assert mgr._lsp is first

                    with pytest.raises(AnalyzerTornDownError):
                        await asyncio.wait_for(mgr.request_definition("src/main.rs", 0, 0), 2)
                    assert not first.request_started.is_set()

                    first.teardown_gate.set()
                    await asyncio.wait_for(shutdown_task, 2)
                    assert mgr._closed is True
                finally:
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# iii — tool call sites map AnalyzerTornDownError to not_ready
# ---------------------------------------------------------------------------


def _ready_manager() -> AnalyzerManager:
    """A bare AnalyzerManager stub in the ready state (mirrors test_goto_definition.py)."""
    mgr = AnalyzerManager.__new__(AnalyzerManager)
    mgr.state = STATE_READY
    mgr._lsp = object()  # type: ignore[assignment]
    mgr._repository_root = "/fake/repo"
    return mgr


async def _call_goto_definition() -> dict[str, Any]:
    from rust_lsp_mcp.tools.goto_definition import goto_definition

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(
            mgr, "request_definition", new=AsyncMock(side_effect=AnalyzerTornDownError("x"))
        ),
    ):
        return await goto_definition("src/main.rs", 1, 1)


async def _call_hover() -> dict[str, Any]:
    from rust_lsp_mcp.tools.hover import hover

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(mgr, "request_hover", new=AsyncMock(side_effect=AnalyzerTornDownError("x"))),
    ):
        return await hover("src/main.rs", 1, 1)


async def _call_document_symbols() -> dict[str, Any]:
    from rust_lsp_mcp.tools.document_symbols import document_symbols

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(
            mgr, "request_document_symbols", new=AsyncMock(side_effect=AnalyzerTornDownError("x"))
        ),
    ):
        return await document_symbols("src/main.rs")


async def _call_find_symbol() -> dict[str, Any]:
    from rust_lsp_mcp.tools.find_symbol import find_symbol

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(
            mgr, "request_workspace_symbol", new=AsyncMock(side_effect=AnalyzerTornDownError("x"))
        ),
    ):
        return await find_symbol("foo")


async def _call_find_references() -> dict[str, Any]:
    from rust_lsp_mcp.tools.find_references import find_references

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(
            mgr, "request_references", new=AsyncMock(side_effect=AnalyzerTornDownError("x"))
        ),
    ):
        return await find_references("src/main.rs", 1, 1)


async def _call_find_references_declaration_definition_raises() -> dict[str, Any]:
    """The SECOND call site: the references call succeeds, the definition
    call (only reached because include_declaration=True) raises."""
    from rust_lsp_mcp.tools.find_references import find_references

    mgr = _ready_manager()
    with (
        patch.object(core, "_manager", mgr),
        patch.object(mgr, "request_references", new=AsyncMock(return_value=[])),
        patch.object(
            mgr, "request_definition", new=AsyncMock(side_effect=AnalyzerTornDownError("x"))
        ),
    ):
        return await find_references("src/main.rs", 1, 1, include_declaration=True)


@pytest.mark.parametrize(
    "factory",
    [
        _call_goto_definition,
        _call_hover,
        _call_document_symbols,
        _call_find_symbol,
        _call_find_references,
        _call_find_references_declaration_definition_raises,
    ],
    ids=[
        "goto_definition",
        "hover",
        "document_symbols",
        "find_symbol",
        "find_references",
        "find_references_include_declaration",
    ],
)
def test_tool_returns_not_ready_envelope(factory: Any) -> None:
    result = asyncio.run(factory())
    assert result["status"] == STATUS_NOT_READY, result
    assert result["message"] == TORN_DOWN_RETRY_MESSAGE


# ---------------------------------------------------------------------------
# iv — fast path returns the result and leaks nothing
# ---------------------------------------------------------------------------


class TestFastPathNoLeak:
    def test_fast_path_returns_and_leaks_nothing(self) -> None:
        instances: list[NavLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(NavLsp, instances))
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready

                    expected = [{"sentinel": True}]
                    first.result = expected
                    first.request_gate.set()

                    baseline = asyncio.all_tasks()
                    result = await asyncio.wait_for(mgr.request_definition("src/main.rs", 0, 0), 2)
                    assert result == expected
                    assert asyncio.all_tasks() == baseline
                finally:
                    await mgr.shutdown()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# v — cancelling the delegate's caller propagates CancelledError, no leaks
# ---------------------------------------------------------------------------


class TestDelegateCancellationPropagates:
    def test_delegate_cancellation_propagates(self) -> None:
        instances: list[NavLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(NavLsp, instances))
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready

                    baseline = asyncio.all_tasks()
                    d = asyncio.create_task(mgr.request_definition("src/main.rs", 0, 0))
                    await asyncio.wait_for(first.request_started.wait(), 2)

                    d.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await d

                    await asyncio.wait_for(first.request_cancelled.wait(), 2)
                    assert asyncio.all_tasks() == baseline
                finally:
                    await mgr.shutdown()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# vi — AssertionError transparency through the helper
# ---------------------------------------------------------------------------


class TestAssertionErrorTransparency:
    def test_null_response_assertion_returns_none(self) -> None:
        instances: list[NavLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(NavLsp, instances))
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready

                    first.exc = AssertionError("Unexpected response from Language Server: None")
                    first.request_gate.set()

                    result = await asyncio.wait_for(mgr.request_definition("src/main.rs", 0, 0), 2)
                    assert result is None
                finally:
                    await mgr.shutdown()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())

    def test_malformed_assertion_propagates(self) -> None:
        instances: list[NavLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(NavLsp, instances))
                mgr = _make_manager()
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(mgr._ready_event.wait(), 2)
                    assert mgr.is_ready

                    first.exc = AssertionError("malformed payload")
                    first.request_gate.set()

                    with pytest.raises(AssertionError, match="malformed payload"):
                        await asyncio.wait_for(mgr.request_definition("src/main.rs", 0, 0), 2)
                finally:
                    await mgr.shutdown()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# vii — external cancel during an undead drain preserves the pairing invariant
# ---------------------------------------------------------------------------


class TestExternalCancelDuringUndeadDrain:
    def test_external_cancel_during_undead_drain_preserves_pairing_invariant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invariant-pinning test (GREEN pre-fix), not a regression test.

        Pins the pre-existing behavior guaranteed by ``wait_for``'s own 3.12+
        semantics: its timeout cancels-and-awaits, delegating the cancellation
        to the drained task, so ``restart()`` can only get past ``_drain_task``
        once the drained task is provably done — the event/_lsp pairing
        invariant ``_race_teardown`` depends on.  The hardened
        ``done()``-checked re-raise in ``_drain_task``'s ``except TimeoutError``
        branch is NOT exercised here (that branch is only reachable via the
        razor-thin ``_must_cancel`` race, which is not deterministically
        drivable); this test guards the observable contract both mechanisms
        serve.
        """
        monkeypatch.setattr(analyzer_module, "DRAIN_TIMEOUT_SECONDS", 0.05)
        instances: list[UndeadLsp] = []

        async def _scenario() -> None:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(analyzer_module, "PatchedRustAnalyzer", _factory(UndeadLsp, instances))
                mgr = _make_manager()
                first: UndeadLsp | None = None
                try:
                    await mgr.start()
                    first = await _await_first_instance(instances)
                    await asyncio.wait_for(first.entered.wait(), 2)
                    # first is now parked on quiesce_gate.wait() (never set).
                    # The drain's wait_for timeout will cancel the DRAIN task,
                    # which DELEGATES the cancellation to the drained task
                    # (the drain's _fut_waiter); UndeadLsp swallows it and
                    # re-parks on second_gate — event-driven signal:
                    # cancel_seen.

                    r = asyncio.create_task(mgr.restart())
                    await asyncio.wait_for(first.cancel_seen.wait(), 2)
                    # restart() is still parked INSIDE _drain_task's wait_for
                    # (it never enters the except TimeoutError branch — the
                    # drained task is still pending), and it can never get
                    # past it on its own since the old task is undead.

                    shutdown_event_before = mgr._shutdown_event
                    assert shutdown_event_before.is_set()

                    # The external cancel likewise DELEGATES through the
                    # parked drain to the undead task, which finally dies;
                    # the CancelledError propagates out of wait_for as-is
                    # (an external cancellation is never converted to
                    # TimeoutError) and up through restart().
                    r.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await r

                    # Pairing invariant held: restart() never reached step 6
                    # (fresh events) or step 7 (new task) — the event set by
                    # the drain is still the live one, and no replacement
                    # instance was constructed.
                    assert mgr._shutdown_event is shutdown_event_before
                    assert mgr._shutdown_event.is_set()
                    assert len(instances) == 1
                finally:
                    if first is not None:
                        first.second_gate.set()
                    await _cancel_leaked_tasks()

        asyncio.run(_scenario())
