/**
 * The data-issue codes the backend puts on a trade row, and what each one does to
 * the figures shown (backend/src/smt/ingest/quality.py sets them).
 *
 * Plain functions with no React, so they can be tested with Node's own test runner
 * (dataIssues.test.ts, `npm test`). components/ui/TradeValue.tsx re-exports them and
 * renders the cells.
 */
import { money, moneyCompact, plainAmount, price, toNumber } from "./format.ts";

export const VALUE_IMPLAUSIBLE = "value_implausible";
export const DATE_AFTER_FILING = "date_after_filing";
export const AMENDMENT_UNPAIRED = "amendment_unpaired";
/** A price footnote says the price is in another currency; always comes with VALUE_IMPLAUSIBLE. */
export const PRICE_NOT_USD = "price_not_usd";
/** A listing outside the U.S. ("Preference shares - BBDC4") with no currency stated on the row;
 * always comes with VALUE_IMPLAUSIBLE. */
export const CURRENCY_UNSTATED = "currency_unstated";

type Issues = readonly string[] | null | undefined;
type Amount = string | number | null | undefined;

/** Plain-English explanations, shown in the quick look's notes box and as hover text. */
export const ISSUE_TEXT: Record<string, string> = {
  [VALUE_IMPLAUSIBLE]:
    "The price on this filing can't be a price per share (for example the total was typed into the price field, or a decimal point is missing). The figures are shown as filed, but this trade's dollar value is left out of totals and rankings.",
  [DATE_AFTER_FILING]:
    "The trade date on this filing is later than the date it was filed, which isn't possible, so the date is a typing error. The delay and late-filing checks can't be worked out for this trade.",
  [AMENDMENT_UNPAIRED]:
    "This corrected row couldn't be matched to the row it corrects, so it's left out of totals.",
  [PRICE_NOT_USD]:
    "The price on this filing is in another currency, as a footnote on the filing says, not in dollars. The figures are shown as filed, but this trade's value is left out of dollar totals and rankings.",
  [CURRENCY_UNSTATED]:
    "The shares are listed outside the U.S. (the filing names the local listing, such as BBDC4 on Brazil's B3), and the filing doesn't say what currency the price is in. The figures are shown as filed, but this trade's value is left out of dollar totals and rankings.",
};

/**
 * The words a value cell uses in place of a dollar amount. This is the only place the
 * site spells them: every page renders them through valueCell and TradeValue, so a
 * row reads the same in the Live band, the Live grid and every table. (Round 6: the
 * Live band hard-coded "Check filing" for UMC's New Taiwan dollar rows while the grid
 * below said "Other currency".) dataIssues.test.ts checks no other source file spells them.
 */
export const VALUE_LABEL = {
  other_currency: "Other currency",
  currency_unstated: "Currency not stated",
  check_filing: "Check filing",
} as const;

/** True when the filing's price is in another currency (a footnote says so). */
export function inOtherCurrency(issues: Issues): boolean {
  return Boolean(issues?.includes(PRICE_NOT_USD));
}

/** True when the security is listed outside the U.S. and the filing doesn't say what currency its price is in. */
export function currencyUnstated(issues: Issues): boolean {
  return Boolean(issues?.includes(CURRENCY_UNSTATED)) && !inOtherCurrency(issues);
}

/**
 * A price as shown: in dollars, or, when the filing's price is in another currency or its currency
 * isn't stated, the number as filed with no "$" (UMC's "163.00" is New Taiwan dollars, not $163).
 *
 * An open-market purchase or sale (`code` P or S) filed at a price of 0 has no stated price and
 * reads "–", like its value: CLMT's cash-settled units (tx 332672) read "$0.00" beside a value
 * of "–" (round 7). An award or gift keeps the "$0.00" its filing gives: nothing was paid.
 */
export function priceShown(value: Amount, issues: Issues, code?: string | null): string {
  if ((code === "P" || code === "S") && toNumber(value) === 0) return "–";
  if (inOtherCurrency(issues)) return `${plainAmount(value)} (filing's currency)`;
  if (currencyUnstated(issues)) return `${plainAmount(value)} (currency not stated)`;
  return price(value);
}

export function hasValueIssue(issues: Issues): boolean {
  return Boolean(issues?.includes(VALUE_IMPLAUSIBLE));
}

/**
 * A trade's dollar value in compact form ("$1.2M"), or "–" when there is none.
 *
 * Zero counts as none. Shares traded at a price of $0 aren't a trade worth nothing:
 * the filing gives no price. CLMT's cash-settled units (tx 332672: 24,555 units "sold"
 * at 0) read "−$0" in Live before round 6, and most awards and gifts are filed at a
 * price of 0 as well. The site's rule: a number the filing doesn't give is "–", never $0.
 */
export function tradeAmount(value: Amount): string {
  const n = toNumber(value);
  return n === null || n === 0 ? "–" : moneyCompact(n);
}

