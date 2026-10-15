import type { Metadata } from "next";
import { Suspense } from "react";

import { SearchButton } from "@/components/search/CommandPalette";
import { TickerIndexView } from "@/components/tickers/TickerIndexView";
import { daysFrom, indexKey, queryFrom, sortFrom } from "@/components/tickers/params";
import { PageHeader } from "@/components/ui/PageHeader";
import { SkeletonRows } from "@/components/ui/States";
import { apiGetOrNull } from "@/lib/api/server";
import type { DataCoverage, TickerIndexPage } from "@/lib/api/types";

// Rankings move as filings arrive, so render on every request.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Tickers",
  description: "Companies ranked by open-market insider buying and selling, from Forms 4 and 5.",
};

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function TickersPage({ searchParams }: { searchParams: SearchParams }) {
  const raw = await searchParams;
  const get = (name: string) => (typeof raw[name] === "string" ? (raw[name] as string) : null);
  const days = daysFrom(get("days"));
  const sort = sortFrom(get("sort"));
  const q = queryFrom(get("q"));
  // The first page is rendered on the server; the client takes over for changes and paging.
  // If this fails (null), the client asks again and shows the error state itself.
  const [initial, coverage] = await Promise.all([
    apiGetOrNull<TickerIndexPage>("/api/tickers", { days, sort, q: q || undefined, limit: 50 }),
    // Which filing dates are held, so a window with missing days says so.
    apiGetOrNull<DataCoverage>("/api/meta/coverage"),
  ]);

  return (
    <div>
      <PageHeader
        title="Tickers"
        subtitle="Companies whose insiders bought or sold shares on the open market, ranked from the latest Form 4 and 5 filings."
        actions={<SearchButton />}
      />
      <Suspense fallback={<SkeletonRows rows={12} height={40} />}>
        <TickerIndexView initial={initial} initialKey={indexKey(days, sort, q)} coverage={coverage} />
      </Suspense>
    </div>
  );
}
