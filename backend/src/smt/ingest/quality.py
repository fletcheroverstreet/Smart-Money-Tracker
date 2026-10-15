"""Data-quality flags for values that cannot be right as filed.

Filers make typing mistakes, and EDGAR accepts them. Two kinds show up often
enough to distort rankings and totals (seen in real 2026 filings):

* **date_after_filing**: the trade date is later than the date the filing was
  made, e.g. a 2029 trade date on a Form 4 filed in March 2026. A filing can
  only report trades that already happened.
* **value_implausible**: the price field holds something other than a price
  per share. Typical cases: the total dollar amount typed as the price
  (100,149,060 shares at "$24,035,774.40" = $2.4 quadrillion), or the share
  count typed as the price (4,000,000 shares at "$4,000,000").

House rule: never change what a filing says. Flagged rows keep their numbers
exactly as filed and stay visible with a plain note ("check the filing"), but
the flagged figure is left out of anything that ranks or adds up rows:
largest trades, dollar totals, "last trade" dates, delay and late-filing
figures.

Two layers:

* `row_issues` runs at load time on one row with absolute rules (including a
  price footnote that names a foreign currency: **price_not_usd**, and a
  listing outside the US with no currency stated: **currency_unstated**; both
  also value_implausible, because the figure can't be taken as dollars).
* `flag_relative_prices` and `flag_totals_as_price` run as SQL over many rows
  (after each load for that company, and every 30 minutes over everything) and
  flag a price wildly out of line with the company's other prices, with its
  Form 144 notices, or growing in step with the share count (a total typed as
  the price on every row). `flag_ads` (a price per ADS on a count of
  underlying shares) runs the same way, after each load for that company.

The 30-minute job recomputes value flags from scratch, so a rule that is
corrected also clears the flags it set wrongly: it works them out on a
temporary copy of the table (reading only), then writes just the rows that
change in a short transaction.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from smt.db.session import session_scope
from smt.worker.scheduler import JobContext

DATE_AFTER_FILING = "date_after_filing"
VALUE_IMPLAUSIBLE = "value_implausible"
#: A 4/A row that couldn't be paired with the row it corrects: shown, but kept out of
#: totals, because the original and the correction may describe the same trade.
AMENDMENT_UNPAIRED = "amendment_unpaired"
#: A price footnote says the price is in a foreign currency (New Taiwan dollars, pesos...): the
#: row keeps the figure as filed, but it isn't a dollar amount, so it is also value_implausible.
PRICE_NOT_USD = "price_not_usd"
#: The security is a listing outside the US (its title names the local code, "Preference shares
#: - BBDC4") and nothing on the row says what currency the price is in. Bank Bradesco reports
#: its B3 price in reais with no note; BrasilAgro's AGRO3 rows look like dollars. Either way
#: the figure can't be taken as dollars, so it is also value_implausible.
CURRENCY_UNSTATED = "currency_unstated"

#: No US-listed share trades anywhere near this price; above it the field is not a per-share price.
MAX_PLAUSIBLE_PRICE = Decimal(1_000_000)
#: Larger than any single insider trade on record; above it the value is a typing error.
MAX_PLAUSIBLE_VALUE = Decimal(10_000_000_000)
#: Shares and price both this large and equal: the share count was typed into the price field.
_TRANSPOSED_MIN = Decimal(1_000)
#: A footnote saying the price is per ADS ("The price reported is the price per American
#: Depositary Share"), and one saying an ADS is exactly one share (then nothing is off).
ADS_PRICE_PATTERN = r"price[^.]{0,80}(per|each|of one|for one)[^.]{0,20}(american depositary|ADS\M)"
ADS_ONE_TO_ONE_PATTERN = r"represents? (one|1) (class [a-z] )?ordinary share"
#: Two filings reporting the same trade are at most this many days apart.
DUPLICATE_FILING_WINDOW_DAYS = 3
#: A price this many times the company's typical price in the same period is flagged.
RELATIVE_PRICE_FACTOR = 50
#: An option or other derivative can't cost more than about the share itself; above twice the
#: company's typical share price, the "price" is something else (often the total premium).
DERIVATIVE_PRICE_FACTOR = 2
#: A derivative trade worth more than this many times its underlying shares is doubtful.
UNDERLYING_VALUE_FACTOR = 10
#: Exercise prices below this are nominal (pre-funded warrants at $0.001) and set no yardstick.
_NOMINAL_EXERCISE_PRICE = Decimal("0.05")
_RELATIVE_MIN_ROWS = 4


#: Number words as filers spell out ADS ratios. Compounds are built from these: "thirty-five"
#: (ZKH, Quoin), "one hundred" (Huize, SOLAI), "four thousand" (Steakholder).
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
                 "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
                 "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALE_WORDS = {"hundred": 100, "thousand": 1000}
#: American Depositary Shares and Receipts, as filers write them (Kanzhun "ADS", Volaris "ADSs",
#: "American Depository Shares" misspelled, "ADRs").
_ADS_TERM = r"american\s+deposit[oa]ry|\bADSs?\b|\bADRs?\b|\bdeposit[oa]ry\s+receipts?"
_ADS_MENTION = re.compile(_ADS_TERM, re.IGNORECASE)
#: The same for Postgres (the pre-filter that finds filings whose notes could matter).
ADS_HINT_SQL = _ADS_TERM.replace(r"\b", r"\y")
_ADS_PRICE = re.compile(r"price[^.]{0,80}?(per|each|of one|for one|of|of the)\s+(american\s+deposit[oa]ry|ADSs?\b|ADRs?\b)",
                        re.IGNORECASE)
#: A sentence about another filing: America Movil's Form 4 notes that "The price reported on the
#: Form 3 ... was the exercise price per American Depository Receipt", while its own price ($1.05
#: a Series B share, 20 to the ADR) is per share.
_OTHER_FILING = re.compile(r"\b(?:on|in)\s+(?:the|a|its|that)\s+(?:original\s+|prior\s+|previous\s+|earlier\s+)?"
                           r"Form\s+[345]\b|\bpreviously\s+reported|\boriginally\s+reported", re.IGNORECASE)
#: A number spelt out: "thirty-five", "one hundred", "four thousand", "two hundred and fifty".
_WORDS_NUMBER = (r"(?:{w})(?:[\s-]+(?:and\s+)?(?:{w}))*\b"
                 .format(w="|".join(rf"\b{w}\b" for w in [*_NUMBER_WORDS, *_SCALE_WORDS])))
#: A count: digits ("43,200", "0.05") or words, with the digits in brackets when the filer adds them
#: ("one hundred (100)", "four thousand (4,000)", "two(2)"). The count must end there: "one-half"
#: and "1/20" are not counts this reads.
_DIGITS = r"\d[\d,]*(?:\.\d+)?"
_COUNT = rf"(?:(?P<{{n}}_digits>{_DIGITS})|(?P<{{n}}_words>{_WORDS_NUMBER}))(?:\s*\(\s*(?P<{{n}}_bracket>{_DIGITS})\s*\))?"
_ADS_NOUN = r"(?:ADSs?|ADRs?|American\s+Deposit[oa]ry\s+(?:Shares?|Receipts?))"
_ADS_RATIO = re.compile(
    # How many ADSs: "Each ADS", "Each of American Depositary Share" (Baozun), "One (1) American
    # Depositary Share" (trivago), "Each 10 ADSs represent 23 Ordinary Shares" (NovaBridge),
    # "Every three ADSs represent two" (Gaotu), "two American depositary shares representing three"
    # (Dingdong). A count in digits needs "each" or "every": "sold 46,947 ADSs representing
    # approximately 107,978 ordinary shares" (NovaBridge) is a holding, not the ratio.
    r"(?:\b(?:each|every)(?:\s+of)?(?:\s+" + _COUNT.format(n="ads") + r")?"
    r"|(?<![\d,.])\b(?P<ads_bare>" + _WORDS_NUMBER + r"|1)(?:\s*\(\s*\d+\s*\))?)"
    r"\s+" + _ADS_NOUN + r"\s*(?:\([^)]*\)\s*)?"
    # "Each ADS representing ten (10) CPOs" (Volaris)
    r"(?:represents?|representing|is\s+equal\s+to|equals?|corresponds?\s+to|evidences?)\s+"
    # "represents a beneficial interest in 10 ordinary shares", "the right to receive five shares"
    r"(?:(?:an?\s+)?(?:beneficial\s+)?(?:ownership\s+)?interest\s+in\s+|the\s+right\s+to\s+receive\s+)?"
    + _COUNT.format(n="shares") + r"(?![\w/-]|\.\d)",
    re.IGNORECASE)
#: "changed the ratio ... from one (1) ADS representing three (3) Class A ordinary shares to one (1)
#: ADS representing thirty (30)" (Tuniu, April 2026): the ratio after "from" is the old one.
_OLD_RATIO = re.compile(r"\bfrom\s*$", re.IGNORECASE)


def _says_price_per_ads(note: str) -> bool:
    """The note says the row's price is per ADS ("The price reported is the weighted average price
    per American Depositary Share"), in a sentence that isn't about another filing (_OTHER_FILING)."""
    for match in _ADS_PRICE.finditer(note):
        start = max(note.rfind(".", 0, match.start()), note.rfind(";", 0, match.start())) + 1
        end = note.find(".", match.end())
        if not _OTHER_FILING.search(note[start:end if end >= 0 else len(note)]):
            return True
    return False


def _words_value(words: str) -> int:
    """The value of a number in words: "thirty-five" 35, "one hundred" 100, "four thousand" 4000,
    "two hundred and fifty" 250."""
    total, current = 0, 0
    for word in re.split(r"[\s-]+", words.lower()):
        if word in _NUMBER_WORDS:
            current += _NUMBER_WORDS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word == "thousand":
            total, current = total + (current or 1) * 1000, 0
    return total + current


