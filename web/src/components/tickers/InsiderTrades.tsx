"use client";

/**
 * The ticker page's insider trades table (Forms 4 and 5), newest first.
 *
 * - The code filter lives in the URL (?codes=P, S or all; open-market buys and
 *   sells by default).
 * - Clicking a row (or its date button, from the keyboard) opens the quick look.
 * - Column headers sort the rows already loaded; "Load older trades" fetches
 *   the next page from the API (keyset paging, newest first).
 * - Phones get two-line rows instead of eight columns.
 */
import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import {
  type SortingState,
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { useOpenTrade } from "@/components/drawer/QuickLook";
import { CodeTag } from "@/components/ui/CodeTag";
import { Direction } from "@/components/ui/Direction";
import { LateFlag } from "@/components/ui/LateFlag";
import { NotCountedTag, TradeValue, notCountedText, priceShown } from "@/components/ui/TradeValue";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/States";
import { apiGet } from "@/lib/api/client";
import type { TradePage, TradeRow } from "@/lib/api/types";
import { day, delayShort, ownerTypeText, shares, toNumber } from "@/lib/format";
import { Segmented } from "@/components/ui/Segmented";

import { useSetParams } from "./controls";
import { CODE_FILTERS, type CodesFilter, codesFrom } from "./params";

const PAGE_SIZE = 100;

/**
 * The action in words. `plainCode` shows the transaction code as text instead
 * of the CodeTag button: phone rows are one big button already, and a button
 * inside a button is invalid (and unreachable by keyboard).
 */
function Action({ row, plainCode = false }: { row: TradeRow; plainCode?: boolean }) {
  const market = row.direction === "buy" || row.direction === "sell";
  return (
    <span className="flex min-w-0 items-center gap-2">
      {market ? (
        <Direction direction={row.direction} label={row.exercise_and_sell && row.code === "S" ? "Exercise and sell" : undefined} />
      ) : (
        <span className="truncate text-dim">{row.code_label}</span>
      )}
      {market ? null : plainCode ? (
        <span className="shrink-0 font-mono text-[12px] text-dim">{row.code}</span>
      ) : (
        <CodeTag code={row.code} label={row.code_label} />
      )}
      {row.is_derivative ? (
        <span className="shrink-0 text-[12px] text-dim" title={row.security_title ?? undefined}>
          derivative
        </span>
      ) : null}
    </span>
  );
}

function Who({ row, link = true }: { row: TradeRow; link?: boolean }) {
  return (
    <span className="block min-w-0 truncate">
      {link ? (
        <Link
          href={`/people/${row.owner.slug}`}
          onClick={(e) => e.stopPropagation()}
          className="hover:underline"
        >
          {row.owner.name}
        </Link>
      ) : (
        row.owner.name
      )}
      {row.joint_owners > 1 ? <span className="text-dim"> +{row.joint_owners - 1} joint</span> : null}
      {row.owner_type !== "self" ? <span className="text-dim"> ({ownerTypeText(row.owner_type)})</span> : null}
    </span>
  );
}

/** "2 d" plus the Dim ⚑ Late flag when the filing was late (never Beacon: it can be on many rows). */
function Filed({ row }: { row: TradeRow }) {
  return (
    <span className="text-dim">
      {delayShort(row.delay_days)}
      {row.is_late ? <LateFlag rule={row.deadline_rule} className="ml-1.5" /> : null}
    </span>
  );
}

const column = createColumnHelper<TradeRow>();

function useColumns(openTrade: (id: number) => void) {
  return useMemo(
    () => [
      column.accessor((r) => r.traded_on ?? undefined, {
        id: "traded",
        header: "Traded",
        sortUndefined: "last",
        cell: ({ row }) => (
          // A real button, so keyboard users can open the quick look too.
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              openTrade(row.original.id);
            }}
            className="whitespace-nowrap text-left underline decoration-hairline underline-offset-2 hover:decoration-paper"
          >
            {day(row.original.traded_on)}
            <span className="sr-only">: open the details of this trade</span>
          </button>
        ),
      }),
      column.accessor((r) => r.owner.name, {
        id: "insider",
        header: "Insider",
        enableSorting: false,
        cell: ({ row }) => (
          <>
            <Who row={row.original} />
            {/* A listed row the ticker's totals leave out: "Also reported by X, counted once". */}
            <NotCountedTag trade={row.original} />
          </>
        ),
      }),
      column.display({ id: "action", header: "Action", cell: ({ row }) => <Action row={row.original} /> }),
      column.accessor((r) => toNumber(r.shares) ?? undefined, {
        id: "shares",
        header: "Shares",
        sortUndefined: "last",
        cell: ({ row }) => shares(row.original.shares),
      }),
      column.accessor((r) => toNumber(r.price) ?? undefined, {
        id: "price",
        header: "Price",
        sortUndefined: "last",
        cell: ({ row }) => (
          <span title={row.original.price_is_weighted_avg ? "Weighted average of several prices" : undefined}>
            {priceShown(row.original.price, row.original.data_issues, row.original.code)}
            {row.original.price_is_weighted_avg ? <span className="text-dim"> avg</span> : null}
          </span>
        ),
      }),
      column.accessor((r) => toNumber(r.value) ?? undefined, {
        id: "value",
        header: "Value",
        sortUndefined: "last",
        cell: ({ row }) => (
          <TradeValue
            direction={row.original.direction}
            value={row.original.value}
            issues={row.original.data_issues}
            counted={notCountedText(row.original) === null}
          />
        ),
      }),
      column.accessor((r) => r.delay_days ?? undefined, {
        id: "filed",
        header: "Filed after",
        sortUndefined: "last",
        cell: ({ row }) => <Filed row={row.original} />,
      }),
      column.display({
        id: "filing",
        header: "Filing",
        cell: ({ row }) => (
          <a
            href={row.original.filing_url}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="font-mono text-[12px] underline decoration-hairline underline-offset-2 hover:decoration-paper"
          >
            {row.original.form_type}
            <span className="sr-only"> filing on the SEC site (opens a new tab)</span>
          </a>
        ),
      }),
    ],
    [openTrade],
  );
}

