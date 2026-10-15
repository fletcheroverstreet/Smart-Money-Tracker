"""Parser for SEC ownership documents: Forms 3, 4 and 5 (and their /A amendments).

Background (for readers new to Section 16 filings):

* Company insiders (directors, officers, and anyone owning more than 10% of a
  class of stock) must tell the SEC about their holdings and trades under
  Section 16 of the Securities Exchange Act of 1934.
* Form 3 is the first statement of what an insider owns. Form 4 reports a
  change (a buy, sale, grant, option exercise, gift, ...), normally within
  two business days. Form 5 is an annual catch-up for transactions that were
  exempt from Form 4 or were not reported on time.
* Each filing carries one machine-readable XML file whose root element is
  <ownershipDocument>. It has two tables: Table I (non-derivative securities,
  e.g. common stock) and Table II (derivative securities, e.g. options, RSUs,
  convertible shares). Each table holds "transaction" rows (something
  happened) and "holding" rows (a position that is only being stated).

This module is a pure function: bytes in, `OwnershipDocument` out, no I/O.
Anything malformed raises `ParseError`, which sends the raw document to
quarantine instead of loading half-parsed data.

Parsing rules that apply everywhere below:

* Elements are matched by LOCAL NAME, so namespace prefixes never matter.
* A missing or empty value is None, never 0 or "".
* Most fields wrap the value in <value>, and may also carry <footnoteId>
  children. A field can hold only a footnote (for example "see footnote 2"
  instead of a price), in which case the value is None but the footnote id is
  still collected for the row.
* Addresses are never read (spec §12: public-role facts only).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

from smt.sources.forms.errors import ParseError
from smt.sources.forms.types import (
    OwnershipDocument,
    OwnershipRow,
    ReportingOwner,
    RowKind,
    TableName,
)

PARSER_NAME = "ownership"
PARSER_VERSION = "1.0.0"

# The only document types the SEC's ownershipDocument schema allows.
_DOCUMENT_TYPES = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})

# Placeholder tickers that filers type when the issuer has no listed stock.
_NO_TICKER = frozenset({"", "NONE", "N/A"})

# Booleans appear as true/false or 1/0 depending on the filing software.
_TRUE_WORDS = frozenset({"true", "1"})
_FALSE_WORDS = frozenset({"false", "0"})

# "YYYY-MM-DD", optionally followed by a timezone ("Z", "-05:00", "+01:00").
# Some filing agents emit dates like "2026-09-17-05:00"; the timezone is dropped
# because a filing date is a calendar date, not a moment in time.
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:Z|[+-]\d{2}:\d{2})?")

# A price footnote saying "weighted average" (or "weighted-average") means the
# reported price is an average over several trades at different prices, not
# the price of one trade.
_WEIGHTED_AVG_RE = re.compile(r"weighted[\s-]+average", re.IGNORECASE)

# The table element names and the row element names they contain, mapped to
# the (table, kind) labels used in `OwnershipRow`.
_TABLES: dict[str, TableName] = {
    "nonDerivativeTable": "nonderivative",
    "derivativeTable": "derivative",
}
_ROW_KINDS: dict[str, RowKind] = {
    "nonDerivativeTransaction": "transaction",
    "nonDerivativeHolding": "holding",
    "derivativeTransaction": "transaction",
    "derivativeHolding": "holding",
}

_UTF8_BOM = b"\xef\xbb\xbf"


# ---------------------------------------------------------------- small XML helpers


def _local_name(element: etree._Element) -> str | None:
    """Return the element's tag without any namespace, or None for comments/PIs."""
    # Comments and processing instructions have a non-string tag at runtime
    # (the type stubs claim str, hence the explicit `object`).
    tag: object = element.tag
    if not isinstance(tag, str):
        return None
    return etree.QName(tag).localname


def _children(parent: etree._Element | None, name: str) -> list[etree._Element]:
    """All direct children of `parent` whose local name is `name`, in document order."""
    if parent is None:
        return []
    return [child for child in parent if _local_name(child) == name]