def _count(match: re.Match[str], name: str) -> Decimal | None:
    """One count of an _ADS_RATIO match: the digits in brackets when given ("one hundred (100)"),
    else the digits or the words as written. None when the count wasn't written."""
    digits = match.group(f"{name}_bracket") or match.group(f"{name}_digits")
    if digits:
        return Decimal(digits.replace(",", ""))
    words = match.group(f"{name}_words")
    return Decimal(_words_value(words)) if words else None


def ads_ratio(texts: list[str]) -> Decimal | None:
    """Ordinary shares per ADS stated in the filing's footnotes, if any.

    "Each ADS represents sixty Class A ordinary shares" is 60, "one hundred (100)" 100 (Huize),
    "four thousand (4,000)" 4,000 (Steakholder), "thirty-five" 35 (ZKH). A ratio can be a
    fraction: "Each 10 ADSs represent 23 Ordinary Shares" is 2.3 (NovaBridge), "Every three ADSs
    represent two" two thirds (Gaotu), "represents 0.05 Common Share" 0.05 (DoubleDown). After a
    ratio change the new ratio counts (Tuniu: "from one (1) ADS representing three (3) ... to one
    (1) ADS representing thirty (30)" is 30). None when no filing note states one.
    """
    for text_ in texts:
        for match in _ADS_RATIO.finditer(text_):
            if _OLD_RATIO.search(text_, 0, match.start()):
                continue
            bare = match.group("ads_bare")  # "two American depositary shares", "One (1) ADS"
            if bare:
                ads = Decimal(1) if bare == "1" else Decimal(_words_value(bare))
            else:  # "Each ADS" is one; "Each 10 ADSs" ten
                ads = _count(match, "ads") or Decimal(1)
            shares = _count(match, "shares")
            if shares is None or shares <= 0 or ads <= 0:
                continue
            return shares / ads
    return None


#: "$23.8512 per ordinary share", "equivalent to $14.80 per Ordinary Share".
_PER_ORDINARY_PRICE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s+per\s+(?:class\s+[a-z]\s+)?ordinary\s+share",
                                 re.IGNORECASE)


#: "$8.905 to $9.325 per ordinary share" (a range of prices per ordinary share).
_PER_ORDINARY_RANGE = re.compile(
    r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:to|-|–)\s*\$?\s?([\d,]+(?:\.\d+)?)[^.;]{0,40}?"
    r"per\s+(?:underlying\s+)?(?:class\s+[a-z]\s+)?ordinary\s+share", re.IGNORECASE)
#: "adjusted to show price per underlying ordinary share": the filer already converted.
#: "Reflects price per share in Ordinary Shares" says the same.
_ADJUSTED_TO_ORDINARY = re.compile(
    r"(?:adjusted|converted|restated|shown)\s+(?:to\s+(?:show|reflect)\s+)?(?:the\s+)?(?:price|prices)\s+per\s+"
    r"(?:underlying\s+)?(?:class\s+[a-z]\s+)?ordinary\s+share"
    r"|(?:price|prices)\s+per\s+share\s+(?:in|of)\s+(?:the\s+)?(?:underlying\s+)?(?:class\s+[a-z]\s+)?"
    r"ordinary\s+shares?"
    # America Movil: "The price reported herein has been adjusted and represents the exercise price
    # per Series B Share" (20 Series B shares to the ADR).
    r"|\b(?:represents|reflects)\s+the\s+(?:weighted\s+average\s+)?(?:exercise\s+|sale\s+|purchase\s+)?price\s+per\s+"
    r"(?:(?:series|class)\s+[a-z]\s+)?(?:underlying\s+)?(?:ordinary\s+|common\s+)?share\b", re.IGNORECASE)

def states_price_per_share(row_notes: list[str], price: Decimal | None) -> bool:
    """A note on the row says its price is per ordinary share: it was adjusted to one (Mesoblast:
    "Purchase prices of ADS have been adjusted to show price per underlying ordinary share"), or
    the note gives the row's own price per ordinary share, alone (BeOne "$23.8512 per ordinary
    share") or as a range (Legend "$8.905 to $9.325 per ordinary share", price 9.14)."""
    for note in row_notes:
        if _ADJUSTED_TO_ORDINARY.search(note):
            return True
    if price is None or price <= 0:
        return False
    for note in row_notes:
        for match in _PER_ORDINARY_PRICE.finditer(note):
            stated = Decimal(match.group(1).replace(",", ""))
            if abs(stated - price) <= price * Decimal("0.02"):
                return True
        for match in _PER_ORDINARY_RANGE.finditer(note):
            try:
                low, high = (Decimal(g.replace(",", "")) for g in match.groups())
            except ArithmeticError:
                continue
            if low * Decimal("0.98") <= price <= high * Decimal("1.02"):
                return True
    return False


#: What an ordinary-share row's footnotes say about its price (ads_price_basis).
PER_ORDINARY = "ordinary"   # the notes show the price is per ordinary share
PER_ADS = "ads"             # a note says the price is per ADS
RATIO_ONLY = "ratio"        # only "each ADS represents N shares": either is possible


def is_ads_title(security_title: str | None) -> bool:
    """The security is the depositary share itself ("American Depositary Shares, each representing
    two Class A Ordinary Shares", "ADS, each representing one Class A ordinary share"): a row counted
    in ADSs and priced per ADS is right as filed. "Ordinary Shares (represented by American
    depositary shares)" is counted in ordinary shares: the ADS words come after the class."""
    if not security_title:
        return False
    ads = _ADS_MENTION.search(security_title)
    if not ads:
        return False
    underlying = re.search(r"ordinary|common|\bshares?\s+of\b|\bseries\b", security_title, re.IGNORECASE)
    return underlying is None or ads.start() < underlying.start()


def ads_eligible_title(security_title: str | None, *, derivative: bool = False) -> bool:
    """Rows whose price could be an ADS price on a count of underlying shares.

    Any share class counts, not only titles with "ordinary": Volaris files "Series A shares of
    common stock" priced per ADS (one ADS is 10 CPOs) and says so in a note. Titles that are ADSs
    themselves don't (their count and price are both per ADS). Options and other derivatives only
    when the title names ordinary shares (as before: "Share Option (right to buy ordinary shares)").
    """
    if not security_title or is_ads_title(security_title):
        return False
    return "ordinary" in security_title.lower() or not derivative


def ads_price_basis(security_title: str | None, row_notes: list[str], filing_notes: list[str],
                    price: Decimal | None = None, *, derivative: bool = False) -> str | None:
    """Whether a row in underlying shares held through ADSs is priced per share or per ADS.

    None when there's no question to answer: the title isn't eligible (ads_eligible_title), or
    its footnotes don't mention ADSs. PER_ORDINARY when a note says the price was adjusted to
    (or is) per ordinary share, gives the row's own price per ordinary share (BeOne: "a price
    of $23.8512 per ordinary share or $310.0659 per ADS" with the price field $23.85), states
    the ADS price that the ratio converts to the row's price (_ads_price_converted, NovaBridge),
    or the filing says one ADS is one share. PER_ADS when a note says the price is per ADS.
    RATIO_ONLY when the filing only states a ratio above 1 ("Each ADS represents two Class A
    ordinary shares"): Kanzhun prices such rows per share, 51Talk per ADS. A ratio below 1
    with nothing else said is PER_ORDINARY (see below).
    """
    if not ads_eligible_title(security_title, derivative=derivative):
        return None
    if not any(_ADS_MENTION.search(n) for n in row_notes):
        return None
    if states_price_per_share(row_notes, price):
        return PER_ORDINARY
    ratio = ads_ratio(filing_notes)
    if ratio == 1:
        return PER_ORDINARY
    if ratio is not None and ratio > 1 and price is not None and price > 0 \
            and _ads_price_converted(row_notes, price, ratio):
        return PER_ORDINARY
    if any(_says_price_per_ads(n) for n in row_notes):
        return PER_ADS
    if ratio is None:
        return None
    # A ratio below 1 (DoubleDown: "Each American Depositary Share represents 0.05 Common Share")
    # means one share is several ADSs: a price per ADS on a count of shares would understate the
    # value, never overstate it, so only a note saying the price is per ADS (above) flags the row.
    return RATIO_ONLY if ratio > 1 else PER_ORDINARY


def _ads_price_converted(row_notes: list[str], price: Decimal, ratio: Decimal) -> bool:
    """A note states the price of the ADSs, and that price divided by the ratio is the row's
    price: the filer converted it to a price per share.

    NovaBridge 263718: 'Ordinary Shares' sold at $0.74 with "Reported sales price represents the
    converted sales price of the ADSs of $1.6908" and "Each 10 ADSs represent 23 Ordinary
    Shares": $1.6908 / 2.3 = $0.7351, so the row is priced per share: 107,978 shares (46,947
    ADSs) for $79,903.72, counted as filed, not left out as a price per ADS. An amount equal to
    the row's own price says the opposite (the price is per ADS), so it never counts here; nor
    does one that doesn't divide back to it (108309: $3.94 beside "$1.71", which is $1.71 x 2.3,
    stays flagged)."""
    tolerance = price * Decimal("0.02")
    for note in row_notes:
        for sentence in re.split(r"(?<=[.;])\s+", note):
            if not _ADS_PRICE.search(sentence):
                continue
            for match in _USD_AMOUNT.finditer(sentence):
                try:
                    stated = Decimal(match.group(1).replace(",", ""))
                except ArithmeticError:
                    continue
                if abs(stated - price) > tolerance and abs(stated / ratio - price) <= tolerance:
                    return True
    return False


