# multilspy — readiness / indexing-complete detection (rust backend)

**Library:** `multilspy` **0.0.15** (current on PyPI as of check). **Date:** 2026-06-19.
**Source:** Context7 (`/microsoft/multilspy`) — docs are silent on this; answer found by
inspecting the package source (`pip download multilspy --no-deps --no-binary :all:`),
file `src/multilspy/language_servers/rust_analyzer/rust_analyzer.py` +
`initialize_params.json`.

## Question asked

How does multilspy know rust-analyzer has finished indexing, so we never query during
the indexing window and get a misleading empty answer?

## Answer (as-built in 0.0.15)

multilspy's `RustAnalyzer.start_server()` **blocks until rust-analyzer is quiescent**,
using rust-analyzer's `experimental/serverStatus` notification:

- `initialize_params.json` advertises the client capability
  `experimental.serverStatusNotification: true` → rust-analyzer agrees to send
  `experimental/serverStatus` notifications.
- Handler wired at startup:
  ```python
  async def check_experimental_status(params):
      if params["quiescent"] == True:
          self.server_ready.set()
  self.server.on_notification("experimental/serverStatus", check_experimental_status)
  ```
- `start_server()` sequence: start process → `initialize` → `initialized` →
  `await self.server_ready.wait()` → **then** `yield self`. So once you are inside
  `async with lsp.start_server():`, indexing is settled.

## Important consequences for our design

- **Detection is solved by the library**, using the exact signal we'd have chosen
  (`serverStatus` / `quiescent`). The §9 "can multilspy surface readiness" audit risk
  is largely closed for the happy path.
- **No progress percentage.** `$/progress` is explicitly swallowed (`do_nothing`), so
  multilspy exposes only the final "done" bit, not "60% indexed". A `status` tool can
  say "indexing / ready" but not a percentage without us adding our own `$/progress`
  handler.
- **Startup blocks.** Because `start_server()` blocks until quiescent (cold index of
  ripgrep may take a while), our MCP server must run multilspy's context in a
  background task and keep its own readiness flag — returning an explicit
  "indexing, not ready" for tool calls that arrive before the context is live, rather
  than blocking the MCP request or returning empty.
- **`quiescent` can flip false on re-index**, but multilspy's handler only ever *sets*
  `server_ready` (never clears on `quiescent: false`). Our refresh = teardown + fresh
  `start_server`, which blocks-until-quiescent again — so we get correct gating across
  refreshes for free; we do not rely on multilspy re-clearing readiness mid-session.

## To re-verify at build (UNVERIFIED specifics)

- Confirm `uv add multilspy` still resolves 0.0.15 (or note the new version + re-read
  `rust_analyzer.py`).
- Confirm whether we hold `start_server()` open for the whole server lifetime via a
  background task vs. a sync wrapper, and how that interacts with the MCP server's
  event loop.
