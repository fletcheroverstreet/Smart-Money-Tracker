"use client";

/**
 * A person's trades, newest trade date first (design sheet §5.2).
 *
 * - The codes filter defaults to open-market buys and sells (codes P and S),
 *   the only trades that reflect the insider's own decision. "All codes" adds
 *   grants, option exercises, tax withholding and gifts.
 * - Clicking a row (or pressing Enter on its date) opens the quick-look drawer
 *   with the footnotes and a link to the filing.
 * - Joint filings show on every owner's page and say so.
 * - On phones each row becomes two lines instead of ten columns (no cards).
 */
import { useInfiniteQuery } from "@tanstack/react-query";
import { type ColumnDef, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import Link from "next/link";
import { useMemo } from "react";

import { useOpenTrade } from "@/components/drawer/QuickLook";
import { CodeTag } from "@/components/ui/CodeTag";
import { Direction } from "@/components/ui/Direction";
import { NotCountedTag, TradeValue, notCountedText, priceShown } from "@/components/ui/TradeValue";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/States";
import { apiGet } from "@/lib/api/client";
import type { TradePage, TradeRow } from "@/lib/api/types";
import { day, ownerTypeText, shares } from "@/lib/format";
import { LateFlag } from "@/components/ui/LateFlag";
import { Segmented } from "@/components/ui/Segmented";

import { type CodesFilter, tradesApiParams } from "./params";

// ---------------------------------------------------------------- small pieces

/** "same day", "1 day later", "12 days later". Disclosures give a trade date, never a time. */
function delayWords(days: number | null | undefined): string {
  if (days === null || days === undefined) return "delay unknown";
  if (days <= 0) return "same day";
  return days === 1 ? "1 day later" : `${days} days later`;
}

/** "Buy", "Sell", "Exercise and sell" or the code's meaning, for labels read aloud. */
function actionWords(t: TradeRow): string {
  if (t.direction === "buy") return "Buy";
  if (t.direction === "sell") return t.exercise_and_sell && t.code === "S" ? "Exercise and sell" : "Sell";
  return t.code_label;
}

function ActionCell({ t }: { t: TradeRow }) {
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
      {t.direction === "buy" || t.direction === "sell" ? (
        <Direction direction={t.direction} label={t.exercise_and_sell && t.code === "S" ? "Exercise and sell" : undefined} />
      ) : (
        <>
          <CodeTag code={t.code} label={t.code_label} />
          <span className="min-w-0 truncate text-dim" title={t.code_label}>
            {t.code_label}
          </span>
        </>
      )}
      {t.is_10b5_1 ? (
        <span
          className="rounded-[var(--radius-chip)] border border-hairline px-1 text-[12px] leading-4 text-dim"
          title="Made under a pre-arranged trading plan (Rule 10b5-1)"
        >
          10b5-1 plan
        </span>
      ) : null}
      {/* A listed row this person's totals leave out: "Also reported by X, counted once". */}
      <NotCountedTag trade={t} className="w-full" />
    </span>
  );
}

function SecurityCell({ t }: { t: TradeRow }) {
  const issuer = t.issuer?.name ?? "Unknown company";
  return (
    <span className="block min-w-0">
      <span className="block truncate">
        {t.ticker ? (
          <Link
            href={`/tickers/${t.ticker}`}
            onClick={(e) => e.stopPropagation()}
            className="font-medium hover:underline"
          >
            {t.ticker}
          </Link>
        ) : null}
        <span className={t.ticker ? "text-dim" : ""}>
          {t.ticker ? ", " : ""}
          {issuer}
        </span>
      </span>
      <span className="block truncate text-dim">
        {t.security_title ?? "Security not named"}
        {t.is_derivative ? " (derivative)" : ""}
      </span>
    </span>
  );
}

