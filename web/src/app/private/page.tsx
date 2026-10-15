import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader } from "@/components/ui/PageHeader";

import { PrivateMode } from "./PrivateMode";

export const metadata: Metadata = {
  title: "Private mode",
  description: "The site owner's sign-in for price data that is licensed for personal use only.",
};

/**
 * /private: the owner's sign-in (PLAN.md decision 1).
 *
 * Free price data (Yahoo, Finnhub and the rest) is licensed for personal use
 * only, so prices and anything computed from them are shown only to the site's
 * owner, after signing in with the passphrase in the server's .env file. This
 * is not an accounts system: there is one passphrase and no sign-up.
 *
 * The sign-in state lives in an HttpOnly cookie the browser sends with API
 * requests, so it is checked in the browser (PrivateMode), not here.
 */
export default function PrivatePage() {
  return (
    <div className="pb-10">
      <PageHeader
        title="Private mode"
        subtitle="A sign-in for the site's owner, to see price data that can't be shown publicly."
      />

      <div className="grid gap-8 px-4 pt-6 md:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <PrivateMode />

        <section aria-labelledby="why-title" className="max-w-[64ch]">
          <h2 id="why-title" className="font-display text-[22px] font-medium leading-7">
            Why there is a private mode
          </h2>
          <p className="mt-2 text-dim">
            Everything in the public disclosures (trades, holdings, planned sales, the prices written on each
            filing) is government data and is shown to everyone. Daily share prices are different: every free
            source licenses them for personal use only, and that includes anything calculated from them.
          </p>
          <p className="mt-2 text-dim">So those appear only after the owner signs in.</p>

          <h3 className="mt-5 text-[12px] font-medium leading-4 text-dim">What private mode adds today</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            <li>A line of daily closing prices behind the markers on each ticker&apos;s event chart.</li>
          </ul>

          <h3 className="mt-5 text-[12px] font-medium leading-4 text-dim">Planned for later phases</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-dim">
            <li>Live quotes, and prices beside the tickers on your watchlist.</li>
            <li>Returns after each trade, and rankings by performance.</li>
          </ul>

          <h3 className="mt-5 text-[12px] font-medium leading-4 text-dim">How the sign-in works</h3>
          <p className="mt-2 text-dim">
            The passphrase is sent once to this site&apos;s server and never kept in your browser. The server answers
            with a signed cookie that scripts on the page can&apos;t read. It lasts seven days, or until you sign out.
            Pages shown in private mode are marked so that shared caches never keep them.
          </p>
          <p className="mt-3 text-dim">
            Details are on the{" "}
            <Link href="/methodology" className="underline decoration-rule-strong underline-offset-2 hover:text-paper">
              Methodology
            </Link>{" "}
            and{" "}
            <Link href="/sources" className="underline decoration-rule-strong underline-offset-2 hover:text-paper">
              Sources
            </Link>{" "}
            pages.
          </p>
        </section>
      </div>
    </div>
  );
}
