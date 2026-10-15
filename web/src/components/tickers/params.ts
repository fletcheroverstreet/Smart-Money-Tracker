/**
 * URL parameters for the ticker pages, shared by the server pages (first
 * render) and the client components (changes), so both read the URL the same
 * way. Every filter lives in the URL, so any view is a shareable link.
 *
 *   /tickers?days=90&sort=net&q=sn
 *   /tickers/SNOW?range=all&codes=all
 */

// ---------------------------------------------------------------- ticker index (/tickers)

/** The windows offered on the index page, in days. */
export const INDEX_DAYS = [
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
  { value: 365, label: "1 year" },
] as const;

export type IndexDays = (typeof INDEX_DAYS)[number]["value"];

export function daysFrom(raw: string | null | undefined): IndexDays {
  const n = Number(raw);
  return n === 90 || n === 365 ? n : 30;
}

/** Server-side rankings (the API sorts; every ranking is largest first). */
export type IndexSort = "activity" | "buys" | "sells" | "net";

export function sortFrom(raw: string | null | undefined): IndexSort {
  return raw === "buys" || raw === "sells" || raw === "net" ? raw : "activity";
}

/** The prefix filter: trimmed, at most 40 characters (the API's limit). */
export function queryFrom(raw: string | null | undefined): string {
  return (raw ?? "").trim().slice(0, 40);
}

/** Identifies one view of the index, so the server's first page is reused only for that same view. */
export function indexKey(days: IndexDays, sort: IndexSort, q: string): string {
  return `${days}|${sort}|${q.toUpperCase()}`;
}

// ---------------------------------------------------------------- ticker page (/tickers/[symbol])

export const RANGES = [
  { value: "3m", label: "3M", long: "the last 3 months" },
  { value: "1y", label: "1Y", long: "the last year" },
  { value: "all", label: "All", long: "all stored history" },
] as const;

export type Range = (typeof RANGES)[number]["value"];

export function rangeFrom(raw: string | null | undefined): Range {
  return raw === "3m" || raw === "all" ? raw : "1y";
}

/** "2026-09-24" for a Date, as a calendar date (UTC fields, so no timezone shift). */
export function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** First day shown for a range, as YYYY-MM-DD; null means "no lower limit". */
export function rangeStart(range: Range, today: Date = new Date()): string | null {
  if (range === "all") return null;
  const start = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
  if (range === "3m") start.setUTCMonth(start.getUTCMonth() - 3);
  else start.setUTCFullYear(start.getUTCFullYear() - 1);
  return isoDay(start);
}

/**
 * Which Form 4 transaction codes the insider table shows.
 * "P,S" (the default) = open-market purchases and sales only.
 */
export const CODE_FILTERS = [
  { value: "P,S", label: "Buys and sells" },
  { value: "P", label: "Buys" },
  { value: "S", label: "Sells" },
  { value: "all", label: "All codes" },
] as const;

export type CodesFilter = (typeof CODE_FILTERS)[number]["value"];

export function codesFrom(raw: string | null | undefined): CodesFilter {
  const upper = (raw ?? "").toUpperCase();
  if (upper === "P" || upper === "S") return upper;
  if (upper === "ALL") return "all";
  return "P,S";
}
