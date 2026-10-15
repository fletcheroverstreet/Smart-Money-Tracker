"""Load a parsed Form 3/4/5 into the database.

One call loads one filing completely, in the caller's transaction:
issuer, owners and roles; the filing and its parties; every transaction and
holding row; amendment supersession; late flags; exercise-and-sell grouping;
and Live-feed events. Loading the same filing twice changes nothing
(PLAN.md §5.4). Keys are identical whether the filing came from live XML or
from the SEC's bulk data sets.

Finance and legal logic, in one place:

* **Attribution (decision 8).** A joint filing is ONE set of trades. It is
  attributed to a fund/institution owner if one is listed, otherwise to the
  first listed owner. Every owner is kept in `transaction_owners`, so each
  person's page shows the trade while aggregates count it once.
* **Open market.** Only codes P and S on Table I (the shares themselves) are
  open-market buys and sells. P/S rows in Table II are option contracts
  bought or written: stored and shown, but not counted as buying or selling
  the stock. Grants, exercises, tax withholding and gifts are stored and
  labeled, but they aren't discretionary trading.
* **Exercise and sell.** A same-day exercise (M/X) plus sale (S) in one
  filing is labeled "exercise and sell", not bearish selling (spec §2.2).
* **Late flags (Rule 16a-3(g)).** A Form 4 is due by the end of the second
  business day after the trade (or after the deemed execution date). Rows
  that may legally wait for Form 5 (codes L, W; gifts before 2023-02-27) and
  rows the filer marked early (timeliness E) are never flagged. A deemed-date
  late flag is labeled approximate: the rule's cap on deemed dates isn't
  verified yet.
* **Amendments (4/A).** EDGAR gives only the original's filing date. The
  original is the filing with the same issuer, an overlapping owner CIK, and
  that filing date; if several filings fit, the one whose rows the amendment
  rows match by content. Amendment rows replace the original rows they
  correct, paired by the strongest unique match (same date and shares, then
  same shares, then same date and price, then a unique date, then a unique
  title, then row position). Original rows not re-reported stay current,
  because an amendment re-reports only the lines it changes. An amendment row
  that can't be paired is kept and shown, flagged "amendment_unpaired", and
  left out of totals, so a trade is never counted twice.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.models import (
    EntityId,
    Filing,
    FilingParty,
    InsiderHolding,
    Quarantine,
    Role,
    Security,
    Transaction,
    TransactionOwner,
)
from smt.domain.business_days import form4_deadline, sec_business_days_between
from smt.domain.codes import (
    CODE_SHORT,
    EXERCISE_CODES,
    FORM5_ELIGIBLE_CODES,
    GIFTS_ON_FORM4_FROM,
    OPEN_MARKET_CODES,
    direction_for,
    label_for,
)
from smt.domain.names import pad_cik
from smt.ingest.entities import EntityRef, ensure_filer, ensure_issuer_security
from smt.ingest.feed import upsert_feed_event
from smt.ingest.quality import (
    AMENDMENT_UNPAIRED,
    DATE_AFTER_FILING,
    VALUE_IMPLAUSIBLE,
    currency_unstated,
    flag_ads,
    flag_relative_prices,
    flag_totals_as_price,
    mark_duplicates,
    price_in_foreign_currency,
    row_issues,
)
from smt.sources.forms.types import OwnershipDocument, OwnershipRow, ReportingOwner

INSTITUTION_KINDS = frozenset({"fund", "bank", "index_manager"})
#: First key of the per-company advisory lock taken while a filing loads (any fixed number).
_ISSUER_LOCK_NAMESPACE = 7301


@dataclass(frozen=True)
class FilingMeta:
    """Where a filing came from and when; supplied by the fetcher or bulk reader."""

    accession: str
    form_type: str
    filed_on: date | None
    accepted_at: datetime | None
    url: str
    primary_doc_url: str | None
    detected_via: str               # feed | daily_index | backfill | manual
    parser_version: str
    raw_document_id: int | None = None
    live: bool = True               # False for backfill/reprocess: never floods the Live feed
    #: {cik: EDGAR conformed name} from the feed or daily index (see edgar_queue.filer_names).
    filer_names: Mapping[str, str] = field(default_factory=dict)


@dataclass
class LoadResult:
    filing_id: int
    new_rows: int = 0
    updated_rows: int = 0
    feed_event_ids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def filing_index_url(cik: str, accession: str) -> str:
    """Human-readable EDGAR index page for a filing."""
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}-index.htm")


def _owner_type(row: OwnershipRow) -> str:
    return "indirect" if row.direct_indirect == "I" else "self"


def _relationship(owner: ReportingOwner) -> dict[str, Any]:
    return {
        "director": owner.is_director, "officer": owner.is_officer,
        "ten_pct_owner": owner.is_ten_percent_owner, "other": owner.is_other,
        "officer_title": owner.officer_title, "other_text": owner.other_text,
    }


def role_summary(owner: ReportingOwner) -> str:
    """Plain role text for the UI, e.g. 'Chief Financial Officer' or 'Director, 10% owner'."""
    parts: list[str] = []
    if owner.is_officer:
        parts.append(owner.officer_title or "Officer")
    if owner.is_director:
        parts.append("Director")
    if owner.is_ten_percent_owner:
        parts.append("10% owner")
    if owner.is_other:
        parts.append(owner.other_text or "Other")
    return ", ".join(parts) or "Insider"


def _late_flag(doc: OwnershipDocument, row: OwnershipRow, filed_on: date | None
               ) -> tuple[date | None, str | None, bool | None, int | None]:
    """(deadline, rule, is_late, business_days_late) for one transaction row. See module docstring."""
    if filed_on is None or row.transaction_date is None or row.kind != "transaction":
        return None, None, None, None
    doc_type = doc.document_type.upper()
    if doc_type.startswith("5"):
        # A Form 4 transaction reported on Form 5, or one the filer marked late, was late.
        late = row.form_type == "4" or row.timeliness == "L"
        return None, "reported_on_form5", late, None
    if not doc_type.startswith("4"):
        return None, None, None, None
    code = (row.code or "").upper()
    if row.timeliness == "E" or code in FORM5_ELIGIBLE_CODES:
        return None, "form5_eligible", None, None
    if code == "G" and row.transaction_date < GIFTS_ON_FORM4_FROM:
        return None, "gift_before_2023", None, None
    deadline = form4_deadline(row.transaction_date, row.deemed_execution_date)
    rule = "form4_2bd_deemed_approx" if row.deemed_execution_date else "form4_2bd"
    # An amendment is judged by the ORIGINAL's filing date, not the 4/A's.
    judged_on = doc.date_of_original_submission if doc.is_amendment and doc.date_of_original_submission else filed_on
    late = judged_on > deadline
    days = sec_business_days_between(deadline, judged_on) if late else None
    return deadline, rule, late, days


def _exercise_and_sell_groups(accession: str, rows: tuple[OwnershipRow, ...]) -> dict[date, str]:
    """Dates on which this filing reports both an exercise (M/X) and a sale (S)."""
    exercised = {r.transaction_date for r in rows if r.kind == "transaction" and r.code in EXERCISE_CODES}
    sold = {r.transaction_date for r in rows if r.kind == "transaction" and r.code == "S"}
    return {d: f"{accession}:{d.isoformat()}" for d in exercised & sold if d is not None}


def _open_market(row: OwnershipRow) -> bool:
    """A purchase or sale of shares on the market (code P or S in Table I)."""
    return (row.code or "").upper() in OPEN_MARKET_CODES and row.table != "derivative"


def _value(row: OwnershipRow) -> Decimal | None:
    """shares × price when both were reported; never guessed.

    An open-market purchase or sale (P/S in Table I) with a price or a share count of 0 states no
    size: no trade on the market happens at $0 or for no shares. CLMT tx 332672: restricted units
    settled in cash, filed as a sale of 24,555 at 0, read as a "-$0" sale. Aetos' funds file
    purchases of LLC interests that "are not unitized" as 0 shares at $7,333.34 (tx 274428).
    The value is missing, not $0. Grants and gifts at 0 are different: $0 is what they cost.
    """
    if row.shares is None or row.price is None:
        return None
    if (row.price == 0 or row.shares == 0) and _open_market(row):
        return None
    return (row.shares * row.price).quantize(Decimal("0.01"))


async def _upsert_roles(session: AsyncSession, owner: EntityRef, issuer: EntityRef,
                        rep: ReportingOwner, doc: OwnershipDocument, filing_id: int) -> None:
    """Record each role this owner holds at the issuer, with first/last seen dates."""
    seen = doc.period_of_report
    roles: list[tuple[str, str]] = []
    if rep.is_officer:
        roles.append(("officer", rep.officer_title or ""))
    if rep.is_director:
        roles.append(("director", ""))
    if rep.is_ten_percent_owner:
        roles.append(("ten_pct_owner", ""))
    if rep.is_other:
        roles.append(("other_insider", rep.other_text or ""))
    # A Form 3 marks the day someone became an insider: that's the role's start.
    start = seen if doc.document_type.startswith("3") else None
    for role_type, title in roles:
        stmt = insert(Role).values(
            entity_id=owner.id, org_entity_id=issuer.id, role_type=role_type, title=title,
            start_date=start, last_seen_on=seen, source_filing_id=filing_id, source="edgar_ownership",
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_roles_entity_id_org_entity_id_role_type_title",
            set_={
                "start_date": func.least(Role.start_date, stmt.excluded.start_date),
                "last_seen_on": func.greatest(Role.last_seen_on, stmt.excluded.last_seen_on),
                "updated_at": text("now()"),
            },
        )
        await session.execute(stmt)


def _choose_primary(owners: list[tuple[ReportingOwner, EntityRef]]) -> int:
    """Index of the owner a joint filing's trades are attributed to (decision 8)."""
    for i, (_rep, ref) in enumerate(owners):
        if ref.kind in INSTITUTION_KINDS:
            return i
    return 0


