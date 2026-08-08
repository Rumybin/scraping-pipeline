# 0005 — Quarantine invalid records instead of dropping them

## Context

Every scrape produces some records that fail validation: a missing required field, a price that
fails a range check, a duplicate within the same run, a type mismatch after a site's markup
changes shape. The easy default is to filter these out before they reach the staging zone and move
on — the "clean" dataset downstream never has to think about them. But silently dropping records
is exactly the failure mode this project is built to catch: a layout change that starts producing
50% garbage looks, from a dropped-records approach, identical to a healthy run that legitimately
had fewer matching records that day. Nothing distinguishes "the site changed" from "there was less
data today" without keeping the rejects and why they were rejected.

## Decision

Every record that fails a `pandera` validation check is written to the quarantine zone
(`quarantine/site=<site_id>/dt=<date>/rejected.jsonl`) as a JSON line carrying the original
extracted payload plus a structured rejection reason (which check failed, what value triggered it).
No record is ever discarded outright — a validation failure changes *where* a record is written,
never whether it's written. The DQ engine's completeness and validity scores are computed from the
ratio of staged-vs-quarantined records, which makes quarantine volume a first-class quality signal
rather than an invisible loss.

## Consequences

- A silent-failure incident — the exact scenario this project is meant to catch — becomes visible
  as a spike in quarantine volume with a specific, groupable rejection reason, instead of an
  unexplained drop in output count days later.
- Quarantined data is available for reprocessing once a scraper or normalizer bug is fixed, without
  re-fetching — consistent with the raw-zone replayability guarantee in Hard Rule 5.
- Storage cost grows slightly (rejected records are kept, not discarded), which is accepted because
  quarantine data is small relative to raw responses and has a natural retention policy (per-run,
  per-date partitioning) if it ever needs pruning.
- Every quality-engine change that adds or tightens a validation rule has an immediate, observable
  cost in quarantine volume, which is a deliberate feedback loop: it forces new rules to be tuned
  against real data shape rather than added speculatively.