const RIGHT = new Set(["shares", "price", "value", "filed", "filing"]);

export function InsiderTrades({
  symbol,
  initial,
  initialCodes,
}: {
  symbol: string;
  initial: TradePage | null;
  initialCodes: CodesFilter;
}) {
  const params = useSearchParams();
  const codes = codesFrom(params.get("codes"));
  const setParams = useSetParams();
  const openTrade = useOpenTrade();

  const query = useInfiniteQuery({
    queryKey: ["ticker-insiders", symbol, codes],
    queryFn: ({ pageParam }) =>
      apiGet<TradePage>(`/api/tickers/${encodeURIComponent(symbol)}/insiders`, { codes, limit: PAGE_SIZE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    initialData: codes === initialCodes && initial ? { pages: [initial], pageParams: [null] } : undefined,
    placeholderData: keepPreviousData,
  });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);

  const [sorting, setSorting] = useState<SortingState>([{ id: "traded", desc: true }]);
  const columns = useColumns(openTrade);
  // TanStack Table v8 hands back fresh functions each render, so the React Compiler
  // skips memoizing this component. That's expected; the table is small.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    sortDescFirst: true,
  });
  const sortedRows = table.getRowModel().rows;
  const isDefaultSort = sorting.length === 1 && sorting[0]?.id === "traded" && sorting[0].desc;

  return (
    <section aria-labelledby="insiders-title" className="mt-6">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 pb-3 md:px-6">
        <h2 id="insiders-title" className="font-display text-[22px] font-medium leading-7">
          Insider trades
        </h2>
        <Segmented
          label="Show"
          value={codes}
          options={CODE_FILTERS}
          onChange={(value) => setParams({ codes: value === "P,S" ? null : value })}
        />
      </div>

      {query.isError && rows.length === 0 ? (
        <ErrorState title="The insider trades didn't load" />
      ) : query.isPending ? (
        <SkeletonRows rows={8} height={40} />
      ) : rows.length === 0 ? (
        <EmptyState title={codes === "all" ? "No insider filings on file" : "No matching insider trades on file"}>
          {codes === "all"
            ? "We haven't stored any Form 4 or 5 for this company yet."
            : "Choose “All codes” to include grants, option exercises, tax withholding and gifts."}
        </EmptyState>
      ) : (
        <div aria-busy={query.isFetching} className={query.isPlaceholderData ? "opacity-60" : ""}>
          {/* Phones: two lines per trade; the whole row opens the quick look. */}
          <ul className="num border-t border-hairline md:hidden">
            {sortedRows.map(({ original: r }) => (
              <li key={r.id} className="border-b border-hairline">
                <button
                  type="button"
                  onClick={() => openTrade(r.id)}
                  className="flex min-h-14 w-full flex-col justify-center gap-0.5 px-4 py-2 text-left text-[13px] leading-[18px] active:bg-raised"
                >
                  <span className="flex w-full items-center justify-between gap-3">
                    <Action row={r} plainCode />
                    <span className="shrink-0">
                      <TradeValue
                        direction={r.direction}
                        value={r.value}
                        issues={r.data_issues}
                        counted={notCountedText(r) === null}
                      />
                    </span>
                  </span>
                  <span className="flex w-full items-center justify-between gap-3 text-dim">
                    <Who row={r} link={false} />
                    <span className="shrink-0">{day(r.traded_on)}</span>
                  </span>
                  <NotCountedTag trade={r} />
                </button>
              </li>
            ))}
          </ul>

          {/* Scrolls sideways on narrow desktops; "relative" keeps the sr-only text inside the scroll box. */}
          <div className="relative hidden overflow-x-auto md:block">
          <table className="num w-full border-collapse text-[13px] leading-[18px]">
            <caption className="sr-only">
              Insider trades in {symbol}. Select a column header to sort; select a date to open the trade&apos;s details.
            </caption>
            <thead className="bg-raised text-[12px] font-medium leading-4 text-dim">
              {table.getHeaderGroups().map((group) => (
                <tr key={group.id}>
                  {group.headers.map((header) => {
                    const sorted = header.column.getIsSorted();
                    const canSort = header.column.getCanSort();
                    return (
                      <th
                        key={header.id}
                        scope="col"
                        aria-sort={canSort ? (sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none") : undefined}
                        className={`whitespace-nowrap px-3 py-2 font-medium first:pl-6 last:pr-6 ${RIGHT.has(header.column.id) ? "text-right" : "text-left"}`}
                      >
                        {canSort ? (
                          <button
                            type="button"
                            onClick={header.column.getToggleSortingHandler()}
                            className={`inline-flex items-center gap-1 hover:text-paper ${sorted ? "text-paper" : ""}`}
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            <span aria-hidden className={sorted ? "" : "invisible"}>
                              {sorted === "asc" ? "↑" : "↓"}
                            </span>
                          </button>
                        ) : (
                          flexRender(header.column.columnDef.header, header.getContext())
                        )}
                      </th>
                    );
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {sortedRows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => openTrade(row.original.id)}
                  className="cursor-pointer border-b border-hairline hover:bg-raised/60"
                >
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className={`h-10 px-3 first:pl-6 last:pr-6 ${RIGHT.has(cell.column.id) ? "text-right" : ""} ${
                        cell.column.id === "insider" ? "max-w-[280px]" : ""
                      }`}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          </div>

          <div className="flex flex-wrap items-center gap-3 px-4 py-3 text-[13px] text-dim md:px-6">
            {query.hasNextPage ? (
              <button
                type="button"
                onClick={() => query.fetchNextPage()}
                disabled={query.isFetchingNextPage}
                className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 text-paper hover:bg-raised disabled:text-dim md:h-9"
              >
                {query.isFetchingNextPage ? "Loading older trades…" : "Load older trades"}
              </button>
            ) : null}
            <span className="num">
              {rows.length} {rows.length === 1 ? "trade" : "trades"}
              {query.hasNextPage ? " loaded so far" : ""}
              {query.hasNextPage && !isDefaultSort ? "; sorting covers the loaded trades only" : ""}
            </span>
            {rows.some((r) => notCountedText(r) !== null) ? (
              <p className="w-full text-[12px] leading-4">
                Rows tagged &ldquo;counted once&rdquo; or &ldquo;Not in totals&rdquo; are listed but left out of the
                totals: another filing&apos;s copy of the same trade is the one counted, or a corrected row
                couldn&apos;t be matched to the row it corrects.
              </p>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}
