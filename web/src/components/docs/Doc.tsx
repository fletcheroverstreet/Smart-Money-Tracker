/**
 * Building blocks for the long reading pages (Methodology and Sources).
 *
 * Plain server components: no state, no JavaScript in the browser. Prose uses
 * the design sheet's `body-l` size (16/24) and a ~72-character line so it is
 * comfortable to read; headings are Barlow Condensed in sentence case.
 */
import Link from "next/link";

export interface TocItem {
  /** The section's id, used as the #anchor. */
  id: string;
  label: string;
}

/** "On this page": a list of anchor links to every section. */
export function Toc({ items }: { items: TocItem[] }) {
  return (
    <nav aria-label="On this page" className="px-4 pt-5 md:px-6">
      <h2 className="text-[12px] font-medium leading-4 text-dim">On this page</h2>
      <ol className="mt-1 grid gap-x-6 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((item, index) => (
          <li key={item.id}>
            {/* 44px tall on phones (touch target), 32px on larger screens. */}
            <a
              href={`#${item.id}`}
              className="flex min-h-11 items-center gap-2 underline decoration-hairline underline-offset-2 hover:text-paper hover:decoration-paper md:min-h-8"
            >
              <span aria-hidden className="num w-5 shrink-0 text-right text-[12px] text-dim">
                {index + 1}
              </span>
              {item.label}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** One section: an anchor the contents list points at, a heading, an optional lead line, the body. */
export function DocSection({
  id,
  title,
  lead,
  children,
}: {
  id: string;
  title: string;
  lead?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="relative scroll-mt-4 border-t border-hairline px-4 pb-2 pt-8 md:px-6">
      <h2 id={`${id}-title`} className="font-display text-[22px] font-medium leading-7">
        {title}
      </h2>
      {lead ? <p className="mt-2 max-w-[72ch] text-[16px] leading-6 text-dim">{lead}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/**
 * Long-form text. The arbitrary variants style the plain <p>, <ul>, <ol> and
 * <h3> elements written inside it, so the page files stay readable.
 */
export function Prose({ children }: { children: React.ReactNode }) {
  return (
    <div
      className={[
        "max-w-[72ch] text-[16px] leading-6",
        "[&_p]:mt-3 [&_p:first-child]:mt-0",
        "[&_ul]:mt-3 [&_ul]:list-disc [&_ul]:space-y-2 [&_ul]:pl-5",
        "[&_ol]:mt-3 [&_ol]:list-decimal [&_ol]:space-y-2 [&_ol]:pl-5",
        "[&_li::marker]:text-dim",
        "[&_h3]:mt-6 [&_h3]:font-display [&_h3]:text-[18px] [&_h3]:font-medium [&_h3]:leading-6",
        "[&_strong]:font-semibold",
        "[&_code]:rounded-[var(--radius-id)] [&_code]:bg-raised [&_code]:px-1 [&_code]:font-mono [&_code]:text-[13px]",
      ].join(" ")}
    >
      {children}
    </div>
  );
}

/** A boxed aside for caveats that change how a number should be read. */
export function Note({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <aside className="mt-4 max-w-[72ch] rounded-[var(--radius-block)] border border-hairline bg-console px-4 py-3">
      <p className="font-medium">{title}</p>
      <div className="mt-1 text-dim [&_p+p]:mt-2">{children}</div>
    </aside>
  );
}

/** An in-site link in the prose style. */
export function DocLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link href={href} className="underline decoration-rule-strong underline-offset-2 hover:decoration-paper">
      {children}
    </Link>
  );
}

/** A worked example: a label on the left and the result on the right; stacks on phones. */
export function Example({ rows }: { rows: { label: string; value: React.ReactNode }[] }) {
  return (
    <dl className="num mt-3 max-w-[72ch] divide-y divide-hairline rounded-[var(--radius-block)] border border-hairline bg-console text-[14px] leading-5">
      {rows.map((row) => (
        <div key={row.label} className="grid gap-x-4 gap-y-0.5 px-4 py-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
          <dt className="text-dim">{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
