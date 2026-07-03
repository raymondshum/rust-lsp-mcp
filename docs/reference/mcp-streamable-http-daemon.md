# mcp SDK — streamable-HTTP daemon with once-per-process state

**Library:** mcp 1.12.4 (official Python SDK) · **Verified:** 2026-07-02 (source
inspection of the installed package + live proof script) · For plan
[cli-frontend.md](../planning/cli-frontend.md) (D2/D3, U1/U2/U8).

## What was asked

How to run a FastMCP server over streamable-HTTP so that expensive state (the
`AnalyzerManager`, doc store) initializes **once per process**, given that
`FastMCP(lifespan=)` runs per MCP session — per **request** when
`stateless_http=True` (`lowlevel/server.py:575`; `streamable_http_manager.py:170-187`).

## The wiring (proven live)

Key facts:

- `streamable_http_app()` returns a Starlette app whose lifespan **is**
  `session_manager.run()` (`fastmcp/server.py:952-956`) — any replacement
  lifespan must delegate to it or sessions break.
- `mcp.run("streamable-http")` / `run_streamable_http_async()` build their own
  app + uvicorn config internally (`fastmcp/server.py:678-691`) — **no injection
  hook; you must own the uvicorn call.**

```python
# mcp 1.12.4 — warm HTTP daemon, once-per-process init
import contextlib, uvicorn
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("svc", host="127.0.0.1", port=PORT,
              stateless_http=True, json_response=True)   # NO lifespan= in HTTP mode

app = mcp.streamable_http_app()                 # lazily creates session manager
original_lifespan = app.router.lifespan_context # == lambda app: session_manager.run()

@contextlib.asynccontextmanager
async def process_lifespan(app):
    ...startup once per process (analyzer, doc store)...
    async with original_lifespan(app):          # MUST delegate: runs session_manager.run()
        yield
    ...shutdown once per process...

app.router.lifespan_context = process_lifespan
uvicorn.run(app, host="127.0.0.1", port=PORT)   # we OWN the uvicorn call
```

Live proof: two separate `streamablehttp_client` connections (initialize →
`call_tool` each) saw `init_count == 1` and the same shared-object id.

## Related facts (same pass)

- **stdio unaffected:** the stdio path keeps `FastMCP(lifespan=...)` as today
  (`MCPServer.run()` enters it once for the single stdio session). The
  transport switch selects the wiring; HTTP mode passes no `lifespan=`.
- **Stateless mode doesn't leak:** `_handle_stateless_request` creates a fresh
  transport per request, never writes `self._server_instances` (only the
  stateful path at `streamable_http_manager.py:232` does), and calls
  `terminate()` after each request. Verified live: instance dict empty after
  repeated one-shot calls.
- **Host header (DNS-rebinding) accepted by default:** with
  `transport_security=None` the middleware defaults to
  `enable_dns_rebinding_protection=False` and skips Host/Origin validation
  entirely (`transport_security.py:40-43,114-115`). Verified live: any `Host:`
  accepted. Fine for a loopback-only daemon; to harden, pass
  `TransportSecuritySettings(enable_dns_rebinding_protection=True,
  allowed_hosts=["127.0.0.1:*"])`. POST `Content-Type: application/json` is
  required regardless (validated even with protection off).
- **`stateless_http`/`json_response`** are FastMCP constructor kwargs →
  Settings fields (`fastmcp/server.py:83-85,139-140`), forwarded to the session
  manager at `:858-864`. `json_response=True` returns a single JSON body, not
  an SSE stream.
- **Benign log noise:** in stateless mode each request logs a
  `ClosedResourceError` "Error in message router" traceback
  (`streamable_http.py:880`, transport `terminate()` racing the message
  router). Harmless in 1.12.4 — requests still return 200/correct results.
  Expect it in daemon logs.
