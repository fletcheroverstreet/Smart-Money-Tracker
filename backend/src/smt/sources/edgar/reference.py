"""The security master from the SEC's ticker reference file.

`company_tickers_exchange.json` maps every current ticker to its issuer CIK
and exchange (about 10,500 rows). It lists CURRENT tickers only, so a
ticker's mapping here is valid "from now". Historical tickers come from the
filings themselves. A CIK can have several tickers (share classes: BRK-A and
BRK-B), so the model is many tickers to one issuer.

Tickers get reused, so each ticker row carries a validity window
(`security_ids.valid_from` / `valid_to`, both inclusive), compared with the
previous load:

* a ticker that now belongs to a **different company** closes the old
  company's window and opens a new one;
* a company whose only ticker changed (FB -> META) keeps its security, with
  the old symbol closed and the new one opened;
* a ticker that comes back for the same company reopens on its old security;
* a ticker this file used to list and no longer does is closed, but only when
  the file looks complete, so a truncated download can't "delist" hundreds
  of companies. Tickers learned from filings are never closed this way.

Sector labels come from the curated map where available (`sector_source =
curated`), then from the issuer's SIC code once its SEC profile is known
(smt.sources.edgar.submissions), else `unmapped`.

Raw first: the downloaded file is stored before it's parsed, and the next
download is conditional (If-Modified-Since), so an unchanged file costs one
small request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import Entity, EntityId, RawDocument, Security, SecurityId
from smt.db.session import session_scope
from smt.domain.business_days import ET
from smt.domain.names import org_display, pad_cik, slugify
from smt.domain.sectors import sector_for
from smt.logs import get_logger
from smt.net.client import FetchError, HttpClient, shared_client
from smt.net.ratelimit import Priority
from smt.net.rawstore import RawStore
from smt.sources.edgar.submissions import (
    conditional_headers,
    mark_parsed,
    quarantine_raw,
    store_raw_document,
)
from smt.worker.scheduler import JobContext

log = get_logger("edgar.reference")

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
PARSER_NAME = "company_tickers"
PARSER_VERSION = "2"
#: Where ticker windows opened by this file come from (`security_ids.source`).
SOURCE = "sec_company_tickers"
#: Vanished tickers are closed only if the file has at least this many rows (the real one has ~10,500)...
MIN_ROWS_TO_CLOSE = 5000
#: ...and fewer than this share of them vanished at once (a broken file looks like mass delistings).
MAX_VANISHED_SHARE = 0.05

#: One row of the file: (ten-digit CIK, company name, ticker, exchange or None).
TickerRow = tuple[str, str, str, str | None]


class TickerFileError(ValueError):
    """The download is not a usable company_tickers_exchange.json (it gets quarantined)."""


def parse_company_tickers(payload: bytes) -> list[TickerRow]:
    """(cik, name, ticker, exchange) rows from company_tickers_exchange.json.

    Tickers are upper-cased. If the same ticker appears twice (it happens,
    rarely), the first row wins, so one ticker maps to one company.
    Raises TickerFileError when the file doesn't have the expected shape.
    """
    try:
        data = json.loads(payload)
        fields = [str(f).lower() for f in data["fields"]]
        i_cik, i_name, i_ticker, i_exch = (fields.index(k) for k in ("cik", "name", "ticker", "exchange"))
        records = data["data"]
        if not isinstance(records, list):
            raise TickerFileError("'data' is not a list")
        rows: list[TickerRow] = []
        seen: set[str] = set()
        for rec in records:
            ticker = str(rec[i_ticker] or "").strip().upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            exchange = str(rec[i_exch]).strip() if rec[i_exch] else None
            rows.append((pad_cik(rec[i_cik]), str(rec[i_name]).strip(), ticker, exchange or None))
    except TickerFileError:
        raise
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise TickerFileError(f"not a company tickers file: {type(exc).__name__}: {exc}") from exc
    return rows


@dataclass
class _Window:
    """An open ticker window (valid_to IS NULL) as loaded from security_ids."""

    id: int
    ticker: str
    security_id: int
    source: str
    valid_from: date | None
    issuer_id: int | None


def _close_date(window: _Window, today: date) -> date:
    """Last valid day of a window closed today: yesterday (it was in yesterday's file), never before it opened."""
    yesterday = today - timedelta(days=1)
    return max(yesterday, window.valid_from) if window.valid_from else yesterday


async def load_company_tickers(session: AsyncSession, rows: list[TickerRow], *, today: date | None = None,
                               min_rows_to_close: int = MIN_ROWS_TO_CLOSE,
                               max_vanished_share: float = MAX_VANISHED_SHARE) -> dict[str, int]:
    """Upsert issuers, securities and ticker windows from the file's rows. Idempotent.

    Returns counts for the run record. See the module docstring for the rules.
    """
    today = today or datetime.now(ET).date()
    counts = {"tickers_seen": len(rows), "issuers_new": 0, "securities_new": 0, "tickers_opened": 0,
              "tickers_reassigned": 0, "tickers_renamed": 0, "tickers_reopened": 0,
              "tickers_closed": 0, "tickers_close_skipped": 0}

    # ------------------------------------------------ issuers (one entity per CIK)
    known_ciks: dict[str, int] = {
        str(v): int(e) for v, e in (await session.execute(
            select(EntityId.value, EntityId.entity_id).where(EntityId.scheme == "sec_cik"))).tuples().all()}
    for cik, name, _ticker, _exch in rows:
        if cik in known_ciks:
            continue
        display = org_display(name)
        slug = slugify(display, suffix=cik.lstrip("0"))
        entity_id = (await session.execute(
            insert(Entity).values(kind="company", name=display, sort_name=display, slug=slug, tags=["issuer"])
            .on_conflict_do_update(index_elements=["slug"], set_={"updated_at": text("now()")})
            .returning(Entity.id))).scalar_one()
        await session.execute(insert(EntityId).values(
            entity_id=entity_id, scheme="sec_cik", value=cik, source=SOURCE,
        ).on_conflict_do_nothing(index_elements=["scheme", "value"]))
        known_ciks[cik] = int(entity_id)
        counts["issuers_new"] += 1

    # ------------------------------------------------ what's open now
    open_by_ticker: dict[str, list[_Window]] = {}
    for r in (await session.execute(text(
            "SELECT x.id, x.value, x.security_id, x.source, x.valid_from, s.issuer_entity_id "
            "FROM security_ids x JOIN securities s ON s.id = x.security_id "
            "WHERE x.scheme = 'ticker' AND x.valid_to IS NULL ORDER BY x.id"))).all():
        open_by_ticker.setdefault(r.value, []).append(
            _Window(int(r.id), r.value, int(r.security_id), r.source, r.valid_from,
                    int(r.issuer_entity_id) if r.issuer_entity_id is not None else None))
    # Load every security once, so the loop below reads them from the session, not one query each.
    securities: dict[int, Security] = {s.id: s for s in (await session.execute(select(Security))).scalars()}
    by_issuer: dict[int, list[int]] = {}
    for s in sorted(securities.values(), key=lambda s: s.id):
        if s.issuer_entity_id is not None:
            by_issuer.setdefault(s.issuer_entity_id, []).append(s.id)
    # The same open windows, looked up by security.
    open_by_security: dict[int, list[_Window]] = {}
    for ws in open_by_ticker.values():
        for w in ws:
            open_by_security.setdefault(w.security_id, []).append(w)
    file_tickers = {ticker for _cik, _name, ticker, _exch in rows}
    #: Which company the file gives each ticker to.
    file_owner = {ticker: known_ciks[cik] for cik, _name, ticker, _exch in rows}
    touched: set[int] = set()

    def remember(window: _Window) -> None:
        open_by_ticker.setdefault(window.ticker, []).append(window)
        open_by_security.setdefault(window.security_id, []).append(window)

    async def close(window: _Window) -> None:
        await session.execute(text("UPDATE security_ids SET valid_to = :to WHERE id = :id"),
                              {"to": _close_date(window, today), "id": window.id})
        open_by_ticker[window.ticker].remove(window)
        open_by_security[window.security_id].remove(window)
        touched.add(window.security_id)

    # ------------------------------------------------ one row at a time
    for cik, name, ticker, exchange in rows:
        issuer_id = known_ciks[cik]
        windows = list(open_by_ticker.get(ticker, []))
        mine = [w for w in windows if w.issuer_id in (issuer_id, None)]
        for w in windows:
            if w.issuer_id not in (issuer_id, None):
                # The ticker was reused: it now belongs to another company.
                await close(w)
                counts["tickers_reassigned"] += 1
        if mine:
            security = securities[mine[0].security_id]
            if exchange:
                security.exchange = exchange
            security.is_active = True
            if security.issuer_entity_id is None:
                security.issuer_entity_id = issuer_id
                by_issuer.setdefault(issuer_id, []).append(security.id)
            continue

        security_id: int | None = None
        # A ticker coming back to the same company: reopen on the security it had before.
        security_id = (await session.execute(text(
            "SELECT x.security_id FROM security_ids x JOIN securities s ON s.id = x.security_id "
            "WHERE x.scheme = 'ticker' AND x.value = :t AND x.valid_to IS NOT NULL "
            "AND s.issuer_entity_id = :issuer ORDER BY x.valid_to DESC, x.id DESC LIMIT 1"),
            {"t": ticker, "issuer": issuer_id})).scalar()
        if security_id is not None:
            counts["tickers_reopened"] += 1
        else:
            # A renamed ticker (FB -> META): the company has exactly one security none of
            # whose tickers the file still gives to this company. That security takes the
            # new symbol (and a ticker the file now gives to someone else is closed on it).
            candidates = [sid for sid in by_issuer.get(issuer_id, [])
                          if not any(file_owner.get(w.ticker) == issuer_id
                                     for w in open_by_security.get(sid, []))]
            if len(candidates) == 1:
                security_id = candidates[0]
                for w in list(open_by_security.get(security_id, [])):
                    await close(w)
                counts["tickers_renamed"] += 1

        if security_id is None:
            label = sector_for(ticker, None)
            new = Security(issuer_entity_id=issuer_id, name=org_display(name), primary_ticker=ticker,
                           exchange=exchange, sector=label.sector, industry=label.industry,
                           sector_source=label.source)
            session.add(new)
            await session.flush()
            securities[new.id] = new
            by_issuer.setdefault(issuer_id, []).append(new.id)
            security_id = new.id
            counts["securities_new"] += 1
        else:
            security = securities[security_id]
            security.primary_ticker = ticker
            if exchange:
                security.exchange = exchange

        # Open the window. Closed and reopened on the same day: reuse that day's row.
        window_id = (await session.execute(
            insert(SecurityId).values(security_id=security_id, scheme="ticker", value=ticker,
                                      valid_from=today, source=SOURCE)
            .on_conflict_do_update(constraint="uq_security_ids_scheme_value_valid_from",
                                   set_={"security_id": security_id, "valid_to": None, "source": SOURCE})
            .returning(SecurityId.id))).scalar_one()
        remember(_Window(int(window_id), ticker, security_id, SOURCE, today, issuer_id))
        touched.add(security_id)
        counts["tickers_opened"] += 1

    # ------------------------------------------------ tickers the file no longer lists
    vanished = [w for ws in open_by_ticker.values() for w in ws
                if w.source == SOURCE and w.ticker not in file_tickers]
    listed = sum(1 for ws in open_by_ticker.values() for w in ws if w.source == SOURCE)
    if vanished:
        looks_complete = len(rows) >= min_rows_to_close and len(vanished) < max_vanished_share * max(listed, 1)
        if looks_complete:
            for w in vanished:
                await close(w)
            counts["tickers_closed"] = len(vanished)
        else:
            counts["tickers_close_skipped"] = len(vanished)
            log.warning("company_tickers.close_skipped", vanished=len(vanished), rows=len(rows))

    # A security is active while it has at least one open ticker.
    await session.flush()
    if touched:
        await session.execute(text(
            "UPDATE securities s SET is_active = EXISTS (SELECT 1 FROM security_ids x "
            "WHERE x.security_id = s.id AND x.scheme = 'ticker' AND x.valid_to IS NULL) "
            "WHERE s.id = ANY(:ids)"), {"ids": sorted(touched)})
    return counts


# ---------------------------------------------------------------- fetch, store, parse, load
async def refresh_company_tickers(client: HttpClient, store: RawStore, *,
                                  today: date | None = None) -> dict[str, Any]:
    """Download the ticker file (conditionally), store it raw, then load it.

    Returns counts. `outcome` is one of: not_modified (the SEC said 304),
    unchanged (same bytes as the last loaded copy), loaded, quarantined.
    """
    url = COMPANY_TICKERS_URL
    async with session_scope() as session:
        headers = await conditional_headers(session, url)
    fetched = await client.get(url, priority=Priority.REFERENCE, headers=headers or None)
    if fetched.not_modified:
        return {"outcome": "not_modified"}
    if not fetched.ok:
        raise FetchError(f"GET {url} returned HTTP {fetched.status}")

    # Raw first: the file is stored (and committed) before anything parses it.
    async with session_scope() as session:
        raw_id, _sha = await store_raw_document(session, store, fetched, role="company_tickers",
                                                parser_name=PARSER_NAME)
    async with session_scope() as session:
        raw = await session.get(RawDocument, raw_id)
        if raw is not None and raw.parse_status == "parsed" and raw.parser_version == PARSER_VERSION:
            return {"outcome": "unchanged"}  # the same bytes were loaded before

    try:
        rows = parse_company_tickers(fetched.content)
    except TickerFileError as exc:
        async with session_scope() as session:
            await quarantine_raw(session, raw_id, ref="company_tickers_exchange.json",
                                 reason=f"company tickers file: {exc}",
                                 parser_name=PARSER_NAME, parser_version=PARSER_VERSION)
        log.warning("company_tickers.quarantined", error=str(exc)[:300])
        return {"outcome": "quarantined"}

    async with session_scope() as session:
        counts: dict[str, Any] = dict(await load_company_tickers(session, rows, today=today))
        await mark_parsed(session, raw_id, parser_name=PARSER_NAME, parser_version=PARSER_VERSION)
    counts["outcome"] = "loaded"
    return counts


async def refresh_company_tickers_job(ctx: JobContext) -> None:
    """Scheduled daily: reload the SEC's ticker <-> CIK <-> exchange file."""
    counts = await refresh_company_tickers(shared_client(), RawStore())
    ctx.docs_fetched += 1
    ctx.rows_new += int(counts.get("issuers_new", 0)) + int(counts.get("securities_new", 0))
    ctx.rows_updated += sum(int(counts.get(k, 0)) for k in (
        "tickers_reassigned", "tickers_renamed", "tickers_reopened", "tickers_closed"))
    if counts["outcome"] == "quarantined":
        ctx.parse_failures += 1
    ctx.stats.update(counts)
