# 0004 — Hostile test harness over testing against third parties

## Context

The project's central engineering claim is resilience: retry/backoff tuned per error class,
per-domain circuit breaking, soft-block detection, and graceful degradation under rate limiting,
malformed HTML, timeouts, and partial failures. Proving that claim requires exercising those code
paths against real failure conditions — but Hard Rule 4 forbids targeting sites with commercial
anti-bot protection, and even permissive real sites cannot be relied on to reproduce a specific
failure mode (a 503 storm, a slow-drip response, a truncated payload) on demand, repeatably, in
CI. Deliberately hammering a real third-party site to validate a circuit breaker is also, simply,
being a bad actor against infrastructure that isn't ours.

## Decision

Build `tests/hostile_server/` — a `fastapi` + `uvicorn` server that deterministically simulates
nine adversarial scenarios (e.g. rate limiting, intermittent 5xx, connection resets, slow
responses, malformed/truncated HTML, soft-block pages, redirect loops). Resilience tests in
`tests/resilience/` run scrapers and fetchers against this local server, never against a real
target. `docker compose up` brings the hostile server up alongside the pipeline so the full
resilience suite runs unattended and reproducibly, including in CI.

## Consequences

- Every resilience scenario is deterministic and version-controlled: a failing circuit-breaker
  test points at a specific, inspectable scenario in the hostile server, not at whatever a real
  site happened to do that day.
- The hostile server is itself a portfolio artifact — "I built a server designed to break my own
  scrapers" is a defensible, zero-legal-risk demonstration of QA thinking, which is the stated
  positioning of this project.
- This validates the pipeline's *general* failure-handling behavior, not the specific quirks of
  any one real site's anti-bot stack; real-site resilience is bounded by what Hard Rule 4 already
  excludes as a target in the first place, so this is not a gap the project claims to cover.
- The hostile server is a dev-only dependency (`fastapi`, `uvicorn`) that must be kept passing
  alongside the rest of the suite — it is test infrastructure, not production infrastructure, and
  is never deployed as part of a live backend.
