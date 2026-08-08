# 0002 — Object store + Parquet over a 24/7 relational database

## Context

The pipeline runs once a day, fans out across roughly six sites, and needs to persist three
distinct zones of data: raw fetched responses (for replayable parsing per Hard Rule 5), staged
structured records after normalization, and a quarantine of rejected records with rejection
reasons. It also needs this data to be queryable for the quality dashboard and for `duckdb`-backed
analytics.

A relational database (Postgres, MySQL) is the default choice for structured data, but it implies
a server that is running, patched, and paid for 24/7 even though the workload is a batch job that
executes for a few minutes once a day. Across the `local` / `free` / `aws` backend matrix (ADR
0001), a 24/7 database also means provisioning and paying for RDS (or running a container
persistently) in every environment, including the free-tier one where that isn't free.

## Decision

Persist everything as files in object storage — local filesystem in `local`, Cloudflare R2 in
`free`, Amazon S3 in `aws` — partitioned by `site_id` and `dt` (date), with raw responses gzipped
and staged/quarantine data in Parquet via `pyarrow`. State that must be mutated between runs
(dedupe hashes, per-site breaker state, run manifests) goes in a lightweight `StateStore`
(SQLite / D1 / DynamoDB per ADR 0001) sized for key-value and small-table access, not for ad hoc
analytical queries. Analytical queries against the Parquet zones run through `duckdb`, which reads
Parquet directly from the object store without a running database process.

## Consequences

- Zero idle compute cost: nothing needs to be "kept on" between daily runs. This matters
  specifically for the `free` backend, which has to actually be free.
- Storage is naturally partitioned and replayable: re-running quality checks or backfilling the
  dashboard means pointing `duckdb` at a prefix, not restoring a database snapshot.
- Schema evolution is looser than a relational schema would enforce — Parquet files from different
  runs could in principle drift in shape. This is accepted because `pandera` validates every batch
  before it's written to the staging zone, so the schema contract is enforced at write time by the
  application, not by the storage engine.
- Point lookups and small mutable state (has this URL been seen before?) are a poor fit for
  Parquet-on-object-storage, which is why that data is explicitly carved out to `StateStore`
  rather than forced into the same Parquet-everywhere model.
