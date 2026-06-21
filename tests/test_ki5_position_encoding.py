"""KI-5 regression: non-ASCII (astral) positions are codepoint-correct.

LSP positions default to UTF-16 code units. multilspy's bundled rust-analyzer
init params advertise ``positionEncodings: ["utf-16"]``, so on a line with an
astral-plane character (one Unicode codepoint, two UTF-16 code units) the
reported ``character`` was off by the surrogate count. All-ASCII fixtures (like
ripgrep) cannot expose this; the unicode fixture crate under
``tests/fixtures/unicode_crate`` does.

The fix: ``PatchedRustAnalyzer._get_initialize_params`` advertises
``["utf-32", "utf-16"]`` so rust-analyzer reports **Unicode codepoint** offsets
(verified supported — see ``docs/reference/lsp-position-encoding.md``). No
per-line transcoding is needed; ``positions.py`` stays pure ±1.

Coverage:
    * unit (CI) — the negotiated encoding list is exactly ``["utf-32","utf-16"]``
      and the override fails loudly if multilspy ever restructures its params.
    * integration (local; live rust-analyzer) — output side (``find_symbol``) and
      input side (``goto_definition`` / ``find_references`` at a codepoint column)
      both land codepoint-correct on an astral line.
"""

import pathlib
from typing import Any
from unittest.mock import patch

import anyio
import pytest

# multilspy: constructing the analyzer for the unit tests does NOT launch a
# process or require the binary to exist (see RustAnalyzer.__init__).
from multilspy.language_servers.rust_analyzer.rust_analyzer import RustAnalyzer
from multilspy.multilspy_config import Language, MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

import rust_lsp_mcp.core as core
from rust_lsp_mcp.analyzer import STATE_READY, AnalyzerManager, PatchedRustAnalyzer
from rust_lsp_mcp.envelope import STATUS_OK
from rust_lsp_mcp.settings import get_settings
from rust_lsp_mcp.tools.find_references import find_references
from rust_lsp_mcp.tools.find_symbol import find_symbol
from rust_lsp_mcp.tools.goto_definition import goto_definition

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "unicode_crate"
_LIB = _FIXTURE / "src" / "lib.rs"
_SYMBOL = "target_after_emoji"
_TIMEOUT = 180  # seconds to wait for rust-analyzer to go quiescent


def _make_patched_analyzer() -> PatchedRustAnalyzer:
    settings = get_settings()
    return PatchedRustAnalyzer(
        config=MultilspyConfig(code_language=Language.RUST),
        logger=MultilspyLogger(),
        repository_root_path=str(_FIXTURE),
        rust_analyzer_bin=settings.rust_analyzer_bin,
    )


def _line_text(line_1indexed: int) -> str:
    return _LIB.read_text(encoding="utf-8").splitlines()[line_1indexed - 1]


def _codepoint_col(line_1indexed: int, needle: str) -> int:
    """1-indexed Unicode-codepoint column where *needle* starts on the line."""
    return _line_text(line_1indexed).index(needle) + 1


async def _with_manager(coro_fn: Any) -> Any:
    """Start the analyzer on the unicode fixture, run coro_fn(manager), shut down."""
    settings = get_settings()
    manager = AnalyzerManager(
        rust_analyzer_bin=settings.rust_analyzer_bin,
        repository_root=str(_FIXTURE),
    )
    await manager.start()
    try:
        with anyio.fail_after(_TIMEOUT):
            await manager._ready_event.wait()
        assert manager.state == STATE_READY
        return await coro_fn(manager)
    finally:
        await manager.shutdown()


# ---------------------------------------------------------------------------
# Fast unit tests (CI) — the fix point itself
# ---------------------------------------------------------------------------


def test_initialize_params_advertises_utf32() -> None:
    """The override requests utf-32 first (codepoints), utf-16 as fallback."""
    params = _make_patched_analyzer()._get_initialize_params(str(_FIXTURE))
    assert params["capabilities"]["general"]["positionEncodings"] == ["utf-32", "utf-16"]


