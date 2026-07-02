"""Fast-tier tests for the validate_file_path tool.

No live analyzer, no network, no git.  The workspace root is pointed at a
pytest ``tmp_path`` and ``get_settings`` / ``get_manager`` are patched so the
tool is fully hermetic.  Runs in CI as part of ``pytest -m "not integration"``.

Test coverage:
    - Existing regular file → exists=True, correct absolute_path, size_bytes==len.
    - Missing file → exists=False, size_bytes=None, but status still ok.
    - Existing directory → exists=True, size_bytes=None (not a regular file).
    - ".." escape attempt → error envelope.
    - Absolute path outside the workspace → error envelope.
    - Absolute path INSIDE the workspace → error envelope (containment rule is
      the same as the position tools': only workspace-relative paths are valid).
    - Empty string / NUL byte → error envelope (same rule).
    - "src/../src/main.rs" → accepted; probed in normalized form so the
      reported absolute_path has the ".." collapsed (symlink+".." hardening).
    - Unconfigured workspace root → error envelope.
"""

import pathlib
from typing import Any
from unittest.mock import patch

from rust_lsp_mcp.envelope import STATUS_ERROR, STATUS_OK
from rust_lsp_mcp.settings import Settings


def _call(repo_root: str, file: str) -> dict[str, Any]:
    """Invoke the tool with ``project_root`` pinned to ``repo_root`` and no manager."""
    import rust_lsp_mcp.tools.validate_file_path as mod

    fake_settings = Settings(project_root=repo_root)
    with (
        patch.object(mod, "get_settings", return_value=fake_settings),
        patch.object(mod, "get_manager", return_value=None),
    ):
        return mod.validate_file_path(file)


# ---------------------------------------------------------------------------
# Existing regular file
# ---------------------------------------------------------------------------


class TestExistingFile:
    _CONTENT = b"fn main() {}\n"

    def _result(self, tmp_path: pathlib.Path) -> tuple[dict[str, Any], pathlib.Path]:
        src = tmp_path / "src"
        src.mkdir()
        target = src / "main.rs"
        target.write_bytes(self._CONTENT)
        return _call(str(tmp_path), "src/main.rs"), target

    def test_status_ok(self, tmp_path: pathlib.Path) -> None:
        result, _ = self._result(tmp_path)
        assert result["status"] == STATUS_OK

    def test_exists_true(self, tmp_path: pathlib.Path) -> None:
        result, _ = self._result(tmp_path)
        assert result["exists"] is True

    def test_absolute_path(self, tmp_path: pathlib.Path) -> None:
        result, target = self._result(tmp_path)
        assert result["absolute_path"] == str(target)

    def test_size_bytes(self, tmp_path: pathlib.Path) -> None:
        result, _ = self._result(tmp_path)
        assert result["size_bytes"] == len(self._CONTENT)


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


class TestMissingFile:
    def _result(self, tmp_path: pathlib.Path) -> dict[str, Any]:
        return _call(str(tmp_path), "src/does_not_exist.rs")

    def test_status_ok(self, tmp_path: pathlib.Path) -> None:
        # A missing path is a valid answer, not an error.
        assert self._result(tmp_path)["status"] == STATUS_OK

    def test_exists_false(self, tmp_path: pathlib.Path) -> None:
        assert self._result(tmp_path)["exists"] is False

    def test_size_bytes_none(self, tmp_path: pathlib.Path) -> None:
        assert self._result(tmp_path)["size_bytes"] is None

    def test_absolute_path_reported(self, tmp_path: pathlib.Path) -> None:
        # The resolved path is reported even when it does not exist.
        assert self._result(tmp_path)["absolute_path"] == str(tmp_path / "src/does_not_exist.rs")


# ---------------------------------------------------------------------------
# Existing directory (exists but not a regular file)
# ---------------------------------------------------------------------------


class TestExistingDirectory:
    def _result(self, tmp_path: pathlib.Path) -> dict[str, Any]:
        (tmp_path / "src").mkdir()
        return _call(str(tmp_path), "src")

    def test_status_ok(self, tmp_path: pathlib.Path) -> None:
        assert self._result(tmp_path)["status"] == STATUS_OK

    def test_exists_true(self, tmp_path: pathlib.Path) -> None:
        assert self._result(tmp_path)["exists"] is True

    def test_size_bytes_none_for_directory(self, tmp_path: pathlib.Path) -> None:
        assert self._result(tmp_path)["size_bytes"] is None


# ---------------------------------------------------------------------------
# Escape attempts → error
# ---------------------------------------------------------------------------


class TestEscapeRejected:
    def test_dotdot_escape_is_error(self, tmp_path: pathlib.Path) -> None:
        result = _call(str(tmp_path), "../secret.txt")
        assert result["status"] == STATUS_ERROR

    def test_absolute_outside_is_error(self, tmp_path: pathlib.Path) -> None:
        # An absolute path pointing outside the workspace must be rejected.
        result = _call(str(tmp_path), "/etc/passwd")
        assert result["status"] == STATUS_ERROR

    def test_absolute_inside_workspace_is_error(self, tmp_path: pathlib.Path) -> None:
        # Containment rule alignment: the position tools reject ALL absolute
        # paths, so this tool must too — even one pointing inside the root.
        # Otherwise validate_file_path would say "ok" for an input that
        # hover/goto_definition would reject with "error".
        target = tmp_path / "main.rs"
        target.write_bytes(b"fn main() {}\n")
        result = _call(str(tmp_path), str(target))
        assert result["status"] == STATUS_ERROR

    def test_empty_file_is_error(self, tmp_path: pathlib.Path) -> None:
        # "" is not a valid workspace-relative file path (same rule as the
        # position tools) — previously it probed the root directory itself.
        result = _call(str(tmp_path), "")
        assert result["status"] == STATUS_ERROR

    def test_nul_byte_is_error(self, tmp_path: pathlib.Path) -> None:
        result = _call(str(tmp_path), "src/\x00/main.rs")
        assert result["status"] == STATUS_ERROR


# ---------------------------------------------------------------------------
# Normalized probing (symlink+".." hardening)
# ---------------------------------------------------------------------------


class TestNormalizedProbing:
    def test_dotdot_within_root_is_probed_normalized(self, tmp_path: pathlib.Path) -> None:
        """ "src/../src/main.rs" is accepted and probed with the ".." collapsed.

        Probing the normalized form means the OS never sees the ".." segment,
        so a symlink in the raw path's prefix cannot launder the probe outside
        the workspace (POSIX resolves symlinks before "..").
        """
        src = tmp_path / "src"
        src.mkdir()
        target = src / "main.rs"
        target.write_bytes(b"fn main() {}\n")

        result = _call(str(tmp_path), "src/../src/main.rs")

        assert result["status"] == STATUS_OK
        assert result["exists"] is True
        assert result["absolute_path"] == str(target)  # ".." collapsed


# ---------------------------------------------------------------------------
# Unconfigured workspace root → error
# ---------------------------------------------------------------------------


class TestUnconfiguredRoot:
    def test_empty_root_is_error(self) -> None:
        result = _call("", "src/main.rs")
        assert result["status"] == STATUS_ERROR