async def load_ownership(session: AsyncSession, meta: FilingMeta, doc: OwnershipDocument) -> LoadResult:
    """Load one parsed ownership document. Idempotent. Runs inside the caller's transaction."""
    period = doc.period_of_report
    issuer = await ensure_filer(session, doc.issuer_cik, doc.issuer_name, kind_hint="company", tags=("issuer",))
    # Loading updates the company's other rows too (duplicates, amendment pairs, price checks), so
    # two loads for one company wait for each other instead of deadlocking. Held until commit.
    await session.execute(text("SELECT pg_advisory_xact_lock(CAST(:ns AS int), CAST(:id AS int))"),
                          {"ns": _ISSUER_LOCK_NAMESPACE, "id": issuer.id})
    security_id = await ensure_issuer_security(session, issuer, doc.issuer_ticker, as_of=period)

    owners: list[tuple[ReportingOwner, EntityRef]] = []
    # The signers' names: an owner who signs personally shows where a double surname ends.
    signatures = tuple(name for name, _signed in doc.signatures)
    for rep in doc.owners:
        tags = ("insider", "ten_pct_owner") if rep.is_ten_percent_owner else ("insider",)
        owners.append((rep, await ensure_filer(session, rep.cik, rep.name, tags=tags, signatures=signatures)))
    if not owners:
        raise ValueError(f"{meta.accession}: ownership document has no reporting owners")
    primary_index = _choose_primary(owners)
    primary_rep, primary = owners[primary_index]

    # ---------------------------------------------------------------- filing
    flags: dict[str, Any] = {
        "aff10b5one": doc.aff10b5one, "not_subject_to_section16": doc.not_subject_to_section16,
        "no_securities_owned": doc.no_securities_owned,
        "form3_holdings_reported": doc.form3_holdings_reported,
        "form4_transactions_reported": doc.form4_transactions_reported,
        "signatures": list(signatures),
    }
    values: dict[str, Any] = {
        "source": "edgar", "source_key": meta.accession, "form_type": meta.form_type,
        "accepted_at": meta.accepted_at, "filed_on": meta.filed_on, "period_of_report": period,
        "issuer_entity_id": issuer.id, "url": meta.url, "primary_doc_url": meta.primary_doc_url,
        "schema_version": doc.schema_version, "is_amendment": doc.is_amendment,
        "original_filed_on": doc.date_of_original_submission, "machine_readable": True,
        "footnotes": doc.footnotes, "remarks": doc.remarks, "flags": flags,
        "detected_via": meta.detected_via, "parser_version": meta.parser_version,
    }
    stmt = insert(Filing).values(**values)
    upsert = stmt.on_conflict_do_update(
        index_elements=["source", "source_key"],
        set_={k: stmt.excluded[k] for k in values if k not in ("source", "source_key", "detected_via")}
        | {"updated_at": text("now()")},
    ).returning(Filing.id)
    filing_id = int((await session.execute(upsert)).scalar_one())
    result = LoadResult(filing_id=filing_id)

    if meta.raw_document_id is not None:
        await session.execute(text(
            "UPDATE raw_documents SET filing_id = :f, parse_status = 'parsed', parser_name = 'ownership', "
            "parser_version = :v, parse_error = NULL WHERE id = :id"),
            {"f": filing_id, "v": meta.parser_version, "id": meta.raw_document_id})

    # ---------------------------------------------------------------- parties and roles
    await session.execute(
        insert(FilingParty).values(filing_id=filing_id, entity_id=issuer.id, role="issuer", is_primary=False)
        .on_conflict_do_nothing()
    )
    for position, (rep, ref) in enumerate(owners):
        await session.execute(
            insert(FilingParty).values(
                filing_id=filing_id, entity_id=ref.id, role="reporting_owner",
                is_primary=(position == primary_index), relationship=_relationship(rep) | {"raw_name": rep.name},
                position=position,
            ).on_conflict_do_update(
                index_elements=["filing_id", "entity_id", "role"],
                set_={"is_primary": position == primary_index, "relationship": _relationship(rep) | {"raw_name": rep.name},
                      "position": position},
            )
        )
        await _upsert_roles(session, ref, issuer, rep, doc, filing_id)

    # ---------------------------------------------------------------- rows
    groups = _exercise_and_sell_groups(meta.accession, doc.rows)
    disclosed_at = meta.accepted_at
    precision = "timestamp" if meta.accepted_at else "date_only"
    new_tx: list[tuple[int, OwnershipRow, list[str]]] = []
    amendment_rows: list[RowKey] = []

    for row in doc.rows:
        key = f"edgar:{meta.accession}:{row.table}:{row.kind}:{row.line_no}"
        if row.kind == "holding":
            h = {
                "filing_id": filing_id, "dedupe_key": key, "table_name": row.table, "line_no": row.line_no,
                "entity_id": primary.id, "issuer_entity_id": issuer.id, "security_title": row.security_title,
                "is_derivative": row.table == "derivative", "shares": row.shares_owned_after,
                "ownership_nature": row.direct_indirect, "nature_text": row.nature_of_ownership,
                "as_of": period, "footnote_ids": list(row.footnote_ids),
                # Derivative holdings (options, RSUs): what they are on and their terms, as filed.
                # Form 3 has no count of the options themselves, so the underlying shares are the size.
                "underlying_title": row.underlying_title, "underlying_shares": row.underlying_shares,
                "exercise_price": row.conversion_or_exercise_price, "exercise_date": row.exercise_date,
                "expiration_date": row.expiration_date,
            }
            hs = insert(InsiderHolding).values(**h)
            await session.execute(hs.on_conflict_do_update(
                index_elements=["dedupe_key"], set_={k: hs.excluded[k] for k in h if k != "dedupe_key"}))
            continue

        deadline, rule, late, days_late = _late_flag(doc, row, meta.filed_on)
        code = (row.code or "").upper() or None
        row_notes = [doc.footnotes[k] for k in row.footnote_ids if k in doc.footnotes]
        # The ADS check runs once every row is stored (flag_ads, below), so that each row is judged
        # against the same rows as in the 30-minute recompute.
        issues = row_issues(traded_on=row.transaction_date, filed_on=meta.filed_on, shares=row.shares,
                            price=row.price, value=_value(row), open_market=_open_market(row),
                            foreign_currency=price_in_foreign_currency(row_notes, row.price),
                            unstated_currency=currency_unstated(row.security_title, row_notes, row.price))
        date_ok = DATE_AFTER_FILING not in issues
        if not date_ok:
            # A trade can't be dated after its own filing: the delay and deadline are unknowable.
            late, days_late = None, None
        v: dict[str, Any] = {
            "filing_id": filing_id, "source": f"form{doc.document_type[0]}", "table_name": row.table,
            "line_no": row.line_no, "dedupe_key": key, "entity_id": primary.id,
            "issuer_entity_id": issuer.id, "owner_type": _owner_type(row),
            "ownership_nature": row.direct_indirect, "nature_text": row.nature_of_ownership,
            "security_id": security_id, "security_title": row.security_title,
            "raw_ticker": doc.issuer_ticker, "match_method": "issuer_primary" if security_id else None,
            "is_derivative": row.table == "derivative", "underlying_title": row.underlying_title,
            "underlying_shares": row.underlying_shares, "exercise_price": row.conversion_or_exercise_price,
            "exercise_date": row.exercise_date, "expiration_date": row.expiration_date,
            "direction": direction_for(code), "code": code,
            "is_open_market": code in OPEN_MARKET_CODES and row.table != "derivative",
            "is_10b5_1": doc.aff10b5one, "equity_swap": row.equity_swap,
            "exercise_and_sell_group": groups.get(row.transaction_date) if row.transaction_date else None,
            "shares": row.shares, "price": row.price, "price_is_weighted_avg": row.price_is_weighted_avg,
            "value": _value(row), "acquired_disposed": row.acquired_disposed,
            "shares_owned_after": row.shares_owned_after, "traded_on": row.transaction_date,
            "deemed_execution_on": row.deemed_execution_date, "disclosed_at": disclosed_at,
            "disclosed_precision": precision,
            "delay_days": (meta.filed_on - row.transaction_date).days
            if meta.filed_on and row.transaction_date and date_ok else None,
            "deadline_on": deadline if date_ok else None, "deadline_rule": rule,
            "is_late": late, "business_days_late": days_late, "data_issues": issues,
            "filer_timeliness": row.timeliness, "footnote_ids": list(row.footnote_ids),
            "parser_version": meta.parser_version,
        }
        ts = insert(Transaction).values(**v)
        tx_upsert = ts.on_conflict_do_update(
            index_elements=["dedupe_key"],
            set_={k: ts.excluded[k] for k in v if k not in ("dedupe_key", "filing_id")},
        ).returning(Transaction.id, text("(xmax = 0) AS inserted"))
        tx_id, inserted = (await session.execute(tx_upsert)).one()
        if inserted:
            result.new_rows += 1
            new_tx.append((int(tx_id), row, issues))
        else:
            result.updated_rows += 1
        amendment_rows.append(RowKey(int(tx_id), row.table, row.line_no, row.security_title,
                                     row.transaction_date, code, row.shares, row.price))
        for position, (_rep, ref) in enumerate(owners):
            await session.execute(
                insert(TransactionOwner).values(transaction_id=tx_id, entity_id=ref.id,
                                                is_primary=(position == primary_index))
                .on_conflict_do_nothing()
            )

    # ---------------------------------------------------------------- amendments
    if doc.is_amendment:
        await _apply_amendment(session, meta.accession, filing_id, issuer.id,
                               doc.date_of_original_submission, [pad_cik(o.cik) for o in doc.owners],
                               amendment_rows, result)
    elif meta.filed_on is not None:
        await _link_waiting_amendments(session, filing_id, issuer.id, meta.filed_on,
                                       [pad_cik(o.cik) for o in doc.owners], result)

    # A price per ADS on a count of underlying shares (quality.flag_ads), judged now that all of the
    # filing's rows are stored, over the company's rows. The new rows' feed events below carry it.
    if any(row.kind != "holding" and row.price is not None for row in doc.rows):
        per_ads = set(await flag_ads(session, filing_ids=[filing_id]))
        new_tx = [(tx_id, row, [*issues, VALUE_IMPLAUSIBLE] if tx_id in per_ads and VALUE_IMPLAUSIBLE not in issues
                   else issues) for tx_id, row, issues in new_tx]

    # The same trade filed by a related owner (a fund and its general partner) counts once, from
    # the moment it loads (the Today strip and Live totals don't wait for the 30-minute job).
    await mark_duplicates(session, filing_ids=[filing_id])

    # ---------------------------------------------------------------- feed
    # The ticker shown is the linked security's, not the filing's own text (which may be a
    # placeholder such as "NA", or a ticker that now belongs to another company).
    ticker = (await session.execute(select(Security.primary_ticker).where(Security.id == security_id))
              ).scalar_one_or_none() if security_id else None
    base_payload = {
        "accession": meta.accession, "form": meta.form_type, "filed_on": meta.filed_on,
        "accepted_at": meta.accepted_at, "issuer": {"name": issuer.name, "slug": issuer.slug},
        "ticker": ticker, "owner": {"name": primary.name, "slug": primary.slug, "kind": primary.kind,
                                    "role": role_summary(primary_rep)},
        "joint_owners": len(owners), "url": meta.url, "is_10b5_1": doc.aff10b5one,
    }
    if doc.document_type.startswith("3"):
        event_id = await upsert_feed_event(
            session, event_key=f"edgar-f3:{meta.accession}", event_type="insider_holding", source="edgar",
            form_type=meta.form_type, section="people", filing_id=filing_id, entity_id=primary.id,
            security_id=security_id, occurred_on=period, disclosed_at=disclosed_at, is_live=meta.live,
            payload=base_payload | {"holdings": sum(1 for r in doc.rows if r.kind == "holding"),
                                    "no_securities_owned": doc.no_securities_owned},
        )
        if event_id:
            result.feed_event_ids.append(event_id)
    for tx_id, row, issues in new_tx:
        code = (row.code or "").upper() or None
        value = _value(row)
        size = None if value is None or VALUE_IMPLAUSIBLE in issues else float(value)
        event_id = await upsert_feed_event(
            session, event_key=f"tx:edgar:{meta.accession}:{row.table}:{row.line_no}",
            event_type="insider_trade", source="edgar", form_type=meta.form_type, section="people",
            filing_id=filing_id, transaction_id=tx_id, entity_id=primary.id, security_id=security_id,
            size_usd=size, occurred_on=row.transaction_date,
            disclosed_at=disclosed_at, is_live=meta.live,
            payload=base_payload | {
                "code": code, "code_short": CODE_SHORT.get(code or "", code), "code_label": label_for(code),
                "direction": direction_for(code), "open_market": code in OPEN_MARKET_CODES and row.table != "derivative",
                "derivative": row.table == "derivative", "security_title": row.security_title,
                "shares": row.shares, "price": row.price, "price_is_weighted_avg": row.price_is_weighted_avg,
                "value": value, "acquired_disposed": row.acquired_disposed, "traded_on": row.transaction_date,
                "delay_days": (meta.filed_on - row.transaction_date).days
                if meta.filed_on and row.transaction_date and DATE_AFTER_FILING not in issues else None,
                "data_issues": issues,
                # The late-filing check, for the Live table's flag (same rule as the trade row).
                "is_late": None if DATE_AFTER_FILING in issues else _late_flag(doc, row, meta.filed_on)[2],
                "deadline_rule": _late_flag(doc, row, meta.filed_on)[1],
                "exercise_and_sell": bool(groups.get(row.transaction_date)) if row.transaction_date else False,
                "owner_type": _owner_type(row),
            },
        )
        if event_id:
            result.feed_event_ids.append(event_id)

    # A reload (smt reprocess, or the XML fetched again) replaced data_issues with the load-time
    # rules above. The price-against-the-market check needs the company's other trades, so it
    # runs here, in the same transaction: a flagged value never re-enters totals, even briefly.
    # It also syncs the new feed events' flags. Only filings with priced rows can be affected.
    if any(row.kind != "holding" and row.price is not None for row in doc.rows):
        await flag_relative_prices(session, filing_ids=[filing_id])
        await flag_totals_as_price(session, filing_ids=[filing_id])
    return result


