"""Fast-tier static-analysis regression tests for DS-15 (#59) and DS-16 (#60).

No live git, no container, no network — these tests read scripts/setup.sh and
Dockerfile as text and assert on their contents.  Mirrors the style of
tests/test_env_sample_honesty.py.  Runs in CI as part of
``pytest -m "not integration"``.

DS-15 (#59): scripts/setup.sh used to run
``git config --global commit.gpgsign false`` unconditionally, which silently
disables commit signing for EVERY repo on a developer's HOST if setup.sh is
ever re-run outside a container (teardown.sh's own final line tells the user
to do exactly that). The fix guards the gpgsign-disable behind a
container-only check.

DS-16 (#60): the production image runs as root with /project as a
host-uid-owned bind mount. Since git >=2.35.2, `git -C /project rev-parse
HEAD` fails with a "detected dubious ownership" error unless
`safe.directory` is configured, which permanently nulls out
indexed_commit/current_commit/stale in status/analyzer. The fix adds a
`git config --system --add safe.directory /project` line to the Dockerfile.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _setup_sh_text() -> str:
    return (REPO_ROOT / "scripts" / "setup.sh").read_text()


def _dockerfile_text() -> str:
    return (REPO_ROOT / "Dockerfile").read_text()


# --- DS-15 -------------------------------------------------------------


def test_setup_sh_has_container_guard_marker() -> None:
    """setup.sh must contain a container-detection marker.

    Accepts any of the common container markers so the check isn't brittle
    to the exact implementation, as long as SOME container-detection
    mechanism is present in the file.
    """
    text = _setup_sh_text()
    markers = ("_in_container", "/.dockerenv", "/run/.containerenv")
    assert any(marker in text for marker in markers), (
        "scripts/setup.sh has no container-detection marker "
        f"(expected one of {markers}). "
        "The git commit.gpgsign disable must be guarded to run only inside "
        "a container — see DS-15 (#59)."
    )


def test_setup_sh_gpgsign_disable_is_guarded_not_unconditional() -> None:
    """The gpgsign-disable line must be inside a container guard, not top-level.

    Concretely: every line containing "commit.gpgsign false" must have a
    guard token (_in_container / /.dockerenv / /run/.containerenv) appearing
    somewhere BEFORE it in the file. This fails against the original
    unconditional ``git config --global commit.gpgsign false`` on line 34,
    which had no preceding guard token anywhere in the file.
    """
    text = _setup_sh_text()
    lines = text.splitlines()

    guard_tokens = ("_in_container", "/.dockerenv", "/run/.containerenv")

    gpgsign_line_indices = [i for i, line in enumerate(lines) if "commit.gpgsign false" in line]
    assert gpgsign_line_indices, (
        "scripts/setup.sh no longer disables commit.gpgsign at all — "
        "expected the guarded disable to still be present (DS-15 / #59)."
    )

    for idx in gpgsign_line_indices:
        preceding_text = "\n".join(lines[:idx])
        assert any(token in preceding_text for token in guard_tokens), (
            f"scripts/setup.sh line {idx + 1} disables commit.gpgsign "
            "without a preceding container guard — this would run "
            "unconditionally, including on the HOST. See DS-15 (#59)."
        )


def test_setup_sh_still_parses() -> None:
    """Sanity check: the guard must not break bash syntax (paired with `bash -n` in CI)."""
    text = _setup_sh_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text


# --- DS-16 -------------------------------------------------------------


def test_dockerfile_configures_safe_directory_for_project() -> None:
    """Dockerfile must configure git safe.directory for /project.

    The production image runs as root with /project as a host-uid-owned
    bind mount; without this, git >=2.35.2 refuses to operate on /project
    ("detected dubious ownership"), permanently nulling out
    indexed_commit/current_commit/stale. See DS-16 (#60).
    """
    text = _dockerfile_text()
    assert "git config --system" in text, (
        "Dockerfile does not configure git --system config — expected a "
        "`git config --system --add safe.directory /project` line (DS-16 / #60)."
    )
    assert "safe.directory" in text, (
        "Dockerfile does not mention safe.directory — the rootful bind-mount "
        "dubious-ownership fix is missing (DS-16 / #60)."
    )
    assert "/project" in text.split("safe.directory", 1)[-1].splitlines()[0], (
        "Dockerfile's safe.directory configuration does not target /project "
        "(the RLM_PROJECT_ROOT bind-mount target) — see DS-16 (#60)."
    )