def ads_mismatch(security_title: str | None, row_notes: list[str], filing_notes: list[str],
                 price: Decimal | None = None, per_share_reference: Decimal | None = None, *,
                 derivative: bool = False) -> bool:
    """The price is per American Depositary Share while the shares are ordinary shares.

    When one ADS stands for N ordinary shares, ordinary shares x the ADS price overstates the
    value N times (51Talk: 60 shares per ADS). With a ratio below 1 (several ADSs per share) it
    understates it instead; either way shares x price isn't the value, so the row is flagged.
    Flagged when a note says the price is per ADS
    (ads_price_basis). When the filing only states the ratio, the price is compared with
    `per_share_reference`, the same class's typical price per ordinary share
    (AdsBook.reference): a price nearer the per-ADS figure (reference x N) than the
    per-share one is flagged (Mesoblast: $14.10 against $1.43 a share, 10 shares per ADS);
    Kanzhun's $8.28 against $7.25 a share is not. With no reference the row is flagged: its
    price could overstate the value N times, and a figure that can't be checked stays out of totals.
    """
    if price is None or price <= 0:
        return False  # a grant or exercise at no price: nothing to overstate
    basis = ads_price_basis(security_title, row_notes, filing_notes, price, derivative=derivative)
    if basis == PER_ADS:
        return True
    if basis != RATIO_ONLY:
        return False
    ratio = ads_ratio(filing_notes)
    if ratio is not None and ratio <= 1:
        return False  # can't overstate (ads_price_basis gives RATIO_ONLY only above 1)
    if per_share_reference is None or per_share_reference <= 0 or ratio is None:
        return True
    return bool(float(price) >= float(per_share_reference) * math.sqrt(ratio))


#: Rows within this many days of a trade set the per-share yardstick for an ADS ratio. When none
#: is that close, rows within _ADS_FALLBACK_DAYS do: Kanzhun's sales of March 18-20 (137352, $6.98)
#: were 187 days from the only row that sets it (253440, $7.25) and were left out of totals, while
#: the same sales 11 days later counted.
_ADS_REFERENCE_DAYS = 180
_ADS_FALLBACK_DAYS = 365
#: A filing this many days either side of a trade can state the issuer's ADS ratio.
_ISSUER_RATIO_DAYS = 365


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


@dataclass(frozen=True)
class _ClassPrice:
    """One current share row's price per underlying share, for the ADS yardsticks."""

    key: str             # the row's dedupe_key (so a row is never its own yardstick)
    traded_on: date
    price: Decimal       # as filed
    per_share: Decimal   # the price per underlying share (PER_ADS rows: price / ratio)
    plain: bool          # nothing on the row says whether the price is per share or per ADS


@dataclass
class AdsBook:
    """What the ADS checks know about a set of issuers: the ADS ratios their filings state
    ("Each ADS represents eight ordinary shares"), and the prices of their current share rows per
    underlying share, by share class. Built once per load or recompute (load_ads_book)."""

    ratios: dict[int, list[tuple[date, Decimal]]] = field(default_factory=dict)
    prices: dict[tuple[int, str], list[_ClassPrice]] = field(default_factory=dict)
    #: The issuer's own rows in ADSs (a count and a price per ADS): (dedupe_key, traded_on, price).
    ads_rows: dict[int, list[tuple[str, date, Decimal]]] = field(default_factory=dict)
    _outliers: dict[str, bool] = field(default_factory=dict)

    def issuer_ratio(self, issuer: int | None, day: date | None) -> Decimal | None:
        """Shares per ADS stated in the issuer's filing nearest `day`, within _ISSUER_RATIO_DAYS."""
        if issuer is None or day is None:
            return None
        best: tuple[int, Decimal] | None = None
        for filed_on, ratio in self.ratios.get(issuer, []):
            distance = abs((filed_on - day).days)
            if distance <= _ISSUER_RATIO_DAYS and (best is None or distance < best[0]):
                best = (distance, ratio)
        return best[1] if best else None

    def _near(self, issuer: int, class_key: str, day: date, exclude: str | None) -> list[_ClassPrice]:
        """The class's rows within _ADS_REFERENCE_DAYS of `day`, or else within _ADS_FALLBACK_DAYS.
        When the class has none, the issuer's trades in ADSs, each divided by the ratio its
        filings state (Studio City: ADSs bought at $0.77-0.85 in September, four shares each, beside
        Class A shares bought at $0.1977)."""
        rows = [r for r in self.prices.get((issuer, class_key), []) if r.key != exclude]
        if not rows:
            for key, traded_on, price in self.ads_rows.get(issuer, []):
                ratio = self.issuer_ratio(issuer, traded_on)
                if ratio and key != exclude:
                    rows.append(_ClassPrice(key=key, traded_on=traded_on, price=price, per_share=price / ratio,
                                            plain=False))
        close = [r for r in rows if abs((r.traded_on - day).days) <= _ADS_REFERENCE_DAYS]
        return close or [r for r in rows if abs((r.traded_on - day).days) <= _ADS_FALLBACK_DAYS]

    def issuer_level_outlier(self, issuer: int | None, class_key: str, day: date | None,
                             key: str | None, price: Decimal) -> bool:
        """A row whose notes say nothing about ADSs, at an issuer whose filings state N shares per
        ADS (N > 1), priced at sqrt(N) times the class's median price per share or more (the
        row itself left out): nearer the per-ADS figure than the per-share one.

        Alibaba 330864: 692,992 "Ordinary Shares" at $94.92, the price of one ADS (8 shares),
        while the same class sold at $12.09-16.10 within the window: counted as $65.8M, about
        eight times the $8.2M the shares were worth. Sibanye 250925: $11.63 (one ADS is 4
        shares) against $2.19-2.91."""
        if issuer is None or day is None or price <= 0:
            return False
        ratio = self.issuer_ratio(issuer, day)
        if ratio is None or ratio <= 1:
            return False
        reference = _median([r.per_share for r in self._near(issuer, class_key, day, key)])
        return reference is not None and reference > 0 and float(price) >= float(reference) * math.sqrt(ratio)

    def _is_outlier(self, issuer: int, class_key: str, row: _ClassPrice) -> bool:
        if row.key not in self._outliers:
            self._outliers[row.key] = row.plain and self.issuer_level_outlier(
                issuer, class_key, row.traded_on, row.key, row.price)
        return self._outliers[row.key]

    def reference(self, issuer: int | None, class_key: str, day: date | None,
                  exclude: str | None = None) -> Decimal | None:
        """The class's median price per underlying share near `day` (see _near), from rows whose
        basis is known: no ADS in their notes, a note that says per ordinary share, or one that
        says per ADS (their price divided by the stated ratio). Rows with only a ratio, a price in
        another currency, or a price the issuer-level check finds to be per ADS (Alibaba 330864)
        don't count. None when there are none."""
        if issuer is None or day is None:
            return None
        rows = [r for r in self._near(issuer, class_key, day, exclude)
                if not self._is_outlier(issuer, class_key, r)]
        return _median([r.per_share for r in rows])


async def load_ads_book(session: AsyncSession, issuers: list[int] | None = None) -> AdsBook:
    """Read the ADS ratios stated in filings whose notes mention ADSs, and the current open-market
    share rows (P and S, which happen at market prices) of those issuers (all of them, or only
    `issuers`). Other codes can carry nominal prices: Zhihu's RSU rows at $0.01 a share would
    make its $2.85 sale look like an ADS price."""
    only = "AND f.issuer_entity_id = ANY(:issuers)" if issuers is not None else ""
    book = AdsBook()
    filings = (await session.execute(text(f"""
        SELECT f.issuer_entity_id, f.filed_on, f.footnotes FROM filings f
         WHERE f.issuer_entity_id IS NOT NULL AND f.filed_on IS NOT NULL
           AND f.footnotes::text ~* :hint {only}
    """), {"hint": ADS_HINT_SQL, "issuers": issuers})).all()
    mentioned: set[int] = set()
    for f in filings:
        mentioned.add(int(f.issuer_entity_id))
        ratio = ads_ratio([str(v) for v in (f.footnotes or {}).values()])
        if ratio is not None:
            book.ratios.setdefault(int(f.issuer_entity_id), []).append((f.filed_on, ratio))
    if not mentioned:
        return book
    rows = (await session.execute(text(f"""
        SELECT t.issuer_entity_id, t.dedupe_key, {_CLASS_KEY_SQL.format(col="t.security_title")} AS class_key,
               t.traded_on, t.security_title, t.price, t.footnote_ids, f.footnotes, t.is_derivative
          FROM transactions t JOIN filings f ON f.id = t.filing_id
         WHERE t.issuer_entity_id = ANY(:issuers) AND t.is_current
           AND (NOT t.is_derivative OR t.security_title ~* :hint)
           AND t.code IN ('P', 'S') AND t.price > 0 AND t.traded_on IS NOT NULL
    """), {"issuers": sorted(mentioned), "hint": ADS_HINT_SQL})).all()
    for r in rows:
        notes = dict(r.footnotes or {})
        row_notes = [str(notes[k]) for k in (r.footnote_ids or []) if k in notes]
        price = Decimal(r.price)
        if price > MAX_PLAUSIBLE_PRICE or price_in_foreign_currency(row_notes, price):
            continue
        if is_ads_title(r.security_title):
            # Counted and priced in ADSs (Studio City files its ADSs in Table II, as convertible).
            book.ads_rows.setdefault(int(r.issuer_entity_id), []).append((r.dedupe_key, r.traded_on, price))
            continue
        filing_notes = [str(v) for v in notes.values()]
        basis = ads_price_basis(r.security_title, row_notes, filing_notes, price)
        per_share: Decimal | None = None
        if basis in (None, PER_ORDINARY):
            per_share = price
        elif basis == PER_ADS and (ratio := ads_ratio(filing_notes)):
            per_share = price / ratio
        if per_share is not None:
            book.prices.setdefault((int(r.issuer_entity_id), r.class_key), []).append(_ClassPrice(
                key=r.dedupe_key, traded_on=r.traded_on, price=price, per_share=per_share, plain=basis is None))
    return book


