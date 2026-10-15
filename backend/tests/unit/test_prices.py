"""Price providers without a database: cleaning rules, finlake's reader, yfinance's adapter.

Every finlake file here is a tiny fixture written into pytest's tmp_path.
The real ~/.finlake is never read, and yfinance is never imported (its
fetch function is replaced by a fake).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from smt.config import Settings, reset_settings_cache
from smt.domain.business_days import ET
from smt.prices.base import (
    Bar,
    PriceProvider,
    ProviderError,
    Split,
    clean_price,
    clean_volume,
    provider_symbol,
    session_is_final,
)
from smt.prices.finlake import FinlakeProvider, dividend_factors
from smt.prices.jobs import (
    RefreshReport,
    last_completed_session,
    mostly_stale,
    private_mode_configured,
    provider_chain,
)
from smt.prices.loader import history_was_rebased
from smt.prices.yahoo import YFinanceProvider, fetch_yahoo_history, rows_from_frame
from smt.worker.jobs import all_jobs
from smt.worker.schedules import DailyAt

NAN = float("nan")


# ---------------------------------------------------------------- cleaning rules

def test_provider_symbol_uses_yahoo_spelling_and_refuses_paths() -> None:
    assert provider_symbol(" brk.b ") == "BRK-B"
    assert provider_symbol("BRK/B") == "BRK-B"
    assert provider_symbol("SPY") == "SPY"
    for bad in ("../secret", "..", "A B", "C:\\x", "", "X" * 30):
        assert provider_symbol(bad) is None


def test_missing_or_impossible_prices_are_none_never_zero() -> None:
    for missing in (None, NAN, float("inf"), 0, 0.0, -1.5, True, "12.5", object()):
        assert clean_price(missing) is None
    # Yahoo's 32-bit float noise is rounded away.
    assert clean_price(335.20001220703125) == Decimal("335.2")
    assert clean_price(0.12345678) == Decimal("0.1235")
    assert clean_price(12.3456789, places=6) == Decimal("12.345679")


def test_volume_keeps_zero_but_not_missing_or_negative() -> None:
    assert clean_volume(0) == 0
    assert clean_volume(1_500_000.0) == 1_500_000
    for missing in (None, NAN, -3, "100"):
        assert clean_volume(missing) is None


def test_a_bar_read_during_the_session_is_not_final() -> None:
    day = date(2026, 9, 21)  # a Monday session
    assert not session_is_final(day, datetime(2026, 9, 21, 14, 35, tzinfo=ET))  # finlake's real write time
    assert not session_is_final(day, datetime(2026, 9, 21, 16, 10, tzinfo=ET))  # still settling
    assert session_is_final(day, datetime(2026, 9, 21, 16, 25, tzinfo=ET))
    assert session_is_final(day, datetime(2026, 9, 22, 9, 0, tzinfo=ET))
    assert session_is_final(date(2026, 9, 19), datetime(2026, 9, 19, 9, 0, tzinfo=ET))  # Saturday: no session


def test_last_completed_session() -> None:
    thursday_noon = datetime(2026, 9, 24, 12, 40, tzinfo=ET)
    assert last_completed_session(thursday_noon) == date(2026, 9, 23)
    assert last_completed_session(datetime(2026, 9, 24, 16, 1, tzinfo=ET)) == date(2026, 9, 24)
    assert last_completed_session(datetime(2026, 9, 26, 10, 0, tzinfo=ET)) == date(2026, 9, 25)  # Saturday
    # Monday morning after Thanksgiving week's Friday half-day.
    assert last_completed_session(datetime(2026, 11, 30, 8, 0, tzinfo=ET)) == date(2026, 11, 27)
    assert last_completed_session(datetime(2026, 9, 24, 16, 30, tzinfo=UTC)) == date(2026, 9, 23)  # 12:30 ET


# ---------------------------------------------------------------- dividend adjustment

def test_dividend_factors_scale_only_closes_before_the_ex_date() -> None:
    days = [date(2026, 9, d) for d in (14, 15, 16, 17)]
    closes = [100.0, 100.0, 99.6, 101.0]
    factors = dividend_factors(days, closes, [(date(2026, 9, 16), 0.5)])
    # 1 - 0.50 / 100.00 (the close the day before the ex-date) = 0.995
    assert factors == pytest.approx([0.995, 0.995, 1.0, 1.0])


def test_dividend_factors_multiply_and_ignore_out_of_range_or_absurd_dividends() -> None:
    days = [date(2026, 9, d) for d in (14, 15, 16, 17)]
    closes = [100.0, 50.0, 50.0, 50.0]
    dividends = [
        (date(2026, 9, 1), 1.0),    # before the first bar: no bar before it to scale
        (date(2026, 9, 15), 1.0),   # 1 - 1/100 = 0.99
        (date(2026, 9, 17), 2.0),   # 1 - 2/50 = 0.96
        (date(2026, 9, 30), 1.0),   # after the last bar: not happened yet in this series
        (date(2026, 9, 16), 60.0),  # bigger than the price: a data error, ignored
    ]
    factors = dividend_factors(days, closes, dividends)
    assert factors == pytest.approx([0.99 * 0.96, 0.96, 0.96, 1.0])


# ---------------------------------------------------------------- finlake fixture

FIXTURE_ROWS: list[tuple[str, float | None, float | None, float | None, float | None, int | None]] = [
    ("2026-09-11", 98.0, 99.0, 97.0, NAN, 1000),      # no close: not a bar
    ("2026-09-14", 99.5, 100.5, 99.0, 100.0, 5000),
    ("2026-09-15", 0.0, 100.8, 99.1, 100.0, 6000),    # open of 0: missing, not $0
    ("2026-09-16", 99.2, 99.9, 99.0, 99.6, None),     # ex-dividend day; volume unknown
    ("2026-09-17", 99.7, 101.2, 99.5, 101.0, 7000),
    ("2026-09-18", 101.0, 102.4, 100.9, 102.0, 8000),
    ("2026-09-21", 102.0, 103.0, 101.5, 102.7, 1200),  # written at 14:35 ET: intraday, not a close
]
#: When finlake last wrote its price files (from its real store: 2026-09-21 13:35 CDT).
WRITTEN_AT = datetime(2026, 9, 21, 14, 35, tzinfo=ET)


def write_parquet(path: Path, rows: list[tuple[object, ...]]) -> None:
    """A finlake-shaped price file (same columns and types as finlake writes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE bars (date VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, "
                    "close DOUBLE, volume BIGINT)")
        con.executemany("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?)", rows)
        con.execute(f"COPY bars TO '{path.as_posix()}' (FORMAT parquet)")
    finally:
        con.close()
    stamp = WRITTEN_AT.timestamp()
    os.utime(path, (stamp, stamp))


def write_actions_db(path: Path, actions: list[tuple[str, str, str, float]]) -> None:
    """A finlake-shaped SQLite file with its corp_actions table."""
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE corp_actions (ticker TEXT NOT NULL, date TEXT NOT NULL, "
                    "kind TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY (ticker, date, kind))")
        con.executemany("INSERT INTO corp_actions VALUES (?, ?, ?, ?)", actions)
        con.commit()
    finally:
        con.close()


@pytest.fixture
def finlake_home(tmp_path: Path) -> Path:
    home = tmp_path / "finlake"
    write_parquet(home / "prices" / "ABC.parquet", list(FIXTURE_ROWS))
    write_parquet(home / "prices" / "BRK-B.parquet", [("2026-09-18", 1.0, 1.0, 1.0, 480.0, 10)])
    write_actions_db(home / "finlake.db", [
        ("ABC", "2026-09-16", "dividend", 0.5),
        ("ABC", "2020-06-01", "split", 2.0),   # splits are already in finlake's closes
        ("XYZ", "2026-09-15", "dividend", 9.0),
    ])
    return home


def files_under(folder: Path) -> set[str]:
    return {str(p.relative_to(folder)) for p in folder.rglob("*")}


def test_finlake_reads_bars_and_adjusts_for_dividends(finlake_home: Path) -> None:
    before = files_under(finlake_home)
    provider = FinlakeProvider(home=finlake_home)
    bars = provider.get_daily_bars("abc", date(2026, 9, 1), date(2026, 9, 30))

    assert [b.day for b in bars] == [date(2026, 9, d) for d in (14, 15, 16, 17, 18)]
    first, second, exdiv = bars[0], bars[1], bars[2]
    assert first.close == Decimal("100.0")
    assert first.adj_close == Decimal("99.5")        # 100 x (1 - 0.5/100)
    assert exdiv.adj_close == exdiv.close             # on/after the ex-date: unchanged
    assert second.open is None                        # 0 in the file means missing
    assert exdiv.volume is None
    assert bars[-1].day == date(2026, 9, 18)          # the intraday 21 Sep bar is left out
    # Reading changed nothing in finlake's folder (no -wal/-shm, no temp files).
    assert files_under(finlake_home) == before


def test_finlake_window_is_inclusive_and_adjustment_uses_the_whole_file(finlake_home: Path) -> None:
    bars = FinlakeProvider(home=finlake_home).get_daily_bars("ABC", date(2026, 9, 14), date(2026, 9, 14))
    # The dividend on 16 Sep lies after the window but still adjusts 14 Sep.
    assert [(b.day, b.adj_close) for b in bars] == [(date(2026, 9, 14), Decimal("99.5"))]


def test_finlake_share_class_ticker_and_unknown_ticker(finlake_home: Path) -> None:
    provider = FinlakeProvider(home=finlake_home)
    assert [b.close for b in provider.get_daily_bars("BRK.B", date(2026, 9, 1), date(2026, 9, 30))] == [
        Decimal("480.0")]
    assert provider.get_daily_bars("NOPE", date(2026, 9, 1), date(2026, 9, 30)) == []
    assert provider.get_daily_bars("../finlake", date(2026, 9, 1), date(2026, 9, 30)) == []


def test_finlake_reports_the_splits_it_applied(finlake_home: Path) -> None:
    """finlake records each split from the download that re-bases the closes (corp_actions kind
    'split'), read only; the value checks use them to tell as-traded closes from re-based ones."""
    before = files_under(finlake_home)
    provider = FinlakeProvider(home=finlake_home)
    assert provider.get_splits("abc") == [Split(ex_date=date(2020, 6, 1), ratio=Decimal("2.0"))]
    assert provider.get_splits("XYZ") == []          # dividends only: no splits
    assert provider.get_splits("../finlake") is None
    assert files_under(finlake_home) == before
    (finlake_home / "finlake.db").unlink()
    assert provider.get_splits("ABC") is None        # unknown, not "no splits"


def test_finlake_without_its_database_leaves_adj_close_unknown(finlake_home: Path) -> None:
    (finlake_home / "finlake.db").unlink()
    bars = FinlakeProvider(home=finlake_home).get_daily_bars("ABC", date(2026, 9, 1), date(2026, 9, 30))
    assert len(bars) == 5
    assert all(b.adj_close is None for b in bars)  # unknown, not "no dividends"


def test_finlake_reads_a_database_another_program_has_open(finlake_home: Path) -> None:
    db = finlake_home / "finlake.db"
    writer = sqlite3.connect(db)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO corp_actions VALUES ('ABC', '2026-09-18', 'dividend', 1.01)")
        writer.commit()  # committed to the write-ahead log, not yet to the main file
        assert (finlake_home / "finlake.db-shm").exists()
        bars = FinlakeProvider(home=finlake_home).get_daily_bars("ABC", date(2026, 9, 1), date(2026, 9, 30))
    finally:
        writer.close()
    by_day = {b.day: b for b in bars}
    # 17 Sep is now before the 18 Sep ex-date: 101 x (1 - 1.01/101) = 99.99
    assert by_day[date(2026, 9, 17)].adj_close == Decimal("99.99")


def test_finlake_open_database_with_an_empty_log_is_read_without_touching_its_files(
        finlake_home: Path) -> None:
    # What finlake's real folder looks like while finlake is idle but running:
    # an empty -wal and an -shm index next to the database.
    db = finlake_home / "finlake.db"
    (finlake_home / "finlake.db-wal").write_bytes(b"")
    shm = finlake_home / "finlake.db-shm"
    shm.write_bytes(b"\0" * 32768)
    os.utime(shm, (1_000_000_000, 1_000_000_000))
    before = {p.name: (p.stat().st_mtime, p.stat().st_size) for p in finlake_home.iterdir() if p.is_file()}
    bars = FinlakeProvider(home=finlake_home).get_daily_bars("ABC", date(2026, 9, 1), date(2026, 9, 30))
    assert bars[0].adj_close == Decimal("99.5")  # the dividend was read
    after = {p.name: (p.stat().st_mtime, p.stat().st_size) for p in finlake_home.iterdir() if p.is_file()}
    assert after == before  # not even the -shm lock index was touched
    assert db.exists()


def test_finlake_unreadable_files_mean_not_available(finlake_home: Path) -> None:
    provider = FinlakeProvider(home=finlake_home)
    window = (date(2026, 9, 1), date(2026, 9, 30))
    # A damaged price file.
    (finlake_home / "prices" / "BAD.parquet").write_bytes(b"not a parquet file")
    assert provider.get_daily_bars("BAD", *window) == []
    # A damaged database: no bars rather than bars with blank adjusted prices.
    (finlake_home / "finlake.db").write_bytes(b"not a database" * 100)
    assert provider.get_daily_bars("ABC", *window) == []


def test_finlake_refuses_a_write_ahead_log_without_its_index(finlake_home: Path) -> None:
    # Pending changes in the log but no index: what a crashed writer leaves.
    (finlake_home / "finlake.db-wal").write_bytes(b"\x01" * 64)
    before = files_under(finlake_home)
    assert FinlakeProvider(home=finlake_home).get_daily_bars("ABC", date(2026, 9, 1), date(2026, 9, 30)) == []
    assert files_under(finlake_home) == before  # SQLite was never asked to create an -shm file


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Change environment variables for one test, then forget the cached settings."""
    yield monkeypatch
    monkeypatch.undo()
    reset_settings_cache()


def test_finlake_home_comes_from_settings_when_used(finlake_home: Path, settings_env: pytest.MonkeyPatch) -> None:
    provider = FinlakeProvider()  # created BEFORE the setting changes
    settings_env.setenv("FINLAKE_HOME", str(finlake_home))
    reset_settings_cache()
    assert provider.home() == finlake_home
    assert len(provider.get_daily_bars("ABC", date(2026, 9, 1), date(2026, 9, 30))) == 5


def test_finlake_refuses_the_real_home_during_tests(tmp_path: Path, settings_env: pytest.MonkeyPatch) -> None:
    # Pretend tmp_path is the user's home, so the real ~/.finlake is never touched.
    fake_home = tmp_path / "user"
    (fake_home / ".finlake").mkdir(parents=True)
    settings_env.setattr(Path, "home", classmethod(lambda cls: fake_home))
    settings_env.setenv("FINLAKE_HOME", str(fake_home / ".finlake"))
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="REAL"):
        FinlakeProvider().get_daily_bars("ABC", date(2026, 9, 1), date(2026, 9, 30))


# ---------------------------------------------------------------- yfinance adapter (fake fetcher)

def test_yfinance_provider_cleans_rows_and_drops_todays_unfinished_bar() -> None:
    calls: list[tuple[str, date, date]] = []

    def fake_fetch(ticker: str, start: date, end_exclusive: date) -> list[dict[str, object]]:
        calls.append((ticker, start, end_exclusive))
        return [
            {"day": date(2026, 9, 21), "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
             "adj_close": 10.2500001, "volume": 1000.0},
            {"day": date(2026, 9, 22), "open": NAN, "high": 11.5, "low": 10.0, "close": 11.0,
             "adj_close": NAN, "volume": NAN},
            {"day": date(2026, 9, 23), "open": 11.0, "high": 11.2, "low": 10.8, "close": NAN,
             "adj_close": NAN, "volume": 5.0},           # no close: dropped
            {"day": date(2026, 9, 24), "open": 11.0, "high": 11.4, "low": 10.9, "close": 11.3,
             "adj_close": 11.3, "volume": 700.0},         # today, mid-session: dropped
            {"day": date(2026, 8, 1), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
             "adj_close": 1.0, "volume": 1.0},            # outside the window: dropped
        ]

    provider = YFinanceProvider(fetch=fake_fetch, clock=lambda: datetime(2026, 9, 24, 12, 40, tzinfo=ET))
    bars = provider.get_daily_bars("brk.b", date(2026, 9, 21), date(2026, 9, 24))
    assert calls == [("BRK-B", date(2026, 9, 21), date(2026, 9, 25))]  # yfinance's end is exclusive
    assert bars == [
        Bar(day=date(2026, 9, 21), open=Decimal("10.0"), high=Decimal("11.0"), low=Decimal("9.5"),
            close=Decimal("10.5"), adj_close=Decimal("10.25"), volume=1000),
        Bar(day=date(2026, 9, 22), open=None, high=Decimal("11.5"), low=Decimal("10.0"),
            close=Decimal("11.0"), adj_close=None, volume=None),
    ]


def test_providers_declare_a_personal_use_licence() -> None:
    for provider in (FinlakeProvider(home=Path("unused")), YFinanceProvider(fetch=lambda *_: [])):
        assert isinstance(provider, PriceProvider)
        assert provider.licence.tag == "personal-use-only"
        assert provider.licence.display_public is False


def test_rows_from_frame_reads_a_yfinance_dataframe() -> None:
    pd = pytest.importorskip("pandas")
    index = pd.DatetimeIndex(["2026-09-21", "2026-09-22"]).tz_localize("America/New_York")
    frame = pd.DataFrame({"Open": [1.0, 2.0], "High": [1.5, 2.5], "Low": [0.5, 1.5], "Close": [1.2, NAN],
                          "Adj Close": [1.1, NAN], "Volume": [100, 200]}, index=index)
    rows = rows_from_frame(frame)
    assert rows[0]["day"] == date(2026, 9, 21)
    assert rows[0]["adj_close"] == 1.1
    assert rows_from_frame(pd.DataFrame()) == []


def test_real_yfinance_fetch_is_blocked_in_tests() -> None:
    with pytest.raises(ProviderError, match="no network"):
        fetch_yahoo_history("SPY", date(2026, 9, 1), date(2026, 9, 2))
    assert "yfinance" not in sys.modules  # never even imported


# ---------------------------------------------------------------- chain and re-base detection

def test_yfinance_joins_the_chain_only_in_private_mode() -> None:
    public = Settings(price_providers="finlake, yfinance, nonsense", private_mode_passphrase="",
                      session_secret="")
    assert not private_mode_configured(public)
    assert [p.name for p in provider_chain(public)] == ["finlake"]
    private = Settings(price_providers="finlake,yfinance", private_mode_passphrase="pass",
                       session_secret="secret")
    assert [p.name for p in provider_chain(private)] == ["finlake", "yfinance"]
    assert provider_chain(Settings(price_providers="", private_mode_passphrase="", session_secret="")) == []


def _bar(day: int, close: str, adj: str | None) -> Bar:
    return Bar(day=date(2026, 9, day), open=None, high=None, low=None, close=Decimal(close),
               adj_close=None if adj is None else Decimal(adj), volume=None)


def test_rebase_detection() -> None:
    stored = {date(2026, 9, 14): (Decimal("100"), Decimal("99")),
              date(2026, 9, 15): (Decimal("100"), Decimal("99"))}
    same = [_bar(14, "100", "99"), _bar(15, "100.4", "99.396"), _bar(16, "101", "101")]
    assert not history_was_rebased(same, stored)                    # a small late correction
    split = [_bar(14, "50", "49.5"), _bar(15, "50", "49.5")]
    assert history_was_rebased(split, stored)                       # every close halved
    dividend = [_bar(14, "100", "98.5"), _bar(15, "100", "98.5")]
    assert history_was_rebased(dividend, stored)                    # adjusted basis moved
    lost_adj = [_bar(14, "100", None)]
    assert history_was_rebased(lost_adj, stored)
    assert not history_was_rebased([_bar(20, "1", "1")], stored)    # no overlap, nothing to compare


def test_the_job_is_registered_after_the_close_on_trading_days() -> None:
    job = next(j for j in all_jobs() if j.name == "prices.refresh_daily")
    assert isinstance(job.schedule, DailyAt)
    assert (job.schedule.at.hour, job.schedule.at.minute, job.schedule.days) == (18, 30, "trading")


def test_a_run_with_nothing_current_is_not_ok() -> None:
    """Run 14236 (2026-09-28) finished "ok" with 0 series current, 676 stale and 4,423 tickers no
    provider has, out of 5,099: stale was under half of all targets. Unavailable tickers say
    nothing about whether the provider keeps up, so the run is judged on the rest."""
    run_14236 = RefreshReport(targets=5099, current={}, stale=676, unavailable=4423)
    assert mostly_stale(run_14236)
    # Nothing priced at all, not even a stale series: still not a clean run.
    assert mostly_stale(RefreshReport(targets=10, unavailable=10))
    # More stale than current.
    assert mostly_stale(RefreshReport(targets=100, current={"finlake": 40}, stale=41, unavailable=19))
    # Mostly current, a few behind (a delisted ticker, a provider gap): fine.
    assert not mostly_stale(RefreshReport(targets=100, current={"finlake": 60}, stale=5, unavailable=35))
    assert not mostly_stale(RefreshReport(targets=100, current={"finlake": 30, "yfinance": 20}, stale=50))
    # Nothing to price is not a stale run.
    assert not mostly_stale(RefreshReport(targets=0))


def test_a_run_with_every_target_unpriced_is_not_ok_but_an_empty_run_is() -> None:
    """The edges of the rule. Targets but nothing current and nothing stale (every ticker
    unavailable, or every lookup failed) means the provider delivered nothing: not a clean run.
    No targets at all (no security with a recent filing) is nothing to judge: a clean run."""
    assert mostly_stale(RefreshReport(targets=4423, current={}, stale=0, unavailable=4423))
    assert mostly_stale(RefreshReport(targets=12, current={}, stale=0, failed=12))
    assert mostly_stale(RefreshReport(targets=1, current={"finlake": 0}, stale=0, unavailable=1))
    assert not mostly_stale(RefreshReport(targets=0, current={}, stale=0))
