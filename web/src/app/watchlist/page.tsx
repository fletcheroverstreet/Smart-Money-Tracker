import type { Metadata } from "next";
import { Suspense } from "react";

import { SearchButton } from "@/components/search/CommandPalette";
import { PageHeader } from "@/components/ui/PageHeader";
import { WatchlistView } from "@/components/watchlist/WatchlistView";

export const metadata: Metadata = {
  title: "Watchlist",
  description: "The tickers and people you follow, with their latest filings. Kept in this browser only.",
};

/**
 * /watchlist. The list itself lives in this browser (lib/watchlist.ts), which
 * the server can't read, so everything below the header renders in the
 * browser (WatchlistView).
 */
export default function WatchlistPage() {
  return (
    <div className="pb-10">
      <PageHeader
        title="Watchlist"
        subtitle="The tickers and people you follow, with their three latest filings each."
        actions={<SearchButton />}
      />
      {/* WatchlistView opens trades in the quick-look drawer, which reads the URL; that needs a Suspense boundary. */}
      <Suspense>
        <WatchlistView />
      </Suspense>
      <div className="max-w-[72ch] space-y-1 px-4 pt-8 text-[13px] leading-[18px] text-dim md:px-6">
        <p>
          Your list is saved in this browser only. There are no accounts, so it doesn&apos;t follow you to another
          device, and clearing your browser&apos;s site data empties it.
        </p>
        <p>This list doesn&apos;t send alerts.</p>
      </div>
    </div>
  );
}