/**
 * A Form 144 notice's estimated market value ("~$1.4M"), or "–" when it gives none.
 *
 * The value is the seller's estimate, so it carries a tilde. A value of 0 is no estimate,
 * never "~$0": Snowflake notice 0001959173-26-007025 (Frederick G Thiel, ~Sep 17, 2026) is
 * filed with a market value of 0 and 0 shares, and read "~$0" in the Planned sales list, the
 * chart tooltip and the Status spot check while its quick look read "–" (pre-review 7).
 */
export function plannedAmount(value: Amount): string {
  const text = tradeAmount(value);
  return text === "–" ? text : `~${text}`;
}

/**
 * What a trade's value cell shows:
 * - "other_currency": the price is in another currency, so there is no dollar value to show;
 * - "currency_unstated": a listing outside the U.S. whose currency the filing doesn't state;
 * - "check_filing": the value can't be right as filed (a total typed as the price);
 * - "not_counted": a dollar amount in Dim ink, listed but not in the totals beside it;
 * - "amount": a dollar amount that the totals count ("–" when the filing gives none).
 * A currency row is also flagged VALUE_IMPLAUSIBLE; it reads "Other currency", not "Check filing".
 */
export type ValueCell =
  | { kind: "other_currency" }
  | { kind: "currency_unstated" }
  | { kind: "check_filing" }
  | { kind: "not_counted"; text: string }
  | { kind: "amount"; text: string };

export function valueCell(direction: string | null | undefined, value: Amount, issues: Issues, counted = true): ValueCell {
  if (inOtherCurrency(issues)) return { kind: "other_currency" };
  if (currencyUnstated(issues)) return { kind: "currency_unstated" };
  if (hasValueIssue(issues)) return { kind: "check_filing" };
  const text = tradeAmount(value);
  if (!counted) {
    const sign = text === "–" ? "" : direction === "buy" ? "+" : direction === "sell" ? "−" : "";
    return { kind: "not_counted", text: `${sign}${text}` };
  }
  return { kind: "amount", text };
}

/** Issues about a figure on the filing; the quick look's notes box lists only these. */
const FIGURE_ISSUES: readonly string[] = [VALUE_IMPLAUSIBLE, DATE_AFTER_FILING, PRICE_NOT_USD, CURRENCY_UNSTATED];

/** The quick look's notes box: a heading and one explanation per issue. */
export interface IssueNotes {
  heading: string;
  notes: { code: string; text: string }[];
}

/**
 * The notes box for a trade's figures, or null when there is nothing to explain.
 *
 * A price in another currency (UMC's New Taiwan dollars) or in a currency the filing
 * doesn't state (Bradesco's BBDC4, in reais) isn't a mistake. The backend still flags
 * such a row VALUE_IMPLAUSIBLE, so its value stays out of dollar totals, but the box
 * says what the price is instead of the generic "can't be right", and drops that
 * explanation. Another issue on the same row (a date after the filing) keeps its own
 * note, and the heading then falls back to the general one.
 */
export function issueNotes(issues: Issues): IssueNotes | null {
  let known = (issues ?? []).filter((i) => FIGURE_ISSUES.includes(i));
  const otherCurrency = known.includes(PRICE_NOT_USD);
  const unstated = !otherCurrency && known.includes(CURRENCY_UNSTATED);
  // A named currency is the more specific finding, so it replaces "currency not stated".
  if (otherCurrency) known = known.filter((i) => i !== VALUE_IMPLAUSIBLE && i !== CURRENCY_UNSTATED);
  if (unstated) known = known.filter((i) => i !== VALUE_IMPLAUSIBLE);
  if (!known.length) return null;
  const heading =
    otherCurrency && known.length === 1
      ? "This price isn't in dollars"
      : unstated && known.length === 1
        ? "This price's currency isn't stated"
        : "This filing has a figure that can't be right";
  return { heading, notes: known.map((code) => ({ code, text: ISSUE_TEXT[code] ?? code })) };
}

/**
 * The quick look's Value line:
 * - "amount": a dollar amount, shown with its +/− sign ("–" when the filing gives none);
 * - "as_filed": the figure as filed, with a Dim note saying why it isn't a dollar amount
 *   the totals use ("see above" points at the notes box).
 */
export type ValueText = { kind: "amount"; text: string } | { kind: "as_filed"; text: string; note: string };

export function valueText(value: Amount, issues: Issues): ValueText {
  if (inOtherCurrency(issues)) {
    return { kind: "as_filed", text: plainAmount(value), note: "in the filing's currency, not in dollar totals (see above)" };
  }
  if (currencyUnstated(issues)) {
    return {
      kind: "as_filed",
      text: plainAmount(value),
      note: "in a currency the filing doesn't state, not in dollar totals (see above)",
    };
  }
  if (hasValueIssue(issues)) {
    const n = toNumber(value);
    return { kind: "as_filed", text: n === null || n === 0 ? "–" : money(n), note: "as filed (can't be right, see above)" };
  }
  return { kind: "amount", text: tradeAmount(value) };
}
