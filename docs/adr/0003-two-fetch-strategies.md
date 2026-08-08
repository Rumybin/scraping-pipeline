# 0003 — HTTP-first fetching with escalation to browser

## Context

The six target sites span static server-rendered HTML (T0/T1 sandboxes and public catalogs) and
genuinely JS-rendered pages (T2 SPAs, infinite scroll, XHR/GraphQL-backed content). A single fetch
strategy is wrong for both ends of that spectrum: a headless browser can render anything but is an
order of magnitude slower and heavier than a plain HTTP request, while a plain HTTP client cannot
execute the JavaScript that some target pages require to produce their content at all.

Always using a browser would make the T0/T1 sites — the majority of the fixed six-site set —
needlessly expensive to fetch and slower to run in CI. Always using plain HTTP would simply fail
to extract data from the T2 sites, since the markup returned by the server never contains the
final content.

## Decision

Each scraper declares its fetch strategy per site in `sites.yaml`: `http` (`httpx` + `selectolax`)
or `browser` (`playwright`, async API). `http` is the default and preferred strategy. A
classifier in `fetchers/classifier.py` can detect signals of a soft block or an empty/JS-shell
response on an `http` fetch and escalate that specific fetch to the `browser` strategy, rather
than every scraper for that site being hard-coded to browser mode from the start. Both fetchers
sit behind the same per-domain rate limiter, robots.txt check, and circuit breaker, so escalation
changes how a page is retrieved, not whether the pipeline's politeness and resilience guarantees
apply.

## Consequences

- Sites that don't need a browser never pay for one — CI stays fast and deterministic for the T0
  sandbox suite, which is the backbone of the resilience test harness (ADR 0004).
- The escalation path is itself an explicit engineering artifact (a classifier with test coverage)
  rather than a silent fallback, which matters for a QA-focused portfolio: escalation decisions are
  observable in structured logs (`fetch.escalated`, with the triggering signal) and countable in
  metrics, not a hidden retry.
- Two code paths means two things to keep correct under the same contract (headers, rate limiting,
  robots compliance); the shared middleware layer (rate limiter, robots check, breaker) is what
  keeps that from becoming two independently-maintained fetchers.
- `browser` fetches are strictly more expensive in compute and wall-clock time, so a
  misconfigured or over-eager escalation heuristic has a direct cost; the classifier's thresholds
  are therefore tuned against the hostile test server, not tuned live against real sites.
