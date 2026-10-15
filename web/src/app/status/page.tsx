import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { SpotCheck } from "@/components/status/SpotCheck";
import { NEWEST_QUERIES } from "@/components/status/queries";
import { StatusView } from "@/components/status/StatusView";
import { PageHeader } from "@/components/ui/PageHeader";
import { apiGetOrNull } from "@/lib/api/server";
import type { CodeOut, Coverage, DataCoverage, FeedPage, SourceOut, SpotCheckRow, StatusReport } from "@/lib/api/types";

export const metadata: Metadata = {
  title: "Status",
  description: "How fresh the data is, what each collection job did last, what was set aside for review, and a random spot check against the original filings.",
};

// Always render fresh: this page reports the live state of the collectors.
export const dynamic = "force-dynamic";

const SPOT_CHECK_ROWS = 10;

export default async function StatusPage() {
  const [status, coverage, newestInsider, newestPlan, sources, codes, spot, datesHeld] = await Promise.all([
    apiGetOrNull<StatusReport>("/api/status"),
    apiGetOrNull<Coverage>("/api/status/coverage"),
    apiGetOrNull<FeedPage>("/api/feed", NEWEST_QUERIES.insider),
    apiGetOrNull<FeedPage>("/api/feed", NEWEST_QUERIES.plan),
    apiGetOrNull<SourceOut[]>("/api/meta/sources"),
    apiGetOrNull<CodeOut[]>("/api/meta/codes"),
    apiGetOrNull<SpotCheckRow[]>("/api/status/spot-check", { limit: SPOT_CHECK_ROWS }),
    apiGetOrNull<DataCoverage>("/api/meta/coverage"),
  ]);
  // The time these were fetched, so "updated X ago" is honest from the first paint.
  // eslint-disable-next-line react-hooks/purity -- a server component renders once per request
  const fetchedAt = Date.now();

  return (
    <div>
      <PageHeader
        title="Status"
        subtitle="Is the data fresh? What did the collectors do last? Everything here comes straight from the worker's own records."
      />
      <nav aria-label="On this page" className="flex flex-wrap gap-x-4 gap-y-1 px-4 pt-3 text-[13px] text-dim md:px-6">
        {[
          ["#sources", "Sources"],
          ["#jobs", "Jobs"],
          ["#reconciliation", "Reconciliation"],
          ["#quarantine", "Set aside"],
          ["#coverage", "Coverage"],
          ["#spot-check", "Spot check"],
        ].map(([href, label]) => (
          <a key={href} href={href} className="inline-flex min-h-8 items-center underline decoration-hairline underline-offset-2 hover:text-paper max-md:min-h-11">
            {label}
          </a>
        ))}
      </nav>
      <Suspense>
        <StatusView initial={{ status, coverage, newestInsider, newestPlan, datesHeld, fetchedAt }} sources={sources} />
        <SpotCheck initial={spot} codes={codes} limit={SPOT_CHECK_ROWS} />
      </Suspense>
      <p className="px-4 pb-8 pt-3 text-[13px] text-dim md:px-6">
        <Link href="/status/spot-check" className="underline decoration-rule-strong underline-offset-2 hover:text-paper">
          A bigger sample of 20 rows
        </Link>{" "}
        is on its own page.{" "}
        <Link href="/methodology" className="underline decoration-rule-strong underline-offset-2 hover:text-paper">
          How collection works
        </Link>{" "}
        is explained on the Methodology page.
      </p>
    </div>
  );
}
