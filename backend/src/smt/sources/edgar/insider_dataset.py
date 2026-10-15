"""Reader for the SEC "Insider Transactions Data Sets" (bulk Forms 3, 4 and 5).

Background:

* Every quarter the SEC publishes a zip (e.g. ``2026q2_form345.zip``) with the
  contents of every Form 3/4/5 XML filed that quarter, flattened into eight
  tab-separated tables:

  ==================  ====================================================
  SUBMISSION          one row per filing (document type, issuer, checkboxes)
  REPORTINGOWNER      one row per reporting owner (joint filings: several)
  NONDERIV_TRANS      Table I transaction rows
  NONDERIV_HOLDING    Table I holding rows
  DERIV_TRANS         Table II transaction rows
  DERIV_HOLDING       Table II holding rows
  FOOTNOTES           one row per footnote (id + text)
  OWNER_SIGNATURE     one row per signature
  ==================  ====================================================

* This module turns those tables back into the same `OwnershipDocument`
  objects that `smt.sources.forms.ownership.parse_ownership` builds from the
  XML, so the loader can ingest history through either path with identical
  natural keys (accession number + table + kind + line_no).

How the tables map back to the XML (verified against the raw XML of 54 real
filings from 2026 Q2; see tests/golden/test_insider_dataset_crosscheck.py):

* Row order. Each row table has a surrogate key column (``*_SK``). Within one
  accession, ascending surrogate key is the order the rows appear in the XML,
  so ``line_no`` = 1-based rank by surrogate key within (accession, table,
  kind). The physical order of lines in the files is NOT document order.
* Footnotes. Each field has a companion ``<FIELD>_FN`` column holding the ids
  attached to it, as "F1" or "F6, F7, F2". The columns are laid out in the
  same order as the XML elements, so reading the ``_FN`` columns left to right
  gives document order across fields. WITHIN one cell the SEC lists the ids in
  no reliable order ("F6, F7, F2" for XML F2, F6, F7), so we sort each cell
  by footnote number; that reproduced the XML order in every sampled row.
  ``EQUITY_SWAP_TRANS_CD_FN`` holds the footnotes on ``<transactionCoding>``.
* Relationships are text such as "Director,TenPercentOwner".
* Dates are "30-JUN-2026". Values containing a double quote are CSV-quoted
  ("" inside). Empty cells are missing values.

Where the data set cannot reproduce the XML (reported as-is, never "fixed"):

* ``schema_version`` is not in the data set, so it is always None here.
* Numbers are rounded to 2 decimal places (half-up in every case seen): an
  XML price of 23.4567 is 23.46 here. Whole numbers come as "100.0", which is
  numerically equal to the XML's 100 (Decimal compares by value).
* Owners and signatures are in no reliable order (there is no surrogate key
  and the file order differs from the XML). We keep file order.
* Newlines in REMARKS are flattened to spaces.
* The order of rows ACROSS kinds is lost: rows come out as Table I
  transactions, Table I holdings, Table II transactions, Table II holdings,
  while a few XML files put holdings before transactions. (table, kind,
  line_no), the key the loader uses, is unaffected.
* SEC defect: NATURE_OF_OWNERSHIP and NATURE_OF_OWNERSHIP_FN are shifted
  onto the wrong rows whenever some rows of a (table, kind) lack a
  <natureOfOwnership> element. The SEC packs the values that exist into the
  first rows, e.g. XML rows (none, "By Trust") arrive as ("By Trust", none).
  In 2026 Q2 about 2,900 of 143,000 rows (6% of Table I holdings) show the
  tell-tale sign: a direct "D" row with a nature text while an indirect "I"
  row has none. Other fields of those rows are correct. We cannot tell which
  rows the values belong to, so they are passed through unchanged; the XML
  path does not have this problem.

Memory: a quarter is ~11 MB zipped but ~75 MB of text in ~400,000 rows. The
tables are loaded into a temporary on-disk DuckDB database (DuckDB spills to
disk instead of growing without bound) and documents are assembled a batch of
accessions at a time, so memory stays bounded however big the zip is.

Errors: a value that cannot be converted raises `ParseError` naming the
accession, which stops the iteration (the generator cannot resume after an
exception).
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb

from smt.sources.forms.errors import ParseError

# These helpers are private to the XML parser, but reusing them (instead of
# copying them) guarantees both ingestion paths convert values identically:
# the same booleans ("1"/"true"/"0"/"false"), tickers ("N/A" -> None), CIK
# padding, numbers and "weighted average" detection.
from smt.sources.forms.ownership import (
    _DOCUMENT_TYPES,
    _WEIGHTED_AVG_RE,
    _normalize_ticker,
    _to_bool,
    _to_cik,
    _to_decimal,
)
from smt.sources.forms.types import (
    OwnershipDocument,
    OwnershipRow,
    ReportingOwner,
    RowKind,
    TableName,
)

READER_NAME = "insider_dataset"
READER_VERSION = "1.0.0"

# How many accessions to assemble per round trip to DuckDB. A quarter has
# ~55,000 accessions, so this is ~30 small queries per table.
_BATCH_SIZE = 2000

# Refuse absurd members (a corrupt or malicious zip). A real quarter's biggest
# table is ~40 MB uncompressed.
_MAX_MEMBER_BYTES = 2 * 1024**3

_UTF8_BOM = b"\xef\xbb\xbf"

# Month abbreviations used in the data set's DD-MON-YYYY dates. A fixed map,
# because strptime's %b depends on the machine's locale.
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}  # fmt: skip

# REPORTINGOWNER.RPTOWNER_RELATIONSHIP tokens -> which checkbox they mean.
_RELATIONSHIP_TOKENS = {
    "director": "director",
    "officer": "officer",
    "tenpercentowner": "ten_percent_owner",
    "other": "other",
}


@dataclass(frozen=True)
class _RowTable:
    """One of the four row tables and how it maps onto `OwnershipRow`."""

    name: str  # table name in the zip, e.g. "NONDERIV_TRANS"
    table: TableName
    kind: RowKind
    surrogate_key: str  # column whose ascending value is document order


_ROW_TABLES = (
    _RowTable("NONDERIV_TRANS", "nonderivative", "transaction", "NONDERIV_TRANS_SK"),
    _RowTable("NONDERIV_HOLDING", "nonderivative", "holding", "NONDERIV_HOLDING_SK"),
    _RowTable("DERIV_TRANS", "derivative", "transaction", "DERIV_TRANS_SK"),
    _RowTable("DERIV_HOLDING", "derivative", "holding", "DERIV_HOLDING_SK"),
)

# Tables that must be in every zip; the rest are treated as empty if absent.
_REQUIRED_TABLES = ("SUBMISSION", "REPORTINGOWNER")
_OTHER_TABLES = ("FOOTNOTES", "OWNER_SIGNATURE", *(spec.name for spec in _ROW_TABLES))

# One database row: column name -> cell text (None when the cell is empty).
_Record = dict[str, str | None]


# ---------------------------------------------------------------- value converters


def _clean(text: str | None) -> str | None:
    """Strip whitespace; an empty cell becomes None (a missing value, never "")."""
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def _to_date(text: str | None, what: str) -> date | None:
    """Convert the data set's "30-JUN-2026" format to a date; empty -> None."""
    text = _clean(text)
    if text is None:
        return None
    parts = text.split("-")
    if len(parts) != 3 or parts[1].upper() not in _MONTHS:
        raise ParseError(f"{what}: expected a DD-MON-YYYY date, got {text!r}")
    day, month, year = parts[0], _MONTHS[parts[1].upper()], parts[2]
    if not (day.isdigit() and year.isdigit() and len(year) == 4):
        raise ParseError(f"{what}: expected a DD-MON-YYYY date, got {text!r}")
    try:
        return date(int(year), month, int(day))
    except ValueError as exc:  # e.g. 30-FEB-2026
        raise ParseError(f"{what}: invalid date {text!r}") from exc


