/**
 * The words the /tickers ranking uses for trades its dollar totals don't include.
 *
 * Two different reasons, which the API counts separately (backend
 * smt/api/routers/tickers.py):
 * - `trades_without_price`: the filing gives no price, so the trade has no value;
 * - `trades_left_out` (per side `buys_left_out`, `sells_left_out`): the filing
 *   gives a price, but not a dollar price per share: another currency (UMC's
 *   Form 4s state New Taiwan dollars), a currency the filing doesn't state
 *   (Bradesco's BBDC4), or a figure that can't be right as filed.
 * Round 6: /tickers?q=UMC read "Sold: 4 (no value reported)" and "4 without a
 * reported price", although all four filings state a price. "Reported no price"
 * is kept for trades that really have none; the others read like the person
 * page's header ("not in dollar totals", "N left out").
 *
 * Plain functions with no React or "@/" imports, tested by indexText.test.ts.
 */
import { moneyCompact, toNumber } from "../../lib/format.ts";

/** The counts a ranking row carries (a subset of the API's TickerActivityRow). */
export interface IndexCounts {
  trades_without_price: number;
  trades_left_out: number;
}

/** Why the "left out" trades are out of the dollar totals, in the ranking's words. */
export const LEFT_OUT_REASON = "other currency or a figure that can't be right";

/**
 * What follows a side's trade count ("▼ 4 ..."), for a side that has trades:
 * - "(+$1.2M)" / "(−$1.2M)": the dollar sum;
 * - "(not in dollar totals)": no sum, and some of the trades state a price the totals
 *   leave out (UMC's four NT$ sales);
 * - "(no price reported)": no sum, and none of them gives a price;
 * - "(no dollar value reported)": the sum is exactly 0, which no real trade adds up to
 *   (a Form 4 row of 0 shares, e.g. tx 274428), so it isn't shown as "+$0".
 * Only `isNote` parts are drawn in Dim ink.
 */
export function sideText(side: "buy" | "sell", value: string | null, leftOut: number): { text: string; isNote: boolean } {
  const n = toNumber(value);
  if (n === null || n === 0) {
    if (leftOut > 0) return { text: "(not in dollar totals)", isNote: true };
    return { text: n === null ? "(no price reported)" : "(no dollar value reported)", isNote: true };
  }
  return { text: `(${side === "buy" ? "+" : "−"}${moneyCompact(n)})`, isNote: false };
}

/**
 * The hover text (and screen-reader text) of the "*" beside a company's trade count,
 * or null when every trade is in its dollar totals.
 * "2 reported no price; 4 left out of dollar totals (other currency or ...)".
 */
export function incompleteText(row: IndexCounts): string | null {
  const parts: string[] = [];
  if (row.trades_without_price > 0) parts.push(`${row.trades_without_price} reported no price`);
  if (row.trades_left_out > 0) parts.push(`${row.trades_left_out} left out of dollar totals (${LEFT_OUT_REASON})`);
  return parts.length ? parts.join("; ") : null;
}

/** The footnote under the ranking, naming only the reasons that occur on screen; null when none do. */
export function incompleteFootnote(rows: readonly IndexCounts[]): string | null {
  const noPrice = rows.some((r) => r.trades_without_price > 0);
  const leftOut = rows.some((r) => r.trades_left_out > 0);
  if (!noPrice && !leftOut) return null;
  const why = noPrice && leftOut
    ? `Some of these trades reported no price, and some are left out of dollar totals (${LEFT_OUT_REASON})`
    : noPrice
      ? "Some of these trades reported no price"
      : `Some of these trades are left out of dollar totals (${LEFT_OUT_REASON})`;
  return `* ${why}, so that company's dollar totals are incomplete.`;
}
