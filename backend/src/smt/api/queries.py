"""SQL shared by several routers: what counts in totals, the trade-row shape used by every
trades table, and the role a person is introduced by."""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any

from smt.api.schemas import EntityLink, FeedItem, TradeCopy, TradeRow
from smt.api.wording import company_name, relationship_role
from smt.domain.codes import label_for

# ---------------------------------------------------------------- feed rows
#: A feed event with its owner's CURRENT name and page (alias `e`). The payload is frozen when
#: the filing loads; a person renamed later (a corrected name) must show under the new name.
#: `owner_rel` is the owner's relationship as given on the event's filing (Forms 3/4/5), so the
#: role reads in the site's wording (see feed_item).
FEED_SELECT = ("SELECT e.id, e.event_type, e.form_type, e.section, e.detected_at, e.disclosed_at, "
               "e.occurred_on, e.size_usd, e.transaction_id, e.payload, o.name AS owner_name, "
               "o.slug AS owner_slug, o.kind AS owner_kind, "
               "(SELECT fp.relationship FROM filing_parties fp WHERE fp.filing_id = e.filing_id "
               "AND fp.entity_id = e.entity_id AND fp.role = 'reporting_owner') AS owner_rel "
               "FROM feed_events e LEFT JOIN entities o ON o.id = e.entity_id")


def entity_link(name: str, slug: str, kind: str, tickers: Collection[str | None] = ()) -> EntityLink:
    """A link to a person, company or fund, with the name as it reads on the site.

    A company or fund reads like a company, not in EDGAR's capitals ("UNITED
    MICROELECTRONICS CORP" is "United Microelectronics Corp", smt.api.wording.company_name);
    `tickers` are its own tickers, which stay in capitals. A person's name is already stored
    as it is shown (smt.domain.names).
    """
    if kind == "person":
        return EntityLink(name=name, slug=slug, kind=kind)
    return EntityLink(name=company_name(name, [t for t in tickers if t]), slug=slug, kind=kind)


def feed_item(r: Any) -> FeedItem:
    """Build a FeedItem from a FEED_SELECT row, with the owner's current name and slug.

    The owner's role is written again from the filing's relationship with the same wording as
    the profile and the today strip (smt.api.wording.relationship_role): the payload stores the
    title as the filer typed it ("Director, CO-FOUNDER"), which reads "Director, Co-Founder".
    Form 144 notices keep the payload's role (their relationships are fixed EDGAR choices).
    """
    payload = dict(r.payload or {})
    issuer = payload.get("issuer")
    if isinstance(issuer, dict) and issuer.get("name"):
        # Stored as EDGAR spells it; shown like every other company name on the site.
        ticker = payload.get("ticker")
        payload["issuer"] = {**issuer, "name": company_name(issuer["name"], [ticker] if ticker else [])}
    owner = payload.get("owner")
    if isinstance(owner, dict):
        owner = dict(owner)
        if r.owner_name:
            owner.update(name=r.owner_name, slug=r.owner_slug, kind=r.owner_kind)
        if owner.get("name") and owner.get("kind") in ("company", "fund"):
            owner["name"] = company_name(owner["name"])
        role = relationship_role(r.owner_rel) if r.owner_rel else None
        if role:
            owner["role"] = role
        payload["owner"] = owner
    return FeedItem(id=r.id, event_type=r.event_type, form_type=r.form_type, section=r.section,
                    detected_at=r.detected_at, disclosed_at=r.disclosed_at, occurred_on=r.occurred_on,
                    size_usd=r.size_usd, transaction_id=r.transaction_id, payload=payload)


# ---------------------------------------------------------------- what counts in totals
# One definition, used by every total, ranking and "today" figure (alias `t` = transactions):
# current (not replaced by an amendment); an open-market trade in the shares themselves
# (code P or S in Table I, not an option contract); not a second copy of a trade another
# filing already reports (a fund and its general partner); and not an amendment row we
# couldn't pair with the row it corrects (it may repeat that row). See smt.ingest.quality.
COUNTED = ("t.is_current AND t.is_open_market AND t.duplicate_of_id IS NULL "
           "AND NOT ('amendment_unpaired' = ANY(t.data_issues))")
#: The row's dollar value is usable (not a figure that can't be right as filed).
VALUE_OK = "NOT ('value_implausible' = ANY(t.data_issues))"
#: The trade date is usable (not later than the filing itself).
DATE_OK = "NOT ('date_after_filing' = ANY(t.data_issues))"
#: Per person (alias `o` = transaction_owners): like COUNTED, except that a second copy still
#: counts for an owner who isn't listed on the counted copy (it is their trade too).
PERSON_COUNTED = ("t.is_current AND t.is_open_market AND NOT ('amendment_unpaired' = ANY(t.data_issues)) "
                  "AND (t.duplicate_of_id IS NULL OR NOT EXISTS (SELECT 1 FROM transaction_owners o2 "
                  "WHERE o2.transaction_id = t.duplicate_of_id AND o2.entity_id = o.entity_id))")

