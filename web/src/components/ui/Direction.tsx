/**
 * Buy / sell never rely on color alone (spec §9): every direction has a glyph
 * AND a word, and color is the third cue. Non-market codes show their short
 * label in neutral ink.
 */
export function Direction({
  direction,
  label,
  compact = false,
}: {
  direction: string | null | undefined;
  label?: string | null;
  compact?: boolean;
}) {
  if (direction === "buy")
    return (
      <span className="inline-flex items-center gap-1 text-inflow">
        <span aria-hidden>▲</span>
        {compact ? "Buy" : label ?? "Buy"}
      </span>
    );
  if (direction === "sell")
    return (
      <span className="inline-flex items-center gap-1 text-outflow">
        <span aria-hidden>▼</span>
        {compact ? "Sell" : label ?? "Sell"}
      </span>
    );
  if (direction === "planned_sale")
    return (
      <span className="inline-flex items-center gap-1 text-dim">
        <span aria-hidden>○</span>
        Plans to sell
      </span>
    );
  if (direction === "holding")
    return (
      <span className="inline-flex items-center gap-1 text-dim">
        <span aria-hidden>◇</span>
        New insider
      </span>
    );
  return <span className="text-dim">{label ?? "Other"}</span>;
}

/** A signed dollar amount: "+$494k" for buys, "−$1.2M" for sells, color plus sign. */
export function SignedAmount({ direction, text }: { direction: string | null | undefined; text: string }) {
  if (text === "–") return <span className="text-dim">–</span>;
  if (direction === "buy") return <span className="text-inflow">+{text}</span>;
  if (direction === "sell") return <span className="text-outflow">−{text}</span>;
  return <span>{text}</span>;
}
