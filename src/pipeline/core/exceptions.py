"""Exception hierarchy for the scraping pipeline.

Every exception raised by pipeline code inherits from `PipelineError`. Bare `Exception` is never
raised and bare `except:` is never used — see CLAUDE.md §7.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for every exception raised by pipeline code."""


class ConfigurationError(PipelineError):
    """Raised when required configuration is missing or invalid."""


class FetchError(PipelineError):
    """Base class for errors raised while fetching a remote resource."""


class RobotsDisallowedError(FetchError):
    """Raised when `robots.txt` disallows the requested path or its `Crawl-delay` is violated."""


class HttpFetchError(FetchError):
    """Raised when an HTTP fetch fails (network error or non-2xx status)."""


class BrowserFetchError(FetchError):
    """Raised when a Playwright-driven browser fetch fails."""


class SoftBlockDetectedError(FetchError):
    """Raised when the response classifier detects a soft block (e.g. an interstitial page)."""


class ParsingError(PipelineError):
    """Raised when a scraper fails to extract expected structure from fetched content."""


class RecordValidationError(PipelineError):
    """Raised when a scraped record fails schema or data-quality validation.

    Carries the rejection reason that is written alongside the record in the quarantine zone.
    """


class BackendError(PipelineError):
    """Base class for errors raised by a backend implementation (object store, state, etc.)."""


class ObjectStoreError(BackendError):
    """Raised when an `ObjectStore` operation fails."""


class ObjectNotFoundError(ObjectStoreError):
    """Raised when `ObjectStore.get` is called for a key that does not exist."""


class StateStoreError(BackendError):
    """Raised when a `StateStore` operation fails."""


class SiteStateNotFoundError(StateStoreError):
    """Raised when `StateStore.get_site_state` is called for a site with no persisted state."""


class NotificationError(BackendError):
    """Raised when a `Notifier` fails to deliver an alert."""


class CircuitOpenError(PipelineError):
    """Raised when a per-domain circuit breaker is open and the site is skipped this run."""


class ScraperError(PipelineError):
    """Raised when a site-specific scraper plugin fails outside of a fetch or parse step."""