def test_initialize_params_fails_loudly_if_general_key_missing() -> None:
    """If multilspy ever drops capabilities.general, the override must KeyError
    loudly (a silent no-op would revert to UTF-16 positions undetectably)."""
    analyzer = _make_patched_analyzer()
    stripped: dict[str, Any] = {"capabilities": {}}  # no "general"
    with (
        patch.object(RustAnalyzer, "_get_initialize_params", return_value=stripped),
        pytest.raises(KeyError),
    ):
        analyzer._get_initialize_params(str(_FIXTURE))


# ---------------------------------------------------------------------------
# Integration (local) — end-to-end codepoint correctness on an astral line
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_find_symbol_position_is_codepoint_offset_on_astral_line() -> None:
    """Output side: find_symbol reports a codepoint-correct position.

    Reading the reported 1-indexed ``character`` as a codepoint offset must land
    exactly on the identifier; under UTF-16 it would be shifted right by the
    astral character's surrogate count (lands mid-identifier).
    """

    async def _run(manager: AnalyzerManager) -> dict[str, Any]:
        with patch.object(core, "_manager", manager):
            return await find_symbol(_SYMBOL)

    result = anyio.run(_with_manager, _run)
    assert result["status"] == STATUS_OK, f"find_symbol failed: {result!r}"
    cands = [c for c in result["results"] if c["name"] == _SYMBOL and c["file"].endswith("lib.rs")]
    assert cands, f"{_SYMBOL!r} not found among {result['results']!r}"
    cand = cands[0]

    sliced = _line_text(cand["line"])[cand["character"] - 1 :]
    assert sliced.startswith(_SYMBOL), (
        f"position not codepoint-correct: character={cand['character']} on line "
        f"{cand['line']} yields {sliced[:24]!r} (expected to start with {_SYMBOL!r})"
    )


@pytest.mark.integration
def test_input_side_positions_are_codepoint_correct_on_astral_line() -> None:
    """Input side: goto_definition / find_references accept a codepoint column.

    Both tools take a caller-supplied position. On the astral call line, the
    identifier's codepoint column differs from its UTF-16 column; supplying the
    codepoint column must resolve correctly and return codepoint-correct
    positions. One warm session exercises both tools.
    """
    # Definition: `pub fn target_after_emoji` on its line; call: `target_after_emoji()`.
    def_line = next(
        i for i, t in enumerate(_LIB.read_text("utf-8").splitlines(), 1) if "pub fn " + _SYMBOL in t
    )
    call_line = next(
        i
        for i, t in enumerate(_LIB.read_text("utf-8").splitlines(), 1)
        if "let _ = " + _SYMBOL in t
    )

    async def _run(manager: AnalyzerManager) -> dict[str, Any]:
        with patch.object(core, "_manager", manager):
            gd = await goto_definition(
                file="src/lib.rs", line=call_line, character=_codepoint_col(call_line, _SYMBOL)
            )
            fr = await find_references(
                file="src/lib.rs",
                line=def_line,
                character=_codepoint_col(def_line, _SYMBOL),
                include_declaration=False,
            )
            return {"goto": gd, "refs": fr}

    out = anyio.run(_with_manager, _run)

    # goto_definition: codepoint input column resolves, and the definition it
    # returns is itself codepoint-correct (lands on the identifier).
    gd = out["goto"]
    assert gd["status"] == STATUS_OK, f"goto_definition failed at codepoint col: {gd!r}"
    assert gd["definitions"], "goto_definition ok but empty"
    d = gd["definitions"][0]
    assert _line_text(d["line"])[d["character"] - 1 :].startswith(_SYMBOL), (
        f"definition position not codepoint-correct: {d!r}"
    )

    # find_references: codepoint input column resolves the call on the astral
    # line, and the reference position is codepoint-correct.
    fr = out["refs"]
    assert fr["status"] == STATUS_OK, f"find_references failed at codepoint col: {fr!r}"
    on_call_line = [r for r in fr["references"] if r["line"] == call_line]
    assert on_call_line, f"no reference on the call line {call_line}; got {fr['references']!r}"
    ref = on_call_line[0]
    assert _line_text(ref["line"])[ref["character"] - 1 :].startswith(_SYMBOL), (
        f"reference position not codepoint-correct: {ref!r}"
    )
