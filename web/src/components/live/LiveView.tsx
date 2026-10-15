"use client";

/**
 * The Live page's moving parts: the downlink band, the filter bar, the feed
 * table and the watchlist panel.
 *
 * How new filings arrive (PLAN.md §7):
 * 1. useLiveFeed hands over batches from the SSE stream at most once a second.
 * 2. Items that pass the current filters appear in the downlink band (the only
 *    animation on the site) and wait in a buffer.
 * 3. The table never shifts under the reader: a "N new" pill merges the buffer
 *    when the reader asks for it.
 *
 * Filters live in the URL (?codes=all&type=planned_sale&min=1000000), so every
 * view is a shareable link. The server renders the first page for those filters.
 */
import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useOpenEvent, useOpenTrade } from "@/components/drawer/quickLookNav";
import { SearchButton } from "@/components/search/CommandPalette";
import { useNow } from "@/components/status/time";
import { Direction } from "@/components/ui/Direction";
import { Segmented } from "@/components/ui/Segmented";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/States";
import { apiGet } from "@/lib/api/client";
import type { FeedItem, FeedPage } from "@/lib/api/types";
import { ago, delayText } from "@/lib/format";
import { type ConnectionState, useLiveFeed } from "@/lib/useLiveFeed";
import { prefersReducedMotion, useReducedMotion } from "@/lib/useMediaQuery";
import { useTodayET } from "@/lib/useTodayET";
import { useWatchlist } from "@/lib/watchlist";

import { DetectedTime } from "./DetectedTime";
import { FeedTable, SizeCell } from "./FeedTable";
import { type LiveFilters, feedParams, filtersFromParams, filtersToQuery } from "./filters";
import { type FeedRowView, matchesFilters, viewOf } from "./feedRow";

// ---------------------------------------------------------------- connection status

const STATE_TEXT: Record<ConnectionState, string> = {
  connecting: "Connecting",
  live: "Live",
  reconnecting: "Reconnecting",
  polling: "Checking every 30 s",
  paused: "Paused",
};

