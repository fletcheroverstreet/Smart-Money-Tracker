/**
 * The late-disclosure flag and the filing-deadline rules in words.
 *
 * The flag is Dim with a ⚑ glyph and the word "Late", never the Beacon alert
 * color: it can sit on many rows at once, and it's a fact about timing, not an
 * alarm (neutral language, spec §12). No hooks, so it renders on the server too.
 */

/**
 * The deadline rule the backend applied (TradeRow.deadline_rule), in plain
 * words; null for a rule this page doesn't know, so no internal name is shown.
 *
 * Legal background: a Form 4 is due before the end of the second business day
 * after the trade (Exchange Act §16(a)). When the filing gives a "deemed
 * execution date" instead of a trade date, the deadline counts from that
 * date, so the check is only approximate.
 */
export function deadlineRuleText(rule: string | null | undefined): string | null {
  switch (rule) {
    case "form4_2bd":
      return "Form 4 is due by the end of the second business day after the trade.";
    case "form4_2bd_deemed_approx":
      return "Form 4 is due two business days after the trade. Approximate here: the filing gives a deemed execution date, not the trade date.";
    case "reported_on_form5":
      return "This trade should have been on a Form 4 within two business days but was reported on the annual Form 5.";
    default:
      return null;
  }
}

/** Where a "late" flag comes from, in words (a generic sentence for an unknown rule). */
export function lateRuleText(rule: string | null | undefined): string {
  return deadlineRuleText(rule) ?? "Filed after the legal deadline.";
}

export function LateFlag({ rule, className = "" }: { rule?: string | null; className?: string }) {
  return (
    <span className={`whitespace-nowrap text-dim ${className}`} title={lateRuleText(rule)}>
      <span aria-hidden>⚑ </span>Late<span className="sr-only"> disclosure</span>
    </span>
  );
}