def ads_row_flag(book: AdsBook, *, issuer_entity_id: int | None, class_key: str, dedupe_key: str | None,
                 traded_on: date | None, security_title: str | None, derivative: bool,
                 row_notes: list[str], filing_notes: list[str], price: Decimal | None) -> bool:
    """Whether one row's price is per ADS while its shares are underlying shares.

    Three ways to tell: a note says the price is per ADS (flagged); the filing states only
    the ratio (the price is judged against the class's price per share, ads_mismatch); or the
    row's notes say nothing about ADSs but the issuer's filings state a ratio above 1
    (AdsBook.issuer_level_outlier: Alibaba, Sibanye)."""
    if price is None or price <= 0:
        return False  # a grant or exercise at no price: nothing to overstate
    basis = ads_price_basis(security_title, row_notes, filing_notes, price, derivative=derivative)
    if basis == PER_ADS:
        return True
    if basis == RATIO_ONLY:
        reference = book.reference(issuer_entity_id, class_key, traded_on, exclude=dedupe_key)
        return ads_mismatch(security_title, row_notes, filing_notes, price, reference, derivative=derivative)
    if basis is None and not derivative and ads_eligible_title(security_title) \
            and not states_price_per_share(row_notes, price):
        return book.issuer_level_outlier(issuer_entity_id, class_key, traded_on, dedupe_key, price)
    return False


async def flag_ads(session: AsyncSession, *, filing_ids: list[int] | None = None,
                   table: str = "transactions") -> list[int]:
    """Add VALUE_IMPLAUSIBLE to rows whose price is per ADS on a count of underlying shares
    (ads_row_flag). Rows already flagged are skipped. At load time this runs after the filing's
    rows are stored, scoped to its issuer, so each row is judged against the same rows as in the
    30-minute recompute (which runs it over `table`, its work copy). Returns the ids flagged."""
    _check_table(table)
    issuers = None
    if filing_ids is not None:
        issuers = [int(i) for i in (await session.execute(text(
            "SELECT DISTINCT issuer_entity_id FROM filings WHERE id = ANY(:ids) AND issuer_entity_id IS NOT NULL"),
            {"ids": filing_ids})).scalars()]
    book = await load_ads_book(session, issuers)
    ratio_issuers = sorted(i for i, found in book.ratios.items() if any(n > 1 for _, n in found))
    scope = "AND t.issuer_entity_id = ANY(:issuers)" if issuers is not None else ""
    candidates = (await session.execute(text(f"""
        SELECT t.id, t.dedupe_key, t.issuer_entity_id, {_CLASS_KEY_SQL.format(col="t.security_title")} AS class_key,
               t.security_title, t.is_derivative, t.price, t.traded_on, t.footnote_ids, f.footnotes
          FROM {table} t JOIN filings f ON f.id = t.filing_id
         WHERE t.price > 0 AND NOT (:flag = ANY(t.data_issues)) {scope}
           AND ((cardinality(t.footnote_ids) > 0 AND f.footnotes::text ~* :hint)
                OR t.issuer_entity_id = ANY(:ratio_issuers))
    """), {"flag": VALUE_IMPLAUSIBLE, "issuers": issuers, "hint": ADS_HINT_SQL,
           "ratio_issuers": ratio_issuers})).all()
    flagged: list[int] = []
    for c in candidates:
        notes = dict(c.footnotes or {})
        if ads_row_flag(book, issuer_entity_id=c.issuer_entity_id, class_key=c.class_key, dedupe_key=c.dedupe_key,
                        traded_on=c.traded_on, security_title=c.security_title, derivative=bool(c.is_derivative),
                        row_notes=[str(notes[k]) for k in (c.footnote_ids or []) if k in notes],
                        filing_notes=[str(v) for v in notes.values()], price=Decimal(c.price)):
            flagged.append(int(c.id))
    if flagged:
        await session.execute(text(
            f"UPDATE {table} SET data_issues = array_append(data_issues, :flag) "
            "WHERE id = ANY(:ids) AND NOT (:flag = ANY(data_issues))"), {"flag": VALUE_IMPLAUSIBLE, "ids": flagged})
        if table == "transactions":
            await sync_feed_flags(session)
    return flagged


#: Currencies filers name in price footnotes. Only a sentence that also mentions the price counts
#: (a note that the shares trade in Hong Kong says nothing about the price field).
_CURRENCIES = re.compile(
    r"new\s+taiwan\s+dollars?|\bNT\$|\bNTD\b|\bTWD\b|argentin(?:e|ian)\s+pesos?|\bARS\b"
    r"|mexican\s+pesos?|\bMXN\b|\bMX\$|australian\s+dollars?|\bAUD\b|\bA\$|hong\s+kong\s+dollars?|\bHK\$"
    r"|\bHKD\b|canadian\s+dollars?|\bCAD\b|\bC\$|\bCDN\$|singapore\s+dollars?|\bSGD\b|new\s+zealand\s+dollars?"
    r"|\bNZD\b|japanese\s+yen|\byen\b|\bJPY\b|israeli\s+(?:new\s+)?shekels?|\bNIS\b|\bILS\b|swiss\s+francs?"
    r"|\bCHF\b|pounds?\s+sterling|british\s+pounds?|\bGBP\b|\bpence\b|\beuros?\b|\bEUR\b|renminbi"
    r"|chinese\s+yuan|\bRMB\b|\bCNY\b|korean\s+won|\bKRW\b|indian\s+rupees?|\bINR\b|brazilian\s+rea(?:l|is)"
    r"|\bBRL\b|\bR\$|swedish\s+kron(?:a|or)|\bSEK\b|norwegian\s+kron(?:e|er)|\bNOK\b|danish\s+kron(?:e|er)"
    r"|\bDKK\b|south\s+african\s+rand|\bZAR\b|[£€¥]",
    re.IGNORECASE)
#: The same pattern for Postgres (its word boundary is \y), so the 30-minute recompute's SQL
#: pre-filter selects every filing the Python check could flag: never a narrower list.
CURRENCY_HINT_SQL = _CURRENCIES.pattern.replace(r"\b", r"\y")
#: The price was converted into dollars (then the figure is in dollars after all).
_USD_WORD = r"(?:U\.?\s?S\.?\s?D?\.?|United\s+States)\s*(?:\$|dollars?\b|\b)"
_IN_USD = re.compile(
    # "converted from Australian Dollars into U.S. Dollars", "translated to US$", "converted to USD"
    r"(?:convert|translat)\w*(?:[^.;]|\.\d){0,80}?\b(?:in)?to\s+" + _USD_WORD
    # "reported in U.S. dollars", "in USD"
    + r"|\bin\s+" + _USD_WORD
    + r"|(?:U\.?\s?S\.?|United\s+States)\s*dollar\s+(?:equivalent|amount\s+reported)"
    # "a conversion rate of NIS 3.142 for each USD $1.00 was used", "3.038 ILS to $1.00",
    # "based on an exchange ratio of 1.1607 U.S. dollars per 1.00 euro" (Bending Spoons). The gap
    # may cross decimal points and the dots of "U.S.", never a sentence's end (not even after a
    # one-letter word: "... of Class A. The dollars ..." is two sentences).
    + r"|(?:conversion|exchange)\s+rat(?:e|io)(?:\bU\.\s?S\.|[^.;]|\.\d){0,80}?"
    + r"(?:USD|US\$|U\.S\.\s*\$|\bto\s+\$\s?1(?:\.00)?\b|dollars?\b)"
    + r"|\bto\s+(?:USD|US\$|U\.S\.\s*\$|\$)\s?1(?:\.00)?\b",
    re.IGNORECASE)


#: A footnote this short that names a currency is a label for the price's currency.
_BARE_CURRENCY_NOTE_MAX = 40
#: A dollar amount written in a note: "$83.31", "USD 1.17", "US$ 6.8", "USD $ 6.8".
_USD_AMOUNT = re.compile(r"(?:\bUSD\s*\$?|\bUS\$|\bU\.S\.\s?\$|(?<![A-Z])\$)\s?([\d,]+(?:\.\d+)?)")


def price_in_foreign_currency(row_notes: list[str], price: Decimal | None = None) -> bool:
    """True when a footnote on the row says its price is in a foreign currency.

    UMC reports NT$142.33 as its price (1,200,000 shares = NT$171M, about $5M), CEMEX
    Mexican pesos, IRSA Argentine pesos. Counted as dollars, those overstate totals up
    to 30 times. A sentence must mention the price and name a currency. Cleared when a
    note says the price was converted into U.S. dollars, or states a dollar amount equal
    to the row's own price (HDFC: "an exercise price in USD $ 6.8" for a price of 6.80).
    """
    if price is None or price <= 0:
        return False  # no price, no dollar figure to get wrong (grants, gifts)
    for note in row_notes:
        if _IN_USD.search(note):
            return False
        for sentence in re.split(r"(?<=[.;])\s+", note):
            # A bare "$" in a sentence that names another currency isn't dollars ("prices ranging
            # from $465 to $465.5 New Taiwan Dollars", "$17.20 MXN"); "USD"/"US$" always is.
            foreign_sentence = bool(_CURRENCIES.search(sentence))
            for match in _USD_AMOUNT.finditer(sentence):
                if foreign_sentence and match.group(0).lstrip().startswith("$"):
                    continue
                try:
                    stated = Decimal(match.group(1).replace(",", ""))
                except ArithmeticError:
                    continue
                if abs(stated - price) <= price * Decimal("0.02"):
                    return False
    for note in row_notes:
        # A note that is little more than a currency name ("New Taiwan Dollars", as UMC files it
        # on the price) can only be saying what the price is in.
        if len(note.strip()) <= _BARE_CURRENCY_NOTE_MAX and _CURRENCIES.search(note):
            return _price_is_the_foreign_figure(note, price)
        for sentence in re.split(r"(?<=[.;])\s+", note):
            if re.search(r"\bpric", sentence, re.IGNORECASE) and _CURRENCIES.search(sentence):
                return _price_is_the_foreign_figure(sentence, price)
    return False


#: A number written right next to a currency: "euro 24.50", "AUD$0.1550", "NT$142.33", "24.50 euros".
_NUMBER = r"(\d[\d,]*(?:\.\d+)?)"