def _footnote_number(footnote_id: str) -> tuple[int, str]:
    """Sort key for footnote ids: "F2" before "F10" (by number, then by text)."""
    digits = "".join(ch for ch in footnote_id if ch.isdigit())
    return (int(digits) if digits else 0, footnote_id)


def _footnote_list(text: str | None) -> list[str]:
    """Split a ``*_FN`` cell ("F6, F7, F2") into ids, sorted by footnote number.

    The data set lists the ids of one cell in no reliable order, while filers
    list them in ascending order in the XML, so sorting restores the XML order.
    """
    if text is None:
        return []
    return sorted((part.strip() for part in text.split(",") if part.strip()), key=_footnote_number)


def _collapse_whitespace(text: str | None) -> str | None:
    """Collapse runs of whitespace to one space, as the XML parser does for footnotes."""
    if text is None:
        return None
    return " ".join(text.split()) or None


# ---------------------------------------------------------------- building one document


def _get(record: _Record, *names: str) -> str | None:
    """The cleaned value of the first of `names` that exists as a column, else None.

    Several names cover spelling differences between tables, e.g. DERIV_TRANS
    spells the exercise date "EXCERCISE_DATE" while DERIV_HOLDING spells it
    "EXERCISE_DATE".
    """
    for name in names:
        if name in record:
            return _clean(record[name])
    return None


