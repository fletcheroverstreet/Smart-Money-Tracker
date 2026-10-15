/**
 * Number, date and delay formatting. Copy is written from the reader's side
 * ("Filed 2 days after the trade"), in sentence case (spec §9).
 *
 * The API sends money and share amounts as decimal STRINGS (exact values), so
 * everything here accepts string | number and converts carefully.
 */

const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const dec = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });

type Num = string | number | null | undefined;

export function toNumber(value: Num): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** "$494k", "$2.1M", "$1.2bn": compact money for dense tables. Missing is an en dash, never $0. */
export function moneyCompact(value: Num): string {
  const n = toNumber(value);
  if (n === null) return "–";
  const abs = Math.abs(n);
  const sign = n < 0 ? "−" : "";
  // Thresholds sit just below each unit, so rounding can't print "$1000k" or "$1000M".
  if (abs >= 999_500_000) return `${sign}$${(abs / 1e9).toFixed(abs >= 9.95e9 ? 0 : 1)}bn`;
  if (abs >= 999_500) return `${sign}$${(abs / 1e6).toFixed(abs >= 9.95e6 ? 0 : 1)}M`;
  if (abs >= 1e3) return `${sign}$${Math.round(abs / 1e3)}k`;
  return `${sign}${usd0.format(abs)}`;
}

export function money(value: Num): string {
  const n = toNumber(value);
  return n === null ? "–" : usd0.format(n);
}

const usdSmall = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 });

/** A per-share price. Under $1, up to 4 decimals, so a $0.0095 price doesn't read as "$0.01". */
export function price(value: Num): string {
  const n = toNumber(value);
  if (n === null) return "–";
  return Math.abs(n) < 1 && n !== 0 ? usdSmall.format(n) : usd2.format(n);
}

const plain = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });

/** A figure in some other currency: the number as filed, with no "$" (e.g. New Taiwan dollars). */
export function plainAmount(value: Num): string {
  const n = toNumber(value);
  return n === null ? "–" : plain.format(n);
}

export function shares(value: Num): string {
  const n = toNumber(value);
  if (n === null) return "–";
  return Number.isInteger(n) ? int.format(n) : dec.format(n);
}

const dateFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const timeEt = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" });
const dateTimeEt = new Intl.DateTimeFormat("en-US", {
  month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/New_York",
});

/** "Sep 17, 2026" for a YYYY-MM-DD date (dates are calendar dates, never shifted by timezone). */
export function day(value: string | null | undefined): string {
  if (!value) return "–";
  const [y, m, d] = value.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return value;
  return dateFmt.format(new Date(Date.UTC(y, m - 1, d)));
}

/** "4:31 PM ET" for an ISO timestamp. EDGAR runs on Eastern time. */
export function timeET(value: string | null | undefined): string {
  if (!value) return "–";
  return `${timeEt.format(new Date(value))} ET`;
}

export function dateTimeET(value: string | null | undefined): string {
  if (!value) return "–";
  return `${dateTimeEt.format(new Date(value))} ET`;
}

// en-CA formats as YYYY-MM-DD, so the Eastern calendar date compares as a plain string.
const isoDayEt = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "America/New_York" });
const shortDayEt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "America/New_York" });
const fullEt = new Intl.DateTimeFormat("en-US", {
  weekday: "long", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  timeZone: "America/New_York",
});

/** The Eastern calendar date ("2026-09-24") of a moment. EDGAR's day is New York's day. */
export function isoDayET(value: string | number | Date): string {
  return isoDayEt.format(new Date(value));
}

/** "Thursday, Sep 24, 2026, 11:41 PM ET": the complete moment, for titles and screen readers. */
export function fullDateTimeET(value: string | null | undefined): string {
  if (!value) return "–";
  return `${fullEt.format(new Date(value))} ET`;
}

/**
 * The day part to show beside a clock time in a feed that spans days: null for
 * today (the time alone is clear), "Yesterday", or "Sep 24". `today` is the
 * Eastern date as YYYY-MM-DD, or null when the clock isn't known yet (server
 * render), in which case the date is always shown.
 */
export function dayLabelET(value: string, today: string | null): string | null {
  const dayOf = isoDayET(value);
  if (today !== null) {
    if (dayOf === today) return null;
    const [y, m, d] = today.split("-").map(Number);
    const yesterday = new Date(Date.UTC(y!, m! - 1, d! - 1)).toISOString().slice(0, 10);
    if (dayOf === yesterday) return "Yesterday";
  }
  return shortDayEt.format(new Date(value));
}

/** "Filed 2 days after the trade" / "Filed the same day". Disclosures give a trade DATE, so days only. */
export function delayText(days: number | null | undefined): string {
  if (days === null || days === undefined) return "Delay unknown";
  if (days <= 0) return "Filed the same day";
  if (days === 1) return "Filed 1 day after the trade";
  return `Filed ${days} days after the trade`;
}

export function delayShort(days: number | null | undefined): string {
  if (days === null || days === undefined) return "–";
  return days <= 0 ? "same day" : `${days} d`;
}

/** "12 s ago", "3 min ago", "2 h ago" */
export function ago(fromMs: number, nowMs: number): string {
  const s = Math.max(0, Math.round((nowMs - fromMs) / 1000));
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  return `${Math.round(m / 60)} h ago`;
}

export function pct(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "–" : `${value.toFixed(digits)}%`;
}

/** "Spouse's account", "Indirect", "Direct": plain owner-type wording. */
export function ownerTypeText(ownerType: string | null | undefined): string {
  switch (ownerType) {
    case "spouse":
      return "Spouse's account";
    case "joint":
      return "Joint account";
    case "dependent":
      return "Dependent child";
    case "indirect":
      return "Indirect";
    default:
      return "Direct";
  }
}
