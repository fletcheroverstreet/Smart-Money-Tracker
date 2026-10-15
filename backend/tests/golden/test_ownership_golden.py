"""Golden tests for the Forms 3/4/5 parser against real SEC filings.

Every expected value below was typed by hand from reading the fixture XML in
tests/fixtures/edgar/ownership/ (saved byte-for-byte from EDGAR). None of it
was produced by running the parser.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

import pytest

from smt.sources.forms.ownership import parse_ownership
from smt.sources.forms.types import OwnershipDocument, OwnershipRow, RowKind, TableName

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "ownership"


@cache
def load(accession: str) -> OwnershipDocument:
    """Parse one saved fixture by accession number (once per test session)."""
    return parse_ownership((FIXTURES / f"{accession}.xml").read_bytes())


def row(doc: OwnershipDocument, table: TableName, kind: RowKind, line_no: int) -> OwnershipRow:
    """Find the row with this (table, kind, line_no); fail loudly if absent."""
    matches = [r for r in doc.rows if r.table == table and r.kind == kind and r.line_no == line_no]
    assert len(matches) == 1, f"expected exactly one {table}/{kind}/{line_no}"
    return matches[0]


def row_counts(doc: OwnershipDocument) -> dict[tuple[str, str], int]:
    """Number of rows per (table, kind)."""
    return dict(Counter((r.table, r.kind) for r in doc.rows))


def owner_flags(doc: OwnershipDocument) -> list[tuple[str, bool, bool, bool, bool]]:
    """(cik, director, officer, 10% owner, other) for each owner, in filing order."""
    return [
        (o.cik, o.is_director, o.is_officer, o.is_ten_percent_owner, o.is_other) for o in doc.owners
    ]


# ---------------------------------------------------------------- DELL / Silver Lake Form 4


class TestDellSilverLakeForm4:
    """0001193125-26-396718: 5 joint owners, M conversion + many weighted-average S sales."""

    @pytest.fixture
    def doc(self) -> OwnershipDocument:
        return load("0001193125-26-396718")

    def test_header(self, doc: OwnershipDocument) -> None:
        assert doc.document_type == "4"
        assert doc.schema_version == "X0609"
        assert doc.period_of_report == date(2026, 9, 17)
        assert doc.issuer_cik == "0001571996"
        assert doc.issuer_name == "Dell Technologies Inc."
        assert doc.issuer_ticker == "DELL"
        assert doc.aff10b5one is False
        assert doc.is_amendment is False
        assert doc.date_of_original_submission is None
        assert doc.not_subject_to_section16 is None
        assert doc.no_securities_owned is None

    def test_owners(self, doc: OwnershipDocument) -> None:
        assert owner_flags(doc) == [
            ("0001552054", True, False, True, False),
            ("0001672566", True, False, True, False),
            ("0001672568", True, False, True, False),
            ("0001418226", True, False, True, False),
            ("0001651403", True, False, False, False),  # Egon Durban: director only
        ]
        assert doc.owners[0].name == "Silver Lake Partners IV, L.P."
        assert doc.owners[4].name == "Durban Egon"
        assert all(o.officer_title is None for o in doc.owners)

    def test_row_counts(self, doc: OwnershipDocument) -> None:
        assert row_counts(doc) == {
            ("nonderivative", "transaction"): 17,
            ("nonderivative", "holding"): 5,
            ("derivative", "transaction"): 1,
            ("derivative", "holding"): 1,
        }

    def test_conversion_row_has_footnote_only_price(self, doc: OwnershipDocument) -> None:
        # Class B -> Class C conversion: the price field holds only F1 and F2.
        r = row(doc, "nonderivative", "transaction", 1)
        assert r.security_title == "Class C Common Stock"
        assert r.transaction_date == date(2026, 9, 17)
        assert r.form_type == "4"
        assert r.code == "M"
        assert r.equity_swap is False
        assert r.shares == Decimal("70460")
        assert r.price is None
        assert r.price_is_weighted_avg is False
        assert r.acquired_disposed == "A"
        assert r.shares_owned_after == Decimal("152015")
        assert r.direct_indirect == "I"
        assert r.nature_of_ownership == "Held through Silver Lake Partners IV, L.P."
        assert r.footnote_ids == ("F1", "F2", "F3", "F4")

    def test_first_sale_weighted_average(self, doc: OwnershipDocument) -> None:
        r = row(doc, "nonderivative", "transaction", 2)
        assert r.code == "S"
        assert r.shares == Decimal("406")
        assert r.price == Decimal("577.60")
        assert r.price_is_weighted_avg is True
        assert r.acquired_disposed == "D"
        assert r.shares_owned_after == Decimal("151609")
        assert r.footnote_ids == ("F10", "F3", "F4")

    def test_last_sale_is_durban_direct(self, doc: OwnershipDocument) -> None:
        r = row(doc, "nonderivative", "transaction", 17)
        assert r.code == "S"
        assert r.shares == Decimal("1800")
        assert r.price == Decimal("582.01")
        assert r.price_is_weighted_avg is True
        assert r.shares_owned_after == Decimal("1383800")
        assert r.direct_indirect == "D"
        assert r.nature_of_ownership is None
        assert r.footnote_ids == ("F25", "F8")

    def test_holding(self, doc: OwnershipDocument) -> None:
        r = row(doc, "nonderivative", "holding", 1)
        assert r.security_title == "Class C Common Stock"
        assert r.code is None
        assert r.shares is None
        assert r.transaction_date is None
        assert r.shares_owned_after == Decimal("3215")
        assert r.direct_indirect == "I"
        assert r.nature_of_ownership == "Held through Silver Lake Group, L.L.C."
        assert r.footnote_ids == ("F4", "F5")
        assert row(doc, "nonderivative", "holding", 5).shares_owned_after == Decimal("174045")

    def test_derivative_transaction(self, doc: OwnershipDocument) -> None:
        r = row(doc, "derivative", "transaction", 1)
        assert r.security_title == "Class B Common Stock"
        assert r.conversion_or_exercise_price is None  # footnote F2 only
        assert r.transaction_date == date(2026, 9, 17)
        assert r.code == "M"
        assert r.shares == Decimal("70460")
        assert r.price == Decimal("0")
        assert r.acquired_disposed == "D"
        assert r.exercise_date is None  # footnote F2 only
        assert r.expiration_date is None  # footnote F2 only
        assert r.underlying_title == "Class C Common Stock"
        assert r.underlying_shares == Decimal("70460")
        assert r.shares_owned_after == Decimal("16383751")
        # F2 first (conversion price), then coding F1 (F2 again is deduplicated), then nature.
        assert r.footnote_ids == ("F2", "F1", "F3", "F4")

    def test_derivative_holding(self, doc: OwnershipDocument) -> None:
        r = row(doc, "derivative", "holding", 1)
        assert r.underlying_shares == Decimal("25178277")
        assert r.shares_owned_after == Decimal("25178277")
        assert r.nature_of_ownership == "See footnote"
        assert r.footnote_ids == ("F2", "F27")

    def test_footnotes(self, doc: OwnershipDocument) -> None:
        assert len(doc.footnotes) == 27
        assert doc.footnotes["F5"] == "Reflects shares of Class C Common Stock held by SLG."
        # &quot; entities decoded.
        assert '("Class C Common Stock")' in doc.footnotes["F1"]
        # F27 has a double space before "8,868,504" in the XML; it is normalized.
        assert "directly holds 8,868,504 shares" in doc.footnotes["F27"]
        assert doc.remarks is not None
        assert doc.remarks.startswith("The Reporting Persons are jointly filing this Form 4")

    def test_signatures(self, doc: OwnershipDocument) -> None:
        assert len(doc.signatures) == 5
        assert doc.signatures[4] == (
            "By: /s/ Justin G. Hamill, Attorney-in-fact for Egon Durban",
            date(2026, 9, 21),
        )


# ---------------------------------------------------------------- SEPN / Third Rock Form 4


class TestSepnThirdRockForm4:
    """0001193125-26-352692: fund 10% owners, 6 joint filers, 10b5-1 box, filer error."""

    @pytest.fixture
    def doc(self) -> OwnershipDocument:
        return load("0001193125-26-352692")

    def test_header(self, doc: OwnershipDocument) -> None:
        assert doc.document_type == "4"
        assert doc.schema_version == "X0609"
        assert doc.period_of_report == date(2026, 8, 12)
        assert doc.issuer_cik == "0001984086"
        assert doc.issuer_name == "Septerna, Inc."
        assert doc.issuer_ticker == "SEPN"
        assert doc.aff10b5one is True
        assert doc.remarks is None

    def test_owners(self, doc: OwnershipDocument) -> None:
        ciks = ["0001778071", "0001977498", "0001990717", "0001931056", "0001977499", "0001990716"]
        assert owner_flags(doc) == [(cik, False, False, True, False) for cik in ciks]
        assert doc.owners[0].name == "Third Rock Ventures V, L.P."

    def test_row_counts(self, doc: OwnershipDocument) -> None:
        assert row_counts(doc) == {
            ("nonderivative", "transaction"): 2,
            ("nonderivative", "holding"): 1,
        }

    def test_sales_reported_as_filed(self, doc: OwnershipDocument) -> None:
        # Filer error: these are sales (code S, holdings go DOWN) but the
        # acquired/disposed box says "A". The parser reports it as filed.
        first = row(doc, "nonderivative", "transaction", 1)
        assert first.transaction_date == date(2026, 8, 12)
        assert first.code == "S"
        assert first.shares == Decimal("147059")
        assert first.price == Decimal("50.27")
        assert first.price_is_weighted_avg is True
        assert first.acquired_disposed == "A"
        assert first.shares_owned_after == Decimal("6068532")
        assert first.direct_indirect == "D"
        assert first.nature_of_ownership is None
        assert first.footnote_ids == ("F1", "F2", "F3")

        second = row(doc, "nonderivative", "transaction", 2)
        assert second.shares == Decimal("141")
        assert second.price == Decimal("51.08")
        assert second.acquired_disposed == "A"
        assert second.shares_owned_after == Decimal("6068391")
        assert second.footnote_ids == ("F1", "F4", "F3")

    def test_holding(self, doc: OwnershipDocument) -> None:
        r = row(doc, "nonderivative", "holding", 1)
        assert r.shares_owned_after == Decimal("4211659")
        assert r.direct_indirect == "I"
        assert r.nature_of_ownership == "By Third Rock Ventures VI, L.P."
        assert r.footnote_ids == ("F5",)

    def test_footnotes_and_signatures(self, doc: OwnershipDocument) -> None:
        assert doc.footnotes["F1"] == (
            "The reported transactions were effected pursuant to a Rule 10b5-1 trading plan "
            "dated March 26, 2026, previously adopted by the Reporting Person."
        )
        # F5 contains "such shares.  Each" (two spaces) in the XML.
        assert "such shares. Each of Reporting Persons" in doc.footnotes["F5"]
        assert len(doc.signatures) == 6
        assert all(signed == date(2026, 8, 14) for _, signed in doc.signatures)


# ---------------------------------------------------------------- LUNR Form 4


class TestLunrForm4:
    """0001213900-26-098884: 1/0 booleans, notSubjectToSection16, derivative rows."""

    @pytest.fixture
    def doc(self) -> OwnershipDocument:
        return load("0001213900-26-098884")

    def test_header(self, doc: OwnershipDocument) -> None:
        assert doc.document_type == "4"
        assert doc.schema_version == "X0609"
        assert doc.period_of_report == date(2026, 9, 8)
        assert doc.not_subject_to_section16 is False  # "0"
        assert doc.issuer_cik == "0001844452"
        assert doc.issuer_name == "Intuitive Machines, Inc."
        assert doc.issuer_ticker == "LUNR"
        assert doc.aff10b5one is True  # "1"

    def test_owners(self, doc: OwnershipDocument) -> None:
        assert owner_flags(doc) == [
            ("0001963529", True, False, True, False),
            ("0001965259", False, False, True, False),
            ("0001965320", False, False, True, False),
        ]
        # <officerTitle></officerTitle> and <otherText></otherText> are empty.
        assert doc.owners[0].officer_title is None
        assert doc.owners[0].other_text is None

    def test_row_counts(self, doc: OwnershipDocument) -> None:
        assert row_counts(doc) == {
            ("nonderivative", "transaction"): 7,
            ("nonderivative", "holding"): 1,
            ("derivative", "transaction"): 2,
        }

    def test_redemption_row(self, doc: OwnershipDocument) -> None:
        r = row(doc, "nonderivative", "transaction", 1)
        assert r.security_title == "Class A Common Stock"
        assert r.transaction_date == date(2026, 9, 8)
        assert r.deemed_execution_date is None  # empty element
        assert r.code == "M"
        assert r.equity_swap is False  # "0"
        assert r.timeliness is None  # empty element
        assert r.shares == Decimal("94606")
        assert r.price is None  # footnote F1 only
        assert r.acquired_disposed == "A"
        assert r.shares_owned_after == Decimal("3589374")
        assert r.direct_indirect == "I"
        assert r.nature_of_ownership == "See footnotes"
        assert r.footnote_ids == ("F1", "F2", "F3")

    def test_class_c_cancellation(self, doc: OwnershipDocument) -> None:
        r = row(doc, "nonderivative", "transaction", 2)
        assert r.security_title == "Class C Common Stock"
        assert r.code == "D"
        assert r.acquired_disposed == "D"
        assert r.shares_owned_after == Decimal("34125934")

    def test_weighted_average_sale(self, doc: OwnershipDocument) -> None:
        r = row(doc, "nonderivative", "transaction", 3)
        assert r.code == "S"
        assert r.shares == Decimal("93105")
        assert r.price == Decimal("15.3913")
        assert r.price_is_weighted_avg is True
        assert r.acquired_disposed == "D"
        assert r.shares_owned_after == Decimal("3496270")
        assert r.footnote_ids == ("F4", "F5", "F2", "F3")
        assert row(doc, "nonderivative", "transaction", 5).transaction_date == date(2026, 9, 9)

    def test_holding(self, doc: OwnershipDocument) -> None:
        r = row(doc, "nonderivative", "holding", 1)
        assert r.shares_owned_after == Decimal("153526")
        assert r.direct_indirect == "D"
        assert r.footnote_ids == ()

    def test_derivative_rows(self, doc: OwnershipDocument) -> None:
        r = row(doc, "derivative", "transaction", 1)
        assert r.security_title == "Common Units"
        assert r.conversion_or_exercise_price is None
        assert r.transaction_date == date(2026, 9, 8)
        assert r.code == "M"
        assert r.shares == Decimal("94606")
        assert r.price is None
        assert r.acquired_disposed == "D"
        assert r.exercise_date is None
        assert r.expiration_date is None
        assert r.underlying_title == "Class A Common Stock"
        assert r.underlying_shares == Decimal("94606")
        assert r.shares_owned_after == Decimal("34125934")
        assert r.footnote_ids == ("F1", "F2", "F3")

        r2 = row(doc, "derivative", "transaction", 2)
        assert r2.transaction_date == date(2026, 9, 9)
        assert r2.shares == Decimal("1698")
        assert r2.shares_owned_after == Decimal("34124236")

    def test_signatures(self, doc: OwnershipDocument) -> None:
        assert len(doc.signatures) == 3
        assert doc.signatures[0][1] == date(2026, 9, 10)


# ---------------------------------------------------------------- GHM Form 4/A and its original


class TestGhmForm4Amendment:
    """0000891839-26-000409: corrects direct -> indirect ownership a year later."""

    @pytest.fixture
    def doc(self) -> OwnershipDocument:
        return load("0000891839-26-000409")

    def test_header(self, doc: OwnershipDocument) -> None:
        assert doc.document_type == "4/A"
        assert doc.is_amendment is True
        assert doc.schema_version == "X0609"
        assert doc.period_of_report == date(2025, 9, 12)
        assert doc.date_of_original_submission == date(2025, 9, 15)
        assert doc.issuer_cik == "0000716314"
        assert doc.issuer_name == "GRAHAM CORP"
        assert doc.issuer_ticker == "GHM"
        assert doc.aff10b5one is False
        assert owner_flags(doc) == [("0001809712", True, False, False, False)]

    def test_rows(self, doc: OwnershipDocument) -> None:
        # The amendment restates only the corrected row; the original's RSU
        # holding is not repeated.
        assert row_counts(doc) == {("nonderivative", "transaction"): 1}
        r = row(doc, "nonderivative", "transaction", 1)
        assert r.transaction_date == date(2025, 9, 12)
        assert r.code == "P"
        assert r.shares == Decimal("1200")
        assert r.price == Decimal("49.01")
        assert r.price_is_weighted_avg is False
        assert r.acquired_disposed == "A"
        assert r.shares_owned_after == Decimal("1200")
        assert r.direct_indirect == "I"
        assert r.nature_of_ownership == "By Family Trust"
        assert r.footnote_ids == ("F1",)

    def test_signature(self, doc: OwnershipDocument) -> None:
        assert doc.signatures == (
            ("/s/ Christina McLeod, Attorney-in-Fact for Mauro Gregorio", date(2026, 9, 21)),
        )


class TestGhmForm4Original:
    """0000891839-25-000050: older X0508 schema."""

    @pytest.fixture
    def doc(self) -> OwnershipDocument:
        return load("0000891839-25-000050")

    def test_header(self, doc: OwnershipDocument) -> None:
        assert doc.document_type == "4"
        assert doc.schema_version == "X0508"
        assert doc.period_of_report == date(2025, 9, 12)
        assert doc.date_of_original_submission is None
        assert doc.issuer_ticker == "GHM"
        assert doc.aff10b5one is False
        assert owner_flags(doc) == [("0001809712", True, False, False, False)]

    def test_rows(self, doc: OwnershipDocument) -> None:
        assert row_counts(doc) == {
            ("nonderivative", "transaction"): 1,
            ("derivative", "holding"): 1,
        }
        purchase = row(doc, "nonderivative", "transaction", 1)
        assert purchase.code == "P"
        assert purchase.direct_indirect == "D"  # what the 4/A later corrected
        assert purchase.nature_of_ownership is None
        assert purchase.footnote_ids == ()

        rsu = row(doc, "derivative", "holding", 1)
        assert rsu.security_title == "Restricted Stock Units"
        assert rsu.conversion_or_exercise_price == Decimal("0")  # a real 0, plus F1
        assert rsu.exercise_date is None
        assert rsu.expiration_date is None
        assert rsu.underlying_title == "Common Stock"
        assert rsu.underlying_shares == Decimal("936")
        assert rsu.shares_owned_after == Decimal("936")
        assert rsu.direct_indirect == "D"
        assert rsu.footnote_ids == ("F1",)
        assert doc.signatures[0][1] == date(2025, 9, 15)


# ---------------------------------------------------------------- NKE Form 4/A


class TestNkeForm4Amendment:
    """0000320187-26-000129: POA-only amendment of a gift (code G)."""

    @pytest.fixture
    def doc(self) -> OwnershipDocument:
        return load("0000320187-26-000129")

    def test_header(self, doc: OwnershipDocument) -> None:
        assert doc.document_type == "4/A"
        assert doc.schema_version == "X0609"
        assert doc.period_of_report == date(2026, 5, 14)
        assert doc.date_of_original_submission == date(2026, 5, 15)
        assert doc.issuer_cik == "0000320187"
        assert doc.issuer_name == "NIKE, Inc."
        assert doc.issuer_ticker == "NKE"
        assert doc.aff10b5one is False
        assert doc.remarks == "Exhibit 24 - Power of Attorney"
        assert len(doc.signatures) == 1

    def test_owner(self, doc: OwnershipDocument) -> None:
        # isTenPercentOwner and isOther are absent from the XML -> unchecked.
        assert owner_flags(doc) == [("0001236641", True, True, False, False)]
        assert doc.owners[0].name == "PARKER MARK G"
        assert doc.owners[0].officer_title == "EXECUTIVE CHAIRMAN"

    def test_rows(self, doc: OwnershipDocument) -> None:
        assert row_counts(doc) == {
            ("nonderivative", "transaction"): 1,
            ("nonderivative", "holding"): 1,
        }
        gift = row(doc, "nonderivative", "transaction", 1)
        assert gift.security_title == "Class B Common Stock"
        assert gift.transaction_date == date(2026, 5, 14)
        assert gift.code == "G"
        assert gift.timeliness is None
        assert gift.shares == Decimal("22230")
        assert gift.price == Decimal("0")
        assert gift.acquired_disposed == "D"
        assert gift.shares_owned_after == Decimal("625385")
        assert gift.direct_indirect == "D"
        assert gift.footnote_ids == ("F1",)  # on the transaction date

        plan = row(doc, "nonderivative", "holding", 1)
        assert plan.shares_owned_after == Decimal("39823")
        assert plan.direct_indirect == "I"
        assert plan.nature_of_ownership == "by Retirement Plan"
        assert plan.footnote_ids == ("F2",)
        assert doc.footnotes["F2"] == "Shares held in account under The NIKE, Inc. 401(k) Plan."


# ---------------------------------------------------------------- FULT Form 3


class TestFultForm3:
    """0001592539-26-000001: noSecuritiesOwned=0 but remarks say none, one 0.00 holding."""

    @pytest.fixture
    def doc(self) -> OwnershipDocument:
        return load("0001592539-26-000001")

    def test_header(self, doc: OwnershipDocument) -> None:
        assert doc.document_type == "3"
        assert doc.schema_version == "X0607"
        assert doc.period_of_report == date(2026, 9, 14)
        assert doc.no_securities_owned is False  # the box was NOT checked...
        assert doc.remarks == "No securities beneficially owned."  # ...yet remarks say none
        assert doc.issuer_cik == "0000700564"
        assert doc.issuer_name == "FULTON FINANCIAL CORP"
        assert doc.issuer_ticker == "FULT"
        assert doc.aff10b5one is None  # Form 3 has no 10b5-1 box
        assert doc.footnotes == {}  # <footnotes></footnotes>

    def test_owner(self, doc: OwnershipDocument) -> None:
        assert owner_flags(doc) == [("0001592539", True, False, False, False)]
        assert doc.owners[0].name == "Schulz David S."

    def test_zero_holding(self, doc: OwnershipDocument) -> None:
        # Empty <derivativeTable></derivativeTable> produces no rows.
        assert row_counts(doc) == {("nonderivative", "holding"): 1}
        r = row(doc, "nonderivative", "holding", 1)
        assert r.security_title == "$2.50 par value Common Stock"
        # A real, filed zero (not a missing value).
        assert r.shares_owned_after is not None
        assert r.shares_owned_after == Decimal("0.00")
        assert r.direct_indirect == "D"
        assert r.footnote_ids == ()
        assert doc.signatures == (
            ("Steven R. Horst, as attorney in fact for Schulz, David S.", date(2026, 9, 16)),
        )


# ---------------------------------------------------------------- THO Form 5


class TestThoForm5:
    """0001488301-26-000002: Form 5 flags; a Form 4 transaction reported late on Form 5."""

    @pytest.fixture
    def doc(self) -> OwnershipDocument:
        return load("0001488301-26-000002")

    def test_header(self, doc: OwnershipDocument) -> None:
        assert doc.document_type == "5"
        assert doc.schema_version == "X0609"
        assert doc.period_of_report == date(2026, 7, 31)
        assert doc.form3_holdings_reported is False
        assert doc.form4_transactions_reported is True
        assert doc.issuer_cik == "0000730263"
        assert doc.issuer_name == "THOR INDUSTRIES INC"
        assert doc.issuer_ticker == "THO"
        assert doc.aff10b5one is False
        assert owner_flags(doc) == [("0001488301", True, False, False, False)]

    def test_late_form4_row(self, doc: OwnershipDocument) -> None:
        assert row_counts(doc) == {("nonderivative", "transaction"): 1}
        r = row(doc, "nonderivative", "transaction", 1)
        assert r.form_type == "4"  # belongs on a Form 4, reported on this Form 5
        # The filing has no <transactionTimeliness> element at all, so it is None.
        assert r.timeliness is None
        assert r.transaction_date == date(2025, 10, 8)
        assert r.code == "F"
        assert r.shares == Decimal("327")
        assert r.price == Decimal("104.83")
        assert r.price_is_weighted_avg is False
        assert r.acquired_disposed == "D"
        assert r.shares_owned_after == Decimal("7047")
        assert r.direct_indirect == "D"
        assert r.nature_of_ownership is None  # <value></value>
        assert r.footnote_ids == ("F1",)  # on transactionShares
        assert doc.footnotes["F1"] == (
            "Represents shares withheld for taxes with respect to the vesting of a "
            "previously granted restricted stock unit award."
        )
        assert doc.signatures == (
            ("/s/ Barb Montague, attorney-in-fact for William J. Kelley Jr", date(2026, 9, 2)),
        )
