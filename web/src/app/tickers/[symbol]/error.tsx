"use client";

import Link from "next/link";

import { ErrorState } from "@/components/ui/States";

/** Something broke while rendering this ticker. Say so plainly and offer a retry. */
export default function TickerError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div>
      <ErrorState title="This ticker page didn't load" />
      <div className="flex flex-wrap items-center gap-4 px-4 md:px-6">
        <button
          type="button"
          onClick={reset}
          className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 hover:bg-raised md:h-9"
        >
          Try again
        </button>
        <Link href="/tickers" className="underline decoration-hairline underline-offset-2 hover:decoration-paper">
          Back to all tickers
        </Link>
      </div>
    </div>
  );
}
