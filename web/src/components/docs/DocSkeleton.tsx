/**
 * Loading frame for the reading pages: the real title, then skeleton lines
 * (never a spinner). Fixed sizes, so nothing jumps when the page arrives.
 */
export function DocSkeleton({ title, what }: { title: string; what: string }) {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading {what}…</span>
      <div className="border-b border-hairline px-4 pb-4 pt-5 md:px-6">
        <p className="font-display text-[32px] font-semibold leading-9">{title}</p>
        <div className="skeleton mt-2 h-4 w-full max-w-md" />
      </div>
      <div aria-hidden className="grid gap-2 px-4 pt-5 sm:grid-cols-2 md:px-6 lg:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="skeleton h-5 w-48 max-w-full" />
        ))}
      </div>
      <div aria-hidden className="max-w-[72ch] space-y-2 px-4 pt-8 md:px-6">
        <div className="skeleton h-7 w-56 max-w-full" />
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="skeleton h-5" />
        ))}
      </div>
    </div>
  );
}
