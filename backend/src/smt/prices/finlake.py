"""Read-only access to the daily prices finlake already has on this machine.

finlake is the user's own research tool (a sibling folder of this repo). What
it stores, per its `finlake/sources/prices.py` and `finlake/store.py`:

* `<FINLAKE_HOME>/prices/<TICKER>.parquet`: one file per ticker with columns
  date (text, "2026-09-21"), open, high, low, close, volume. About 700
  tickers, some back to 1962.
* `close` is split-adjusted (Yahoo applies splits to the whole history) but
  NOT dividend-adjusted.
* `<FINLAKE_HOME>/finlake.db` (SQLite) table `corp_actions`
  (ticker, date, kind, value): dividends are kind 'dividend', value = cash per
  share, date = ex-dividend date; splits are kind 'split', value = the ratio
  (2.0 = 2-for-1), date = the first day on the new basis. finlake records a
  split from the same download that re-bases the closes, so every split applied
  to a close it holds is in this table.

finlake downloads these bars from Yahoo, so they carry Yahoo's licence:
personal use only, never public display.

Rules this module follows (see CLAUDE.md, "House rules"):

* **Read only.** Parquet files are read through an in-memory DuckDB
  connection. The SQLite file is opened with `mode=ro&immutable=1` whenever
  its write-ahead log is empty or absent, so SQLite touches no other file
  in finlake's folder. Only when finlake has un-checkpointed changes is it
  opened plain `mode=ro`, which records a read lock in finlake's existing
  -shm index (no data is changed). No data is ever written under
  FINLAKE_HOME.
* **Resolved at call time.** The folder comes from settings (FINLAKE_HOME) on
  every call, never at import. finlake's own test suite once deleted 15.5M
  real rows because a data path was bound at import time.
* **Missing or locked means "not available".** The problem is logged and no
  bars are returned, so the next provider in the chain can answer.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_left
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from smt.config import get_settings
from smt.logs import get_logger
from smt.prices.base import (
    YAHOO_PERSONAL_USE,
    Bar,
    Licence,
    Split,
    as_float,
    clean_price,
    clean_volume,
    parse_day,
    provider_symbol,
    session_is_final,
)

log = get_logger("prices.finlake")


class FinlakeProvider:
    """Daily bars from finlake's local parquet store. Never fetches, never writes."""

    name: str = "finlake"
    licence: Licence = YAHOO_PERSONAL_USE

    def __init__(self, home: Path | None = None) -> None:
        # Tests pass a temporary folder. None means "ask settings on every call".
        self._home = home

    def home(self) -> Path:
        """finlake's data folder, resolved now (not at import)."""
        if self._home is not None:
            return self._home
        settings = get_settings()
        home = settings.finlake_home.expanduser()
        if settings.environment == "test" and _is_real_finlake_home(home):
            raise RuntimeError("FinlakeProvider resolved the REAL ~/.finlake during tests. Aborting.")
        return home

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        ticker = provider_symbol(symbol)
        if ticker is None or start > end:
            return []
        home = self.home()
        path = home / "prices" / f"{ticker}.parquet"
        if not path.is_file():
            return []  # finlake never fetched this ticker: not available here
        try:
            written_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            rows = _read_parquet(path)
        except (duckdb.Error, OSError) as exc:
            # A locked file (finlake rewriting it right now) or a damaged one.
            log.warning("finlake.parquet_unreadable", symbol=ticker, error=str(exc))
            return []
        try:
            dividends = _read_dividends(home / "finlake.db", ticker)
        except sqlite3.Error as exc:
            # Locked or damaged. Returning bars without adj_close would
            # overwrite good stored values with blanks, so answer nothing.
            log.warning("finlake.actions_unreadable", symbol=ticker, error=str(exc))
            return []
        return bars_from_rows(rows, dividends, start, end, written_at=written_at)

    def get_splits(self, symbol: str) -> list[Split] | None:
        """Every split finlake recorded for `symbol`, read only. None when that can't be known:
        no database, or it can't be read right now (the caller then trusts no close that a split
        could have changed)."""
        ticker = provider_symbol(symbol)
        if ticker is None:
            return None
        try:
            found = _read_actions(self.home() / "finlake.db", ticker, "split")
        except sqlite3.Error as exc:
            log.warning("finlake.actions_unreadable", symbol=ticker, error=str(exc))
            return None
        if found is None:
            return None
        return [Split(ex_date=day, ratio=Decimal(str(ratio))) for day, ratio in found]


def _is_real_finlake_home(path: Path) -> bool:
    try:
        return path.resolve() == (Path.home() / ".finlake").resolve()
    except OSError:
        return False


def _read_parquet(path: Path) -> list[tuple[Any, ...]]:
    """Every row of one finlake price file, oldest first.

    An in-memory DuckDB connection reads the file; nothing is written
    anywhere. Extension auto-install is off, so this can never touch the
    network.
    """
    con = duckdb.connect(":memory:", config={
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
    })
    try:
        result = con.execute(
            "SELECT date, open, high, low, close, volume FROM read_parquet(?) ORDER BY date",
            [str(path)],
        ).fetchall()
        return [tuple(row) for row in result]
    finally:
        con.close()


