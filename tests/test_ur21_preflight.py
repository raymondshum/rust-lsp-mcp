"""Regression tests for UR-21 (narrowed) — non-fatal advisory startup preflight.

The audit's round-2 revision (docs/audit/2026-07-02-usability-review.md,
UR-21) narrows the original proposal to exactly two ADVISORY (never fatal,
never ``STATE_ERROR``) checks, computed ONCE at real server startup:

    (a) the configured ``rust_analyzer_bin`` resolves to an executable file —
        ``shutil.which`` handles both a bare command name (PATH lookup) and
        an explicit/relative path (checked directly) with one call.
    (b) the configured ``project_root`` exists and is a directory.

No ``Cargo.toml`` check (rejected — ``project_root`` is documented
repo-agnostic; a Cargo.toml-at-root gate would misfire on valid
rust-project.json / subdirectory-Cargo.toml configurations).

Deliberately NOT wired into ``AnalyzerManager.start()``/``_run`` — those are
exactly the seam the fast/race suites (``test_phase1_fast.py``,
``test_lifecycle_races.py``, ``test_analyzer_error_state.py``) drive directly
with fake paths (``/fake/repo``, ``/nonexistent``, ...) and a mocked LSP; a
preflight wired in there would fire on every one of those constructions. It
instead lives in ``core._compute_preflight_warnings`` / ``core._lifespan``,
which only runs for the real FastMCP lifespan (production) and
``test_lifespan_startup.py``'s direct ``core._lifespan`` exercises.

No live analyzer, no network. Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    1. ``_compute_preflight_warnings`` is a pure function of ``Settings``:
       missing binary -> one warning; missing/non-dir project_root -> one
       warning; both broken -> two warnings; both fine -> ``[]``.
    2. ``get_preflight_warnings()`` defaults to ``[]`` when the lifespan has
       never run (the "startup never ran" / "all clear" cases are
       intentionally indistinguishable — documented on the holder).
    3. ``core._lifespan`` wires ``_compute_preflight_warnings(get_settings())``
       once at startup: ``get_preflight_warnings()`` reflects whatever the
       holder contains while the lifespan is active, and resets to ``[]`` on
       teardown.
    4. The preflight never touches ``AnalyzerManager`` state — a bad
       binary/root produces warnings only, never ``STATE_ERROR``, and the
       fake/mocked manager used inside the lifespan is unaffected.
"""

from __future__ import annotations

import asyncio
import pathlib
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import rust_lsp_mcp.core as core_mod
from rust_lsp_mcp.settings import Settings

# ---------------------------------------------------------------------------
# 1. _compute_preflight_warnings — pure function of Settings.
# ---------------------------------------------------------------------------


