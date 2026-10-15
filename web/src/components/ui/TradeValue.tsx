/**
 * A trade's dollar value, honest about filing typos and about what totals count.
 *
 * Some filings carry numbers that can't be right as filed (the backend flags
 * them, see backend/src/smt/ingest/quality.py): a total typed into the price
 * field, a misplaced decimal point, a trade dated after its own filing. The
 * numbers are kept exactly as filed, but a flagged value is shown as "Check
 * filing" instead of a dollar amount, and it is left out of every total and
 * ranking on the site. A price in another currency, or in one the filing
 * doesn't state, is not a mistake: it reads "Other currency" or "Currency not
 * stated", and is left out of dollar totals too.
 *
 * Some rows are listed but not counted at all (backend smt/api/queries.py,
 * COUNTED and PERSON_COUNTED):
 * - a second copy of a trade another filing already reports (a fund and its
 *   general partner each file the fund's sale; the same trade filed twice).
 *   Totals count the trade once, on the copy the row's `duplicate_of` names.
 * - an amendment row that couldn't be matched to the row it corrects: it may
 *   repeat that row, so it isn't counted ("amendment_unpaired").
 * Tables show those rows with the value in Dim ink and a short Dim tag, so a
 * table adds up to the figures next to it.
 *
 * The words and rules live in lib/dataIssues.ts, where they are tested; this
 * file only draws them.
 */
import { SignedAmount } from "@/components/ui/Direction";
import type { TradeRow } from "@/lib/api/types";
import {
  AMENDMENT_UNPAIRED,
  CURRENCY_UNSTATED,
  ISSUE_TEXT,
  PRICE_NOT_USD,
  VALUE_IMPLAUSIBLE,
  VALUE_LABEL,
  issueNotes,
  valueCell,
} from "@/lib/dataIssues";

// The codes and the plain rules live in lib/dataIssues.ts (tested there); pages import them from here.
export {
  AMENDMENT_UNPAIRED,
  CURRENCY_UNSTATED,
  DATE_AFTER_FILING,
  ISSUE_TEXT,
  PRICE_NOT_USD,
  VALUE_IMPLAUSIBLE,
  currencyUnstated,
  hasValueIssue,
  inOtherCurrency,
  priceShown,
  tradeAmount,
  valueText,
} from "@/lib/dataIssues";

/** Hover text for a second copy of a trade (a column on the row, not an issue code). */
export const DUPLICATE_TEXT =
  "Another filing reports the same trade (for example a fund and its general partner each reported it). Totals count it once.";

/** The fields a trade row needs to say whether the totals next to it count it. */
export type Countable = Pick<TradeRow, "is_current" | "is_open_market" | "in_totals" | "duplicate_of" | "owner">;

/**
 * Why an open-market buy or sell listed in a table isn't in the totals next to
 * it, in a few words; null when it's counted. Grants, option exercises, tax
 * withholding and the like are never in buy and sell totals (by design), so
 * they get no tag: marking every one of them would say nothing.
 */
export function notCountedText(t: Countable): string | null {
  // Only an explicit `false` means "not counted". A response from an API older
  // than the `in_totals` field has no value here at all, and reading that as
  // "not counted" would tag every buy and sell on the page.
  if (t.in_totals !== false || !t.is_open_market || !t.is_current) return null;
  const copy = t.duplicate_of;
  if (copy) {
    return copy.owner.slug === t.owner.slug
      ? "Also in another filing, counted once"
      : `Also reported by ${copy.owner.name}, counted once`;
  }
  return "Not in totals";
}

/** The Dim tag under a row that isn't counted (see notCountedText). Renders nothing for a counted row. */
export function NotCountedTag({ trade, className = "" }: { trade: Countable; className?: string }) {
  const text = notCountedText(trade);
  if (!text) return null;
  return (
    <span
      className={`block text-[12px] leading-4 text-dim ${className}`}
      title={trade.duplicate_of ? DUPLICATE_TEXT : ISSUE_TEXT[AMENDMENT_UNPAIRED]}
    >
      {text}
    </span>
  );
}

/**
 * The value cell. `counted={false}` shows the amount in Dim ink (the row is
 * listed but not in the totals, see NotCountedTag), keeping the +/− sign.
 * A missing or zero value reads "–", never $0 (tradeAmount).
 */
export function TradeValue({
  direction,
  value,
  issues,
  counted = true,
}: {
  direction: string | null | undefined;
  value: string | number | null | undefined;
  issues?: readonly string[] | null;
  counted?: boolean;
}) {
  const cell = valueCell(direction, value, issues, counted);
  if (cell.kind === "other_currency") {
    return (
      <span className="whitespace-nowrap text-dim" title={ISSUE_TEXT[PRICE_NOT_USD]}>
        {VALUE_LABEL.other_currency}
      </span>
    );
  }
  if (cell.kind === "currency_unstated") {
    return (
      <span className="whitespace-nowrap text-dim" title={ISSUE_TEXT[CURRENCY_UNSTATED]}>
        {VALUE_LABEL.currency_unstated}
      </span>
    );
  }
  if (cell.kind === "check_filing") {
    return (
      <span className="whitespace-nowrap text-dim" title={ISSUE_TEXT[VALUE_IMPLAUSIBLE]}>
        <span aria-hidden className="text-beacon">? </span>
        {VALUE_LABEL.check_filing}
      </span>
    );
  }
  if (cell.kind === "not_counted") {
    return (
      <span className="whitespace-nowrap text-dim" title="Not in totals (see the note on this row)">
        {cell.text}
      </span>
    );
  }
  return <SignedAmount direction={direction} text={cell.text} />;
}

/** The full explanation of a figure that can't be right (issueNotes), for the quick look. Renders nothing for a clean trade. */
export function DataIssueNotes({ issues }: { issues: readonly string[] | null | undefined }) {
  const box = issueNotes(issues);
  if (!box) return null;
  return (
    <div role="note" className="space-y-1 rounded-[var(--radius-block)] border border-beacon/50 bg-raised px-3 py-2">
      <p className="flex items-center gap-2 font-medium">
        <span aria-hidden className="text-beacon">?</span>
        {box.heading}
      </p>
      {box.notes.map((note) => (
        <p key={note.code} className="text-[13px] leading-[18px] text-dim">
          {note.text}
        </p>
      ))}
    </div>
  );
}
