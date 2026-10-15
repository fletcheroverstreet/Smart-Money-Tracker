"use client";

/**
 * The ticker page's event timeline: insider buys ▲, sells ▼ and Form 144
 * planned sales ○ on a shared date axis (the chart logic is in timelineChart.ts).
 *
 * - The range (3M, 1Y, All) lives in the URL (?range=).
 * - Clicking a trade marker opens its quick look (the same drawer as every table).
 * - The "Table view" toggle (a pressed/not-pressed button with a fixed label)
 *   swaps the chart for a table of the same events, which also works with a
 *   keyboard and a screen reader.
 * - Stretches whose filings aren't loaded yet are shaded and labelled "not
 *   loaded yet", and a note under the chart names them, so a blank stretch
 *   isn't read as "no insider activity".
 * - Prices appear only in private mode (PLAN.md decision 1: no free price source
 *   allows public display). If the price request fails for any reason, the chart
 *   simply has no price line.
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { EChartsType } from "echarts/core";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { useOpenTrade } from "@/components/drawer/QuickLook";
import { Direction } from "@/components/ui/Direction";
import { CoverageNote } from "@/components/ui/CoverageNote";
import { ErrorState } from "@/components/ui/States";
import { apiGet } from "@/lib/api/client";
import type { DataCoverage, PriceSeriesOut, SessionOut, TimelineEvent } from "@/lib/api/types";
import { day, moneyCompact } from "@/lib/format";
import { Segmented } from "@/components/ui/Segmented";
import { useIsPhone, useReducedMotion } from "@/lib/useMediaQuery";

import { useSetParams } from "./controls";
import { RANGES, type Range, rangeFrom, rangeStart } from "./params";
import {
  type ChartColors,
  type Marker,
  type UnloadedSpan,
  buildMarkers,
  buildOption,
  dayToTime,
  pricePoints,
  summarize,
  tooltipHtml,
} from "./timelineChart";

const DAY_MS = 86_400_000;

/** Reads the design tokens from the page's CSS (globals.css), so the canvas uses the same colors. */
function readColors(): ChartColors {
  const css = getComputedStyle(document.documentElement);
  // The fallbacks are the same token values, used only if a variable isn't present.
  const token = (name: string, fallback: string) => css.getPropertyValue(`--color-${name}`).trim() || fallback;
  return {
    buy: token("inflow-mark", "#14958a"),
    sell: token("outflow-mark", "#d9692a"),
    planned: token("dim", "#535e74"),
    text: token("paper", "#111a2e"),
    dim: token("dim", "#535e74"),
    hairline: token("hairline", "#dce2eb"),
    rule: token("rule-strong", "#7b869c"),
    panel: token("console", "#f5f7fa"),
    price: token("series-1", "#2f6fd6"),
  };
}

function usePrivateMode(): boolean {
  const session = useQuery({
    queryKey: ["private-session"],
    queryFn: () => apiGet<SessionOut>("/api/private/session"),
    staleTime: 5 * 60_000,
    retry: false,
  });
  // GET /api/private/session answers { active: true } in private mode. Any error
  // (API down, not configured) counts as public mode.
  return session.data?.active === true;
}

// ---------------------------------------------------------------- table view