def _price_is_the_foreign_figure(text_: str, price: Decimal) -> bool:
    """Given a note that names a foreign currency: does the row's price hold that currency's
    figure? When the note states amounts in that currency and none of them is the row's price,
    the price field holds something else, the dollar conversion (Star Bulk: "euro 24.50" beside
    a price of 28.27, which is $28.27). With no amount stated, the note labels the price."""
    amounts: list[Decimal] = []
    for match in _CURRENCIES.finditer(text_):
        after = re.match(r"\s*\$?\s*" + _NUMBER, text_[match.end():])
        before = re.search(_NUMBER + r"\s*$", text_[:match.start()])
        for found in (after, before):
            if found:
                try:
                    amounts.append(Decimal(found.group(1).replace(",", "")))
                except ArithmeticError:
                    continue
    if not amounts:
        return True
    return any(abs(a - price) <= price * Decimal("0.02") for a in amounts)


#: A security title naming a listing outside the US: a Brazilian B3 code ("BBDC4", "BBDC4F", "SANB11",
#: "BVMF: CSAN3"), an exchange prefix ("TWSE:"), or a ticker with a foreign exchange suffix
#: ("2330.TW", "SHOP.TO"). Case-sensitive: the codes are written in capitals.
_FOREIGN_LISTING = re.compile(
    # "F" marks B3's odd-lot market ("Preference shares - BBDC4F", Bank Bradesco's fractional lots).
    r"\b[A-Z]{4}\d{1,2}F?\b"
    r"|\b(?:BVMF|BOVESPA|B3|TWSE|SEHK|HKEX|TSX|ASX|LSE|BMV|NSE|BSE)\s*:"
    r"|\b\d{4}\.(?:TW|HK|T)\b|\b[A-Z]{1,5}\.(?:SA|TO|AX|L|MX|NS|BO|HK)\b")
#: The same pattern for Postgres (the recompute's pre-filter).
FOREIGN_LISTING_SQL = _FOREIGN_LISTING.pattern.replace(r"\b", r"\y")


def currency_unstated(security_title: str | None, row_notes: list[str], price: Decimal | None) -> bool:
    """True when the row's security is a listing outside the US and no note on the row says
    what currency its price is in: no conversion into dollars, no currency named (that case is
    price_in_foreign_currency's), and no dollar amount equal to the price. Santander Brasil
    ("UNIT - SANB11") and Petrobras ("PETR4") state their conversions into dollars, so they
    count; Bank Bradesco ("Preference shares - BBDC4", 17.98 with no note, the B3 price in
    reais) does not."""
    if price is None or price <= 0:
        return False
    if not security_title or not _FOREIGN_LISTING.search(security_title):
        return False
    for note in row_notes:
        if _IN_USD.search(note) or _CURRENCIES.search(note):
            return False
        for match in _USD_AMOUNT.finditer(note):
            try:
                stated = Decimal(match.group(1).replace(",", ""))
            except ArithmeticError:
                continue
            if abs(stated - price) <= price * Decimal("0.02"):
                return False
    return True


def row_issues(*, traded_on: date | None, filed_on: date | None, shares: Decimal | None,
               price: Decimal | None, value: Decimal | None, ads: bool = False,
               foreign_currency: bool = False, unstated_currency: bool = False,
               open_market: bool = False) -> list[str]:
    """Absolute checks on one transaction row, as filed. Returns issue codes (empty = fine).
    `ads`: the caller found an ADS/ordinary-share mismatch (see ads_mismatch).
    `foreign_currency`: a price footnote names another currency (see price_in_foreign_currency).
    `unstated_currency`: a foreign listing with no currency stated (see currency_unstated).
    `open_market`: the row is a purchase or sale on the market (P/S in Table I). One of 0 shares
    at a price above 0 is not a trade anyone can size: Aetos' funds file LLC interests that "are
    not unitized" as 0 shares at $7,333.34, the dollar amount put in (tx 274428), and a 4/A
    that cancels a sale files it as 0 shares at $73.93 (tx 289479). Its value is missing
    (ownership._value), and the row reads "check the filing"."""
    issues: list[str] = []
    if traded_on is not None and filed_on is not None and traded_on > filed_on:
        issues.append(DATE_AFTER_FILING)
    unstated_currency = unstated_currency and not foreign_currency
    if (
        (price is not None and price > MAX_PLAUSIBLE_PRICE)
        or (value is not None and value > MAX_PLAUSIBLE_VALUE)
        or (shares is not None and price is not None and shares >= _TRANSPOSED_MIN and price == shares)
        or (open_market and shares is not None and shares == 0 and price is not None and price > 0)
        or ads
        or foreign_currency
        or unstated_currency
    ):
        issues.append(VALUE_IMPLAUSIBLE)
    if foreign_currency:
        issues.append(PRICE_NOT_USD)
    if unstated_currency:
        issues.append(CURRENCY_UNSTATED)
    return issues


#: A share class key: "Common Stock, par value $0.01" and "Common stock, $0.01 par value"
#: both become "commonstock", so the same class compares with itself even when spelled differently.
_CLASS_KEY_SQL = r"regexp_replace(lower(coalesce({col}, '')), 'par value.*|\$.*|[^a-z]', '', 'g')"


async def flag_relative_prices(session: AsyncSession, *, filing_ids: list[int] | None = None,
                               table: str = "transactions") -> int:
    """Flag prices over RELATIVE_PRICE_FACTOR times the market price of the same share class.

    The yardstick is the median price of the same issuer's open-market trades
    (codes P and S, which happen at market prices) in the same share class and
    calendar quarter, from current, unflagged rows; it needs at least 4 of them.
    Comparing within a class matters: a super-voting share or a preferred share
    legitimately trades at many times the common stock's price. Grants and
    tax withholdings often carry nominal prices, so they never set the
    yardstick. Catches misplaced decimal points ($15,159 for a $151.59 share).
    Scoped to the issuers in `filing_ids` when given, else every row. `table` is the
    recompute's work copy of transactions (compute_value_flags), else the table itself.
    Returns rows flagged.
    """
    _check_table(table)
    scope = ("AND t.issuer_entity_id IN (SELECT issuer_entity_id FROM filings WHERE id = ANY(:ids))"
             if filing_ids is not None else "")
    key_t = _CLASS_KEY_SQL.format(col="t.security_title")
    result = await session.execute(text(f"""
        WITH yardsticks AS (
            SELECT t.issuer_entity_id, {key_t} AS class_key, date_trunc('quarter', t.traded_on) AS quarter,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price) AS median, count(*) AS n
              FROM {table} t
             WHERE t.code IN ('P', 'S') AND t.price > 0 AND t.traded_on IS NOT NULL AND t.is_current
               AND NOT t.is_derivative AND NOT (:flag = ANY(t.data_issues)) {scope}
             GROUP BY 1, 2, 3
        )
        UPDATE {table} t
           SET data_issues = array_append(t.data_issues, :flag)
          FROM yardsticks y
         WHERE t.issuer_entity_id = y.issuer_entity_id AND {key_t} = y.class_key
           AND date_trunc('quarter', t.traded_on) = y.quarter
           AND y.n >= :min_rows AND y.median > 0 AND t.price > y.median * :factor
           AND t.is_current AND NOT t.is_derivative AND NOT (:flag = ANY(t.data_issues))
    """), {"flag": VALUE_IMPLAUSIBLE, "ids": filing_ids, "min_rows": _RELATIVE_MIN_ROWS,
           "factor": RELATIVE_PRICE_FACTOR})
    flagged = int(getattr(result, "rowcount", 0) or 0)
    # Options and warrants: a contract price above twice the company's typical share
    # price that quarter is not a price per contract (e.g. the total premium typed as the price:
    # 21,943 contracts at "$438,860" = $9.6B). The yardstick is the median open-market price of
    # the company's shares (any class), from at least 4 trades.
    derivatives = await session.execute(text(rf"""
        WITH yardsticks AS (
            SELECT t.issuer_entity_id, date_trunc('quarter', t.traded_on) AS quarter,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price) AS median, count(*) AS n
              FROM {table} t
             WHERE t.code IN ('P', 'S') AND t.price > 0 AND t.traded_on IS NOT NULL AND t.is_current
               AND NOT t.is_derivative AND NOT (:flag = ANY(t.data_issues)) {scope}
             GROUP BY 1, 2
        )
        UPDATE {table} t
           SET data_issues = array_append(t.data_issues, :flag)
          FROM yardsticks y
         WHERE t.issuer_entity_id = y.issuer_entity_id AND date_trunc('quarter', t.traded_on) = y.quarter
           AND t.is_derivative AND t.code IN ('P', 'S') AND t.is_current
           -- options and warrants only: a convertible note is legitimately priced near $1,000
           AND t.security_title ~* '(option|warrant|\mcall\M|\mput\M)'
           AND y.n >= :min_rows AND y.median > 0 AND t.price > y.median * :derivative_factor
           AND NOT (:flag = ANY(t.data_issues))
    """), {"flag": VALUE_IMPLAUSIBLE, "ids": filing_ids, "min_rows": _RELATIVE_MIN_ROWS,
           "derivative_factor": DERIVATIVE_PRICE_FACTOR})
    flagged += int(getattr(derivatives, "rowcount", 0) or 0)
    # Any derivative (a convertible note too): its value can't be many times what its underlying
    # shares are worth. Barfresh: a note convertible into 10,345 shares at $2.90 (about $30,000)
    # reported as 9,375 notes at "$30,000" = $281M. The share price is the company's median
    # open-market price that quarter, or else the row's own exercise price (ignored when nominal,
    # as for pre-funded warrants at $0.001).
    underlying = await session.execute(text(f"""
        WITH yardsticks AS (
            SELECT t.issuer_entity_id, date_trunc('quarter', t.traded_on) AS quarter,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price) AS median
              FROM {table} t
             WHERE t.code IN ('P', 'S') AND t.price > 0 AND t.traded_on IS NOT NULL AND t.is_current
               AND NOT t.is_derivative AND NOT (:flag = ANY(t.data_issues)) {scope}
             GROUP BY 1, 2
        )
        UPDATE {table} t
           SET data_issues = array_append(t.data_issues, :flag)
         WHERE t.is_derivative AND t.code IN ('P', 'S') AND t.is_current AND t.underlying_shares > 0
           AND t.value > t.underlying_shares * :underlying_factor * coalesce(
                 (SELECT y.median FROM yardsticks y WHERE y.issuer_entity_id = t.issuer_entity_id
                     AND y.quarter = date_trunc('quarter', t.traded_on)),
                 CASE WHEN t.exercise_price >= :min_exercise THEN t.exercise_price END)
           AND NOT (:flag = ANY(t.data_issues)) {scope}
    """), {"flag": VALUE_IMPLAUSIBLE, "ids": filing_ids, "underlying_factor": UNDERLYING_VALUE_FACTOR,
           "min_exercise": _NOMINAL_EXERCISE_PRICE})
    flagged += int(getattr(underlying, "rowcount", 0) or 0)
    if flagged and table == "transactions":
        await sync_feed_flags(session)
    return flagged