def _read_dividends(db_path: Path, ticker: str) -> list[tuple[date, float]] | None:
    """(ex-date, cash per share) for `ticker` from finlake's corp_actions table.

    Returns None when finlake has no database at all: dividends are then
    unknown, so adj_close stays empty rather than pretending there were none.
    Raises sqlite3.Error when the database exists but can't be read.
    """
    return _read_actions(db_path, ticker, "dividend")


def _read_actions(db_path: Path, ticker: str, kind: str) -> list[tuple[date, float]] | None:
    """(date, value) of `ticker`'s corporate actions of one kind ('dividend' or 'split'), with a
    positive value. None when finlake has no database. Raises sqlite3.Error when it can't be read."""
    if not db_path.is_file():
        return None
    wal = db_path.with_name(db_path.name + "-wal")
    shm = db_path.with_name(db_path.name + "-shm")
    wal_size = wal.stat().st_size if wal.exists() else 0
    if wal_size == 0:
        # No pending write-ahead log (none at all, or an empty one left by a
        # program that has the database open but hasn't written), so the main
        # file is complete. immutable=1 reads it without touching ANY other
        # file: SQLite neither creates -wal/-shm files nor takes read locks in
        # an existing -shm (which would update that file's timestamp).
        options = "mode=ro&immutable=1"
    elif shm.exists():
        # finlake has written changes that are still only in its write-ahead
        # log. Reading them needs the shared-memory index; SQLite records a
        # read lock there (no data changes), which is unavoidable for a
        # consistent read.
        options = "mode=ro"
    else:
        # A write-ahead log without its index: a crashed writer. Reading it
        # would make SQLite create files in finlake's folder, so don't.
        raise sqlite3.OperationalError("finlake.db has a -wal file but no -shm file; not reading it")
    con = sqlite3.connect(f"{db_path.resolve().as_uri()}?{options}", uri=True, timeout=2.0)
    try:
        found = con.execute(
            "SELECT date, value FROM corp_actions WHERE ticker = ? AND kind = ? ORDER BY date",
            (ticker, kind),
        ).fetchall()
    finally:
        con.close()
    actions: list[tuple[date, float]] = []
    for day_text, value in found:
        day, amount = parse_day(day_text), as_float(value)
        if day is not None and amount is not None and amount > 0:
            actions.append((day, amount))
    return actions


def bars_from_rows(
    rows: list[tuple[Any, ...]],
    dividends: list[tuple[date, float]] | None,
    start: date,
    end: date,
    *,
    written_at: datetime,
) -> list[Bar]:
    """Turn finlake's raw rows into Bars for [start, end].

    * Rows without a usable date or close are skipped (a bar with no close is
      not a bar).
    * A bar whose session had not closed when the file was written is
      skipped: it holds an intraday price, not the close.
    * adj_close is computed over the WHOLE file and then cut to the window,
      because a dividend paid after `end` still changes earlier adjusted
      prices (adjusted history is anchored at the latest bar, like Yahoo's).
    """
    by_day: dict[date, Bar] = {}
    for raw_day, open_, high, low, close, volume in rows:
        day, close_price = parse_day(raw_day), clean_price(close)
        if day is None or close_price is None or not session_is_final(day, written_at):
            continue
        # A repeated date: the later row wins (finlake keeps the last one too).
        by_day[day] = Bar(day=day, open=clean_price(open_), high=clean_price(high), low=clean_price(low),
                          close=close_price, adj_close=None, volume=clean_volume(volume))
    series = [by_day[day] for day in sorted(by_day)]
    if dividends is not None:
        factors = dividend_factors([b.day for b in series], [float(b.close) for b in series], dividends)
        series = [replace(b, adj_close=clean_price(float(b.close) * f, places=6))
                  for b, f in zip(series, factors, strict=True)]
    return [b for b in series if start <= b.day <= end]


def dividend_factors(days: list[date], closes: list[float],
                     dividends: list[tuple[date, float]]) -> list[float]:
    """Multipliers that turn each split-adjusted close into a dividend-adjusted one.

    The standard method (Yahoo's and CRSP's): on an ex-dividend date the
    price drops by roughly the dividend, which is not a loss to the holder,
    who received the cash. So every close BEFORE the ex-date is scaled by

        1 - dividend / (close on the day before the ex-date)

    and the factors of successive dividends multiply. The latest bar has
    factor 1. A dividend dated before the first bar or after the last bar
    has no effect on this series. `days` must be sorted and match `closes`.
    """
    n = len(days)
    # multiplier_at[j]: combined effect of dividends whose first bar on or
    # after the ex-date is bar j. It applies to every bar before j.
    multiplier_at: dict[int, float] = {}
    for ex_date, amount in dividends:
        j = bisect_left(days, ex_date)
        if j <= 0 or j >= n:
            continue
        prev_close = closes[j - 1]
        step = 1.0 - amount / prev_close if prev_close > 0 else 0.0
        if step <= 0:
            # A dividend at least as large as the price is a data error.
            log.warning("finlake.dividend_ignored", ex_date=ex_date.isoformat(), amount=amount)
            continue
        multiplier_at[j] = multiplier_at.get(j, 1.0) * step
    factors = [1.0] * n
    running = 1.0
    for i in range(n - 1, -1, -1):
        factors[i] = running
        running *= multiplier_at.get(i, 1.0)
    return factors
