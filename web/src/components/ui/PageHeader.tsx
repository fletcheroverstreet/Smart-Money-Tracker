/** Page title block: Barlow Condensed title, one line of plain sentence-case context. */
export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-hairline px-4 pb-4 pt-5 md:px-6">
      <div className="min-w-0">
        <h1 className="font-display text-[32px] font-semibold leading-9">{title}</h1>
        {subtitle ? <p className="mt-1 text-dim">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-3">{actions}</div> : null}
    </header>
  );
}
