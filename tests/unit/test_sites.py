"""Tests for the sites.yaml loader in `pipeline.core.sites`."""

from pathlib import Path

import pytest

from pipeline.core.exceptions import ConfigurationError
from pipeline.core.sites import SiteConfig, load_sites_config

FIXTURES = Path(__file__).parent.parent / "fixtures"
REPO_ROOT_SITES_YAML = Path(__file__).parent.parent.parent / "sites.yaml"


def test_load_valid_sites_config_parses_registered_site() -> None:
    config = load_sites_config(FIXTURES / "sites_valid.yaml")

    assert len(config.sites) == 1
    site = config.sites[0]
    assert isinstance(site, SiteConfig)
    assert site.id == "books_sandbox"
    assert site.module == "scrapers.books_sandbox:BooksScraper"
    assert site.strategy == "http"
    assert site.rate_limit.rps == 2.0
    assert site.rate_limit.burst == 5
    assert site.rate_limit.respect_crawl_delay is True
    assert site.dq_profile == "strict"


def test_load_invalid_schema_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="invalid sites config"):
        load_sites_config(FIXTURES / "sites_invalid_schema.yaml")


def test_load_malformed_yaml_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="invalid YAML"):
        load_sites_config(FIXTURES / "sites_invalid_yaml.yaml")


def test_load_missing_file_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="cannot read sites config"):
        load_sites_config(FIXTURES / "does_not_exist.yaml")


def test_load_repo_root_sites_yaml_registers_books_sandbox() -> None:
    config = load_sites_config(REPO_ROOT_SITES_YAML)

    assert len(config.sites) == 1
    assert config.sites[0].id == "books_sandbox"
    assert config.sites[0].module == "scrapers.books_sandbox:BooksScraper"
    assert config.sites[0].enabled is True
