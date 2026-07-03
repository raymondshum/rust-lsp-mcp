"""Fast tests for Phase C1 — daemon transport (docs/handoff/cli-phase-1-daemon.md).

No live analyzer, no network — runs in CI as part of ``pytest -m "not integration"``.
Covers:
    - Settings parsing for the new `transport`/`http_port` fields (env vars,
      defaults, Literal validation).
    - Conditional FastMCP construction (`core._build_mcp`) — stdio keeps
      `lifespan=_lifespan` byte-identical to the pre-Phase-1 wiring; HTTP mode
      passes no `lifespan=` and sets host/port/stateless_http/json_response.
    - Loopback hard-coding: no Settings value can move the HTTP host off
      127.0.0.1 (D3/D4), and `FASTMCP_*` env vars cannot override the port we
      pass explicitly (D3's "RLM_* plumbed explicitly, not FASTMCP_*" rule).
    - The composed daemon lifespan (`server.compose_daemon_lifespan`) nests a
      stubbed `_lifespan` around a stubbed "original" (SDK) lifespan in the
      documented enter/exit order.

`core._build_mcp` exists specifically so this module can unit-test the
transport branch without an `importlib.reload` dance — the module-level `mcp`
app is still built exactly once at import (the process-level contract the
daemon relies on); see core.py's comment above the `mcp = _build_mcp(...)`
line.

What this file does NOT cover: the live daemon-mode wiring (real
streamable-HTTP server on loopback, warm-across-connections, the
teardown-race). Those require driving requests against the process-level
`mcp` app built at import and are proven by the integration fixture/tests in
tests/test_daemon_http_integration.py (marker `integration`, local QA gate
only).
"""

import contextlib
from collections.abc import AsyncIterator

import anyio
import pytest
from pydantic import ValidationError

from rust_lsp_mcp import server as server_mod
from rust_lsp_mcp.core import _build_mcp, _lifespan
from rust_lsp_mcp.settings import Settings

# ---------------------------------------------------------------------------
# Settings parsing
# ---------------------------------------------------------------------------


def test_transport_defaults_to_stdio() -> None:
    s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.transport == "stdio"
    assert s.http_port == 8000


def test_transport_and_port_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RLM_TRANSPORT", "streamable-http")
    monkeypatch.setenv("RLM_HTTP_PORT", "9001")
    s = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert s.transport == "streamable-http"
    assert s.http_port == 9001


