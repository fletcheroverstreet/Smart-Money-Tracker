"""Pairing 4/A amendment rows with the original rows they correct (smt.ingest.ownership._pair_rows).

The main case is real: Fifth Third (FITB) 0000035527-26-000043 reported seven
sales, four of them on 2026-02-12. Its 4/A 0000035527-26-000186 re-reports
three of those with corrected prices. Pairing by date alone is ambiguous (four
candidates), so the rows used to stay unpaired and the three sales were counted
twice. Pairing by date and shares resolves each one uniquely.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from smt.ingest.ownership import RowKey, _pair_rows

D = Decimal
T = "Common Stock"


def original(id_: int, line: int, day: date, shares: str, price: str, superseded_by: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=id_, filing_id=1, table_name="nonderivative", line_no=line, security_title=T,
                           traded_on=day, code="S", shares=D(shares), price=D(price), superseded_by_id=superseded_by)


FEB11, FEB12 = date(2026, 2, 11), date(2026, 2, 12)
FITB_ORIGINAL = [
    original(110998, 1, FEB11, "9139.10", "54.91"),
    original(110999, 2, FEB11, "5673.58", "54.74"),
    original(111000, 3, FEB11, "581", "54.74"),
    original(111001, 4, FEB12, "32769", "53.20"),
    original(111002, 5, FEB12, "101", "53.21"),
    original(111003, 6, FEB12, "49175", "53.00"),
    original(111004, 7, FEB12, "77", "53.29"),
]
FITB_AMENDMENT = [
    RowKey(1558, "nonderivative", 1, T, FEB12, "S", D("32769"), D("54.68")),
    RowKey(1559, "nonderivative", 2, T, FEB12, "S", D("101"), D("54.68")),
    RowKey(1560, "nonderivative", 3, T, FEB12, "S", D("49175"), D("54.68")),
]


def test_fitb_price_corrections_pair_by_date_and_shares() -> None:
    pairs = _pair_rows(FITB_AMENDMENT, FITB_ORIGINAL, strong_only=False)
    assert {k: v.id for k, v in pairs.items()} == {1558: 111001, 1559: 111002, 1560: 111003}


def test_a_reload_keeps_the_existing_pairs() -> None:
    # After the first load, the originals are superseded by the amendment rows.
    loaded = [o if o.id not in (111001, 111002, 111003) else
              SimpleNamespace(**{**vars(o), "superseded_by_id": {111001: 1558, 111002: 1559, 111003: 1560}[o.id]})
              for o in FITB_ORIGINAL]
    pairs = _pair_rows(FITB_AMENDMENT, loaded, strong_only=False)
    assert {k: v.id for k, v in pairs.items()} == {1558: 111001, 1559: 111002, 1560: 111003}


def test_strong_rules_run_before_weak_ones() -> None:
    # Row A matches only by date (weak); row B matches original X by date and shares (strong).
    x = original(1, 1, FEB12, "500", "10")
    y = original(2, 2, FEB12, "900", "10")
    a = RowKey(10, "nonderivative", 1, T, FEB12, "S", D("777"), D("10"))
    b = RowKey(11, "nonderivative", 2, T, FEB12, "S", D("500"), D("11"))
    pairs = _pair_rows([a, b], [x, y], strong_only=False)
    assert pairs[11].id == 1  # B got X by the strong rule...
    assert pairs[10].id == 2  # ...and A got what was left.


def test_ambiguous_rows_stay_unpaired() -> None:
    x = original(1, 1, FEB12, "500", "10")
    y = original(2, 2, FEB12, "500", "10")
    row = RowKey(10, "nonderivative", 1, T, FEB12, "S", D("500"), D("12"))
    assert _pair_rows([row], [x, y], strong_only=False) == {}


def test_strong_only_ignores_weak_matches() -> None:
    x = original(1, 1, FEB12, "500", "10")
    row = RowKey(10, "nonderivative", 1, T, FEB12, "S", D("600"), D("10"))
    assert _pair_rows([row], [x], strong_only=True) == {}
    assert _pair_rows([row], [x], strong_only=False)[10].id == 1


def test_the_last_rule_needs_a_nearby_similar_row() -> None:
    # Real case 0001924953-26-000005: an amendment row dated 5/29 must not replace the 4/10 purchase.
    far = original(1, 1, date(2026, 4, 10), "5000", "10")
    row = RowKey(10, "nonderivative", 1, T, date(2026, 5, 29), "S", D("900"), D("12"))
    assert _pair_rows([row], [far], strong_only=False) == {}
    near = original(2, 1, date(2026, 5, 27), "1000", "11")
    assert _pair_rows([row], [near], strong_only=False)[10].id == 2
