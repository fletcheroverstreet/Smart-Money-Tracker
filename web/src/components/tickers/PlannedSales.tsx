/**
 * Form 144 notices for one company: people who told the SEC they intend to
 * sell, and whether a Form 4 later reported the sale.
 *
 * The same list serves a seller's profile (pass `seller`): there each row names
 * the company instead of the seller. Sellers who file only Form 144s
 * (Metropolitan Life Insurance Co, six notices) have no other page that lists
 * their notices.
 *
 * A Form 144 is a notice of intent, not a sale. Many people who file one
 * (affiliates who aren't officers or directors) never have to file a Form 4,
 * so "no Form 4 found" never means "didn't sell". The copy says so.
 *
 * No hooks here, so this renders on the server with the rest of the page.
 */
import Link from "next/link";

import type { PlannedSaleOut } from "@/lib/api/types";
import { plannedAmount } from "@/lib/dataIssues";
import { day, shares, toNumber } from "@/lib/format";

/**
 * The Form 4 match status in plain words. A match is by seller, company and a
 * code-S sale within 90 days of the notice (backend smt/ingest/form144.py), so
 * it's "likely", not certain.
 */
export function matchStatus(sale: PlannedSaleOut): { text: string; found: boolean } {
  switch (sale.status) {
    case "matched": {
      const planned = toNumber(sale.shares);
      const matched = toNumber(sale.matched_shares);
      if (planned !== null && matched !== null && matched < planned) {
        return { text: `Part of it likely reported on Form 4 (${shares(matched)} of ${shares(planned)} shares)`, found: true };
      }
      return { text: "Likely reported on Form 4", found: true };
    }
    case "no_form4_found":
      return { text: "No Form 4 found within 90 days", found: false };
    case "withdrawn":
      return { text: "Notice withdrawn", found: false };
    default:
      return { text: "No Form 4 yet (we look for 90 days)", found: false };
  }
}

function Status({ sale }: { sale: PlannedSaleOut }) {
  const status = matchStatus(sale);
  return (
    <span className={status.found ? "text-paper" : "text-dim"}>
      <span aria-hidden>{status.found ? "✓ " : "– "}</span>
      {status.text}
    </span>
  );
}

/** Who the row is about: the seller on a company's page, the company on a seller's profile. */
function Party({ sale, onProfile }: { sale: PlannedSaleOut; onProfile: boolean }) {
  const company = sale.issuer;
  return (
    <>
      {!onProfile ? (
        <Link href={`/people/${sale.seller.slug}`} className="hover:underline">
          {sale.seller.name}
        </Link>
      ) : company && sale.ticker ? (
        <Link href={`/tickers/${sale.ticker}`} className="hover:underline">
          {company.name} ({sale.ticker})
        </Link>
      ) : (
        // A company with no ticker has no page here; the filing link names it in full.
        <span>{company?.name ?? "Company not named on the notice"}</span>
      )}
      {sale.relationships.length ? <span className="text-dim">, {sale.relationships.join(", ")}</span> : null}
      {sale.plan_adoption_dates.length ? (
        <span className="block text-[12px] leading-4 text-dim">
          Under a trading plan adopted {sale.plan_adoption_dates.map((d) => day(d)).join(", ")}
        </span>
      ) : null}
    </>
  );
}

