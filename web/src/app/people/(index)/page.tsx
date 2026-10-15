import type { Metadata } from "next";
import { Suspense } from "react";

import { DirectoryView } from "@/components/people/DirectoryView";
import { NotablesNote, PeopleTabs } from "@/components/people/PeopleTabs";
import { directoryApiParams, directoryFromParams, readerOf } from "@/components/people/params";
import { PageHeader } from "@/components/ui/PageHeader";
import { apiGetOrNull } from "@/lib/api/server";
import type { DirectoryPage } from "@/lib/api/types";

export const metadata: Metadata = {
  title: "People",
  description: "Company insiders who report their trades to the SEC: officers, directors and 10% owners.",
};

// Filings arrive all day, so render on every request.
export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function PeoplePage({ searchParams }: { searchParams: SearchParams }) {
  const filters = directoryFromParams(readerOf(await searchParams));
  // The server renders the first page, so the table is there before any JavaScript runs.
  const first =
    filters.tab === "insiders" ? await apiGetOrNull<DirectoryPage>("/api/people/insiders", directoryApiParams(filters)) : null;

  return (
    <div>
      <PageHeader
        title="People"
        subtitle="Company insiders who report their trades to the SEC. Members of Congress join in a later phase."
      />
      <PeopleTabs current={filters.tab} />
      {filters.tab === "notables" ? (
        <NotablesNote />
      ) : (
        // If the server couldn't reach the API (first is null), the client view tries
        // again in the browser and shows an honest error if that fails too.
        // useSearchParams in the client view needs a Suspense boundary.
        <Suspense>
          <DirectoryView initial={first} initialFilters={filters} />
        </Suspense>
      )}
    </div>
  );
}
