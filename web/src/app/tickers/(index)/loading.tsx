import { SkeletonRows } from "@/components/ui/States";

/** Shown while the ticker ranking renders on the server. Same shape as the page, so nothing jumps. */
export default function Loading() {
  return (
    <div aria-busy="true" aria-label="Loading tickers">
      <header className="border-b border-hairline px-4 pb-4 pt-5 md:px-6">
        <h1 className="font-display text-[32px] font-semibold leading-9">Tickers</h1>
        <div className="skeleton mt-2 h-4 w-full max-w-md" />
      </header>
      <div className="flex gap-3 border-b border-hairline px-4 py-3 md:px-6">
        <div className="skeleton h-9 w-56" />
        <div className="skeleton h-9 w-72" />
      </div>
      <SkeletonRows rows={12} height={40} />
    </div>
  );
}
