import type { Metadata } from "next";
import Link from "next/link";

import { SearchButton } from "@/components/search/CommandPalette";

export const metadata: Metadata = {
  title: "Page not found",
};

/**
 * Shown for any address that doesn't match a page, and when a page calls
 * notFound() (for example a ticker or person we have no record of).
 */
const link = "underline decoration-rule-strong underline-offset-2 hover:text-paper";

export default function NotFound() {
  return (
    <div className="max-w-[72ch] px-4 pb-10 pt-10 md:px-6">
      <p className="font-mono text-[13px] text-dim">404</p>
      <h1 className="mt-1 font-display text-[32px] font-semibold leading-9">We couldn&apos;t find that page</h1>
      <p className="mt-2 text-dim">
        The address may be mistyped, or it points to a ticker or person we have no filings for. Tickers change when
        companies rename or merge, so an old link can stop working.
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <SearchButton />
      </div>
      <p className="mt-6 text-dim">
        Or go to{" "}
        <Link href="/" className={link}>
          Live
        </Link>
        ,{" "}
        <Link href="/tickers" className={link}>
          Tickers
        </Link>{" "}
        or{" "}
        <Link href="/people" className={link}>
          People
        </Link>
        .
      </p>
    </div>
  );
}
