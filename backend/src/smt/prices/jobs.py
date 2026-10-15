"""The nightly price job, `prices.refresh_daily` (registered in smt.worker.jobs).

Runs at 18:30 ET on NYSE trading days, after the 16:00 close (PLAN.md §7).

What it prices:

* every security with insider activity in the last 90 days: a Form 3/4/5 or
  Form 144 filed in that window, on that security or on any share class of
  its issuer (ticker pages show issuer-level activity on every class);
* the benchmarks returns are measured against: SPY, plus the sector ETFs
  once the security master has them.

How it picks a provider, per security:

1. Providers are tried in the configured order (PRICE_PROVIDERS, default
   "finlake,yfinance"). yfinance joins the chain only when private mode is
   configured, because Yahoo's terms allow personal use only.
2. The first provider whose bars reach the last completed session wins.
3. If none is current, the provider with the most recent bars is stored
   anyway: real but stale prices beat none, and the run's stats say so.

Every run re-reads the last 30 days (a provider can skip a session and fill
it in later) and reloads a security's whole history when a split or a new
dividend has re-based it (see loader.history_was_rebased).

Rate limits: yfinance calls are paced at 2 per second, and the first
"too many requests" answer stops yfinance for the rest of the run. finlake
is a local read with no limit.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from smt.config import Settings, get_settings
from smt.db.session import session_scope
from smt.domain.business_days import is_trading_day, session_close, to_eastern
from smt.logs import get_logger
from smt.net.ratelimit import Priority, TokenBucket
from smt.prices.base import Bar, PriceProvider, ProviderError, ProviderRateLimited, SplitSource
from smt.prices.finlake import FinlakeProvider
from smt.prices.loader import (
    history_was_rebased,
    record_splits,
    stored_closes,
    stored_ranges,
    upsert_bars,
)
from smt.prices.yahoo import YFinanceProvider
from smt.worker.scheduler import JobContext

log = get_logger("prices.jobs")

#: Benchmarks priced every night: the S&P 500 ETF and the 11 Select Sector
#: SPDR ETFs (sector-relative returns, PLAN.md §8). Ones the security master
#: doesn't know yet are listed in the run's stats instead of being invented.
BENCHMARKS = ("SPY", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
#: "Recent insider activity" means a filing in the last 90 days.
ACTIVITY_DAYS = 90
#: Days re-read before the last stored bar on every run.
OVERLAP_DAYS = 30
#: Pace for Yahoo. It publishes no limit; this stays well clear of blocks.
YAHOO_REQUESTS_PER_SECOND = 2.0
#: Stop starting new securities after this long, so a slow night can never
#: outlive the job's 3-hour lease. The rest are picked up the next night.
RUN_BUDGET_SECONDS = 150 * 60
#: Symbols listed per category in the run's stats (keeps the row small).
SAMPLE_SIZE = 25


# ---------------------------------------------------------------- providers

def private_mode_configured(settings: Settings) -> bool:
    """True when the single-owner private mode has a passphrase and a session secret."""
    return bool(settings.private_mode_passphrase.get_secret_value()
                and settings.session_secret.get_secret_value())


def provider_chain(settings: Settings | None = None) -> list[PriceProvider]:
    """The providers to try, in order, from PRICE_PROVIDERS."""
    settings = settings or get_settings()
    chain: list[PriceProvider] = []
    for name in (part.strip().lower() for part in settings.price_providers.split(",")):
        if not name:
            continue
        if name == "finlake":
            chain.append(FinlakeProvider())
        elif name == "yfinance":
            if private_mode_configured(settings):
                chain.append(YFinanceProvider())
            else:
                log.info("prices.yfinance_skipped", reason="private mode is not configured")
        else:
            log.warning("prices.unknown_provider", name=name)
    return chain


# ---------------------------------------------------------------- what to price

@dataclass(frozen=True)
class Target:
    """One security to price, and the ticker providers know it by."""

    security_id: int
    symbol: str


async def select_targets(session: AsyncSession, today: date) -> tuple[list[Target], list[str]]:
    """Benchmarks first, then securities by most recent insider activity.

    Returns (targets, benchmarks the security master doesn't have).
    """
    since = today - timedelta(days=ACTIVITY_DAYS)
    found = dict((await session.execute(text("""
        SELECT s.primary_ticker, min(s.id)
        FROM securities s
        WHERE s.primary_ticker = ANY(:symbols)
        GROUP BY s.primary_ticker"""), {"symbols": list(BENCHMARKS)})).tuples().all())
    targets = [Target(int(found[symbol]), symbol) for symbol in BENCHMARKS if symbol in found]
    missing = [symbol for symbol in BENCHMARKS if symbol not in found]

    # Filings are the entry point: every trade is filed after it happens, so
    # "filed in the last 90 days" covers every trade made in that window.
    active = await session.execute(text("""
        WITH recent AS (
            SELECT t.security_id, t.issuer_entity_id, f.filed_on AS seen
            FROM filings f JOIN transactions t ON t.filing_id = f.id
            WHERE f.filed_on >= :since AND t.is_current
            UNION ALL
            SELECT p.security_id, p.issuer_entity_id, f.filed_on
            FROM filings f JOIN planned_sales p ON p.filing_id = f.id
            WHERE f.filed_on >= :since
        ),
        touched AS (
            SELECT r.security_id AS id, max(r.seen) AS seen
            FROM recent r WHERE r.security_id IS NOT NULL GROUP BY r.security_id
            UNION ALL
            SELECT s.id, max(r.seen)
            FROM recent r JOIN securities s ON s.issuer_entity_id = r.issuer_entity_id
            GROUP BY s.id
        )
        SELECT s.id, s.primary_ticker
        FROM touched x JOIN securities s ON s.id = x.id
        WHERE s.primary_ticker IS NOT NULL
          AND (s.delisted_on IS NULL OR s.delisted_on >= :since)
        GROUP BY s.id, s.primary_ticker
        ORDER BY max(x.seen) DESC, s.primary_ticker"""), {"since": since})
    seen = {t.security_id for t in targets}
    for security_id, symbol in active.tuples():
        if security_id not in seen:
            seen.add(security_id)
            targets.append(Target(int(security_id), str(symbol)))
    return targets, missing


def last_completed_session(now: datetime) -> date:
    """The most recent NYSE session whose close has passed at `now`."""
    local = to_eastern(now)
    day = local.date()
    if is_trading_day(day) and local >= session_close(day):
        return day
    day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


# ---------------------------------------------------------------- the run

@dataclass
class RefreshReport:
    """What one run did. Saved as the run's stats on /status."""

    targets: int = 0
    current: dict[str, int] = field(default_factory=dict)  # provider -> securities priced to the last session
    stale: int = 0            # stored, but the newest bar is older than the last session
    unavailable: int = 0      # no provider has this ticker
    failed: int = 0           # every provider that might have it failed (retried next run)
    reloaded: int = 0         # whole history reloaded after a split or dividend re-base
    rows_new: int = 0
    rows_updated: int = 0
    requests: dict[str, int] = field(default_factory=dict)
    missing_sessions: int = 0     # NYSE sessions absent inside a stored series
    off_calendar_dropped: int = 0  # bars dated on days the NYSE was closed
    rate_limited: list[str] = field(default_factory=list)
    stopped_early: bool = False
    samples: dict[str, list[str]] = field(default_factory=dict)

    def note(self, kind: str, symbol: str) -> None:
        bucket = self.samples.setdefault(kind, [])
        if len(bucket) < SAMPLE_SIZE:
            bucket.append(symbol)

    def as_stats(self) -> dict[str, Any]:
        return {
            "targets": self.targets, "current": self.current, "stale": self.stale,
            "unavailable": self.unavailable, "failed": self.failed, "reloaded": self.reloaded,
            "requests": self.requests, "missing_sessions": self.missing_sessions,
            "off_calendar_dropped": self.off_calendar_dropped, "rate_limited": self.rate_limited,
            "stopped_early": self.stopped_early, "samples": self.samples,
        }


@dataclass
class _Candidate:
    provider: PriceProvider
    bars: list[Bar]
    reloaded: bool


async def refresh_prices(
    targets: list[Target],
    chain: list[PriceProvider],
    *,
    now: datetime,
    history_days: int,
    pacers: dict[str, TokenBucket] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
) -> RefreshReport:
    """Fetch and store bars for every target. Each security commits on its own."""
    report = RefreshReport(targets=len(targets))
    today = to_eastern(now).date()
    last_session = last_completed_session(now)
    disabled: set[str] = set()
    deadline = time.monotonic() + budget_seconds
    for target in targets:
        if time.monotonic() > deadline:
            report.stopped_early = True
            break
        await _refresh_one(target, chain, report, disabled=disabled, today=today,
                           last_session=last_session, history_days=history_days, pacers=pacers or {})
    return report


async def _refresh_one(target: Target, chain: list[PriceProvider], report: RefreshReport, *,
                       disabled: set[str], today: date, last_session: date, history_days: int,
                       pacers: dict[str, TokenBucket]) -> None:
    async with session_scope() as session:
        ranges = await stored_ranges(session, target.security_id)
    best: _Candidate | None = None
    failed = False
    for provider in chain:
        if provider.name in disabled:
            failed = True
            continue
        stored = ranges.get(provider.name)
        start = (stored.last_day - timedelta(days=OVERLAP_DAYS) if stored
                 else today - timedelta(days=history_days))
        reloaded = False
        try:
            bars = await _fetch(provider, target.symbol, start, today, pacers, report)
            if bars and stored is not None:
                async with session_scope() as session:
                    old = await stored_closes(session, target.security_id, provider.name,
                                              bars[0].day, bars[-1].day)
                if history_was_rebased(bars, old):
                    full_start = min(stored.first_day, today - timedelta(days=history_days))
                    bars = await _fetch(provider, target.symbol, full_start, today, pacers, report)
                    reloaded = True
        except ProviderRateLimited as exc:
            log.warning("prices.rate_limited", provider=provider.name, error=str(exc))
            disabled.add(provider.name)
            report.rate_limited.append(provider.name)
            failed = True
            continue
        except ProviderError as exc:
            log.warning("prices.provider_failed", provider=provider.name, symbol=target.symbol, error=str(exc))
            failed = True
            continue
        bars = _on_calendar(bars, report)
        if not bars:
            continue
        candidate = _Candidate(provider, bars, reloaded)
        if bars[-1].day >= last_session:
            best = candidate
            break
        if best is None or bars[-1].day > best.bars[-1].day:
            best = candidate

    if best is None:
        if failed:
            report.failed += 1
            report.note("failed", target.symbol)
        else:
            report.unavailable += 1
            report.note("unavailable", target.symbol)
        return

    async with session_scope() as session:
        counts = await upsert_bars(session, security_id=target.security_id, provider=best.provider.name,
                                   licence=best.provider.licence, bars=best.bars)
        # The provider's splits, read after its bars: they tell the value checks which stored
        # closes are still the prices trades were made at (quality.flag_totals_as_price).
        if isinstance(best.provider, SplitSource):
            splits = await asyncio.to_thread(best.provider.get_splits, target.symbol)
            if splits is not None:
                await record_splits(session, security_id=target.security_id, provider=best.provider.name,
                                    splits=splits)
    report.rows_new += counts.new
    report.rows_updated += counts.updated
    report.reloaded += int(best.reloaded)
    report.missing_sessions += _missing_sessions(best.bars)
    if best.bars[-1].day >= last_session:
        report.current[best.provider.name] = report.current.get(best.provider.name, 0) + 1
    else:
        report.stale += 1
        report.note("stale", f"{target.symbol} ({best.bars[-1].day.isoformat()})")


async def _fetch(provider: PriceProvider, symbol: str, start: date, end: date,
                 pacers: dict[str, TokenBucket], report: RefreshReport) -> list[Bar]:
    """One provider call, paced, in a worker thread (providers block)."""
    pacer = pacers.get(provider.name)
    if pacer is not None:
        await pacer.acquire(Priority.BACKFILL)
    report.requests[provider.name] = report.requests.get(provider.name, 0) + 1
    return await asyncio.to_thread(provider.get_daily_bars, symbol, start, end)


def _on_calendar(bars: list[Bar], report: RefreshReport) -> list[Bar]:
    """Drop bars dated on days the NYSE held no session (a provider artifact)."""
    kept = [b for b in bars if is_trading_day(b.day)]
    report.off_calendar_dropped += len(bars) - len(kept)
    return kept


def _missing_sessions(bars: list[Bar]) -> int:
    """NYSE sessions between the first and last bar that have no bar."""
    if not bars:
        return 0
    have = {b.day for b in bars}
    missing, day = 0, bars[0].day
    while day <= bars[-1].day:
        if is_trading_day(day) and day not in have:
            missing += 1
        day += timedelta(days=1)
    return missing


# ---------------------------------------------------------------- the job

def mostly_stale(report: RefreshReport) -> bool:
    """True when the run left the prices out of date, so it must not read as a clean success.

    Judged against the series the run could price at all. Tickers no provider has
    ("unavailable") say nothing about whether the provider is keeping up, so they are left
    out: the old rule, stale > half of all targets, counted them in the denominator and let
    run 14236 (2026-09-28) finish "ok" with 0 series current, 676 stale and 4,423
    unavailable out of 5,099, while finlake's cache had stopped at 2026-09-18.

    * Nothing current although there was something to price: the provider delivered no
      bar for the last session at all.
    * More series stale than current: the provider is mostly behind.
    """
    current = sum(report.current.values())
    if report.targets and current == 0:
        return True
    return report.stale > current


async def refresh_daily(ctx: JobContext) -> None:
    """Scheduler entry point: price active securities and benchmarks."""
    settings = get_settings()
    chain = provider_chain(settings)
    ctx.stats["providers"] = [p.name for p in chain]
    if not chain:
        ctx.stats["skipped"] = "no price provider is configured"
        return
    now = datetime.now(UTC)
    async with session_scope() as session:
        targets, missing = await select_targets(session, to_eastern(now).date())
    pacers = {"yfinance": TokenBucket(YAHOO_REQUESTS_PER_SECOND, burst=1)}
    report = await refresh_prices(targets, chain, now=now, history_days=settings.price_history_days,
                                  pacers=pacers)
    ctx.rows_new += report.rows_new
    ctx.rows_updated += report.rows_updated
    ctx.docs_fetched += sum(report.requests.values())
    # Counted as failures so the run shows "partial" on /status. So is a run that left the
    # prices out of date (mostly_stale): the provider stopped updating, and "ok" would hide that.
    ctx.parse_failures += report.failed
    if mostly_stale(report):
        ctx.parse_failures += 1
        ctx.stats["mostly_stale"] = True
    ctx.stats.update(report.as_stats())
    ctx.stats["benchmarks_missing"] = missing
    log.info("prices.refreshed", **{k: v for k, v in report.as_stats().items() if k != "samples"})
