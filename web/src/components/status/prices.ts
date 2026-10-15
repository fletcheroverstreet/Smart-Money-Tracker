/**
 * The daily prices job's line on the Status page, from its run stats (backend
 * smt/prices/jobs.py: RefreshReport.as_stats, plus mostly_stale).
 *
 * Plain functions with no React or "@/" imports, so Node's own test runner can
 * test them (prices.test.ts).
 */

const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** "a, b and c". */
function listText(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/**
 * The sum of a per-provider count ({"finlake": 400, "yfinance": 12}); a plain number as it is;
 * null when it is neither.
 */
export function providerTotal(value: unknown): number | null {
  const plain = num(value);
  if (plain !== null) return plain;
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return Object.values(value).reduce<number>((sum, x) => sum + (num(x) ?? 0), 0);
}

/**
 * "4,887 tickers checked, 0 up to date, 659 behind and 4,228 with no prices available; most
 * prices are out of date".
 *
 * The run's "current" is counted per provider, as an object ({"finlake": 400}, or {} when
 * nothing is up to date: RefreshReport.current), so it is summed, never read as a number.
 * It is the figure the run is judged by, so it is given whenever tickers were checked,
 * "0 up to date" too. Runs 24114 and 24590 (Oct 2026) stored current {} with 4,792 and
 * 4,887 targets, ran "partial", and read "4,887 tickers checked, 659 behind and 4,228 with
 * no prices available" with neither the up-to-date count nor the reason they were partial.
 * The backend marks a run whose prices are mostly behind (mostly_stale: nothing up to
 * date, or more behind than up to date) as partial; the line says so in words.
 */
export function pricesText(stats: Record<string, unknown>): string {
  if (typeof stats.skipped === "string") return "no price source is set up";
  const targets = num(stats.targets);
  const current = providerTotal(stats.current);
  const parts: string[] = [];
  if (targets) {
    parts.push(`${count(targets, "ticker")} checked`);
    if (current !== null) parts.push(`${current.toLocaleString("en-US")} up to date`);
  }
  const others: [key: string, words: string][] = [
    ["reloaded", "reloaded"],
    ["stale", "behind"],
    ["unavailable", "with no prices available"],
    ["failed", "failed"],
  ];
  for (const [key, words] of others) {
    const x = num(stats[key]);
    if (x) parts.push(`${x.toLocaleString("en-US")} ${words}`);
  }
  const text = parts.length ? listText(parts) : targets !== null ? "nothing new" : "";
  const notes: string[] = [];
  if (stats.mostly_stale === true) notes.push("most prices are out of date");
  if (stats.stopped_early === true) notes.push("stopped early to respect the provider's limits");
  return notes.length ? [text || "nothing loaded", ...notes].join("; ") : text;
}