def _child(parent: etree._Element | None, name: str) -> etree._Element | None:
    """The first direct child of `parent` with local name `name`, or None."""
    found = _children(parent, name)
    return found[0] if found else None


def _clean(text: str | None) -> str | None:
    """Strip whitespace; an empty result becomes None."""
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def _field_text(field: etree._Element | None) -> str | None:
    """Read a field's value, whether or not it is wrapped in <value>.

    Handles the three shapes seen in real filings:
      <transactionCode>S</transactionCode>                    -> "S"
      <transactionShares><value>100</value></transactionShares> -> "100"
      <exerciseDate><footnoteId id="F2"/></exerciseDate>      -> None
    """
    if field is None:
        return None
    value = _child(field, "value")
    if value is not None:
        return _clean(value.text)
    # No <value> wrapper: the text sits directly in the element (it may also
    # contain only footnote references, in which case the text is blank).
    return _clean(field.text)


def _footnote_ids(element: etree._Element | None) -> list[str]:
    """Every <footnoteId id="..."> anywhere inside `element`, in document order, deduplicated."""
    if element is None:
        return []
    ids: list[str] = []
    for node in element.iter():
        if _local_name(node) != "footnoteId":
            continue
        footnote_id = _clean(node.get("id"))
        if footnote_id is not None and footnote_id not in ids:
            ids.append(footnote_id)
    return ids


# ---------------------------------------------------------------- value converters


def _to_bool(text: str | None, what: str) -> bool | None:
    """Convert "true"/"false"/"1"/"0" to a bool; empty or missing -> None."""
    if text is None:
        return None
    word = text.strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    raise ParseError(f"{what}: expected a boolean, got {text!r}")


def _to_decimal(text: str | None, what: str) -> Decimal | None:
    """Convert a numeric string to an exact Decimal; empty or missing -> None."""
    if text is None:
        return None
    try:
        number = Decimal(text.strip())
    except InvalidOperation as exc:
        raise ParseError(f"{what}: expected a number, got {text!r}") from exc
    if not number.is_finite():
        # Decimal() accepts "NaN" and "Infinity"; neither is a real amount.
        raise ParseError(f"{what}: expected a finite number, got {text!r}")
    return number


def _to_date(text: str | None, what: str) -> date | None:
    """Convert "YYYY-MM-DD" (optionally with a trailing timezone) to a date."""
    if text is None:
        return None
    match = _DATE_RE.fullmatch(text.strip())
    if match is None:
        raise ParseError(f"{what}: expected a YYYY-MM-DD date, got {text!r}")
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError as exc:  # e.g. 2026-02-30
        raise ParseError(f"{what}: invalid date {text!r}") from exc


def _to_cik(text: str | None, what: str) -> str:
    """Normalize a CIK to 10 zero-padded digits. A CIK is required wherever it is read."""
    if text is None:
        raise ParseError(f"{what}: CIK is missing")
    digits = text.strip()
    if not digits.isdigit() or len(digits) > 10:
        raise ParseError(f"{what}: invalid CIK {text!r}")
    return digits.zfill(10)


def _normalize_ticker(text: str | None) -> str | None:
    """Upper-case the ticker; placeholders like "NONE" or "N/A" mean there is none."""
    if text is None:
        return None
    ticker = text.strip().upper()
    return None if ticker in _NO_TICKER else ticker


# ---------------------------------------------------------------- document sections


def _parse_root(xml: bytes) -> etree._Element:
    """Parse bytes into the <ownershipDocument> root element, safely."""
    if xml.startswith(_UTF8_BOM):
        xml = xml[len(_UTF8_BOM) :]
    # Some filing agents put blank lines before the XML declaration, which is
    # technically invalid XML; stripping them loses nothing.
    xml = xml.lstrip()
    if not xml:
        raise ParseError("ownership document is empty")

    # Hardened parser: never expand entities (defends against "billion laughs"
    # style payloads), never fetch anything over the network, and refuse
    # absurdly deep or large trees.
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(xml, parser)
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"malformed XML: {exc}") from exc

    if _local_name(root) != "ownershipDocument":
        raise ParseError(f"expected root <ownershipDocument>, got <{_local_name(root)}>")
    return root


