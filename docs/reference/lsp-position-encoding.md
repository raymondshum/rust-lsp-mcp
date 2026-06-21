# LSP position encoding & rust-analyzer support

**Stamp:** LSP 3.17 spec + rust-analyzer 1.96.0 · verified 2026-06-21 (Context7 +
empirical probe). Source for KI-5's fix decision.

## The mechanism (LSP 3.17)

- LSP positions are **column offsets within a line, measured in code units of a
  negotiated encoding** — UTF-16 by default.
- The client advertises an ordered preference list in
  `capabilities.general.positionEncodings`; the server picks one and echoes it in
  `initializeResult.capabilities.positionEncoding`. If the server omits it, the
  encoding is `utf-16`.
- The three predefined kinds:
  - `utf-8` — offsets = UTF-8 **bytes**.
  - `utf-16` — offsets = UTF-16 **code units** (legacy default; an astral-plane
    char costs **2** units).
  - `utf-32` — offsets = UTF-32 code units = **Unicode codepoints** (the spec's
    "encoding-agnostic" character count).
- `utf-16` is mandatory (every server must support it).

## What rust-analyzer actually supports (empirically verified)

Probe: raw LSP `initialize` handshake against `rust-analyzer 1.96.0` over a
minimal crate, reading back `capabilities.positionEncoding`:

| Client advertises | rust-analyzer returns |
|-------------------|-----------------------|
| `["utf-8","utf-16"]`  | `utf-8`  |
| `["utf-32","utf-16"]` | `utf-32` |
| `["utf-16"]` (control) | `utf-16` |

**rust-analyzer supports utf-8 AND utf-32.** Its `line-index` library natively
handles utf-8/utf-16/utf-32 offsets (Context7, rust-lang/rust-analyzer).

## Consequence for this project (KI-5)

- multilspy's bundled rust-analyzer `initialize_params.json` advertises
  `positionEncodings: ["utf-16"]` (`.venv/.../multilspy/language_servers/rust_analyzer/initialize_params.json`),
  so rust-analyzer emits/expects **UTF-16** offsets. On non-ASCII (astral) lines
  the `character` offset is off by the surrogate count — this is [KI-5](../impl/known-issues.md).
- **Implemented (Approach A):** `PatchedRustAnalyzer._get_initialize_params`
  advertises `positionEncodings: ["utf-32","utf-16"]`, so rust-analyzer reports
  `utf-32` — every range it emits/accepts is a **Unicode codepoint** offset, the
  intuitive "Nth character." No per-line transcoding; `positions.py` stays pure
  ±1. (Approach B — keep utf-16 and transcode via the line text — was the
  fallback if utf-32 were unsupported; the probe showed it isn't needed.)
  `utf-16` stays second purely as a protocol-level fallback for a server lacking
  utf-32 (it would silently revert to the old behaviour, never error).
- **Confirmed end-to-end** by [tests/test_ki5_position_encoding.py](../../tests/test_ki5_position_encoding.py):
  the negotiated list is exactly `["utf-32","utf-16"]`, and on an astral-emoji
  fixture both output (`find_symbol`) and input (`goto_definition`,
  `find_references`) positions are codepoint-correct. Adversarial review confirmed
  multilspy does no internal UTF-16 position math (positions pass through). One
  accepted residual: there is no runtime assertion that `utf-32` was negotiated —
  a future rust-analyzer that dropped utf-32 would silently revert; the
  integration tests are the guard.
