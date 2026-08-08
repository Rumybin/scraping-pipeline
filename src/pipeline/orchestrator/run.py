"""Minimal single-site orchestrator: discover → fetch → parse → dedupe → DQ gate → persist.

This is the Phase 1 vertical-slice orchestrator (`CLAUDE.md` §9, Phase 1 DoD): one site, run
sequentially end to end on the `local` backend. Fan-out across sites, retries, and per-domain
circuit breaking are Phase 2 concerns and are intentionally not built here.

Assumes the process's working directory is the repository root, matching how the documented CLI
invocation (`python -m pipeline run --site <id>`) and CI both run it.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import importlib
import io
import json
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from pipeline.backends import build_backend_set
from pipeline.backends.base import ObjectStore, StateStore
from pipeline.core.config import Settings
from pipeline.core.context import RunContext
from pipeline.core.exceptions import ConfigurationError
from pipeline.core.models import QuarantinedRecord, RunManifest, ScrapedRecord
from pipeline.core.scraper import BaseScraper
from pipeline.core.sites import SiteConfig, SitesConfig, load_sites_config
from pipeline.fetchers.http import HttpFetcher
from pipeline.quality.dq_engine import DqReport, GateStatus, evaluate, resolve_profile
from pipeline.quality.report import render_html

_DEDUPE_TTL_DAYS = 90


@dataclass(frozen=True)
class RunResult:
    """Summary of one `run_site` call, returned to the CLI for reporting."""

    run_id: str
    site_id: str
    record_count: int
    quarantined_count: int
    gate_status: GateStatus
    dq_report: DqReport


async def run_site(
    site_id: str,
    *,
    settings: Settings | None = None,
    sites_path: Path = Path("sites.yaml"),
    local_root: Path = Path("data"),
) -> RunResult:
    """Run the registered scraper for `site_id` end to end and return a `RunResult`.

    Raises `ConfigurationError` if `site_id` is not registered (or is disabled) in `sites.yaml`,
    or if its `module` spec does not resolve to a `BaseScraper` subclass.
    """
    settings = settings or Settings()
    site = _resolve_site(load_sites_config(sites_path), site_id)
    scraper = _load_scraper_class(site.module)()

    run_id = _new_run_id()
    ctx = RunContext(
        run_id=run_id,
        site_id=site.id,
        backend=settings.pipeline_backend,
        scraper_version=await _resolve_scraper_version(),
        started_at=datetime.now(UTC),
    )
    run_date = ctx.started_at.date().isoformat()

    backend_set = build_backend_set(settings, local_root=local_root)
    fetched_records = await _fetch_and_parse(
        scraper, ctx, backend_set.object_store, run_date, settings.user_agent
    )
    deduped = await _dedupe(fetched_records, backend_set.state_store)

    profile = resolve_profile(site.dq_profile)
    report = evaluate(deduped, run_id=run_id, site_id=site.id, profile=profile)

    await _persist_staging(backend_set.object_store, site.id, run_date, run_id, deduped)
    if report.gate_status != "fail":
        await _persist_curated(backend_set.object_store, site.id, run_date, run_id, deduped)
    if scraper.quarantined:
        await _persist_quarantine(backend_set.object_store, site.id, run_date, scraper.quarantined)
    await _persist_report(backend_set.object_store, run_id, report)

    await backend_set.state_store.save_run(
        RunManifest(
            run_id=run_id,
            started_at=ctx.started_at,
            finished_at=datetime.now(UTC),
            site_counts={site.id: len(deduped)},
            error_counts={"quarantined": len(scraper.quarantined)},
            git_sha=ctx.scraper_version,
        )
    )

    return RunResult(
        run_id=run_id,
        site_id=site.id,
        record_count=len(deduped),
        quarantined_count=len(scraper.quarantined),
        gate_status=report.gate_status,
        dq_report=report,
    )


def _resolve_site(sites_config: SitesConfig, site_id: str) -> SiteConfig:
    for site in sites_config.sites:
        if site.id == site_id:
            if not site.enabled:
                raise ConfigurationError(f"site {site_id!r} is registered but disabled")
            return site
    raise ConfigurationError(f"site {site_id!r} is not registered in sites.yaml")


def _load_scraper_class(module_spec: str) -> type[BaseScraper]:
    try:
        module_path, class_name = module_spec.split(":", 1)
    except ValueError as exc:
        raise ConfigurationError(
            f"invalid scraper module spec {module_spec!r}, expected 'module.path:ClassName'"
        ) from exc

    try:
        module = importlib.import_module(f"pipeline.{module_path}")
    except ImportError as exc:
        raise ConfigurationError(f"cannot import scraper module {module_path!r}: {exc}") from exc

    scraper_class = getattr(module, class_name, None)
    if not (isinstance(scraper_class, type) and issubclass(scraper_class, BaseScraper)):
        raise ConfigurationError(f"{module_spec!r} does not resolve to a BaseScraper subclass")
    return scraper_class


async def _fetch_and_parse(
    scraper: BaseScraper,
    ctx: RunContext,
    object_store: ObjectStore,
    run_date: str,
    user_agent: str,
) -> list[ScrapedRecord]:
    records: list[ScrapedRecord] = []
    async with httpx.AsyncClient(http2=True, timeout=30.0) as client:
        fetcher = HttpFetcher(user_agent=user_agent, client=client)
        async for target in scraper.discover(ctx):
            raw = await fetcher.fetch(target, rate_limit=scraper.rate_limit)
            await object_store.put(
                _raw_key(ctx.site_id, run_date, ctx.run_id, target.url),
                gzip.compress(raw.body),
                content_type="application/gzip",
            )
            for item in await scraper.parse(raw, ctx):
                if isinstance(item, ScrapedRecord):
                    records.append(item)
    return records


async def _dedupe(
    records: Sequence[ScrapedRecord], state_store: StateStore, *, ttl_days: int = _DEDUPE_TTL_DAYS
) -> list[ScrapedRecord]:
    """Drop records whose `content_hash` was already seen in a prior run (FR-8)."""
    deduped: list[ScrapedRecord] = []
    for record in records:
        if await state_store.seen(record.content_hash):
            continue
        await state_store.mark_seen(record.content_hash, record.site_id, ttl_days)
        deduped.append(record)
    return deduped


async def _persist_staging(
    object_store: ObjectStore,
    site_id: str,
    run_date: str,
    run_id: str,
    records: Sequence[ScrapedRecord],
) -> None:
    key = f"staging/site={site_id}/dt={run_date}/part-{run_id}.parquet"
    await object_store.put(
        key, _records_to_parquet_bytes(records), content_type="application/x-parquet"
    )


async def _persist_curated(
    object_store: ObjectStore,
    site_id: str,
    run_date: str,
    run_id: str,
    records: Sequence[ScrapedRecord],
) -> None:
    key = f"curated/domain={site_id}/dt={run_date}/part-{run_id}.parquet"
    await object_store.put(
        key, _records_to_parquet_bytes(records), content_type="application/x-parquet"
    )


async def _persist_quarantine(
    object_store: ObjectStore, site_id: str, run_date: str, quarantined: Sequence[QuarantinedRecord]
) -> None:
    key = f"quarantine/site={site_id}/dt={run_date}/rejected.jsonl"
    body = "\n".join(record.model_dump_json() for record in quarantined).encode("utf-8")
    await object_store.put(key, body, content_type="application/jsonl")


async def _persist_report(object_store: ObjectStore, run_id: str, report: DqReport) -> None:
    html = render_html(report).encode("utf-8")
    await object_store.put(f"reports/run={run_id}/dq_report.html", html, content_type="text/html")
    await object_store.put("reports/dq_report.html", html, content_type="text/html")


def _records_to_parquet_bytes(records: Sequence[ScrapedRecord]) -> bytes:
    frame = pl.DataFrame(
        {
            "record_id": [r.record_id for r in records],
            "site_id": [r.site_id for r in records],
            "source_url": [str(r.source_url) for r in records],
            "content_hash": [r.content_hash for r in records],
            "run_id": [r.run_id for r in records],
            "scraped_at": [r.scraped_at for r in records],
            "scraper_version": [r.scraper_version for r in records],
            "fetch_strategy": [r.fetch_strategy for r in records],
            "backend": [r.backend.value for r in records],
            "title": [r.title for r in records],
            "price": [float(r.price) if r.price is not None else None for r in records],
            "currency": [r.currency for r in records],
            "availability": [r.availability for r in records],
            "attributes_json": [json.dumps(r.attributes) for r in records],
            "completeness_score": [r.completeness_score for r in records],
            "extraction_method": [r.extraction_method for r in records],
        }
    )
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    return buffer.getvalue()


def _raw_key(site_id: str, run_date: str, run_id: str, url: str) -> str:
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"raw/site={site_id}/dt={run_date}/run={run_id}/{url_hash}.html.gz"


def _new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


async def _resolve_scraper_version() -> str:
    """Best-effort current git commit SHA for `RunContext.scraper_version`.

    Returns `"unknown"` outside a git checkout or if `git` is not installed, rather than failing
    the run over metadata that is not essential to correctness.
    """
    return await asyncio.to_thread(_git_sha_sync)


def _git_sha_sync() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"
