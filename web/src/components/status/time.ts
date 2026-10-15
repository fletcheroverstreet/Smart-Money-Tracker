/**
 * Time helpers for the Status page.
 *
 * Relative times ("3 min ago") depend on the clock, so they are only shown
 * after the page has loaded in the browser. The server renders absolute
 * Eastern times, which are identical on server and client (no hydration
 * mismatch).
 */
import { useEffect, useState } from "react";

/** The current time, ticking every `intervalMs`. Null during server render and the first client render. */
export function useNow(intervalMs = 1000): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    // First tick on the next task, so the effect itself never sets state synchronously.
    const first = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, intervalMs);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [intervalMs]);
  return now;
}

/** A length of time in words: "45 s", "12 min", "1.9 h", "3 days". Missing is an en dash. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  const s = Math.max(0, seconds);
  if (s < 90) return `${Math.round(s)} s`;
  const minutes = s / 60;
  if (minutes < 90) return `${Math.round(minutes)} min`;
  const hours = minutes / 60;
  if (hours < 48) return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} h`;
  return `${Math.round(hours / 24)} days`;
}

/**
 * A total of stopped time within filing hours, in words: "45 min", "6.5 hours", "58 hours".
 * Never whole days: a filing day is 16 hours long, so 58 hours of it is most of four
 * filing days, and duration() would call it "2 days".
 */
export function hoursTotal(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  const s = Math.max(0, seconds);
  if (s < 5400) return duration(s);
  const hours = s / 3600;
  return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} hours`;
}

/**
 * A length of time for a "within" claim ("95% within 2 min 15 s"): always rounded UP,
 * so the claim stays true. duration() rounds to the nearest unit, which would show
 * 135 s as "2 min" and make a missed 2-minute goal read as met.
 *
 *   75.2 s -> "76 s"    94 s -> "1 min 34 s"    120 s -> "2 min"    120.4 s -> "2 min 1 s"
 *   135 s -> "2 min 15 s"    888 s -> "15 min"    100 min -> "1.7 h"
 *
 * Under 10 minutes it shows minutes and seconds, so a figure just over a goal
 * counted in minutes never looks equal to it.
 */
export function durationWithin(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  const s = Math.ceil(Math.max(0, seconds));
  if (s < 90) return `${s} s`;
  if (s < 600) {
    const rest = s % 60;
    return rest ? `${Math.floor(s / 60)} min ${rest} s` : `${s / 60} min`;
  }
  const minutes = Math.ceil(s / 60);
  if (minutes < 90) return `${minutes} min`;
  const hours = Math.ceil(minutes / 6) / 10; // tenths of an hour, rounded up
  if (hours < 48) return `${hours < 10 ? hours.toFixed(1) : Math.ceil(hours)} h`;
  return `${Math.ceil(hours / 24)} days`;
}

/** "3 min ago" for a past ISO time, "in 42 s" for a future one, "now" within a second. */
export function relative(iso: string | null | undefined, now: number | null): string | null {
  if (!iso || now === null) return null;
  const diff = (Date.parse(iso) - now) / 1000;
  if (!Number.isFinite(diff)) return null;
  if (Math.abs(diff) < 1) return "now";
  return diff > 0 ? `in ${duration(diff)}` : `${duration(-diff)} ago`;
}

/** Seconds between two ISO times (b − a), or null if either is missing. */
export function secondsBetween(a: string | null | undefined, b: string | null | undefined): number | null {
  if (!a || !b) return null;
  const diff = (Date.parse(b) - Date.parse(a)) / 1000;
  return Number.isFinite(diff) ? diff : null;
}