@dataclass(frozen=True)
class RowKey:
    """The fields used to pair an amendment row with the original row it corrects."""

    id: int
    table: str
    line_no: int
    security_title: str | None
    traded_on: date | None
    code: str | None
    shares: Decimal | None = None
    price: Decimal | None = None


async def amendment_row_keys(session: AsyncSession, filing_id: int) -> list[RowKey]:
    """The pairing keys of a stored amendment's transaction rows."""
    rows = (await session.execute(
        select(Transaction.id, Transaction.table_name, Transaction.line_no, Transaction.security_title,
               Transaction.traded_on, Transaction.code, Transaction.shares, Transaction.price)
        .where(Transaction.filing_id == filing_id).order_by(Transaction.table_name, Transaction.line_no)
    )).all()
    return [RowKey(r.id, r.table_name, r.line_no, r.security_title, r.traded_on, r.code, r.shares, r.price)
            for r in rows]


async def _link_waiting_amendments(session: AsyncSession, filing_id: int, issuer_id: int, filed_on: date,
                                   owner_ciks: list[str], result: LoadResult) -> None:
    """An original that loads AFTER its 4/A (common in backfills) links the waiting amendment now."""
    waiting = (await session.execute(
        select(Filing.id, Filing.source_key).distinct()
        .join(FilingParty, and_(FilingParty.filing_id == Filing.id, FilingParty.role == "reporting_owner"))
        .join(EntityId, and_(EntityId.entity_id == FilingParty.entity_id, EntityId.scheme == "sec_cik"))
        .where(Filing.source == "edgar", Filing.is_amendment.is_(True), Filing.amends_filing_id.is_(None),
               Filing.issuer_entity_id == issuer_id, Filing.original_filed_on == filed_on,
               EntityId.value.in_(owner_ciks))
    )).all()
    for amendment_id, accession in waiting:
        rows = await amendment_row_keys(session, int(amendment_id))
        await _apply_amendment(session, accession, int(amendment_id), issuer_id, filed_on, owner_ciks,
                               rows, result, original_hint=filing_id)


