/**
 * A one-line note under a date window that runs over dates whose filings
 * aren't loaded yet (see lib/coverage.ts). Renders nothing when the window is
 * complete. No hooks, so it works on the server and in client views.
 */
import type { DataCoverage } from "@/lib/api/types";
import { coverageNote } from "@/lib/coverage";

export function CoverageNote({
  coverage,
  from,
  to,
  what,
  className = "",
}: {
  coverage: DataCoverage | null | undefined;
  from: string | null | undefined;
  to: string | null | undefined;
  /** What is incomplete, e.g. "these counts are" (default "totals for this window are"). */
  what?: string;
  className?: string;
}) {
  const text = coverageNote(coverage, from, to, what);
  if (!text) return null;
  return (
    <p role="note" className={`text-[12px] leading-4 text-dim ${className}`}>
      <span aria-hidden className="text-beacon">
        !{" "}
      </span>
      {text}
    </p>
  );
}