function FilingLink({ sale }: { sale: PlannedSaleOut }) {
  return (
    <a
      href={sale.filing_url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex min-h-11 items-center font-mono text-[12px] underline decoration-hairline underline-offset-2 hover:decoration-paper md:min-h-0"
    >
      144<span className="sr-only"> filing {sale.accession} on the SEC site (opens a new tab)</span>
    </a>
  );
}

/**
 * "~$1.4M (12,000 shares)": a Form 144 value is the seller's estimate, so it carries a tilde.
 * A value of 0 is no estimate and reads "–" (plannedAmount: 0001959173-26-007025 read "~$0"),
 * and a notice filed with 0 shares gives no share count either: "–", not "– (0 sh)".
 */
function Planned({ sale }: { sale: PlannedSaleOut }) {
  const value = plannedAmount(sale.market_value);
  return (
    <span>
      <span className="text-dim">○ </span>
      {value === "–" ? <span className="text-dim">–</span> : value}
      {toNumber(sale.shares) ? <span className="text-dim"> ({shares(sale.shares)} sh)</span> : null}
    </span>
  );
}

export function PlannedSales({
  sales,
  symbol,
  seller,
}: {
  sales: PlannedSaleOut[] | null;
  /** The company whose page this is. */
  symbol?: string;
  /** Set on a seller's profile: the rows then name the company, not the seller. */
  seller?: { name: string; kind: string };
}) {
  const onProfile = seller !== undefined;
  const whose = seller ? seller.name : symbol;
  return (
    <section aria-labelledby="planned-title" className="mt-8">
      <div className="px-4 pb-3 md:px-6">
        <h2 id="planned-title" className="font-display text-[22px] font-medium leading-7">
          Planned sales (Form 144)
        </h2>
        <p className="mt-1 max-w-prose text-dim">
          {onProfile
            ? "Notices filed by this seller that they intend to sell shares soon."
            : "Notices that someone close to the company intends to sell shares soon."}{" "}
          A notice isn&apos;t a sale; when a later Form 4 from the same {onProfile ? "seller" : "person"} reports a sale
          within 90 days, it&apos;s marked here as likely reported.
        </p>
      </div>

      {sales === null ? (
        <p role="alert" className="mx-4 rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3 md:mx-6">
          <span aria-hidden className="text-beacon">! </span>
          The planned sales didn&apos;t load. Try again in a minute.
        </p>
      ) : sales.length === 0 ? (
        <p className="px-4 pb-2 text-dim md:px-6">
          No Form 144 notices on file {onProfile ? `from ${whose}` : `for ${whose}`}.
        </p>
      ) : (
        <>
          <ul className="num border-t border-hairline md:hidden">
            {sales.map((s) => (
              <li key={s.id} className="flex flex-col gap-0.5 border-b border-hairline px-4 py-2 text-[13px] leading-[18px]">
                <span className="flex items-center justify-between gap-3">
                  <Planned sale={s} />
                  <span className="shrink-0 text-dim">~{day(s.approx_sale_on)}</span>
                </span>
                <span className="min-w-0 truncate">
                  <Party sale={s} onProfile={onProfile} />
                </span>
                <span className="flex items-center justify-between gap-3 text-[12px]">
                  <Status sale={s} />
                  <FilingLink sale={s} />
                </span>
              </li>
            ))}
          </ul>

          <table className="num hidden w-full border-collapse text-[13px] leading-[18px] md:table">
            <caption className="sr-only">
              Form 144 notices of planned sales {onProfile ? `filed by ${whose}` : `in ${whose}`}, newest first
            </caption>
            <thead className="bg-raised text-left text-[12px] font-medium leading-4 text-dim">
              <tr>
                <th scope="col" aria-sort="descending" className="px-3 py-2 pl-6 font-medium">
                  Filed <span aria-hidden>↓</span>
                </th>
                <th scope="col" className="px-3 py-2 font-medium">{onProfile ? "Company" : "Seller"}</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Plans to sell</th>
                <th scope="col" className="px-3 py-2 font-medium">Around</th>
                <th scope="col" className="px-3 py-2 font-medium">Form 4</th>
                <th scope="col" className="px-3 py-2 pr-6 text-right font-medium">Filing</th>
              </tr>
            </thead>
            <tbody>
              {sales.map((s) => (
                <tr key={s.id} className="border-b border-hairline align-top">
                  <td className="whitespace-nowrap px-3 py-2.5 pl-6 text-dim">{day(s.filed_on)}</td>
                  <td className="max-w-[360px] px-3 py-2.5">
                    <Party sale={s} onProfile={onProfile} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-right">
                    <Planned sale={s} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-dim">{day(s.approx_sale_on)}</td>
                  <td className="px-3 py-2.5">
                    <Status sale={s} />
                  </td>
                  <td className="px-3 py-2.5 pr-6 text-right">
                    <FilingLink sale={s} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="max-w-prose px-4 py-3 text-[13px] text-dim md:px-6">
            Many people who file a Form 144 don&apos;t have to file a Form 4, so &ldquo;no Form 4 found&rdquo; doesn&apos;t
            mean the sale didn&apos;t happen. Values are the seller&apos;s estimate on the notice.
          </p>
        </>
      )}
    </section>
  );
}
