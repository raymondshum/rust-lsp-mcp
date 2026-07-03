"""DS-28 — assert tools are actually registered on the FastMCP app.

Tools self-register by import side effect: ``rust_lsp_mcp.tools`` iterates its
submodules via ``pkgutil.iter_modules`` (skipping any module whose name starts
with ``_``) and imports each one, and each tool module decorates its function
with ``@mcp.tool()`` at import time (see
``src/rust_lsp_mcp/tools/__init__.py``).  No test previously called
``mcp.list_tools()`` — every other test imports tool functions directly and
calls them in-process, so a discovery regression (e.g. an accidental leading
underscore on a module name, a change to the ``pkgutil.iter_modules`` walk, or
a tool file moved out of the ``tools`` package) would silently shrink the
registered tool set while the rest of the suite stayed green.

Branch-safety note: this repo's ``bob_prototype`` branch cherry-picks fixes
from ``main`` but does not carry every tool ``main`` has (e.g.
``validate_file_path`` exists on ``main``, not on ``bob_prototype``).  This
test therefore asserts a stable CORE SET is a *subset* of the registered
names (not exact equality, and not membership of any branch-specific tool),
so it passes on both branches.
"""

import asyncio

from mcp.types import Tool

# Triggers rust_lsp_mcp.tools._register_all(), which imports every
# tools/<name>.py submodule and registers its @mcp.tool()-decorated function.
import rust_lsp_mcp.tools  # noqa: F401
from rust_lsp_mcp.core import mcp

# Tool names expected to exist on every branch this test runs on. Deliberately
# NOT the full/exact set — e.g. `validate_file_path` exists on `main` but not
# on `bob_prototype`, and this test file is cherry-picked across both.
_CORE_TOOL_NAMES = frozenset(
    {
        "goto_definition",
        "hover",
        "find_references",
        "document_symbols",
        "find_symbol",
        "search_docs",
        "refresh",
        "status",
        "analyzer_status",
        "probe",
    }
)


def _list_tool_names() -> set[str]:
    async def _scenario() -> list[str]:
        tools = await mcp.list_tools()
        return [tool.name for tool in tools]

    return set(asyncio.run(asyncio.wait_for(_scenario(), timeout=5)))


def test_core_tools_are_registered() -> None:
    """The stable core tool set must be a subset of what's actually registered.

    A subset check (not exact equality) so this test is safe across branches
    that carry a different additional tool set (e.g. bob_prototype vs main).
    """
    registered = _list_tool_names()
    missing = _CORE_TOOL_NAMES - registered
    assert not missing, (
        f"Expected core tools missing from mcp.list_tools(): {sorted(missing)!r}. "
        f"Registered: {sorted(registered)!r}. This usually means "
        "rust_lsp_mcp.tools._register_all() failed to discover a module "
        "(check for a stray leading underscore or a module moved out of the "
        "tools package)."
    )


def test_registered_tool_count_meets_core_floor() -> None:
    """Sanity floor: at least as many tools are registered as are in the core set."""
    registered = _list_tool_names()
    assert len(registered) >= len(_CORE_TOOL_NAMES), (
        f"Registered tool count ({len(registered)}) is below the core-set floor "
        f"({len(_CORE_TOOL_NAMES)}) — registered: {sorted(registered)!r}."
    )


def test_no_registered_tool_name_is_private() -> None:
    """No registered tool name may start with `_` — private helpers must not
    be exposed on the MCP surface. Branch-safe: doesn't depend on the exact
    tool set, only on the underscore-prefix discovery convention documented
    in tools/__init__.py.
    """
    registered = _list_tool_names()
    private_leaks = {name for name in registered if name.startswith("_")}
    assert not private_leaks, (
        f"Private-looking tool names leaked onto the MCP app: {private_leaks!r}"
    )


# ---------------------------------------------------------------------------
# CC-1 — ToolAnnotations (readOnlyHint / destructiveHint) on registered tools.
# ---------------------------------------------------------------------------

# Nav/read-only tools that must carry readOnlyHint=True. A subset of
# _CORE_TOOL_NAMES minus `refresh` (the one mutating tool) — deliberately not
# `validate_file_path` (branch-specific, per the module docstring above).
_READ_ONLY_CORE_TOOL_NAMES = _CORE_TOOL_NAMES - {"refresh"}


def _list_tools_by_name() -> dict[str, Tool]:
    async def _scenario() -> list[Tool]:
        return await mcp.list_tools()

    tools = asyncio.run(asyncio.wait_for(_scenario(), timeout=5))
    return {tool.name: tool for tool in tools}


def test_core_read_only_tools_have_read_only_hint() -> None:
    """Every core read-only tool must advertise annotations.readOnlyHint=True.

    CC-1 (2026-07-02 usability review): clients need a machine-readable signal
    that these tools never mutate server state, without parsing descriptions.
    """
    by_name = _list_tools_by_name()
    for name in sorted(_READ_ONLY_CORE_TOOL_NAMES):
        tool = by_name[name]
        assert tool.annotations is not None, f"{name}: no annotations at all"
        assert tool.annotations.readOnlyHint is True, (
            f"{name}: expected annotations.readOnlyHint=True, got {tool.annotations!r}"
        )


def test_refresh_has_destructive_hint() -> None:
    """`refresh` is the one mutating/destructive tool — its annotations must say so.

    CC-1 (2026-07-02 usability review): refresh tears down and re-indexes the
    global analyzer, so it must be flagged readOnlyHint=False,
    destructiveHint=True, idempotentHint=False (repeated calls each restart a
    fresh in-flight re-index — not a no-op after the first call).
    """
    by_name = _list_tools_by_name()
    tool = by_name["refresh"]
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True
    assert tool.annotations.idempotentHint is False
