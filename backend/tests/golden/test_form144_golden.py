"""Golden tests for the Form 144 parser against real EDGAR filings.

Every expected value below was written by hand from reading the saved XML
fixture, not copied from parser output.

Fixtures (tests/fixtures/edgar/form144/, saved byte-for-byte from EDGAR):
* 0001959173-26-006819: Snowflake (SNOW), Benoit Dageville, 144, `com:` prefix,
  one 10b5-1 plan date, 6 past sales (some by a family trust).
* 0001959173-26-007084: the same seller two weeks later, 7 past sales.
* 0001950047-26-009752: Pharvaris (PHVS), 144/A, `ns2:` prefix, empty
  <planAdoptionDates/>, amends 0001950047-26-009654.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from smt.sources.forms.form144 import parse_form144
from smt.sources.forms.types import (
    Form144Acquisition,
    Form144Notice,
    Form144PastSale,
    Form144Security,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form144"

SNOW_REMARKS = (
    "Today's sale and some sales taking place in the past 3 months occurred in "
    "The Snow Trust U/A Dtd 9/10/2019 of which Benoit Dageville is a trustee and "
    "account stakeholder."
)
SNOW_TRUST = "The Snow Trust U/A Dtd 9/10/2019"


def _load(accession: str) -> Form144Notice:
    return parse_form144((FIXTURES / f"{accession}.xml").read_bytes())


# ---------------------------------------------------------------- SNOW 0001959173-26-006819


@pytest.fixture(scope="module")
def snow_0819() -> Form144Notice:
    return _load("0001959173-26-006819")


def test_snow_0819_header_and_issuer(snow_0819: Form144Notice) -> None:
    n = snow_0819
    assert n.submission_type == "144"
    assert n.previous_accession is None
    assert n.filer_cik == "0001821737"  # the seller, not Snowflake
    assert n.issuer_cik == "0001640147"
    assert n.issuer_name == "Snowflake Inc."
    assert n.person_name == "Dageville Benoit"
    assert n.relationships == ("Officer", "Director")
    assert n.nothing_sold_past_3_months is False
    assert n.notice_date == date(2026, 9, 9)
    assert n.plan_adoption_dates == (date(2026, 4, 3),)
    assert n.remarks == SNOW_REMARKS


def test_snow_0819_securities(snow_0819: Form144Notice) -> None:
    assert snow_0819.securities == (
        Form144Security(
            class_title="Common",
            broker_name="Fidelity Brokerage Services LLC",
            units_to_sell=Decimal("50000"),
            aggregate_market_value=Decimal("16786000.00"),
            units_outstanding=Decimal("352800000"),
            approx_sale_date=date(2026, 9, 9),
            exchange="NYSE",
        ),
    )


def test_snow_0819_acquisitions(snow_0819: Form144Notice) -> None:
    assert snow_0819.acquisitions == (
        Form144Acquisition(
            class_title="Common",
            acquired_date=date(2012, 8, 1),
            nature="Founders Shares",
            acquired_from="Issuer",
            is_gift=False,
            amount=Decimal("50000"),
            payment_date=date(2012, 8, 1),
            payment_nature="Compensation",
        ),
    )


def test_snow_0819_past_sales(snow_0819: Form144Notice) -> None:
    sales = snow_0819.past_sales
    assert len(sales) == 6
    # First row is sold by Dageville himself; the rest by his trust.
    assert sales[0] == Form144PastSale(
        seller_name="Benoit Dageville",
        class_title="Common",
        sale_date=date(2026, 7, 2),
        amount_sold=Decimal("140819"),
        gross_proceeds=Decimal("35858150.16"),
    )
    assert sales[5] == Form144PastSale(
        seller_name=SNOW_TRUST,
        class_title="Common",
        sale_date=date(2026, 8, 26),
        amount_sold=Decimal("50000"),
        gross_proceeds=Decimal("15472000.00"),
    )
    assert [s.seller_name for s in sales[1:]] == [SNOW_TRUST] * 5
    assert [s.sale_date for s in sales] == [
        date(2026, 7, 2),
        date(2026, 7, 2),
        date(2026, 7, 15),
        date(2026, 7, 29),
        date(2026, 8, 12),
        date(2026, 8, 26),
    ]


# ---------------------------------------------------------------- SNOW 0001959173-26-007084


@pytest.fixture(scope="module")
def snow_7084() -> Form144Notice:
    return _load("0001959173-26-007084")


def test_snow_7084_header_and_issuer(snow_7084: Form144Notice) -> None:
    n = snow_7084
    assert n.submission_type == "144"
    assert n.previous_accession is None
    assert n.filer_cik == "0001821737"
    assert n.issuer_cik == "0001640147"
    assert n.issuer_name == "Snowflake Inc."
    assert n.person_name == "Dageville Benoit"
    assert n.relationships == ("Officer", "Director")
    assert n.nothing_sold_past_3_months is False
    assert n.notice_date == date(2026, 9, 23)
    assert n.plan_adoption_dates == (date(2026, 4, 3),)
    assert n.remarks == SNOW_REMARKS


def test_snow_7084_securities(snow_7084: Form144Notice) -> None:
    assert snow_7084.securities == (
        Form144Security(
            class_title="Common",
            broker_name="Fidelity Brokerage Services LLC",
            units_to_sell=Decimal("50000"),
            aggregate_market_value=Decimal("16954000.00"),
            units_outstanding=Decimal("352800000"),
            approx_sale_date=date(2026, 9, 23),
            exchange="NYSE",
        ),
    )


def test_snow_7084_acquisitions(snow_7084: Form144Notice) -> None:
    assert len(snow_7084.acquisitions) == 1
    acq = snow_7084.acquisitions[0]
    assert acq.nature == "Founders Shares"
    assert acq.acquired_date == date(2012, 8, 1)
    assert acq.amount == Decimal("50000")
    assert acq.is_gift is False


def test_snow_7084_past_sales(snow_7084: Form144Notice) -> None:
    sales = snow_7084.past_sales
    assert len(sales) == 7
    # The newest row is the sale proposed in the 09/09 notice, now completed.
    assert sales[6] == Form144PastSale(
        seller_name=SNOW_TRUST,
        class_title="Common",
        sale_date=date(2026, 9, 9),
        amount_sold=Decimal("50000"),
        gross_proceeds=Decimal("16786000.00"),
    )


# ---------------------------------------------------------------- PHVS 144/A 0001950047-26-009752


@pytest.fixture(scope="module")
def phvs() -> Form144Notice:
    return _load("0001950047-26-009752")


def test_phvs_amendment_header(phvs: Form144Notice) -> None:
    assert phvs.submission_type == "144/A"
    assert phvs.previous_accession == "0001950047-26-009654"
    assert phvs.filer_cik == "0002014983"


def test_phvs_issuer_and_person(phvs: Form144Notice) -> None:
    assert phvs.issuer_cik == "0001830487"
    assert phvs.issuer_name == "PHARVARIS N.V."
    assert phvs.person_name == "JOHANNES GER. CHRIS. PE SCHIKAN"
    assert phvs.relationships == ("Officer", "Director")


def test_phvs_securities(phvs: Form144Notice) -> None:
    assert phvs.securities == (
        Form144Security(
            class_title="Common",
            broker_name="Morgan Stanley Smith Barney LLC Executive Financial Services",
            units_to_sell=Decimal("200000"),
            aggregate_market_value=Decimal("7000000.00"),
            units_outstanding=Decimal("65210590"),
            approx_sale_date=date(2026, 9, 17),
            exchange="NASDAQ",
        ),
    )


def test_phvs_acquisitions(phvs: Form144Notice) -> None:
    assert phvs.acquisitions == (
        Form144Acquisition(
            class_title="Common",
            acquired_date=date(2015, 12, 23),
            nature="Founder Stock",
            acquired_from="Issuer",
            is_gift=False,
            amount=Decimal("200000"),
            payment_date=date(2015, 12, 23),
            payment_nature="Cash",
        ),
    )


def test_phvs_past_sales(phvs: Form144Notice) -> None:
    sales = phvs.past_sales
    assert len(sales) == 3
    assert sales[0] == Form144PastSale(
        seller_name="JOHANNES GER. CHRIS. PE SCHIKAN",
        class_title="Common",
        sale_date=date(2026, 6, 30),
        amount_sold=Decimal("30712"),
        gross_proceeds=Decimal("1090048.73"),
    )
    assert [s.amount_sold for s in sales] == [
        Decimal("30712"),
        Decimal("6888"),
        Decimal("2400"),
    ]
    assert [s.gross_proceeds for s in sales] == [
        Decimal("1090048.73"),
        Decimal("241424.40"),
        Decimal("84240.00"),
    ]


def test_phvs_signature_and_remarks(phvs: Form144Notice) -> None:
    assert phvs.nothing_sold_past_3_months is False
    assert phvs.notice_date == date(2026, 9, 22)
    # <planAdoptionDates/> is present but empty: no 10b5-1 plan.
    assert phvs.plan_adoption_dates == ()
    assert phvs.remarks == (
        "This Form 144/A is being filed to amend Amount of Securities Sold and "
        "Gross Proceeds for trade completed on 06/30/2026."
    )