def _parse_footnotes(root: etree._Element) -> dict[str, str]:
    """Map footnote id -> text, with runs of whitespace collapsed to one space.

    A footnote with no text is left out; any row that references it keeps the
    id in `footnote_ids`, so nothing is lost.
    """
    footnotes: dict[str, str] = {}
    for node in _children(_child(root, "footnotes"), "footnote"):
        footnote_id = _clean(node.get("id"))
        # itertext() also picks up text inside any nested markup.
        parts = [part for part in node.itertext() if isinstance(part, str)]
        text = " ".join("".join(parts).split())
        if footnote_id is not None and text:
            footnotes[footnote_id] = text
    return footnotes


def _owner_flag(relationship: etree._Element | None, name: str, cik: str) -> bool:
    """Read one relationship checkbox. An absent or empty box means "not checked".

    `ReportingOwner` stores plain bools, and the paper form has checkboxes, so
    a missing element is False rather than unknown. (Real example: NIKE's
    Form 4/A omits isTenPercentOwner and isOther entirely.)
    """
    value = _to_bool(_field_text(_child(relationship, name)), f"owner {cik} {name}")
    return bool(value)


def _parse_owners(root: etree._Element) -> tuple[ReportingOwner, ...]:
    """Read every <reportingOwner> (joint filings list up to 10). Addresses are skipped."""
    owners: list[ReportingOwner] = []
    for node in _children(root, "reportingOwner"):
        owner_id = _child(node, "reportingOwnerId")
        cik = _to_cik(_field_text(_child(owner_id, "rptOwnerCik")), "rptOwnerCik")
        name = _field_text(_child(owner_id, "rptOwnerName"))
        if name is None:
            raise ParseError(f"reporting owner {cik} has no name")

        relationship = _child(node, "reportingOwnerRelationship")
        owners.append(
            ReportingOwner(
                cik=cik,
                name=name,
                is_director=_owner_flag(relationship, "isDirector", cik),
                is_officer=_owner_flag(relationship, "isOfficer", cik),
                is_ten_percent_owner=_owner_flag(relationship, "isTenPercentOwner", cik),
                is_other=_owner_flag(relationship, "isOther", cik),
                officer_title=_field_text(_child(relationship, "officerTitle")),
                other_text=_field_text(_child(relationship, "otherText")),
            )
        )
    if not owners:
        raise ParseError("ownership document has no reporting owner")
    return tuple(owners)


