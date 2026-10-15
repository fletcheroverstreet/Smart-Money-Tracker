"""Cross-check: the SEC bulk data set reader against the XML parser, filing by filing.

Fixtures in tests/fixtures/edgar/insider_dataset/:

* ``<accession>.xml``: the primary XML of four filings, saved byte-for-byte
  from EDGAR (found through each filing's index.json).
* ``2026q2_form345_sample.zip``: the eight .tsv tables of the SEC's
  ``2026q2_form345.zip`` (Insider Transactions Data Sets, downloaded
  2026-09-24), keeping the header line and every line of those four
  accessions. Kept lines are byte-for-byte copies, in their original file
  order (which is NOT document order; that is part of what is tested).

The four filings, chosen for their different shapes:

* 0001012975-26-000503  Infleqtion (INFQ) Form 4, joint filing by 3 owners,
  12 transactions, weighted-average prices with 4 decimals.
* 0001482981-26-000122  Vita Coco (COCO) Form 4, all four row kinds, option
  exercise, 10b5-1 box checked.
* 0000719955-26-000140  Williams-Sonoma (WSM) Form 3, holdings only; shows
  the SEC's nature-of-ownership defect (see below).
* 0001750219-26-000005  Voip-pal (VPLM) Form 4/A, penny-stock prices, a
  holding listed BEFORE the transactions in the XML.

Fields the data set does not carry, or cannot carry faithfully, and how the
comparison treats them (each is also pinned by a hand-written test below):

* schema_version: not in the data set (always None). Excluded.
* Numbers: the SEC rounds to 2 decimals, half-up. XML values are rounded the
  same way before comparing.
* Owner and signature order: the data set's order differs from the XML, so
  they are compared as sets.
* remarks: newlines become spaces, so whitespace is normalized before comparing.
* Row tuple order across kinds (VPLM lists a holding first): rows are
  compared by their key (table, kind, line_no), never by tuple position.
* SEC defect: nature_of_ownership (and its footnote) is shifted onto the
  wrong row when some rows of a (table, kind) lack <natureOfOwnership>. The
  affected fields of WSM's Table I holdings are excluded from the generic
  comparison and pinned exactly in TestWsmForm3.

Every expected value in the hand-written tests was typed from reading the
fixture XML and TSV lines, not by running the code.
"""

from __future__ import annotations

import dataclasses
import zipfile
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path

import pytest

from smt.sources.edgar.insider_dataset import iter_dataset_documents
from smt.sources.forms.errors import ParseError
from smt.sources.forms.ownership import parse_ownership
from smt.sources.forms.types import OwnershipDocument, OwnershipRow, RowKind, TableName

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "insider_dataset"
SAMPLE_ZIP = FIXTURES / "2026q2_form345_sample.zip"

INFQ = "0001012975-26-000503"
COCO = "0001482981-26-000122"
WSM = "0000719955-26-000140"
VPLM = "0001750219-26-000005"
ACCESSIONS = [INFQ, COCO, WSM, VPLM]

# (accession, table, kind) groups hit by the SEC's nature-of-ownership shift.
NATURE_SHIFTED: set[tuple[str, TableName, RowKind]] = {(WSM, "nonderivative", "holding")}

# OwnershipRow fields holding numbers the SEC rounds to 2 decimals.
NUMBER_FIELDS = {
    "shares",
    "price",
    "shares_owned_after",
    "value_owned_after",
    "conversion_or_exercise_price",
    "underlying_shares",
}


# ---------------------------------------------------------------- helpers


@cache
def dataset() -> dict[str, tuple[date | None, OwnershipDocument]]:
    """Read the sample zip once: accession -> (filing date, document)."""
    return {acc: (filed, doc) for acc, filed, doc in iter_dataset_documents(SAMPLE_ZIP)}


def from_dataset(accession: str) -> OwnershipDocument:
    """The data set's version of one filing."""
    return dataset()[accession][1]


@cache
def from_xml(accession: str) -> OwnershipDocument:
    """The XML parser's version of one filing."""
    return parse_ownership((FIXTURES / f"{accession}.xml").read_bytes())


def row(doc: OwnershipDocument, table: TableName, kind: RowKind, line_no: int) -> OwnershipRow:
    """Find the row with this (table, kind, line_no); fail loudly if absent."""
    matches = [r for r in doc.rows if (r.table, r.kind, r.line_no) == (table, kind, line_no)]
    assert len(matches) == 1, f"expected exactly one {table}/{kind}/{line_no}"
    return matches[0]