async def _apply_amendment(session: AsyncSession, accession: str, filing_id: int, issuer_id: int,
                           original_filed_on: date | None, owner_ciks: list[str],
                           amendment_rows: list[RowKey], result: LoadResult,
                           original_hint: int | None = None) -> None:
    """Link a 4/A to its original and supersede the rows it corrects (see module docstring)."""
    if original_filed_on is None:
        result.notes.append("amendment without dateOfOriginalSubmission")
        return
    if original_hint is not None:
        candidates: list[int] = [original_hint]
    else:
        candidates = [int(c) for c in (await session.execute(
            select(Filing.id).distinct()
            .join(FilingParty, and_(FilingParty.filing_id == Filing.id, FilingParty.role == "reporting_owner"))
            .join(EntityId, and_(EntityId.entity_id == FilingParty.entity_id, EntityId.scheme == "sec_cik"))
            .where(
                Filing.source == "edgar", Filing.id != filing_id, Filing.issuer_entity_id == issuer_id,
                Filing.filed_on == original_filed_on, Filing.is_amendment.is_(False),
                EntityId.value.in_(owner_ciks),
            )
        )).scalars().all()]
    if not candidates:
        result.notes.append("original filing not in database yet")
        return  # nothing to double count yet; linked when the original arrives
    originals = await _original_rows(session, candidates, [r.id for r in amendment_rows])
    how = "issuer+owner+date"
    if len(candidates) > 1:
        # Several filings fit the date: take the one the rows match by content (strong rules only).
        strong = _pair_rows(amendment_rows, originals, strong_only=True)
        chosen = {o.filing_id for o in strong.values()}
        if amendment_rows and len(strong) == len(amendment_rows) and len(chosen) == 1:
            candidates, how = [chosen.pop()], "issuer+owner+date+content"
            originals = [o for o in originals if o.filing_id == candidates[0]]
        else:
            note = f"{len(candidates)} candidate originals; not linked"
            result.notes.append(note)
            await _quarantine_once(session, accession, None, f"4/A amendment ambiguous: {note}",
                                   {"candidates": candidates})
            # Only rows that might repeat a row in one of the candidate originals stay out of totals.
            kinds = {(o.table_name, o.code) for o in originals}
            await _flag_unpaired(session, [r.id for r in amendment_rows if (r.table, r.code) in kinds])
            return
    original_id = candidates[0]
    await session.execute(update(Filing).where(Filing.id == filing_id)
                          .values(amends_filing_id=original_id, amendment_match=how))
    if not amendment_rows:
        return  # e.g. a POA-only amendment: supersedes nothing

    pairs = _pair_rows(amendment_rows, originals, strong_only=False,
                       same_count=len(originals) == len(amendment_rows))
    paired_originals = {o.id for o in pairs.values()}
    # Original rows still current and not re-reported: an unpaired amendment row of the same kind might
    # repeat one of them. With none of its kind left, the row is a trade the original left out.
    repeatable = {(o.table_name, o.code) for o in originals if o.id not in paired_originals}
    for row in amendment_rows:
        old = pairs.get(row.id)
        if old is None and (row.table, row.code) not in repeatable:
            await session.execute(update(Transaction).where(Transaction.id == row.id).values(
                review_note="added by the amendment: the original filing has no row it could repeat",
                data_issues=func.array_remove(Transaction.data_issues, AMENDMENT_UNPAIRED)))
            continue
        if old is not None:
            await session.execute(update(Transaction).where(Transaction.id == old.id)
                                  .values(is_current=False, superseded_by_id=row.id))
            await session.execute(update(Transaction).where(Transaction.id == row.id).values(
                supersedes_id=old.id, review_note=None,
                data_issues=func.array_remove(Transaction.data_issues, AMENDMENT_UNPAIRED)))
        else:
            await _flag_unpaired(session, [row.id])
            await _quarantine_once(session, accession, f"{row.table}:{row.line_no}",
                                   "4/A row could not be paired with its original",
                                   {"original_filing_id": original_id})
            result.notes.append(f"unpaired amendment row {row.table}:{row.line_no}")


