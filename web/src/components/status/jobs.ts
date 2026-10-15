/**
 * Plain-English descriptions of the worker's scheduled jobs, and how to read
 * their state and last-run numbers.
 *
 * The names and schedules mirror backend/src/smt/worker/jobs.py and
 * backend/src/smt/sources/edgar/jobs.py. The API does not send schedules, so
 * if a schedule changes there, change it here too. A job this file doesn't
 * know is still shown, under a readable form of its name, and its run numbers
 * are shown only when this file knows how to say them in words.
 */
import type { JobStatus } from "@/lib/api/types";

import { pricesText } from "./prices";
import { reconcileText } from "./reconcile";
import { duration } from "./time";

export interface JobInfo {
  name: string;
  what: string;
  schedule: string;
}

const EDGAR_HOURS = "Every minute from 6 AM to 10 PM ET on business days, every 10 minutes otherwise";
const EDGAR_HOURS_FAST = "Every 20 seconds from 6 AM to 10 PM ET on business days, every 10 minutes otherwise";

export const JOB_INFO: Record<string, JobInfo> = {
  "edgar.poll_ownership": {
    name: "Form 4 filings",
    what: "Checks EDGAR's list of the latest Form 4 filings (insider trades) and loads the new ones.",
    schedule: EDGAR_HOURS_FAST,
  },
  "edgar.poll_forms_3_5": {
    name: "Forms 3 and 5 feed",
    what: "Checks EDGAR's list of the latest Form 3 (new insider) and Form 5 (annual) filings and loads the new ones.",
    schedule: EDGAR_HOURS,
  },
  "edgar.poll_form144": {
    name: "Form 144 notices",
    what: "Checks EDGAR's list of the latest filings for notices of planned sales and loads the new ones.",
    schedule: EDGAR_HOURS,
  },
  "edgar.reach_back_form4": {
    name: "Form 4 catch-up",
    what: "Looks further back in EDGAR's list of Form 4 filings than the quick checks do, to load any filing they missed (a burst too big for one page, or a check that failed).",
    schedule: "Every minute from 6 AM to 10 PM ET on business days, every 10 minutes otherwise",
  },
  "edgar.reach_back_other": {
    name: "Forms 3, 5 and 144 catch-up",
    what: "The same catch-up for Forms 3 and 5 and Form 144 notices.",
    schedule: "Every 2 minutes from 6 AM to 10 PM ET on business days, every 10 minutes otherwise",
  },
  "edgar.process_queue": {
    name: "Filing queue",
    what: "Fetches, stores and reads each filing the checks found, and retries any that failed.",
    schedule: "Every 15 seconds",
  },
  "edgar.reconcile_daily_index": {
    name: "Nightly reconciliation",
    what: "Compares the SEC's official daily index with what we hold, and fetches anything the checks missed.",
    schedule: "11 PM ET every day",
  },
  "edgar.retry_daily_index": {
    name: "Daily index retry",
    what: "Checks again, every hour, any recent day whose daily index the SEC hadn't posted when the nightly reconciliation ran.",
    schedule: "Every hour",
  },
  "edgar.refresh_public_float": {
    name: "Company sizes",
    what: "Looks up the public float (the market value of shares not held by insiders) that companies with large trades report to the SEC, to catch trade values no company that size could have.",
    schedule: "Every 30 minutes, a batch at a time",
  },
  "edgar.fill_gaps": {
    name: "Missing dates",
    what: "Compares each business day that isn't fully loaded with the SEC's daily index, and queues the filings we don't hold, a batch at a time. A day counts as loaded when 99% of its filings are held.",
    schedule: "Every 10 minutes",
  },
  "edgar.load_new_dataset": {
    name: "Quarterly history",
    what: "Loads the SEC's newest quarterly data set of insider filings once it is published.",
    schedule: "3 AM ET every day",
  },
  "people.refresh_stats": {
    name: "People directory counts",
    what: "Recounts each person's open-market buys and sales for the People directory.",
    schedule: "Every 5 minutes",
  },
  "edgar.expire_planned_sales": {
    name: "Planned-sale follow-up",
    what: "Marks Form 144 notices that no Form 4 matched within 90 days as \u201cno Form 4 found\u201d.",
    schedule: "4 AM ET every day",
  },
  "quality.repair_amendments": {
    name: "Amendments",
    what: "Pairs each amended filing (such as a 4/A) with the rows it corrects, so a trade is never counted twice.",
    schedule: "4:30 AM ET every day",
  },
  "edgar.refresh_company_tickers": {
    name: "Ticker list",
    what: "Reloads the SEC's file that matches ticker symbols to companies.",
    schedule: "5:30 AM ET every day",
  },
  "edgar.verify_names": {
    name: "Seller names",
    what: "Replaces names typed by hand on Form 144 notices with the person's official EDGAR name.",
    schedule: "5:45 AM ET every day",
  },
  "edgar.refresh_submissions": {
    name: "Company profiles",
    what: "Refreshes companies' SEC profiles (industry code, former names) a batch at a time, and the sectors that come from them.",
    schedule: "6:15 AM ET every day",
  },
  "prices.refresh_daily": {
    name: "Daily prices",
    what: "Loads closing prices for the tracked tickers after the US market closes.",
    schedule: "6:30 PM ET on trading days",
  },
  "quality.flag_transactions": {
    name: "Figures that can't be right",
    what: "Flags values and dates that can't be right as filed, so they stay out of rankings and totals, and links a trade filed twice to the copy that's counted.",
    schedule: "Every 30 minutes",
  },
  "worker.watchdog": {
    name: "Watchdog",
    // Whether it also sends alerts depends on the setup: see jobInfo.
    what: "Looks for stale sources, a growing queue, filings set aside, stuck jobs and failed runs, and records each problem.",
    schedule: "Every 5 minutes",
  },
  "worker.heartbeat": {
    name: "Worker heartbeat",
    what: "Shows that the background worker, which does all the fetching, is running.",
    schedule: "Every minute",
  },
};

