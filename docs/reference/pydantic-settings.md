# pydantic-settings — settings layer (defaults + .env + env vars)

**Library:** `pydantic-settings` (current; Context7 `/pydantic/pydantic-settings`).
**Date:** 2026-06-19. **Source:** Context7.

## Question
Confirm the current API for code-defaults + `.env` + env-var overrides and the
precedence order (Phase 0.5).

## Answer (confirmed)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    ripgrep_src: str = "/workspaces/.../cache/ripgrep"   # defaults live in code
    chroma_path: str = "/workspaces/.../cache/chroma"
    model_config = SettingsConfigDict(env_prefix="RLM_", env_file=".env")
```

- `model_config = SettingsConfigDict(...)` is the current config mechanism (Pydantic
  v2 style). Useful keys: `env_prefix`, `env_file` (str or list), `secrets_dir`.
- **Default precedence (highest → lowest):** `init args` → **env vars** → **`.env`
  (dotenv)** → secrets dir. **This matches our plan exactly** (code defaults < `.env`
  < real env vars), with no customization needed.
- Override order only if needed via `settings_customise_sources(...)`.
- The server loads `.env` itself by setting `env_file` — no external loader needed.

## To re-verify at build (UNVERIFIED specifics)
- Confirm `uv add pydantic-settings` resolves a 2.x release and `SettingsConfigDict`
  is unchanged.
- Decide `env_prefix` value; ensure every read field has an `env.sample` entry
  (the CI honesty check, Phase 0.10).
