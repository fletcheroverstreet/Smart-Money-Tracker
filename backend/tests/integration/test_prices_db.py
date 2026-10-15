"""Prices against the test database: the upsert, the nightly run, and the private endpoint.

Providers here are small fakes that return fixed bars, so nothing reads
finlake's real folder or reaches Yahoo. Every price row is a test fixture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text

from smt.api.app import create_app
from smt.config import reset_settings_cache
from smt.db.models import PriceDaily
from smt.db.session import session_scope
from smt.domain.business_days import ET
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.prices import jobs as price_jobs
from smt.prices.base import (
    YAHOO_PERSONAL_USE,
    Bar,
    Licence,
    ProviderError,
    ProviderRateLimited,
    Split,
)
from smt.prices.jobs import Target, refresh_prices, select_targets
from smt.prices.loader import upsert_bars
from smt.prices.queries import PUBLIC_ONLY
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership
from smt.worker.scheduler import JobContext

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
PUBLIC_LICENCE = Licence(tag="public-display-ok", display_public=True)


def bar(day: date, close: str, adj: str | None = None, volume: int | None = 100) -> Bar:
    return Bar(day=day, open=None, high=None, low=None, close=Decimal(close),
               adj_close=None if adj is None else Decimal(adj), volume=volume)


async def add_security(ticker: str, *, alias: str | None = None) -> int:
    """A security row (and optionally a current ticker row in security_ids)."""
    async with session_scope() as s:
        security_id = int((await s.execute(text(
            "INSERT INTO securities (name, primary_ticker, sector_source) "
            "VALUES (:n, :t, 'unmapped') RETURNING id"), {"n": f"{ticker} test", "t": ticker})).scalar_one())
        if alias:
            await s.execute(text(
                "INSERT INTO security_ids (security_id, scheme, value, source) "
                "VALUES (:id, 'ticker', :v, 'test')"), {"id": security_id, "v": alias})
    return security_id


async def count_rows() -> int:
    async with session_scope() as s:
        return int((await s.execute(select(func.count()).select_from(PriceDaily))).scalar_one())


# ---------------------------------------------------------------- the upsert

async def test_upsert_is_idempotent_and_only_rewrites_changed_rows() -> None:
    security_id = await add_security("ABC")
    bars = [bar(date(2026, 9, 14), "100", "99.5"), bar(date(2026, 9, 15), "101", None, volume=None)]
    async with session_scope() as s:
        first = await upsert_bars(s, security_id=security_id, provider="finlake",
                                  licence=YAHOO_PERSONAL_USE, bars=bars)
    assert (first.new, first.updated, first.unchanged) == (2, 0, 0)

    async with session_scope() as s:
        again = await upsert_bars(s, security_id=security_id, provider="finlake",
                                  licence=YAHOO_PERSONAL_USE, bars=bars)
    # NULL adj_close and NULL volume count as "the same" (IS DISTINCT FROM).
    assert (again.new, again.updated, again.unchanged) == (0, 0, 2)

    corrected = [bars[0], bar(date(2026, 9, 15), "101.2", None, volume=None)]
    async with session_scope() as s:
        third = await upsert_bars(s, security_id=security_id, provider="finlake",
                                  licence=YAHOO_PERSONAL_USE, bars=corrected)
    assert (third.new, third.updated, third.unchanged) == (0, 1, 1)
    assert await count_rows() == 2

    async with session_scope() as s:
        rows = (await s.execute(select(PriceDaily.day, PriceDaily.close, PriceDaily.display_public,
                                       PriceDaily.provider).order_by(PriceDaily.day))).tuples().all()
    assert [(r[0], r[1]) for r in rows] == [(date(2026, 9, 14), Decimal("100")),
                                            (date(2026, 9, 15), Decimal("101.2"))]
    assert all(r[2] is False and r[3] == "finlake" for r in rows)  # licence flag and provider stored


async def test_public_filter_never_returns_personal_use_rows() -> None:
    security_id = await add_security("ABC")
    async with session_scope() as s:
        await upsert_bars(s, security_id=security_id, provider="finlake", licence=YAHOO_PERSONAL_USE,
                          bars=[bar(date(2026, 9, 14), "100")])
        await upsert_bars(s, security_id=security_id, provider="licensed", licence=PUBLIC_LICENCE,
                          bars=[bar(date(2026, 9, 14), "100.1")])
    async with session_scope() as s:
        public = (await s.execute(select(PriceDaily.provider).where(PUBLIC_ONLY))).scalars().all()
    assert public == ["licensed"]


# ---------------------------------------------------------------- the nightly run

class FakeProvider:
    """A provider that returns canned bars (or raises), and records its calls."""

    def __init__(self, name: str, bars: list[Bar] | None = None, error: Exception | None = None) -> None:
        self.name = name
        self.licence = YAHOO_PERSONAL_USE
        self.bars = bars or []
        self.error = error
        self.calls: list[tuple[str, date, date]] = []

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        self.calls.append((symbol, start, end))
        if self.error is not None:
            raise self.error
        return [b for b in self.bars if start <= b.day <= end]


#: Thursday 24 Sep 2026, 18:30 ET: the job's slot. The last completed session is that day.
NOW = datetime(2026, 9, 24, 18, 30, tzinfo=ET)
WEEK = [date(2026, 9, d) for d in (21, 22, 23, 24)]


async def test_refresh_uses_the_first_current_provider_and_falls_back() -> None:
    abc, xyz, gone = await add_security("ABC"), await add_security("XYZ"), await add_security("GONE")
    stale = FakeProvider("finlake", [bar(d, "10") for d in WEEK[:2]])   # stops on the 22nd
    current = FakeProvider("yfinance", [bar(d, "11") for d in WEEK])
    report = await refresh_prices(
        [Target(abc, "ABC"), Target(xyz, "XYZ"), Target(gone, "GONE")],
        [stale, current], now=NOW, history_days=30)
    # Every target got the current provider (the fakes don't know tickers apart); GONE included.
    assert report.current == {"yfinance": 3}
    assert report.rows_new == 12
    assert report.stale == 0
    assert report.failed == 0
    async with session_scope() as s:
        providers = set((await s.execute(select(PriceDaily.provider))).scalars())
    assert providers == {"yfinance"}  # the stale provider's bars were not stored alongside

    # Second night: nothing new, so nothing is written; the overlap window starts 30 days back.
    again = await refresh_prices([Target(abc, "ABC")], [current], now=NOW, history_days=30)
    assert (again.rows_new, again.rows_updated) == (0, 0)
    assert current.calls[-1] == ("ABC", date(2026, 8, 25), date(2026, 9, 24))


async def test_refresh_keeps_stale_prices_and_reports_failures() -> None:
    abc = await add_security("ABC")
    stale = FakeProvider("finlake", [bar(d, "10") for d in WEEK[:2]])
    broken = FakeProvider("yfinance", error=ProviderError("network down"))
    report = await refresh_prices([Target(abc, "ABC")], [stale, broken], now=NOW, history_days=30)
    assert report.stale == 1
    assert report.current == {}
    assert report.samples["stale"] == ["ABC (2026-09-22)"]
    assert await count_rows() == 2

    nothing = await refresh_prices([Target(abc, "ABC")], [broken], now=NOW, history_days=30)
    assert nothing.failed == 1
    assert nothing.unavailable == 0
    empty = await refresh_prices([Target(abc, "ABC")], [FakeProvider("finlake")], now=NOW, history_days=30)
    assert empty.unavailable == 1
    assert empty.failed == 0


async def test_a_rate_limit_stops_that_provider_for_the_rest_of_the_run() -> None:
    abc, xyz = await add_security("ABC"), await add_security("XYZ")
    limited = FakeProvider("yfinance", error=ProviderRateLimited("429"))
    report = await refresh_prices([Target(abc, "ABC"), Target(xyz, "XYZ")], [limited],
                                  now=NOW, history_days=30)
    assert len(limited.calls) == 1  # XYZ never asked
    assert report.rate_limited == ["yfinance"]
    assert report.failed == 2


async def test_bars_on_market_holidays_are_dropped() -> None:
    abc = await add_security("ABC")
    # Saturday 19 Sep and Labor Day (7 Sep) had no NYSE session.
    odd = FakeProvider("finlake", [bar(date(2026, 9, 7), "9"), bar(date(2026, 9, 19), "9")]
                       + [bar(d, "10") for d in WEEK])
    report = await refresh_prices([Target(abc, "ABC")], [odd], now=NOW, history_days=30)
    assert report.off_calendar_dropped == 2
    assert report.rows_new == 4


async def test_a_split_reloads_the_whole_history() -> None:
    abc = await add_security("ABC")
    old_days = [date(2026, 8, 3) + timedelta(days=i) for i in range(0, 60) if
                (date(2026, 8, 3) + timedelta(days=i)).weekday() < 5 and date(2026, 8, 3) + timedelta(days=i)
                != date(2026, 9, 7)]
    before = FakeProvider("finlake", [bar(d, "100") for d in old_days if d <= date(2026, 9, 23)])
    await refresh_prices([Target(abc, "ABC")], [before], now=NOW, history_days=60)
    # A 2-for-1 split: the provider now reports every earlier close halved.
    after = FakeProvider("finlake", [bar(d, "50") for d in old_days])
    report = await refresh_prices([Target(abc, "ABC")], [after], now=NOW, history_days=60)
    assert report.reloaded == 1
    assert len(after.calls) == 2
    assert after.calls[1][1] <= old_days[0]
    async with session_scope() as s:
        closes = set((await s.execute(select(PriceDaily.close))).scalars())
    assert closes == {Decimal("50")}  # no row left on the old basis


class SplitProvider(FakeProvider):
    """A fake that also reports the splits it applied (as finlake does), or None (unknown)."""

    def __init__(self, name: str, bars: list[Bar], splits: list[Split] | None) -> None:
        super().__init__(name, bars)
        self.splits = splits

    def get_splits(self, symbol: str) -> list[Split] | None:
        return self.splits


async def test_the_run_records_the_splits_the_provider_applied() -> None:
    """The value checks trust a close fetched long after its session only when the provider's
    splits were read with it (quality.flag_totals_as_price)."""
    abc, xyz, plain = await add_security("ABC"), await add_security("XYZ"), await add_security("PLN")
    split = [Split(ex_date=date(2026, 7, 2), ratio=Decimal("4"))]
    await refresh_prices([Target(abc, "ABC")], [SplitProvider("finlake", [bar(d, "10") for d in WEEK], split)],
                         now=NOW, history_days=30)
    await refresh_prices([Target(xyz, "XYZ")], [SplitProvider("finlake", [bar(d, "10") for d in WEEK], None)],
                         now=NOW, history_days=30)
    await refresh_prices([Target(plain, "PLN")], [FakeProvider("yfinance", [bar(d, "10") for d in WEEK])],
                         now=NOW, history_days=30)
    async with session_scope() as s:
        splits = (await s.execute(text("SELECT security_id, provider, ex_date, ratio FROM price_splits"))).all()
        checks = (await s.execute(text(
            "SELECT c.security_id, bool_and(c.checked_at >= d.fetched_at) FROM price_split_checks c "
            "JOIN prices_daily d ON d.security_id = c.security_id AND d.provider = c.provider "
            "GROUP BY c.security_id"))).all()
    assert [tuple(r) for r in splits] == [(abc, "finlake", date(2026, 7, 2), Decimal("4"))]
    # Checked for ABC, after its bars were fetched; XYZ's splits were unknown and PLN's provider
    # reports none, so neither has a check.
    assert [tuple(r) for r in checks] == [(abc, True)]


async def test_the_job_is_a_clean_no_op_without_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICE_PROVIDERS", "")
    reset_settings_cache()
    try:
        ctx = JobContext(job_name="prices.refresh_daily", run_id=1, cursor={})
        await price_jobs.refresh_daily(ctx)
    finally:
        monkeypatch.delenv("PRICE_PROVIDERS")
        reset_settings_cache()
    assert ctx.stats == {"providers": [], "skipped": "no price provider is configured"}
    assert (ctx.rows_new, ctx.docs_fetched, ctx.parse_failures) == (0, 0, 0)
    assert await count_rows() == 0


@pytest.mark.parametrize(("report", "partial"), [
    # Run 14236 (2026-09-28): 0 current, 676 stale, 4,423 unavailable of 5,099. Was "ok".
    (price_jobs.RefreshReport(targets=5099, stale=676, unavailable=4423), True),
    # Targets, but no ticker any provider has: nothing current, nothing stale.
    (price_jobs.RefreshReport(targets=7, unavailable=7), True),
    # Nothing to price at all.
    (price_jobs.RefreshReport(targets=0), False),
    # Mostly current.
    (price_jobs.RefreshReport(targets=10, current={"finlake": 8}, stale=1, unavailable=1), False),
])
async def test_the_job_is_partial_when_it_left_prices_out_of_date(
        monkeypatch: pytest.MonkeyPatch, report: price_jobs.RefreshReport, partial: bool) -> None:
    """The scheduler marks a run "partial" when parse_failures > 0 (Scheduler._run), so the
    job adds one for a run that left the prices out of date."""
    async def targets(_session: object, _today: date) -> tuple[list[Target], list[str]]:
        return [], []

    async def refresh(*_args: object, **_kwargs: object) -> price_jobs.RefreshReport:
        return report

    monkeypatch.setattr(price_jobs, "provider_chain", lambda _settings: [FakeProvider("finlake")])
    monkeypatch.setattr(price_jobs, "select_targets", targets)
    monkeypatch.setattr(price_jobs, "refresh_prices", refresh)
    ctx = JobContext(job_name="prices.refresh_daily", run_id=1, cursor={})
    await price_jobs.refresh_daily(ctx)
    assert (ctx.parse_failures > 0) is partial
    assert ctx.stats.get("mostly_stale", False) is partial
    assert ctx.stats["targets"] == report.targets


async def test_targets_are_benchmarks_then_securities_with_recent_filings() -> None:
    spy = await add_security("SPY")
    await add_security("IDLE")  # no filings: not priced
    acc = "0001193125-26-396718"  # a real Form 4 fixture (Dell), filed 21 Sep 2026
    async with session_scope() as s:
        doc = parse_ownership((FIX / "ownership" / f"{acc}.xml").read_bytes())
        await load_ownership(s, FilingMeta(
            accession=acc, form_type="4", filed_on=date(2026, 9, 21), accepted_at=None,
            url=f"https://www.sec.gov/x/{acc}-index.htm", primary_doc_url=f"https://www.sec.gov/x/{acc}.xml",
            detected_via="feed", parser_version=PARSER_VERSION), doc)
    async with session_scope() as s:
        targets, missing = await select_targets(s, date(2026, 9, 24))
        later, _ = await select_targets(s, date(2027, 3, 1))  # the filing is now > 90 days old
    symbols = [t.symbol for t in targets]
    assert symbols[0] == "SPY"
    assert targets[0].security_id == spy
    assert "DELL" in symbols
    assert "IDLE" not in symbols
    assert "XLK" in missing
    assert "SPY" not in missing
    assert [t.symbol for t in later] == ["SPY"]


# ---------------------------------------------------------------- the private endpoint

@pytest.fixture
def private_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PRIVATE_MODE_PASSPHRASE", "correct horse")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    reset_settings_cache()
    yield
    monkeypatch.delenv("PRIVATE_MODE_PASSPHRASE")
    monkeypatch.delenv("SESSION_SECRET")
    reset_settings_cache()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def store_recent_prices() -> date:
    """Two providers' closes for BRK-B over the last 10 days; yfinance's run one day further."""
    today = datetime.now(ET).date()
    security_id = await add_security("BRK-B", alias="BRK.B")
    days = [today - timedelta(days=n) for n in range(10, 0, -1)]
    async with session_scope() as s:
        await upsert_bars(s, security_id=security_id, provider="finlake", licence=YAHOO_PERSONAL_USE,
                          bars=[bar(d, "480") for d in days[:-1]])
        await upsert_bars(s, security_id=security_id, provider="yfinance", licence=YAHOO_PERSONAL_USE,
                          bars=[bar(d, "481.25") for d in days])
    return today


async def test_prices_endpoint_refuses_without_a_private_session(client: httpx.AsyncClient,
                                                                private_mode: None) -> None:
    await store_recent_prices()
    answer = await client.get("/api/private/prices/BRK.B")
    assert answer.status_code == 403
    assert "480" not in answer.text
    assert "481" not in answer.text
    assert answer.headers["cache-control"] == "private, no-store"
    client.cookies.set("smt_private", "99999999999.forged")
    assert (await client.get("/api/private/prices/BRK.B")).status_code == 403


async def test_prices_endpoint_refuses_when_private_mode_is_not_configured(client: httpx.AsyncClient) -> None:
    await store_recent_prices()
    assert (await client.get("/api/private/prices/BRK.B")).status_code == 403


async def test_prices_endpoint_serves_one_providers_closes(client: httpx.AsyncClient, private_mode: None) -> None:
    today = await store_recent_prices()
    assert (await client.post("/api/private/login", json={"passphrase": "correct horse"})).status_code == 200

    answer = await client.get("/api/private/prices/brk.b")
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "private, no-store"
    assert "Cookie" in answer.headers["vary"]
    body = answer.json()
    # yfinance has the newer bar, so its series is served whole (never mixed with finlake's).
    assert body["symbol"] == "BRK.B"
    assert body["provider"] == "yfinance"
    assert body["licence"] == "personal-use-only"
    assert [b["day"] for b in body["bars"]] == [
        (today - timedelta(days=n)).isoformat() for n in range(10, 0, -1)]
    assert {Decimal(b["close"]) for b in body["bars"]} == {Decimal("481.25")}

    short = (await client.get("/api/private/prices/BRK-B", params={"days": 3})).json()
    assert [b["day"] for b in short["bars"]] == [(today - timedelta(days=n)).isoformat() for n in (3, 2, 1)]

    missing = await client.get("/api/private/prices/NOPE")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "private, no-store"
    await add_security("EMPTY")
    assert (await client.get("/api/private/prices/EMPTY")).status_code == 404
    assert (await client.get("/api/private/prices/BRK.B", params={"days": 0})).status_code == 422
