import { SkeletonRows } from "@/components/ui/States";

/** Shown while the directory's first page loads: the same frame, with skeleton rows (never a spinner). */
export default function Loading() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading the insider directory…</span>
      <div className="border-b border-hairline px-4 pb-4 pt-5 md:px-6">
        <p className="font-display text-[32px] font-semibold leading-9">People</p>
        <div className="skeleton mt-2 h-4 w-full max-w-md" />
      </div>
      <div className="flex gap-3 border-b border-hairline px-4 py-3 md:px-6">
        <div className="skeleton h-5 w-20" />
        <div className="skeleton h-5 w-20" />
      </div>
      <div className="flex flex-wrap gap-3 px-4 py-4 md:px-6">
        <div className="skeleton h-11 w-full sm:w-80 md:h-9" />
        <div className="skeleton h-11 w-32 md:h-9" />
      </div>
      <SkeletonRows rows={12} height={40} />
    </div>
  );
}
