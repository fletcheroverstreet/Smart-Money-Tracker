"use client";

/**
 * The watchlist lives in this browser (spec §8: "stored in the browser in v1,
 * no accounts"). Nothing is sent to the server.
 *
 * Every component using the hook stays in sync: changes are broadcast within
 * the tab (a custom event) and across tabs (the storage event).
 */
import { useCallback, useSyncExternalStore } from "react";

export type WatchKind = "ticker" | "person";

export interface WatchItem {
  kind: WatchKind;
  /** Ticker symbol or person slug. */
  id: string;
  label: string;
}

const KEY = "smt.watchlist";
const EVENT = "smt:watchlist";
const EMPTY: WatchItem[] = [];

let cachedRaw: string | null = null;
let cachedItems: WatchItem[] = EMPTY;

function read(): WatchItem[] {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(KEY);
  } catch {
    return EMPTY;
  }
  if (raw === cachedRaw) return cachedItems;
  cachedRaw = raw;
  try {
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    cachedItems = Array.isArray(parsed)
      ? parsed.filter(
          (x): x is WatchItem =>
            typeof x === "object" && x !== null && (x.kind === "ticker" || x.kind === "person") && typeof x.id === "string",
        )
      : EMPTY;
  } catch {
    cachedItems = EMPTY;
  }
  return cachedItems;
}

function write(items: WatchItem[]) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(items));
  } catch {
    /* storage full or blocked: the change lasts for this page view only */
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(callback: () => void) {
  window.addEventListener(EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

export function useWatchlist() {
  const items = useSyncExternalStore(subscribe, read, () => EMPTY);
  const has = useCallback((kind: WatchKind, id: string) => items.some((i) => i.kind === kind && i.id === id), [items]);
  const toggle = useCallback((item: WatchItem) => {
    const current = read();
    const exists = current.some((i) => i.kind === item.kind && i.id === item.id);
    write(exists ? current.filter((i) => !(i.kind === item.kind && i.id === item.id)) : [...current, item]);
  }, []);
  const remove = useCallback((kind: WatchKind, id: string) => {
    write(read().filter((i) => !(i.kind === kind && i.id === id)));
  }, []);
  return { items, has, toggle, remove };
}