#: Form 144 notices and stored closing prices within this many days of a trade set its yardstick.
_EXTERNAL_WINDOW_DAYS = 90
#: A stored close recorded within this many days of its session judges prices even when its
#: provider's splits are unknown (see flag_totals_as_price).
_CLOSE_RECORDED_DAYS = 10
#: A stored close `d` (prices_daily) that is still the price its session's trades were made at:
#: recorded within _CLOSE_RECORDED_DAYS of its session, or its provider's splits were read after
#: it was fetched and none lies between its session and its fetch (a day's margin for time zones).
USABLE_CLOSE_SQL = """(d.fetched_at < d.day + CAST(:recorded AS int)
       OR (EXISTS (SELECT 1 FROM price_split_checks c
                    WHERE c.security_id = d.security_id AND c.provider = d.provider AND c.checked_at >= d.fetched_at)
           AND NOT EXISTS (SELECT 1 FROM price_splits s
                            WHERE s.security_id = d.security_id AND s.provider = d.provider
                              AND s.ex_date > d.day AND s.ex_date <= CAST(d.fetched_at AS date) + 1)))"""
#: A price this many times the company's Form 144 price for ANY class is flagged too.
CROSS_CLASS_FACTOR = 2000
#: One trade worth more than this many times the company's reported public float is flagged.
FLOAT_FACTOR = 10
#: A price this many times the same class's median price over the year before is flagged.
HISTORY_FACTOR = 1000
#: The outside yardstick only judges prices from this level up: a total typed as the price is
#: large (IHT's smallest was $9,407), and a Form 144 is itself filer-typed (a market value in
#: thousands would make every normal price look high).
_MIN_EXTERNAL_CHECK_PRICE = 1000
#: A "price" that grows in step with the share count: at least this many rows, with prices at
#: least this many times apart, whose price / shares varies at most a quarter as much (in logs).
_PROPORTIONAL_MIN_ROWS = 3
_PROPORTIONAL_MIN_SPREAD = 2.0
#: ...and price / shares (the share price, if the price field holds a total) at least this much.
_MIN_IMPLIED_PRICE = 0.05


