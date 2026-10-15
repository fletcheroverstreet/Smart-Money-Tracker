"""Store provider bars in `prices_daily`, idempotently.

The natural key is (security, day, provider). Loading the same bars twice
changes nothing: a row is rewritten only when one of its values differs,
and only then does its `fetched_at` move. Every row records its provider and
the provider's licence flag (`display_public`), so public endpoints can
leave out anything that may not be shown publicly (PLAN.md decision 1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import PriceDaily, PriceSplit, PriceSplitCheck
from smt.prices.base import Bar, Licence, Split

#: Rows per INSERT statement (11 parameters each, well under Postgres' 65,535 limit).
CHUNK = 1000

_VALUE_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume", "display_public")


@dataclass
class UpsertCounts:
    """What an upsert did: rows inserted, rows changed, rows already identical."""

    new: int = 0
    updated: int = 0
    unchanged: int = 0


async def upsert_bars(session: AsyncSession, *, security_id: int, provider: str,
                      licence: Licence, bars: list[Bar]) -> UpsertCounts:
    """Insert or update one provider's bars for one security. Idempotent."""
    counts = UpsertCounts()
    rows = [{
        "security_id": security_id,
        "day": bar.day,
        "provider": provider,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "adj_close": bar.adj_close,
        "volume": bar.volume,
        "display_public": licence.display_public,
    } for bar in bars]
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        new_rows = insert(PriceDaily).values(chunk)
        excluded = new_rows.excluded
        stmt = new_rows.on_conflict_do_update(
            index_elements=[PriceDaily.security_id, PriceDaily.day, PriceDaily.provider],
            set_={**{name: excluded[name] for name in _VALUE_COLUMNS}, "fetched_at": func.now()},
            # Only rewrite a row whose values actually changed. IS DISTINCT FROM
            # treats NULL = NULL as "same", which plain != does not.
            where=or_(*[getattr(PriceDaily, name).is_distinct_from(excluded[name])
                        for name in _VALUE_COLUMNS]),
        ).returning(literal_column("(xmax = 0)", Boolean).label("inserted"))
        # Postgres returns one row per inserted or updated row; unchanged rows
        # are skipped by the WHERE above. xmax = 0 marks a fresh insert.
        written = (await session.execute(stmt)).scalars().all()
        inserted = sum(1 for flag in written if flag)
        counts.new += inserted
        counts.updated += len(written) - inserted
        counts.unchanged += len(chunk) - len(written)
    return counts


async def record_splits(session: AsyncSession, *, security_id: int, provider: str, splits: list[Split]) -> None:
    """Store the splits a provider has recorded for one security, and when they were read.

    Call it after that provider's bars were fetched and in the same transaction as upsert_bars:
    `checked_at` is then at or after every stored bar's `fetched_at` (both are the transaction's
    now()), so any split the provider could have applied to one of those closes is stored.
    Idempotent apart from `checked_at`, which records the latest read.
    """
    rows = [{"security_id": security_id, "provider": provider, "ex_date": s.ex_date, "ratio": s.ratio}
            for s in splits]
    if rows:
        new_rows = insert(PriceSplit).values(rows)
        await session.execute(new_rows.on_conflict_do_update(
            index_elements=[PriceSplit.security_id, PriceSplit.provider, PriceSplit.ex_date],
            set_={"ratio": new_rows.excluded.ratio},
            where=PriceSplit.ratio.is_distinct_from(new_rows.excluded.ratio)))
    check = insert(PriceSplitCheck).values(security_id=security_id, provider=provider, checked_at=func.now())
    await session.execute(check.on_conflict_do_update(
        index_elements=[PriceSplitCheck.security_id, PriceSplitCheck.provider], set_={"checked_at": func.now()}))


@dataclass(frozen=True)
class StoredRange:
    """The span of days already stored for one security from one provider."""

    first_day: date
    last_day: date


async def stored_ranges(session: AsyncSession, security_id: int) -> dict[str, StoredRange]:
    """{provider: first and last stored day} for one security."""
    result = await session.execute(
        select(PriceDaily.provider, func.min(PriceDaily.day), func.max(PriceDaily.day))
        .where(PriceDaily.security_id == security_id)
        .group_by(PriceDaily.provider)
    )
    return {provider: StoredRange(first, last) for provider, first, last in result.tuples()}


async def stored_closes(session: AsyncSession, security_id: int, provider: str,
                        start: date, end: date) -> dict[date, tuple[Decimal | None, Decimal | None]]:
    """{day: (close, adj_close)} already stored for one security and provider in [start, end]."""
    result = await session.execute(
        select(PriceDaily.day, PriceDaily.close, PriceDaily.adj_close).where(
            PriceDaily.security_id == security_id,
            PriceDaily.provider == provider,
            PriceDaily.day >= start,
            PriceDaily.day <= end,
        )
    )
    return {day: (close, adj) for day, close, adj in result.tuples()}


#: A stored close that moved by more than 1% means the provider re-based its
#: history, which is what a split does (every earlier close is divided by
#: the split ratio). Smaller moves are ordinary late corrections.
CLOSE_REBASE_TOLERANCE = 0.01
#: A change in adj_close / close means a new dividend re-scaled the whole
#: adjusted history. 0.005% is far above float noise and below any real dividend
#: worth tracking (a $0.05 dividend on a $600 stock is 0.008%).
ADJ_REBASE_TOLERANCE = 5e-5


def history_was_rebased(fresh: list[Bar],
                        stored: Mapping[date, tuple[Decimal | None, Decimal | None]]) -> bool:
    """True if the fresh bars disagree with stored ones in a way only a full reload fixes.

    Adjusted prices are relative to the latest bar. When a split or a new
    dividend arrives, the provider rescales EVERY earlier price, but the
    nightly job only re-reads the last few weeks. Comparing the overlap with
    what we stored shows whether that happened; if so, the caller reloads the
    security's whole history so old and new rows share one basis.
    """
    for bar in fresh:
        if bar.day not in stored:
            continue
        old_close, old_adj = stored[bar.day]
        if old_close is None or old_close <= 0:
            return True
        if abs(float(bar.close) / float(old_close) - 1) > CLOSE_REBASE_TOLERANCE:
            return True
        if (old_adj is None) != (bar.adj_close is None):
            return True
        if old_adj is not None and bar.adj_close is not None:
            old_factor = float(old_adj) / float(old_close)
            new_factor = float(bar.adj_close) / float(bar.close)
            if old_factor <= 0 or abs(new_factor / old_factor - 1) > ADJ_REBASE_TOLERANCE:
                return True
    return False