# ---------------------------------------------------------------- a person's lead role
#: Officer titles that lead a person's description, most senior first (matched as lower-case
#: substrings of the title, so "President & CEO" ranks with "ceo").
TOP_TITLES = ("chief executive", "ceo", "president", "chief financial", "cfo", "chief operating", "coo",
              "chair")
#: "Vice President" and "Vice Chair" are not the President or the Chair: removed before matching.
#: (The same pattern works in Python and in PostgreSQL; no "(?:", which SQL text() would read as a
#: ":president" parameter.)
_VICE = r"vice[ -]?(president|chair)"


def title_rank(title: str | None) -> int:
    """How senior an officer title is: its place in TOP_TITLES (0 = most senior), or
    len(TOP_TITLES) when it names none of them. LEAD_ROLE_ORDER does the same in SQL."""
    text = re.sub(_VICE, "", (title or "").lower())
    return next((i for i, t in enumerate(TOP_TITLES) if t in text), len(TOP_TITLES))


#: The order that picks the one role a person is introduced by (alias `r` = roles), best first:
#: officer, then director, then 10% owner, then anything else; among officer roles the most
#: senior title (title_rank); then the most recently seen; then the newest row, so ties never
#: fall out at random. The People directory, the search palette's person label and the
#: profile summary all use it (the summary sorts in Python with insiders._role_rank, which
#: must agree; a test runs both on the same roles). Tim Cook, a NIKE director on his latest
#: filing, is still introduced as Apple's Chief Executive Officer.
LEAD_ROLE_ORDER = (
    "CASE r.role_type WHEN 'officer' THEN 0 WHEN 'director' THEN 1 WHEN 'ten_pct_owner' THEN 2 ELSE 3 END, "
    "CASE WHEN r.role_type <> 'officer' THEN 0 "
    + " ".join(f"WHEN regexp_replace(lower(r.title), '{_VICE}', '', 'g') LIKE '%{t}%' THEN {i}"
               for i, t in enumerate(TOP_TITLES))
    + f" ELSE {len(TOP_TITLES)} END, r.last_seen_on DESC NULLS LAST, r.id DESC"
)


def org_tickers(alias: str) -> str:
    """SQL for every ticker the company `alias` (an entities alias) has traded under, as a
    text array (NULL when it has none): its securities' primary tickers and all their
    ticker ids, current or past. company_name keeps a word such as "ACNB" in capitals
    when it is one of these (see smt.api.wording)."""
    return ("(SELECT array_agg(DISTINCT v.t) FROM securities s "
            "LEFT JOIN security_ids x ON x.security_id = s.id AND x.scheme = 'ticker' "
            "CROSS JOIN LATERAL (VALUES (s.primary_ticker), (x.value)) v(t) "
            f"WHERE s.issuer_entity_id = {alias}.id AND v.t IS NOT NULL)")


#: PERSON_COUNTED for the person whose trades list this is (bind `:eid`), as one
#: true/false value per row. PERSON_COUNTED needs that person's transaction_owners row
#: under the alias `o`, so it runs in a sub-query: inside the brackets `o` is the
#: owner row; outside them (in TRADE_SQL) `o` stays the row's owner entity.
PERSON_IN_TOTALS = ("EXISTS (SELECT 1 FROM transaction_owners o WHERE o.transaction_id = t.id "
                    "AND o.entity_id = :eid AND " + PERSON_COUNTED + ")")

#: For a row that is a second copy of a trade (alias `t`): the copy the site-wide totals
#: (COUNTED) count, as a one-column sub-query for a LATERAL join. mark_duplicates points
#: each copy at the earliest copy it is linked to, and links don't always chain: a
#: fund's row (B, indirect ownership) can point at another owner's row (A) because both
#: report the same trade within 3 days, while the fund files the trade again weeks later
#: (C) and C points at B (same owner) but not at A (no owner in common, and filed more
#: than 3 days apart). So follow the pointers up to the row that points nowhere: that copy is
#: counted. Rows only ever point at lower ids, so the walk always ends; the depth cap is
#: a safety net. Rows that aren't copies get no row here.
COUNTED_COPY = """
    WITH RECURSIVE chain(id, next, depth) AS (
        SELECT d.id, d.duplicate_of_id, 1 FROM transactions d WHERE d.id = t.duplicate_of_id
        UNION ALL
        SELECT d.id, d.duplicate_of_id, c.depth + 1 FROM chain c JOIN transactions d ON d.id = c.next
         WHERE c.depth < 20
    )
    SELECT chain.id FROM chain ORDER BY chain.depth DESC LIMIT 1"""

#: The same for one person's trades list: the copy that person's own totals count
#: (PERSON_COUNTED), which is the row this one points at directly. PERSON_COUNTED leaves
#: a copy out of a person's totals only when that person is an owner of the row it
#: points at, and that row is counted for them: its own pointer can't lead to another
#: row of theirs, because a row the person shares with this one would have been linked
#: to this row directly (mark_duplicates picks the earliest linked copy).
PERSON_COUNTED_COPY = "SELECT t.duplicate_of_id AS id WHERE t.duplicate_of_id IS NOT NULL"

