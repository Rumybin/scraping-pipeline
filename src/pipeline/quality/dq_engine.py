"""Data-quality engine v1: completeness, uniqueness, validity, consistency (FR-13, PRD §4.3).

Timeliness and accuracy are the remaining two of the six PRD dimensions; they need a 7-day
baseline and manually-sampled ground truth respectively, neither of which exists yet, so they are
added in Phase 4. This engine gates staging → curated promotion for the Phase 1 vertical slice
with the four dimensions that can be scored from a single batch alone (FR-14).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Literal

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors
from pydantic import BaseModel

from pipeline.core.models import ScrapedRecord

GateStatus = Literal["pass", "warn", "fail"]


class DimensionResult(BaseModel):
    """Score for one DQ dimension against its profile's warn/fail thresholds."""

    dimension: str
    score: float
    warn_threshold: float
    fail_threshold: float
    status: GateStatus
    detail: str


class DqReport(BaseModel):
    """Result of evaluating one batch of `ScrapedRecord`s (FR-13, FR-16)."""

    run_id: str
    site_id: str
    generated_at: datetime
    total_records: int
    dimensions: list[DimensionResult]
    gate_status: GateStatus


class DqProfile(BaseModel):
    """Per-dimension warn/fail thresholds, selected in `sites.yaml` by the `dq_profile` field."""

    completeness_warn: float
    completeness_fail: float
    uniqueness_warn: float
    uniqueness_fail: float
    validity_warn: float
    validity_fail: float
    consistency_warn: float
    consistency_fail: float


STRICT_PROFILE = DqProfile(
    completeness_warn=0.99,
    completeness_fail=0.95,
    uniqueness_warn=0.995,
    uniqueness_fail=0.98,
    validity_warn=0.99,
    validity_fail=0.95,
    consistency_warn=0.99,
    consistency_fail=0.95,
)

DQ_PROFILES: dict[str, DqProfile] = {"strict": STRICT_PROFILE}

_VALIDITY_SCHEMA = pa.DataFrameSchema(
    {
        "title": pa.Column(str, checks=pa.Check.str_length(min_value=1)),
        "price": pa.Column(float, checks=pa.Check.gt(0), nullable=True),
        "currency": pa.Column(str, checks=pa.Check.str_length(3, 3), nullable=True),
    },
    strict=False,
)


def resolve_profile(name: str) -> DqProfile:
    """Look up a named DQ profile, as declared by a site's `dq_profile` field in `sites.yaml`.

    Raises `KeyError` if `name` is not a registered profile.
    """
    return DQ_PROFILES[name]


def evaluate(
    records: Sequence[ScrapedRecord], *, run_id: str, site_id: str, profile: DqProfile
) -> DqReport:
    """Score `records` against `profile`'s four DQ dimensions and derive an overall gate status.

    An empty `records` sequence scores every dimension at 0.0 and fails the gate: an empty batch
    is the silent-failure scenario this engine exists to catch, not a vacuous pass (ADR 0005).
    """
    dimensions = [
        _score_completeness(records, profile),
        _score_uniqueness(records, profile),
        _score_validity(records, profile),
        _score_consistency(records, profile),
    ]
    return DqReport(
        run_id=run_id,
        site_id=site_id,
        generated_at=datetime.now(UTC),
        total_records=len(records),
        dimensions=dimensions,
        gate_status=_overall_status(dimensions),
    )


def _score_completeness(records: Sequence[ScrapedRecord], profile: DqProfile) -> DimensionResult:
    """Mean of each record's own `completeness_score` (fraction of required fields present)."""
    score = _mean(r.completeness_score for r in records)
    return _dimension_result(
        "completeness",
        score,
        profile.completeness_warn,
        profile.completeness_fail,
        detail=f"mean per-record completeness across {len(records)} records",
    )


def _score_uniqueness(records: Sequence[ScrapedRecord], profile: DqProfile) -> DimensionResult:
    """Fraction of records whose `content_hash` is unique within the batch (FR-8)."""
    if not records:
        score = 0.0
    else:
        hashes = [record.content_hash for record in records]
        score = len(set(hashes)) / len(hashes)
    return _dimension_result(
        "uniqueness",
        score,
        profile.uniqueness_warn,
        profile.uniqueness_fail,
        detail=f"{len({r.content_hash for r in records})} unique of {len(records)} content hashes",
    )


def _score_validity(records: Sequence[ScrapedRecord], profile: DqProfile) -> DimensionResult:
    """Fraction of records with a non-empty title, positive price, and 3-letter currency code."""
    if not records:
        return _dimension_result(
            "validity",
            0.0,
            profile.validity_warn,
            profile.validity_fail,
            detail="no records to validate",
        )

    frame = pl.DataFrame(
        {
            "title": [record.title for record in records],
            "price": [
                float(record.price) if record.price is not None else None for record in records
            ],
            "currency": [record.currency for record in records],
        }
    )
    try:
        _VALIDITY_SCHEMA.validate(frame, lazy=True)
        invalid_row_count = 0
    except SchemaErrors as exc:
        invalid_row_count = exc.failure_cases.select("index").unique().height

    score = 1 - (invalid_row_count / len(records))
    return _dimension_result(
        "validity",
        score,
        profile.validity_warn,
        profile.validity_fail,
        detail=f"{invalid_row_count} of {len(records)} records failed a validity check",
    )


def _score_consistency(records: Sequence[ScrapedRecord], profile: DqProfile) -> DimensionResult:
    """Fraction of records where `price` and `currency` are both present or both absent."""
    if not records:
        score = 0.0
    else:
        consistent = sum(1 for r in records if (r.price is None) == (r.currency is None))
        score = consistent / len(records)
    return _dimension_result(
        "consistency",
        score,
        profile.consistency_warn,
        profile.consistency_fail,
        detail="price and currency must both be present or both be absent",
    )


def _dimension_result(
    name: str, score: float, warn_threshold: float, fail_threshold: float, *, detail: str
) -> DimensionResult:
    if score < fail_threshold:
        status: GateStatus = "fail"
    elif score < warn_threshold:
        status = "warn"
    else:
        status = "pass"
    return DimensionResult(
        dimension=name,
        score=score,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        status=status,
        detail=detail,
    )


def _overall_status(dimensions: Sequence[DimensionResult]) -> GateStatus:
    statuses = {dimension.status for dimension in dimensions}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return sum(materialized) / len(materialized)
