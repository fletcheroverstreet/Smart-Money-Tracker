"use client";

/**
 * The positions chart itself: shares owned after each reported transaction,
 * drawn as step lines (a holding stays level until the next filing changes
 * it). ECharts, with only the modules this chart uses.
 *
 * Loaded in the browser only (PositionsPanel imports it with ssr: false), and
 * always paired with a table view there, so no value depends on hovering.
 */
import type { LineSeriesOption } from "echarts/charts";
import { LineChart } from "echarts/charts";
import type { GridComponentOption, TooltipComponentOption } from "echarts/components";
import { GridComponent, TooltipComponent } from "echarts/components";
import type { ComposeOption } from "echarts/core";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

import { day, shares as sharesText } from "@/lib/format";

import { sharesCompact } from "./text";

echarts.use([LineChart, GridComponent, TooltipComponent, SVGRenderer]);

type Option = ComposeOption<LineSeriesOption | GridComponentOption | TooltipComponentOption>;

/** One line on the chart. `slot` (0, 1 or 2) picks the color and marker shape. */
export interface PlotSeries {
  key: string;
  label: string;
  /** A few words for the end-of-line label. */
  short: string;
  slot: number;
  /** Sorted by date. `t` is the date as a UTC timestamp; `day` is YYYY-MM-DD. */
  /** filingUrl: the filing that reported this figure (every number links to its filing). */
  points: { t: number; day: string; shares: number; filingUrl?: string }[];
}

/** Marker shapes per slot, so lines differ by shape as well as color (circle, square, diamond). */
export const SLOT_SYMBOLS = ["circle", "rect", "diamond"] as const;

const DAY_MS = 86_400_000;
/**
 * The shortest stretch of time the date axis covers. Holdings have a date, not a time of
 * day, so a single figure (or a few on nearby dates) sits inside a month rather than on an
 * axis stretched across one day with hour ticks and the point lost at its edge.
 */
const MIN_SPAN_DAYS = 30;

/** The date axis range: the dates plotted, widened evenly to MIN_SPAN_DAYS when shorter, in whole days. */
function dateRange(series: PlotSeries[]): { min: number; max: number } | null {
  const times = series.flatMap((s) => s.points.map((p) => p.t));
  if (!times.length) return null;
  let min = Math.min(...times);
  let max = Math.max(...times);
  const short = MIN_SPAN_DAYS * DAY_MS - (max - min);
  if (short > 0) {
    min -= short / 2;
    max += short / 2;
  }
  return { min: Math.floor(min / DAY_MS) * DAY_MS, max: Math.ceil(max / DAY_MS) * DAY_MS };
}

/** Read a design token from the stylesheet (globals.css), so the chart uses the same colors as the page. */
function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** The holding on date `t`: the last reported figure on or before it (null before the first filing). */
function holdingAt(points: PlotSeries["points"], t: number): PlotSeries["points"][number] | null {
  let found: PlotSeries["points"][number] | null = null;
  for (const p of points) {
    if (p.t <= t) found = p;
    else break;
  }
  return found;
}

/** The hover readout, built with textContent (filing text is never parsed as HTML). */
function tooltipFor(series: PlotSeries[], t: number, colors: string[]): HTMLElement {
  const box = document.createElement("div");
  const head = document.createElement("div");
  head.textContent = day(new Date(t).toISOString());
  head.style.cssText = "font-weight:500;margin-bottom:4px";
  box.appendChild(head);
  for (const s of series) {
    const p = holdingAt(s.points, t);
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:8px;line-height:18px";
    const key = document.createElement("span");
    key.style.cssText = `display:inline-block;width:12px;height:2px;background:${colors[s.slot]}`;
    const value = document.createElement("strong");
    value.style.cssText = "font-weight:600;font-variant-numeric:tabular-nums";
    value.textContent = p ? `${sharesText(p.shares)} sh` : "–";
    const name = document.createElement("span");
    name.style.cssText = "opacity:.8";
    name.textContent = p && p.t !== t ? `${s.label} (as of ${day(p.day)})` : s.label;
    row.append(key, value, name);
    box.appendChild(row);
  }
  return box;
}

export default function PositionsChart({ series, height }: { series: PlotSeries[]; height: number }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = echarts.init(el, null, { renderer: "svg" });
    const colors = [token("--color-series-1"), token("--color-series-2"), token("--color-series-3")];
    const dim = token("--color-dim");
    const paper = token("--color-paper");
    const hairline = token("--color-hairline");
    const console_ = token("--color-console");
    const ruleStrong = token("--color-rule-strong");
    const font = getComputedStyle(document.body).fontFamily;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const narrow = el.clientWidth < 560;
    const range = dateRange(series);

    const option: Option = {
      animation: !reduceMotion,
      textStyle: { fontFamily: font, color: dim, fontSize: 12 },
      // Room on the right for the end-of-line labels (dropped on narrow screens; the legend names the lines).
      grid: { left: 8, right: narrow ? 16 : 140, top: 16, bottom: 8, containLabel: true },
      xAxis: {
        type: "time",
        // Whole days only: no hour ticks between two dates.
        minInterval: DAY_MS,
        ...(range ?? {}),
        axisLine: { lineStyle: { color: hairline } },
        axisTick: { show: false },
        axisLabel: { color: dim, hideOverlap: true },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: 0, // holdings start from zero, so line heights compare honestly
        axisLabel: { color: dim, formatter: (v: number) => sharesCompact(v) },
        splitLine: { lineStyle: { color: hairline, type: "solid" } },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line", snap: true, lineStyle: { color: ruleStrong } },
        backgroundColor: console_,
        borderColor: ruleStrong,
        borderWidth: 1,
        textStyle: { color: paper, fontFamily: font, fontSize: 12 },
        extraCssText: "border-radius:4px;box-shadow:var(--shadow-float);padding:8px 10px",
        formatter: (params) => {
          const first = Array.isArray(params) ? params[0] : params;
          // With trigger "axis", each item carries the hovered date as `axisValue` (a timestamp).
          const t = first && "axisValue" in first ? Number(first.axisValue) : NaN;
          if (!Number.isFinite(t)) return "";
          return tooltipFor(series, t, colors);
        },
      },
      series: series.map((s) => ({
        type: "line",
        name: s.label,
        // "end": the line stays at a filing's figure until the next filing, then steps.
        step: "end",
        data: s.points.map((p) => [p.t, p.shares]),
        // Always a marker on each figure, so a line with a single date still shows as a point.
        showSymbol: true,
        symbol: SLOT_SYMBOLS[s.slot],
        symbolSize: 8,
        lineStyle: { width: 2, color: colors[s.slot] },
        itemStyle: { color: colors[s.slot], borderColor: console_, borderWidth: 2 },
        emphasis: { scale: 1.4, focus: "none" },
        endLabel: narrow
          ? { show: false }
          : { show: true, formatter: s.short, color: dim, fontSize: 12, fontFamily: font, distance: 8 },
        labelLayout: { moveOverlap: "shiftY" },
      })),
    };
    chart.setOption(option);

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [series]);

  // Hidden from screen readers: the panel around it gives a text summary and a table.
  // data-unmanaged-dom: ECharts builds this div's contents, not React (see lib/pageReady.ts).
  return <div ref={ref} aria-hidden data-unmanaged-dom style={{ height, width: "100%" }} />;
}
