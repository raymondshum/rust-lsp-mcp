"""KI-5 regression: non-ASCII (astral) positions must be codepoint-correct.

LSP positions default to UTF-16 code units, and rust-analyzer — as negotiated by
multilspy today (``positionEncodings: ["utf-16"]``) — returns UTF-16 offsets. On
a line containing an astral-plane character (one Unicode codepoint, two UTF-16
code units) the reported ``character`` is therefore off by the surrogate count.
All-ASCII fixtures (like ripgrep) cannot expose this; the unicode fixture crate
under ``tests/fixtures/unicode_crate`` does.

The verified fix is to negotiate ``positionEncoding`` ``utf-32`` (empirically
supported by rust-analyzer — see ``docs/reference/lsp-position-encoding.md``),
which makes ``character`` a Unicode codepoint offset with no transcoding.

This test is ``xfail(strict)`` until KI-5 is fixed: it fails today (the reported
position lands mid-identifier), and when the fix lands it will pass — at which
point strict xfail fails the suite, signalling that this marker should be
removed and the test kept as a live regression.

Marker: ``integration`` (live rust-analyzer; never runs in CI).
"""

import pathlib
from typing import Any
from unittest.mock import patch

import anyio
import pytest

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import STATE_READY, AnalyzerManager
from rust_lsp_mcp.envelope import STATUS_OK
from rust_lsp_mcp.settings import get_settings
from rust_lsp_mcp.tools.find_symbol import find_symbol

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "unicode_crate"
_LIB = _FIXTURE / "src" / "lib.rs"
_SYMBOL = "target_after_emoji"


async def _find_symbol_in_fixture() -> dict[str, Any]:
    settings = get_settings()
    manager = AnalyzerManager(
        rust_analyzer_bin=settings.rust_analyzer_bin,
        repository_root=str(_FIXTURE),
    )
    await manager.start()
    try:
        with anyio.fail_after(180):
            await manager._ready_event.wait()
        assert manager.state == STATE_READY
        with patch.object(core, "_manager", manager):
            return await find_symbol(_SYMBOL)
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.xfail(
    strict=True,
    reason="KI-5: positions are UTF-16 code units, not codepoints, on non-ASCII "
    "lines. Fix = negotiate positionEncoding utf-32, then remove this marker.",
)
def test_find_symbol_position_is_codepoint_offset_on_astral_line() -> None:
    result = anyio.run(_find_symbol_in_fixture)

    assert result["status"] == STATUS_OK, f"find_symbol failed: {result!r}"
    candidates = [
        c for c in result["results"] if c["name"] == _SYMBOL and c["file"].endswith("lib.rs")
    ]
    assert candidates, f"{_SYMBOL!r} not found among {result['results']!r}"
    cand = candidates[0]

    line_text = _LIB.read_text(encoding="utf-8").splitlines()[cand["line"] - 1]
    # The reported 1-indexed ``character``, read as a Unicode codepoint offset,
    # must land exactly on the identifier. Under UTF-16 it is shifted right by
    # the astral character's surrogate count, so the slice starts mid-identifier.
    sliced = line_text[cand["character"] - 1 :]
    assert sliced.startswith(_SYMBOL), (
        f"position not codepoint-correct: character={cand['character']} on line "
        f"{cand['line']} yields {sliced[:24]!r} (expected to start with {_SYMBOL!r})"
    )
