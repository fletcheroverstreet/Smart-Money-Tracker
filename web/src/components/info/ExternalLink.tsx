/**
 * A link that leaves the site (EDGAR, the House Clerk, a licence page).
 * It opens in a new tab, and says so to screen readers; the ↗ marks it for
 * sighted readers.
 */
export function ExternalLink({
  href,
  children,
  className,
}: {
  href: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={className ?? "underline decoration-rule-strong underline-offset-2 hover:decoration-paper"}
    >
      {children}
      <span aria-hidden> ↗</span>
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}
