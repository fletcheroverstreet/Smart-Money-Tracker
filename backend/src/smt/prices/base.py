"""What every daily-price provider has in common (PLAN.md §6.10, decision 1).

A provider answers one question: "what were the daily bars for this ticker
between these two dates?" It also declares the licence its data comes under,
because free price data may not be shown publicly:

* `personal-use-only`: Yahoo (yfinance) and finlake's store, which is Yahoo
  data too. Stored with `display_public = false`; shown in private mode only.
* `public-display-ok`: a licensed provider bought later (option A). Stored
  with `display_public = true`.

Switching to a licensed provider later is a config change: the licence
travels with every stored bar, so nothing else has to know where a price
came from.

House rule: a missing number is missing, never zero. Providers return None
for any value they don't have, and drop a bar entirely when it has no close.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol, SupportsFloat, runtime_checkable

from smt.domain.business_days import is_trading_day, session_close

# ---------------------------------------------------------------- licences

#: Licence tag for data that may be used privately but not shown to the public.
PERSONAL_USE_ONLY_TAG = "personal-use-only"
#: Licence tag for data bought with a public display licence (none yet).
PUBLIC_DISPLAY_OK_TAG = "public-display-ok"


@dataclass(frozen=True, slots=True)
class Licence:
    """The terms a provider's prices come under."""

    #: Short machine tag, e.g. "personal-use-only".
    tag: str
    #: May these prices (and numbers computed from them) appear on public pages?
    display_public: bool
    #: Credit line to show next to the data, when the terms ask for one.
    attribution: str | None = None


#: Yahoo's terms: personal use only, no redistribution or public display.
YAHOO_PERSONAL_USE = Licence(
    tag=PERSONAL_USE_ONLY_TAG,
    display_public=False,
    attribution="Yahoo Finance data, for personal use only",
)


# ---------------------------------------------------------------- bars

@dataclass(frozen=True, slots=True)
class Bar:
    """One trading day for one security, as a provider reported it.

    `close` is split-adjusted: after a 2-for-1 split every earlier close is
    halved, so the series has no fake 50% drop. `adj_close` also folds in
    dividends (as if they were reinvested), and is what return calculations
    use. Every field except `day` and `close` may be None (unknown).
    """

    day: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    adj_close: Decimal | None
    volume: int | None


@dataclass(frozen=True, slots=True)
class Split:
    """A stock split: from `ex_date` on, one old share is `ratio` new ones (2 = 2-for-1)."""

    ex_date: date
    ratio: Decimal


@runtime_checkable
class SplitSource(Protocol):
    """A provider that can also say which splits it has applied to its closes."""

    def get_splits(self, symbol: str) -> list[Split] | None:
        """Every split recorded for `symbol` (none: an empty list), or None when unknown."""
        ...


class ProviderError(Exception):
    """The provider could not answer right now (network error, locked file). Try again later."""


class ProviderRateLimited(ProviderError):
    """The provider asked us to slow down. Stop calling it for the rest of this run."""


@runtime_checkable
class PriceProvider(Protocol):
    """A source of daily bars.

    `get_daily_bars` is a plain (blocking) function; the nightly job runs it
    in a worker thread so it never stalls the event loop. It returns an empty
    list when the provider simply doesn't have the ticker ("not available",
    so the next provider in the chain gets a turn), and raises
    `ProviderError` when it failed and should be retried later.
    """

    #: Stored in prices_daily.provider (at most 20 characters).
    name: str
    licence: Licence

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        """Bars for `symbol` with start <= day <= end, oldest first."""
        ...


# ---------------------------------------------------------------- cleaning helpers

#: Minutes after the closing bell before a daily bar counts as final. Closing
#: auction prints and late corrections keep arriving for a few minutes.
SETTLE_MINUTES = 20

_TICKER = re.compile(r"[A-Z0-9][A-Z0-9-]{0,14}")


def provider_symbol(symbol: str) -> str | None:
    """The ticker in Yahoo's spelling, or None if it isn't a plain exchange ticker.

    Filings often write share classes with a dot or a slash ("BRK.B",
    "BRK/B"); Yahoo and finlake use a dash ("BRK-B"). Anything else (spaces,
    backslashes, "..") is refused, so a ticker can never turn into a file
    path outside finlake's price folder.
    """
    cleaned = symbol.strip().upper().replace(".", "-").replace("/", "-")
    return cleaned if _TICKER.fullmatch(cleaned) else None


def as_float(value: object) -> float | None:
    """`value` as a finite float, or None (for None, NaN, infinity, text, booleans)."""
    if isinstance(value, bool) or not isinstance(value, SupportsFloat):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def clean_price(value: object, places: int = 4) -> Decimal | None:
    """A provider's price as a Decimal, or None when it is missing or impossible.

    A share price of zero or less does not exist, so it is treated as
    missing rather than stored. Rounding removes float noise: Yahoo sends
    32-bit floats, so $335.20 arrives as 335.20001220703125. Four places keep
    sub-dollar prices exact (Yahoo quotes those to 4 decimals).
    """
    number = as_float(value)
    if number is None or number <= 0:
        return None
    return Decimal(str(round(number, places)))


def clean_volume(value: object) -> int | None:
    """Shares traded, or None when missing or negative. Zero is kept: a halted stock trades nothing."""
    number = as_float(value)
    if number is None or number < 0:
        return None
    return round(number)


def parse_day(value: object) -> date | None:
    """A bar's date from a date, a datetime or ISO text ("2026-09-21")."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def session_is_final(day: date, as_of: datetime) -> bool:
    """True if `day`'s session had closed (plus a settling margin) by `as_of`.

    A bar read during the session holds the price at that moment, not the
    close. finlake's store has exactly that problem: its refresher last ran
    at 14:35 ET on 2026-09-21, so its 21 September "close" is an intraday
    price. Days that are not NYSE sessions return True here; the loader's
    calendar check deals with those.
    """
    if not is_trading_day(day):
        return True
    return as_of >= session_close(day) + timedelta(minutes=SETTLE_MINUTES)