class TestComputePreflightWarnings:
    def test_both_fine_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        # A real executable that exists on PATH in any POSIX container image
        # would be environment-dependent; use an explicit path to a file we
        # create and chmod +x ourselves so the test is hermetic.
        fake_bin = tmp_path / "rust-analyzer"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        project_root = tmp_path / "project"
        project_root.mkdir()

        settings = Settings(
            rust_analyzer_bin=str(fake_bin),
            project_root=str(project_root),
        )

        assert core_mod._compute_preflight_warnings(settings) == []

    def test_missing_binary_produces_one_warning(self, tmp_path: pathlib.Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        settings = Settings(
            rust_analyzer_bin=str(tmp_path / "does-not-exist" / "rust-analyzer"),
            project_root=str(project_root),
        )

        warnings = core_mod._compute_preflight_warnings(settings)
        assert len(warnings) == 1
        assert "rust-analyzer" in warnings[0]

    def test_bare_binary_name_not_on_path_produces_warning(self, tmp_path: pathlib.Path) -> None:
        """A bare command name (no path separators) is looked up on PATH, not
        checked as a literal relative path — shutil.which handles this."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        settings = Settings(
            rust_analyzer_bin="definitely-not-a-real-binary-name-xyz",
            project_root=str(project_root),
        )

        with patch.object(core_mod.shutil, "which", return_value=None) as which_mock:
            warnings = core_mod._compute_preflight_warnings(settings)
        which_mock.assert_called_once_with("definitely-not-a-real-binary-name-xyz")
        assert len(warnings) == 1

    def test_missing_project_root_produces_one_warning(self, tmp_path: pathlib.Path) -> None:
        fake_bin = tmp_path / "rust-analyzer"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)

        settings = Settings(
            rust_analyzer_bin=str(fake_bin),
            project_root=str(tmp_path / "no-such-project-root"),
        )

        warnings = core_mod._compute_preflight_warnings(settings)
        assert len(warnings) == 1
        assert "project_root" in warnings[0] or "does not exist" in warnings[0]

    def test_project_root_is_a_file_not_a_directory_produces_warning(
        self, tmp_path: pathlib.Path
    ) -> None:
        fake_bin = tmp_path / "rust-analyzer"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        not_a_dir = tmp_path / "project_root_is_a_file"
        not_a_dir.write_text("oops")

        settings = Settings(rust_analyzer_bin=str(fake_bin), project_root=str(not_a_dir))

        warnings = core_mod._compute_preflight_warnings(settings)
        assert len(warnings) == 1

    def test_both_broken_produces_two_warnings(self, tmp_path: pathlib.Path) -> None:
        settings = Settings(
            rust_analyzer_bin=str(tmp_path / "no-such-binary"),
            project_root=str(tmp_path / "no-such-root"),
        )

        warnings = core_mod._compute_preflight_warnings(settings)
        assert len(warnings) == 2

    def test_never_raises_and_never_touches_analyzer_state(self, tmp_path: pathlib.Path) -> None:
        """Purely advisory: computing warnings must not raise or require a manager."""
        settings = Settings(
            rust_analyzer_bin=str(tmp_path / "no-such-binary"),
            project_root=str(tmp_path / "no-such-root"),
        )
        # Must not raise.
        core_mod._compute_preflight_warnings(settings)


# ---------------------------------------------------------------------------
# 2. get_preflight_warnings() default (no lifespan run).
# ---------------------------------------------------------------------------


class TestGetPreflightWarningsDefault:
    def test_defaults_to_empty_list(self) -> None:
        # No lifespan has run in this test's process lifetime (or a previous
        # lifespan's teardown already reset it) — either way the holder reads
        # as "all clear", which is the documented, intentional overlap with
        # "preflight never ran".
        assert core_mod.get_preflight_warnings() == []

    def test_returns_a_copy_not_the_live_list(self) -> None:
        result = core_mod.get_preflight_warnings()
        result.append("mutate me")
        assert core_mod.get_preflight_warnings() == []


# ---------------------------------------------------------------------------
# 3 & 4. core._lifespan wiring — computed once, reset on teardown, advisory only.
# ---------------------------------------------------------------------------


class _FakeManager:
    """Minimal stand-in for AnalyzerManager — mirrors test_lifespan_startup.py."""

    def __init__(self) -> None:
        self.state = "ready"
        self.error_message: str | None = None
        self.indexed_commit: str | None = "deadbeef"
        self.repository_root = "/tmp"


def _fake_analyzer_lifespan_factory(manager: _FakeManager) -> Any:
    @asynccontextmanager
    async def _fake_analyzer_lifespan(app: object) -> Any:
        yield {"manager": manager}

    return _fake_analyzer_lifespan


class TestLifespanWiresPreflightOnce:
    def test_warnings_visible_during_lifespan_and_reset_after(self, tmp_path: pathlib.Path) -> None:
        bad_settings = Settings(
            rust_analyzer_bin=str(tmp_path / "no-such-binary"),
            project_root=str(tmp_path / "no-such-root"),
        )

        async def _scenario() -> None:
            fake_manager = _FakeManager()
            with (
                patch.object(core_mod, "get_settings", return_value=bad_settings),
                patch.object(
                    core_mod, "analyzer_lifespan", _fake_analyzer_lifespan_factory(fake_manager)
                ),
                patch.object(core_mod, "init_doc_store_background", AsyncMock()),
            ):
                async with core_mod._lifespan(object()) as ctx:  # ty: ignore[invalid-argument-type]
                    assert ctx["manager"] is fake_manager
                    warnings = core_mod.get_preflight_warnings()
                    assert len(warnings) == 2

                    # Advisory only: the fake manager is untouched — state
                    # stays "ready", never flipped to an error state by the
                    # preflight finding a bad binary/root.
                    assert fake_manager.state == "ready"

            # Teardown resets the holder — no leakage into the next test.
            assert core_mod.get_preflight_warnings() == []

        asyncio.run(asyncio.wait_for(_scenario(), timeout=5))

    def test_all_clear_yields_empty_list_during_lifespan(self, tmp_path: pathlib.Path) -> None:
        fake_bin = tmp_path / "rust-analyzer"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        project_root = tmp_path / "project"
        project_root.mkdir()
        good_settings = Settings(rust_analyzer_bin=str(fake_bin), project_root=str(project_root))

        async def _scenario() -> None:
            fake_manager = _FakeManager()
            with (
                patch.object(core_mod, "get_settings", return_value=good_settings),
                patch.object(
                    core_mod, "analyzer_lifespan", _fake_analyzer_lifespan_factory(fake_manager)
                ),
                patch.object(core_mod, "init_doc_store_background", AsyncMock()),
            ):
                async with core_mod._lifespan(object()) as ctx:  # ty: ignore[invalid-argument-type]
                    assert ctx["manager"] is fake_manager
                    assert core_mod.get_preflight_warnings() == []

        asyncio.run(asyncio.wait_for(_scenario(), timeout=5))