/** Display order: the live checks first, then the daily jobs by time, housekeeping last. Unknown jobs go at the end. */
const ORDER = [
  "edgar.poll_ownership",
  "edgar.poll_forms_3_5",
  "edgar.poll_form144",
  "edgar.reach_back_form4",
  "edgar.reach_back_other",
  "edgar.process_queue",
  "edgar.reconcile_daily_index",
  "edgar.retry_daily_index",
  "edgar.refresh_public_float",
  "edgar.fill_gaps",
  "edgar.load_new_dataset",
  "edgar.expire_planned_sales",
  "quality.repair_amendments",
  "edgar.refresh_company_tickers",
  "edgar.verify_names",
  "edgar.refresh_submissions",
  "prices.refresh_daily",
  "quality.flag_transactions",
  "people.refresh_stats",
  "worker.watchdog",
  "worker.heartbeat",
];

export function sortJobs(jobs: JobStatus[]): JobStatus[] {
  const rank = (name: string) => (ORDER.includes(name) ? ORDER.indexOf(name) : ORDER.length);
  return [...jobs].sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name));
}

/**
 * What the watchdog does with a problem, by the alert channel the API reports
 * (report.watchdog.alert_channel): "apprise" means a notification service is set up.
 */
function watchdogWhat(alertChannel: string | undefined): string {
  const base = "Looks for stale sources, a growing queue, filings set aside, stuck jobs and failed runs";
  if (alertChannel === "apprise") return `${base}, and sends the owner an alert when a problem starts and ends.`;
  if (alertChannel === "log") return `${base}, and writes each problem to the worker's log (no alert channel is set up).`;
  return JOB_INFO["worker.watchdog"]!.what;
}

/**
 * A job's name, description and schedule. A job this page doesn't describe keeps a
 * readable name ("edgar.new_job" -> "New job"), never the raw key. `alertChannel`
 * (from the watchdog report) makes the Watchdog's description say where problems go.
 */
export function jobInfo(name: string, alertChannel?: string): JobInfo {
  const known = JOB_INFO[name];
  if (known && name === "worker.watchdog") return { ...known, what: watchdogWhat(alertChannel) };
  if (known) return known;
  const words = (name.split(".").pop() ?? name).replaceAll("_", " ");
  return { name: words.charAt(0).toUpperCase() + words.slice(1), what: "", schedule: "–" };
}

/**
 * Swap job keys inside backend-written sentences for their plain names:
 * "did not finish (edgar.poll_form144)" -> "did not finish (Form 144 notices)".
 */
export function plainJobNames(text: string): string {
  return text.replace(/\b(?:edgar|worker|quality|prices|people)\.[a-z0-9_]+\b/g, (key) => jobInfo(key).name);
}

// ---------------------------------------------------------------- health

/** One job's state, always shown as glyph + words (+ color), never color alone. */
export type Health = "ok" | "running" | "stale" | "failed" | "partial" | "off" | "never" | "planned";

export const HEALTH: Record<Health, { glyph: string; word: string; className: string }> = {
  ok: { glyph: "✓", word: "On schedule", className: "text-paper" },
  running: { glyph: "◐", word: "Running now", className: "text-paper" },
  stale: { glyph: "!", word: "Behind schedule", className: "text-beacon" },
  failed: { glyph: "!", word: "Last run failed", className: "text-beacon" },
  // The latest run finished, but not all of its work did (prices: some tickers not updated).
  partial: { glyph: "~", word: "Ran, incomplete", className: "text-paper" },
  off: { glyph: "–", word: "Turned off", className: "text-dim" },
  never: { glyph: "○", word: "Hasn't run yet", className: "text-dim" },
  planned: { glyph: "○", word: "Not collected yet", className: "text-dim" },
};

