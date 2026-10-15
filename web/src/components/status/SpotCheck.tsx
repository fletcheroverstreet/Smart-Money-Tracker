"use client";

/**
 * Spot check (spec §11): random recent rows from each source (Form 3/4/5
 * trades and Form 144 notices) beside links to the original documents, so
 * anyone can check our numbers against the SEC's by eye.
 *
 * The sample is random each time, so it does NOT refresh on a timer (rows
 * would change under the reader). "Draw another sample" asks for a new one.
 */
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { useOpenTrade } from "@/components/drawer/QuickLook";
import { ExternalLink } from "@/components/info/ExternalLink";
import { CodeTag } from "@/components/ui/CodeTag";
import { TradeValue, priceShown } from "@/components/ui/TradeValue";
import { ErrorState, SkeletonRows } from "@/components/ui/States";
import { apiGet } from "@/lib/api/client";
import type { CodeOut, SpotCheckRow } from "@/lib/api/types";
import { plannedAmount } from "@/lib/dataIssues";
import { day, shares } from "@/lib/format";

/** The API types these loosely (dates and decimals arrive as strings); keep only real strings. */
function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

const linkClass = "hover:underline";

/** The filer, linked to their profile as in every other table. */
function Owner({ row }: { row: SpotCheckRow }) {
  if (!row.owner_slug) return <>{row.owner}</>;
  return (
    <Link href={`/people/${row.owner_slug}`} className={linkClass}>
      {row.owner}
    </Link>
  );
}

/** The company (named as on every other page), linked to its ticker page when it has a ticker. */
function Issuer({ row }: { row: SpotCheckRow }) {
  if (!row.issuer) return <span className="text-dim">–</span>;
  if (!row.issuer_ticker) return <>{row.issuer}</>;
  return (
    <Link href={`/tickers/${encodeURIComponent(row.issuer_ticker)}`} className={linkClass}>
      {row.issuer} <span className="font-mono text-[12px] text-dim">{row.issuer_ticker}</span>
    </Link>
  );
}

const th = "px-3 py-2 text-left text-[12px] font-medium leading-4 text-dim";
const td = "px-3 py-2.5 align-top";
const button =
  "inline-flex h-8 items-center rounded-[var(--radius-control)] border border-rule-strong px-2.5 text-[13px] hover:bg-raised max-md:h-11";

