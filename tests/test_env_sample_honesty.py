"""env.sample honesty check (fast tier — runs in CI).

Every settings variable that Settings reads must have a corresponding entry in
env.sample (with the correct RLM_ prefix).  This prevents a settings variable
being added without documenting it.

The check is duplicated in CI via scripts/check-env-sample.py; this test
makes it runnable in the ordinary pytest suite too.
"""

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent


def _settings_field_names() -> list[str]:
    """Return all field names declared on Settings (excluding model_config)."""
    from rust_lsp_mcp.settings import Settings

    return [
        name
        for name in Settings.model_fields
        if name != "model_config"
    ]


def _env_sample_keys() -> set[str]:
    """Return all RLM_* keys present in env.sample."""
    sample = REPO_ROOT / "env.sample"
    keys: set[str] = set()
    for line in sample.read_text().splitlines():
        line = line.strip()
        if line.startswith("RLM_") and "=" in line:
            keys.add(line.split("=", 1)[0])
    return keys


@pytest.mark.parametrize("field", _settings_field_names())
def test_env_sample_has_entry_for(field: str) -> None:
    """Every Settings field must appear in env.sample as RLM_<FIELD_UPPER>."""
    expected_key = f"RLM_{field.upper()}"
    sample_keys = _env_sample_keys()
    assert expected_key in sample_keys, (
        f"env.sample is missing an entry for Settings.{field} "
        f"(expected key: {expected_key}). "
        "Add it to env.sample before committing."
    )
