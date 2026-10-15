"""Absolute data-quality rules (smt.ingest.quality.row_issues).

The cases mirror real 2026 filings: a Form 4 dated 2029-02-19 but filed on
2026-03-23 (0001193125-26-118517), a total typed as the price (100,149,060
shares at $24,035,774.40, 0001140361-26-009309) and a share count typed as the
price (4,000,000 at $4,000,000, 0001628280-26-033804).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from smt.ingest.quality import (
    CURRENCY_UNSTATED,
    DATE_AFTER_FILING,
    PER_ORDINARY,
    PRICE_NOT_USD,
    RATIO_ONLY,
    VALUE_IMPLAUSIBLE,
    AdsBook,
    _ClassPrice,
    ads_eligible_title,
    ads_mismatch,
    ads_price_basis,
    ads_ratio,
    ads_row_flag,
    currency_unstated,
    is_ads_title,
    price_in_foreign_currency,
    row_issues,
)
from smt.ingest.quality import PER_ADS as BASIS_PER_ADS

D = Decimal


def issues(traded: date | None = date(2026, 3, 20), filed: date | None = date(2026, 3, 23),
           shares: str | None = "1000", price: str | None = "25.50") -> list[str]:
    s, p = (D(shares) if shares else None), (D(price) if price else None)
    value = s * p if s is not None and p is not None else None
    return row_issues(traded_on=traded, filed_on=filed, shares=s, price=p, value=value)


def test_an_ordinary_trade_has_no_issues() -> None:
    assert issues() == []


def test_trade_dated_after_its_filing() -> None:
    assert issues(traded=date(2029, 2, 19)) == [DATE_AFTER_FILING]
    # Same day is normal (filed after the close).
    assert issues(traded=date(2026, 3, 23)) == []


@pytest.mark.parametrize(("shares", "price"), [
    ("100149060", "24035774.40"),  # the total typed as the price
    ("4000000", "4000000"),        # the share count typed as the price
    ("268470", "30518001"),        # price above any listed share
])
def test_implausible_values(shares: str, price: str) -> None:
    assert issues(shares=shares, price=price) == [VALUE_IMPLAUSIBLE]


def test_legitimately_high_prices_pass() -> None:
    # A Berkshire Hathaway class A share trades near $700,000.
    assert issues(shares="10", price="712000") == []
    # Small equal numbers are coincidence, not a typing error (e.g. 25 shares at $25).
    assert issues(shares="25", price="25") == []


def test_missing_parts_are_not_errors() -> None:
    assert issues(traded=None, filed=None, shares=None, price=None) == []


def test_both_issues_at_once() -> None:
    assert issues(traded=date(2027, 1, 25), shares="4000000", price="4000000") == [DATE_AFTER_FILING,
                                                                                   VALUE_IMPLAUSIBLE]


# ---------------------------------------------------------------- ADS vs ordinary shares
# Foreign companies list American Depositary Shares; one ADS stands for N ordinary shares.
# A row counting ordinary shares but priced per ADS overstates the value N times.
ORDINARY = "Class A Ordinary Shares"
PER_ADS = "The price reported is the weighted average price per American Depositary Share (\"ADS\")."
RATIO_60 = "Each ADS represents sixty Class A ordinary shares of the Issuer."
RATIO_1 = "Each ADS represents one ordinary share."


@pytest.mark.parametrize(("texts", "ratio"), [
    ([RATIO_60], 60),
    (["Each American Depositary Share represents 43,200 ordinary shares."], 43200),
    (["One ADS (as defined below) is equal to twenty-five ordinary shares."], 25),
    ([RATIO_1], 1),
    # Mesoblast's wording.
    (["Each ADS represents a beneficial interest in 10 ordinary shares."], 10),
    (["The shares were sold in several trades."], None),
])
def test_ads_ratio_is_read_from_footnotes(texts: list[str], ratio: int | None) -> None:
    assert ads_ratio(texts) == ratio


# Real wordings (2026 filings) that the ratio parse once read by their first word only: "one
# hundred (100)" as 1, "four thousand (4,000)" as 4, "thirty-five" as nothing, "0.05" as 0.
@pytest.mark.parametrize(("text", "ratio"), [
    # Huize 5851 (13 filings) and SOLAI 6718.
    ('Each American depositary share ("ADS") represents one hundred (100) Class A common shares of Huize '
     "Holding Limited.", D(100)),
    ('Each American Depositary Share ("ADS") represents seven hundred (700) Class A ordinary shares.', D(700)),  # SOLAI
    ("Each ADS represents nine hundred (900) Class A ordinary shares of Quhuo Limited,", D(900)),               # QUHUO
    ("Each ADS represents four hundred (400) Class A Ordinary Shares.", D(400)),                               # Yunji
    ("Each American Depositary Share represents four thousand (4,000) ordinary shares, no par value.", D(4000)),  # Steakholder
    # Sound Group 5216: words only, no digits in brackets.
    ('Each American depositary share ("ADS") represents two hundred Class A ordinary shares, par value '
     "US$0.0001 per share", D(200)),
    ("Each ADS represents thirty-five (35) Class A ordinary shares of the Issuer.", D(35)),                    # ZKH
    ("Each ADS represents thirty-five ordinary shares of the Issuer.", D(35)),                                 # Quoin
    ("Each ADS represents two(2) Class A ordinary shares of the Issuer.", D(2)),
    ("One (1) American Depositary Share (ADS) represents five (5) Class A Shares.", D(5)),                     # trivago
    ("Each of American Depositary Share represents three Class A Ordinary Shares.", D(3)),                     # Baozun
    # Tuniu 5122: after a ratio change the new ratio counts, not the one it changed from.
    ('Effective April 22, 2026, the Issuer changed the ratio of its American Depositary Shares ("ADSs") from one '
     "(1) ADS representing three (3) Class A ordinary shares to one (1) ADS representing thirty (30) Class A "
     'ordinary shares (the "ADS Ratio Change").', D(30)),
    # Fractions: several ADSs per share, or N ADSs per M shares.
    ('The Ordinary Shares may be represented by American Depositary Shares ("ADS"). Each 10 ADSs represent 23 '
     "Ordinary Shares of the Issuer.", D("2.3")),                                                            # NovaBridge
    ("Each American depositary shares (two American depositary shares representing three Class A ordinary "
     "share, par value US$0.000002 per share)", D("1.5")),                                                   # Dingdong
    ("Each American Depositary Share represents 0.05 Common Share of the Issuer.", D("0.05")),                # DoubleDown
    ("Every three ADSs represent two Class A ordinary shares.", D(2) / D(3)),                                 # Gaotu
    ('Each three American Depositary Shares ("ADSs") represent one Class A common share, with a par value of '
     "$0.001 per share, of the Issuer.", D(1) / D(3)),                                                        # TAL
    # A holding, not the ratio: NovaBridge "sold 46,947 ADSs representing approximately 107,978".
    ("The Reporting Person sold 46,947 ADSs representing approximately 107,978 ordinary shares in connection "
     "with the partial vesting of a restricted stock unit award.", None),
    ("Represents 20,260,082 ordinary shares and 357,810 ADSs representing 4,293,728 ordinary shares "
     "beneficially owned by the Reporting Person.", None),                                                    # LightInTheBox
    ("These American Depositary Shares represent restricted share units, which vest in quarterly equal "
     "installments.", None),                                                                                 # Steakholder
    ("Each ADS represents one-half of one ordinary share.", None),  # a fraction in words isn't read
])
def test_ads_ratio_reads_the_whole_number(text: str, ratio: Decimal | None) -> None:
    assert ads_ratio([text]) == ratio


def test_a_ratio_of_one_hundred_is_not_one_to_one() -> None:
    """Huize: with "one hundred (100)" read as 1, any price on a count of Class A shares (100 to
    the ADS) was taken as a price per share, an ADS price too. Now the price is judged against
    the class's price per share (figures here are illustrative)."""
    huize = ['Each American depositary share ("ADS") represents one hundred (100) Class A common shares.']
    assert ads_price_basis("Class A ordinary shares", huize, huize, D("3.57")) == RATIO_ONLY
    assert ads_mismatch("Class A ordinary shares", huize, huize, D("3.57"), per_share_reference=D("0.036"))
    assert not ads_mismatch("Class A ordinary shares", huize, huize, D("0.036"), per_share_reference=D("0.035"))