def _build_owner(record: _Record, where: str) -> ReportingOwner:
    """Turn one REPORTINGOWNER row into a `ReportingOwner` (addresses are skipped)."""
    cik = _to_cik(_get(record, "RPTOWNERCIK"), f"{where} RPTOWNERCIK")
    name = _get(record, "RPTOWNERNAME")
    if name is None:
        raise ParseError(f"{where}: reporting owner {cik} has no name")

    # The four checkboxes of the form arrive as one comma-separated text,
    # e.g. "Director,Officer,TenPercentOwner". A box that is not listed is
    # unchecked (False), matching the XML parser.
    checked: set[str] = set()
    for token in (_get(record, "RPTOWNER_RELATIONSHIP") or "").split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token not in _RELATIONSHIP_TOKENS:
            raise ParseError(f"{where}: unknown owner relationship {token!r}")
        checked.add(_RELATIONSHIP_TOKENS[token])

    return ReportingOwner(
        cik=cik,
        name=name,
        is_director="director" in checked,
        is_officer="officer" in checked,
        is_ten_percent_owner="ten_percent_owner" in checked,
        is_other="other" in checked,
        officer_title=_get(record, "RPTOWNER_TITLE"),
        other_text=_get(record, "RPTOWNER_TXT"),
    )


def _build_row(
    record: _Record,
    spec: _RowTable,
    line_no: int,
    footnotes: dict[str, str],
    where: str,
) -> OwnershipRow:
    """Turn one row-table record into an `OwnershipRow`, field for field like the XML parser.

    Columns that a table does not have (e.g. TRANS_CODE on a holding table)
    simply come back as None, the same as a missing XML element.
    """
    where = f"{where} {spec.table} {spec.kind} #{line_no}"

    # Footnote ids from every *_FN column, left to right. The columns follow the
    # XML element order, so this is document order; duplicates are dropped
    # (the XML parser keeps the first occurrence of each id).
    footnote_ids: list[str] = []
    for column, cell in record.items():
        if not column.endswith("_FN"):
            continue
        for footnote_id in _footnote_list(_clean(cell)):
            if footnote_id not in footnote_ids:
                footnote_ids.append(footnote_id)

    # A price footnote that says "weighted average" means the price is an
    # average over several trades at different prices (same rule as the XML).
    price_footnotes = _footnote_list(_get(record, "TRANS_PRICEPERSHARE_FN"))
    price_is_weighted_avg = any(
        _WEIGHTED_AVG_RE.search(footnotes.get(footnote_id, "")) for footnote_id in price_footnotes
    )

    return OwnershipRow(
        table=spec.table,
        kind=spec.kind,
        line_no=line_no,
        security_title=_get(record, "SECURITY_TITLE"),
        # --- what happened (transaction tables only)
        transaction_date=_to_date(_get(record, "TRANS_DATE"), f"{where} TRANS_DATE"),
        deemed_execution_date=_to_date(
            _get(record, "DEEMED_EXECUTION_DATE"), f"{where} DEEMED_EXECUTION_DATE"
        ),
        # Which form the row belongs to; on a Form 5 a "4" means a late Form 4 item.
        form_type=_get(record, "TRANS_FORM_TYPE"),
        # P purchase, S sale, A award, M exercise/conversion, F tax withholding, G gift...
        code=_get(record, "TRANS_CODE"),
        equity_swap=_to_bool(_get(record, "EQUITY_SWAP_INVOLVED"), f"{where} EQUITY_SWAP_INVOLVED"),
        # "E" reported early, "L" reported late.
        timeliness=_get(record, "TRANS_TIMELINESS"),
        shares=_to_decimal(_get(record, "TRANS_SHARES"), f"{where} TRANS_SHARES"),
        price=_to_decimal(_get(record, "TRANS_PRICEPERSHARE"), f"{where} TRANS_PRICEPERSHARE"),
        price_is_weighted_avg=price_is_weighted_avg,
        acquired_disposed=_get(record, "TRANS_ACQUIRED_DISP_CD"),
        # --- position after the transaction (or as held, for holdings)
        shares_owned_after=_to_decimal(
            _get(record, "SHRS_OWND_FOLWNG_TRANS"), f"{where} SHRS_OWND_FOLWNG_TRANS"
        ),
        value_owned_after=_to_decimal(
            _get(record, "VALU_OWND_FOLWNG_TRANS"), f"{where} VALU_OWND_FOLWNG_TRANS"
        ),
        # "D" direct, "I" indirect (through a trust, fund, spouse, ...).
        direct_indirect=_get(record, "DIRECT_INDIRECT_OWNERSHIP"),
        nature_of_ownership=_get(record, "NATURE_OF_OWNERSHIP"),
        # --- derivative-only fields (options, RSUs, convertibles, ...)
        conversion_or_exercise_price=_to_decimal(
            _get(record, "CONV_EXERCISE_PRICE"), f"{where} CONV_EXERCISE_PRICE"
        ),
        exercise_date=_to_date(
            _get(record, "EXERCISE_DATE", "EXCERCISE_DATE"), f"{where} EXERCISE_DATE"
        ),
        expiration_date=_to_date(_get(record, "EXPIRATION_DATE"), f"{where} EXPIRATION_DATE"),
        underlying_title=_get(record, "UNDLYNG_SEC_TITLE"),
        underlying_shares=_to_decimal(
            _get(record, "UNDLYNG_SEC_SHARES"), f"{where} UNDLYNG_SEC_SHARES"
        ),
        footnote_ids=tuple(footnote_ids),
    )


