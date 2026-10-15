"use client";

/**
 * Shown if any page fails while rendering and has no error page of its own
 * (an unexpected bug; a data service that is down shows its own message inside
 * each page instead). "Try again" re-fetches the page from the server.
 */
import Link from "next/link";

import { ErrorState } from "@/components/ui/States";

export default function AppError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <div className="pb-10">
      <ErrorState title="This page didn't load" detail="Something went wrong while building it." />
      <div className="flex flex-wrap items-center gap-3 px-4 md:px-6">
        <button
          type="button"
          onClick={() => retry()}
          className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 hover:bg-raised md:h-9"
        >
          Try again
        </button>
        <Link href="/" className="inline-flex h-11 items-center px-2 text-dim underline underline-offset-2 hover:text-paper md:h-9">
          Go to Live
        </Link>
        <Link href="/status" className="inline-flex h-11 items-center px-2 text-dim underline underline-offset-2 hover:text-paper md:h-9">
          Check the Status page
        </Link>
        {error.digest ? <span className="text-[12px] text-dim">Reference {error.digest}</span> : null}
      </div>
    </div>
  );
}
