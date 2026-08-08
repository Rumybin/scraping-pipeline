"""Loader for `sites.yaml`: the registry of scraper plugins the orchestrator fans out to."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError

from pipeline.core.exceptions import ConfigurationError
from pipeline.core.models import RateLimitConfig


class SiteConfig(BaseModel):
    """One entry in `sites.yaml`, registering a scraper plugin."""

    id: str
    module: str
    enabled: bool
    schedule: str
    strategy: Literal["http", "browser"]
    rate_limit: RateLimitConfig
    dq_profile: str


class SitesConfig(BaseModel):
    """The full `sites.yaml` document."""

    sites: list[SiteConfig]


def load_sites_config(path: Path) -> SitesConfig:
    """Load and validate `path` as a `sites.yaml` document.

    Raises `ConfigurationError` if the file is missing, is not valid YAML, or fails schema
    validation.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read sites config at {path}: {exc}") from exc

    try:
        raw_document = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in sites config at {path}: {exc}") from exc

    try:
        return SitesConfig.model_validate(raw_document)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid sites config at {path}: {exc}") from exc
