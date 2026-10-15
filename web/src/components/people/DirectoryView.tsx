"use client";

/**
 * The insider directory: a name search, a role filter, a sortable table and a
 * "Show more" button.
 *
 * - Filters live in the URL (?q=&role=&sort=), so every view is a shareable
 *   link, and the server renders the first page for them.
 * - Sorting happens in the API (it has four orders, each with one direction),
 *   so the table is "manually sorted": a header click asks for a new order.
 * - On phones each row becomes two lines instead of six columns (no cards).
 */
import { useInfiniteQuery } from "@tanstack/react-query";
import { type ColumnDef, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { SearchButton } from "@/components/search/CommandPalette";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/States";
import { apiGet } from "@/lib/api/client";
import type { DirectoryPage, DirectoryRow } from "@/lib/api/types";
import { day } from "@/lib/format";

import {
  type DirectoryFilters,
  type DirectoryRole,
  type DirectorySort,
  MAX_QUERY,
  ROLE_OPTIONS,
  directoryApiParams,
  directoryFromParams,
  directoryHref,
  sameDirectory,
} from "./params";
import { kindShort } from "./text";

// ---------------------------------------------------------------- sorting

/** What each order means, in words (used for the sort menu and screen-reader hints). */
const SORT_TEXT: Record<DirectorySort, string> = {
  recent: "Most recent trade first",
  buys: "Most open-market buys first",
  sells: "Most open-market sells first",
  name: "Name, A to Z",
};

/** Columns the API can sort by map to its `sort` values; the rest aren't sortable. */
const SORTABLE: Partial<Record<string, DirectorySort>> = {
  name: "name",
  recent: "recent",
  buys: "buys",
  sells: "sells",
};

// ---------------------------------------------------------------- cells

function Count({ n, direction }: { n: number; direction: "buy" | "sell" }) {
  // A count of zero is a real zero (we counted and found none), not missing data.
  if (n === 0) return <span className="text-dim">0</span>;
  const buy = direction === "buy";
  return (
    <span className={buy ? "text-inflow" : "text-outflow"}>
      <span aria-hidden>{buy ? "▲ " : "▼ "}</span>
      <span className="sr-only">{buy ? "Buys: " : "Sells: "}</span>
      {n.toLocaleString("en-US")}
    </span>
  );
}

function RoleText({ row }: { row: DirectoryRow }) {
  if (!row.role && !row.organization) return <span className="text-dim">–</span>;
  return (
    <>
      {row.role ?? "Insider"}
      {row.organization ? <span className="text-dim">, {row.organization.name}</span> : null}
    </>
  );
}

function KindTag({ kind }: { kind: string }) {
  const tag = kindShort(kind);
  if (!tag) return null;
  return (
    <span className="shrink-0 rounded-[var(--radius-chip)] border border-hairline px-1.5 text-[12px] leading-4 text-dim">{tag}</span>
  );
}

function NameCell({ row }: { row: DirectoryRow }) {
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <Link href={`/people/${row.entity.slug}`} title={row.entity.name} className="truncate font-medium hover:underline">
        {row.entity.name}
      </Link>
      <KindTag kind={row.entity.kind} />
    </span>
  );
}

const COLUMNS: ColumnDef<DirectoryRow>[] = [
  { id: "name", header: "Name", cell: ({ row }) => <NameCell row={row.original} /> },
  { id: "role", header: "Latest role and company", cell: ({ row }) => <RoleText row={row.original} /> },
  { id: "recent", header: "Last trade", cell: ({ row }) => day(row.original.last_traded_on) },
  {
    id: "buys",
    header: "Buys",
    cell: ({ row }) => <Count n={row.original.open_market_buys} direction="buy" />,
  },
  {
    id: "sells",
    header: "Sells",
    cell: ({ row }) => <Count n={row.original.open_market_sells} direction="sell" />,
  },
  { id: "trades", header: "All transactions", cell: ({ row }) => row.original.trades.toLocaleString("en-US") },
];

/** Column widths and alignment (desktop). */
const COL_CLASS: Record<string, string> = {
  name: "w-[30%] text-left",
  role: "text-left",
  recent: "w-[124px] text-right",
  buys: "w-[84px] text-right",
  sells: "w-[84px] text-right",
  trades: "hidden w-[128px] text-right lg:table-cell",
};

// ---------------------------------------------------------------- the view

