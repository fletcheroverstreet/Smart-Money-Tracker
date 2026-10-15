"""Reading stored prices back out, for the API.

Two doors, and which one a caller uses decides what it may see
(PLAN.md decision 1):

* `private_series`: any provider, for private mode only. The router that
  calls it must require the private-mode session.
* `PUBLIC_ONLY`: the filter every public endpoint must apply to
  `prices_daily`. Under option B no stored price is public yet, so public
  pages get nothing; once a licensed provider is added, its rows
  (display_public = true) start showing with no other change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import PriceDaily
from smt.prices.base import (
    PERSONAL_USE_ONLY_TAG,
    PUBLIC_DISPLAY_OK_TAG,
    YAHOO_PERSONAL_USE,
    Licence,
)

#: WHERE clause for any public read of prices_daily.
PUBLIC_ONLY: ColumnElement[bool] = PriceDaily.display_public.is_(True)

#: Licences of the providers this app knows, by the name stored in prices_daily.provider.
KNOWN_LICENCES: dict[str, Licence] = {
    "finlake": YAHOO_PERSONAL_USE,
    "yfinance": YAHOO_PERSONAL_USE,
}
#: When two providers are equally current, prefer them in this order.
PREFERENCE = ("finlake", "yfinance")


@dataclass(frozen=True)
class Series:
    """One provider's closes for one security."""

    symbol: str
    provider: str
    licence: str
    bars: list[tuple[date, Decimal]]


async def resolve_security(session: AsyncSession, symbol: str) -> int | None:
    """The security a ticker points to today (a current ticker row first, then primary_ticker)."""
    wanted = symbol.strip().upper()
    spellings = list(dict.fromkeys([wanted, wanted.replace(".", "-"), wanted.replace("-", ".")]))
    found = (await session.execute(text("""
        SELECT x.security_id FROM security_ids x
        WHERE x.scheme = 'ticker' AND x.value = ANY(:t)
        ORDER BY x.valid_to DESC NULLS FIRST, x.security_id LIMIT 1"""), {"t": spellings})).scalar()
    if found is None:
        found = (await session.execute(text(
            "SELECT id FROM securities WHERE primary_ticker = ANY(:t) ORDER BY id LIMIT 1"),
            {"t": spellings})).scalar()
    return None if found is None else int(found)


async def private_series(session: AsyncSession, symbol: str, since: date) -> Series | None:
    """Closes since `since` from the most current provider, or None if nothing is stored.

    PRIVATE MODE ONLY: this ignores display_public on purpose.

    One provider's series is returned, never a mix: two providers' prices
    can differ slightly, and a chart that silently switched source mid-line
    would be misleading. The provider with the newest bar wins; ties go to
    the order in PREFERENCE.
    """
    security_id = await resolve_security(session, symbol)
    if security_id is None:
        return None
    latest = (await session.execute(
        select(PriceDaily.provider, func.max(PriceDaily.day), func.bool_and(PriceDaily.display_public))
        .where(PriceDaily.security_id == security_id, PriceDaily.day >= since)
        .group_by(PriceDaily.provider)
    )).tuples().all()
    if not latest:
        return None

    def rank(row: tuple[str, date, bool]) -> tuple[date, int]:
        provider, newest, _public = row
        preference = PREFERENCE.index(provider) if provider in PREFERENCE else len(PREFERENCE)
        return newest, -preference

    provider, _newest, all_public = max(latest, key=rank)
    rows = (await session.execute(
        select(PriceDaily.day, PriceDaily.close)
        .where(PriceDaily.security_id == security_id, PriceDaily.provider == provider,
               PriceDaily.day >= since, PriceDaily.close.is_not(None))
        .order_by(PriceDaily.day)
    )).tuples().all()
    bars = [(day, close) for day, close in rows if close is not None]
    if not bars:
        return None
    known = KNOWN_LICENCES.get(provider)
    licence = known.tag if known else (PUBLIC_DISPLAY_OK_TAG if all_public else PERSONAL_USE_ONLY_TAG)
    return Series(symbol=symbol.strip().upper(), provider=provider, licence=licence, bars=bars)