function AccountCell({ t, slug }: { t: TradeRow; slug: string }) {
  const listedUnderOther = t.owner.slug !== slug;
  return (
    <span className="block min-w-0">
      <span className="block truncate">{ownerTypeText(t.owner_type)}</span>
      {t.joint_owners > 1 ? (
        <span className="block truncate text-dim">
          Joint filing, {t.joint_owners} owners
          {listedUnderOther ? (
            <>
              {", counted under "}
              <Link
                href={`/people/${t.owner.slug}`}
                onClick={(e) => e.stopPropagation()}
                className="underline decoration-hairline underline-offset-2 hover:text-paper"
              >
                {t.owner.name}
              </Link>
            </>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}

function FilingLink({ t }: { t: TradeRow }) {
  return (
    <a
      href={t.filing_url}
      target="_blank"
      rel="noreferrer"
      onClick={(e) => e.stopPropagation()}
      className="inline-flex h-8 items-center whitespace-nowrap text-dim underline decoration-hairline underline-offset-2 hover:text-paper"
    >
      Form {t.form_type}
      <span aria-hidden>&nbsp;↗</span>
      <span className="sr-only"> on EDGAR (opens the SEC site)</span>
    </a>
  );
}

// ---------------------------------------------------------------- columns

/** Column widths, alignment and which columns hide on narrower screens. */
const COL_CLASS: Record<string, string> = {
  traded: "w-[112px] text-left",
  disclosed: "w-[140px] text-left",
  action: "w-[148px] text-left",
  security: "text-left",
  shares: "w-[104px] text-right",
  price: "hidden w-[96px] text-right xl:table-cell",
  value: "w-[96px] text-right",
  after: "hidden w-[112px] text-right xl:table-cell",
  account: "hidden w-[168px] text-left 2xl:table-cell",
  filing: "w-[88px] text-right",
};

function makeColumns(slug: string, open: (id: number) => void): ColumnDef<TradeRow>[] {
  return [
    {
      id: "traded",
      header: "Traded",
      cell: ({ row }) => {
        const t = row.original;
        return (
          // The date is the row's keyboard handle: Tab to it, Enter opens the quick look.
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              open(t.id);
            }}
            aria-label={`Open details: ${actionWords(t)}${t.ticker ? `, ${t.ticker}` : ""}, traded ${day(t.traded_on)}`}
            className="h-8 text-left hover:underline"
          >
            {day(t.traded_on)}
          </button>
        );
      },
    },
    {
      id: "disclosed",
      header: "Disclosed",
      cell: ({ row }) => {
        const t = row.original;
        return (
          <span className="block">
            <span className="block">{day(t.filed_on)}</span>
            <span className="flex flex-wrap gap-x-2 text-dim">
              <span className="whitespace-nowrap">{delayWords(t.delay_days)}</span>
              {t.is_late ? <LateFlag rule={t.deadline_rule} /> : null}
            </span>
          </span>
        );
      },
    },
    { id: "action", header: "Action", cell: ({ row }) => <ActionCell t={row.original} /> },
    { id: "security", header: "Company and security", cell: ({ row }) => <SecurityCell t={row.original} /> },
    { id: "shares", header: "Shares", cell: ({ row }) => shares(row.original.shares) },
    {
      id: "price",
      header: "Price",
      cell: ({ row }) => (
        <>
          {priceShown(row.original.price, row.original.data_issues, row.original.code)}
          {row.original.price_is_weighted_avg ? <span className="block text-[12px] leading-4 text-dim">avg</span> : null}
        </>
      ),
    },
    {
      id: "value",
      header: "Value",
      cell: ({ row }) => (
        <TradeValue
          direction={row.original.direction}
          value={row.original.value}
          issues={row.original.data_issues}
          counted={notCountedText(row.original) === null}
        />
      ),
    },
    { id: "after", header: "Owned after", cell: ({ row }) => shares(row.original.shares_owned_after) },
    { id: "account", header: "Account", cell: ({ row }) => <AccountCell t={row.original} slug={slug} /> },
    { id: "filing", header: "Filing", cell: ({ row }) => <FilingLink t={row.original} /> },
  ];
}

// ---------------------------------------------------------------- the panel

export function TradesPanel({
  slug,
  codes,
  onCodesChange,
  initial,
  initialCodes,
  totalTransactions,
}: {
  slug: string;
  codes: CodesFilter;
  onCodesChange: (codes: CodesFilter) => void;
  /** The first page, rendered on the server for `initialCodes`. */
  initial: TradePage | null;
  initialCodes: CodesFilter;
  /** Every transaction on record for this person, any code (for the empty-state hint). */
  totalTransactions: number;
}) {
  const openTrade = useOpenTrade();

  const query = useInfiniteQuery({
    queryKey: ["person-trades", slug, codes],
    queryFn: ({ pageParam }) =>
      apiGet<TradePage>(`/api/people/${encodeURIComponent(slug)}/trades`, tradesApiParams(codes, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    initialData: codes === initialCodes && initial ? { pages: [initial], pageParams: [null] } : undefined,
  });

  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);
  // openTrade changes identity with the URL; the columns only need the latest one when clicked.
  const columns = useMemo(() => makeColumns(slug, (id) => openTrade(id)), [slug, openTrade]);

  // (The lint note below is about React Compiler memoization, which this app doesn't enable.)
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(row.id),
    // The API returns trades newest first; the table shows that order and doesn't re-sort.
    manualSorting: true,
    state: { sorting: [{ id: "traded", desc: true }] },
  });

  const anyLate = rows.some((t) => t.is_late);
  const anyJoint = rows.some((t) => t.joint_owners > 1);
  const anyAvg = rows.some((t) => t.price_is_weighted_avg);
  const anyNotCounted = rows.some((t) => notCountedText(t) !== null);
  const otherCodes = totalTransactions - rows.length;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 md:px-6">
        <Segmented
          label="Codes"
          value={codes}
          options={[
            { value: "ps", label: "Buys and sells" },
            { value: "all", label: "All codes" },
          ]}
          onChange={onCodesChange}
        />
        <p aria-live="polite" className="num text-[13px] text-dim">
          {query.data && rows.length
            ? `${rows.length.toLocaleString("en-US")} ${rows.length === 1 ? "trade" : "trades"} shown${query.hasNextPage ? ", more below" : ""}. Newest trade first.`
            : ""}
        </p>
      </div>
      {codes === "ps" ? (
        <p className="px-4 pb-2 text-[12px] leading-4 text-dim md:px-6">
          Showing open-market buys (code P) and sells (code S), the trades an insider chooses to make. Grants, option
          exercises, tax withholding and gifts are under All codes.
        </p>
      ) : null}

      {query.isPending ? <SkeletonRows rows={10} height={48} /> : null}
      {query.isError && !query.data ? <ErrorState title="The trades didn't load" /> : null}

      {query.data && rows.length === 0 ? (
        <EmptyState title={codes === "ps" ? "No open-market buys or sells on record" : "No transactions on record yet"}>
          {codes === "ps" && otherCodes > 0 ? (
            <>
              <p>
                There {otherCodes === 1 ? "is" : "are"} {otherCodes.toLocaleString("en-US")} other{" "}
                {otherCodes === 1 ? "transaction" : "transactions"}, such as grants or option exercises.
              </p>
              <button
                type="button"
                onClick={() => onCodesChange("all")}
                className="mt-3 inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 text-paper hover:bg-raised md:h-9"
              >
                Show all codes
              </button>
            </>
          ) : (
            <p>Filings with only holdings (such as a first Form 3) don&apos;t list trades.</p>
          )}
        </EmptyState>
      ) : null}

      {rows.length ? (
        <>
          {/* Tablet and desktop: a real table. On a narrow tablet it scrolls sideways inside
              its own box; "relative" keeps screen-reader-only text inside that box too. */}
          <div className="relative hidden overflow-x-auto md:block">
            <table className="num w-full min-w-[820px] table-fixed border-collapse text-[13px] leading-[18px]">
              <caption className="sr-only">
                Trades, newest trade date first. Select a date to open the trade&apos;s details.
              </caption>
              <thead className="sticky top-0 z-10 bg-raised">
                {table.getHeaderGroups().map((group) => (
                  <tr key={group.id}>
                    {group.headers.map((header) => {
                      const sorted = header.column.getIsSorted();
                      return (
                        <th
                          key={header.id}
                          scope="col"
                          aria-sort={sorted === "desc" ? "descending" : undefined}
                          className={`h-9 border-b border-hairline px-3 text-[12px] font-medium leading-4 text-dim first:pl-6 last:pr-6 ${COL_CLASS[header.column.id] ?? ""}`}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {sorted === "desc" ? <span aria-hidden> ↓</span> : null}
                        </th>
                      );
                    })}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    onClick={() => openTrade(row.original.id)}
                    className="cursor-pointer border-b border-hairline align-top hover:bg-raised/60"
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className={`px-3 py-2 first:pl-6 last:pr-6 ${COL_CLASS[cell.column.id] ?? ""}`}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Phones: two-line rows. Line one: action, ticker, value. Line two: date, delay, shares. */}
          <ul className="border-t border-hairline md:hidden">
            {rows.map((t) => (
              <li key={t.id} className="border-b border-hairline">
                <button
                  type="button"
                  onClick={() => openTrade(t.id)}
                  className="num block min-h-14 w-full px-4 py-2 text-left text-[13px] leading-[18px] active:bg-raised"
                >
                  <span className="flex items-baseline justify-between gap-3">
                    <span className="flex min-w-0 items-baseline gap-2">
                      {t.direction === "buy" || t.direction === "sell" ? (
                        <Direction direction={t.direction} />
                      ) : (
                        <span className="shrink-0 text-dim">
                          <span className="font-mono">{t.code ?? "–"}</span> {t.code_label}
                        </span>
                      )}
                      <span className="truncate font-medium">{t.ticker ?? t.issuer?.name ?? ""}</span>
                    </span>
                    <span className="shrink-0">
                      <TradeValue
                        direction={t.direction}
                        value={t.value}
                        issues={t.data_issues}
                        counted={notCountedText(t) === null}
                      />
                    </span>
                  </span>
                  <span className="flex justify-between gap-3 text-dim">
                    <span className="truncate">
                      {day(t.traded_on)}, filed {delayWords(t.delay_days)}
                      {t.joint_owners > 1 ? ", joint filing" : ""}
                      {t.is_late ? <LateFlag rule={t.deadline_rule} className="ml-1.5" /> : null}
                    </span>
                    <span className="shrink-0">{shares(t.shares)} sh</span>
                  </span>
                  <NotCountedTag trade={t} />
                </button>
              </li>
            ))}
          </ul>

          <div className="space-y-1 px-4 pt-3 text-[12px] leading-4 text-dim md:px-6">
            {anyLate ? (
              <p>
                <span aria-hidden>⚑ </span>Late disclosure: filed after the legal deadline. A Form 4 is due by the end of
                the second business day after the trade. Delays are counted in calendar days.
              </p>
            ) : null}
            {anyJoint ? (
              <p>
                Joint filing: several owners reported the same shares on one form, so the trade appears on each
                owner&apos;s page. Site-wide totals count it once, under the first owner listed (or under the fund or
                other institution, if one is listed).
              </p>
            ) : null}
            {anyAvg ? <p>avg: the filing reports one weighted-average price for several trades made that day.</p> : null}
            {anyNotCounted ? (
              <p>
                Rows tagged &ldquo;counted once&rdquo; or &ldquo;Not in totals&rdquo; are listed but left out of this
                page&apos;s totals: another filing&apos;s copy of the same trade is the one counted, or a corrected row
                couldn&apos;t be matched to the row it corrects.
              </p>
            ) : null}
            <p>Select a trade for its footnotes and a link to the original filing on EDGAR.</p>
          </div>

          <div className="px-4 py-4 md:px-6">
            {query.hasNextPage ? (
              <button
                type="button"
                onClick={() => void query.fetchNextPage()}
                disabled={query.isFetchingNextPage}
                className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 hover:bg-raised disabled:text-dim md:h-9"
              >
                {query.isFetchingNextPage ? "Loading more…" : "Show 100 more"}
              </button>
            ) : (
              <p className="text-dim">That&apos;s every {codes === "ps" ? "open-market trade" : "transaction"} on record here.</p>
            )}
            {query.isError && query.data ? (
              <p role="alert" className="mt-2 text-dim">
                <span aria-hidden className="text-beacon">! </span>
                The next page didn&apos;t load. Try the button again in a minute.
              </p>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