async def flag_totals_as_price(session: AsyncSession, *, filing_ids: list[int] | None = None,
                               table: str = "transactions") -> int:
    """Flag prices that are really totals, even when every row a company reports has the mistake.

    The relative check compares a price with the company's other prices, so it can't see a
    company whose every row carries the total dollar amount in the price field. InnSuites
    (IHT, about $1.40 a share) reported 34,800 shares at "$52,316.70", 25,000 at "$33,000",
    16,000 at "$22,593.60": over $1.8B counted for a $15M company. Two checks that don't lean
    on the filer's own prices:

    1. Proportional prices: one owner's open-market trades in one company and share class
       whose "price" moves in step with the share count (price / shares nearly constant while
       the prices themselves differ several-fold). Real prices barely move while share counts
       vary.
    2. An outside yardstick: the price implied by the company's Form 144 notices for the same
       class (aggregate market value / shares) within 90 days, or a stored closing price in
       that window; a price over RELATIVE_PRICE_FACTOR times it is flagged.

    Scoped to the companies in `filing_ids` when given. `table` as for flag_relative_prices.
    Returns rows flagged.
    """
    _check_table(table)

    scope = ("AND t.issuer_entity_id IN (SELECT issuer_entity_id FROM filings WHERE id = ANY(:ids))"
             if filing_ids is not None else "")
    key_t = _CLASS_KEY_SQL.format(col="t.security_title")
    rows = (await session.execute(text(f"""
        SELECT t.id, t.issuer_entity_id, t.entity_id, {key_t} AS class_key, t.price, t.shares
          FROM {table} t
         WHERE t.code IN ('P', 'S') AND t.is_current AND NOT t.is_derivative
           AND t.price > 0 AND t.shares > 0 AND NOT (:flag = ANY(t.data_issues)) {scope}
    """), {"flag": VALUE_IMPLAUSIBLE, "ids": filing_ids})).all()
    groups: dict[tuple[int, int, str], list[tuple[int, float, float]]] = {}
    for r in rows:
        groups.setdefault((r.issuer_entity_id, r.entity_id, r.class_key), []).append(
            (int(r.id), float(r.price), float(r.shares)))
    proportional: list[int] = []
    for members in groups.values():
        if len(members) < _PROPORTIONAL_MIN_ROWS:
            continue
        prices = [m[1] for m in members]
        if max(prices) < min(prices) * _PROPORTIONAL_MIN_SPREAD:
            continue
        # price / shares must itself look like a share price: a total typed as the price divides
        # back to one (IHT: about $1.40). Real prices divided by share counts give fractions of a
        # cent, and a few rows whose prices and sizes happen to move together must not count.
        implied = sorted(m[1] / m[2] for m in members)
        if implied[len(implied) // 2] < _MIN_IMPLIED_PRICE:
            continue
        log_p = [math.log(m[1]) for m in members]
        log_r = [math.log(m[1] / m[2]) for m in members]

        def variance(xs: list[float]) -> float:
            mean = sum(xs) / len(xs)
            return sum((x - mean) ** 2 for x in xs) / len(xs)

        if variance(log_r) * 4 < variance(log_p):
            proportional.extend(m[0] for m in members)
    flagged = int(getattr(await session.execute(text(
        f"UPDATE {table} SET data_issues = array_append(data_issues, :flag) "
        "WHERE id = ANY(:ids) AND NOT (:flag = ANY(data_issues))"),
        {"flag": VALUE_IMPLAUSIBLE, "ids": proportional}), "rowcount", 0) or 0)

    key_p = _CLASS_KEY_SQL.format(col="p.security_title")
    # Form 144s name the class loosely ("Common" against a Form 4's "Common Stock, par value
    # $0.01" or even the company's name), so a notice whose class is contained in the trade's
    # counts as the same class (factor RELATIVE_PRICE_FACTOR); any other notice for the company
    # still bounds the price, but only at CROSS_CLASS_FACTOR (a super-voting class can trade at
    # about 1,500 times the common, as Berkshire's A and B shares do). LEAST ignores missing ones.
    notice_median = """(SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY p.market_value / p.shares)
                    FROM planned_sales p JOIN filings f ON f.id = p.filing_id
                   WHERE p.issuer_entity_id = t.issuer_entity_id AND p.shares > 0 AND p.market_value > 0
                     AND f.superseded_by_id IS NULL {same_class}
                     AND f.filed_on BETWEEN t.traded_on - CAST(:window AS int) AND t.traded_on + CAST(:window AS int))"""
    same = notice_median.format(same_class=f"AND {key_p} <> '' AND position({key_p} in {key_t}) > 0")
    any_class = notice_median.format(same_class="")
    # Stored closes are split-adjusted when fetched, while a Form 4 price is as filed: a close
    # fetched after a 4-for-1 split reads a quarter of the price that day (CRWD $684 filed
    # against $168 stored). A close judges a price only when it is still the price that day's
    # trades were made at: its provider's splits were read after it was fetched and none falls
    # between its session and its fetch (price_splits, price_split_checks), or it was recorded
    # within _CLOSE_RECORDED_DAYS of its session, before any later split could change it
    # (USABLE_CLOSE_SQL). The history backfilled on 24-25 September 2026 was recorded months after
    # most of its sessions: by the second test alone, 1,448 of 320,462 closes could judge a price.
    external = await session.execute(text(f"""
        UPDATE {table} t
           SET data_issues = array_append(t.data_issues, :flag)
         WHERE t.code IN ('P', 'S') AND t.is_current AND NOT t.is_derivative AND t.price > 0
           AND t.traded_on IS NOT NULL AND NOT (:flag = ANY(t.data_issues)) {scope}
           AND t.price >= :min_external
           AND t.price > least(
                 :factor * {same},
                 :cross_factor * {any_class},
                 :factor * (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY d.close)
                              FROM prices_daily d
                             WHERE d.security_id = t.security_id AND d.close > 0
                               AND d.day BETWEEN t.traded_on - CAST(:window AS int) AND t.traded_on + CAST(:window AS int)
                               AND {USABLE_CLOSE_SQL}
                               -- ...and on the trade's side of every known split: BKNG's sales at
                               -- $5,200 on 15 January 2026 against its closes after the 25-for-1
                               -- split of 6 April ($170) would read 29 times the price.
                               AND NOT EXISTS (SELECT 1 FROM price_splits s
                                                WHERE s.security_id = d.security_id AND s.provider = d.provider
                                                  AND s.ex_date > least(d.day, t.traded_on)
                                                  AND s.ex_date <= greatest(d.day, t.traded_on))
                               AND {key_t} LIKE '%common%'))
    """), {"flag": VALUE_IMPLAUSIBLE, "ids": filing_ids, "factor": RELATIVE_PRICE_FACTOR,
           "cross_factor": CROSS_CLASS_FACTOR, "window": _EXTERNAL_WINDOW_DAYS,
           "min_external": _MIN_EXTERNAL_CHECK_PRICE, "recorded": _CLOSE_RECORDED_DAYS})
    flagged += int(getattr(external, "rowcount", 0) or 0)
    # Company size: one trade worth more than FLOAT_FACTOR times the company's whole reported
    # public float (Reborn Coffee: 131,387 shares at "$180,000" = $23.6 billion). A float of
    # zero (new listings file one) or one more than 3 years before the trade says nothing.
    # The float is filer-typed too, and some companies type it in thousands (LandBridge
    # "1,700,000" for a company worth billions, Crexendo three years running), so, like the
    # Form 144 yardstick, it only judges prices of _MIN_EXTERNAL_CHECK_PRICE and up: the error
    # it is there for is a total typed as the price, which is always a large "price". A normal
    # price against a float in thousands (LandBridge's $93.8M sale at $75.05) stays counted.
    above_float = await session.execute(text(f"""
        UPDATE {table} t
           SET data_issues = array_append(t.data_issues, :flag)
          FROM issuer_public_float p
         WHERE p.entity_id = t.issuer_entity_id AND p.public_float > 0
           AND p.as_of > t.traded_on - 1100
           AND t.code IN ('P', 'S') AND t.is_current AND NOT t.is_derivative
           AND t.price >= :min_price
           AND t.value > :float_factor * p.public_float
           AND NOT (:flag = ANY(t.data_issues)) {scope}
    """), {"flag": VALUE_IMPLAUSIBLE, "ids": filing_ids, "float_factor": FLOAT_FACTOR,
           "min_price": _MIN_EXTERNAL_CHECK_PRICE})
    flagged += int(getattr(above_float, "rowcount", 0) or 0)
    # The same class's own history: a price of $1,000 or more that is over HISTORY_FACTOR times
    # the median price of the class's other rows (any code with a price) in the year before.
    # Securetech's Series A Preferred changed hands at $1.00 in May; in August a filing said
    # 1,000 of them were bought at "$100,000" each ($100M). Grants, option exercises and tax
    # withholdings sit near market prices, so a thousandfold gap is never a normal move.
    history = await session.execute(text(f"""
        UPDATE {table} t
           SET data_issues = array_append(t.data_issues, :flag)
         WHERE t.code IN ('P', 'S') AND t.is_current AND NOT t.is_derivative AND t.price >= :min_price
           AND t.traded_on IS NOT NULL AND NOT (:flag = ANY(t.data_issues)) {scope}
           AND t.price > :history_factor * (
                 SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY y.price)
                   FROM {table} y
                  WHERE y.issuer_entity_id = t.issuer_entity_id AND y.id <> t.id AND y.price > 0
                    AND y.is_current AND NOT y.is_derivative AND NOT (:flag = ANY(y.data_issues))
                    AND {_CLASS_KEY_SQL.format(col="y.security_title")} = {key_t}
                    AND y.traded_on BETWEEN t.traded_on - 365 AND t.traded_on)
    """), {"flag": VALUE_IMPLAUSIBLE, "ids": filing_ids, "min_price": _MIN_EXTERNAL_CHECK_PRICE,
           "history_factor": HISTORY_FACTOR})
    flagged += int(getattr(history, "rowcount", 0) or 0)
    if flagged and table == "transactions":
        await sync_feed_flags(session)
    return flagged


#: The recompute's work copy of transactions: a temporary table, private to its session and
#: dropped when the transaction that made it ends.
WORK_TABLE = "value_flags_work"
_TABLES = ("transactions", WORK_TABLE)
#: The flags the value checks own. The recompute takes them off and puts back what the rules
#: say; other flags (date_after_filing, amendment_unpaired) are never touched by it.
VALUE_CODES = (VALUE_IMPLAUSIBLE, PRICE_NOT_USD, CURRENCY_UNSTATED)


def _check_table(table: str) -> None:
    """The checks take a table name into their SQL, so only these two are allowed."""
    if table not in _TABLES:
        raise ValueError(f"not a transactions table: {table!r}")


@dataclass(frozen=True)
class FlagChange:
    """One row whose flags the current rules would change: what it has, and what it should have.
    `version` is the row's version (Postgres xmin) when the flags were worked out: any write to
    the row since (a reload with new figures, even one that sets the same flags) changes it."""

    id: int
    before: list[str]
    after: list[str]
    version: str


async def compute_value_flags(session: AsyncSession) -> list[FlagChange]:
    """Work out every row's value flags from the current rules, without changing any row.

    The checks run on a temporary copy of transactions with the value flags taken off (the same
    SQL and Python as at load time, pointed at the copy), in the same order as always: absolute
    rules, then the currency checks, the ADS check, the relative prices and the outside
    yardsticks. Only reads touch the real tables, so a live load is never kept waiting. Returns the
    rows whose flags differ from what they have now.
    """
    await session.execute(text(f"DROP TABLE IF EXISTS {WORK_TABLE}"))
    await session.execute(text(f"""
        CREATE TEMP TABLE {WORK_TABLE} ON COMMIT DROP AS
        SELECT id, filing_id, dedupe_key, issuer_entity_id, entity_id, security_id, security_title, code,
               price, shares, value, traded_on, is_current, is_derivative, is_open_market, underlying_shares,
               exercise_price,
               footnote_ids, data_issues AS before, xmin::text AS row_version,
               array_remove(array_remove(array_remove(data_issues, :a), :b), :c) AS data_issues
          FROM transactions
    """), dict(zip(("a", "b", "c"), VALUE_CODES, strict=True)))
    await session.execute(text(f"CREATE UNIQUE INDEX ON {WORK_TABLE} (id)"))
    await session.execute(text(f"CREATE INDEX ON {WORK_TABLE} (issuer_entity_id)"))
    await session.execute(text(f"ANALYZE {WORK_TABLE}"))
    await flag_absolute(session, table=WORK_TABLE)
    await flag_relative_prices(session, table=WORK_TABLE)
    await flag_totals_as_price(session, table=WORK_TABLE)
    rows = (await session.execute(text(
        f"SELECT id, before, data_issues, row_version FROM {WORK_TABLE} "
        "WHERE before IS DISTINCT FROM data_issues ORDER BY id"
    ))).all()
    return [FlagChange(int(r.id), list(r.before), list(r.data_issues), str(r.row_version)) for r in rows]


async def apply_value_flags(session: AsyncSession, changes: list[FlagChange]) -> dict[str, int]:
    """Write the flags compute_value_flags worked out, and nothing else.

    A row is only changed if nothing has written to it since the flags were computed (its xmin
    is the one compute_value_flags read) and it still has the flags it had then: a filing
    reloaded in between set its own flags with the load-time rules, from its new figures, which
    stand until the next recompute. Checking the flags alone isn't enough: a reload that fixes a
    price ($15,159 back to $151.59) usually sets the same flags as before (none), and the
    recompute's flag, worked out from the old price, would land on the corrected row. Short: one
    UPDATE over the rows that change (usually none), the feed sync, and the date rule. Returns
    counts (newly_flagged / cleared count rows gaining or losing any value flag).
    """
    applied: list[FlagChange] = []
    if changes:
        payload = json.dumps([{"id": c.id, "before": c.before, "after": c.after, "version": c.version}
                              for c in changes])
        done = {int(i) for i in (await session.execute(text("""
            UPDATE transactions t SET data_issues = v.after
              FROM jsonb_to_recordset(CAST(:rows AS jsonb))
                   AS v(id bigint, before text[], after text[], version text)
             WHERE t.id = v.id AND t.xmin::text = v.version AND t.data_issues = v.before
            RETURNING t.id
        """), {"rows": payload})).scalars()}
        applied = [c for c in changes if c.id in done]
    await sync_feed_flags(session)
    await flag_dates(session)

    def valued(issues: list[str]) -> bool:
        return any(code in issues for code in VALUE_CODES)

    return {"newly_flagged": sum(1 for c in applied if valued(c.after) and not valued(c.before)),
            "cleared": sum(1 for c in applied if valued(c.before) and not valued(c.after)),
            "changed": len(applied), "skipped": len(changes) - len(applied)}


async def recompute_value_flags(session: AsyncSession) -> dict[str, int]:
    """Recompute every value flag from the current rules (compute, then apply the differences).

    Flags are only ever added by the load-time and relative checks, so a rule that was
    corrected (an ADS note giving the price per ordinary share, say) would otherwise never
    clear the flags it set. Here both steps share the caller's transaction; the 30-minute job
    (flag_transactions) computes in one transaction and applies in a second, short one, so its
    row locks never make a live load wait (a run that stripped and re-applied every flag in one
    transaction once held a poll up for 7 minutes). Returns counts.
    """
    return await apply_value_flags(session, await compute_value_flags(session))


async def sync_feed_flags(session: AsyncSession) -> None:
    """Copy transaction flags onto their feed events, in both directions.

    The payload carries the flags for the Live table, and a doubtful value no
    longer counts as the event's size (so it can't top "largest" lists or pass
    a size filter). If a flag is removed, the event gets its size back.
    Events that never had flags are left untouched.
    """
    await session.execute(text("""
        UPDATE feed_events e
           SET payload = jsonb_set(e.payload, '{data_issues}', to_jsonb(t.data_issues)),
               size_usd = CASE WHEN :flag = ANY(t.data_issues) THEN NULL ELSE t.value::float8 END
          FROM transactions t
         WHERE e.transaction_id = t.id
           AND (cardinality(t.data_issues) > 0 OR e.payload ? 'data_issues')
           AND e.payload->'data_issues' IS DISTINCT FROM to_jsonb(t.data_issues)
    """), {"flag": VALUE_IMPLAUSIBLE})


async def flag_dates(session: AsyncSession) -> int:
    """date_after_filing for rows loaded before the rule existed (load time sets it since). Such
    rows also lose their delay and late-filing figures. Only adds; usually changes nothing."""
    dated = await session.execute(text("""
        UPDATE transactions t
           SET data_issues = array_append(t.data_issues, :flag),
               delay_days = NULL, deadline_on = NULL, is_late = NULL, business_days_late = NULL
          FROM filings f
         WHERE f.id = t.filing_id AND t.traded_on > f.filed_on AND NOT (:flag = ANY(t.data_issues))
    """), {"flag": DATE_AFTER_FILING})
    return int(getattr(dated, "rowcount", 0) or 0)


async def flag_absolute(session: AsyncSession, *, table: str = "transactions") -> int:
    """Apply `row_issues`' absolute value rules in SQL to every row of `table`.

    Same rules as `row_issues`, kept side by side: change both together. The ADS check is
    flag_ads, as at load time.
    """
    _check_table(table)
    valued = await session.execute(text(f"""
        UPDATE {table} t
           SET data_issues = array_append(t.data_issues, :flag)
         WHERE NOT (:flag = ANY(t.data_issues))
           AND (t.price > :max_price OR t.value > :max_value
                OR (t.shares >= :transposed AND t.price = t.shares)
                OR (t.is_open_market AND t.shares = 0 AND t.price > 0))
    """), {"flag": VALUE_IMPLAUSIBLE, "max_price": MAX_PLAUSIBLE_PRICE, "max_value": MAX_PLAUSIBLE_VALUE,
           "transposed": _TRANSPOSED_MIN})
    # Prices in a foreign or unstated currency: evaluated in Python (the same functions as at load
    # time) over rows whose footnotes or title could matter.
    candidates = (await session.execute(text(f"""
        SELECT t.id, t.security_title, t.price, t.footnote_ids, f.footnotes
          FROM {table} t JOIN filings f ON f.id = t.filing_id
         WHERE t.price IS NOT NULL
           AND ((cardinality(t.footnote_ids) > 0 AND f.footnotes::text ~* :currency_hint)
                OR t.security_title ~ :listing)
    """), {"currency_hint": CURRENCY_HINT_SQL, "listing": FOREIGN_LISTING_SQL})).all()
    currency_ids, unstated_ids = [], []
    for c in candidates:
        notes = dict(c.footnotes or {})
        row_notes = [str(notes[k]) for k in (c.footnote_ids or []) if k in notes]
        if price_in_foreign_currency(row_notes, c.price):
            currency_ids.append(int(c.id))
        elif currency_unstated(c.security_title, row_notes, c.price):
            unstated_ids.append(int(c.id))
    currency = await _flag_with_reason(session, currency_ids, PRICE_NOT_USD, table)
    unstated = await _flag_with_reason(session, unstated_ids, CURRENCY_UNSTATED, table)
    # ADS vs underlying shares, for rows no rule above has flagged.
    ads = await flag_ads(session, table=table)
    return int(getattr(valued, "rowcount", 0) or 0) + currency + unstated + len(ads)


async def _flag_with_reason(session: AsyncSession, ids: list[int], reason: str, table: str = "transactions") -> int:
    """Add VALUE_IMPLAUSIBLE and `reason` (each once) to these rows. Returns rows changed."""
    _check_table(table)
    result = await session.execute(text(f"""
        UPDATE {table} w
           SET data_issues = CASE WHEN :reason = ANY(v.issues) THEN v.issues ELSE array_append(v.issues, :reason) END
          FROM (SELECT id, CASE WHEN :flag = ANY(data_issues) THEN data_issues
                                ELSE array_append(data_issues, :flag) END AS issues
                  FROM {table} WHERE id = ANY(:ids)) v
         WHERE w.id = v.id AND NOT (w.data_issues @> ARRAY[:flag, :reason])
    """), {"flag": VALUE_IMPLAUSIBLE, "reason": reason, "ids": ids})
    return int(getattr(result, "rowcount", 0) or 0)


async def repair_zero_values(session: AsyncSession) -> int:
    """An open-market purchase or sale reported at a price of 0, or for 0 shares, states no size,
    so it has no value (NULL), never $0. CLMT tx 332672: 24,555 units "sold" at 0, restricted
    units settled in cash, showed on Live as a "-$0" sale. Load time leaves the value empty since
    round 6 (ownership._value); this clears it on rows loaded before, with their feed events' size
    and payload value. Idempotent: usually changes nothing. Returns transactions changed."""
    cleared = await session.execute(text("""
        UPDATE transactions SET value = NULL
         WHERE is_open_market AND (price = 0 OR shares = 0) AND value IS NOT NULL
    """))
    await session.execute(text("""
        UPDATE feed_events e
           SET size_usd = NULL,
               payload = CASE WHEN e.payload ? 'value' THEN jsonb_set(e.payload, '{value}', 'null'::jsonb)
                              ELSE e.payload END
          FROM transactions t
         WHERE e.transaction_id = t.id AND t.is_open_market AND (t.price = 0 OR t.shares = 0)
           AND (e.size_usd IS NOT NULL OR (e.payload ? 'value' AND e.payload->'value' <> 'null'::jsonb))
    """))
    return int(getattr(cleared, "rowcount", 0) or 0)


async def mark_duplicates(session: AsyncSession, *, filing_ids: list[int] | None = None) -> int:
    """Mark the same trade reported in more than one filing, so it counts once.

    Rows are the same trade when they share the company, trade date, code,
    shares, price and the holding after the trade, come from different
    filings, AND the filings are linked:

    * they have a reporting owner in common (the same trade re-filed, however
      far apart), or
    * at least one side is indirect ownership (a fund and its general partner
      each reporting the fund's trade) and they were filed within 3 days.

    Two different people who each bought the same amount directly are never
    merged (identical IPO allotments are common). The earliest copy counts;
    the others point at it (duplicate_of_id); every owner's page still lists
    its own row. Two identical lines inside one filing are separate lots.
    With `filing_ids` only those filings' companies are recomputed (at load
    time); otherwise everything (the 30-minute job). Returns rows changed.
    """
    scope = ("AND t.issuer_entity_id IN (SELECT issuer_entity_id FROM filings WHERE id = ANY(:ids))"
             if filing_ids is not None else "")
    result = await session.execute(text(f"""
        WITH cand AS (
            SELECT t.id, t.filing_id, f.filed_on, t.issuer_entity_id, t.traded_on, t.code, t.shares, t.price,
                   t.shares_owned_after, t.ownership_nature
              FROM transactions t JOIN filings f ON f.id = t.filing_id
             WHERE t.is_current AND t.is_open_market AND t.shares IS NOT NULL
               AND NOT (:unpaired = ANY(t.data_issues)) {scope}
        ), linked AS (
            SELECT r.id, min(c.id) AS rep
              FROM cand r JOIN cand c
                ON c.issuer_entity_id = r.issuer_entity_id AND c.traded_on = r.traded_on AND c.code = r.code
               AND c.shares = r.shares AND c.price IS NOT DISTINCT FROM r.price
               AND c.shares_owned_after IS NOT DISTINCT FROM r.shares_owned_after
               AND c.id < r.id AND c.filing_id <> r.filing_id
             WHERE EXISTS (SELECT 1 FROM transaction_owners a JOIN transaction_owners b ON b.entity_id = a.entity_id
                            WHERE a.transaction_id = r.id AND b.transaction_id = c.id)
                OR ((r.ownership_nature = 'I' OR c.ownership_nature = 'I')
                    AND abs(r.filed_on - c.filed_on) <= :window)
             GROUP BY r.id
        ), wanted AS (
            SELECT t.id, l.rep AS dup
              FROM transactions t LEFT JOIN linked l ON l.id = t.id
             WHERE l.id IS NOT NULL OR (t.duplicate_of_id IS NOT NULL {scope})
        )
        UPDATE transactions t SET duplicate_of_id = w.dup
          FROM wanted w
         WHERE t.id = w.id AND t.duplicate_of_id IS DISTINCT FROM w.dup
    """), {"unpaired": AMENDMENT_UNPAIRED, "window": DUPLICATE_FILING_WINDOW_DAYS, "ids": filing_ids})
    return int(getattr(result, "rowcount", 0) or 0)


async def flag_transactions(ctx: JobContext) -> None:
    """Scheduler entry point (job `quality.flag_transactions`): value flags, zero prices, duplicates.

    The flags are worked out in one transaction that only reads (compute_value_flags), then the
    differences are written in a second, short one, so live loads never wait on this job.
    """
    async with session_scope() as session:
        changes = await compute_value_flags(session)
        await session.rollback()  # ends the read-only transaction and drops its work table
    async with session_scope() as session:
        recomputed = await apply_value_flags(session, changes)
        zero_values = await repair_zero_values(session)
    async with session_scope() as session:
        duplicates = await mark_duplicates(session)
        totals = (await session.execute(text(
            "SELECT count(*) FILTER (WHERE :d = ANY(data_issues)) AS dates, "
            "count(*) FILTER (WHERE :v = ANY(data_issues)) AS values, "
            "count(*) FILTER (WHERE duplicate_of_id IS NOT NULL) AS duplicates FROM transactions"),
            {"d": DATE_AFTER_FILING, "v": VALUE_IMPLAUSIBLE})).one()
        # How many stored closes can judge a price (flag_totals_as_price): the rest may be re-based
        # by a split. Under a second over 320,000 closes.
        closes = (await session.execute(text(
            f"SELECT count(*) AS stored, count(*) FILTER (WHERE {USABLE_CLOSE_SQL}) AS usable FROM prices_daily d"),
            {"recorded": _CLOSE_RECORDED_DAYS})).one()
    ctx.rows_updated += recomputed["newly_flagged"] + recomputed["cleared"] + zero_values
    ctx.stats.update({"newly_flagged": recomputed["newly_flagged"], "flags_cleared": recomputed["cleared"],
                      "flag_rows_changed": recomputed["changed"], "flag_rows_skipped": recomputed["skipped"],
                      "zero_values_cleared": zero_values,
                      "duplicates_changed": duplicates, "flagged_dates": int(totals.dates),
                      "flagged_values": int(totals.values), "duplicates": int(totals.duplicates),
                      "closes_stored": int(closes.stored), "closes_usable": int(closes.usable)})


async def repair_amendments_job(ctx: JobContext) -> None:
    """Scheduler entry point (job `quality.repair_amendments`, daily): re-pair 4/A amendments and
    re-point rows linked to another company's security."""
    from smt.ingest.entities import repair_security_links
    from smt.ingest.ownership import repair_amendments  # local: ownership imports this module

    async with session_scope() as session:
        counts = await repair_amendments(session)
        counts["rows_moved_to_own_security"] = await repair_security_links(session)
        await mark_duplicates(session)
    ctx.rows_updated += counts["rows_paired"]
    ctx.stats.update(counts)
