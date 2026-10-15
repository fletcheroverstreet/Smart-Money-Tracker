import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader } from "@/components/ui/PageHeader";

export const metadata: Metadata = {
  title: "Institutions",
  description: "Hedge fund, bank and activist holdings from 13F, 13D and 13G filings. Coming in a later phase.",
};

/**
 * /institutions: a placeholder that says honestly what is coming (PLAN.md §12,
 * Phase 3). No data is collected for this page yet, so nothing is shown:
 * no sample rows, no example charts.
 */
const link = "underline decoration-rule-strong underline-offset-2 hover:text-paper";

export default function InstitutionsPage() {
  return (
    <div className="pb-10">
      <PageHeader title="Institutions" subtitle="What big investors hold and change each quarter." />

      <div className="max-w-[72ch] px-4 pt-6 md:px-6">
        <p className="rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3">
          <span aria-hidden className="text-dim">○ </span>
          Coming in a later phase. The site doesn&apos;t collect these filings yet, so there&apos;s nothing to show
          here today.
        </p>

        <h2 className="mt-8 font-display text-[22px] font-medium leading-7">What will be here</h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5">
          <li>
            <span className="font-medium">Quarterly holdings (Form 13F).</span>{" "}
            <span className="text-dim">
              Every investment manager with $100 million or more in US-listed stocks and similar securities reports its holdings four times a year.
            </span>
          </li>
          <li>
            <span className="font-medium">Changes quarter to quarter.</span>{" "}
            <span className="text-dim">Most bought, most sold, new positions and full exits, with a quarter picker.</span>
          </li>
          <li>
            <span className="font-medium">Big stakes (Schedules 13D and 13G).</span>{" "}
            <span className="text-dim">
              Anyone who buys more than 5% of a company must say so, and an activist (13D) must also say what they
              plan to do.
            </span>
          </li>
          <li>
            <span className="font-medium">Separate tabs</span>{" "}
            <span className="text-dim">
              for hedge funds, banks, activists and index funds, because a bank&apos;s or an index fund&apos;s holdings
              mostly reflect its clients&apos; money, not a view on the stock.
            </span>
          </li>
          <li>
            <span className="font-medium">A page for each fund</span>{" "}
            <span className="text-dim">and a list of the big holders on each ticker&apos;s page.</span>
          </li>
          <li>
            <span className="font-medium">A filing-season tracker</span>{" "}
            <span className="text-dim">showing which funds have filed for the latest quarter.</span>
          </li>
        </ul>

        <h2 className="mt-8 font-display text-[22px] font-medium leading-7">Why not yet</h2>
        <p className="mt-2 text-dim">
          The site is being built in phases, and insider filings (Forms 3, 4, 5 and 144) come first. 13F reports
          need more careful reading before they can be shown: the SEC changed the unit of their dollar values in
          2023, funds can ask to keep some holdings confidential for a while, and the securities in them are listed
          by CUSIP code, which has to be matched to a ticker. Holdings will appear once those checks pass against
          real filings.
        </p>
        <p className="mt-2 text-dim">
          One thing to know in advance: a 13F is filed up to 45 days after the quarter ends, so it shows what a fund
          held then, not what it holds today.
        </p>

        <p className="mt-6 text-dim">
          What is collected today is on the{" "}
          <Link href="/sources" className={link}>
            Sources
          </Link>{" "}
          page, and how each number is worked out is on the{" "}
          <Link href="/methodology" className={link}>
            Methodology
          </Link>{" "}
          page. Insider trades are live now on{" "}
          <Link href="/" className={link}>
            Live
          </Link>{" "}
          and{" "}
          <Link href="/people" className={link}>
            People
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