def test_a_ratio_below_one_never_flags_a_price_on_its_own() -> None:
    """DoubleDown: 'Each American Depositary Share represents 0.05 Common Share' (20 ADSs to a
    share). Read as 0, every priced 'Common Shares' row was flagged (the yardstick was sqrt(0) x
    the reference). An ADS price on a count of shares would understate the value, never
    overstate it, so only a note saying the price is per ADS flags the row."""
    dd = ["Each American Depositary Share represents 0.05 Common Share of the Issuer."]
    assert ads_price_basis("Common Shares", dd, dd, D("30")) == PER_ORDINARY
    assert not ads_mismatch("Common Shares", dd, dd, D("30"), D("30"))
    assert not ads_mismatch("Common Shares", dd, dd, D("30"))
    assert ads_mismatch("Common Shares", [*dd, PER_ADS], [*dd, PER_ADS], D("1.50"))


# NovaBridge (issuer 4278): 'Ordinary Shares' sold, held as ADSs, "Each 10 ADSs represent 23
# Ordinary Shares of the Issuer" (2.3 shares per ADS).
NBP_RATIO = ('The Ordinary Shares may be represented by American Depositary Shares ("ADS"). '
             "Each 10 ADSs represent 23 Ordinary Shares of the Issuer.")


@pytest.mark.parametrize(("tx", "price", "note"), [
    ("263718", "0.74", "Reported sales price represents the converted sales price of the ADSs of $1.6908."),
    ("107496", "0.66", "Reported sales price represents the converted sales price of the ADSs of $1.51."),
    ("253362", "0.71", "Reported sales price represents the converted sales price of the ADSs of $1.6247."),
    ("270003", "0.79", "Reported sales price represents the converted sales price of the ADSs of $1.8186."),
    ("323094", "0.89", "Reported sales price represents the converted sales price of the ADSs of $2.04."),
])
def test_an_ads_price_converted_by_the_ratio_is_a_price_per_share(tx: str, price: str, note: str) -> None:
    """263718: 107,978 shares at $0.74 = $1.6908 / 2.3, the ADS price converted to a price per
    share: counted as filed ($79,903.72), not left out as a price per ADS."""
    notes = [NBP_RATIO, note]
    assert ads_price_basis("Ordinary Shares", notes, notes, D(price)) == PER_ORDINARY
    assert not ads_mismatch("Ordinary Shares", notes, notes, D(price))


