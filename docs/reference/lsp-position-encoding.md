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
- **Chosen fix (Approach A):** negotiate `positionEncodings: ["utf-32","utf-16"]`.
  rust-analyzer then reports `utf-32`, so every range it emits/accepts is a
  **Unicode codepoint** offset — exactly the intuitive "Nth character." No
  per-line transcoding is needed; `positions.py` stays pure ±1 arithmetic.
  (Approach B — keep utf-16 and transcode via the line text — was the fallback if
  utf-32 were unsupported; the probe shows it isn't needed.)
- **To confirm at implement time:** that the encoding is patched into multilspy's
  init params (the existing `PatchedRustAnalyzer` hooks the binary; the init
  params need a similar override), that rust-analyzer negotiates `utf-32` in the
  real multilspy path (read back `capabilities.positionEncoding`), and that
  multilspy itself does no internal UTF-16 position math. The regression test
  [tests/test_ki5_position_encoding.py](../../tests/test_ki5_position_encoding.py)
  (xfail → pass) proves the end-to-end result.
