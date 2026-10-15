"use client";

import { type WatchItem, useWatchlist } from "@/lib/watchlist";

/** Follow / unfollow a ticker or person. The state is announced (aria-pressed), not just colored. */
export function WatchButton({ item }: { item: WatchItem }) {
  const { has, toggle } = useWatchlist();
  const watching = has(item.kind, item.id);
  return (
    <button
      type="button"
      aria-pressed={watching}
      onClick={() => toggle(item)}
      className={`inline-flex h-11 items-center gap-2 rounded-[var(--radius-control)] border px-3 md:h-9 ${
        watching ? "border-paper text-paper" : "border-rule-strong text-dim hover:text-paper"
      }`}
    >
      <span aria-hidden>{watching ? "★" : "☆"}</span>
      {watching ? "Watching" : "Watch"}
    </button>
  );
}