def _build_document(
    accession: str,
    submission: _Record,
    children: dict[str, list[_Record]],
) -> OwnershipDocument:
    """Assemble one `OwnershipDocument` from its SUBMISSION row and its child-table rows.

    `children` maps a table name to that accession's rows, already in
    document order (by surrogate key for row tables, by file order otherwise).
    """
    where = f"accession {accession}"

    document_type = _get(submission, "DOCUMENT_TYPE")
    if document_type not in _DOCUMENT_TYPES:
        raise ParseError(f"{where}: unsupported DOCUMENT_TYPE {document_type!r}")
    issuer_cik = _to_cik(_get(submission, "ISSUERCIK"), f"{where} ISSUERCIK")
    issuer_name = _get(submission, "ISSUERNAME")
    if issuer_name is None:
        raise ParseError(f"{where}: ISSUERNAME is missing")

    # Footnote id -> text. Empty texts are dropped, as in the XML parser.
    footnotes: dict[str, str] = {}
    for record in children.get("FOOTNOTES", []):
        footnote_id = _get(record, "FOOTNOTE_ID")
        text = _collapse_whitespace(_get(record, "FOOTNOTE_TXT"))
        if footnote_id is not None and text is not None:
            footnotes[footnote_id] = text

    owners = tuple(_build_owner(record, where) for record in children.get("REPORTINGOWNER", []))
    if not owners:
        raise ParseError(f"{where}: no reporting owner")

    rows: list[OwnershipRow] = []
    for spec in _ROW_TABLES:
        for line_no, record in enumerate(children.get(spec.name, []), start=1):
            rows.append(_build_row(record, spec, line_no, footnotes, where))
    # Rows come out grouped as Table I transactions, Table I holdings, Table II
    # transactions, Table II holdings. The XML parser lists them in document
    # order instead; the two only differ in the rare filing that interleaves
    # holdings between transactions, and never in (table, kind, line_no),
    # which is the key the loader uses.

    signatures: list[tuple[str, date | None]] = []
    for record in children.get("OWNER_SIGNATURE", []):
        name = _get(record, "OWNERSIGNATURENAME")
        signed = _to_date(_get(record, "OWNERSIGNATUREDATE"), f"{where} OWNERSIGNATUREDATE")
        if name is not None:  # the XML parser skips nameless signatures too
            signatures.append((name, signed))

    return OwnershipDocument(
        schema_version=None,  # not in the data set
        document_type=document_type,
        period_of_report=_to_date(_get(submission, "PERIOD_OF_REPORT"), f"{where} PERIOD"),
        issuer_cik=issuer_cik,
        issuer_name=issuer_name,
        issuer_ticker=_normalize_ticker(_get(submission, "ISSUERTRADINGSYMBOL")),
        owners=owners,
        rows=tuple(rows),
        footnotes=footnotes,
        remarks=_get(submission, "REMARKS"),
        date_of_original_submission=_to_date(
            _get(submission, "DATE_OF_ORIG_SUB"), f"{where} DATE_OF_ORIG_SUB"
        ),
        # Rule 10b5-1 box; the column was added in 2023 and is absent from older zips.
        aff10b5one=_to_bool(_get(submission, "AFF10B5ONE"), f"{where} AFF10B5ONE"),
        not_subject_to_section16=_to_bool(
            _get(submission, "NOT_SUBJECT_SEC16"), f"{where} NOT_SUBJECT_SEC16"
        ),
        no_securities_owned=_to_bool(
            _get(submission, "NO_SECURITIES_OWNED"), f"{where} NO_SECURITIES_OWNED"
        ),
        form3_holdings_reported=_to_bool(
            _get(submission, "FORM3_HOLDINGS_REPORTED"), f"{where} FORM3_HOLDINGS_REPORTED"
        ),
        form4_transactions_reported=_to_bool(
            _get(submission, "FORM4_TRANS_REPORTED"), f"{where} FORM4_TRANS_REPORTED"
        ),
        signatures=tuple(signatures),
    )


