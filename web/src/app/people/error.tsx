"use client";

/**
 * Shown if a People page fails while rendering (an unexpected bug, not a
 * missing API: that case shows its own message inside the page). It says what
 * happened and offers a retry, which re-fetches the page from the server.
 */
import Link from "next/link";

import { ErrorState } from "@/components/ui/States";

export default function PeopleError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <div>
      <ErrorState title="This page didn't load" detail="Something went wrong while building it." />
      <div className="flex flex-wrap items-center gap-3 px-4 md:px-6">
        <button
          type="button"
          onClick={() => retry()}
          className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 hover:bg-raised md:h-9"
        >
          Try again
        </button>
        <Link href="/people" className="inline-flex h-11 items-center px-2 text-dim underline underline-offset-2 hover:text-paper md:h-9">
          Back to the directory
        </Link>
        {error.digest ? <span className="text-[12px] text-dim">Reference {error.digest}</span> : null}
      </div>
    </div>
  );
}