function EventTable({ events, onOpen }: { events: TimelineEvent[]; onOpen: (id: number) => void }) {
  const rows = useMemo(() => [...events].sort((a, b) => (a.day < b.day ? 1 : a.day > b.day ? -1 : 0)), [events]);
  return (
    // "relative" keeps the sr-only caption and link text inside this scroll box
    // (otherwise they are positioned against the page and can widen it on phones).
    <div className="relative max-h-[480px] overflow-y-auto">
      <table className="num w-full border-collapse text-[13px] leading-[18px]">
        <caption className="sr-only">The timeline&apos;s events, newest first</caption>
        <thead className="sticky top-0 bg-raised text-left text-[12px] font-medium leading-4 text-dim">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">Date</th>
            <th scope="col" className="px-3 py-2 font-medium">Event</th>
            <th scope="col" className="hidden px-3 py-2 font-medium sm:table-cell">Who</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e, i) => {
            const planned = e.layer === "planned_sales";
            const who = e.label.replace(/: [^:]*$/, "");
            return (
              <tr key={`${e.transaction_id ?? "p"}-${e.day}-${i}`} className="border-b border-hairline">
                <td className="whitespace-nowrap px-3 py-2 text-dim">
                  {planned ? "~" : ""}
                  {day(e.day)}
                </td>
                <td className="px-3 py-2">
                  <Direction direction={e.direction} />
                  <span className="block text-dim sm:hidden">{who}</span>
                </td>
                <td className="hidden px-3 py-2 sm:table-cell">{who}</td>
                <td className="px-3 py-2 text-right">
                  {/* A value of 0 is no value (a price of 0: an award, a gift), never "+$0". */}
                  {e.value === null || e.value === undefined || Number(e.value) === 0 ? (
                    <span className="text-dim">–</span>
                  ) : planned ? (
                    <span className="text-dim">~{moneyCompact(e.value)}</span>
                  ) : (
                    <span className={e.direction === "buy" ? "text-inflow" : "text-outflow"}>
                      {e.direction === "buy" ? "+" : "−"}
                      {moneyCompact(e.value)}
                    </span>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right">
                  {e.transaction_id ? (
                    <button
                      type="button"
                      onClick={() => (e.transaction_id ? onOpen(e.transaction_id) : undefined)}
                      className="min-h-11 underline decoration-hairline underline-offset-2 hover:decoration-paper md:min-h-0"
                    >
                      Details
                    </button>
                  ) : (
                    <a
                      href={e.filing_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex min-h-11 items-center underline decoration-hairline underline-offset-2 hover:decoration-paper md:min-h-0"
                    >
                      Form 144<span className="sr-only"> filing (opens the SEC site)</span>
                    </a>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- chart

function useChart(
  container: React.RefObject<HTMLDivElement | null>,
  markersByLane: React.RefObject<Marker[][]>,
  onOpen: (id: number) => void,
) {
  const [chart, setChart] = useState<EChartsType | null>(null);
  const openRef = useRef(onOpen);
  useEffect(() => {
    openRef.current = onOpen;
  }, [onOpen]);

  // Create the chart once, in the browser only (ECharts draws on a canvas).
  useEffect(() => {
    let disposed = false;
    let instance: EChartsType | null = null;
    let observer: ResizeObserver | null = null;
    void import("./echarts").then(({ echarts }) => {
      const element = container.current;
      if (disposed || !element) return;
      instance = echarts.init(element, undefined, { renderer: "canvas" });
      instance.on("click", (params) => {
        const lane = Number(String(params.seriesId ?? "").replace("lane-", ""));
        const marker = markersByLane.current[lane]?.[params.dataIndex];
        if (marker?.transactionId) openRef.current(marker.transactionId);
      });
      observer = new ResizeObserver(() => instance?.resize());
      observer.observe(element);
      setChart(instance);
    });
    return () => {
      disposed = true;
      observer?.disconnect();
      instance?.dispose();
    };
  }, [container, markersByLane]);

  return chart;
}

export function EventTimeline({
  symbol,
  initialEvents,
  initialRange,
  coverage,
}: {
  symbol: string;
  initialEvents: TimelineEvent[] | null;
  initialRange: Range;
  /** Which filing dates the site holds (GET /api/meta/coverage); null if it didn't load. */
  coverage: DataCoverage | null;
}) {
  const params = useSearchParams();
  const range = rangeFrom(params.get("range"));
  const setParams = useSetParams();
  const openTrade = useOpenTrade();
  const phone = useIsPhone();
  const reducedMotion = useReducedMotion();
  const [asTable, setAsTable] = useState(false);
  const path = `/api/tickers/${encodeURIComponent(symbol)}`;

  const eventsQuery = useQuery({
    queryKey: ["ticker-events", symbol, range],
    queryFn: () => apiGet<TimelineEvent[]>(`${path}/events`, { start: rangeStart(range) ?? undefined }),
    initialData: range === initialRange && initialEvents ? initialEvents : undefined,
    // Keep showing the previous range while the next one loads (no blank chart).
    placeholderData: keepPreviousData,
  });
  const events = useMemo(() => eventsQuery.data ?? [], [eventsQuery.data]);
  const markers = useMemo(() => buildMarkers(events), [events]);

  // The x axis: the range's first day (or the first event, for "All") up to today.
  // `todayTime` is today at 00:00 UTC (the last day the window covers).
  const [start, end, todayTime] = useMemo(() => {
    const today = new Date();
    const endTime = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
    const from = rangeStart(range, today);
    // A little room after today, so a marker on the latest day isn't cut in half by the edge.
    if (from) {
      const startTime = dayToTime(from);
      return [startTime, endTime + Math.max(2 * DAY_MS, (endTime - startTime) * 0.02), endTime];
    }
    const first = markers[0]?.time ?? endTime - 365 * DAY_MS;
    const pad = Math.max(7 * DAY_MS, (endTime - first) * 0.02);
    return [first - pad, endTime + pad, endTime];
  }, [range, markers]);

  // Stretches of the axis whose filings aren't loaded: the time before the first
  // loaded filing date, and each gap. A gap's last day is shaded to its end.
  const unloaded = useMemo<UnloadedSpan[]>(() => {
    if (!coverage) return [];
    const spans: UnloadedSpan[] = coverage.gaps.map((gap) => ({
      from: dayToTime(gap.first),
      to: dayToTime(gap.last) + DAY_MS,
      partial: gap.state === "partial",
    }));
    if (coverage.first_filed) spans.unshift({ from: start, to: dayToTime(coverage.first_filed) });
    return spans;
  }, [coverage, start]);
  // The same window as ISO dates, for the note under the chart.
  const windowFrom = new Date(start).toISOString().slice(0, 10);
  const windowTo = new Date(todayTime).toISOString().slice(0, 10);

  // Private mode only: closing prices for the same span.
  const isPrivate = usePrivateMode();
  const priceDays = Math.min(36_500, Math.max(30, Math.ceil((end - start) / DAY_MS) + 1));
  const pricesQuery = useQuery({
    queryKey: ["private-prices", symbol, priceDays],
    queryFn: () => apiGet<PriceSeriesOut>(`/api/private/prices/${encodeURIComponent(symbol)}`, { days: priceDays }),
    enabled: isPrivate,
    retry: false, // 401/403/404 mean "no price line", not "try again"
    staleTime: 10 * 60_000,
  });
  const prices = useMemo(
    () => (isPrivate && pricesQuery.data ? pricePoints(pricesQuery.data.bars, start, end) : null),
    [isPrivate, pricesQuery.data, start, end],
  );
  const hasPrices = Boolean(prices && prices.length > 1);

  // The click handler reads the current markers through this ref.
  const markersByLane = useRef<Marker[][]>([[], [], []]);
  useEffect(() => {
    markersByLane.current = [0, 1, 2].map((lane) => markers.filter((m) => m.lane === lane));
  }, [markers]);

  const container = useRef<HTMLDivElement>(null);
  const chart = useChart(container, markersByLane, openTrade);

  useEffect(() => {
    if (!chart) return;
    const option = buildOption({
      markers,
      prices,
      start,
      end,
      colors: readColors(),
      font: getComputedStyle(document.body).fontFamily,
      animate: !reducedMotion,
      phone,
      // Tooltips need the marker behind each point; the lane is in the series id ("lane-1").
      tooltipFor: (seriesId, dataIndex) => {
        const marker = markersByLane.current[Number(seriesId.replace("lane-", ""))]?.[dataIndex];
        return marker ? tooltipHtml(marker) : "";
      },
      unloaded,
    });
    chart.setOption(option, { notMerge: true });
  }, [chart, markers, prices, start, end, reducedMotion, phone, unloaded]);

  const rangeText = RANGES.find((r) => r.value === range)!.long;
  const summary = summarize(events, symbol, rangeText);
  const heightClass = hasPrices ? "h-[380px] md:h-[440px]" : "h-[260px] md:h-[280px]";

  return (
    <section aria-labelledby="timeline-title" className="mx-4 mt-5 rounded-[var(--radius-panel)] border border-hairline bg-console md:mx-6">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline px-4 py-3">
        <h2 id="timeline-title" className="font-display text-[22px] font-medium leading-7">
          Insider events
        </h2>
        <div className="flex flex-wrap items-center gap-3">
          <Segmented
            label="Range"
            value={range}
            options={RANGES}
            onChange={(value) => setParams({ range: value === "1y" ? null : value })}
          />
          <button
            type="button"
            // A toggle keeps one label; aria-pressed (and the fill) says whether it's on.
            aria-pressed={asTable}
            onClick={() => setAsTable((v) => !v)}
            className={`inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-3 text-[13px] md:h-8 ${
              asTable ? "bg-raised text-paper shadow-[inset_0_-2px_0_var(--color-paper)]" : "hover:bg-raised"
            }`}
          >
            Table view
          </button>
        </div>
      </div>

      {/* Legend: shape + word + color for every layer. */}
      <ul className="flex flex-wrap gap-x-4 gap-y-1 px-4 pt-3 text-[12px] leading-4 text-dim" aria-label="Legend">
        <li>
          <span aria-hidden className="text-inflow-mark">▲</span> Insider buy
        </li>
        <li>
          <span aria-hidden className="text-outflow-mark">▼</span> Insider sale
        </li>
        <li>
          <span aria-hidden>○</span> Planned sale (Form 144 notice)
        </li>
        {hasPrices ? (
          <li className="inline-flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-0.5 w-4 bg-series-1" /> Closing price
          </li>
        ) : null}
        <li>Bigger marker = more dollars</li>
      </ul>

      {eventsQuery.isError && !eventsQuery.data ? (
        <ErrorState title="The timeline didn't load" />
      ) : (
        <div aria-busy={eventsQuery.isFetching} className="px-2 pb-2 pt-1">
          {/* The chart stays mounted (hidden in table view) so switching back is instant. */}
          <div className={`relative ${asTable ? "hidden" : ""}`}>
            <div ref={container} role="img" aria-label={summary} data-unmanaged-dom className={`w-full ${heightClass}`} />
            {eventsQuery.isPending ? <div aria-hidden className={`skeleton absolute inset-0 ${heightClass}`} /> : null}
            {!eventsQuery.isPending && markers.length === 0 ? (
              <p className="absolute inset-0 flex items-center justify-center px-6 text-center text-dim">
                No open-market insider trades or planned sales in {rangeText}.
                {range !== "all" ? " Try a longer range." : ""}
              </p>
            ) : null}
          </div>
          {asTable ? <EventTable events={events} onOpen={openTrade} /> : null}
        </div>
      )}
      <CoverageNote coverage={coverage} from={windowFrom} to={windowTo} what="this timeline is" className="px-4 pb-2" />

      <p className="border-t border-hairline px-4 py-2 text-[12px] leading-4 text-dim">
        {isPrivate ? (
          hasPrices && pricesQuery.data ? (
            <>
              Private mode: closing prices from {pricesQuery.data.provider} ({pricesQuery.data.licence}). Not shown publicly.
            </>
          ) : pricesQuery.isFetching ? (
            <>Private mode: loading prices…</>
          ) : (
            <>Private mode: no stored prices for {symbol} yet, so there is no price line.</>
          )
        ) : (
          <>
            Prices aren&apos;t shown publicly: no free source allows it (see{" "}
            <Link href="/sources" className="underline decoration-hairline underline-offset-2 hover:decoration-paper">
              Sources
            </Link>
            ).
          </>
        )}{" "}
        Dates are trade dates; a Form 144 shows the approximate planned sale date.
      </p>
    </section>
  );
}
