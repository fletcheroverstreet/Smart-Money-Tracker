"""Small focused tests for the Forms 3/4/5 parser, on hand-written XML snippets."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from smt.sources.forms.errors import ParseError
from smt.sources.forms.ownership import PARSER_NAME, PARSER_VERSION, parse_ownership

OWNER = """
<reportingOwner>
  <reportingOwnerId><rptOwnerCik>1234</rptOwnerCik><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
  <reportingOwnerAddress><rptOwnerStreet1>1 MAIN ST</rptOwnerStreet1></reportingOwnerAddress>
  <reportingOwnerRelationship>
    <isDirector>1</isDirector><isOfficer>true</isOfficer><isTenPercentOwner></isTenPercentOwner>
    <officerTitle> CEO </officerTitle>
  </reportingOwnerRelationship>
</reportingOwner>
"""


def make_doc(
    body: str = "",
    *,
    doc_type: str = "4",
    ticker: str = "abc",
    header: str = "",
    owner: str = OWNER,
) -> bytes:
    """Build a minimal ownershipDocument around `body` (tables, footnotes, ...)."""
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0609</schemaVersion>
  <documentType>{doc_type}</documentType>
  <periodOfReport>2026-09-17</periodOfReport>
  {header}
  <issuer>
    <issuerCik>320187</issuerCik>
    <issuerName>Example Corp</issuerName>
    <issuerTradingSymbol>{ticker}</issuerTradingSymbol>
  </issuer>
  {owner}
  {body}
</ownershipDocument>""".encode()


def test_parser_identity() -> None:
    assert PARSER_NAME == "ownership"
    assert PARSER_VERSION == "1.0.0"


# ---------------------------------------------------------------- header and owners


def test_minimal_document() -> None:
    doc = parse_ownership(make_doc())
    assert doc.issuer_cik == "0000320187"  # zero-padded to 10 digits
    assert doc.issuer_ticker == "ABC"  # upper-cased
    assert doc.aff10b5one is None  # absent
    assert doc.rows == ()
    assert doc.footnotes == {}
    assert doc.signatures == ()

    (owner,) = doc.owners
    assert owner.cik == "0000001234"
    assert owner.is_director is True
    assert owner.is_officer is True
    assert owner.is_ten_percent_owner is False  # empty element
    assert owner.is_other is False  # absent element
    assert owner.officer_title == "CEO"
    assert owner.other_text is None


@pytest.mark.parametrize("raw", ["NONE", "none", " N/A ", ""])
def test_placeholder_ticker_is_none(raw: str) -> None:
    assert parse_ownership(make_doc(ticker=raw)).issuer_ticker is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("1", True), ("TRUE", True), ("false", False), ("0", False), ("", None)],
)
def test_boolean_coercion(raw: str, expected: bool | None) -> None:
    doc = parse_ownership(make_doc(header=f"<aff10b5One>{raw}</aff10b5One>"))
    assert doc.aff10b5one is expected


def test_form3_and_form5_flags() -> None:
    doc = parse_ownership(
        make_doc(
            doc_type="5/A",
            header=(
                "<dateOfOriginalSubmission>2026-01-02</dateOfOriginalSubmission>"
                "<notSubjectToSection16>1</notSubjectToSection16>"
                "<noSecuritiesOwned>true</noSecuritiesOwned>"
                "<form3HoldingsReported>0</form3HoldingsReported>"
                "<form4TransactionsReported>1</form4TransactionsReported>"
            ),
        )
    )
    assert doc.is_amendment is True
    assert doc.date_of_original_submission == date(2026, 1, 2)
    assert doc.not_subject_to_section16 is True
    assert doc.no_securities_owned is True
    assert doc.form3_holdings_reported is False
    assert doc.form4_transactions_reported is True


def test_date_with_timezone_suffix() -> None:
    doc = parse_ownership(
        make_doc(
            "<ownerSignature><signatureName>/s/ J</signatureName>"
            "<signatureDate>2026-09-17-05:00</signatureDate></ownerSignature>"
        )
    )
    assert doc.signatures == (("/s/ J", date(2026, 9, 17)),)


def test_namespaced_document_and_bom() -> None:
    # Prefixes and a default namespace must not matter; only local names do.
    xml = b"""\xef\xbb\xbf<?xml version="1.0"?>
<ns1:ownershipDocument xmlns:ns1="urn:a" xmlns:com="urn:b">
  <ns1:documentType>3</ns1:documentType>
  <ns1:issuer><com:issuerCik>1</com:issuerCik><com:issuerName>X</com:issuerName></ns1:issuer>
  <ns1:reportingOwner>
    <ns1:reportingOwnerId><com:rptOwnerCik>2</com:rptOwnerCik><com:rptOwnerName>Y</com:rptOwnerName></ns1:reportingOwnerId>
  </ns1:reportingOwner>
  <ns1:nonDerivativeTable>
    <ns1:nonDerivativeHolding>
      <ns1:securityTitle><com:value>Common</com:value></ns1:securityTitle>
      <ns1:postTransactionAmounts><ns1:sharesOwnedFollowingTransaction><com:value>5</com:value></ns1:sharesOwnedFollowingTransaction></ns1:postTransactionAmounts>
    </ns1:nonDerivativeHolding>
  </ns1:nonDerivativeTable>
</ns1:ownershipDocument>"""
    doc = parse_ownership(xml)
    assert doc.document_type == "3"
    assert doc.schema_version is None
    assert doc.issuer_cik == "0000000001"
    assert doc.issuer_ticker is None
    assert doc.owners[0].cik == "0000000002"
    assert doc.rows[0].security_title == "Common"
    assert doc.rows[0].shares_owned_after == Decimal("5")