def sec_rounded(value: Decimal | None) -> Decimal | None:
    """Round the way the SEC data set does: 2 decimals, halves up."""
    if value is None:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def squash(text: str | None) -> str | None:
    """Collapse all whitespace runs (the data set turns newlines into spaces)."""
    return None if text is None else " ".join(text.split())


def rows_by_key(doc: OwnershipDocument) -> dict[tuple[str, str, int], OwnershipRow]:
    """Index rows by the loader's key (table, kind, line_no)."""
    return {(r.table, r.kind, r.line_no): r for r in doc.rows}


# ---------------------------------------------------------------- the whole zip


def test_zip_yields_the_four_filings_in_accession_order() -> None:
    accessions = [acc for acc, _, _ in iter_dataset_documents(SAMPLE_ZIP)]
    assert accessions == [WSM, INFQ, COCO, VPLM]


def test_filing_dates() -> None:
    # FILING_DATE column of SUBMISSION.tsv (the XML itself has no filing date).
    assert dataset()[INFQ][0] == date(2026, 5, 29)
    assert dataset()[COCO][0] == date(2026, 4, 30)
    assert dataset()[WSM][0] == date(2026, 5, 29)
    assert dataset()[VPLM][0] == date(2026, 6, 29)


# ---------------------------------------------------------------- generic cross-check


@pytest.mark.parametrize("accession", ACCESSIONS)
def test_header_fields_agree(accession: str) -> None:
    xml, ds = from_xml(accession), from_dataset(accession)
    assert ds.document_type == xml.document_type
    assert ds.period_of_report == xml.period_of_report
    assert ds.issuer_cik == xml.issuer_cik
    assert ds.issuer_name == xml.issuer_name
    assert ds.issuer_ticker == xml.issuer_ticker
    assert squash(ds.remarks) == squash(xml.remarks)
    assert ds.date_of_original_submission == xml.date_of_original_submission
    assert ds.aff10b5one == xml.aff10b5one
    assert ds.not_subject_to_section16 == xml.not_subject_to_section16
    assert ds.no_securities_owned == xml.no_securities_owned
    assert ds.form3_holdings_reported == xml.form3_holdings_reported
    assert ds.form4_transactions_reported == xml.form4_transactions_reported
    assert ds.footnotes == xml.footnotes
    assert ds.is_amendment == xml.is_amendment
    # Not in the data set at all.
    assert ds.schema_version is None


@pytest.mark.parametrize("accession", ACCESSIONS)
def test_owners_and_relationships_agree(accession: str) -> None:
    xml, ds = from_xml(accession), from_dataset(accession)
    # Same owners with the same checkboxes and titles; order is not comparable.
    assert sorted(ds.owners, key=lambda o: o.cik) == sorted(xml.owners, key=lambda o: o.cik)


@pytest.mark.parametrize("accession", ACCESSIONS)
def test_signatures_agree(accession: str) -> None:
    assert sorted(from_dataset(accession).signatures) == sorted(from_xml(accession).signatures)


@pytest.mark.parametrize("accession", ACCESSIONS)
def test_rows_agree_field_by_field(accession: str) -> None:
    xml_rows = rows_by_key(from_xml(accession))
    ds_rows = rows_by_key(from_dataset(accession))
    assert ds_rows.keys() == xml_rows.keys()

    for key, xml_row in xml_rows.items():
        ds_row = ds_rows[key]
        shifted = (accession, xml_row.table, xml_row.kind) in NATURE_SHIFTED
        for field in dataclasses.fields(OwnershipRow):
            name = field.name
            if shifted and name in ("nature_of_ownership", "footnote_ids"):
                continue  # SEC defect, pinned in TestWsmForm3
            xml_value = getattr(xml_row, name)
            ds_value = getattr(ds_row, name)
            if name in NUMBER_FIELDS:
                xml_value = sec_rounded(xml_value)
            assert ds_value == xml_value, f"{accession} {key} {name}"


# ---------------------------------------------------------------- INFQ: joint Form 4


