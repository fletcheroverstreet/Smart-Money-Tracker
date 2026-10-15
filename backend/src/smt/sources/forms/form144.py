"""Parser for SEC Form 144 and 144/A (notice of proposed sale of securities).

What Form 144 is
----------------
Rule 144 lets an "affiliate" of a company (an officer, director or large
holder) sell restricted or control stock in the public market. If the sale is
bigger than 5,000 shares or $50,000 in any 3-month window, the seller must file
a Form 144 notice with the SEC *before or at the time* the sell order is
placed. So a Form 144 is a statement of intent: it says "I plan to sell about
this many shares, through this broker, around this date". It is NOT a record
of a completed trade. The completed trade shows up later on a Form 4.

A 144/A amends an earlier 144 and names it in `previousAccessionNumber`.

The XML
-------
The document root is `<edgarSubmission xmlns="http://www.sec.gov/edgar/ownership">`.
Addresses use a second "common" namespace whose prefix varies from filer agent
to filer agent (`com:`, `ns2:`, ...). To stay immune to that, every element is
matched by its LOCAL name (the part after the prefix) and the namespace is
ignored. Addresses are deliberately not parsed (public-role facts only).

This module is a pure function: bytes in, `Form144Notice` out, no I/O.
Malformed input raises `ParseError`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

from smt.sources.forms.errors import ParseError
from smt.sources.forms.types import (
    Form144Acquisition,
    Form144Notice,
    Form144PastSale,
    Form144Security,
)

PARSER_NAME = "form144"
PARSER_VERSION = "1.0.0"

_UTF8_BOM = b"\xef\xbb\xbf"

# The submission types this parser understands. "144" is the original notice,
# "144/A" an amendment of an earlier one.
_SUBMISSION_TYPES = frozenset({"144", "144/A"})


# ---------------------------------------------------------------- XML helpers


def _make_parser() -> etree.XMLParser:
    """Build a hardened XML parser.

    * resolve_entities=False: never expand entities (blocks XXE / "billion laughs").
    * no_network=True: never fetch anything referenced by the document.
    * huge_tree=False: keep libxml2's size limits on.
    """
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def _local_name(element: etree._Element) -> str:
    """Return the tag name without its namespace prefix ("ns2:street1" -> "street1")."""
    return etree.QName(element).localname


def _children(parent: etree._Element | None, name: str) -> Iterator[etree._Element]:
    """Yield the direct children of `parent` whose local name is `name`."""
    if parent is None:
        return
    # tag=etree.Element skips comments and processing instructions.
    for child in parent.iterchildren(tag=etree.Element):
        if _local_name(child) == name:
            yield child


def _child(parent: etree._Element | None, name: str) -> etree._Element | None:
    """Return the first direct child named `name`, or None if there is none."""
    return next(_children(parent, name), None)


def _path(parent: etree._Element | None, *names: str) -> etree._Element | None:
    """Walk down a chain of child names, e.g. _path(root, "headerData", "filerInfo")."""
    node = parent
    for name in names:
        node = _child(node, name)
        if node is None:
            return None
    return node


def _text(parent: etree._Element | None, *names: str) -> str | None:
    """Return the stripped text at a child path, or None when missing or blank."""
    node = _path(parent, *names)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


# ---------------------------------------------------------------- value converters


def _to_date(value: str | None, field_name: str) -> date | None:
    """Convert a Form 144 date to `date`.

    Form 144 writes dates as MM/DD/YYYY (US order). ISO YYYY-MM-DD is accepted
    too, in case a filer agent ever sends it.
    """
    if value is None:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return date.strptime(value, fmt)  # date.strptime is new in 3.14
        except ValueError:
            continue
    raise ParseError(f"{field_name}: not a date: {value!r}")


def _to_decimal(value: str | None, field_name: str) -> Decimal | None:
    """Convert a share count or dollar amount to an exact Decimal.

    Thousands separators ("1,000") are removed. Anything else that is not a
    finite number is a ParseError, never a silent zero.
    """
    if value is None:
        return None
    cleaned = value.replace(",", "")
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        raise ParseError(f"{field_name}: not a number: {value!r}") from None
    if not number.is_finite():
        raise ParseError(f"{field_name}: not a finite number: {value!r}")
    return number


def _to_bool(value: str | None, field_name: str) -> bool | None:
    """Convert a Y/N flag (the Form 144 style) to bool; None when missing."""
    if value is None:
        return None
    flag = value.strip().upper()
    if flag in ("Y", "YES", "TRUE", "1"):
        return True
    if flag in ("N", "NO", "FALSE", "0"):
        return False
    raise ParseError(f"{field_name}: not a Y/N flag: {value!r}")


def _to_cik(value: str | None, field_name: str) -> str | None:
    """Normalize a CIK to the 10-digit zero-padded form ("0001640147")."""
    if value is None:
        return None
    if not value.isdigit() or len(value) > 10:
        raise ParseError(f"{field_name}: not a CIK: {value!r}")
    return value.zfill(10)


# ---------------------------------------------------------------- table rows


def _parse_security(node: etree._Element) -> Form144Security:
    """One <securitiesInformation> block: a class of securities to be sold."""
    return Form144Security(
        class_title=_text(node, "securitiesClassTitle"),
        # The broker who will execute the sale. Their address is skipped.
        broker_name=_text(node, "brokerOrMarketmakerDetails", "name"),
        # Despite its name, noOfUnitsSold is the number of units TO BE sold.
        units_to_sell=_to_decimal(_text(node, "noOfUnitsSold"), "noOfUnitsSold"),
        # The seller's estimate of the market value of those units (dollars).
        aggregate_market_value=_to_decimal(
            _text(node, "aggregateMarketValue"), "aggregateMarketValue"
        ),
        # Total units of the class outstanding, per the issuer's latest report.
        units_outstanding=_to_decimal(
            _text(node, "noOfUnitsOutstanding"), "noOfUnitsOutstanding"
        ),
        approx_sale_date=_to_date(_text(node, "approxSaleDate"), "approxSaleDate"),
        exchange=_text(node, "securitiesExchangeName"),
    )


def _parse_acquisition(node: etree._Element) -> Form144Acquisition:
    """One <securitiesToBeSold> block: how the seller got the shares being sold.

    Rule 144 has a holding period for restricted stock, which is why the form
    asks when and how the shares were acquired and when they were paid for.
    """
    return Form144Acquisition(
        class_title=_text(node, "securitiesClassTitle"),
        acquired_date=_to_date(_text(node, "acquiredDate"), "acquiredDate"),
        nature=_text(node, "natureOfAcquisitionTransaction"),
        # Note the lowercase "f" in the SEC's element name.
        acquired_from=_text(node, "nameOfPersonfromWhomAcquired"),
        is_gift=_to_bool(_text(node, "isGiftTransaction"), "isGiftTransaction"),
        amount=_to_decimal(
            _text(node, "amountOfSecuritiesAcquired"), "amountOfSecuritiesAcquired"
        ),
        payment_date=_to_date(_text(node, "paymentDate"), "paymentDate"),
        payment_nature=_text(node, "natureOfPayment"),
    )


def _parse_past_sale(node: etree._Element) -> Form144PastSale:
    """One <securitiesSoldInPast3Months> block.

    The seller may be the person themselves or a related account (a trust, for
    example), so the seller name is kept per row.
    """
    return Form144PastSale(
        seller_name=_text(node, "sellerDetails", "name"),
        class_title=_text(node, "securitiesClassTitle"),
        sale_date=_to_date(_text(node, "saleDate"), "saleDate"),
        amount_sold=_to_decimal(_text(node, "amountOfSecuritiesSold"), "amountOfSecuritiesSold"),
        gross_proceeds=_to_decimal(_text(node, "grossProceeds"), "grossProceeds"),
    )


# ---------------------------------------------------------------- entry point


def _parse_root(xml: bytes) -> etree._Element:
    """Parse the bytes into a root element, or raise ParseError."""
    if not xml or not xml.strip():
        raise ParseError("empty document")
    if xml.startswith(_UTF8_BOM):
        xml = xml[len(_UTF8_BOM):]
    try:
        root = etree.fromstring(xml, parser=_make_parser())
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"invalid XML: {exc}") from exc
    if root is None:
        raise ParseError("document has no root element")
    if _local_name(root) != "edgarSubmission":
        raise ParseError(f"root element is {_local_name(root)!r}, expected 'edgarSubmission'")
    return root


def parse_form144(xml: bytes) -> Form144Notice:
    """Parse a Form 144 or 144/A `primary_doc.xml` into a `Form144Notice`.

    Raises ParseError when the bytes are not XML, the root is not
    `edgarSubmission`, the submission type is not 144 or 144/A, the filer CIK
    or `formData` section is missing, or a value cannot be converted.
    """
    root = _parse_root(xml)

    header = _child(root, "headerData")
    form = _child(root, "formData")
    if header is None or form is None:
        raise ParseError("missing headerData or formData")

    submission_type = _text(header, "submissionType")
    if submission_type not in _SUBMISSION_TYPES:
        raise ParseError(f"submissionType is {submission_type!r}, expected 144 or 144/A")

    # The filer is the SELLER, not the company. The <ccc> next to the CIK is a
    # masked access code ("XXXXXXXX") and is ignored.
    filer_cik = _to_cik(
        _text(header, "filerInfo", "filer", "filerCredentials", "cik"), "filer cik"
    )
    if filer_cik is None:
        raise ParseError("missing filer CIK (headerData/filerInfo/filer/filerCredentials/cik)")

    # Only amendments carry this: the accession number of the notice being amended.
    previous_accession = _text(header, "previousAccessionNumber")

    issuer = _child(form, "issuerInfo")
    relationships = tuple(
        text
        for node in _children(_child(issuer, "relationshipsToIssuer"), "relationshipToIssuer")
        if node.text is not None and (text := node.text.strip())
    )

    securities = tuple(_parse_security(n) for n in _children(form, "securitiesInformation"))
    acquisitions = tuple(_parse_acquisition(n) for n in _children(form, "securitiesToBeSold"))
    past_sales = tuple(_parse_past_sale(n) for n in _children(form, "securitiesSoldInPast3Months"))

    # "Y" means the seller checked the box saying there is nothing to report,
    # i.e. no sales in the past 3 months. "N" means past sales are listed.
    nothing_sold = _to_bool(
        _text(form, "nothingToReportFlagOnSecuritiesSoldInPast3Months"),
        "nothingToReportFlagOnSecuritiesSoldInPast3Months",
    )

    signature = _child(form, "noticeSignature")
    # Rule 10b5-1 trading plans: the date(s) the seller adopted a pre-arranged
    # selling plan. Empty (<planAdoptionDates/>) when the sale is not under a plan.
    plan_dates: list[date] = []
    for node in _children(_child(signature, "planAdoptionDates"), "planAdoptionDate"):
        value = _to_date((node.text or "").strip() or None, "planAdoptionDate")
        if value is not None:
            plan_dates.append(value)

    return Form144Notice(
        submission_type=submission_type,
        previous_accession=previous_accession,
        filer_cik=filer_cik,
        issuer_cik=_to_cik(_text(issuer, "issuerCik"), "issuerCik"),
        issuer_name=_text(issuer, "issuerName"),
        person_name=_text(issuer, "nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold"),
        relationships=relationships,
        securities=securities,
        acquisitions=acquisitions,
        past_sales=past_sales,
        nothing_sold_past_3_months=nothing_sold,
        notice_date=_to_date(_text(signature, "noticeDate"), "noticeDate"),
        plan_adoption_dates=tuple(plan_dates),
        remarks=_text(form, "remarks"),
    )
