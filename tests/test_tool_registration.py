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