class TestInfqJointForm4:
    """Three Maverick entities; 12 sales/distributions; many footnotes per row."""

    def test_owners(self) -> None:
        doc = from_dataset(INFQ)
        assert {o.cik for o in doc.owners} == {"0000934639", "0001286655", "0001286654"}
        # All three tick only "Director" (RPTOWNER_RELATIONSHIP = "Director").
        assert all(o.is_director for o in doc.owners)
        assert not any(o.is_officer or o.is_ten_percent_owner or o.is_other for o in doc.owners)
        assert doc.aff10b5one is False  # AFF10B5ONE "0"

    def test_line_1_is_lowest_surrogate_key_not_first_line_in_file(self) -> None:
        # SK 8988756 is the 6th line of this accession in NONDERIV_TRANS.tsv,
        # but the first <nonDerivativeTransaction> in the XML.
        r = row(from_dataset(INFQ), "nonderivative", "transaction", 1)
        assert r.code == "J"
        assert r.shares == Decimal("1386029")
        assert r.price == Decimal("0")
        assert r.acquired_disposed == "D"
        assert r.shares_owned_after == Decimal("178041")
        # F1 on transactionCoding; NATURE_OF_OWNERSHIP_FN cell is "F8, F7, F9"
        # while the XML lists F7, F8, F9.
        assert r.footnote_ids == ("F1", "F7", "F8", "F9")

    def test_weighted_average_price_rounded(self) -> None:
        r = row(from_dataset(INFQ), "nonderivative", "transaction", 2)
        assert r.code == "S"
        assert r.shares == Decimal("169411")
        assert r.price == Decimal("16.05")  # XML: 16.0520
        # F6: "The reported price is a weighted average price. ..."
        assert r.price_is_weighted_avg is True
        assert r.footnote_ids == ("F2", "F6", "F7", "F8", "F9")

    def test_footnote_order_across_fields_is_document_order(self) -> None:
        # F16 (price) comes before F7/F8/F9 (nature) because price precedes
        # ownershipNature in the XML, even though 16 > 9.
        r = row(from_dataset(INFQ), "nonderivative", "transaction", 3)
        assert r.price == Decimal("16.85")  # XML: 16.8503
        assert r.footnote_ids == ("F2", "F16", "F7", "F8", "F9")

    def test_half_up_rounding(self) -> None:
        r = row(from_dataset(INFQ), "nonderivative", "transaction", 4)
        assert r.price == Decimal("17.91")  # XML: 17.9054
        assert r.shares_owned_after == Decimal("0")

    def test_last_row(self) -> None:
        r = row(from_dataset(INFQ), "nonderivative", "transaction", 12)
        assert r.code == "J"
        assert r.shares == Decimal("350115")
        assert r.acquired_disposed == "A"
        assert r.nature_of_ownership == "See Footnotes"
        assert r.footnote_ids == ("F14", "F7", "F8", "F15")
        assert len(from_dataset(INFQ).rows) == 12


# ---------------------------------------------------------------- COCO: all four row kinds


class TestCocoForm4:
    """CEO/director option exercise and sale under a 10b5-1 plan."""

    def test_header_and_owner(self) -> None:
        doc = from_dataset(COCO)
        assert doc.aff10b5one is True  # AFF10B5ONE "1"
        (owner,) = doc.owners
        assert owner.cik == "0001206513"
        assert owner.name == "Roper Martin"
        # "Director,Officer" -> two boxes; the XML omits the other two elements.
        assert (owner.is_director, owner.is_officer) == (True, True)
        assert (owner.is_ten_percent_owner, owner.is_other) == (False, False)
        assert owner.officer_title == "Chief Executive Officer"

    def test_row_counts(self) -> None:
        doc = from_dataset(COCO)
        counts = {(t, k): sum(1 for r in doc.rows if (r.table, r.kind) == (t, k)) for t, k in [
            ("nonderivative", "transaction"),
            ("nonderivative", "holding"),
            ("derivative", "transaction"),
            ("derivative", "holding"),
        ]}  # fmt: skip
        assert counts == {
            ("nonderivative", "transaction"): 4,
            ("nonderivative", "holding"): 4,
            ("derivative", "transaction"): 2,
            ("derivative", "holding"): 6,
        }

    def test_exercise_row(self) -> None:
        r = row(from_dataset(COCO), "nonderivative", "transaction", 1)
        assert r.code == "M"
        assert r.transaction_date == date(2026, 4, 28)
        assert r.price == Decimal("10.18")  # XML: 10.178
        assert r.timeliness is None  # <transactionTimeliness></transactionTimeliness>
        assert r.equity_swap is False
        assert r.direct_indirect == "D"
        assert r.footnote_ids == ()

    def test_weighted_average_sale(self) -> None:
        r = row(from_dataset(COCO), "nonderivative", "transaction", 4)
        assert r.code == "S"
        assert r.price == Decimal("59.35")  # XML: 59.348
        assert r.price_is_weighted_avg is True  # F2
        assert r.footnote_ids == ("F1", "F2")

    def test_holding_nature(self) -> None:
        # Every holding here has <natureOfOwnership>, so nothing is shifted.
        doc = from_dataset(COCO)
        assert row(doc, "nonderivative", "holding", 1).nature_of_ownership == "by Chris Roper FT"
        assert row(doc, "nonderivative", "holding", 1).footnote_ids == ("F3",)
        assert row(doc, "nonderivative", "holding", 4).nature_of_ownership == "by Spouse"
        assert row(doc, "nonderivative", "holding", 4).footnote_ids == ()

    def test_derivative_transaction(self) -> None:
        r = row(from_dataset(COCO), "derivative", "transaction", 1)
        assert r.security_title == "Non-Qualified Stock Option (Right to Buy)"
        assert r.conversion_or_exercise_price == Decimal("10.18")  # XML: 10.178
        assert r.code == "M"
        assert r.shares == Decimal("25000")
        assert r.price == Decimal("0")
        assert r.acquired_disposed == "D"
        assert r.exercise_date is None  # footnote F6 only
        assert r.expiration_date == date(2029, 9, 19)
        assert r.underlying_title == "Common Stock"
        assert r.underlying_shares == Decimal("25000")
        assert r.shares_owned_after == Decimal("350214")
        assert r.footnote_ids == ("F6",)

    def test_derivative_holding(self) -> None:
        r = row(from_dataset(COCO), "derivative", "holding", 2)
        assert r.conversion_or_exercise_price == Decimal("15.00")
        assert r.exercise_date is None  # footnote F7 only
        assert r.expiration_date == date(2031, 10, 21)
        assert r.underlying_shares == Decimal("298507")
        assert r.transaction_date is None
        assert r.code is None
        assert r.footnote_ids == ("F7",)