def test_novabridge_rows_priced_per_ads_stay_flagged() -> None:
    """108309: $3.94 beside "the converted sales price of the ADSs of $1.71" ($1.71 x 2.3, not
    / 2.3). 34823: $1.99, "Price expressed in terms of ADSs". 31485: 230,000 shares (100,000
    ADSs) bought at $2.51, "These ADSs were purchased ... at prices ranging from $2.44 to $2.54",
    against about $0.74 a share."""
    converted = [NBP_RATIO, "Reported sales price represents the converted sales price of the ADSs of $1.71."]
    assert ads_price_basis("Ordinary Shares", converted, converted, D("3.94")) == BASIS_PER_ADS
    in_ads = [NBP_RATIO, "Price expressed in terms of ADSs."]
    assert ads_mismatch("Ordinary Shares", in_ads, in_ads, D("1.99"))
    bought = [NBP_RATIO, "The reported securities are represented by 100,000 ADSs.",
              "The price reported in Column 4 is a weighted average price. These ADSs were purchased in multiple "
              "transactions at prices ranging from $2.44 to $2.54, inclusive."]
    assert ads_price_basis("Ordinary Shares", bought, bought, D("2.51")) == RATIO_ONLY
    assert ads_mismatch("Ordinary Shares", bought, bought, D("2.51"), per_share_reference=D("0.74"))


def test_price_per_ads_on_ordinary_shares_is_a_mismatch() -> None:
    assert ads_mismatch(ORDINARY, [PER_ADS], [PER_ADS, RATIO_60], D("17.13"))
    # A stated ratio with nothing to check the price against: it could overstate 60 times.
    assert ads_mismatch(ORDINARY, ["Represented by ADSs."], [RATIO_60], D("17.13"))
    # The price is per ADS but the filing says nothing about the ratio: still doubtful.
    assert ads_mismatch(ORDINARY, [PER_ADS], [PER_ADS], D("17.13"))


def test_one_to_one_ads_or_unrelated_rows_are_fine() -> None:
    price = D("17.13")
    assert not ads_mismatch(ORDINARY, [PER_ADS], [PER_ADS, RATIO_1], price)       # 1 ADS = 1 share: no error
    assert not ads_mismatch("American Depositary Shares", [PER_ADS], [RATIO_60], price)  # counted in ADSs
    assert not ads_mismatch(ORDINARY, ["Weighted average price."], [RATIO_60], price)     # notes don't mention ADSs
    assert not ads_mismatch(None, [PER_ADS], [RATIO_60], price)
    assert not ads_mismatch(ORDINARY, [PER_ADS], [PER_ADS, RATIO_60], D("0"))  # a grant at no price
    assert not ads_mismatch(ORDINARY, [PER_ADS], [PER_ADS, RATIO_60], None)


