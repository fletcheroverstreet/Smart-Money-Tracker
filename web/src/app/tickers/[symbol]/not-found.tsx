import Link from "next/link";

import { SearchButton } from "@/components/search/CommandPalette";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/States";

/** notFound() from the ticker page lands here: an unknown symbol. */
export default function TickerNotFound() {
  return (
    <div>
      <PageHeader title="Ticker not found" />
      <EmptyState title="We don't have a company with that ticker">
        <p>
          Tickers come from the SEC&apos;s company list and from insider filings. Check the spelling, or search by
          company name.
        </p>
        <div className="mx-auto mt-4 flex max-w-sm flex-col items-center gap-3">
          <SearchButton />
          <Link href="/tickers" className="underline decoration-hairline underline-offset-2 hover:decoration-paper">
            See companies with recent insider trades
          </Link>
        </div>
      </EmptyState>
    </div>
  );
}
