import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { SpotCheck } from "@/components/status/SpotCheck";
import { apiGetOrNull } from "@/lib/api/server";
import type { CodeOut, SpotCheckRow } from "@/lib/api/types";

export const metadata: Metadata = {
  title: "Spot check",
  description: "Twenty random recent transactions beside links to the original SEC filings, so anyone can check the numbers by eye.",
};

// A fresh random sample on every visit.
export const dynamic = "force-dynamic";

const ROWS = 20;

export default async function SpotCheckPage() {
  const [spot, codes] = await Promise.all([
    apiGetOrNull<SpotCheckRow[]>("/api/status/spot-check", { limit: ROWS }),
    apiGetOrNull<CodeOut[]>("/api/meta/codes"),
  ]);
  return (
    <div className="pb-8">
      <nav aria-label="Breadcrumb" className="px-4 pt-5 text-[13px] text-dim md:px-6">
        <Link href="/status" className="underline decoration-hairline underline-offset-2 hover:text-paper">
          Status
        </Link>
        <span aria-hidden> / </span>
        <span aria-current="page">Spot check</span>
      </nav>
      <Suspense>
        <SpotCheck initial={spot} codes={codes} limit={ROWS} headingLevel={1} />
      </Suspense>
    </div>
  );
}
