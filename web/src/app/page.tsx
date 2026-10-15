import { Suspense } from "react";

import { LiveView } from "@/components/live/LiveView";
import { feedParams, filtersFromParams } from "@/components/live/filters";
import { TodayStrip } from "@/components/live/TodayStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorState } from "@/components/ui/States";
import { apiGetOrNull } from "@/lib/api/server";
import type { FeedPage, TodaySummary } from "@/lib/api/types";

// Always render fresh: this page is the live feed.
export const dynamic = "force-dynamic";

export default async function LivePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const raw = await searchParams;
  const filters = filtersFromParams({ get: (name) => (typeof raw[name] === "string" ? (raw[name] as string) : null) });
  const [feed, today] = await Promise.all([
    apiGetOrNull<FeedPage>("/api/feed", feedParams(filters)),
    apiGetOrNull<TodaySummary>("/api/feed/today"),
  ]);

  return (
    <div>
      <PageHeader
        title="Live"
        subtitle="Insider filings as the SEC publishes them: Forms 3, 4 and 5, and Form 144 notices of planned sales."
      />
      {feed === null && today === null ? (
        <ErrorState title="The data service isn't answering" detail="The page will fill in once it's back." />
      ) : null}
      {today ? <TodayStrip today={today} /> : null}
      <Suspense>
        <LiveView initial={feed} initialFilters={filters} />
      </Suspense>
    </div>
  );
}