# Real footnotes: the filing states a ratio and nothing else about the price's basis.
KANZHUN = ('These Class A ordinary shares are held through American Depositary Shares ("ADS") of the issuer. '
           "Each ADS represents two Class A ordinary shares.")
MESOBLAST_RATIO = "Each ADS represents a beneficial interest in 10 ordinary shares. Held as ADSs."


def test_a_ratio_alone_is_judged_against_the_classs_price_per_share() -> None:
    """With only a ratio, the price is flagged when it is nearer the per-ADS figure (the class's
    price per share x N) than the per-share one."""
    assert ads_price_basis(ORDINARY, [KANZHUN], [KANZHUN], D("8.28")) == RATIO_ONLY
    # Kanzhun: $8.28 against its other rows' $7.25 a share, 2 shares per ADS: per share, counted.
    assert not ads_mismatch(ORDINARY, [KANZHUN], [KANZHUN], D("8.28"), per_share_reference=D("7.25"))
    # Mesoblast: $14.10 against $1.43 a share, 10 per ADS: an ADS price, flagged.
    assert ads_mismatch(ORDINARY, [MESOBLAST_RATIO], [MESOBLAST_RATIO], D("14.10"), per_share_reference=D("1.43"))
    # 51Talk: $17.13 against about $0.30 a share (its per-ADS rows / 60): flagged.
    assert ads_mismatch(ORDINARY, ["Held as ADS.", RATIO_60], [RATIO_60], D("17.13"), per_share_reference=D("0.30"))
    # A note that says the price is per ADS doesn't need the yardstick.
    assert ads_mismatch(ORDINARY, [PER_ADS], [PER_ADS, RATIO_60], D("0.35"), per_share_reference=D("0.30"))


def test_a_note_that_the_price_is_per_ordinary_share_settles_it() -> None:
    structure = "Reflects price per share in Ordinary Shares."
    ratio = 'Each ADS represents three Ordinary Shares of the Issuer. The shares may be represented by ADSs.'
    assert ads_price_basis("Ordinary Shares", [ratio, structure], [ratio, structure], D("11.67")) == PER_ORDINARY
    assert not ads_mismatch("Ordinary Shares", [ratio, structure], [ratio, structure], D("11.67"))


def test_the_ads_flag_marks_the_value_implausible() -> None:
    assert row_issues(traded_on=None, filed_on=None, shares=D("1000"), price=D("3.45"), value=D("3450"),
                      ads=True) == [VALUE_IMPLAUSIBLE]


