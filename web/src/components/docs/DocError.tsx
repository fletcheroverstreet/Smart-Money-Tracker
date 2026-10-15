"use client";

/**
 * Shown if a reading page fails while rendering (an unexpected bug; a missing
 * API is handled inside the page). Says what happened and offers a retry,
 * which asks the server for the page again.
 */
import { ErrorState } from "@/components/ui/States";

export function DocError({ what, digest, retry }: { what: string; digest?: string; retry: () => void }) {
  return (
    <div>
      <ErrorState title={`The ${what} page didn't load`} detail="Something went wrong while building it." />
      <div className="flex flex-wrap items-center gap-3 px-4 md:px-6">
        <button
          type="button"
          onClick={() => retry()}
          className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 hover:bg-raised md:h-9"
        >
          Try again
        </button>
        {digest ? <span className="text-[12px] text-dim">Reference {digest}</span> : null}
      </div>
    </div>
  );
}
