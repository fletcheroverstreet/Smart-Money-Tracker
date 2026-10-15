/** Primary navigation (spec §8: Live, People, Institutions, Tickers, Signals, Watchlist, Status). */

export type IconName = "live" | "people" | "institutions" | "tickers" | "signals" | "watch" | "status" | "more";

/** `mobile`: shown in the phone bottom bar; the rest sit under "More" there (design sheet §5.1). */
export const NAV_ITEMS: { href: string; label: string; icon: IconName; mobile: boolean }[] = [
  { href: "/", label: "Live", icon: "live", mobile: true },
  { href: "/people", label: "People", icon: "people", mobile: true },
  { href: "/institutions", label: "Institutions", icon: "institutions", mobile: false },
  { href: "/tickers", label: "Tickers", icon: "tickers", mobile: true },
  { href: "/signals", label: "Signals", icon: "signals", mobile: false },
  { href: "/watchlist", label: "Watchlist", icon: "watch", mobile: true },
  { href: "/status", label: "Status", icon: "status", mobile: false },
];

/** Pages reached from the footer and the phone "More" sheet. */
export const MORE_LINKS: { href: string; label: string }[] = [
  { href: "/methodology", label: "Methodology" },
  { href: "/sources", label: "Sources" },
  { href: "/private", label: "Private mode" },
];

/** Line icons drawn on a 20px grid; stroke uses currentColor so they follow text color. */
export function NavIcon({ name }: { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    live: (
      <>
        <circle cx="10" cy="10" r="2" />
        <path d="M6.2 6.2a5.4 5.4 0 0 0 0 7.6M13.8 6.2a5.4 5.4 0 0 1 0 7.6M3.6 3.6a9 9 0 0 0 0 12.8M16.4 3.6a9 9 0 0 1 0 12.8" />
      </>
    ),
    people: (
      <>
        <circle cx="7.5" cy="7" r="2.8" />
        <path d="M2.5 16c.6-2.8 2.6-4.3 5-4.3s4.4 1.5 5 4.3" />
        <path d="M13 4.6a2.6 2.6 0 0 1 0 5M14.6 11.9c1.6.5 2.6 1.9 2.9 4.1" />
      </>
    ),
    institutions: <path d="M3 16.5h14M4.5 16.5V8.5M8.5 16.5V8.5M11.5 16.5V8.5M15.5 16.5V8.5M2.5 8.5 10 3.5l7.5 5z" />,
    tickers: <path d="M3 15l4.5-5 3 3L17 5.5M13 5.5h4v4" />,
    signals: <path d="M3 10h3l2-5 4 10 2-5h3" />,
    watch: (
      <>
        <path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z" />
        <circle cx="10" cy="10" r="2.2" />
      </>
    ),
    status: (
      <>
        <rect x="3" y="3.5" width="14" height="13" rx="1.5" />
        <path d="M6 12.5l2.2-2.8 2.2 1.6L14 7" />
      </>
    ),
    more: (
      <>
        <circle cx="4.5" cy="10" r="1" />
        <circle cx="10" cy="10" r="1" />
        <circle cx="15.5" cy="10" r="1" />
      </>
    ),
  };
  return (
    <svg
      aria-hidden
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}
