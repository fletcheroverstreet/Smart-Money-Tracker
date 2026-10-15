"""The one-line profile summary (smt.api.routers.insiders). Cases come from real profiles."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from smt.api.routers.insiders import _role_rank, _summary, clean_title
from smt.api.schemas import EntityLink, PersonStats, RoleOut


def role(role_type: str, title: str | None, org: str, seen: date | None, row_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=row_id, role_type=role_type, title=title, last_seen_on=seen, name=org)


def test_the_top_officer_role_leads_even_when_a_board_seat_is_more_recent() -> None:
    # Tim Cook: CEO of Apple, and a director of NIKE (his NIKE filing is the latest).
    roles = [role("director", None, "NIKE, Inc.", date(2026, 9, 1)),
             role("officer", "Chief Executive Officer", "Apple Inc.", date(2026, 4, 2))]
    assert [r.name for r in sorted(roles, key=_role_rank)] == ["Apple Inc.", "NIKE, Inc."]


def test_roles_seen_the_same_day_fall_back_to_the_newest_row() -> None:
    # Two board seats last seen on the same day: the newer row leads, every time; a role
    # never seen comes after the dated ones.
    roles = [role("director", "", "A Corp", date(2026, 9, 1), row_id=7),
             role("director", "", "B Corp", date(2026, 9, 1), row_id=9),
             role("director", "", "C Corp", None, row_id=12)]
    assert [r.name for r in sorted(roles, key=_role_rank)] == ["B Corp", "A Corp", "C Corp"]


def test_summary_reads_as_plain_sentences() -> None:
    nike = EntityLink(name="NIKE, Inc.", slug="nike-inc-320187", kind="company")
    roles = [RoleOut(organization=nike, role_type="director", title="Director", start_date=None,
                     last_seen_on=date(2026, 9, 1))]
    stats = PersonStats(trades=1, open_market_buys=0, open_market_sells=1, buy_value=None, sell_value=None,
                        first_filed=date(2026, 9, 1), last_filed=date(2026, 9, 1), median_delay_days=None,
                        late_filings=0, sales_under_10b5_1_pct=None)
    text = _summary("Timothy D Cook", roles, stats)
    assert text == ("Timothy D Cook is a Director of NIKE, Inc. "
                    "1 reported transaction on file, including 1 open-market sale.")
    assert ".." not in text


def test_placeholder_and_all_caps_titles() -> None:
    assert clean_title("See Remarks", "officer") == "Officer"
    assert clean_title("CHIEF FINANCIAL OFFICER", "officer") == "Chief Financial Officer"
    assert clean_title("EVP AND GENERAL COUNSEL", "officer") == "EVP and General Counsel"
    assert clean_title(None, "ten_pct_owner") == "10% owner"