async def _original_rows(session: AsyncSession, filing_ids: list[int], amendment_ids: list[int]) -> list[Any]:
    """Rows of the candidate originals an amendment can pair with: current rows, plus rows
    already superseded by this very amendment (so re-loading a 4/A pairs them again)."""
    return list((await session.execute(
        select(Transaction.id, Transaction.filing_id, Transaction.table_name, Transaction.line_no,
               Transaction.security_title, Transaction.traded_on, Transaction.code, Transaction.shares,
               Transaction.price, Transaction.superseded_by_id)
        .where(Transaction.filing_id.in_(filing_ids),
               Transaction.is_current.is_(True) | Transaction.superseded_by_id.in_(amendment_ids or [-1]))
        .order_by(Transaction.filing_id, Transaction.table_name, Transaction.line_no)
    )).all())


_Rule = Callable[[RowKey, Any], bool]
#: Strongest first. Title, table and code must always match.
_STRONG_RULES: tuple[_Rule, ...] = (
    lambda r, o: o.traded_on == r.traded_on and o.shares == r.shares,  # price or ownership corrected
    lambda r, o: o.shares == r.shares,                                 # the date corrected
)
_WEAK_RULES: tuple[_Rule, ...] = (
    lambda r, o: o.traded_on == r.traded_on and o.price == r.price,    # the shares corrected
    lambda r, o: o.traded_on == r.traded_on,                           # unique by date
    lambda r, o: _near(r, o),                                          # unique nearby row, same title+code
)