# ---------------------------------------------------------------- prices in a foreign currency
@pytest.mark.parametrize(("notes", "price", "foreign"), [
    (["The price reported is in New Taiwan Dollars."], "142.333", True),
    (["New Taiwan Dollars"], "142.333", True),                                                  # UMC's own note
    (["euro 24.50"], "28.27", False),              # Star Bulk: the price field holds the dollar conversion
    (["AUD$0.1550 per share."], "0.11", False),    # ioneer: likewise
    (["Great British Pounds (GBP)"], "17.19", True),
    (["Price in Mexican Pesos (MXN).", "at prices ranging from $17.20 MXN to $17.34 MXN."], "17.2812", True),  # CEMEX
    (["The price reported is in Argentine pesos."], "2115", True),                              # IRSA
    (["Price converted from Australian Dollars into U.S. Dollars based on an exchange rate."], "83.31", False),
    (["The price reported was converted from Canadian dollars to U.S. dollars."], "20.10", False),
    (["The exercise price is INR 617.90, which would correspond to an exercise price in USD $ 6.8"], "6.80", False),
    (["... resulting in a reported price of USD 1.17 per ordinary share."], "1.17", False),    # Cresud
    (["Shares trade on the Hong Kong Stock Exchange. The price reported is a weighted average."], "10", False),
    (["The exercise price is C$12.00."], "0", False),                                           # no price, no issue
    # Converted by the filer (round 4): Elbit, Nayax, HSBC.
    (["The price was paid in New Israeli Shekel. For purposes of this Form 4, a conversion rate of NIS 3.142 "
      "for each USD $1.00 was used."], "887.40", False),
    (["The exchange rate in effect on the transaction date was 3.038 ILS to $1.00."], "42.90", False),
    (["The U.S. dollar amount reported reflects the exchange rate on the transaction date."], "40.10", False),
    (["The price was GBP11.91276, which was converted to USD at the rate of 1.3272."], "15.81", False),
    # Bending Spoons (tx 298094, 0001214607-26-000017): the whole stored note. "exchange ratio", not
    # "rate", and the dots of "U.S." inside the sentence; the first sentence names euros.
    (["The exercise price is denominated in euros. The price reported herein is based on an exchange ratio "
      "of 1.1607 U.S. dollars per 1.00 euro."], "1.2396", False),
    (["The exercise price is denominated in euros. The price reported herein is based on an exchange ratio "
      "of 1.1607 U.S. dollars per 1.00 euro.", "The warrants are fully vested and exercisable."], "1.2396", False),
    # ...but the gap still stops at a sentence's end: a later sentence's "dollars" doesn't convert.
    (["The price is in euros based on an exchange ratio. Holdings are worth 5 dollars."], "1.2396", True),
    # ...also after a one-letter word, in either case.
    (["The price is in euros at the exchange rate for Class A. The dollars are not shown."], "1.2396", True),
    (["The price is in euros at the exchange rate for plan a. The dollars are not shown."], "1.2396", True),
    # A dollar sign in a sentence naming another currency isn't dollars (ASE), and reais are foreign (Cosan).
    (["New Taiwan Dollars.", "Prices ranging from $465 to $465.5 New Taiwan Dollars per share."], "465.11", True),
    (["Brazilian reais."], "5.14", True),
    (["Price in BRL"], "5.14", True),
])
def test_price_in_a_foreign_currency(notes: list[str], price: str, foreign: bool) -> None:
    assert price_in_foreign_currency(notes, D(price)) is foreign


def test_a_foreign_currency_price_is_left_out_of_dollar_totals() -> None:
    assert row_issues(traded_on=None, filed_on=None, shares=D("1200000"), price=D("142.333"),
                      value=D("170799600"), foreign_currency=True) == [VALUE_IMPLAUSIBLE, PRICE_NOT_USD]


def test_an_ads_note_giving_the_rows_own_price_per_ordinary_share_is_fine() -> None:
    """BeOne: 'at a price of $23.8512 per ordinary share or $310.0659 per ADS', price field $23.85."""
    note = "Sold at a price of $23.8512 per ordinary share or $310.0659 per ADS. Each ADS represents 13 ordinary shares."
    assert not ads_mismatch(ORDINARY, [note], [note], D("23.85"))
    assert ads_mismatch(ORDINARY, [note], [note], D("310.07"))  # the ADS price on ordinary shares



def test_ads_notes_that_already_give_per_ordinary_prices() -> None:
    mesoblast = "Purchase prices of ADS have been adjusted to show price per underlying ordinary share."
    assert not ads_mismatch(ORDINARY, [mesoblast], [mesoblast, "Each ADS represents 10 ordinary shares."], D("1.44"))
    legend = "Sold at prices ranging from $8.905 to $9.325 per ordinary share, in ADS form."
    assert not ads_mismatch(ORDINARY, [legend], [legend, "Each ADS represents two ordinary shares."], D("9.14"))


def test_the_recompute_prefilter_covers_every_currency_the_check_knows() -> None:
    """The 30-minute job's SQL pre-filter is built from the same pattern (never narrower)."""
    from smt.ingest.quality import _CURRENCIES, CURRENCY_HINT_SQL

    assert _CURRENCIES.pattern.replace(r"\b", r"\y") == CURRENCY_HINT_SQL
    assert "brazilian" in CURRENCY_HINT_SQL
    assert "BRL" in CURRENCY_HINT_SQL


