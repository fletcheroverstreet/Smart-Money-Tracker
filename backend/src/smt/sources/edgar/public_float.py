"""Companies' public float from the SEC's XBRL data (job `edgar.refresh_public_float`).

`https://data.sec.gov/api/xbrl/companyconcept/CIK##########/dei/EntityPublicFloat.json`
lists every public float a company has reported on the cover of its annual
reports: the market value of the shares not held by insiders, measured at the
end of its second fiscal quarter. The latest one is an outside yardstick for
the size of a trade (smt.ingest.quality.flag_totals_as_price): a microcap whose
filing shows a $100 million "purchase" has typed something other than a price.

Only companies with large open-market trades are looked up, at most
`PER_RUN` a run on the REFERENCE lane of the shared SEC rate limiter, and each
is looked up again after `RECHECK_AFTER`. Every response is stored raw first.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from smt.db.models import IssuerPublicFloat
from smt.db.session import session_scope
from smt.logs import get_logger
from smt.net.client import FetchError, SecThrottled, shared_client
from smt.net.ratelimit import Priority
from smt.net.rawstore import RawStore
from smt.sources.edgar.pipeline import _store_raw
from smt.worker.scheduler import JobContext

log = get_logger("edgar.public_float")

URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/dei/EntityPublicFloat.json"
PER_RUN = 200
RECHECK_AFTER = timedelta(days=90)
#: Companies whose largest counted open-market trade in the last 400 days is at least this big.
MIN_TRADE_VALUE = 1_000_000


def latest_public_float(content: bytes) -> tuple[Decimal | None, date | None]:
    """(value in US dollars, as-of date) of the most recent reported public float, or (None, None).

    A reported float of zero is kept as reported (new listings often file one); callers decide
    what a zero means.
    """
    try:
        data: Any = json.loads(content)
    except ValueError:
        return None, None
    facts = (data.get("units") or {}).get("USD") if isinstance(data, dict) else None
    best: tuple[str, str, Any] | None = None
    for fact in facts or []:
        if not isinstance(fact, dict) or fact.get("val") is None or not fact.get("end"):
            continue
        key = (str(fact["end"]), str(fact.get("filed") or ""), fact["val"])
        if best is None or key[:2] > best[:2]:
            best = key
    if best is None:
        return None, None
    try:
        return Decimal(str(best[2])), date.fromisoformat(best[0])
    except (ArithmeticError, ValueError):
        return None, None


async def refresh_public_float(limit: int = PER_RUN, store: RawStore | None = None) -> dict[str, int]:
    """Look up the public float of companies with large trades that haven't been checked lately."""
    store = store or RawStore()
    async with session_scope() as session:
        todo = (await session.execute(text("""
            SELECT DISTINCT t.issuer_entity_id AS entity_id, x.value AS cik
              FROM transactions t
              JOIN entity_ids x ON x.entity_id = t.issuer_entity_id AND x.scheme = 'sec_cik'
              LEFT JOIN issuer_public_float p ON p.entity_id = t.issuer_entity_id
             WHERE t.code IN ('P', 'S') AND NOT t.is_derivative AND t.is_current
               AND t.value >= :min_value AND t.traded_on > current_date - 400
               AND (p.entity_id IS NULL OR p.checked_at < now() - :recheck)
             LIMIT :limit
        """), {"min_value": MIN_TRADE_VALUE, "recheck": RECHECK_AFTER, "limit": limit})).all()
    counts = {"checked": 0, "found": 0, "none": 0, "errors": 0}
    client = shared_client()
    for row in todo:
        cik = str(row.cik).zfill(10)
        try:
            fetched = await client.get(URL.format(cik=cik), priority=Priority.REFERENCE)
        except SecThrottled:
            counts["errors"] += 1
            break  # the SEC asked us to slow down: stop this run
        except FetchError:
            counts["errors"] += 1
            continue
        value, as_of = latest_public_float(fetched.content) if fetched.status == 200 else (None, None)
        async with session_scope() as session:
            raw_id = await _store_raw(session, store, fetched, "public_float") if fetched.status == 200 else None
            stmt = insert(IssuerPublicFloat).values(
                entity_id=row.entity_id, cik=cik, http_status=fetched.status, public_float=value,
                as_of=as_of, raw_document_id=raw_id)
            await session.execute(stmt.on_conflict_do_update(
                index_elements=["entity_id"],
                set_={"cik": cik, "http_status": fetched.status, "public_float": value, "as_of": as_of,
                      "raw_document_id": raw_id, "checked_at": text("now()")}))
        counts["checked"] += 1
        counts["found" if value is not None else "none"] += 1
    log.info("public_float.refreshed", **counts)
    return counts


async def refresh_public_float_job(ctx: JobContext) -> None:
    """Scheduler entry point."""
    counts = await refresh_public_float()
    ctx.docs_fetched += counts["checked"]
    ctx.stats.update(counts)