# ---------------------------------------------------------------- WSM: Form 3


class TestWsmForm3:
    """A new officer's initial statement: 2 stock holdings, 6 RSU grants."""

    def test_header(self) -> None:
        doc = from_dataset(WSM)
        assert doc.document_type == "3"
        assert doc.no_securities_owned is False  # NO_SECURITIES_OWNED "0"
        assert doc.aff10b5one is None  # Form 3 has no 10b5-1 box
        (owner,) = doc.owners
        assert owner.is_officer is True
        assert owner.is_director is False
        assert owner.officer_title == "PRESIDENT POTTERY BARN BRAND"

    def test_derivative_holdings_follow_surrogate_key(self) -> None:
        # The file lists SKs 2037519, 2037520, 2037522, 2037523, 2037518, 2037521.
        # The XML order (8300, 6298, 4790, 9452, 11044, 14824 units) is SK order.
        doc = from_dataset(WSM)
        units = [row(doc, "derivative", "holding", n).underlying_shares for n in range(1, 7)]
        assert units == [Decimal(v) for v in ("8300", "6298", "4790", "9452", "11044", "14824")]
        first = row(doc, "derivative", "holding", 1)
        assert first.security_title == "Restricted Stock Units"
        assert first.conversion_or_exercise_price is None  # footnote F4 only
        # conversion price F4, exercise date F2, expiration F3: document order.
        assert first.footnote_ids == ("F4", "F2", "F3")
        assert row(doc, "derivative", "holding", 6).footnote_ids == ("F4", "F9", "F3")

    def test_nature_of_ownership_is_shifted_by_the_sec(self) -> None:
        """SEC data defect: the one <natureOfOwnership> lands on the wrong row.

        XML: holding 1 = 36,965 shares Direct (no nature element);
             holding 2 = 8 shares Indirect, "By Managed Account" + F1.
        Data set: the nature text and F1 sit on holding 1 instead.
        """
        xml, ds = from_xml(WSM), from_dataset(WSM)

        xml_1 = row(xml, "nonderivative", "holding", 1)
        xml_2 = row(xml, "nonderivative", "holding", 2)
        assert (xml_1.shares_owned_after, xml_1.direct_indirect) == (Decimal("36965"), "D")
        assert xml_1.nature_of_ownership is None
        assert xml_1.footnote_ids == ()
        assert (xml_2.shares_owned_after, xml_2.direct_indirect) == (Decimal("8"), "I")
        assert xml_2.nature_of_ownership == "By Managed Account"
        assert xml_2.footnote_ids == ("F1",)

        ds_1 = row(ds, "nonderivative", "holding", 1)
        ds_2 = row(ds, "nonderivative", "holding", 2)
        # Shares and direct/indirect are right...
        assert (ds_1.shares_owned_after, ds_1.direct_indirect) == (Decimal("36965"), "D")
        assert (ds_2.shares_owned_after, ds_2.direct_indirect) == (Decimal("8"), "I")
        # ...but the nature moved up one row.
        assert ds_1.nature_of_ownership == "By Managed Account"
        assert ds_1.footnote_ids == ("F1",)
        assert ds_2.nature_of_ownership is None
        assert ds_2.footnote_ids == ()


