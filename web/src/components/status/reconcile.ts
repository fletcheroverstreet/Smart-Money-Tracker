/**
 * The nightly reconciliation job's line on the Status page, from its run stats
 * (backend sources/edgar/jobs.py, _reconcile_day: one entry per day it checked).
 *
 * Plain functions with no React or "@/" imports, so Node's own test runner can
 * test them (reconcile.test.ts).
 */

const dayFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

/** "Sep 23" for "2026-09-23"; null if the key isn't a date. */
function dateKey(key: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
  if (!m) return null;
  return dayFmt.format(new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))));
}

/**
 * Why a day's index couldn't be checked (backend sources/edgar/jobs.py, _reconcile_day).
 * Only a 404 means the SEC hasn't posted it yet; any other failure means EDGAR didn't
 * answer, with the HTTP status of the last try when there was one (a 503, say).
 * Runs recorded before the status was kept say only "index not available", and that was
 * always a failure to answer, never a 404.
 */
function indexProblem(v: { error?: string; http_status?: number | null }): string {
  if (v.error === "not posted" || v.http_status === 404 || v.http_status === 410) return "index not posted yet";
  if (typeof v.http_status === "number") return `EDGAR didn't answer (HTTP ${v.http_status})`;
  return "EDGAR didn't answer";
}

/**
 * One entry per day the job checked, e.g. "Sep 28: 468 in the index, 155 not held yet".
 *
 * The job's "missing" is every filing in the index we didn't hold when it ran, including
 * ones the live feed had already queued but not yet loaded. The reconciliation table
 * counts only what the check itself found (and splits it by whether the worker was
 * running), so on Sep 28 the job's 155 was the table's 148 plus 7 filings the feed had
 * queued minutes earlier. The job line therefore says "not held yet", not "missing",
 * which read as a different count of the same thing.
 */
export function reconcileText(stats: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(stats)) {
    const day = dateKey(key);
    if (!day || !value || typeof value !== "object") continue;
    const v = value as { expected?: number; missing?: number; error?: string; http_status?: number | null };
    if (v.error) parts.push(`${day}: ${indexProblem(v)}`);
    else if (typeof v.expected === "number") {
      const missing = v.missing ?? 0;
      parts.push(
        `${day}: ${v.expected.toLocaleString("en-US")} in the index, ` +
          (missing ? `${missing.toLocaleString("en-US")} not held yet` : "all held"),
      );
    }
  }
  return parts.join("; ");
}