export function DirectoryView({
  initial,
  initialFilters,
}: {
  initial: DirectoryPage | null;
  initialFilters: DirectoryFilters;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const urlFilters = directoryFromParams(params);

  // The filters shown right now. They change at once on a click or a search
  // and the URL follows a moment later. `pending` lists the URLs this view
  // asked for, so when the URL changes we can tell our own update (ignore it:
  // the reader may already be typing the next letter) from a Back or Forward
  // step (follow it, including the text in the search box).
  const urlHref = directoryHref(urlFilters);
  const [filters, setFiltersState] = useState(urlFilters);
  const [text, setText] = useState(urlFilters.q);
  const [seenHref, setSeenHref] = useState(urlHref);
  const [pending, setPending] = useState<string[]>([]);
  if (seenHref !== urlHref) {
    setSeenHref(urlHref);
    const ours = pending.indexOf(urlHref);
    if (ours >= 0) {
      setPending(pending.slice(ours + 1));
    } else {
      setPending([]);
      setFiltersState(urlFilters);
      setText(urlFilters.q);
    }
  }

  const setFilters = (next: DirectoryFilters) => {
    const href = directoryHref(next);
    setFiltersState(next);
    setPending((prev) => [...prev, href]);
    router.replace(href, { scroll: false });
  };

  // Search as you type, but only after a 300 ms pause, so a fast typist sends one request.
  const typed = text.trim().slice(0, MAX_QUERY);
  useEffect(() => {
    if (typed === filters.q) return;
    const timer = window.setTimeout(() => {
      const next = { ...filters, q: typed };
      const href = directoryHref(next);
      setFiltersState(next);
      setPending((prev) => [...prev, href]);
      router.replace(href, { scroll: false });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [typed, filters, router]);

  const sameAsServer = sameDirectory(filters, initialFilters);
  const query = useInfiniteQuery({
    queryKey: ["insiders", filters.q, filters.role, filters.sort],
    queryFn: ({ pageParam }) => apiGet<DirectoryPage>("/api/people/insiders", directoryApiParams(filters, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    initialData: sameAsServer && initial ? { pages: [initial], pageParams: [null] } : undefined,
  });

  const rows = useMemo(() => {
    // Pages are offset-based on the server, so a row can repeat if the data moved between pages.
    const seen = new Set<string>();
    return (query.data?.pages.flatMap((p) => p.items) ?? []).filter((r) =>
      seen.has(r.entity.slug) ? false : (seen.add(r.entity.slug), true),
    );
  }, [query.data]);

  // TanStack Table holds the column model; the API does the sorting (manualSorting).
  // (The lint note below is about React Compiler memoization, which this app doesn't enable.)
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns: COLUMNS,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.entity.slug,
    manualSorting: true,
    enableSortingRemoval: false,
    state: { sorting: [{ id: filters.sort, desc: filters.sort !== "name" }] },
  });

  const searching = filters.q !== "" || filters.role !== null;

  return (
    <section aria-labelledby="directory-title">
      <h2 id="directory-title" className="sr-only">
        Insider directory
      </h2>

      <div className="flex flex-wrap items-end gap-3 px-4 py-4 md:px-6">
        <label className="flex w-full min-w-0 flex-col gap-1 sm:w-80">
          <span className="text-[12px] font-medium text-dim">Search by name</span>
          <input
            type="search"
            value={text}
            maxLength={MAX_QUERY}
            onChange={(e) => setText(e.target.value)}
            placeholder="For example, Smith or Jane Smith"
            autoComplete="off"
            spellCheck={false}
            className="h-11 w-full rounded-[var(--radius-control)] border border-rule-strong bg-night px-3 placeholder:text-dim md:h-9"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[12px] font-medium text-dim">Role</span>
          <select
            value={filters.role ?? ""}
            onChange={(e) => setFilters({ ...filters, role: (e.target.value || null) as DirectoryRole | null })}
            className="h-11 rounded-[var(--radius-control)] border border-rule-strong bg-night px-2 md:h-9"
          >
            {ROLE_OPTIONS.map((o) => (
              <option key={o.label} value={o.value ?? ""}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        {/* On phones the column headers are hidden, so sorting moves into a menu. */}
        <label className="flex flex-col gap-1 md:hidden">
          <span className="text-[12px] font-medium text-dim">Sort</span>
          <select
            value={filters.sort}
            onChange={(e) => setFilters({ ...filters, sort: e.target.value as DirectorySort })}
            className="h-11 rounded-[var(--radius-control)] border border-rule-strong bg-night px-2"
          >
            {(Object.keys(SORT_TEXT) as DirectorySort[]).map((s) => (
              <option key={s} value={s}>
                {SORT_TEXT[s]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <p className="px-4 pb-2 text-[12px] leading-4 text-dim md:px-6">
        Everyone who has filed a Form 3, 4 or 5 as a company insider: officers, directors and owners of more than
        10% of a company&apos;s shares. Buys and sells count open-market trades only (codes P and S); grants,
        option exercises and gifts are in all transactions.
      </p>

      <p aria-live="polite" className="num px-4 pb-2 text-[13px] text-dim md:px-6">
        {query.data
          ? rows.length === 0
            ? ""
            : `${rows.length.toLocaleString("en-US")} shown${query.hasNextPage ? ", more below" : ""}. ${SORT_TEXT[filters.sort]}.`
          : ""}
      </p>

      {query.isPending ? <SkeletonRows rows={12} height={40} /> : null}
      {query.isError && !query.data ? <ErrorState title="The directory didn't load" /> : null}

      {query.data && rows.length === 0 ? (
        <EmptyState title={searching ? "No insider matches this search" : "No insiders on record yet"}>
          {searching ? (
            <>
              <p>
                Names are matched as they appear on SEC filings, which often put the last name first. Try part of
                the name, or look up a company or ticker instead:
              </p>
              <div className="mt-3 flex justify-center">
                <SearchButton />
              </div>
            </>
          ) : (
            <p>Insiders appear here once their first filing has been loaded. The Status page shows what has loaded.</p>
          )}
        </EmptyState>
      ) : null}

      {rows.length ? (
        <>
          {/* Desktop and tablet: a real table with sortable headers. */}
          <div className="hidden md:block">
            <table className="num w-full table-fixed border-collapse text-[13px] leading-[18px]">
              <caption className="sr-only">
                Insiders. {SORT_TEXT[filters.sort]}. Column headers with a button change the order.
              </caption>
              <thead className="sticky top-0 z-10 bg-raised">
                {table.getHeaderGroups().map((group) => (
                  <tr key={group.id}>
                    {group.headers.map((header) => {
                      const sortKey = SORTABLE[header.column.id];
                      const sorted = header.column.getIsSorted();
                      const label = flexRender(header.column.columnDef.header, header.getContext());
                      return (
                        <th
                          key={header.id}
                          scope="col"
                          aria-sort={sortKey ? (sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none") : undefined}
                          className={`border-b border-hairline px-3 py-0 text-[12px] font-medium leading-4 text-dim first:pl-6 last:pr-6 ${COL_CLASS[header.column.id] ?? ""}`}
                        >
                          {sortKey ? (
                            <button
                              type="button"
                              onClick={() => setFilters({ ...filters, sort: sortKey })}
                              title={SORT_TEXT[sortKey]}
                              className={`inline-flex h-9 items-center gap-1 hover:text-paper ${sorted ? "text-paper" : ""}`}
                            >
                              {label}
                              <span aria-hidden className="w-3 text-center">
                                {sorted === "asc" ? "↑" : sorted === "desc" ? "↓" : ""}
                              </span>
                            </button>
                          ) : (
                            <span className="inline-flex h-9 items-center">{label}</span>
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
                        className={`h-10 truncate px-3 first:pl-6 last:pr-6 ${COL_CLASS[cell.column.id] ?? ""} ${
                          cell.column.id === "recent" ? "text-dim" : ""
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

          {/* Phones: two-line rows. Line one: name and counts. Line two: role and last trade. */}
          <ul className="border-t border-hairline md:hidden">
            {rows.map((r) => (
              <li key={r.entity.slug} className="relative border-b border-hairline">
                <Link href={`/people/${r.entity.slug}`} className="num block min-h-14 px-4 py-2 text-[13px] leading-[18px] active:bg-raised">
                  <span className="flex items-baseline justify-between gap-3">
                    <span className="flex min-w-0 items-baseline gap-2">
                      <span className="truncate font-medium">{r.entity.name}</span>
                      <KindTag kind={r.entity.kind} />
                    </span>
                    <span className="flex shrink-0 gap-3">
                      <Count n={r.open_market_buys} direction="buy" />
                      <Count n={r.open_market_sells} direction="sell" />
                    </span>
                  </span>
                  <span className="flex justify-between gap-3 text-dim">
                    <span className="truncate">
                      {r.role ?? "Insider"}
                      {r.organization ? `, ${r.organization.name}` : ""}
                    </span>
                    <span className="shrink-0">{r.last_traded_on ? day(r.last_traded_on) : "No trades"}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>

          <div className="px-4 py-4 md:px-6">
            {query.hasNextPage ? (
              <button
                type="button"
                onClick={() => void query.fetchNextPage()}
                disabled={query.isFetchingNextPage}
                className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 hover:bg-raised disabled:text-dim md:h-9"
              >
                {query.isFetchingNextPage ? "Loading more…" : "Show 50 more"}
              </button>
            ) : (
              <p className="text-dim">
                {searching ? "That's everyone matching." : "That's everyone in the directory so far."}
              </p>
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
    </section>
  );
}
