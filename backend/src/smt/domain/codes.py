"""Form 4 transaction codes and what they mean (Form 4 General Instruction 8).

Only **P** (open-market or private purchase) and **S** (open-market or
private sale) are discretionary trades that carry a "smart money" signal. The
rest are compensation and mechanics: grants, option exercises, tax
withholding, gifts. They're stored, and shown when the viewer asks for all
codes, but they never count as buying or selling in signals.

K and V are not codes in the XML. EDGAR derives them: K from
`equitySwapInvolved`, V from `transactionTimeliness = E` (a Form 5
transaction reported early on Form 4).
"""

from __future__ import annotations

from datetime import date
from typing import Final

CODE_LABELS: Final[dict[str, str]] = {
    "P": "Open-market or private purchase",
    "S": "Open-market or private sale",
    "V": "Voluntarily reported earlier than required",
    "A": "Grant or award from the company",
    "D": "Sold or returned to the company",
    "F": "Shares withheld to pay exercise price or tax",
    "I": "Discretionary transaction (Rule 16b-3(f))",
    "M": "Exercise or conversion of an exempt derivative",
    "C": "Conversion of a derivative security",
    "E": "Expiration of a short derivative position",
    "H": "Expiration or cancellation of a long derivative position with value received",
    "O": "Exercise of an out-of-the-money derivative",
    "X": "Exercise of an in-the-money or at-the-money derivative",
    "G": "Gift",
    "L": "Small acquisition (Rule 16a-6)",
    "W": "Acquired or disposed of by will or inheritance",
    "Z": "Deposit into or withdrawal from a voting trust",
    "J": "Other (see footnotes)",
    "K": "Equity swap or similar instrument",
    "U": "Tendered in a change-of-control transaction",
}

#: Short labels for tables ("▲ Buy" / "▼ Sell" come from P and S only).
CODE_SHORT: Final[dict[str, str]] = {
    "P": "Buy", "S": "Sell", "A": "Grant", "D": "To issuer", "F": "Tax withholding",
    "M": "Exercise", "X": "Exercise", "C": "Conversion", "G": "Gift", "J": "Other",
    "I": "Discretionary", "L": "Small acquisition", "W": "Inheritance", "Z": "Voting trust",
    "E": "Expiration", "H": "Expiration", "O": "Exercise", "U": "Tender", "K": "Swap", "V": "Early report",
}

#: Codes that carry discretionary buy/sell signal.
OPEN_MARKET_CODES: Final = frozenset({"P", "S"})
#: Derivative exercises that, with a same-day S, read as "exercise and sell".
EXERCISE_CODES: Final = frozenset({"M", "X"})
#: Codes that may be reported on Form 5 instead of within two business days on
#: Form 4. They get no Form 4 late flag. (Gifts moved to Form 4 in Feb 2023; see
#: GIFTS_ON_FORM4_FROM.)
FORM5_ELIGIBLE_CODES: Final = frozenset({"L", "W"})
#: From this date bona fide gifts (code G) must be reported on Form 4 within two
#: business days (SEC amendments to Rule 16a-3, effective 2023-02-27). Before it,
#: gifts could wait for Form 5, so earlier gifts get no Form 4 late flag.
GIFTS_ON_FORM4_FROM: Final = date(2023, 2, 27)


def direction_for(code: str | None) -> str:
    """Economic direction used by signals: only P and S are buys and sells."""
    if code == "P":
        return "buy"
    if code == "S":
        return "sell"
    return "other"


def label_for(code: str | None) -> str:
    """Plain-English meaning for tooltips (spec §9: explain codes on hover/focus)."""
    return CODE_LABELS.get(code or "", "Unrecognized code")