def _near(r: RowKey, o: Any) -> bool:
    """The last content rule: within 5 days and within 50% of the shares (so an unrelated row weeks
    apart can't replace a real trade just because it is the only one with that title and code)."""
    if r.traded_on is not None and o.traded_on is not None and abs((r.traded_on - o.traded_on).days) > 5:
        return False
    if r.shares and o.shares:
        low, high = sorted((r.shares, o.shares))
        return high <= low * Decimal(2) if low > 0 else False
    return True


def _pair_rows(rows: list[RowKey], originals: list[Any], *, strong_only: bool,
               same_count: bool = False) -> dict[int, Any]:
    """Pair amendment rows with original rows. Returns {amendment row id: original row}.

    Rules run as passes, strongest first, each over every still-unpaired row,
    so a weak rule can't take an original that a strong rule would give to
    another row. A pair needs exactly one candidate. Rows already paired with
    this amendment (a re-load) keep their pair.
    """
    pairs: dict[int, Any] = {}
    used: set[int] = set()
    for row in rows:
        for o in originals:
            if o.superseded_by_id == row.id:
                pairs[row.id] = o
                used.add(o.id)
    rules = _STRONG_RULES if strong_only else _STRONG_RULES + _WEAK_RULES
    for rule in rules:
        for row in rows:
            if row.id in pairs:
                continue
            match = [o for o in originals if o.id not in used and o.table_name == row.table
                     and o.code == row.code and o.security_title == row.security_title and rule(row, o)]
            if len(match) == 1:
                pairs[row.id] = match[0]
                used.add(match[0].id)
    if same_count and not strong_only:  # last resort: the same position, same number of rows
        for row in rows:
            if row.id in pairs:
                continue
            match = [o for o in originals if o.id not in used and o.table_name == row.table
                     and o.line_no == row.line_no]
            if len(match) == 1:
                pairs[row.id] = match[0]
                used.add(match[0].id)
    return pairs


