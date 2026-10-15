import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense, cache } from "react";

import { ProfileBody } from "@/components/people/ProfileBody";
import { ProfileHeader } from "@/components/people/ProfileHeader";
import { profileFromParams, readerOf, tradesApiParams } from "@/components/people/params";
import { whoseText } from "@/components/people/whose";
import { PlannedSales } from "@/components/tickers/PlannedSales";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorState, SkeletonRows } from "@/components/ui/States";
import { ApiError, apiGet, apiGetOrNull } from "@/lib/api/server";
import type { DataCoverage, PersonProfile, PlannedSaleOut, PositionSeries, TradePage } from "@/lib/api/types";

// Filings arrive all day, so render on every request.
export const dynamic = "force-dynamic";

type Params = Promise<{ slug: string }>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

type Loaded = { kind: "ok"; profile: PersonProfile } | { kind: "missing" } | { kind: "down" };

/**
 * Fetch the profile once per request (the page and its <title> both need it).
 * A 404 from the API means no such person; anything else means the API is
 * down, which gets an honest error instead of a false "not found".
 */
const loadProfile = cache(async (slug: string): Promise<Loaded> => {
  try {
    const profile = await apiGet<PersonProfile>(`/api/people/${encodeURIComponent(slug)}`, undefined, { cache: "no-store" });
    return { kind: "ok", profile };
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return { kind: "missing" };
    return { kind: "down" };
  }
});

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { slug } = await params;
  const loaded = await loadProfile(slug);
  if (loaded.kind === "ok") {
    return { title: loaded.profile.entity.name, description: loaded.profile.summary };
  }
  return { title: loaded.kind === "missing" ? "Person not found" : "Person" };
}

/** Company slug → ticker, taken from this person's own trade rows (roles and positions don't carry tickers). */
function tickersFrom(...pages: (TradePage | null)[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const page of pages) {
    for (const t of page?.items ?? []) {
      if (t.issuer && t.ticker && !out[t.issuer.slug]) out[t.issuer.slug] = t.ticker;
    }
  }
  return out;
}

export default async function PersonPage({ params, searchParams }: { params: Params; searchParams: SearchParams }) {
  const { slug } = await params;
  const view = profileFromParams(readerOf(await searchParams));
  const base = `/api/people/${encodeURIComponent(slug)}`;

  // Everything the header needs is awaited here, before anything streams, so
  // an unknown person gets a real HTTP 404 (a loading.tsx boundary would have
  // sent "200 OK" first). The trades feed the header's ticker links, so they
  // come now too (in parallel); the position history streams in below.
  const [loaded, trades, allCodes, coverage] = await Promise.all([
    loadProfile(slug),
    apiGetOrNull<TradePage>(`${base}/trades`, tradesApiParams(view.codes)),
    // Grants and exercises name companies too, which helps link roles to ticker pages.
    view.codes === "all" ? Promise.resolve(null) : apiGetOrNull<TradePage>(`${base}/trades`, tradesApiParams("all")),
    apiGetOrNull<DataCoverage>("/api/meta/coverage"),
  ]);

  if (loaded.kind === "missing") notFound();
  if (loaded.kind === "down") {
    return (
      <div>
        <PageHeader title="Person" />
        <ErrorState title="This profile didn't load" detail="The data service didn't answer." />
      </div>
    );
  }

  const { profile } = loaded;
  const tickers = tickersFrom(trades, allCodes);
  // Form 144 notices this seller filed. A seller with no Form 3, 4 or 5 (Metropolitan Life
  // Insurance Co: six notices, nothing else) has only these, so they come before the empty
  // trades tabs; for an insider they follow the trades.
  const notices =
    (profile.stats.planned_sales ?? 0) > 0 ? (
      <Suspense fallback={null}>
        <SellerNotices base={base} seller={profile.entity} />
      </Suspense>
    ) : null;
  const noticesFirst = profile.stats.trades === 0;

  return (
    <div>
      <ProfileHeader profile={profile} tickers={tickers} coverage={coverage} />
      {noticesFirst ? notices : null}
      {/* Also the boundary useSearchParams in the client parts needs. */}
      <Suspense fallback={<BodySkeleton kind={profile.entity.kind} />}>
        <Body
          base={base}
          slug={profile.entity.slug}
          view={view}
          trades={trades}
          tickers={tickers}
          totalTransactions={profile.stats.trades}
          coverage={coverage}
        />
      </Suspense>
      {noticesFirst ? null : notices}
    </div>
  );
}

/** The seller's Form 144 notices, newest first, each naming the company. */
async function SellerNotices({ base, seller }: { base: string; seller: PersonProfile["entity"] }) {
  const planned = await apiGetOrNull<PlannedSaleOut[]>(`${base}/planned-sales`);
  return (
    <div className={planned === null || planned.length ? "pb-6" : undefined}>
      <PlannedSales sales={planned} seller={seller} />
    </div>
  );
}

/** The tabs (trades, positions), once the position history has arrived. */
async function Body({
  base,
  slug,
  view,
  trades,
  tickers,
  totalTransactions,
  coverage,
}: {
  base: string;
  slug: string;
  view: ReturnType<typeof profileFromParams>;
  trades: TradePage | null;
  tickers: Record<string, string>;
  totalTransactions: number;
  coverage: DataCoverage | null;
}) {
  const positions = await apiGetOrNull<PositionSeries[]>(`${base}/positions`);
  return (
    <ProfileBody
      slug={slug}
      initialView={view}
      initialTrades={trades}
      positions={positions}
      positionsFailed={positions === null}
      tickers={tickers}
      totalTransactions={totalTransactions}
      coverage={coverage}
    />
  );
}

/** The tab strip and rows, in fixed sizes, so nothing jumps when they arrive. */
function BodySkeleton({ kind }: { kind: string }) {
  return (
    <div aria-busy="true">
      <span className="sr-only">Loading {whoseText(kind)} trades…</span>
      <div className="flex gap-3 border-b border-hairline px-4 py-3 md:px-6">
        <div className="skeleton h-5 w-16" />
        <div className="skeleton h-5 w-36" />
      </div>
      <SkeletonRows rows={10} height={48} />
    </div>
  );
}