def _parse_row(
    node: etree._Element,
    table: TableName,
    kind: RowKind,
    line_no: int,
    footnotes: dict[str, str],
) -> OwnershipRow:
    """Parse one table row (transaction or holding, non-derivative or derivative).

    Every field is read if present. Holdings simply lack the transaction
    fields, and non-derivative rows lack the derivative fields, so those come
    back as None without any special casing.
    """
    where = f"{table} {kind} #{line_no}"

    coding = _child(node, "transactionCoding")
    amounts = _child(node, "transactionAmounts")
    post = _child(node, "postTransactionAmounts")
    nature = _child(node, "ownershipNature")
    underlying = _child(node, "underlyingSecurity")

    # The price may point at footnotes; if any of them says "weighted average"
    # the price is an average across several trades (common for large sales
    # executed in many lots through the day).
    price_field = _child(amounts, "transactionPricePerShare")
    price_is_weighted_avg = any(
        _WEIGHTED_AVG_RE.search(footnotes.get(footnote_id, ""))
        for footnote_id in _footnote_ids(price_field)
    )

    return OwnershipRow(
        table=table,
        kind=kind,
        line_no=line_no,
        security_title=_field_text(_child(node, "securityTitle")),
        # --- what happened (transactions only)
        transaction_date=_to_date(
            _field_text(_child(node, "transactionDate")), f"{where} transactionDate"
        ),
        deemed_execution_date=_to_date(
            _field_text(_child(node, "deemedExecutionDate")), f"{where} deemedExecutionDate"
        ),
        # transactionFormType says which form the row belongs to. On a Form 5 it
        # can be "4": a transaction that should have been on a Form 4 and is
        # being reported late on the annual form.
        form_type=_field_text(_child(coding, "transactionFormType")),
        # SEC transaction codes: P open-market purchase, S open-market sale,
        # A grant/award, M option exercise or conversion, F tax withholding,
        # G gift, D disposition to the issuer, and more.
        code=_field_text(_child(coding, "transactionCode")),
        equity_swap=_to_bool(
            _field_text(_child(coding, "equitySwapInvolved")), f"{where} equitySwapInvolved"
        ),
        # "E" = reported early (voluntarily before it was due), "L" = reported late.
        timeliness=_field_text(_child(node, "transactionTimeliness")),
        shares=_to_decimal(
            _field_text(_child(amounts, "transactionShares")), f"{where} transactionShares"
        ),
        price=_to_decimal(_field_text(price_field), f"{where} transactionPricePerShare"),
        price_is_weighted_avg=price_is_weighted_avg,
        # "A" acquired or "D" disposed, reported exactly as filed (filers
        # sometimes get this wrong; we never "fix" it).
        acquired_disposed=_field_text(_child(amounts, "transactionAcquiredDisposedCode")),
        # --- position after the transaction (or as held, for holdings)
        shares_owned_after=_to_decimal(
            _field_text(_child(post, "sharesOwnedFollowingTransaction")),
            f"{where} sharesOwnedFollowingTransaction",
        ),
        # Some securities (e.g. certain notes) are reported by dollar value, not shares.
        value_owned_after=_to_decimal(
            _field_text(_child(post, "valueOwnedFollowingTransaction")),
            f"{where} valueOwnedFollowingTransaction",
        ),
        # "D" direct ownership, "I" indirect (held through a trust, fund, spouse, ...).
        direct_indirect=_field_text(_child(nature, "directOrIndirectOwnership")),
        nature_of_ownership=_field_text(_child(nature, "natureOfOwnership")),
        # --- derivative-only fields (options, RSUs, convertible shares, ...)
        conversion_or_exercise_price=_to_decimal(
            _field_text(_child(node, "conversionOrExercisePrice")),
            f"{where} conversionOrExercisePrice",
        ),
        exercise_date=_to_date(_field_text(_child(node, "exerciseDate")), f"{where} exerciseDate"),
        expiration_date=_to_date(
            _field_text(_child(node, "expirationDate")), f"{where} expirationDate"
        ),
        underlying_title=_field_text(_child(underlying, "underlyingSecurityTitle")),
        underlying_shares=_to_decimal(
            _field_text(_child(underlying, "underlyingSecurityShares")),
            f"{where} underlyingSecurityShares",
        ),
        footnote_ids=tuple(_footnote_ids(node)),
    )


def _parse_rows(root: etree._Element, footnotes: dict[str, str]) -> tuple[OwnershipRow, ...]:
    """Read both tables in document order, numbering rows per (table, kind)."""
    rows: list[OwnershipRow] = []
    counters: dict[tuple[TableName, RowKind], int] = {}
    for table_element in root:
        table = _TABLES.get(_local_name(table_element) or "")
        if table is None:
            continue
        for row_element in table_element:
            kind = _ROW_KINDS.get(_local_name(row_element) or "")
            if kind is None:
                continue
            # line_no is 1-based within (table, kind), matching the SEC's bulk
            # data sets, which keep transactions and holdings in separate files.
            line_no = counters.get((table, kind), 0) + 1
            counters[(table, kind)] = line_no
            rows.append(_parse_row(row_element, table, kind, line_no, footnotes))
    return tuple(rows)