# ---------------------------------------------------------------- a foreign listing, no currency stated
@pytest.mark.parametrize(("title", "notes", "price", "unstated"), [
    # Bank Bradesco: the B3 preferred share, priced in reais, no note at all.
    ("Preference shares - BBDC4", [], "17.98", True),
    # Its fractional lots trade on B3's odd-lot market, whose codes end in F (tx 265597, 292383).
    ("Preference shares - BBDC4F", [], "18.41", True),
    ("Preference shares - BBDC2F", [], "0.06", True),
    ("COMMON STOCK (BVMF: CSAN3)", [], "4.37", True),
    ("Common Shares (2330.TW)", [], "76.62", True),
    # Santander Brasil and Petrobras state their conversion into dollars.
    ("UNIT - SANB11", ["Equivalent to R$29.09 per share. Amounts presented were converted into U.S. dollars at the "
                       "exchange rate of R$ 5.1047 per U.S.$1.00."], "5.70", False),
    ("PETR4", ["The price reported in column 4 is a convenience conversion of the purchase price of 48.24 BRL per "
               "share into U.S. dollars based on the Central Bank of Brazil exchange rate."], "9.49", False),
    # Naming the currency is price_in_foreign_currency's case, not this one.
    ("COMMON STOCK (BVMF: CSAN3)", ["Brazilian reais."], "3.90", False),
    # A dollar amount equal to the price settles it.
    ("Preference shares - BBDC4", ["The price is US$3.05 per share."], "3.05", False),
    # US titles, grants at no price.
    ("Common Stock", [], "17.98", False),
    ("Class A Common Stock, par value $0.0001", [], "17.98", False),
    ("Preference shares - BBDC4", [], "0", False),
])
def test_a_foreign_listing_with_no_currency_stated(title: str, notes: list[str], price: str, unstated: bool) -> None:
    assert currency_unstated(title, notes, D(price)) is unstated


def test_an_unstated_currency_is_left_out_of_dollar_totals() -> None:
    assert row_issues(traded_on=None, filed_on=None, shares=D("309111"), price=D("17.98"), value=D("5557815.78"),
                      unstated_currency=True) == [VALUE_IMPLAUSIBLE, CURRENCY_UNSTATED]
    # A named foreign currency is the more specific finding.
    assert row_issues(traded_on=None, filed_on=None, shares=D("1"), price=D("3.9"), value=D("3.9"),
                      foreign_currency=True, unstated_currency=True) == [VALUE_IMPLAUSIBLE, PRICE_NOT_USD]


# ---------------------------------------------------------------- ADS checks over a company's rows (AdsBook)
def _row(key: str, day: date, price: str) -> _ClassPrice:
    """One open-market row of a share class, priced per share as far as its notes say."""
    return _ClassPrice(key=key, traded_on=day, price=D(price), per_share=D(price), plain=True)


def _alibaba_book() -> AdsBook:
    """Alibaba's current open-market 'Ordinary Shares' rows (issuer 45) as stored, and its filings'
    ratio ('Each ADS represents 8 ordinary shares', stated in March and June 2026)."""
    rows = [("250860", date(2026, 3, 25), "16.06"), ("250873", date(2026, 3, 25), "16.07"),
            ("250885", date(2026, 3, 25), "16.10"), ("42856", date(2026, 6, 25), "12.10"),
            ("42848", date(2026, 6, 25), "12.15"), ("42840", date(2026, 6, 25), "12.10"),
            ("42832", date(2026, 6, 25), "12.09"), ("330864", date(2026, 6, 29), "94.92"),
            ("330865", date(2026, 6, 29), "95.59"), ("283021", date(2026, 8, 24), "14.24"),
            ("283117", date(2026, 8, 24), "14.29"), ("281482", date(2026, 8, 25), "14.47"),
            ("332966", date(2026, 9, 25), "13.58")]
    return AdsBook(ratios={45: [(date(2026, 3, 18), 8), (date(2026, 6, 26), 8)]},
                   prices={(45, "ordinaryshares"): [_row(*r) for r in rows]})


def _judge(book: AdsBook, key: str, day: date, price: str, *, issuer: int = 45, title: str = "Ordinary Shares",
           row_notes: list[str] | None = None, filing_notes: list[str] | None = None) -> bool:
    """ads_row_flag for one non-derivative row of `title`."""
    class_key = "".join(ch for ch in title.lower() if ch.isalpha())
    return ads_row_flag(book, issuer_entity_id=issuer, class_key=class_key, dedupe_key=key, traded_on=day,
                        security_title=title, derivative=False, row_notes=row_notes or [],
                        filing_notes=filing_notes or [], price=D(price))


