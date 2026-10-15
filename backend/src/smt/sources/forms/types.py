"""What the SEC form parsers return. Loaders consume exactly these types.

Parsers are pure functions: bytes in, one of these dataclasses out, no I/O.
That keeps them testable against saved fixtures (golden files) and lets
`smt reprocess` re-run them over the raw store.

Conventions:
* A missing value is None, never 0 or "".
* Numbers are Decimal (exact), dates are `date`.
* CIKs are 10-digit zero-padded strings ("0001067983").
* Addresses are NOT parsed or stored anywhere (spec §12: public-role facts only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

TableName = Literal["nonderivative", "derivative"]
RowKind = Literal["transaction", "holding"]


# ---------------------------------------------------------------- Forms 3, 4, 5 (and /A)

@dataclass(frozen=True)
class ReportingOwner:
    """One reporting owner on an ownership document (joint filings have up to 10)."""

    cik: str
    name: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    is_other: bool
    officer_title: str | None
    other_text: str | None


@dataclass(frozen=True)
class OwnershipRow:
    """One row of the non-derivative or derivative table.

    `line_no` is the 1-based position of this row among rows of the same
    (table, kind) in document order. That is the stable key the loader uses,
    and it matches the SEC's bulk data sets, which split transactions and
    holdings into separate tables.
    """

    table: TableName
    kind: RowKind
    line_no: int
    security_title: str | None

    # Transaction fields (None for holdings)
    transaction_date: date | None = None
    deemed_execution_date: date | None = None
    form_type: str | None = None             # transactionFormType ("4", "5", "3")
    code: str | None = None                  # P, S, A, M, F, G, ...
    equity_swap: bool | None = None          # equitySwapInvolved
    timeliness: str | None = None            # transactionTimeliness: "E" early, "L" late
    shares: Decimal | None = None            # transactionShares
    price: Decimal | None = None             # transactionPricePerShare
    price_is_weighted_avg: bool = False      # a price footnote says "weighted average"
    acquired_disposed: str | None = None     # "A" or "D"

    # After the transaction (transactions) / as held (holdings)
    shares_owned_after: Decimal | None = None
    value_owned_after: Decimal | None = None
    direct_indirect: str | None = None       # "D" or "I"
    nature_of_ownership: str | None = None   # e.g. "By Family Trust"

    # Derivative-only fields
    conversion_or_exercise_price: Decimal | None = None
    exercise_date: date | None = None
    expiration_date: date | None = None
    underlying_title: str | None = None
    underlying_shares: Decimal | None = None

    #: Every footnote id referenced anywhere in this row, in document order, deduplicated.
    footnote_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OwnershipDocument:
    """A parsed `ownershipDocument` (Forms 3, 3/A, 4, 4/A, 5, 5/A)."""

    schema_version: str | None
    document_type: str                         # "3", "3/A", "4", "4/A", "5", "5/A"
    period_of_report: date | None
    issuer_cik: str
    issuer_name: str
    issuer_ticker: str | None
    owners: tuple[ReportingOwner, ...]
    rows: tuple[OwnershipRow, ...]
    footnotes: dict[str, str] = field(default_factory=dict)
    remarks: str | None = None
    #: Amendments only: the filing date of the original (EDGAR gives no accession link).
    date_of_original_submission: date | None = None
    #: Rule 10b5-1 box (<aff10b5One>), Forms 4/5 from 2023. None when absent.
    aff10b5one: bool | None = None
    not_subject_to_section16: bool | None = None
    no_securities_owned: bool | None = None            # Form 3
    form3_holdings_reported: bool | None = None        # Form 5
    form4_transactions_reported: bool | None = None    # Form 5
    signatures: tuple[tuple[str, date | None], ...] = ()

    @property
    def is_amendment(self) -> bool:
        return self.document_type.endswith("/A")


# ---------------------------------------------------------------- Form 144 (and 144/A)

@dataclass(frozen=True)
class Form144Security:
    """One class of securities the seller intends to sell."""

    class_title: str | None
    broker_name: str | None
    units_to_sell: Decimal | None
    aggregate_market_value: Decimal | None
    units_outstanding: Decimal | None
    approx_sale_date: date | None
    exchange: str | None


@dataclass(frozen=True)
class Form144Acquisition:
    """How the seller acquired the securities being sold (the 'nature of acquisition' table)."""

    class_title: str | None
    acquired_date: date | None
    nature: str | None
    acquired_from: str | None
    is_gift: bool | None
    amount: Decimal | None
    payment_date: date | None
    payment_nature: str | None


@dataclass(frozen=True)
class Form144PastSale:
    """A sale of the same issuer's securities by the seller in the past 3 months."""

    seller_name: str | None
    class_title: str | None
    sale_date: date | None
    amount_sold: Decimal | None
    gross_proceeds: Decimal | None


@dataclass(frozen=True)
class Form144Notice:
    """A parsed Form 144: notice of a PROPOSED sale, not a completed one."""

    submission_type: str                 # "144" or "144/A"
    previous_accession: str | None       # 144/A only: the filing it amends
    filer_cik: str                       # the seller's CIK (filerCredentials)
    issuer_cik: str | None
    issuer_name: str | None
    person_name: str | None              # nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold
    relationships: tuple[str, ...]       # e.g. ("Officer", "Director")
    securities: tuple[Form144Security, ...]
    acquisitions: tuple[Form144Acquisition, ...]
    past_sales: tuple[Form144PastSale, ...]
    nothing_sold_past_3_months: bool | None
    notice_date: date | None
    plan_adoption_dates: tuple[date, ...]
    remarks: str | None