# ---------------------------------------------------------------- rows


def test_line_numbers_count_per_table_and_kind() -> None:
    txn = "<nonDerivativeTransaction><securityTitle><value>T</value></securityTitle></nonDerivativeTransaction>"
    hold = "<nonDerivativeHolding><securityTitle><value>H</value></securityTitle></nonDerivativeHolding>"
    dtxn = "<derivativeTransaction><securityTitle><value>D</value></securityTitle></derivativeTransaction>"
    body = (
        f"<nonDerivativeTable>{txn}{hold}{txn}{hold}{txn}</nonDerivativeTable>"
        f"<derivativeTable>{dtxn}</derivativeTable>"
    )
    rows = parse_ownership(make_doc(body)).rows
    assert [(r.table, r.kind, r.line_no) for r in rows] == [
        ("nonderivative", "transaction", 1),
        ("nonderivative", "holding", 1),
        ("nonderivative", "transaction", 2),
        ("nonderivative", "holding", 2),
        ("nonderivative", "transaction", 3),
        ("derivative", "transaction", 1),
    ]


def test_footnote_only_fields_and_dedup() -> None:
    body = """
<derivativeTable><derivativeTransaction>
  <securityTitle><value>Option</value></securityTitle>
  <conversionOrExercisePrice><footnoteId id="F2"/></conversionOrExercisePrice>
  <transactionDate><value>2026-09-17</value><footnoteId id="F1"/></transactionDate>
  <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>M</transactionCode>
    <equitySwapInvolved>false</equitySwapInvolved><footnoteId id="F2"/></transactionCoding>
  <transactionTimeliness><value>L</value></transactionTimeliness>
  <transactionAmounts>
    <transactionShares><value>1,0</value></transactionShares>
  </transactionAmounts>
</derivativeTransaction></derivativeTable>"""
    # "1,0" is not a number: the parser refuses instead of guessing.
    with pytest.raises(ParseError, match="transactionShares"):
        parse_ownership(make_doc(body))

    row = parse_ownership(make_doc(body.replace("1,0", "10.5"))).rows[0]
    assert row.conversion_or_exercise_price is None
    assert row.transaction_date == date(2026, 9, 17)
    assert row.timeliness == "L"
    assert row.shares == Decimal("10.5")
    assert row.footnote_ids == ("F2", "F1")


@pytest.mark.parametrize(
    ("footnote", "expected"),
    [
        ("The price reported is a Weighted Average price.", True),
        ("Represents the weighted-average sale price.", True),
        ("Sold under a 10b5-1 plan.", False),
    ],
)
def test_weighted_average_detection(footnote: str, expected: bool) -> None:
    body = f"""
<nonDerivativeTable><nonDerivativeTransaction>
  <securityTitle><value>Common</value></securityTitle>
  <transactionAmounts>
    <transactionPricePerShare><value>10.00</value><footnoteId id="F9"/></transactionPricePerShare>
  </transactionAmounts>
  <ownershipNature><natureOfOwnership><footnoteId id="F1"/></natureOfOwnership></ownershipNature>
</nonDerivativeTransaction></nonDerivativeTable>
<footnotes><footnote id="F9">{footnote}</footnote><footnote id="F1">weighted average</footnote></footnotes>"""
    row = parse_ownership(make_doc(body)).rows[0]
    assert row.price == Decimal("10.00")
    # Only footnotes on the PRICE count; F1 on the nature field is ignored.
    assert row.price_is_weighted_avg is expected


def test_value_owned_following_transaction() -> None:
    body = """
<nonDerivativeTable><nonDerivativeHolding>
  <securityTitle><value>Notes</value></securityTitle>
  <postTransactionAmounts><valueOwnedFollowingTransaction><value>250000</value></valueOwnedFollowingTransaction></postTransactionAmounts>
</nonDerivativeHolding></nonDerivativeTable>"""
    row = parse_ownership(make_doc(body)).rows[0]
    assert row.shares_owned_after is None
    assert row.value_owned_after == Decimal("250000")


def test_footnote_whitespace_is_normalized() -> None:
    body = '<footnotes><footnote id="F1">  line one\n\n   line\ttwo  </footnote><footnote id="F2"> </footnote></footnotes>'
    assert parse_ownership(make_doc(body)).footnotes == {"F1": "line one line two"}


# ---------------------------------------------------------------- errors and safety


@pytest.mark.parametrize(
    "xml",
    [
        b"",
        b"<ownershipDocument><documentType>4</documentType>",  # truncated
        b"<edgarSubmission><documentType>4</documentType></edgarSubmission>",  # wrong root
    ],
)
def test_malformed_input_raises(xml: bytes) -> None:
    with pytest.raises(ParseError):
        parse_ownership(xml)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"doc_type": "13F-HR"}, "documentType"),
        ({"header": "<aff10b5One>maybe</aff10b5One>"}, "aff10b5One"),
        ({"owner": ""}, "no reporting owner"),
    ],
)
def test_invalid_fields_raise(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(ParseError, match=message):
        parse_ownership(make_doc(**kwargs))


def test_bad_date_raises() -> None:
    with pytest.raises(ParseError, match="periodOfReport"):
        parse_ownership(make_doc().replace(b"2026-09-17", b"09/17/2026"))


def test_external_entities_are_not_resolved() -> None:
    # An XXE payload must never pull a local file into the parsed output.
    xml = make_doc("<remarks>&xxe;</remarks>").replace(
        b'<?xml version="1.0"?>',
        b'<?xml version="1.0"?><!DOCTYPE ownershipDocument '
        b'[<!ENTITY xxe SYSTEM "file:///C:/Windows/win.ini">]>',
    )
    doc = parse_ownership(xml)
    assert doc.remarks is None
