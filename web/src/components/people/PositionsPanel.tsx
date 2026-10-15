"use client";

/**
 * "Positions over time" (design sheet §5.2, insider variant): the shares a
 * person reported owning after each transaction, one line per company, share
 * class and way of holding (directly, or through a trust or a family member).
 *
 * - At most three lines at once, because the design has three chart colors
 *   that don't already mean something (teal is buy, orange is sell). The
 *   reader picks which. A line keeps its color while it stays picked.
 * - Every line also has its own marker shape, a legend and an end label, so
 *   color is never the only way to tell them apart.
 * - The "Table view" toggle (fixed label, aria-pressed) shows the same numbers
 *   as a table, for screen readers, keyboards and anyone who prefers numbers.
 * - A note under the chart names any stretch of the drawn period whose filings
 *   aren't loaded yet: a line can miss changes filed then.
 */
import dynamic from "next/dynamic";
import Link from "next/link";
import { useMemo, useState } from "react";

import { CoverageNote } from "@/components/ui/CoverageNote";
import { EmptyState } from "@/components/ui/States";
import type { DataCoverage, PositionSeries } from "@/lib/api/types";
import { day, shares, toNumber } from "@/lib/format";
import { useIsPhone } from "@/lib/useMediaQuery";

import type { PlotSeries } from "./PositionsChart";

// ECharts only runs in the browser; until it loads, a fixed-height placeholder keeps the layout still.
const PositionsChart = dynamic(() => import("./PositionsChart"), {
  ssr: false,
  loading: () => <div className="skeleton h-[260px] md:h-[320px]" />,
});

/** Tailwind needs these class names written out in full to generate them. */
const SWATCH = ["bg-series-1", "bg-series-2", "bg-series-3"] as const;
/** The same shapes as the chart markers (circle, square, diamond). */
const SHAPE = ["●", "■", "◆"] as const;
const MAX_LINES = 3;
/** Picker rows shown before "Show all". */
const PICKER_ROWS = 8;

interface Line {
  key: string;
  label: string;
  short: string;
  ownership: string;
  issuerName: string;
  ticker: string | undefined;
  securityTitle: string;
  points: { t: number; day: string; shares: number; filingUrl: string }[];
}