function ConnectionStatus({ state, lastHeard }: { state: ConnectionState; lastHeard: number | null }) {
  const now = useNow(1000);
  const dot =
    state === "live"
      ? "bg-inflow"
      : state === "paused"
        ? "bg-dim"
        : state === "polling"
          ? "bg-beacon"
          : "bg-dim animate-pulse motion-reduce:animate-none";
  return (
    <div className="flex items-center gap-2 text-[13px] text-dim">
      {/* The visible line re-renders every second ("updated X ago"), so it is NOT a live region. */}
      <p aria-hidden className="flex items-center gap-2">
        <span className={`inline-block size-2 rounded-full ${dot}`} />
        <span className="text-paper">{STATE_TEXT[state]}</span>
        {lastHeard && now ? <span className="num">(updated {ago(lastHeard, now)})</span> : null}
      </p>
      {/* Screen readers hear only a change of connection state, once. */}
      <p role="status" className="sr-only">
        Live updates: {STATE_TEXT[state]}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- downlink band

/** How long a new arrival keeps its static "New" marker when motion is reduced (design sheet §4). */
const NEW_MARKER_MS = 60_000;

function DownlinkBand({
  arrivals,
  state,
  paused,
  onTogglePause,
  onOpen,
}: {
  arrivals: Arrival[];
  state: ConnectionState;
  paused: boolean;
  onTogglePause: () => void;
  onOpen: (row: FeedRowView) => void;
}) {
  // With reduced motion there is no arrival animation, so new rows carry a word instead.
  const reduced = useReducedMotion();
  const now = useNow(5000);
  const today = useTodayET();
  const isNew = (row: Arrival) => reduced && now !== null && now - row.arrivedAt < NEW_MARKER_MS;
  return (
    <section aria-labelledby="downlink-title" className="mx-4 mt-5 rounded-[var(--radius-panel)] border border-hairline bg-console md:mx-6">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hairline px-4 py-2">
        <h2 id="downlink-title" className="font-display text-[18px] font-medium leading-6 tracking-wide">
          Downlink
        </h2>
        <div className="flex items-center gap-3">
          <span className="num text-[13px] text-dim">
            {arrivals.length ? `${arrivals.length} arrived this visit` : null}
          </span>
          {/* A toggle keeps one label; aria-pressed (and the fill) says whether it's on. A label
              that flipped to "Resume live" would be read as "Resume live, pressed". */}
          <button
            type="button"
            onClick={onTogglePause}
            aria-pressed={paused}
            className={`inline-flex h-11 items-center gap-1.5 rounded-[var(--radius-control)] border border-rule-strong px-2.5 text-[13px] md:h-8 ${
              paused ? "bg-raised text-paper shadow-[inset_0_-2px_0_var(--color-paper)]" : "hover:bg-raised"
            }`}
          >
            <span aria-hidden>❚❚</span>
            Pause live
          </button>
        </div>
      </div>
      {/* Screen readers hear new arrivals politely, one summary per batch. */}
      <ol aria-live="polite" aria-relevant="additions" className="min-h-[120px]">
        {arrivals.length === 0 ? (
          <li className="px-4 py-4 text-dim">
            {paused
              ? "Live updates are paused. The table below still works; press Pause live again to resume."
              : state === "polling"
                ? "The live stream is unavailable, so the page checks for new filings every 30 seconds."
                : "Listening to EDGAR. New insider filings land here about a minute after the SEC publishes them."}
          </li>
        ) : (
          arrivals.slice(0, 3).map((row) => (
            <li key={row.id} className="downlink-row relative">
              <button
                type="button"
                onClick={() => onOpen(row)}
                className="num grid w-full grid-cols-[64px_minmax(0,1fr)_auto] items-center gap-3 px-4 py-2 text-left text-[13px] hover:bg-raised md:grid-cols-[72px_44px_120px_56px_minmax(0,1fr)_112px_200px]"
              >
                <span className="text-dim">
                  <DetectedTime iso={row.detectedAt} today={today} />
                </span>
                <span className="hidden font-mono text-[12px] text-dim md:inline">{row.form}</span>
                <span className="hidden md:inline">
                  <Direction direction={row.direction} label={row.actionLabel} />
                </span>
                <span className="hidden font-medium md:inline">{row.ticker ?? "–"}</span>
                <span className="min-w-0 truncate">
                  {isNew(row) ? (
                    <span className="mr-1.5 rounded-[var(--radius-chip)] border border-rule-strong px-1 text-[12px] leading-4 text-paper">
                      New
                    </span>
                  ) : null}
                  <span className="md:hidden">
                    <Direction direction={row.direction} label={row.actionLabel} /> {row.ticker ? `${row.ticker}: ` : ""}
                  </span>
                  {row.who}
                  {row.role ? <span className="text-dim">, {row.role}</span> : null}
                </span>
                {/* The same cell as the grid below: "Other currency" there never reads "Check filing" here. */}
                <span className="text-right">
                  <SizeCell row={row} />
                </span>
                <span className="downlink-readout hidden text-right text-dim md:inline">
                  {row.isPlan ? "Notice of a planned sale" : row.eventType === "insider_holding" ? "Initial statement" : delayText(row.delayDays)}
                </span>
              </button>
              <span
                aria-hidden
                className={`downlink-carrier absolute inset-x-0 bottom-0 h-px ${
                  row.direction === "buy" ? "bg-inflow-mark" : row.direction === "sell" ? "bg-outflow-mark" : "bg-neutral-mark"
                }`}
              />
            </li>
          ))
        )}
      </ol>
    </section>
  );
}

// ---------------------------------------------------------------- watchlist panel

function WatchlistPanel({ rows }: { rows: FeedRowView[] }) {
  const { items, remove } = useWatchlist();
  const counts = useMemo(() => {
    const byKey = new Map<string, number>();
    for (const row of rows) {
      if (row.ticker) byKey.set(`ticker:${row.ticker}`, (byKey.get(`ticker:${row.ticker}`) ?? 0) + 1);
      if (row.whoSlug) byKey.set(`person:${row.whoSlug}`, (byKey.get(`person:${row.whoSlug}`) ?? 0) + 1);
    }
    return byKey;
  }, [rows]);

  return (
    // tabIndex -1: the "skip past the table" link lands here.
    <aside
      id="watchlist"
      tabIndex={-1}
      aria-labelledby="watch-title"
      className="rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3"
    >
      <h2 id="watch-title" className="font-display text-[18px] font-medium leading-6">
        Watchlist
      </h2>
      {items.length === 0 ? (
        <p className="mt-2 text-[13px] leading-[18px] text-dim">
          Follow a ticker or a person with the Watch button on their page. Your list stays in this browser; nothing is
          sent anywhere.
        </p>
      ) : (
        <ul className="mt-2 space-y-2">
          {items.map((item) => {
            const n = counts.get(`${item.kind}:${item.id}`) ?? 0;
            return (
              <li key={`${item.kind}:${item.id}`} className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <Link
                    href={item.kind === "ticker" ? `/tickers/${item.id}` : `/people/${item.id}`}
                    className={`${item.kind === "ticker" ? "font-medium" : ""} hover:underline`}
                  >
                    {item.label}
                  </Link>
                  <p className="num text-[13px] text-dim">
                    {n === 0 ? "Nothing in the feed" : `${n} in the feed`}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => remove(item.kind, item.id)}
                  aria-label={`Remove ${item.label} from the watchlist`}
                  className="flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-control)] text-dim hover:bg-raised hover:text-paper md:size-8"
                >
                  <svg aria-hidden width="12" height="12" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="1.5">
                    <path d="M3 3l10 10M13 3L3 13" />
                  </svg>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-3 border-t border-hairline pt-2 text-[12px] leading-4 text-dim">
        Quotes aren&apos;t shown on the public site: no free price source allows it. See{" "}
        <Link href="/sources" className="underline underline-offset-2">
          Sources
        </Link>
        .
      </p>
    </aside>
  );
}

// ---------------------------------------------------------------- the view

/** A Downlink row, with the time it reached this page (for the reduced-motion "New" marker). */
type Arrival = FeedRowView & { arrivedAt: number };

export function LiveView({ initial, initialFilters }: { initial: FeedPage | null; initialFilters: LiveFilters }) {
  const router = useRouter();
  const params = useSearchParams();
  const filters = filtersFromParams(params);
  const openTrade = useOpenTrade();
  const openEvent = useOpenEvent();
  const sameAsServer =
    filters.codes === initialFilters.codes && filters.type === initialFilters.type && filters.min === initialFilters.min;

  const feed = useInfiniteQuery({
    queryKey: ["feed", filters],
    queryFn: ({ pageParam }) => apiGet<FeedPage>("/api/feed", feedParams(filters, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    initialData: sameAsServer && initial ? { pages: [initial], pageParams: [null] } : undefined,
  });

  const [incoming, setIncoming] = useState<FeedItem[]>([]);
  const [arrivals, setArrivals] = useState<Arrival[]>([]);

  const onBatch = useCallback(
    (items: FeedItem[]) => {
      const wanted = items.filter((i) =>
        matchesFilters(
          i,
          filters.codes === "all" ? null : ["P", "S"],
          filters.type === "all" ? null : [filters.type],
          filters.min,
        ),
      );
      if (!wanted.length) return;
      const newestFirst = [...wanted].sort((a, b) => b.id - a.id);
      setIncoming((prev) => [...newestFirst, ...prev]);
      const arrivedAt = Date.now();
      setArrivals((prev) => [...newestFirst.map((i) => ({ ...viewOf(i), arrivedAt })), ...prev].slice(0, 20));
    },
    [filters.codes, filters.type, filters.min],
  );

  const live = useLiveFeed(onBatch);

  // The rows already on screen must not be announced again by a reconnect replay.
  const loaded = useMemo(() => feed.data?.pages.flatMap((p) => p.items) ?? [], [feed.data]);
  const { markSeen } = live;
  useEffect(() => markSeen(loaded), [loaded, markSeen]);

  // New filters: start fresh (the buffer belonged to the old filters).
  const filterKey = filtersToQuery(filters);
  const [bufferKey, setBufferKey] = useState(filterKey);
  if (bufferKey !== filterKey) {
    setBufferKey(filterKey);
    setIncoming([]);
    setArrivals([]);
  }

  const rows: FeedRowView[] = useMemo(() => {
    const seen = new Set<number>();
    const out: FeedRowView[] = [];
    for (const item of loaded) {
      if (!seen.has(item.id)) {
        seen.add(item.id);
        out.push(viewOf(item));
      }
    }
    return out;
  }, [loaded]);

  const mergeIncoming = () => {
    // Reload from the newest page (the server already has the new rows), refresh
    // the today strip, and take the reader to the top where the new rows are.
    router.refresh();
    void feed.refetch();
    setIncoming([]);
    window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
  };

  const setFilters = (next: LiveFilters) => {
    const qs = filtersToQuery(next);
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  };

  // Trades open by trade id; Form 144 notices and Form 3s by their Live event id. Both live in the URL.
  const open = (row: FeedRowView) => {
    if (row.tradeId !== null) openTrade(row.tradeId);
    else openEvent(row.id);
  };

  // The table's column header sticks just below the (sticky) filter bar, whose
  // height changes when it wraps; publish it as a CSS variable.
  // The table also needs it as a number, to keep the active row clear of the bar.
  const sectionRef = useRef<HTMLElement>(null);
  const filterBarRef = useRef<HTMLDivElement>(null);
  const [filterBarHeight, setFilterBarHeight] = useState(0);
  useEffect(() => {
    const bar = filterBarRef.current;
    const section = sectionRef.current;
    if (!bar || !section) return;
    const observer = new ResizeObserver(() => {
      section.style.setProperty("--filterbar-h", `${bar.offsetHeight}px`);
      setFilterBarHeight(bar.offsetHeight);
    });
    observer.observe(bar);
    return () => observer.disconnect();
  }, []);

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = feed;
  const onEndReached = useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 pt-4 md:px-6">
        <ConnectionStatus state={live.state} lastHeard={live.lastHeard} />
        <div className="w-full sm:w-auto">
          <SearchButton />
        </div>
      </div>

      <DownlinkBand
        arrivals={arrivals}
        state={live.state}
        paused={live.paused}
        onTogglePause={() => live.setPaused(!live.paused)}
        onOpen={open}
      />

      <div className="mt-5 xl:grid xl:grid-cols-[minmax(0,1fr)_280px] xl:gap-5 xl:pr-6">
        <section ref={sectionRef} aria-labelledby="feed-title" className="min-w-0">
          <div ref={filterBarRef} className="sticky top-0 z-20 flex items-center gap-x-4 gap-y-2 overflow-x-auto border-y border-hairline bg-night/95 px-4 py-2 backdrop-blur md:flex-wrap md:overflow-visible md:px-6">
            <h2 id="feed-title" className="sr-only">
              All disclosures
            </h2>
            <Segmented
              label="Codes"
              value={filters.codes}
              options={[
                { value: "ps", label: "Buys and sells" },
                { value: "all", label: "All codes" },
              ]}
              onChange={(codes) => setFilters({ ...filters, codes })}
            />
            <Segmented
              label="Show"
              value={filters.type}
              options={[
                { value: "all", label: "Everything" },
                { value: "insider_trade", label: "Trades" },
                { value: "planned_sale", label: "Planned sales" },
                { value: "insider_holding", label: "New insiders" },
              ]}
              onChange={(type) => setFilters({ ...filters, type })}
            />
            <Segmented
              label="Size"
              value={filters.min}
              options={[
                { value: null, label: "Any" },
                { value: 100_000, label: "$100k+" },
                { value: 1_000_000, label: "$1M+" },
                { value: 10_000_000, label: "$10M+" },
              ]}
              onChange={(min) => setFilters({ ...filters, min })}
            />
            {incoming.length ? (
              <button
                type="button"
                onClick={mergeIncoming}
                className="ml-auto inline-flex h-11 shrink-0 items-center rounded-full border border-paper px-3 text-[13px] font-medium md:h-8"
              >
                Show {incoming.length} new
              </button>
            ) : null}
          </div>
          {filters.codes === "ps" ? (
            <p className="px-4 pt-2 text-[12px] leading-4 text-dim md:px-6">
              Showing open-market buys (code P) and sells (code S), plus planned sales and new insiders. Grants,
              exercises and gifts are under All codes.
            </p>
          ) : null}

          {feed.isPending ? <SkeletonRows rows={12} height={40} /> : null}
          {feed.isError && !feed.data ? <ErrorState title="The feed didn't load" /> : null}
          {feed.data && rows.length === 0 ? (
            <EmptyState title="Nothing matches these filters yet">
              New filings arrive through the day. Try All codes or a smaller size.
            </EmptyState>
          ) : null}
          {rows.length ? (
            <>
              {/* The table is one tab stop, but a long one to scroll past: this jumps to what follows it. */}
              <a
                href="#watchlist"
                className="sr-only focus:not-sr-only focus:mx-4 focus:my-2 focus:inline-flex focus:min-h-11 focus:items-center focus:rounded-[var(--radius-control)] focus:bg-console focus:px-3 md:focus:mx-6"
              >
                Skip past the table to the watchlist
              </a>
              <FeedTable
                rows={rows}
                onOpen={open}
                onEndReached={onEndReached}
                hasMore={Boolean(feed.hasNextPage)}
                loadingMore={feed.isFetchingNextPage}
                stickyTop={filterBarHeight}
              />
            </>
          ) : null}
        </section>
        <div className="px-4 pt-6 md:px-6 xl:px-0 xl:pt-0">
          <div className="xl:sticky xl:top-4">
            <WatchlistPanel rows={rows} />
          </div>
        </div>
      </div>
    </>
  );
}
