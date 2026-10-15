"use client";

/**
 * Live updates from /api/stream (Server-Sent Events), per PLAN.md §7:
 *
 * - One EventSource per tab. Events arrive as `feed` (a new disclosure),
 *   `heartbeat` (every 15 s) or `hello`.
 * - Incoming items are BATCHED and handed over at most once a second, so a
 *   burst of filings never causes a re-render storm.
 * - Duplicate ids are dropped (the server's reconnect replay overlaps on purpose).
 * - Hidden tab: disconnect. Visible again: resync by fetching the newest page, then reconnect.
 * - The reader can pause live updates (WCAG 2.2.2); the choice is remembered.
 * - If the stream keeps failing, fall back to polling every 30 s and say so.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { apiGet } from "@/lib/api/client";
import type { FeedItem, FeedPage } from "@/lib/api/types";

export type ConnectionState = "connecting" | "live" | "reconnecting" | "polling" | "paused";

const BATCH_MS = 1000;
const POLL_MS = 30_000;
const GIVE_UP_STREAM_MS = 45_000;
const PAUSE_KEY = "smt.livePaused";

const PAUSE_EVENT = "smt:live-paused";

function readPaused(): boolean {
  try {
    return window.localStorage.getItem(PAUSE_KEY) === "1";
  } catch {
    return false;
  }
}

/** The pause choice is a stored preference, read like any external store (server: not paused). */
function subscribePaused(callback: () => void) {
  window.addEventListener(PAUSE_EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(PAUSE_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

export function useLiveFeed(onBatch: (items: FeedItem[]) => void) {
  const [state, setState] = useState<ConnectionState>("connecting");
  const [lastHeard, setLastHeard] = useState<number | null>(null);
  const paused = useSyncExternalStore(subscribePaused, readPaused, () => false);

  const seen = useRef(new Set<number>());
  const pending = useRef<FeedItem[]>([]);
  const onBatchRef = useRef(onBatch);
  const source = useRef<EventSource | null>(null);
  const failingSince = useRef<number | null>(null);
  const pollTimer = useRef<number | null>(null);

  useEffect(() => {
    onBatchRef.current = onBatch;
  }, [onBatch]);

  const accept = useCallback((items: FeedItem[]) => {
    for (const item of items) {
      if (!seen.current.has(item.id)) {
        seen.current.add(item.id);
        pending.current.push(item);
      }
    }
  }, []);

  /** Mark ids the page already shows, so a replay doesn't announce them again. */
  const markSeen = useCallback((items: FeedItem[]) => {
    for (const item of items) seen.current.add(item.id);
  }, []);

  const resync = useCallback(async () => {
    try {
      const page = await apiGet<FeedPage>("/api/feed", { limit: 50, codes: "all" });
      accept([...page.items].reverse());
      setLastHeard(Date.now());
    } catch {
      /* next attempt will retry */
    }
  }, [accept]);

  const stopPolling = () => {
    if (pollTimer.current !== null) window.clearInterval(pollTimer.current);
    pollTimer.current = null;
  };

  const disconnect = useCallback(() => {
    source.current?.close();
    source.current = null;
    stopPolling();
  }, []);

  // Opens the stream. The state already reads "connecting" on first mount;
  // later reconnects go through `reconnect`, which says so first.
  const connect = useCallback(() => {
    disconnect();
    const es = new EventSource("/api/stream");
    source.current = es;
    const heard = () => {
      setLastHeard(Date.now());
      failingSince.current = null;
      setState("live");
    };
    es.addEventListener("hello", heard);
    es.addEventListener("heartbeat", heard);
    es.addEventListener("feed", (event) => {
      heard();
      try {
        accept([JSON.parse((event as MessageEvent<string>).data) as FeedItem]);
      } catch {
        /* ignore a malformed event */
      }
    });
    es.onerror = () => {
      failingSince.current ??= Date.now();
      if (Date.now() - failingSince.current > GIVE_UP_STREAM_MS) {
        // The stream isn't coming back soon (a proxy or network issue): poll instead.
        es.close();
        source.current = null;
        setState("polling");
        void resync();
        pollTimer.current = window.setInterval(() => void resync(), POLL_MS);
      } else {
        setState("reconnecting"); // EventSource retries by itself
      }
    };
  }, [accept, disconnect, resync]);

  const reconnect = useCallback(async () => {
    setState("connecting");
    await resync();
    connect();
  }, [connect, resync]);

  // Flush batched items at most once a second.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (pending.current.length) {
        const batch = pending.current;
        pending.current = [];
        onBatchRef.current(batch);
      }
    }, BATCH_MS);
    return () => window.clearInterval(timer);
  }, []);

  // Connect on mount; follow pause state and tab visibility.
  useEffect(() => {
    if (!readPaused()) connect();
    const onVisibility = () => {
      if (readPaused()) return;
      if (document.hidden) {
        disconnect();
      } else {
        void reconnect();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      disconnect();
    };
  }, [connect, disconnect, reconnect]);

  const setPaused = useCallback(
    (next: boolean) => {
      try {
        window.localStorage.setItem(PAUSE_KEY, next ? "1" : "0");
      } catch {
        /* storage unavailable: the choice isn't remembered */
      }
      window.dispatchEvent(new Event(PAUSE_EVENT));
      if (next) {
        disconnect();
      } else {
        void reconnect();
      }
    },
    [disconnect, reconnect],
  );

  return { state: paused ? ("paused" as const) : state, lastHeard, paused, setPaused, markSeen };
}