def _parse_signatures(root: etree._Element) -> tuple[tuple[str, date | None], ...]:
    """Read (signer name, signature date) pairs. A signature block with no name is skipped."""
    signatures: list[tuple[str, date | None]] = []
    for node in _children(root, "ownerSignature"):
        name = _field_text(_child(node, "signatureName"))
        signed = _to_date(_field_text(_child(node, "signatureDate")), "signatureDate")
        if name is not None:
            signatures.append((name, signed))
    return tuple(signatures)


# ---------------------------------------------------------------- public entry point


def parse_ownership(xml: bytes) -> OwnershipDocument:
    """Parse the raw XML of a Form 3, 3/A, 4, 4/A, 5 or 5/A.

    Args:
        xml: The primary XML document of the filing, exactly as downloaded
            (the raw file, not the xslF345X0N HTML rendering).

    Returns:
        The parsed document. Values are reported as filed, including obvious
        filer errors; interpretation happens later, in the loaders.

    Raises:
        ParseError: The bytes are not well-formed XML, the root element is not
            <ownershipDocument>, a required field (document type, issuer CIK
            and name, reporting owner) is missing, or a value cannot be
            converted (bad date, number, boolean or CIK).
    """
    root = _parse_root(xml)

    document_type = _field_text(_child(root, "documentType"))
    if document_type not in _DOCUMENT_TYPES:
        raise ParseError(f"unsupported documentType {document_type!r}")

    issuer = _child(root, "issuer")
    issuer_cik = _to_cik(_field_text(_child(issuer, "issuerCik")), "issuerCik")
    issuer_name = _field_text(_child(issuer, "issuerName"))
    if issuer_name is None:
        raise ParseError("issuerName is missing")

    footnotes = _parse_footnotes(root)

    return OwnershipDocument(
        schema_version=_field_text(_child(root, "schemaVersion")),
        document_type=document_type,
        period_of_report=_to_date(_field_text(_child(root, "periodOfReport")), "periodOfReport"),
        issuer_cik=issuer_cik,
        issuer_name=issuer_name,
        issuer_ticker=_normalize_ticker(_field_text(_child(issuer, "issuerTradingSymbol"))),
        owners=_parse_owners(root),
        rows=_parse_rows(root, footnotes),
        footnotes=footnotes,
        remarks=_field_text(_child(root, "remarks")),
        # Amendments give the original's filing date, not its accession number,
        # so matching an amendment to its original is done later by the loader.
        date_of_original_submission=_to_date(
            _field_text(_child(root, "dateOfOriginalSubmission")), "dateOfOriginalSubmission"
        ),
        # Rule 10b5-1 checkbox (added to Forms 4 and 5 in 2023): the trade was
        # made under a pre-arranged trading plan. Absent in older filings.
        aff10b5one=_to_bool(_field_text(_child(root, "aff10b5One")), "aff10b5One"),
        # Checked when the filer is no longer subject to Section 16 but some
        # obligations may continue (the "exit" box on Forms 4 and 5).
        not_subject_to_section16=_to_bool(
            _field_text(_child(root, "notSubjectToSection16")), "notSubjectToSection16"
        ),
        # Form 3 only: the insider owns no securities of the issuer.
        no_securities_owned=_to_bool(
            _field_text(_child(root, "noSecuritiesOwned")), "noSecuritiesOwned"
        ),
        # Form 5 only: checkboxes saying holdings/transactions that should have
        # been on an earlier Form 3 or Form 4 are included here.
        form3_holdings_reported=_to_bool(
            _field_text(_child(root, "form3HoldingsReported")), "form3HoldingsReported"
        ),
        form4_transactions_reported=_to_bool(
            _field_text(_child(root, "form4TransactionsReported")), "form4TransactionsReported"
        ),
        signatures=_parse_signatures(root),
    )
