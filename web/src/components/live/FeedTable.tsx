"use client";

/**
 * The Live feed table.
 *
 * - Virtualized against the window, so thousands of rows scroll without jank
 *   and the page keeps one natural scrollbar (no box-in-a-box on phones).
 *   The server renders the first screenful of rows, so the page isn't an
 *   empty 4000 px box before JavaScript runs.
 * - Keyboard: the table is ONE tab stop. Focus it, then j/k or ↑/↓ to move,
 *   Home/End for the newest/oldest, Enter to open the quick look. The active
 *   row is announced through aria-activedescendant, which points at the row's
 *   first cell (in a grid only a cell may be the active descendant). The active
 *   row is always rendered, even when scrolled out of the virtual window, so
 *   that id always exists. It's drawn with a Paper bar (plus an outline while
 *   the grid has focus), not just a fill, and it never hides under the sticky
 *   bars.
 * - aria-rowcount is -1 (unknown) while older pages can still load.
 * - Links inside rows (person, ticker, code) stay clickable but are taken out
 *   of the tab order, so Tab moves past the table in one step.
 * - On phones each row becomes two lines instead of seven columns (no cards).
 */
import { defaultRangeExtractor, useWindowVirtualizer, type Range } from "@tanstack/react-virtual";
import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { CodeTag } from "@/components/ui/CodeTag";
import { Direction, SignedAmount } from "@/components/ui/Direction";
import { LateFlag } from "@/components/ui/LateFlag";
import { TradeValue, hasValueIssue } from "@/components/ui/TradeValue";
import { delayShort } from "@/lib/format";
import { useIsPhone } from "@/lib/useMediaQuery";
import { useTodayET } from "@/lib/useTodayET";

import { DetectedTime } from "./DetectedTime";

import type { FeedRowView } from "./feedRow";

const COLS = "grid-cols-[76px_52px_minmax(150px,176px)_minmax(0,1fr)_64px_96px_104px]";

/** The phone bottom bar (h-16) covers the last 64 px of the window. */
const PHONE_BOTTOM_BAR = 64;

/**
 * A feed row's size: the signed dollar value, "~" and the estimate for a Form 144, or "–".
 * Shared by the grid and the downlink band above it, so a row reads the same in both.
 */
export function SizeCell({ row }: { row: FeedRowView }) {
  if (hasValueIssue(row.dataIssues)) {
    // The filing's price isn't a dollar price per share: TradeValue says why, in the site's
    // words ("Other currency" for UMC's New Taiwan dollars, "Check filing" for a typo).
    return <TradeValue direction={row.direction} value={null} issues={row.dataIssues} />;
  }
  if (row.sizeApprox && row.sizeText !== "–") {
    // Form 144 gives an estimated market value for a sale that hasn't happened.
    return <span className="text-dim">~{row.sizeText}</span>;
  }
  return <SignedAmount direction={row.direction} text={row.sizeText} />;
}

