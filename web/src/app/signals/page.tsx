import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader } from "@/components/ui/PageHeader";

export const metadata: Metadata = {
  title: "Signals",
  description: "Patterns in the filings, such as several insiders buying at once, each with its inputs shown. Coming in a later phase.",
};

/**
 * /signals: a placeholder that says honestly what is coming (PLAN.md §8.4 and
 * §12, Phase 4). No signals are computed yet, so nothing is shown: no sample
 * rows, no example scores.
 *
 * The list below mirrors PLAN.md §8.4. Thresholds there may still change
 * (they will live in config/signals.yaml), so the wording here stays general.
 */
const link = "underline decoration-rule-strong underline-offset-2 hover:text-paper";

const PLANNED: { name: string; what: string }[] = [
  { name: "Cluster buy", what: "Several officers or directors of the same company buying shares on the open market within a couple of weeks." },
  { name: "Executive conviction buy", what: "A CEO or CFO making a large open-market purchase." },
  { name: "First purchase in two years or more", what: "An insider buying their company's shares after a long gap." },
  { name: "Selling cluster", what: "Several officers selling outside a pre-arranged trading plan (Rule 10b5-1) within a month." },
  { name: "Late disclosure", what: "A trade reported after its legal deadline. Single late Form 4s are already marked on each trade today." },
  { name: "Activist stake", what: "A new Schedule 13D, or an investor switching from a passive 13G to an active 13D." },
  { name: "Consensus add or exit", what: "Several tracked hedge funds opening, adding to or leaving the same stock in the same quarter." },
  { name: "Committee overlap", what: "A member of Congress trading a company in an industry their committee oversees." },
];

export default function SignalsPage() {
  return (
    <div className="pb-10">
      <PageHeader title="Signals" subtitle="Patterns in the filings worth a closer look, each with its inputs shown." />

      <div className="max-w-[72ch] px-4 pt-6 md:px-6">
        <p className="rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3">
          <span aria-hidden className="text-dim">○ </span>
          Coming in a later phase. No signals are calculated yet, so there&apos;s nothing to show here today.
        </p>

        <h2 className="mt-8 font-display text-[22px] font-medium leading-7">What will be here</h2>
        <p className="mt-2 text-dim">
          Each signal is a plain rule applied to public filings. Every one will list the filings that triggered it,
          with links, so you can check it yourself.
        </p>
        <dl className="mt-3 divide-y divide-hairline rounded-[var(--radius-panel)] border border-hairline bg-console">
          {PLANNED.map((s) => (
            <div key={s.name} className="px-4 py-3">
              <dt className="font-medium">{s.name}</dt>
              <dd className="mt-0.5 text-[13px] leading-[18px] text-dim">{s.what}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-dim">
          For each kind of signal, the page will also show what happened to share prices afterwards in the past, with
          the number of cases and a margin of error. That is research, not advice: a pattern in past filings
          doesn&apos;t say what a stock will do.
        </p>

        <h2 className="mt-8 font-display text-[22px] font-medium leading-7">Why not yet</h2>
        <p className="mt-2 text-dim">
          Signals are built on top of the data, so the data comes first. Insider filings are collected now; trades by
          members of Congress and fund holdings are still to come, and several signals need them. Measuring what
          followed a signal also needs share prices, which are licensed for personal use only (see{" "}
          <Link href="/private" className={link}>
            private mode
          </Link>
          ).
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
          page.
        </p>
      </div>
    </div>
  );
}
