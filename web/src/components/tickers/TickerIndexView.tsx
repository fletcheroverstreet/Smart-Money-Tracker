"use client";

/**
 * The /tickers list: companies ranked by open-market insider trading.
 *
 * - The window (30 days, 90 days, 1 year), the ranking and the prefix filter
 *   all live in the URL, so every view is a shareable link.
 * - The ranking is done by the API over every company, not just the rows on
 *   screen, so clicking a column header asks the API for a new ranking.
 * - Desktop shows a table; phones show two-line rows (no cards).
 */
import { useInfiniteQuery } from "@tanstack/react-query";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/States";
import { apiGet } from "@/lib/api/client";
import type { DataCoverage, TickerActivityRow, TickerIndexPage } from "@/lib/api/types";
import { day, moneyCompact, toNumber } from "@/lib/format";
import { CoverageNote } from "@/components/ui/CoverageNote";
import { Segmented } from "@/components/ui/Segmented";

import { useSetParams } from "./controls";
import { incompleteFootnote, incompleteText, sideText } from "./indexText";
import { INDEX_DAYS, type IndexSort, daysFrom, indexKey, queryFrom, sortFrom } from "./params";

const PAGE_SIZE = 50;

// ---------------------------------------------------------------- cells

/**
 * "▲ 3 (+$1.2M)": count and value of purchases. A side with no trades shows a dash.
 * A side whose trades add nothing to the sum says why (sideText): "(not in dollar
 * totals)" for UMC's NT$ sales, "(no price reported)" only when no price was given.
 */
function SideCell({
  side,
  count,
  value,
  leftOut,
}: {
  side: "buy" | "sell";
  count: number;
  value: string | null;
  leftOut: number;
}) {
  if (count === 0) return <span className="text-dim">–</span>;
  const buy = side === "buy";
  const shown = sideText(side, value, leftOut);
  return (
    <span className={buy ? "text-inflow" : "text-outflow"}>
      <span aria-hidden>{buy ? "▲" : "▼"} </span>
      <span className="sr-only">{buy ? "Bought: " : "Sold: "}</span>
      {count} {shown.isNote ? <span className="text-dim">{shown.text}</span> : shown.text}
    </span>
  );
}

/** Net = purchases minus sales, with a glyph and a word so the sign never relies on color. */
function NetCell({ value }: { value: string | null }) {
  const n = toNumber(value);
  if (n === null) return <span className="text-dim">–</span>;
  if (n === 0) return <span className="text-dim">$0</span>;
  const buying = n > 0;
  return (
    <span className={buying ? "text-inflow" : "text-outflow"}>
      <span aria-hidden>{buying ? "▲" : "▼"} </span>
      {buying ? "+" : "−"}
      {moneyCompact(Math.abs(n))}
      <span className="sr-only">{buying ? " net buying" : " net selling"}</span>
    </span>
  );
}

/** Marks a row whose dollar totals don't include every trade it counts, and says why (incompleteText). */
function Incomplete({ row }: { row: TickerActivityRow }) {
  const text = incompleteText(row);
  if (text === null) return null;
  return (
    <span className="text-dim" title={text}>
      *<span className="sr-only">. {text}.</span>
    </span>
  );
}

// ---------------------------------------------------------------- table

const column = createColumnHelper<TickerActivityRow>();

/** Column id -> the API ranking it asks for (only these headers are sortable). */
const SORT_OF: Record<string, IndexSort> = { trades: "activity", bought: "buys", sold: "sells", net: "net" };
const COLUMN_OF: Record<IndexSort, string> = { activity: "trades", buys: "bought", sells: "sold", net: "net" };

const columns = [
  column.accessor((r) => r.security.ticker ?? "", {
    id: "company",
    header: "Company",
    cell: ({ row }) => {
      const r = row.original;
      return (
        <Link href={`/tickers/${encodeURIComponent(r.security.ticker ?? "")}`} className="group flex min-w-0 items-baseline gap-2">
          <span className="font-medium group-hover:underline">{r.security.ticker}</span>
          <span className="truncate text-dim">{r.security.name}</span>
        </Link>
      );
    },
  }),
  column.accessor((r) => r.buys + r.sells, {
    id: "trades",
    header: "Trades",
    cell: ({ row, getValue }) => (
      <>
        {getValue()}
        <Incomplete row={row.original} />
      </>
    ),
  }),
  column.accessor("buy_value", {
    id: "bought",
    header: "Bought",
    cell: ({ row }) => (
      <SideCell side="buy" count={row.original.buys} value={row.original.buy_value} leftOut={row.original.buys_left_out} />
    ),
  }),
  column.accessor("sell_value", {
    id: "sold",
    header: "Sold",
    cell: ({ row }) => (
      <SideCell side="sell" count={row.original.sells} value={row.original.sell_value} leftOut={row.original.sells_left_out} />
    ),
  }),
  column.accessor("net_value", { id: "net", header: "Net", cell: ({ getValue }) => <NetCell value={getValue()} /> }),
  column.accessor("insiders", { id: "insiders", header: "Insiders" }),
  column.accessor("last_traded_on", { id: "last", header: "Last trade", cell: ({ getValue }) => day(getValue()) }),
];