function toUtc(isoDay: string): number {
  const [y, m, d] = isoDay.split("-").map(Number);
  return Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

function linesFrom(series: PositionSeries[], tickers: Record<string, string>): Line[] {
  return series.map((s) => {
    const ticker = tickers[s.issuer.slug];
    const who = ticker ?? s.issuer.name;
    const ownership = s.ownership || "Direct";
    return {
      key: `${s.issuer.slug}|${s.security_title}|${ownership}`,
      label: `${who}: ${s.security_title || "Shares"}, ${ownership}`,
      short: `${who.length > 14 ? `${who.slice(0, 13)}…` : who}, ${ownership.length > 14 ? `${ownership.slice(0, 13)}…` : ownership}`,
      ownership,
      issuerName: s.issuer.name,
      ticker,
      securityTitle: s.security_title || "Shares",
      points: s.points
        .map((p) => ({ t: toUtc(p.day), day: p.day, shares: toNumber(p.shares) ?? NaN, filingUrl: p.filing_url }))
        .filter((p) => Number.isFinite(p.shares)),
    };
  });
}

/**
 * The lines picked at first: the ones with the most filings, newest first,
 * skipping any more than 20 times smaller than the first pick (on a shared
 * axis they would look flat at zero; the reader can still pick them).
 */
function defaultPicks(lines: Line[]): (string | null)[] {
  const peak = (l: Line) => Math.max(...l.points.map((p) => p.shares), 0);
  const last = (l: Line) => l.points[l.points.length - 1]?.t ?? 0;
  const ranked = [...lines].sort((a, b) => b.points.length - a.points.length || last(b) - last(a));
  const picks: string[] = [];
  const top = ranked[0] ? peak(ranked[0]) : 0;
  for (const l of ranked) {
    if (picks.length === MAX_LINES) break;
    if (picks.length === 0 || top === 0 || peak(l) * 20 >= top) picks.push(l.key);
  }
  return [0, 1, 2].map((i) => picks[i] ?? null);
}

/** A sentence per line for screen readers: first and latest figure, and how many filings. */
function summaryOf(lines: Line[]): string {
  if (!lines.length) return "No lines picked.";
  const parts = lines.map((l) => {
    const first = l.points[0];
    const latest = l.points[l.points.length - 1];
    if (!first || !latest) return `${l.label}: no figures.`;
    if (l.points.length === 1) return `${l.label}: ${shares(first.shares)} shares on ${day(first.day)}.`;
    return `${l.label}: ${l.points.length} figures from ${day(first.day)} to ${day(latest.day)}, going from ${shares(first.shares)} to ${shares(latest.shares)} shares.`;
  });
  return `Step chart of shares owned after each reported transaction. ${parts.join(" ")}`;
}

export function PositionsPanel({
  series,
  tickers,
  failed,
  coverage,
}: {
  series: PositionSeries[] | null;
  tickers: Record<string, string>;
  failed: boolean;
  /** Which filing dates the site holds (GET /api/meta/coverage); null if it didn't load. */
  coverage: DataCoverage | null;
}) {
  const lines = useMemo(() => linesFrom(series ?? [], tickers), [series, tickers]);
  // picks[slot] = the key of the line drawn in that color slot, or null when the slot is free.
  const [picks, setPicks] = useState<(string | null)[]>(() => defaultPicks(lines));
  const [asTable, setAsTable] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const phone = useIsPhone();

  const picked: PlotSeries[] = useMemo(
    () =>
      picks.flatMap((key, slot) => {
        const line = lines.find((l) => l.key === key);
        return line ? [{ key: line.key, label: line.label, short: line.short, slot, points: line.points }] : [];
      }),
    [picks, lines],
  );

  if (failed) {
    return (
      <p role="alert" className="px-4 py-6 md:px-6">
        <span aria-hidden className="text-beacon">! </span>
        Holdings didn&apos;t load. The data service didn&apos;t answer; try again in a minute.
      </p>
    );
  }
  if (!lines.length) {
    return (
      <EmptyState title="No holdings reported yet">
        Holdings come from the &ldquo;shares owned after the transaction&rdquo; figure on each Form 4. This filer
        hasn&apos;t reported one for a stock trade yet. Options and other derivatives aren&apos;t charted.
      </EmptyState>
    );
  }

  const toggle = (key: string) => {
    const slot = picks.indexOf(key);
    if (slot >= 0) {
      // Free this slot; the other lines keep their colors.
      setPicks(picks.map((k) => (k === key ? null : k)));
      return;
    }
    const free = picks.indexOf(null);
    if (free < 0) return;
    setPicks(picks.map((k, i) => (i === free ? key : k)));
  };

  const full = picks.every((k) => k !== null);
  const pickerLines = showAll ? lines : lines.slice(0, PICKER_ROWS);
  const peaks = picked.map((p) => Math.max(...p.points.map((x) => x.shares), 0)).filter((x) => x > 0);
  const scalesDiffer = peaks.length > 1 && Math.max(...peaks) > 20 * Math.min(...peaks);
  // The period the picked lines cover, from their first figure to their last.
  const pickedDays = picked.flatMap((p) => p.points.map((x) => x.day)).sort();

  return (
    <div className="px-4 py-4 md:px-6">
      <p className="max-w-[80ch] text-[13px] leading-[18px] text-dim">
        Shares owned after each reported transaction, as stated on each filing. One line per company, share class and
        way of holding: directly, or through a trust, a fund or a family member. Each line steps at a filing date and
        ends at the latest one. Options and other derivatives aren&apos;t charted.
      </p>

      <div className="mt-4 grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            {/* The legend: color swatch, marker shape and name for each line drawn. */}
            <ul aria-label="Lines on the chart" className="flex min-w-0 flex-wrap gap-x-4 gap-y-1 text-[13px]">
              {picked.map((p) => (
                <li key={p.key} className="flex min-w-0 items-center gap-1.5">
                  <span aria-hidden className={`inline-block h-0.5 w-3 ${SWATCH[p.slot]}`} />
                  <span aria-hidden className="text-dim">
                    {SHAPE[p.slot]}
                  </span>
                  <span className="truncate">{p.label}</span>
                </li>
              ))}
            </ul>
            <button
              type="button"
              // A toggle keeps one label; aria-pressed (and the fill) says whether it's on.
              aria-pressed={asTable}
              onClick={() => setAsTable(!asTable)}
              className={`inline-flex h-11 shrink-0 items-center rounded-[var(--radius-control)] border border-rule-strong px-3 text-[13px] md:h-8 ${
                asTable ? "bg-raised text-paper shadow-[inset_0_-2px_0_var(--color-paper)]" : "hover:bg-raised"
              }`}
            >
              Table view
            </button>
          </div>

          {picked.length === 0 ? (
            <p className="rounded-[var(--radius-block)] border border-hairline px-4 py-10 text-center text-dim">
              Pick up to three lines to draw.
            </p>
          ) : asTable ? (
            <PositionsTable lines={picked} />
          ) : (
            <div role="img" aria-label={summaryOf(lines.filter((l) => picks.includes(l.key)))} className="rounded-[var(--radius-block)] border border-hairline bg-console p-2">
              <PositionsChart series={picked} height={phone ? 260 : 320} />
            </div>
          )}
          <CoverageNote
            coverage={coverage}
            from={pickedDays[0]}
            to={pickedDays[pickedDays.length - 1]}
            what="these lines may be"
            className="mt-2"
          />
          {scalesDiffer && !asTable ? (
            <p className="mt-2 text-[12px] leading-4 text-dim">
              These holdings differ a lot in size, so the smaller ones sit close to zero on a shared scale. Pick one
              at a time to see it clearly, or use the table.
            </p>
          ) : null}
        </div>

        {lines.length > 1 ? (
          <fieldset className="min-w-0">
            <legend className="text-[12px] font-medium text-dim">
              Lines to draw ({picked.length} of {Math.min(MAX_LINES, lines.length)}
              {lines.length > MAX_LINES ? `, from ${lines.length}` : ""})
            </legend>
            <ul className="mt-1 divide-y divide-hairline border-y border-hairline">
              {pickerLines.map((l) => {
                const slot = picks.indexOf(l.key);
                const latest = l.points[l.points.length - 1];
                const disabled = slot < 0 && full;
                return (
                  <li key={l.key}>
                    <label className={`flex min-h-11 items-start gap-2 py-2 text-[13px] leading-[18px] ${disabled ? "text-dim" : "cursor-pointer"}`}>
                      <input
                        type="checkbox"
                        checked={slot >= 0}
                        disabled={disabled}
                        onChange={() => toggle(l.key)}
                        className="mt-0.5 size-4 shrink-0 accent-[var(--color-paper)]"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          {slot >= 0 ? (
                            <>
                              <span aria-hidden className={`inline-block h-0.5 w-3 shrink-0 ${SWATCH[slot]}`} />
                              <span aria-hidden className="text-dim">
                                {SHAPE[slot]}
                              </span>
                            </>
                          ) : null}
                          <span className="truncate">{l.label}</span>
                        </span>
                        <span className="num block text-dim">
                          {latest ? `${shares(latest.shares)} shares on ${day(latest.day)}` : "–"},{" "}
                          {l.points.length === 1 ? "1 filing" : `${l.points.length} filings`}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
            {lines.length > PICKER_ROWS ? (
              <button
                type="button"
                onClick={() => setShowAll(!showAll)}
                className="mt-1 inline-flex h-11 items-center text-[13px] text-dim underline underline-offset-2 hover:text-paper md:h-8"
              >
                {showAll ? "Show fewer" : `Show all ${lines.length}`}
              </button>
            ) : null}
            {full && lines.length > MAX_LINES ? (
              <p className="mt-1 text-[12px] leading-4 text-dim">Three lines at most. Untick one to pick another.</p>
            ) : null}
          </fieldset>
        ) : null}
      </div>

      <p className="mt-4 text-[12px] leading-4 text-dim">
        Each figure is the &ldquo;amount owned after the transaction&rdquo; from one filing. The Trades tab links every
        one of them to its filing on EDGAR.{" "}
        <Link href="/methodology" className="underline decoration-hairline underline-offset-2 hover:text-paper">
          How holdings are read
        </Link>
      </p>
    </div>
  );
}

/** The table view: every figure of every picked line, newest first, with the change from the one before. */
function PositionsTable({ lines }: { lines: PlotSeries[] }) {
  return (
    <div className="space-y-5">
      {lines.map((l) => {
        const rows = l.points.map((p, i) => ({ ...p, change: i > 0 ? p.shares - l.points[i - 1]!.shares : null })).reverse();
        return (
          <table key={l.key} className="num w-full border-collapse text-[13px] leading-[18px]">
            <caption className="mb-1 text-left font-medium">
              <span aria-hidden className="text-dim">
                {SHAPE[l.slot]}{" "}
              </span>
              {l.label}
            </caption>
            <thead>
              <tr className="bg-raised text-[12px] text-dim">
                <th scope="col" className="h-8 px-3 text-left font-medium">
                  Date
                </th>
                <th scope="col" className="h-8 px-3 text-right font-medium">
                  Shares owned after
                </th>
                <th scope="col" className="h-8 px-3 text-right font-medium">
                  Change
                </th>
                <th scope="col" className="h-8 px-3 text-right font-medium">
                  Source
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.day} className="border-b border-hairline">
                  <td className="h-9 px-3">{day(r.day)}</td>
                  <td className="h-9 px-3 text-right">{shares(r.shares)}</td>
                  <td className="h-9 px-3 text-right">
                    {r.change === null ? (
                      <span className="text-dim">first figure</span>
                    ) : r.change === 0 ? (
                      <span className="text-dim">no change</span>
                    ) : r.change > 0 ? (
                      <span className="text-inflow">
                        <span aria-hidden>▲ </span>+{shares(r.change)}
                        <span className="sr-only"> more</span>
                      </span>
                    ) : (
                      <span className="text-outflow">
                        <span aria-hidden>▼ </span>−{shares(-r.change)}
                        <span className="sr-only"> fewer</span>
                      </span>
                    )}
                  </td>
                  <td className="h-9 px-3 text-right">
                    {r.filingUrl ? (
                      <a
                        href={r.filingUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="text-dim underline decoration-hairline underline-offset-2 hover:text-paper"
                      >
                        Filing<span aria-hidden> ↗</span>
                        <span className="sr-only"> for {day(r.day)} on EDGAR (opens in a new tab)</span>
                      </a>
                    ) : (
                      <span className="text-dim">–</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        );
      })}
    </div>
  );
}
