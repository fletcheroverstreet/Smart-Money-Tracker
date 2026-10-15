/**
 * Today's date in New York (YYYY-MM-DD), for feeds that label older rows with
 * their day. Read with useSyncExternalStore: the server render (and the
 * hydration pass that must match it) sees null, so nothing depends on which
 * machine's clock rendered the page. Re-checked every minute, so the labels
 * turn over at midnight Eastern while the page stays open.
 */
import { useSyncExternalStore } from "react";

import { isoDayET } from "./format";

function subscribe(onChange: () => void): () => void {
  const timer = window.setInterval(onChange, 60_000);
  return () => window.clearInterval(timer);
}

// A string snapshot: React only re-renders when the date itself changes.
const todaySnapshot = () => isoDayET(Date.now());
const serverSnapshot = () => null;

export function useTodayET(): string | null {
  return useSyncExternalStore(subscribe, todaySnapshot, serverSnapshot);
}