const RANKINGS: readonly { value: IndexSort; label: string; long: string }[] = [
  { value: "activity", label: "Trades", long: "Most trades first" },
  { value: "buys", label: "Bought", long: "Most dollars bought first" },
  { value: "sells", label: "Sold", long: "Most dollars sold first" },
  { value: "net", label: "Net", long: "Most net buying first" },
];

const HEADER_HINT: Record<string, string> = {
  trades: "Rank by number of trades",
  bought: "Rank by dollars bought",
  sold: "Rank by dollars sold",
  net: "Rank by net buying (bought minus sold)",
};

function IndexTable({ rows, sort, onSort }: { rows: TickerActivityRow[]; sort: IndexSort; onSort: (sort: IndexSort) => void }) {
  // The API does the sorting (manualSorting); TanStack only renders and tracks the state.
  // TanStack Table v8 hands back fresh functions each render, so the React Compiler
  // skips memoizing this component. That's expected; the table is small.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    state: { sorting: [{ id: COLUMN_OF[sort], desc: true }] },
    enableSortingRemoval: false,
  });

  return (
    <table className="num hidden w-full table-fixed border-collapse text-[13px] leading-[18px] md:table">
      <caption className="sr-only">Companies ranked by open-market insider trades. Column headers with a button change the ranking.</caption>
      <colgroup>
        <col />
        <col className="w-[88px]" />
        <col className="w-[184px]" />
        <col className="w-[184px]" />
        <col className="w-[128px]" />
        <col className="w-[84px]" />
        <col className="w-[120px]" />
      </colgroup>
      <thead className="bg-raised text-[12px] font-medium leading-4 text-dim">
        {table.getHeaderGroups().map((group) => (
          <tr key={group.id}>
            {group.headers.map((header) => {
              const id = header.column.id;
              const sortable = id in SORT_OF;
              const active = sortable && SORT_OF[id] === sort;
              const alignRight = id !== "company";
              return (
                <th
                  key={header.id}
                  scope="col"
                  aria-sort={sortable ? (active ? "descending" : "none") : undefined}
                  className={`px-3 py-2 font-medium first:pl-6 last:pr-6 ${alignRight ? "text-right" : "text-left"}`}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => onSort(SORT_OF[id]!)}
                      title={HEADER_HINT[id]}
                      className={`inline-flex items-center gap-1 rounded-[var(--radius-control)] hover:text-paper ${active ? "text-paper" : ""}`}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {/* The arrow only shows the active ranking; aria-sort announces it. */}
                      <span aria-hidden className={active ? "" : "invisible"}>
                        ↓
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
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id} className="border-b border-hairline hover:bg-raised/60">
            {row.getVisibleCells().map((cell) => (
              <td
                key={cell.id}
                className={`h-10 px-3 first:pl-6 last:pr-6 ${cell.column.id === "company" ? "min-w-0" : "text-right"} ${
                  cell.column.id === "last" ? "text-dim" : ""
                }`}
              >
                {flexRender(cell.column.columnDef.cell, cell.getContext())}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Phones: two lines per company, whole row is the link. */
function PhoneRows({ rows }: { rows: TickerActivityRow[] }) {
  return (
    <ul className="num md:hidden">
      {rows.map((r) => (
        <li key={`${r.security.ticker}-${r.issuer?.slug ?? ""}`} className="border-b border-hairline">
          <Link
            href={`/tickers/${encodeURIComponent(r.security.ticker ?? "")}`}
            className="flex min-h-14 flex-col justify-center gap-0.5 px-4 py-2 text-[13px] leading-[18px] active:bg-raised"
          >
            <span className="flex items-baseline justify-between gap-3">
              <span className="flex min-w-0 items-baseline gap-2">
                <span className="font-medium">{r.security.ticker}</span>
                <span className="truncate text-dim">{r.security.name}</span>
              </span>
              <span className="shrink-0">
                <NetCell value={r.net_value} />
              </span>
            </span>
            <span className="flex items-baseline justify-between gap-3 text-dim">
              <span className="truncate">
                {/* Counts of trades, not shares or dollars: "3 buys, 875 sales". */}
                {r.buys ? (
                  <span className="text-inflow">
                    <span aria-hidden>▲ </span>
                    {r.buys.toLocaleString("en-US")} {r.buys === 1 ? "buy" : "buys"}
                  </span>
                ) : null}
                {r.buys && r.sells ? ", " : null}
                {r.sells ? (
                  <span className="text-outflow">
                    <span aria-hidden>▼ </span>
                    {r.sells.toLocaleString("en-US")} {r.sells === 1 ? "sale" : "sales"}
                  </span>
                ) : null}
                {`${r.buys || r.sells ? ", " : ""}${r.insiders} ${r.insiders === 1 ? "insider" : "insiders"}`}
                <Incomplete row={r} />
              </span>
              <span className="shrink-0">{day(r.last_traded_on)}</span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- filter bar

function PrefixFilter({ value, onChange }: { value: string; onChange: (q: string) => void }) {
  // `text` is what's in the box; `value` is what's in the URL.
  const [text, setText] = useState(value);
  // Follow the URL when it changes from elsewhere (Back button, a shared link).
  // This is React's "adjust state while rendering" pattern, not an effect.
  const [seen, setSeen] = useState(value);
  if (value !== seen) {
    setSeen(value);
    if (text.trim() !== value) setText(value);
  }
  // Keep the latest callback without restarting the typing timer on every render.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);
  // Update the URL 250 ms after typing stops, not on every key press.
  useEffect(() => {
    const clean = text.trim();
    if (clean === value) return;
    const timer = window.setTimeout(() => onChangeRef.current(clean), 250);
    return () => window.clearTimeout(timer);
  }, [text, value]);

  return (
    <label className="flex min-w-0 flex-1 items-center gap-2 sm:max-w-80">
      <span className="sr-only">Filter by ticker or company name</span>
      <input
        type="search"
        value={text}
        maxLength={40}
        onChange={(e) => setText(e.target.value)}
        placeholder="Filter: ticker or name starts with…"
        autoComplete="off"
        spellCheck={false}
        className="h-11 w-full min-w-0 rounded-[var(--radius-control)] border border-rule-strong bg-night px-3 text-paper placeholder:text-dim md:h-9"
      />
    </label>
  );
}

// ---------------------------------------------------------------- view

export function TickerIndexView({
  initial,
  initialKey,
  coverage,
}: {
  initial: TickerIndexPage | null;
  initialKey: string;
  /** Which filing dates the site holds, to flag a window with missing days. */
  coverage: DataCoverage | null;
}) {
  const params = useSearchParams();
  const days = daysFrom(params.get("days"));
  const sort = sortFrom(params.get("sort"));
  const q = queryFrom(params.get("q"));
  const setParams = useSetParams();
  const key = indexKey(days, sort, q);

  const query = useInfiniteQuery({
    queryKey: ["ticker-index", days, sort, q.toUpperCase()],
    queryFn: ({ pageParam }) =>
      apiGet<TickerIndexPage>("/api/tickers", { days, sort, q: q || undefined, limit: PAGE_SIZE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    // Reuse the server's first page only when it was rendered for this exact view.
    initialData: initial && key === initialKey ? { pages: [initial], pageParams: [null] } : undefined,
  });

  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);
  const first = query.data?.pages[0];
  const footnote = incompleteFootnote(rows);
  const windowText = first ? `Trades from ${day(first.since)} to ${day(first.until)}` : null;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 border-b border-hairline px-4 py-3 md:px-6">
        <Segmented
          label="Period"
          value={days}
          options={INDEX_DAYS}
          onChange={(value) => setParams({ days: value === 30 ? null : String(value) })}
        />
        {/* Phones have no column headers, so the ranking gets its own control there. */}
        <div className="md:hidden">
          <Segmented
            label="Rank by"
            value={sort}
            options={RANKINGS}
            onChange={(value) => setParams({ sort: value === "activity" ? null : value })}
          />
        </div>
        <PrefixFilter value={q} onChange={(value) => setParams({ q: value || null })} />
        <p className="num text-[13px] text-dim md:ml-auto" aria-live="polite">
          {windowText}
        </p>
      </div>
      <CoverageNote coverage={coverage} from={first?.since} to={first?.until} className="border-b border-hairline px-4 py-2 md:px-6" />

      {query.isError && rows.length === 0 ? (
        <ErrorState title="The ranking didn't load" />
      ) : query.isPending ? (
        <SkeletonRows rows={12} height={40} />
      ) : rows.length === 0 ? (
        <EmptyState title={q ? `Nothing starts with “${q}”` : "No open-market insider trades in this window"}>
          {q
            ? `No company whose ticker or name starts with “${q}” had open-market insider trades in the last ${days} days. Try a longer window, or search everything with Ctrl K.`
            : "Try a longer window. Awards, option exercises and gifts aren't counted here."}
        </EmptyState>
      ) : (
        <>
          <PhoneRows rows={rows} />
          <IndexTable rows={rows} sort={sort} onSort={(value) => setParams({ sort: value === "activity" ? null : value })} />
          <div className="space-y-2 px-4 py-4 text-[13px] text-dim md:px-6">
            {query.hasNextPage ? (
              <button
                type="button"
                onClick={() => query.fetchNextPage()}
                disabled={query.isFetchingNextPage}
                className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 text-paper hover:bg-raised disabled:text-dim md:h-9"
              >
                {query.isFetchingNextPage ? "Loading more companies…" : "Show more companies"}
              </button>
            ) : null}
            {footnote ? <p>{footnote}</p> : null}
            <p>
              Counts open-market purchases and sales (codes P and S) from Forms 4 and 5, by trade date, after amendments.
              A joint filing by a fund and its managers counts as one insider.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