export function SpotCheck({
  initial,
  codes,
  limit,
  headingLevel = 2,
}: {
  initial: SpotCheckRow[] | null;
  codes: CodeOut[] | null;
  limit: number;
  headingLevel?: 1 | 2;
}) {
  const openTrade = useOpenTrade();
  const labels = new Map((codes ?? []).map((c) => [c.code, c.label]));
  const sample = useQuery({
    queryKey: ["spot-check", limit],
    queryFn: () => apiGet<SpotCheckRow[]>("/api/status/spot-check", { limit }),
    initialData: initial ?? undefined,
    // A sample stays put until the reader asks for a new one.
    staleTime: Infinity,
  });
  const all = sample.data ?? [];
  // Form 3/4/5 trades, then Form 144 notices (the API draws up to `limit` of each).
  const rows = all.filter((r) => r.source !== "form144" && r.transaction_id !== null);
  const notices = all.filter((r) => r.source === "form144");
  const Heading = headingLevel === 1 ? "h1" : "h2";

  return (
    <section id="spot-check" aria-labelledby="spot-check-title" className="scroll-mt-4 px-4 pt-8 md:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <Heading
            id="spot-check-title"
            className={
              headingLevel === 1
                ? "font-display text-[32px] font-semibold leading-9"
                : "font-display text-[22px] font-medium leading-7"
            }
          >
            Spot check
          </Heading>
          <p className="mt-1 max-w-[72ch] text-dim">
            Up to {limit} transactions and {limit} Form 144 notices picked at random from filings of the last 30
            days. Open the filing on EDGAR and compare the shares, price and date with ours. If anything differs,
            the original filing is right.
          </p>
        </div>
        <button type="button" onClick={() => void sample.refetch()} disabled={sample.isFetching} className={`${button} disabled:text-dim`}>
          {sample.isFetching ? "Drawing…" : "Draw another sample"}
        </button>
      </div>

      <div className="mt-3">
        {sample.isPending ? <SkeletonRows rows={Math.min(limit, 10)} height={40} /> : null}
        {sample.isError && !sample.data ? <ErrorState title="The spot check didn't load" /> : null}
        {sample.data && all.length === 0 ? (
          <p className="rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3 text-dim">
            No transactions from the last 30 days yet. Once filings arrive, a random sample shows here.
          </p>
        ) : null}

        {rows.length ? (
          <>
            <div className="hidden overflow-hidden rounded-[var(--radius-panel)] border border-hairline bg-console md:block">
              <table className="num w-full border-collapse text-[13px] leading-[18px]">
                <caption className="sr-only">A random sample of recent transactions with links to the original filings</caption>
                <thead className="bg-raised">
                  <tr>
                    <th scope="col" className={th}>Form</th>
                    <th scope="col" className={th}>Filer</th>
                    <th scope="col" className={th}>Company</th>
                    <th scope="col" className={th}>Code</th>
                    <th scope="col" className={th}>Traded</th>
                    <th scope="col" className={`${th} text-right`}>Shares</th>
                    <th scope="col" className={`${th} text-right`}>Price</th>
                    <th scope="col" className={`${th} text-right`}>Value</th>
                    <th scope="col" className={th}>Check it</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.accession + String(r.transaction_id)} className="border-t border-hairline">
                      <td className={`${td} font-mono text-[12px] text-dim`}>{r.form_type}</td>
                      <th scope="row" className={`${td} text-left font-normal`}>
                        <Owner row={r} />
                      </th>
                      <td className={td}>
                        <Issuer row={r} />
                      </td>
                      <td className={td}>
                        <CodeTag code={r.code} label={r.code ? labels.get(r.code) : undefined} />
                      </td>
                      <td className={`${td} whitespace-nowrap`}>{day(text(r.traded_on))}</td>
                      <td className={`${td} text-right`}>{shares(text(r.shares))}</td>
                      <td className={`${td} text-right`}>{priceShown(text(r.price), r.data_issues, r.code)}</td>
                      <td className={`${td} text-right`}>
                        {/* A figure that can't be right as filed reads "Check filing", as in every table. */}
                        <TradeValue direction={r.direction} value={text(r.value)} issues={r.data_issues} />
                      </td>
                      <td className={td}>
                        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                          <ExternalLink href={r.filing_url}>Filing</ExternalLink>
                          {r.document_url ? <ExternalLink href={r.document_url}>XML</ExternalLink> : null}
                          <button
                            type="button"
                            onClick={() => (r.transaction_id !== null ? openTrade(r.transaction_id) : undefined)}
                            className="underline decoration-rule-strong underline-offset-2 hover:decoration-paper"
                          >
                            Our record
                          </button>
                        </span>
                        <span className="mt-0.5 block font-mono text-[12px] text-dim">{r.accession}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <ul className="num divide-y divide-hairline rounded-[var(--radius-panel)] border border-hairline bg-console text-[13px] leading-[18px] md:hidden">
              {rows.map((r) => (
                <li key={r.accession + String(r.transaction_id)} className="space-y-1 px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0">
                      <span className="font-medium">
                        <Owner row={r} />
                      </span>
                      <span className="block text-dim">
                        <Issuer row={r} />
                      </span>
                    </span>
                    <CodeTag code={r.code} label={r.code ? labels.get(r.code) : undefined} />
                  </div>
                  <p className="text-dim">
                    Form {r.form_type}, traded <span className="text-paper">{day(text(r.traded_on))}</span>:{" "}
                    <span className="text-paper">{shares(text(r.shares))}</span> shares at{" "}
                    <span className="text-paper">{priceShown(text(r.price), r.data_issues, r.code)}</span>, value{" "}
                    <TradeValue direction={r.direction} value={text(r.value)} issues={r.data_issues} />
                  </p>
                  <div className="flex flex-wrap gap-2 pt-1">
                    <ExternalLink href={r.filing_url} className={button}>
                      Filing
                    </ExternalLink>
                    {r.document_url ? (
                      <ExternalLink href={r.document_url} className={button}>
                        XML
                      </ExternalLink>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => (r.transaction_id !== null ? openTrade(r.transaction_id) : undefined)}
                      className={button}
                    >
                      Our record
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : null}

        {notices.length ? (
          <div className="mt-6">
            <h3 className="font-medium">Form 144 notices</h3>
            <p className="mt-0.5 text-[13px] leading-[18px] text-dim">
              A notice of a planned sale. Compare the planned shares, the estimated value and the date with the
              filing.
            </p>
            <ul className="num mt-2 divide-y divide-hairline rounded-[var(--radius-panel)] border border-hairline bg-console text-[13px] leading-[18px]">
              {notices.map((r) => (
                <li key={r.accession} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
                  <span className="min-w-0">
                    <span className="font-medium">
                      <Owner row={r} />
                    </span>
                    <span className="block text-dim">
                      <Issuer row={r} />
                    </span>
                    <span className="block text-dim">
                      Plans to sell <span className="text-paper">{shares(text(r.shares))}</span> shares
                      {/* The filer's estimate of the market value: "~" says it's an estimate, once.
                          A value of 0 ("0.00", 0001959173-26-007025) is no estimate: left out. */}
                      {plannedAmount(text(r.value)) !== "–" ? (
                        <>
                          {" "}
                          worth <span className="text-paper">{plannedAmount(text(r.value))}</span>
                        </>
                      ) : null}
                      , around <span className="text-paper">{day(text(r.traded_on))}</span>
                    </span>
                    <span className="mt-0.5 block font-mono text-[12px] text-dim">{r.accession}</span>
                  </span>
                  <span className="flex flex-wrap gap-2">
                    <ExternalLink href={r.filing_url} className={button}>
                      Filing
                    </ExternalLink>
                    {r.document_url ? (
                      <ExternalLink href={r.document_url} className={button}>
                        XML
                      </ExternalLink>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}