# ---------------------------------------------------------------- VPLM: Form 4/A


class TestVplmForm4A:
    """Same-day amendment of a director's penny-stock sales."""

    def test_amendment_header(self) -> None:
        doc = from_dataset(VPLM)
        assert doc.document_type == "4/A"
        assert doc.is_amendment is True
        assert doc.period_of_report == date(2026, 6, 22)
        assert doc.date_of_original_submission == date(2026, 6, 29)
        assert doc.not_subject_to_section16 is False
        assert doc.remarks == "ISSUER INFORMATION IN BOX 2 WAS INCORRECT - REFILED"
        assert doc.signatures == (("DENNIS CHANG", date(2026, 6, 29)),)

    def test_penny_prices_lose_precision(self) -> None:
        doc = from_dataset(VPLM)
        # XML prices 0.0101, 0.0106, 0.0102, 0.0102 all become 0.01.
        prices = [row(doc, "nonderivative", "transaction", n).price for n in range(1, 5)]
        assert prices == [Decimal("0.01")] * 4
        # Exercise price 0.005 becomes 0.01: rounded half-up, doubling it.
        assert row(doc, "derivative", "holding", 1).conversion_or_exercise_price == Decimal("0.01")
        assert row(from_xml(VPLM), "derivative", "holding", 1).conversion_or_exercise_price == (
            Decimal("0.005")
        )

    def test_rows(self) -> None:
        doc = from_dataset(VPLM)
        first_sale = row(doc, "nonderivative", "transaction", 1)
        assert first_sale.transaction_date == date(2026, 6, 22)
        assert first_sale.shares == Decimal("189999")
        assert first_sale.shares_owned_after == Decimal("1397596")
        last_sale = row(doc, "nonderivative", "transaction", 4)
        assert last_sale.transaction_date == date(2026, 6, 24)
        assert last_sale.shares == Decimal("70001")
        holding = row(doc, "nonderivative", "holding", 1)
        assert holding.shares_owned_after == Decimal("1587595")
        options = row(doc, "derivative", "holding", 1)
        assert options.security_title == "OPTIONS"
        assert options.exercise_date == date(2024, 1, 12)
        assert options.expiration_date == date(2029, 1, 12)
        assert options.shares_owned_after == Decimal("10000000")
        assert options.footnote_ids == ("F1",)
        assert row(doc, "derivative", "holding", 5).footnote_ids == ("F2",)

    def test_tuple_order_differs_but_keys_match(self) -> None:
        # The XML lists the holding before the four sales; the data set cannot
        # know that, so only the (table, kind, line_no) keys are comparable.
        xml_first = from_xml(VPLM).rows[0]
        ds_first = from_dataset(VPLM).rows[0]
        assert (xml_first.kind, xml_first.line_no) == ("holding", 1)
        assert (ds_first.kind, ds_first.line_no) == ("transaction", 1)


# ---------------------------------------------------------------- malformed input


def _rewrite_zip(target: Path, change: dict[str, bytes | None]) -> Path:
    """Copy the sample zip to `target`, replacing (bytes) or dropping (None) members."""
    with zipfile.ZipFile(SAMPLE_ZIP) as source, zipfile.ZipFile(target, "w") as out:
        for info in source.infolist():
            if info.filename in change:
                replacement = change[info.filename]
                if replacement is not None:
                    out.writestr(info.filename, replacement)
            else:
                out.writestr(info.filename, source.read(info))
    return target


def _submission_bytes() -> bytes:
    with zipfile.ZipFile(SAMPLE_ZIP) as archive:
        return archive.read("SUBMISSION.tsv")


def test_missing_required_table_raises(tmp_path: Path) -> None:
    broken = _rewrite_zip(tmp_path / "x.zip", {"REPORTINGOWNER.tsv": None})
    with pytest.raises(ParseError, match="REPORTINGOWNER"):
        list(iter_dataset_documents(broken))


def test_bad_date_raises(tmp_path: Path) -> None:
    bad = _submission_bytes().replace(b"30-APR-2026", b"2026-04-30")
    broken = _rewrite_zip(tmp_path / "x.zip", {"SUBMISSION.tsv": bad})
    with pytest.raises(ParseError, match="DD-MON-YYYY"):
        list(iter_dataset_documents(broken))


def test_utf8_bom_is_ignored(tmp_path: Path) -> None:
    with_bom = _rewrite_zip(
        tmp_path / "x.zip", {"SUBMISSION.tsv": b"\xef\xbb\xbf" + _submission_bytes()}
    )
    assert [acc for acc, _, _ in iter_dataset_documents(with_bom)] == [WSM, INFQ, COCO, VPLM]
