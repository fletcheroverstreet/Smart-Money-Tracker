/** Empty, error and loading states. They say what happened and what to do next (spec §9). */

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="px-4 py-10 text-center md:px-6">
      <p className="font-display text-[22px] font-medium leading-7">{title}</p>
      {children ? <div className="mx-auto mt-2 max-w-prose text-dim">{children}</div> : null}
    </div>
  );
}

export function ErrorState({ title = "This didn't load", detail }: { title?: string; detail?: string }) {
  return (
    <div role="alert" className="mx-4 my-6 rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3 md:mx-6">
      <p className="flex items-center gap-2 font-medium">
        <span aria-hidden className="text-beacon">!</span>
        {title}
      </p>
      <p className="mt-1 text-dim">
        {detail ?? "The data service didn't answer."} Try again in a minute; the last good data stays on the Status page.
      </p>
    </div>
  );
}

/** Skeleton rows for tables (never spinners). Fixed height, so nothing shifts when data arrives. */
export function SkeletonRows({ rows = 8, height = 32 }: { rows?: number; height?: number }) {
  return (
    <div aria-hidden className="space-y-1 px-4 py-2 md:px-6">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ height: height - 4 }} />
      ))}
    </div>
  );
}
