"""Unit tests for the Form 144 parser: edge cases built from small hand-made XML."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from smt.sources.forms.errors import ParseError
from smt.sources.forms.form144 import PARSER_NAME, PARSER_VERSION, parse_form144


def _doc(
    form_data: str = "",
    *,
    submission_type: str = "144",
    cik: str = "<cik>1821737</cik>",
    extra_header: str = "",
) -> bytes:
    """Build a minimal Form 144 document around a formData body."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/ownership"
                 xmlns:c="http://www.sec.gov/edgar/common">
  <headerData>
    <submissionType>{submission_type}</submissionType>
    {extra_header}
    <filerInfo><filer><filerCredentials>{cik}<ccc>XXXXXXXX</ccc>
    </filerCredentials></filer></filerInfo>
  </headerData>
  <formData>{form_data}</formData>
</edgarSubmission>""".encode()


def test_parser_identity() -> None:
    assert PARSER_NAME == "form144"
    assert PARSER_VERSION == "1.0.0"


def test_minimal_document_has_none_and_empty_tuples() -> None:
    n = parse_form144(_doc())
    assert n.submission_type == "144"
    assert n.filer_cik == "0001821737"  # zero-padded to 10 digits
    assert n.previous_accession is None
    assert n.issuer_cik is None
    assert n.issuer_name is None
    assert n.person_name is None
    assert n.relationships == ()
    assert n.securities == ()
    assert n.acquisitions == ()
    assert n.past_sales == ()
    assert n.nothing_sold_past_3_months is None
    assert n.notice_date is None
    assert n.plan_adoption_dates == ()
    assert n.remarks is None


def test_bom_is_stripped() -> None:
    n = parse_form144(b"\xef\xbb\xbf" + _doc())
    assert n.filer_cik == "0001821737"


def test_nothing_to_report_yes() -> None:
    body = "<nothingToReportFlagOnSecuritiesSoldInPast3Months>Y" \
        "</nothingToReportFlagOnSecuritiesSoldInPast3Months>"
    assert parse_form144(_doc(body)).nothing_sold_past_3_months is True


def test_blank_values_are_none_not_zero() -> None:
    body = """
      <securitiesInformation>
        <securitiesClassTitle> </securitiesClassTitle>
        <noOfUnitsSold></noOfUnitsSold>
        <aggregateMarketValue/>
        <approxSaleDate/>
      </securitiesInformation>"""
    (sec,) = parse_form144(_doc(body)).securities
    assert sec.class_title is None
    assert sec.units_to_sell is None
    assert sec.aggregate_market_value is None
    assert sec.approx_sale_date is None
    assert sec.broker_name is None


def test_any_namespace_prefix_and_multiple_securities() -> None:
    # Elements in a prefixed namespace still match by local name.
    body = """
      <c:securitiesInformation>
        <c:securitiesClassTitle>Class A</c:securitiesClassTitle>
        <c:noOfUnitsSold>1,500</c:noOfUnitsSold>
      </c:securitiesInformation>
      <securitiesInformation>
        <securitiesClassTitle>Class B</securitiesClassTitle>
        <noOfUnitsSold>10.5</noOfUnitsSold>
      </securitiesInformation>"""
    secs = parse_form144(_doc(body)).securities
    assert [s.class_title for s in secs] == ["Class A", "Class B"]
    assert [s.units_to_sell for s in secs] == [Decimal("1500"), Decimal("10.5")]


def test_gift_and_plan_dates() -> None:
    body = """
      <securitiesToBeSold><isGiftTransaction>Y</isGiftTransaction></securitiesToBeSold>
      <noticeSignature>
        <noticeDate>2026-01-05</noticeDate>
        <planAdoptionDates>
          <planAdoptionDate>12/01/2025</planAdoptionDate>
          <planAdoptionDate>02/15/2026</planAdoptionDate>
        </planAdoptionDates>
      </noticeSignature>"""
    n = parse_form144(_doc(body))
    assert n.acquisitions[0].is_gift is True
    assert n.notice_date == date(2026, 1, 5)
    assert n.plan_adoption_dates == (date(2025, 12, 1), date(2026, 2, 15))


def test_amendment_previous_accession() -> None:
    header = "<previousAccessionNumber>0001950047-26-009654</previousAccessionNumber>"
    n = parse_form144(_doc(submission_type="144/A", extra_header=header))
    assert n.submission_type == "144/A"
    assert n.previous_accession == "0001950047-26-009654"


def test_addresses_are_ignored() -> None:
    body = """
      <issuerInfo>
        <issuerCik>1640147</issuerCik>
        <issuerAddress><c:street1>1 Main St</c:street1></issuerAddress>
        <relationshipsToIssuer>
          <relationshipToIssuer>Officer</relationshipToIssuer>
          <relationshipToIssuer> </relationshipToIssuer>
        </relationshipsToIssuer>
      </issuerInfo>"""
    n = parse_form144(_doc(body))
    assert n.issuer_cik == "0001640147"
    assert n.relationships == ("Officer",)


@pytest.mark.parametrize(
    "xml",
    [
        b"",
        b"   ",
        b"not xml at all",
        b"<edgarSubmission><headerData>",  # truncated
        b"<ownershipDocument/>",  # wrong root
        b"<edgarSubmission><headerData/></edgarSubmission>",  # no formData
    ],
)
def test_malformed_input_raises(xml: bytes) -> None:
    with pytest.raises(ParseError):
        parse_form144(xml)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"submission_type": "4"},
        {"submission_type": ""},
        {"cik": ""},
        {"cik": "<cik>ABC</cik>"},
        {"cik": "<cik>12345678901</cik>"},
    ],
)
def test_bad_header_raises(kwargs: dict[str, str]) -> None:
    with pytest.raises(ParseError):
        parse_form144(_doc(**kwargs))


@pytest.mark.parametrize(
    "body",
    [
        "<securitiesInformation><noOfUnitsSold>many</noOfUnitsSold></securitiesInformation>",
        "<securitiesInformation><noOfUnitsSold>NaN</noOfUnitsSold></securitiesInformation>",
        "<securitiesInformation><approxSaleDate>13/45/2026</approxSaleDate>"
        "</securitiesInformation>",
        "<securitiesToBeSold><isGiftTransaction>maybe</isGiftTransaction></securitiesToBeSold>",
        "<issuerInfo><issuerCik>12-34</issuerCik></issuerInfo>",
    ],
)
def test_bad_values_raise(body: str) -> None:
    with pytest.raises(ParseError):
        parse_form144(_doc(body))


def test_external_entities_are_not_resolved() -> None:
    xml = b"""<?xml version="1.0"?>
<!DOCTYPE edgarSubmission [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<edgarSubmission>
  <headerData><submissionType>144</submissionType>
    <filerInfo><filer><filerCredentials><cik>1</cik></filerCredentials></filer></filerInfo>
  </headerData>
  <formData><remarks>&xxe;</remarks></formData>
</edgarSubmission>"""
    n = parse_form144(xml)
    assert n.remarks is None