# ---------------------------------------------------------------- zip and DuckDB plumbing


def _find_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Map table name ("SUBMISSION") -> zip member, matching "*.tsv" names case-insensitively."""
    members: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        filename = Path(info.filename).name
        if info.is_dir() or not filename.lower().endswith(".tsv"):
            continue
        table = filename[: -len(".tsv")].upper()
        if table in members:
            raise ParseError(f"zip has two {table} tables")
        members[table] = info
    missing = [name for name in _REQUIRED_TABLES if name not in members]
    if missing:
        raise ParseError(f"zip is missing required tables: {', '.join(missing)}")
    return members


def _extract(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    """Copy one member to `target`, dropping a UTF-8 BOM if present.

    The file is written under a name we choose, never the member's own path,
    so a crafted zip cannot write outside the temporary folder.
    """
    if info.file_size > _MAX_MEMBER_BYTES:
        raise ParseError(f"{info.filename} is implausibly large ({info.file_size} bytes)")
    with archive.open(info) as source, target.open("wb") as sink:
        head = source.read(len(_UTF8_BOM))
        if head != _UTF8_BOM:
            sink.write(head)
        shutil.copyfileobj(source, sink)


def _sql_string(text: str) -> str:
    """Quote `text` as a SQL string literal (file paths cannot be bound as parameters)."""
    return "'" + text.replace("'", "''") + "'"


def _load_table(con: Any, table: str, path: Path, order_column: str | None) -> None:
    """Load one TSV into DuckDB, sorted by accession and then by document order.

    Every column is read as text (``all_varchar``) so numbers keep their exact
    digits for Decimal and nothing is guessed. The column list comes from the
    file's own header row, so columns added in later years (AFF10B5ONE in
    2023) or missing from early years are handled without code changes.

    A helper column ``_ORDER`` fixes the order of rows within an accession:
    the surrogate key for row tables (that is document order), or the line's
    position in the file for tables without one (owners, signatures,
    footnotes), which at least keeps the output deterministic. ``rowid`` of a
    table created straight from the file is that position, because DuckDB
    preserves insertion order (checked on all 60,153 owner rows of 2026 Q2).
    """
    raw = f"raw_{table}"
    con.execute(
        f"CREATE TABLE {raw} AS SELECT * FROM read_csv({_sql_string(str(path))}, "
        "delim = '\t', header = true, quote = '\"', escape = '\"', "
        "all_varchar = true, auto_detect = true)"
    )
    order = f"CAST({order_column} AS BIGINT)" if order_column else "rowid"
    con.execute(
        f"CREATE TABLE {table} AS SELECT *, {order} AS _ORDER FROM {raw} "
        "ORDER BY ACCESSION_NUMBER, _ORDER"
    )
    con.execute(f"DROP TABLE {raw}")


def _fetch_range(con: Any, table: str, first: str, last: str) -> dict[str, list[_Record]]:
    """Rows of `table` whose accession is between `first` and `last`, grouped by accession."""
    result = con.execute(
        f"SELECT * EXCLUDE (_ORDER) FROM {table} "
        "WHERE ACCESSION_NUMBER BETWEEN ? AND ? ORDER BY ACCESSION_NUMBER, _ORDER",
        [first, last],
    )
    columns = [column[0] for column in result.description]
    grouped: dict[str, list[_Record]] = defaultdict(list)
    for values in result.fetchall():
        record: _Record = dict(zip(columns, values, strict=True))
        grouped[str(record["ACCESSION_NUMBER"])].append(record)
    return grouped


def _batches(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    """Yield consecutive slices of `items` of at most `size` elements."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ---------------------------------------------------------------- public entry point


