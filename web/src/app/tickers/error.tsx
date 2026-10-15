"use client";

import { ErrorState } from "@/components/ui/States";

/** Something broke while rendering the ranking. Say so plainly and offer a retry. */
export default function TickersError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div>
      <ErrorState title="The ticker list didn't load" />
      <div className="px-4 md:px-6">
        <button
          type="button"
          onClick={reset}
          className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 hover:bg-raised md:h-9"
        >
          Try again
        </button>
      </div>
    </div>
  );
}
