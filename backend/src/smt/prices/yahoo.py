"""Daily bars from Yahoo Finance through the yfinance library. PRIVATE MODE ONLY.

Yahoo's terms allow personal use only: no public display, no redistribution
(docs/plan/source-verification.md, "prices-and-quotes"). Every bar from here
is stored with `display_public = false`, and the nightly job only uses this
provider when private mode is configured (PLAN.md decision 1, option B).

What Yahoo returns with `auto_adjust=False`:

* `Close`: split-adjusted, not dividend-adjusted (same meaning as finlake's).
* `Adj Close`: split- and dividend-adjusted, anchored at the latest bar.

Known weak spots (source-verification.md): Yahoo sometimes skips a session
(2026-08-28 was missing for a while) and endpoints break without warning. The
nightly job re-reads the last 30 days on every run, so a skipped session is
filled in once Yahoo has it, and a failure only delays prices by a day.

Network happens only at run time. Tests pass a fake `fetch` function, so
yfinance is never even imported during tests.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from smt.config import get_settings
from smt.prices.base import (
    YAHOO_PERSONAL_USE,
    Bar,
    Licence,
    ProviderError,
    ProviderRateLimited,
    clean_price,
    clean_volume,
    parse_day,
    provider_symbol,
    session_is_final,
)

#: One raw row: keys day, open, high, low, close, adj_close, volume.
RawRow = Mapping[str, object]
#: (Yahoo ticker, first day, day AFTER the last day) -> raw rows. yfinance's `end` is exclusive.
HistoryFetcher = Callable[[str, date, date], Sequence[RawRow]]
#: Returns "now" as an aware datetime (tests freeze it).
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class YFinanceProvider:
    """Daily bars from Yahoo via yfinance (personal-use licence)."""

    name: str = "yfinance"
    licence: Licence = YAHOO_PERSONAL_USE

    def __init__(self, fetch: HistoryFetcher | None = None, clock: Clock | None = None) -> None:
        self._fetch = fetch or fetch_yahoo_history
        self._clock = clock or _utc_now

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        ticker = provider_symbol(symbol)
        if ticker is None or start > end:
            return []
        rows = self._fetch(ticker, start, end + timedelta(days=1))
        now = self._clock()
        by_day: dict[date, Bar] = {}
        for row in rows:
            day, close = parse_day(row.get("day")), clean_price(row.get("close"))
            if day is None or close is None or not start <= day <= end:
                continue
            if not session_is_final(day, now):
                continue  # today's bar during the session: an intraday price, not the close
            by_day[day] = Bar(
                day=day,
                open=clean_price(row.get("open")),
                high=clean_price(row.get("high")),
                low=clean_price(row.get("low")),
                close=close,
                adj_close=clean_price(row.get("adj_close"), places=6),
                volume=clean_volume(row.get("volume")),
            )
        return [by_day[day] for day in sorted(by_day)]


def fetch_yahoo_history(ticker: str, start: date, end_exclusive: date) -> list[dict[str, object]]:
    """Download daily bars for one ticker from Yahoo. Blocking; network.

    Errors are translated so the caller can tell them apart:
    * Yahoo has no data for the ticker or window -> [] ("not available");
    * Yahoo is rate limiting us -> ProviderRateLimited (stop for this run);
    * anything else (network, a changed endpoint) -> ProviderError (retry tomorrow).
    """
    # Belt and braces: the test suite must never reach the internet.
    if get_settings().environment == "test" and os.environ.get("SMT_NETWORK_TESTS") != "1":
        raise ProviderError("yfinance is disabled while testing (no network in tests)")
    # Imported here so that importing this module (and every test) never
    # loads yfinance or touches the network. yfinance ships no type hints.
    import yfinance as yf  # type: ignore[import-untyped]
    from yfinance import exceptions as yf_errors

    try:
        # raise_errors is deprecated in yfinance 1.x (in favour of a global
        # config switch) but still works per call, which keeps this module
        # from changing yfinance settings for anything else in the process.
        frame = yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=end_exclusive.isoformat(),
            interval="1d",
            auto_adjust=False,  # keep both Close and Adj Close
            actions=False,
            raise_errors=True,  # raise instead of printing, so failures are visible
            timeout=20,
        )
    except yf_errors.YFRateLimitError as exc:
        raise ProviderRateLimited(f"yfinance rate limited on {ticker}") from exc
    except yf_errors.YFTickerMissingError:
        return []  # no data: delisted, unknown to Yahoo, or nothing in this window
    except Exception as exc:
        raise ProviderError(f"yfinance failed on {ticker}: {exc}") from exc
    return rows_from_frame(frame)


def rows_from_frame(frame: Any) -> list[dict[str, object]]:
    """yfinance's pandas DataFrame -> plain rows.

    The index holds the session dates (midnight in the exchange's time
    zone); the columns are Open, High, Low, Close, Adj Close and Volume.
    """
    if frame is None or bool(getattr(frame, "empty", True)):
        return []
    rows: list[dict[str, object]] = []
    for stamp, record in zip(frame.index, frame.to_dict("records"), strict=True):
        day = stamp.date() if hasattr(stamp, "date") else parse_day(stamp)
        rows.append({
            "day": day,
            "open": record.get("Open"),
            "high": record.get("High"),
            "low": record.get("Low"),
            "close": record.get("Close"),
            "adj_close": record.get("Adj Close"),
            "volume": record.get("Volume"),
        })
    return rows