def iter_dataset_documents(
    zip_path: Path,
) -> Iterator[tuple[str, date | None, OwnershipDocument]]:
    """Yield every filing in one quarterly Insider Transactions Data Set zip.

    Args:
        zip_path: A ``YYYYqN_form345.zip`` as downloaded from the SEC.

    Yields:
        ``(accession_number, filing_date, document)`` for each row of
        SUBMISSION, in accession-number order. ``document`` matches what
        `parse_ownership` returns for the same filing's XML, except for the
        fields the data set lacks or rounds (see the module docstring).

    Raises:
        ParseError: The zip lacks SUBMISSION or REPORTINGOWNER, an accession
            appears twice in SUBMISSION, or a value cannot be converted.
    """
    with (
        tempfile.TemporaryDirectory(prefix="smt-form345-", ignore_cleanup_errors=True) as tmp,
        zipfile.ZipFile(zip_path) as archive,
    ):
        folder = Path(tmp)
        members = _find_members(archive)

        # An on-disk database in the temp folder, so DuckDB can spill to disk
        # instead of holding a whole quarter in RAM.
        with duckdb.connect(str(folder / "form345.duckdb")) as con:
            con.execute("SET memory_limit = '512MB'")
            con.execute("SET preserve_insertion_order = true")  # rowid = file line order
            con.execute(f"SET temp_directory = {_sql_string(str(folder / 'spill'))}")

            loaded: list[str] = []
            for table in (*_REQUIRED_TABLES, *_OTHER_TABLES):
                if table not in members:
                    continue  # e.g. an old zip without OWNER_SIGNATURE: treated as empty
                path = folder / f"{table}.tsv"
                _extract(archive, members[table], path)
                order_column = next(
                    (spec.surrogate_key for spec in _ROW_TABLES if spec.name == table), None
                )
                _load_table(con, table, path, order_column)
                path.unlink()  # the database now holds it
                loaded.append(table)

            accessions: list[str] = [
                str(row[0])
                for row in con.execute(
                    "SELECT ACCESSION_NUMBER FROM SUBMISSION ORDER BY ACCESSION_NUMBER"
                ).fetchall()
            ]
            for previous, current in pairwise(accessions):
                if previous == current:
                    raise ParseError(f"accession {current} appears twice in SUBMISSION")

            # Rows in child tables whose accession is not in SUBMISSION are
            # ignored: there is no filing header to attach them to.
            for batch in _batches(accessions, _BATCH_SIZE):
                first, last = batch[0], batch[-1]
                by_table = {table: _fetch_range(con, table, first, last) for table in loaded}
                submissions = by_table.pop("SUBMISSION")
                for accession in batch:
                    submission = submissions[accession][0]
                    children = {table: rows.get(accession, []) for table, rows in by_table.items()}
                    document = _build_document(accession, submission, children)
                    filing_date = _to_date(
                        _get(submission, "FILING_DATE"), f"accession {accession} FILING_DATE"
                    )
                    yield accession, filing_date, document
