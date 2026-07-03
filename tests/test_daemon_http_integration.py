"""Integration tests for Phase C1 — daemon transport (real streamable-HTTP,
real analyzer). Marker: ``integration`` (registered in pyproject.toml).

Run locally only, via the podman harness described in
docs/handoff/cli-frontend-effort-handoff.md: ``uv run pytest -m integration``.
Never runs in CI. NOT run by the build agent — the QA gate runs this.

These are the two Phase 1 DoD proofs (docs/handoff/cli-phase-1-daemon.md):

    (a) warm-across-connections: two sequential raw-client connections must
        hit the SAME warm analyzer — no re-index between them (D2/U1's core
        claim: the lifespan initializes once per PROCESS, not per MCP
        session/request).
    (b) teardown-race: `refresh` issued concurrently with N in-flight nav
        calls, each over its own separate HTTP request (stateless mode) —
        nav calls must return cleanly (never hang/crash), the manager must
        recover to `ready` afterward, and the SDK's stateless session
        manager must show zero leaked `_server_instances` (U2 / KI-9).

(c) "stdio regression — existing integration suite green" from the DoD is
NOT a new test here: this phase does not touch the stdio code path (see
`core._build_mcp`'s stdio branch, unit-tested byte-identical in
tests/test_daemon_transport_fast.py), and the existing stdio-path
integration suite (tests/test_phase1_integration.py etc.) is unmodified —
the QA gate re-running it green is the proof.

Both tests share the session-scoped `daemon_app` fixture (tests/daemon_fixture.py)
so the daemon pays the cold-index cost exactly once for the whole file.
"""

import time
from typing import Any

import anyio
import pytest

from tests.daemon_fixture import DaemonHandle, call_tool, wait_until_ready

# ---------------------------------------------------------------------------
# (a) Warm-across-connections
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_warm_across_connections_same_analyzer(daemon_app: DaemonHandle) -> None:
    """Two SEQUENTIAL client connections must hit the SAME warm analyzer.

    Combines both signals the DoD calls for:
        - `status` state: the SECOND connection (a brand-new
          `streamablehttp_client` + `ClientSession` — a fresh MCP session,
          and in stateless HTTP mode a fresh transport PER REQUEST too) must
          observe `state == "ready"` immediately; a cold re-init would start
          back at `"indexing"`.
        - Object identity: `core_mod.get_manager()` read directly in-process
          — mirroring the SDK's own live proof (see
          docs/reference/mcp-streamable-http-daemon.md: "two separate client
          connections saw `init_count == 1` and the same shared-object id")
          — must return the SAME `AnalyzerManager` instance both times.
        - Timing: a real re-index of the ripgrep fixture takes tens of
          seconds; a second `status` round-trip against an already-warm
          daemon must be near-instant.
    """
    first = anyio.run(wait_until_ready, daemon_app.base_url)
    assert first["state"] == "ready"
    manager_after_first = daemon_app.core_mod.get_manager()
    assert manager_after_first is not None

    start = time.monotonic()
    second = anyio.run(call_tool, daemon_app.base_url, "status")
    elapsed = time.monotonic() - start

    assert second["state"] == "ready"
    manager_after_second = daemon_app.core_mod.get_manager()
    assert manager_after_second is manager_after_first, (
        "second connection observed a DIFFERENT AnalyzerManager instance — "
        "the analyzer was re-initialized per session/request instead of "
        "once per process"
    )
    assert elapsed < 5.0, (
        f"second status call took {elapsed:.2f}s — too slow for an "
        "already-warm daemon; suggests a re-index was triggered"
    )


# ---------------------------------------------------------------------------
# (b) Teardown-race: refresh concurrent with in-flight nav calls
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_refresh_races_concurrent_nav_calls(daemon_app: DaemonHandle) -> None:
    """`refresh` concurrent with N in-flight nav calls over separate HTTP
    requests: nav returns cleanly (never hangs, never crashes, never an
    unhandled `error`), the manager recovers to `ready`, and
    `_server_instances` stays empty throughout (U2 / KI-9 — only observable
    in-process, which is the whole reason this fixture runs in-process
    rather than via podman exec).
    """
    anyio.run(wait_until_ready, daemon_app.base_url)

    n_nav_calls = 8

    async def _scenario() -> tuple[list[dict[str, Any] | None], dict[str, Any]]:
        nav_results: list[dict[str, Any] | None] = [None] * n_nav_calls
        refresh_result_holder: dict[str, Any] = {}

        async def _nav(i: int) -> None:
            nav_results[i] = await call_tool(daemon_app.base_url, "find_symbol", {"name": "main"})

        async def _refresh() -> None:
            refresh_result_holder["result"] = await call_tool(daemon_app.base_url, "refresh")

        async with anyio.create_task_group() as tg:
            for i in range(n_nav_calls):
                tg.start_soon(_nav, i)
            tg.start_soon(_refresh)

        return nav_results, refresh_result_holder["result"]

    nav_results, refresh_result = anyio.run(_scenario)

    assert refresh_result["status"] == "ok", f"refresh did not report ok: {refresh_result!r}"

    # Every nav call must have completed cleanly (anyio.run returning at all
    # already rules out a hang) with a status that is an ANSWER, not a
    # failure: "ok"/"not_found" if it beat the teardown, "not_ready" if it
    # landed during the re-index window. "error" would mean the readiness
    # guard was bypassed mid-teardown (KI-9) and must never appear.
    for i, result in enumerate(nav_results):
        assert result is not None, f"nav call {i} never completed"
        assert result.get("status") in ("ok", "not_found", "not_ready"), (
            f"nav call {i} returned an unexpected status: {result!r}"
        )

    final_status = anyio.run(wait_until_ready, daemon_app.base_url)
    assert final_status["state"] == "ready", (
        f"manager did not recover to ready after the race: {final_status!r}"
    )

    # U2: stateless mode creates a fresh transport per request and
    # terminates it after each one — _server_instances must be empty now
    # that every request (including the race) has completed.
    server_instances = daemon_app.session_manager()._server_instances
    assert server_instances == {}, (
        f"_server_instances leaked entries after the refresh/nav race: {server_instances!r}"
    )