async def _flag_unpaired(session: AsyncSession, tx_ids: list[int]) -> None:
    """Keep unpaired amendment rows visible but out of totals (both versions may describe one trade)."""
    if not tx_ids:
        return
    await session.execute(update(Transaction).where(
        Transaction.id.in_(tx_ids), ~Transaction.data_issues.contains([AMENDMENT_UNPAIRED])
    ).values(data_issues=func.array_append(Transaction.data_issues, AMENDMENT_UNPAIRED),
             review_note="amendment row could not be paired with the row it corrects; left out of totals"))


async def _quarantine_once(session: AsyncSession, accession: str, row_ref: str | None, reason: str,
                           payload: dict[str, Any]) -> None:
    """Set an item aside for review unless the same open item is already there (re-loads are idempotent)."""
    same_row = Quarantine.row_ref.is_(None) if row_ref is None else Quarantine.row_ref == row_ref
    exists = (await session.execute(select(Quarantine.id).where(
        Quarantine.source == "edgar", Quarantine.filing_ref == accession, Quarantine.reason == reason,
        same_row, Quarantine.resolved_at.is_(None)))).first()
    if exists is None:
        await session.execute(insert(Quarantine).values(source="edgar", filing_ref=accession, row_ref=row_ref,
                                                        reason=reason, payload=payload))


async def reset_amendment_links(session: AsyncSession, filing_id: int) -> None:
    """Undo an amendment's pairing, so it can be paired again from scratch under the current rules."""
    await session.execute(text("""
        UPDATE transactions o SET is_current = true, superseded_by_id = NULL
          FROM transactions a
         WHERE a.filing_id = :f AND o.superseded_by_id = a.id"""), {"f": filing_id})
    await session.execute(text("""
        UPDATE transactions SET supersedes_id = NULL, review_note = NULL,
               data_issues = array_remove(data_issues, :flag)
         WHERE filing_id = :f"""), {"f": filing_id, "flag": AMENDMENT_UNPAIRED})


