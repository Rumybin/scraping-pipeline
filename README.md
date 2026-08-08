# Scraping Pipeline

[![CI](https://github.com/Rumybin/scraping-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/Rumybin/scraping-pipeline/actions/workflows/ci.yml)

A production-grade, backend-agnostic web scraping pipeline that runs daily across a fixed set of
heterogeneous sites, validates data quality before promotion, detects silent failures, and
publishes a public data-quality dashboard.

📊 [Live data-quality report](https://rumybin.github.io/scraping-pipeline/) — a real run, not a
mockup (currently a manually-published snapshot; automated per-run publishing lands in Phase 3A).

**Status: Phase 2 in progress.** 4 sites live (2 plain-HTTP, 2 browser-rendered), full resilience
layer built and proven against a purpose-built hostile test server: per-error-class retry,
per-domain circuit breaker, soft-block/JS-shell classifier, automatic http→browser escalation.

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

## Reliability engineering

- **Chaos test harness** (`tests/hostile_server/`): a FastAPI server built to break the scrapers —
  9 adversarial scenarios (429 + `Retry-After`, intermittent 503, hung responses, mid-run CSS
  drift, soft-block challenge pages, mismatched encoding, unexpected JSON, self-enforced rate
  limiting, a 50MB streamed body) — see
  [`docs/adr/0004-hostile-test-harness.md`](docs/adr/0004-hostile-test-harness.md).
- **Per-error-class retry**: `429` honors `Retry-After`, `5xx` backs off exponentially with
  jitter, network/timeout errors retry fast, other `4xx` never retries.
- **Per-domain circuit breaker**: one failing site's domain trips independently — every other
  site keeps running.
- **Automatic http→browser escalation**: an http-first fetch that turns out empty/JS-shell
  transparently retries through a real (lazily-launched) browser, so a run that never needs one
  never pays for launching one.
- Retry and circuit-breaker behavior is proven against the real hostile server over a real
  loopback socket (`tests/resilience/`), not just mocked.

## Metrics

Populated from real runs only — never invented. Still `TBD` where nothing has actually measured
it yet (no soak run or metrics sink exist before Phase 4/5).

| Metric | Value |
|---|---|
| Sites active | 4 (`books_sandbox`, `quotes_js`, `quotes_scroll`, `wikipedia_tech`) |
| Tests passing | 225 |
| Test coverage | 97% |
| Sample run — `books_sandbox` | 1,000 records, 0 quarantined, DQ gate **PASS** |
| Success rate (14-day soak) | TBD — soak run not started (Phase 5) |
| p95 fetch latency | TBD — no metrics sink yet (Phase 4) |

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