# One trade, joined to everything a table row needs. `{where}` and `{order}` are
# filled by the caller with parameterized conditions (never with user input).
# `in_totals` answers "does this row count in the totals shown next to it?" with the
# shared rule above, so a table can mark the rows that don't add to its page's figures.
# `dup_*` is the copy those totals count when this row is a second copy of the same trade
# (COUNTED_COPY or PERSON_COUNTED_COPY).
_TRADE_SQL_TEMPLATE = """
SELECT t.id, f.source_key AS accession, f.form_type, f.url AS filing_url, t.traded_on, f.filed_on,
       f.accepted_at, t.detected_at, t.delay_days, t.is_late, t.deadline_rule, t.code, t.direction,
       t.is_open_market, t.is_derivative, t.security_title, s.primary_ticker AS ticker,
       i.name AS issuer_name, i.slug AS issuer_slug, i.kind AS issuer_kind,
       o.name AS owner_name, o.slug AS owner_slug, o.kind AS owner_kind,
       (SELECT count(*) FROM transaction_owners x WHERE x.transaction_id = t.id) AS joint_owners,
       t.owner_type, t.shares, t.price, t.price_is_weighted_avg, t.value, t.shares_owned_after,
       t.is_10b5_1, t.exercise_and_sell_group IS NOT NULL AS exercise_and_sell, t.is_current,
       t.data_issues, (@IN_TOTALS@) AS in_totals,
       dup.id AS dup_id, dupo.name AS dup_owner_name, dupo.slug AS dup_owner_slug, dupo.kind AS dup_owner_kind
FROM transactions t
JOIN filings f ON f.id = t.filing_id
JOIN entities o ON o.id = t.entity_id
LEFT JOIN entities i ON i.id = t.issuer_entity_id
LEFT JOIN securities s ON s.id = t.security_id
LEFT JOIN LATERAL (@COUNTED_COPY@) counted_copy ON true
LEFT JOIN transactions dup ON dup.id = counted_copy.id
LEFT JOIN entities dupo ON dupo.id = dup.entity_id
WHERE {where}
ORDER BY {order}
LIMIT :limit
"""
#: Trade rows whose `in_totals` and `duplicate_of` follow the site-wide totals (ticker
#: pages, the quick look).
TRADE_SQL = _TRADE_SQL_TEMPLATE.replace("@IN_TOTALS@", COUNTED).replace("@COUNTED_COPY@", COUNTED_COPY)
#: Trade rows on one person's list: `in_totals` and `duplicate_of` follow that person's
#: own totals (bind `:eid`).
PERSON_TRADE_SQL = (_TRADE_SQL_TEMPLATE.replace("@IN_TOTALS@", PERSON_IN_TOTALS)
                    .replace("@COUNTED_COPY@", PERSON_COUNTED_COPY))


def trade_row(r: Any) -> TradeRow:
    """Map a TRADE_SQL (or PERSON_TRADE_SQL) row to the API model."""
    duplicate_of = None
    if r.dup_id is not None:
        duplicate_of = TradeCopy(trade_id=r.dup_id, owner=entity_link(
            r.dup_owner_name, r.dup_owner_slug, r.dup_owner_kind))
    return TradeRow(
        id=r.id, accession=r.accession, form_type=r.form_type, filing_url=r.filing_url,
        traded_on=r.traded_on, filed_on=r.filed_on, accepted_at=r.accepted_at, detected_at=r.detected_at,
        delay_days=r.delay_days, is_late=r.is_late, deadline_rule=r.deadline_rule, code=r.code,
        code_label=label_for(r.code), direction=r.direction, is_open_market=r.is_open_market,
        is_derivative=r.is_derivative, security_title=r.security_title, ticker=r.ticker,
        issuer=entity_link(r.issuer_name, r.issuer_slug, r.issuer_kind, [r.ticker]) if r.issuer_slug else None,
        owner=entity_link(r.owner_name, r.owner_slug, r.owner_kind),
        joint_owners=r.joint_owners, owner_type=r.owner_type, shares=r.shares, price=r.price,
        price_is_weighted_avg=r.price_is_weighted_avg, value=r.value, shares_owned_after=r.shares_owned_after,
        is_10b5_1=r.is_10b5_1, exercise_and_sell=r.exercise_and_sell, is_current=r.is_current,
        data_issues=list(r.data_issues or []), in_totals=bool(r.in_totals), duplicate_of=duplicate_of,
    )


def parse_codes(codes: str | None) -> list[str] | None:
    """'P,S' -> ['P', 'S']; None/'all' -> None (no filter)."""
    if not codes or codes.lower() == "all":
        return None
    return [c.strip().upper() for c in codes.split(",") if c.strip()]