def test_an_ads_price_on_ordinary_shares_is_found_from_the_companys_other_filings() -> None:
    """Alibaba 330864: 692,992 'Ordinary Shares' at $94.92, the price of one ADS (8 shares); the
    row's notes only say the prices were 'denominated in US dollars'. The same class sold at
    $12.09-16.10 around it: flagged (it counted $65.8M, about eight times the $8.2M it was worth).
    42848, sold in Hong Kong at $12.15 a share four days earlier, counts."""
    book = _alibaba_book()
    us_dollars = ("The price reported in Column 4 is a weighted average price. These shares were sold in multiple "
                  "transactions at prices denominated in US dollars ranging from 94.455 to 95.45 inclusive.")
    assert _judge(book, "330864", date(2026, 6, 29), "94.92", row_notes=[us_dollars])
    assert _judge(book, "330865", date(2026, 6, 29), "95.59")
    assert not _judge(book, "42848", date(2026, 6, 25), "12.15")
    # The rows the check finds per ADS don't set the per-share yardstick: the median of the other
    # eleven is $14.24 (with them it would be $14.29).
    assert book.reference(45, "ordinaryshares", date(2026, 6, 25)) == D("14.24")


def test_the_issuer_level_check_needs_a_ratio_above_one_and_a_yardstick() -> None:
    book = _alibaba_book()
    # No ratio stated within a year of the trade: nothing to go on.
    assert not _judge(book, "x", date(2028, 1, 5), "94.92")
    # One ADS is one share: an ADS price is a share price.
    assert not _judge(AdsBook(ratios={45: [(date(2026, 6, 26), 1)]}, prices=book.prices),
                      "330864", date(2026, 6, 29), "94.92")
    # No other rows of the class: no yardstick, so the row isn't judged (it stays counted).
    assert not _judge(AdsBook(ratios=book.ratios), "330864", date(2026, 6, 29), "94.92")
    # A note giving the row's own price per ordinary share settles it.
    assert not _judge(book, "330864", date(2026, 6, 29), "94.92", row_notes=["Sold at $94.92 per ordinary share."])


def test_sibanye_ordinary_shares_bought_at_the_ads_price_are_flagged() -> None:
    """Sibanye 250923 ($13.34) and 250925 ($11.63): 'Ordinary Shares' with no notes, bought at about
    the price of its ADSs (four shares each; 250947 bought ADSs at $11.43) while the class's other
    purchases were $2.19-2.91. 250926 at $2.70 counts."""
    rows = [("250923", date(2026, 3, 16), "13.34"), ("250925", date(2026, 3, 20), "11.63"),
            ("250926", date(2026, 3, 23), "2.70"), ("108357", date(2026, 3, 31), "2.91"),
            ("108358", date(2026, 6, 26), "2.24"), ("331829", date(2026, 6, 30), "2.19")]
    book = AdsBook(ratios={1113: [(date(2026, 3, 26), 4)]},
                   prices={(1113, "ordinaryshares"): [_row(*r) for r in rows]})
    assert _judge(book, "250925", date(2026, 3, 20), "11.63", issuer=1113)
    assert _judge(book, "250923", date(2026, 3, 16), "13.34", issuer=1113)
    assert not _judge(book, "250926", date(2026, 3, 23), "2.70", issuer=1113)


def test_a_ratio_only_row_uses_the_nearest_yardstick_within_a_year() -> None:
    """Kanzhun 137352 (March 18, $6.98, 'Each ADS represents two Class A ordinary shares'): the
    only row setting the per-share yardstick (253440, $7.25) is 187 days later. It counts; with no
    yardstick within a year it would still be left out."""
    held = ('These Class A ordinary shares are held through American Depositary Shares ("ADS") of the issuer. '
            "Each ADS represents two Class A ordinary shares.")
    title = "Class A ordinary shares"
    key = (1258, "classaordinaryshares")
    book = AdsBook(ratios={1258: [(date(2026, 3, 20), 2)]}, prices={key: [_row("253440", date(2026, 9, 21), "7.2484")]})
    assert not _judge(book, "137352", date(2026, 3, 18), "6.98", issuer=1258, title=title,
                      row_notes=[held], filing_notes=[held])
    assert _judge(book, "137352", date(2026, 3, 18), "14.10", issuer=1258, title=title,
                  row_notes=[held], filing_notes=[held])
    far = AdsBook(ratios=book.ratios, prices={key: [_row("x", date(2027, 6, 1), "7.25")]})
    assert _judge(far, "137352", date(2026, 3, 18), "6.98", issuer=1258, title=title,
                  row_notes=[held], filing_notes=[held])


