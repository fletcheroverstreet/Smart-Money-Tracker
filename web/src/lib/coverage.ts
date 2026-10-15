/**
 * Which filing dates the site actually holds (GET /api/meta/coverage), and
 * the honest wording when a page's date window runs over a hole in them.
 *
 * The local history of Forms 3, 4 and 5 has gaps: SEC business days whose
 * filings aren't loaded, or only partly (the backend compares each day with the
 * SEC's daily index and counts it as loaded at 99%). A count such as
 * "open-market buys in the last 90 days" is then too low, so the page must say
 * so instead of presenting it as complete.
 *
 * Dates are ISO strings ("2026-07-01"), which compare correctly as text.
 */
import type { DataCoverage } from "@/lib/api/types";

const md = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const mdy = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

function asDate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y!, (m ?? 1) - 1, d ?? 1));
}

/** The ISO date `days` calendar days before `iso`. */
export function daysBefore(iso: string, days: number): string {
  const date = asDate(iso);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

/** Today's date in New York, the SEC's clock. */
export function todayET(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}

/** "Jul 1 to Sep 2", "Sep 8 to 11", "Sep 8" (no year: the caller adds it once). */
function spanNoYear(first: string, last: string): string {
  if (first === last) return md.format(asDate(first));
  const sameMonth = first.slice(0, 7) === last.slice(0, 7);
  return `${md.format(asDate(first))} to ${sameMonth ? String(asDate(last).getUTCDate()) : md.format(asDate(last))}`;
}

/** "a", "a and b", "a, b and c". */
function listText(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

export interface MissingSpan {
  first: string;
  last: string;
  /** Some of those days' filings are held, but not all (usually still loading). */
  partial: boolean;
}

/** The gaps inside [from, to], cut to the window. Empty when nothing is missing (or coverage is unknown). */
export function missingIn(coverage: DataCoverage | null | undefined, from: string | null | undefined, to: string | null | undefined): MissingSpan[] {
  if (!coverage || !from || !to) return [];
  const out: MissingSpan[] = [];
  for (const gap of coverage.gaps) {
    if (gap.last < from || gap.first > to) continue;
    out.push({
      first: gap.first < from ? from : gap.first,
      last: gap.last > to ? to : gap.last,
      partial: gap.state === "partial",
    });
  }
  return out;
}

/** True when the window starts before the first filing date the site holds. */
function startsBeforeHistory(coverage: DataCoverage | null | undefined, from: string | null | undefined): boolean {
  return Boolean(coverage?.first_filed && from && from < coverage.first_filed);
}

/**
 * "Filings from Jul 1 to Aug 31, 2026 aren't loaded yet, and from Sep 14 to 15
 * only partly, so totals for this window are incomplete." Null when the window
 * is fully covered.
 */
export function coverageNote(
  coverage: DataCoverage | null | undefined,
  from: string | null | undefined,
  to: string | null | undefined,
  what = "totals for this window are",
): string | null {
  const spans = missingIn(coverage, from, to);
  const before = startsBeforeHistory(coverage, from) ? coverage!.first_filed! : null;
  if (!spans.length && !before) return null;

  // One year for the whole note when every date shares it ("Jul 1 to Aug 31 and Sep 3 to 4, 2026").
  const dates = [...spans.flatMap((s) => [s.first, s.last]), ...(before ? [before] : [])];
  const oneYear = dates.every((d) => d.slice(0, 4) === dates[0]!.slice(0, 4));
  const spanText = (s: MissingSpan) =>
    oneYear ? `from ${spanNoYear(s.first, s.last)}` : `from ${mdy.format(asDate(s.first))} to ${mdy.format(asDate(s.last))}`;
  const missing = [
    ...(before ? [`before ${oneYear ? md.format(asDate(before)) : mdy.format(asDate(before))}`] : []),
    ...spans.filter((s) => !s.partial).map(spanText),
  ];
  const partial = spans.filter((s) => s.partial).map(spanText);
  const year = oneYear ? `, ${dates[0]!.slice(0, 4)}` : "";
  if (!missing.length) return `Filings ${listText(partial)}${year} are only partly loaded so far, so ${what} incomplete.`;
  const also = partial.length ? `, and ${listText(partial)} only partly` : "";
  return `Filings ${listText(missing)}${year} aren't loaded yet${also}, so ${what} incomplete.`;
}

/** "Jan 2 to Sep 24, 2026, except Jul 1 to Aug 31 and Sep 14 to 15 (partly loaded)" for the Status page. */
export function datesHeldText(coverage: DataCoverage): string | null {
  if (!coverage.first_filed || !coverage.last_filed) return null;
  const held = `${mdy.format(asDate(coverage.first_filed))} to ${mdy.format(asDate(coverage.last_filed))}`;
  if (!coverage.gaps.length) return `${held}, with no gaps.`;
  const gaps = coverage.gaps.map((g) => {
    const span =
      g.first.slice(0, 4) === g.last.slice(0, 4) && g.first.slice(0, 4) === coverage.last_filed!.slice(0, 4)
        ? spanNoYear(g.first, g.last)
        : `${mdy.format(asDate(g.first))} to ${mdy.format(asDate(g.last))}`;
    return g.state === "partial" ? `${span} (partly loaded)` : span;
  });
  return `${held}, except ${listText(gaps)}.`;
}