async def repair_all_amendments(session: AsyncSession) -> dict[str, int]:
    """Re-pair every amendment from scratch, oldest first (after a change to the pairing rules)."""
    rows = (await session.execute(text("""
        SELECT f.id, f.source_key, f.issuer_entity_id, f.original_filed_on FROM filings f
         WHERE f.source = 'edgar' AND f.is_amendment AND f.original_filed_on IS NOT NULL
           AND f.issuer_entity_id IS NOT NULL AND f.form_type IN ('3/A', '4/A', '5/A')
         ORDER BY f.filed_on, f.id"""))).all()
    for f in rows:
        await reset_amendment_links(session, int(f.id))
    for f in rows:
        owner_ciks = [str(c) for c in (await session.execute(text(
            "SELECT x.value FROM filing_parties p JOIN entity_ids x ON x.entity_id = p.entity_id "
            "AND x.scheme = 'sec_cik' WHERE p.filing_id = :f AND p.role = 'reporting_owner'"), {"f": f.id})).scalars()]
        await _apply_amendment(session, f.source_key, int(f.id), int(f.issuer_entity_id), f.original_filed_on,
                               owner_ciks, await amendment_row_keys(session, int(f.id)),
                               LoadResult(filing_id=int(f.id)))
    paired = int((await session.execute(text(
        "SELECT count(*) FROM transactions t JOIN filings f ON f.id = t.filing_id "
        "WHERE f.is_amendment AND t.supersedes_id IS NOT NULL"))).scalar_one())
    unpaired = int((await session.execute(text(
        "SELECT count(*) FROM transactions WHERE :flag = ANY(data_issues)"), {"flag": AMENDMENT_UNPAIRED})).scalar_one())
    return {"amendments": len(rows), "rows_paired": paired, "rows_unpaired": unpaired}


async def repair_amendments(session: AsyncSession, *, limit: int = 2000) -> dict[str, int]:
    """Re-run pairing for amendments that still have unpaired rows or an unresolved original.

    Pairing rules improve over time (and an original may have arrived since),
    so this runs from the quality job. Rows that now pair are superseded
    properly; rows that still don't are flagged so they stay out of totals.
    Review items that are now resolved are closed with a note.
    """
    todo = (await session.execute(text("""
        SELECT DISTINCT f.id, f.source_key, f.issuer_entity_id, f.original_filed_on
          FROM filings f
         WHERE f.source = 'edgar' AND f.is_amendment AND f.original_filed_on IS NOT NULL
           AND f.issuer_entity_id IS NOT NULL
           AND (EXISTS (SELECT 1 FROM transactions t WHERE t.filing_id = f.id AND t.supersedes_id IS NULL
                          AND (t.review_note LIKE 'amendment row could not be paired%'
                               OR :flag = ANY(t.data_issues)))
                OR EXISTS (SELECT 1 FROM quarantine q WHERE q.filing_ref = f.source_key
                             AND q.reason LIKE '4/A amendment ambiguous%' AND q.resolved_at IS NULL))
         ORDER BY f.id LIMIT :limit"""), {"flag": AMENDMENT_UNPAIRED, "limit": limit})).all()
    counts = {"amendments_checked": len(todo), "rows_paired": 0}
    for f in todo:
        owner_ciks = [str(c) for c in (await session.execute(text(
            "SELECT x.value FROM filing_parties p JOIN entity_ids x ON x.entity_id = p.entity_id "
            "AND x.scheme = 'sec_cik' WHERE p.filing_id = :f AND p.role = 'reporting_owner'"), {"f": f.id})).scalars()]
        rows = await amendment_row_keys(session, int(f.id))
        before = len(await _paired_ids(session, [r.id for r in rows]))
        await _apply_amendment(session, f.source_key, int(f.id), int(f.issuer_entity_id), f.original_filed_on,
                               owner_ciks, rows, LoadResult(filing_id=int(f.id)))
        after = len(await _paired_ids(session, [r.id for r in rows]))
        counts["rows_paired"] += max(0, after - before)
    # Close review items the re-check resolved.
    await session.execute(text("""
        UPDATE quarantine q SET resolved_at = now(), resolution = 'paired on re-check'
          FROM filings f JOIN transactions t ON t.filing_id = f.id
         WHERE q.resolved_at IS NULL AND q.reason = '4/A row could not be paired with its original'
           AND q.filing_ref = f.source_key AND q.row_ref = t.table_name || ':' || t.line_no
           AND t.supersedes_id IS NOT NULL"""))
    # A row the original never had (nothing it could repeat) is a trade the amendment adds: it counts.
    await session.execute(text("""
        UPDATE quarantine q SET resolved_at = now(), resolution = 'counted as a trade the original left out'
          FROM filings f JOIN transactions t ON t.filing_id = f.id
         WHERE q.resolved_at IS NULL AND q.reason = '4/A row could not be paired with its original'
           AND q.filing_ref = f.source_key AND q.row_ref = t.table_name || ':' || t.line_no
           AND t.supersedes_id IS NULL AND NOT (:flag = ANY(t.data_issues))"""), {"flag": AMENDMENT_UNPAIRED})
    await session.execute(text("""
        UPDATE quarantine q SET resolved_at = now(), resolution = 'original identified on re-check'
          FROM filings f
         WHERE q.resolved_at IS NULL AND q.reason LIKE '4/A amendment ambiguous%'
           AND q.filing_ref = f.source_key AND f.amends_filing_id IS NOT NULL"""))
    return counts


async def _paired_ids(session: AsyncSession, tx_ids: list[int]) -> set[int]:
    if not tx_ids:
        return set()
    return {int(i) for i in (await session.execute(
        select(Transaction.id).where(Transaction.id.in_(tx_ids), Transaction.supersedes_id.is_not(None))
    )).scalars()}
