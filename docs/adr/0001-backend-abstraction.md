# 0001 — Backend abstraction over direct AWS coupling

## Context

The pipeline needs to run in at least three environments over its lifetime: a laptop during
development, a free-tier scheduled environment for the portfolio's public daily run, and AWS for a
time-boxed "proof window" that demonstrates cloud deployment skills without paying for idle
infrastructure indefinitely. Coupling scraper and orchestration code directly to `boto3` calls
would mean the code that proves the project's engineering value (scrapers, quality engine,
fetchers) only runs where AWS credentials and billing exist, and cannot be exercised, tested, or
demoed for free.

The alternative — writing straight to AWS primitives (S3, DynamoDB, EventBridge, SQS) — is the
default instinct for a project with "AWS" in its resume value, but it silently makes the AWS SDK a
load-bearing dependency of business logic, which is exactly the coupling this project exists to
avoid.

## Decision

Define capability as `Protocol` interfaces (`ObjectStore`, `StateStore`, `MetricsSink`,
`Notifier`) in `src/pipeline/backends/base.py`. Every concrete implementation — filesystem, R2,
DynamoDB, CloudWatch, Discord — lives behind that protocol in `backends/`. A single environment
variable, `PIPELINE_BACKEND ∈ {local, free, aws}`, selects the concrete set at process start via a
factory in `backends/__init__.py`. No module outside `backends/` imports a vendor SDK; a tree-wide
grep test enforces this as a hard CI gate, not a convention.

All backend implementations are validated against one shared contract test suite
(`tests/unit/test_backend_contract.py`) so behavioral drift between `local` and `aws` is caught
before a scraper ever runs against the "wrong" backend's edge cases.

## Consequences

- Scraper, fetcher, and quality code is provably portable: switching `PIPELINE_BACKEND` cannot
  require touching that code, because it has no path to a vendor type in the first place.
- The AWS proof window (Phase 3B) can be `terraform apply`'d, evidenced, and `terraform destroy`'d
  without the rest of the system ever depending on AWS being up.
- Cost: every new capability needs a protocol method defined once and implemented N times, and the
  contract test suite must grow alongside it. This is deliberate overhead in exchange for the
  portability guarantee — acceptable because backend count is small (3) and fixed.
- Some backend-specific features (e.g. DynamoDB conditional writes, CloudWatch dimensions) get
  flattened to the lowest common denominator the protocol exposes. Backend-specific optimizations
  that can't be expressed in the shared interface are out of scope by design.