export function FeedTable({
  rows,
  onOpen,
  onEndReached,
  hasMore,
  loadingMore,
  stickyTop,
}: {
  rows: FeedRowView[];
  onOpen: (row: FeedRowView) => void;
  onEndReached: () => void;
  hasMore: boolean;
  loadingMore: boolean;
  /** Height of the sticky filter bar above the table, in px. */
  stickyTop: number;
}) {
  const [active, setActive] = useState(0);
  const phone = useIsPhone();
  const bodyRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const [margin, setMargin] = useState(0);
  const [headerHeight, setHeaderHeight] = useState(0);
  const today = useTodayET();

  // The virtualizer needs to know where the list starts on the page. That moves
  // whenever something above it changes height (new Downlink arrivals, the today
  // strip, fonts), so re-measure on any layout change, not only on resize.
  useLayoutEffect(() => {
    const measure = () => setMargin(bodyRef.current ? bodyRef.current.getBoundingClientRect().top + window.scrollY : 0);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  // The sticky column header (desktop only; 0 px when hidden on phones).
  useEffect(() => {
    const header = headerRef.current;
    if (!header) return;
    const observer = new ResizeObserver(() => setHeaderHeight(header.offsetHeight));
    observer.observe(header);
    return () => observer.disconnect();
  }, []);

  // Everything sticky above the rows: the active row must scroll clear of it.
  const coveredTop = stickyTop + headerHeight;

  // Keyboard scrolling of the page (Tab, find-in-page) must also stop below the sticky bars.
  useEffect(() => {
    const html = document.documentElement;
    html.style.scrollPaddingTop = `${coveredTop + 8}px`;
    return () => {
      html.style.scrollPaddingTop = "";
    };
  }, [coveredTop]);

  const clampActive = Math.min(active, Math.max(0, rows.length - 1));
  const activeId = rows[clampActive]?.id;

  // The rows in the virtual window, plus the active row wherever it is, so the
  // cell aria-activedescendant names always exists (and keeps its place in order).
  const rangeExtractor = useCallback(
    (range: Range) => {
      const indexes = defaultRangeExtractor(range);
      if (activeId === undefined || indexes.includes(clampActive)) return indexes;
      return [...indexes, clampActive].sort((a, b) => a - b);
    },
    [activeId, clampActive],
  );

  const rowHeight = phone ? 56 : 40;
  const virtualizer = useWindowVirtualizer({
    count: rows.length,
    estimateSize: () => rowHeight,
    overscan: 10,
    scrollMargin: margin,
    scrollPaddingStart: coveredTop,
    scrollPaddingEnd: phone ? PHONE_BOTTOM_BAR : 0,
    // Server render and first client render: assume the top of a typical window,
    // so the first ~35 rows are real HTML instead of an empty box.
    initialRect: { width: 1280, height: 1000 },
    initialOffset: 0,
    rangeExtractor,
  });
  useEffect(() => virtualizer.measure(), [rowHeight, virtualizer]);

  const items = virtualizer.getVirtualItems();
  // The last row in view (not the last rendered one: the active row may be rendered far below).
  const lastIndex = virtualizer.range?.endIndex ?? (items.length ? items[items.length - 1]!.index : -1);
  useEffect(() => {
    if (hasMore && !loadingMore && lastIndex >= rows.length - 10) onEndReached();
  }, [lastIndex, rows.length, hasMore, loadingMore, onEndReached]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    // Only keys pressed on the grid itself: Enter on a link inside a row belongs to that link.
    if (event.target !== event.currentTarget) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const move = (to: number) => {
      const next = Math.max(0, Math.min(rows.length - 1, to));
      setActive(next);
      virtualizer.scrollToIndex(next, { align: "auto" });
      event.preventDefault();
    };
    if (event.key === "j" || event.key === "ArrowDown") move(clampActive + 1);
    else if (event.key === "k" || event.key === "ArrowUp") move(clampActive - 1);
    else if (event.key === "Home") move(0);
    else if (event.key === "End") move(rows.length - 1);
    else if (event.key === "Enter" && rows[clampActive]) {
      onOpen(rows[clampActive]);
      event.preventDefault();
    }
  };

  return (
    <>
      <div
        role="grid"
        aria-readonly
        aria-label="Latest disclosures, newest first. Use j and k or the arrow keys to move, Enter to open."
        // -1 = total unknown: older pages load as the reader scrolls.
        aria-rowcount={hasMore ? -1 : rows.length + 1}
        aria-activedescendant={activeId !== undefined ? `feed-cell-${activeId}` : undefined}
        tabIndex={0}
        onKeyDown={onKeyDown}
        // Closing a quick look hands focus back here when the row that opened it has scrolled away.
        data-focus-return
        className="feed-grid group min-w-0"
      >
        <div
          ref={headerRef}
          role="rowgroup"
          className="sticky z-10 hidden border-b border-hairline bg-raised md:block"
          style={{ top: "var(--filterbar-h, 0px)" }}
        >
          <div role="row" aria-rowindex={1} className={`grid ${COLS} gap-3 px-6 py-2 text-[12px] font-medium leading-4 text-dim`}>
            <span role="columnheader" aria-sort="descending" className="text-paper">
              Detected <span aria-hidden>↓</span>
            </span>
            <span role="columnheader">Form</span>
            <span role="columnheader">Action</span>
            <span role="columnheader">Who</span>
            <span role="columnheader">Ticker</span>
            <span role="columnheader" className="text-right">
              Size
            </span>
            <span role="columnheader" className="text-right">
              Delay
            </span>
          </div>
        </div>
        <div ref={bodyRef} role="rowgroup" style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {items.map((v) => {
            const row = rows[v.index]!;
            const isActive = v.index === clampActive;
            return (
              <div
                key={row.id}
                role="row"
                aria-rowindex={v.index + 2}
                aria-selected={isActive ? true : undefined}
                onClick={() => {
                  setActive(v.index);
                  onOpen(row);
                }}
                // The active row: Raised fill plus a 3 px Paper bar (11:1), so it doesn't rely on the
                // 1.2:1 fill alone; while the grid has focus it also gets a 1 px Paper outline.
                className={`absolute inset-x-0 top-0 cursor-pointer border-b border-hairline px-4 md:px-6 ${
                  isActive
                    ? "bg-raised shadow-[inset_3px_0_0_var(--color-paper)] group-focus:shadow-[inset_3px_0_0_var(--color-paper),inset_0_0_0_1px_var(--color-paper)]"
                    : "hover:bg-raised/60"
                }`}
                style={{ transform: `translateY(${v.start - virtualizer.options.scrollMargin}px)`, height: v.size }}
              >
                {phone ? (
                  <div className="num flex h-full flex-col justify-center text-[13px] leading-[18px]">
                    <div className="flex items-center justify-between gap-2">
                      <span id={`feed-cell-${row.id}`} role="gridcell" className="flex min-w-0 items-center gap-2">
                        <Direction direction={row.direction} label={row.actionLabel} />
                        <span className="font-medium">{row.ticker ?? ""}</span>
                      </span>
                      <span role="gridcell" className="shrink-0">
                        <SizeCell row={row} />
                      </span>
                    </div>
                    <div className="flex justify-between gap-2 text-dim">
                      <span role="gridcell" className="truncate">
                        {row.who}
                        {row.role ? `, ${row.role}` : ""}
                      </span>
                      <span role="gridcell" className="shrink-0">
                        {row.delayDays !== null ? delayShort(row.delayDays) : <DetectedTime iso={row.detectedAt} today={today} inline />}
                        {row.isLate ? <LateFlag rule={row.deadlineRule} className="ml-1.5" /> : null}
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className={`num grid h-full items-center gap-3 text-[13px] leading-[18px] ${COLS}`}>
                    <span id={`feed-cell-${row.id}`} role="gridcell" className="text-dim">
                      <DetectedTime iso={row.detectedAt} today={today} />
                    </span>
                    <span role="gridcell" className="font-mono text-[12px] text-dim">
                      {row.form}
                    </span>
                    <span role="gridcell" className="flex min-w-0 items-center gap-2">
                      <Direction direction={row.direction} label={row.actionLabel} />
                      {row.code && row.direction === "other" ? <CodeTag code={row.code} label={row.codeLabel} tabIndex={-1} /> : null}
                    </span>
                    <span role="gridcell" className="min-w-0 truncate">
                      {row.whoSlug ? (
                        <Link
                          href={`/people/${row.whoSlug}`}
                          tabIndex={-1}
                          onClick={(e) => e.stopPropagation()}
                          className="hover:underline"
                        >
                          {row.who}
                        </Link>
                      ) : (
                        row.who
                      )}
                      {row.role ? <span className="text-dim">, {row.role}</span> : null}
                      {row.jointOwners > 1 ? <span className="text-dim"> (+{row.jointOwners - 1} joint)</span> : null}
                    </span>
                    <span role="gridcell">
                      {row.ticker ? (
                        <Link
                          href={`/tickers/${row.ticker}`}
                          tabIndex={-1}
                          onClick={(e) => e.stopPropagation()}
                          className="font-medium hover:underline"
                        >
                          {row.ticker}
                        </Link>
                      ) : (
                        <span className="text-dim">–</span>
                      )}
                    </span>
                    <span role="gridcell" className="text-right">
                      <SizeCell row={row} />
                    </span>
                    <span role="gridcell" className="whitespace-nowrap text-right text-dim">
                      {delayShort(row.delayDays)}
                      {row.isLate ? <LateFlag rule={row.deadlineRule} className="ml-1.5" /> : null}
                    </span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
      {loadingMore ? <p className="px-4 py-3 text-dim md:px-6">Loading older disclosures…</p> : null}
      {!hasMore && rows.length > 0 ? (
        <p className="px-4 py-4 text-dim md:px-6">That&apos;s everything the Live feed holds. Older filings are on each person&apos;s and ticker&apos;s page.</p>
      ) : null}
    </>
  );
}