@pytest.mark.parametrize(("title", "ads"), [
    ("American Depositary Shares", True),
    ("ADS, each representing one Class A ordinary share", True),
    ("American Depositary Shares, each representing two Class A Ordinary Shares", True),
    ("ADRs", True),
    ("Ordinary Shares (represented by American depositary shares)", False),
    ("Series A shares of common stock", False),
    ("Class B Shares", False),
    ("Ordinary Shares", False),
])
def test_ads_titles(title: str, ads: bool) -> None:
    assert is_ads_title(title) is ads
    assert ads_eligible_title(title) is not ads


def test_any_share_class_priced_per_ads_is_flagged() -> None:
    """Volaris 101909: 'Series A shares of common stock', 50,000 at $7.33, 'a weighted average price
    per ADS sold' (one ADS is 10 CPOs, each one Series A share): counted $366,500, about ten times
    the value. Telecom Argentina 21534: 38,890 'Class B Shares' at $13.43, 'the price of each ADR'."""
    volaris = ['Shares owned through American Depositary Shares ("ADSs"). Each ADS representing ten (10) '
               "Certificados de Participacion Ordinarios (CPOs). Each CPO representing the economic interest in one "
               "(1) share of Series A shares of common stock.",
               "The price reported in column 4 is a weighted average price per ADS sold. The ADSs were sold in "
               "multiple transactions at prices ranging from $7.32 to $7.34."]
    assert ads_ratio(volaris) == 10
    assert ads_mismatch("Series A shares of common stock", volaris, volaris, D("7.33"))
    telecom = ['The shares purchased were in the form of American Depository Receipts ("ADRs"). The reported price '
               "is the price of each ADR purchased.",
               "The securities reported herein include 384,890 Class B shares held in the form of 76,978 ADRs. "
               "Each ADR represents five Class B shares."]
    assert ads_ratio(telecom) == 5
    assert ads_mismatch("Class B Shares", telecom, telecom, D("13.43"))
    # The ADSs themselves, counted and priced per ADS, are right as filed.
    assert not ads_mismatch("American Depositary Shares", volaris, volaris, D("7.33"))


# ---------------------------------------------------------------- open-market rows with no size
def test_an_open_market_trade_of_zero_shares_is_flagged() -> None:
    """Aetos' funds: LLC interests that 'are not unitized', filed as a purchase of 0 at $7,333.34
    (the dollar amount; tx 274428). A grant of 0 shares or a sale at 0 is not this case."""
    assert row_issues(traded_on=None, filed_on=None, shares=D("0"), price=D("7333.34"), value=None,
                      open_market=True) == [VALUE_IMPLAUSIBLE]
    assert row_issues(traded_on=None, filed_on=None, shares=D("0"), price=D("7333.34"), value=None) == []
    assert row_issues(traded_on=None, filed_on=None, shares=D("24555"), price=D("0"), value=None,
                      open_market=True) == []


# ---------------------------------------------------------------- America Movil (round-6 review)
# The real notes of tx 280797-280799 (0001140361-26-034350): 'Series B Shares' at $1.05-1.15, held as
# ADRs of 20 shares. F2 mentions the price per ADR only to say this filing's price was adjusted to a
# price per Series B Share, so the rows are priced per share and must count.
AMX_F1 = "The securities reported herein are held in the form of 23,200 ADRs. Each ADR represents 20 Series B Shares."
AMX_F2 = ('The price reported on the Form 3 filed by the reporting person on March 18, 2026 (the "Form 3") was the '
          'exercise price per American Depository Receipt ("ADR"). The price reported herein has been adjusted and '
          "represents the exercise price per Series B Share.")
AMX_F3 = ("These securities are held by Inmobiliaria Evian-Chamonix S.A. de C.V, a sociedad anonima de capital "
          "variable organized under the laws of Mexico.")


def test_a_price_adjusted_to_a_per_share_price_counts() -> None:
    notes = [AMX_F1, AMX_F2, AMX_F3]
    assert ads_price_basis("Series B Shares", notes, notes, D("1.05")) == PER_ORDINARY
    assert not ads_mismatch("Series B Shares", notes, notes, D("1.05"))


def test_a_price_per_adr_without_the_adjustment_is_per_ads() -> None:
    per_adr = 'The price reported herein is the exercise price per American Depository Receipt ("ADR").'
    notes = [AMX_F1, per_adr]
    assert ads_price_basis("Series B Shares", notes, notes, D("21.00")) == BASIS_PER_ADS
    assert ads_mismatch("Series B Shares", notes, notes, D("21.00"))
