"""Typed process configuration, loaded from environment variables and `.env`.

`Settings` is the only place in the codebase that reads `PIPELINE_BACKEND` from the environment —
see CLAUDE.md §7 ("No raw os.environ reads scattered through the code").
"""

from __future__ import annotations

from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class Backend(StrEnum):
    """Runtime backend selected via `PIPELINE_BACKEND`, resolved by `backends/__init__.py`."""

    LOCAL = "local"
    FREE = "free"
    AWS = "aws"


class Settings(BaseSettings):
    """Process-wide configuration, populated from environment variables and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    pipeline_backend: Backend = Backend.FREE
    # HTTP header values must be ASCII-encodable (httpx raises otherwise), so this stays plain
    # ASCII rather than using an em dash or other non-ASCII punctuation.
    user_agent: str = "scraping-pipeline/0.1 (portfolio project, see repository README)"