export function healthOf(job: JobStatus): Health {
  if (!job.enabled) return "off";
  // "Behind schedule" wins over "running": a job catching up is still late.
  if (job.stale) return "stale";
  if (job.running) return "running";
  if (job.last_status === "failed") return "failed";
  // On time, but the latest run did only part of its work: not a plain tick.
  if (job.last_status === "partial") return "partial";
  if (!job.last_ok_at && !job.last_status) return "never";
  return "ok";
}

/** A short explanation under the state, e.g. "No success in over 26 h". */
export function healthDetail(job: JobStatus): string | null {
  if (job.stale && !job.last_ok_at) return "No successful run yet";
  if (job.stale && job.sla_seconds) return `No success in over ${duration(job.sla_seconds)}`;
  return null;
}

// ---------------------------------------------------------------- last run, in words

const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const n = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

/** "a, b and c". */
function listText(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** Only the non-zero counters, in words; "nothing new" when every counter was zero. */
function counters(stats: Record<string, unknown>, words: [key: string, text: (x: number) => string][]): string {
  const parts: string[] = [];
  let seen = 0;
  for (const [key, text] of words) {
    const value = n(stats[key]);
    if (value === null) continue;
    seen += 1;
    if (value !== 0) parts.push(text(value));
  }
  if (parts.length) return listText(parts);
  return seen ? "nothing new" : "";
}

/** The EDGAR checks and the queue share one set of counters (the checks prefix theirs with queue_). */
function queueText(stats: Record<string, unknown>): string {
  const pick = (key: string) => stats[key] ?? stats[`queue_${key}`];
  return counters(
    { discovered: stats.discovered, done: pick("done"), events: pick("events"), retry: pick("retry"), failed: pick("failed") },
    [
      ["discovered", (x) => `${count(x, "new filing")} found`],
      ["done", (x) => `${x.toLocaleString("en-US")} loaded`],
      ["events", (x) => `${x.toLocaleString("en-US")} sent to Live`],
      ["retry", (x) => `${x.toLocaleString("en-US")} to retry`],
      ["failed", (x) => `${x.toLocaleString("en-US")} failed`],
    ],
  );
}


const TICKER_OUTCOME: Record<string, string> = {
  not_modified: "the SEC's file hadn't changed",
  unchanged: "same file as last time",
  quarantined: "the file couldn't be read and was set aside for review",
};

/** Per-job wording. A job or counter not listed here shows nothing rather than an internal name. */
const JOB_STATS: Record<string, (stats: Record<string, unknown>) => string> = {
  "edgar.poll_ownership": queueText,
  "edgar.poll_forms_3_5": queueText,
  "edgar.poll_form144": queueText,
  "edgar.reach_back_form4": queueText,
  "edgar.reach_back_other": queueText,
  "edgar.fill_gaps": (stats) =>
    counters(stats, [
      ["checked", (x) => `${count(x, "day")} checked`],
      ["queued", (x) => `${count(x, "filing")} queued`],
      ["days_left", (x) => `${count(x, "business day")} not fully loaded`],
      ["waiting", (x) => `${count(x, "filing")} still waiting from earlier runs`],
    ]),
  "edgar.process_queue": queueText,
  "edgar.reconcile_daily_index": reconcileText,
  "edgar.refresh_public_float": (stats) =>
    counters(stats, [
      ["checked", (x) => `${count(x, "company", "companies")} checked`],
      ["found", (x) => `${x.toLocaleString("en-US")} with a reported float`],
    ]),
  "edgar.retry_daily_index": (stats) =>
    counters(stats, [["days_checked", (x) => `${count(x, "day")} checked again`]]) || "nothing to check again",
  "edgar.refresh_company_tickers": (stats) => {
    const outcome = typeof stats.outcome === "string" ? stats.outcome : null;
    if (outcome && TICKER_OUTCOME[outcome]) return TICKER_OUTCOME[outcome];
    return counters(stats, [
      ["tickers_seen", (x) => `${count(x, "ticker")} read`],
      ["issuers_new", (x) => count(x, "new company", "new companies")],
      ["securities_new", (x) => count(x, "new security", "new securities")],
    ]);
  },
  "edgar.refresh_submissions": (stats) =>
    counters(stats, [
      ["due", (x) => `${count(x, "company", "companies")} checked`],
      ["updated", (x) => `${x.toLocaleString("en-US")} updated`],
      ["not_found", (x) => `${x.toLocaleString("en-US")} not on EDGAR`],
      ["quarantined", (x) => `${x.toLocaleString("en-US")} set aside for review`],
      ["fetch_errors", (x) => `${x.toLocaleString("en-US")} couldn't be fetched`],
      ["securities_relabelled", (x) => `${count(x, "sector label")} updated`],
    ]),
  "edgar.verify_names": (stats) =>
    counters(stats, [
      ["refreshed_from_filings", (x) => `${count(x, "name")} corrected from the person's own filings`],
      ["from_filings", (x) => `${count(x, "name")} corrected from the person's own filings`],
      ["from_submissions", (x) => `${count(x, "name")} corrected from EDGAR`],
      ["unverified", (x) => `${count(x, "name")} left to check`],
      ["left_for_next_run", (x) => `${x.toLocaleString("en-US")} left for the next run`],
    ]),
  // Its own module, so Node's test runner can test it (prices.test.ts).
  "prices.refresh_daily": pricesText,
  "quality.flag_transactions": (stats) => {
    // Flags are recomputed from the current rules each run: new ones, and ones a corrected rule cleared.
    const fresh = n(stats.newly_flagged) ?? 0;
    const cleared = n(stats.flags_cleared) ?? 0;
    const held = counters(stats, [
      ["flagged_values", (x) => count(x, "value")],
      ["flagged_dates", (x) => count(x, "date")],
    ]);
    const parts: string[] = [];
    if (held && held !== "nothing new") parts.push(`${held} flagged in all`);
    parts.push(fresh ? `${count(fresh, "new flag")}` : "no new flags");
    if (cleared) parts.push(`${count(cleared, "flag")} cleared`);
    const dup = n(stats.duplicates);
    // A trade filed twice (a fund and its general partner) is shown on both filings and counted
    // once; the second copy is linked to the counted one, not set aside for review.
    if (dup) parts.push(`${count(dup, "second copy", "second copies")} linked to the counted trade`);
    return parts.join(", ");
  },
  "quality.repair_amendments": (stats) =>
    counters(stats, [
      ["amendments_checked", (x) => `${count(x, "amendment")} checked`],
      ["rows_paired", (x) => `${count(x, "row")} paired with the original`],
    ]),
  "edgar.expire_planned_sales": () => "",
  "worker.watchdog": (stats) => {
    const open = n(stats.still_open) ?? 0;
    const opened = n(stats.opened) ?? 0;
    const cleared = n(stats.cleared) ?? 0;
    const parts = [open ? `${count(open, "problem")} open` : "no open problems"];
    if (opened) parts.push(`${opened.toLocaleString("en-US")} new`);
    if (cleared) parts.push(`${cleared.toLocaleString("en-US")} cleared`);
    const errors = Array.isArray(stats.check_errors) ? stats.check_errors.length : 0;
    if (errors) parts.push(`${count(errors, "check")} couldn't run`);
    return parts.join(", ");
  },
  "worker.heartbeat": (stats) => (stats.alive === true ? "worker answered" : ""),
};

/**
 * A job's last-run numbers as a short phrase ("41 new filings found, 100
 * loaded and 250 sent to Live"). Jobs this page doesn't know get the common
 * counters only; internal keys and values are never printed.
 */
export function statsText(job: string, stats: Record<string, unknown>): string {
  const known = JOB_STATS[job];
  return known ? known(stats) : queueText(stats);
}

/** Shown instead of backend text that still reads like a log line. */
export const TECHNICAL_FALLBACK = "The technical details are in the worker's log.";

/**
 * True when a text holds something a reader shouldn't see: a web address, a
 * traceback, a dotted module path ("smt.net.client"), an exception class name
 * ("FetchError"), a machine name with a process id ("MYPC:27328") or a Windows
 * file path. Two-part job keys ("edgar.poll_form144") are allowed, because
 * plainJobNames swaps them for job names. The backend runs the same test before
 * sending (smt/worker/error_text.py, looks_technical); keep the two in step.
 */
export function looksTechnical(text: string | null | undefined): boolean {
  if (!text) return false;
  return /https?:\/\/|www\.|Traceback|\b[a-z_]\w*(?:\.[a-z_]\w*){2,}\b|\b_?[A-Z]\w*(?:Error|Exception|Throttled|Contention)\b|\b[A-Za-z][\w.-]*:\d{2,}\b|\b[A-Za-z]:\\/.test(
    text,
  );
}

/**
 * Why a job's latest run failed, as one line. The API already sends a plain
 * sentence (the full error stays in the worker's log); if an older or
 * unexpected value still reads like a log line, a plain fallback is shown.
 */
export function errorLine(error: string | null | undefined): string | null {
  if (!error) return null;
  const lines = error.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const last = lines[lines.length - 1];
  if (!last) return null;
  return looksTechnical(last) || lines.length > 1 ? TECHNICAL_FALLBACK : last;
}