def test_invalid_transport_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only "stdio"/"streamable-http" are valid — a Literal, not a bare str."""
    monkeypatch.setenv("RLM_TRANSPORT", "sse")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # ty: ignore[unknown-argument]


def test_settings_has_no_host_field() -> None:
    """Host is a literal hard-coded in core.py/server.py, never a Settings
    field — there must be no configuration path that could move it off
    loopback (D3/D4)."""
    assert "host" not in Settings.model_fields


# ---------------------------------------------------------------------------
# Conditional FastMCP construction (core._build_mcp)
# ---------------------------------------------------------------------------


def test_build_mcp_stdio_keeps_lifespan_byte_identical() -> None:
    settings = Settings(transport="stdio", _env_file=None)  # ty: ignore[unknown-argument]
    mcp = _build_mcp(settings)
    assert mcp.settings.lifespan is _lifespan


def test_build_mcp_http_passes_no_lifespan_kwarg() -> None:
    settings = Settings(transport="streamable-http", http_port=9100, _env_file=None)  # ty: ignore[unknown-argument]
    mcp = _build_mcp(settings)
    assert mcp.settings.lifespan is None


def test_build_mcp_http_sets_stateless_and_json_response() -> None:
    settings = Settings(transport="streamable-http", http_port=9100, _env_file=None)  # ty: ignore[unknown-argument]
    mcp = _build_mcp(settings)
    assert mcp.settings.stateless_http is True
    assert mcp.settings.json_response is True


def test_build_mcp_http_uses_settings_port() -> None:
    settings = Settings(transport="streamable-http", http_port=54321, _env_file=None)  # ty: ignore[unknown-argument]
    mcp = _build_mcp(settings)
    assert mcp.settings.port == 54321


def test_build_mcp_stdio_does_not_set_stateless_or_json_response() -> None:
    """stdio mode must be indistinguishable from pre-Phase-1: no stray HTTP
    kwargs bleed into the stdio construction branch."""
    settings = Settings(transport="stdio", _env_file=None)  # ty: ignore[unknown-argument]
    mcp = _build_mcp(settings)
    assert mcp.settings.stateless_http is False
    assert mcp.settings.json_response is False


@pytest.mark.parametrize("http_port", [1, 8000, 65535])
def test_build_mcp_http_host_always_loopback(http_port: int) -> None:
    """No matter what else varies, HTTP host is always 127.0.0.1 — never
    configurable (D3/D4)."""
    settings = Settings(transport="streamable-http", http_port=http_port, _env_file=None)  # ty: ignore[unknown-argument]
    mcp = _build_mcp(settings)
    assert mcp.settings.host == "127.0.0.1"


def test_build_mcp_http_ignores_fastmcp_env_plumbing(monkeypatch: pytest.MonkeyPatch) -> None:
    """FastMCP reads FASTMCP_* env vars into its own Settings by default (see
    docs/planning/cli-frontend.md D3) — but we pass host/port/stateless_http/
    json_response as explicit constructor kwargs, which MUST win over
    whatever FASTMCP_* pollution happens to be in the environment. This is
    the "RLM_* vs FASTMCP_*" plumbing the adversarial pass calls out."""
    monkeypatch.setenv("FASTMCP_PORT", "1")
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    monkeypatch.setenv("FASTMCP_STATELESS_HTTP", "false")
    monkeypatch.setenv("FASTMCP_JSON_RESPONSE", "false")
    settings = Settings(transport="streamable-http", http_port=7777, _env_file=None)  # ty: ignore[unknown-argument]
    mcp = _build_mcp(settings)
    assert mcp.settings.port == 7777
    assert mcp.settings.host == "127.0.0.1"
    assert mcp.settings.stateless_http is True
    assert mcp.settings.json_response is True


# ---------------------------------------------------------------------------
# Composed daemon lifespan ordering (server.compose_daemon_lifespan)
# ---------------------------------------------------------------------------


def test_compose_daemon_lifespan_nests_core_around_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composed lifespan must enter `_lifespan` (core — analyzer/doc-store/
    preflight) BEFORE the SDK's original (session-manager) lifespan, and exit
    in the reverse order — matching the reference doc's proven wiring
    (docs/reference/mcp-streamable-http-daemon.md) exactly, just written as a
    single combined `async with` (ruff SIM117) instead of nested `with`
    blocks (semantically identical enter/exit ordering).

    Also proves each stubbed lifespan is entered exactly once per composed
    invocation — a hand-rolled composition could accidentally double-enter
    one side; the real "once-per-process" guarantee (Starlette/ASGI calls
    this lifespan exactly once for the app's whole lifetime, not per request)
    is a property of the HTTP framework, exercised live by the integration
    fixture, not reproducible at this unit-test level.
    """
    events: list[str] = []
    core_enter_count = 0
    original_enter_count = 0

    @contextlib.asynccontextmanager
    async def fake_core_lifespan(app: object) -> AsyncIterator[None]:
        nonlocal core_enter_count
        core_enter_count += 1
        events.append("core_enter")
        yield
        events.append("core_exit")

    @contextlib.asynccontextmanager
    async def fake_original_lifespan(app: object) -> AsyncIterator[None]:
        nonlocal original_enter_count
        original_enter_count += 1
        events.append("original_enter")
        yield
        events.append("original_exit")

    monkeypatch.setattr(server_mod, "_lifespan", fake_core_lifespan)
    composed = server_mod.compose_daemon_lifespan(fake_original_lifespan)

    async def _scenario() -> None:
        async with composed(object()):
            events.append("body")

    anyio.run(_scenario)

    assert events == ["core_enter", "original_enter", "body", "original_exit", "core_exit"]
    assert core_enter_count == 1
    assert original_enter_count == 1


def test_compose_daemon_lifespan_propagates_body_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception raised in the request-handling body must still unwind
    both nested context managers (neither lifespan silently swallows it)."""
    events: list[str] = []

    @contextlib.asynccontextmanager
    async def fake_core_lifespan(app: object) -> AsyncIterator[None]:
        events.append("core_enter")
        try:
            yield
        finally:
            events.append("core_exit")

    @contextlib.asynccontextmanager
    async def fake_original_lifespan(app: object) -> AsyncIterator[None]:
        events.append("original_enter")
        try:
            yield
        finally:
            events.append("original_exit")

    monkeypatch.setattr(server_mod, "_lifespan", fake_core_lifespan)
    composed = server_mod.compose_daemon_lifespan(fake_original_lifespan)

    async def _scenario() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            async with composed(object()):
                raise RuntimeError("boom")

    anyio.run(_scenario)

    assert events == ["core_enter", "original_enter", "original_exit", "core_exit"]
