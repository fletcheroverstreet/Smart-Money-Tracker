import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { Suspense, cache } from "react";

import { EventTimeline } from "@/components/tickers/EventTimeline";
import { InsiderTrades } from "@/components/tickers/InsiderTrades";
import { PlannedSales } from "@/components/tickers/PlannedSales";
import { TickerHeader } from "@/components/tickers/TickerHeader";
import { type CodesFilter, type Range, codesFrom, rangeFrom, rangeStart } from "@/components/tickers/params";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorState, SkeletonRows } from "@/components/ui/States";
import { ApiError, apiGet, apiGetOrNull } from "@/lib/api/server";
import type { DataCoverage, PlannedSaleOut, TickerProfile, TimelineEvent, TradePage } from "@/lib/api/types";
import { daysBefore, todayET } from "@/lib/coverage";

// Filings arrive all day, so render on every request.
export const dynamic = "force-dynamic";

type Params = Promise<{ symbol: string }>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

/** The symbol as typed in the URL, decoded ("BRK%2EB" -> "BRK.B"). */
function decoded(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw; // not URL-encoded after all
  }
}

/** "snow" -> "SNOW". Tickers are upper case; the URL may not be. */
function normalize(raw: string): string {
  return decoded(raw).trim().toUpperCase();
}

/**
 * The company header, fetched once per request (React's cache() shares it
 * between generateMetadata and the page). "missing" means the API said 404.
 */
const getProfile = cache(async (symbol: string): Promise<TickerProfile | "missing" | null> => {
  try {
    return await apiGet<TickerProfile>(`/api/tickers/${encodeURIComponent(symbol)}`, undefined, { cache: "no-store" });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return "missing";
    return null; // the API is down or failing: say so, don't claim the ticker doesn't exist
  }
});

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const symbol = normalize((await params).symbol);
  const profile = await getProfile(symbol);
  if (profile === "missing") return { title: "Ticker not found" };
  if (profile === null) return { title: symbol };
  const ticker = profile.security.ticker ?? symbol;
  return {
    title: `${profile.security.name} (${ticker})`,
    description: `Insider buys, sales and planned sales in ${profile.security.name} (${ticker}), from SEC Forms 4, 5 and 144. Every number links to its filing.`,
  };
}

export default async function TickerPage({ params, searchParams }: { params: Params; searchParams: SearchParams }) {
  const { symbol: rawSymbol } = await params;
  const raw = await searchParams;
  const get = (name: string) => (typeof raw[name] === "string" ? (raw[name] as string) : null);
  const symbol = normalize(rawSymbol);

  // One address per ticker: /tickers/snow -> /tickers/SNOW (keeping the filters).
  if (decoded(rawSymbol) !== symbol) {
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(raw)) if (typeof value === "string") query.set(name, value);
    const qs = query.toString();
    redirect(`/tickers/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ""}`);
  }

  const range = rangeFrom(get("range"));
  const codes = codesFrom(get("codes"));

  // Only the company header is awaited here, before anything streams, so an
  // unknown ticker gets a real HTTP 404 (a loading.tsx boundary would have
  // sent "200 OK" first). The heavier sections stream in below.
  const [profile, coverage] = await Promise.all([
    getProfile(symbol),
    apiGetOrNull<DataCoverage>("/api/meta/coverage"),
  ]);
  if (profile === "missing") notFound();
  if (profile === null) {
    return (
      <div>
        <PageHeader title={symbol} />
        <ErrorState title="This ticker didn't load" detail="The data service didn't answer." />
      </div>
    );
  }

  const today = todayET();
  return (
    <div className="pb-10">
      <TickerHeader profile={profile} symbol={symbol} coverage={coverage} span={{ from: daysBefore(today, 90), to: today }} />
      <Suspense fallback={<BodySkeleton />}>
        <TickerBody
          symbol={symbol}
          displaySymbol={profile.security.ticker ?? symbol}
          range={range}
          codes={codes}
          coverage={coverage}
        />
      </Suspense>
    </div>
  );
}

/** The timeline, the insider trades and the Form 144 notices, fetched together once the header is out. */
async function TickerBody({
  symbol,
  displaySymbol,
  range,
  codes,
  coverage,
}: {
  symbol: string;
  displaySymbol: string;
  range: Range;
  codes: CodesFilter;
  coverage: DataCoverage | null;
}) {
  const path = `/api/tickers/${encodeURIComponent(symbol)}`;
  const [events, trades, planned] = await Promise.all([
    apiGetOrNull<TimelineEvent[]>(`${path}/events`, { start: rangeStart(range) ?? undefined }),
    apiGetOrNull<TradePage>(`${path}/insiders`, { codes, limit: 100 }),
    apiGetOrNull<PlannedSaleOut[]>(`${path}/planned-sales`),
  ]);
  return (
    <>
      <Suspense fallback={<div className="skeleton mx-4 mt-5 h-[340px] md:mx-6" />}>
        <EventTimeline symbol={symbol} initialEvents={events} initialRange={range} coverage={coverage} />
      </Suspense>
      <Suspense fallback={<SkeletonRows rows={8} height={40} />}>
        <InsiderTrades symbol={symbol} initial={trades} initialCodes={codes} />
      </Suspense>
      <PlannedSales sales={planned} symbol={displaySymbol} />
    </>
  );
}

/** Same shape as the sections it stands in for, so nothing jumps when they arrive. */
function BodySkeleton() {
  return (
    <div aria-busy="true">
      <span className="sr-only">Loading the timeline and insider trades…</span>
      <div className="skeleton mx-4 mt-5 h-[340px] md:mx-6" />
      <div className="mt-6 px-4 pb-3 md:px-6">
        <div className="skeleton h-7 w-40" />
      </div>
      <SkeletonRows rows={8} height={40} />
    </div>
  );
}
