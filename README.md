# Scraping Pipeline

A production-grade, backend-agnostic web scraping pipeline that runs daily across a fixed set of
heterogeneous sites, validates data quality before promotion, detects silent failures, and
publishes a public data-quality dashboard.

**Status: Phase 0 — repository scaffolding.** No scraping logic exists yet.

## Why this exists

Most scraping projects are "a script that runs." This one treats the pipeline itself as the
product: every site is validated for data quality before its output is trusted, silent failures
(a layout change that quietly returns garbage) are caught by design, and resilience is proven
against a purpose-built hostile test server rather than by attacking real, defended sites. The
primary quality bar is the test infrastructure, not the scrapers — see
[`docs/adr/0004-hostile-test-harness.md`](docs/adr/0004-hostile-test-harness.md).

## Architecture

One environment variable, `PIPELINE_BACKEND ∈ {local, free, aws}`, selects the entire runtime —
scheduler, compute, object store, state store, metrics sink, and notifier — without changing a
single line of scraper code. See [`docs/adr/0001-backend-abstraction.md`](docs/adr/0001-backend-abstraction.md)
for why.

```
sites.yaml → orchestrator → fetchers (http | browser) → scrapers → quality engine → object store
                                                                          ↓
                                                                    quarantine (rejects + reason)
```

Architectural decisions are recorded as ADRs in [`docs/adr/`](docs/adr/).

## Metrics

Populated from real runs only — never invented. `TBD` until Phase 5's soak run.

| Metric | Value |
|---|---|
| Sites active | TBD |
| Records scraped (last run) | TBD |
| Success rate | TBD |
| p95 fetch latency | TBD |
| Data-quality score | TBD |
| Test coverage | TBD |

## Getting started

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run mypy src
```

## Compliance

Every target site is checked against `robots.txt` and Terms of Service before a scraper is
written; see [`docs/compliance.md`](docs/compliance.md). Sites with commercial anti-bot protection
are never targeted — resilience is proven against `tests/hostile_server/` instead.

## License

[MIT](LICENSE)
