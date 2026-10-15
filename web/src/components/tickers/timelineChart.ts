/**
 * The ticker event timeline, as plain data. No React here: these functions
 * turn API events into chart markers and an ECharts option object.
 *
 * Layout (design sheet §5.4): one lane per layer on a shared date axis.
 *
 *   Insider buys    ▲ filled triangles (inflow-mark)
 *   Insider sells   ▼ filled, upside-down triangles (outflow-mark)
 *   Planned sales   ○ hollow circles (Form 144 notices; not completed trades)
 *
 * Shape and lane tell the layers apart, so color is never the only cue.
 * Marker AREA is proportional to the dollar value (size grows with the square
 * root), so a $10M sale looks 10x a $1M sale, not 100x.
 *
 * Several trades by the same person, same action, same day (one Form 4 often
 * lists a sale in many price lots) become ONE marker with their total. Different
 * people on the same day stack vertically inside the lane so they never overlap.
 */
// Types only (erased at build time): the runtime pieces are registered in ./echarts.ts.
import type {
  ComposeOption,
  GridComponentOption,
  LineSeriesOption,
  MarkAreaComponentOption,
  ScatterSeriesOption,
  TooltipComponentFormatterCallbackParams,
  TooltipComponentOption,
  XAXisComponentOption,
  YAXisComponentOption,
} from "echarts";

import type { TimelineEvent } from "@/lib/api/types";
import { plannedAmount } from "@/lib/dataIssues";
import { day, moneyCompact, price, toNumber } from "@/lib/format";

export type TimelineOption = ComposeOption<
  | LineSeriesOption
  | ScatterSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | XAXisComponentOption
  | YAXisComponentOption
  | MarkAreaComponentOption
>;

/** A stretch of the x axis whose filings aren't loaded yet (ms since 1970, start and end of the stretch). */
export interface UnloadedSpan {
  from: number;
  to: number;
  /** Some of the stretch's filings are held, not all. */
  partial?: boolean;
}

export type Lane = 0 | 1 | 2;

export const LANES: { lane: Lane; name: string; short: string; glyph: string }[] = [
  { lane: 0, name: "Insider buys", short: "Buys", glyph: "▲" },
  { lane: 1, name: "Insider sells", short: "Sells", glyph: "▼" },
  { lane: 2, name: "Planned sales", short: "144", glyph: "○" },
];

/** One marker on the chart: one or more events combined. */
export interface Marker {
  lane: Lane;
  day: string;
  /** Milliseconds since 1970 for the day at 00:00 UTC (the chart's x value). */
  time: number;
  /** The API's label, e.g. "Rosa Ortiz: Buy". */
  label: string;
  /** How many events were combined into this marker. */
  count: number;
  /** Sum of the reported values; null when none of them reported a value. */
  value: number | null;
  /** The largest trade in the group: clicking the marker opens its quick look. */
  transactionId: number | null;
  filingUrl: string;
  /** Lane center plus a small stacking offset. */
  y: number;
  /** Marker size in pixels. */
  size: number;
}

export interface PricePoint {
  time: number;
  close: number;
}

export interface ChartColors {
  buy: string;
  sell: string;
  planned: string;
  text: string;
  dim: string;
  hairline: string;
  rule: string;
  panel: string;
  price: string;
}

/** "2026-09-17" -> milliseconds at 00:00 UTC that day. Dates are calendar dates, never shifted. */
export function dayToTime(value: string): number {
  const [y, m, d] = value.slice(0, 10).split("-").map(Number);
  return Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

export function laneOf(event: TimelineEvent): Lane | null {
  if (event.layer === "planned_sales") return 2;
  if (event.direction === "buy") return 0;
  if (event.direction === "sell") return 1;
  return null; // other codes aren't drawn (the API sends only buys and sells by default)
}

const MIN_SIZE = 9;
const MAX_SIZE = 30;
/** Vertical room one lane gives its stack, in lane units (a lane is 1.0 tall). */
const STACK_SPAN = 0.72;

export function buildMarkers(events: TimelineEvent[]): Marker[] {
  // 1. Combine same lane + day + label (same person, same action, same day).
  const groups = new Map<string, { lane: Lane; day: string; label: string; events: TimelineEvent[] }>();
  for (const event of events) {
    const lane = laneOf(event);
    if (lane === null) continue;
    const key = `${lane}|${event.day}|${event.label}`;
    const group = groups.get(key) ?? { lane, day: event.day, label: event.label, events: [] };
    group.events.push(event);
    groups.set(key, group);
  }

  const markers: Marker[] = [];
  for (const g of groups.values()) {
    const values = g.events.map((e) => e.value).filter((v): v is number => v !== null && v !== undefined);
    const largest = [...g.events].sort((a, b) => (b.value ?? -1) - (a.value ?? -1))[0]!;
    markers.push({
      lane: g.lane,
      day: g.day,
      time: dayToTime(g.day),
      label: g.label,
      count: g.events.length,
      value: values.length ? values.reduce((sum, v) => sum + v, 0) : null,
      transactionId: largest.transaction_id ?? null,
      filingUrl: largest.filing_url,
      y: g.lane + 0.5,
      size: MIN_SIZE,
    });
  }

  // 2. Size by value: area proportional to dollars, relative to the largest marker shown.
  const maxValue = Math.max(0, ...markers.map((m) => m.value ?? 0));
  for (const m of markers) {
    if (m.value !== null && m.value > 0 && maxValue > 0) {
      m.size = MIN_SIZE + (MAX_SIZE - MIN_SIZE) * Math.sqrt(m.value / maxValue);
    }
  }

  // 3. Stack markers that share a lane and a day, largest in the middle of the stack.
  const byLaneDay = new Map<string, Marker[]>();
  for (const m of markers) {
    const key = `${m.lane}|${m.day}`;
    byLaneDay.set(key, [...(byLaneDay.get(key) ?? []), m]);
  }
  for (const stack of byLaneDay.values()) {
    if (stack.length < 2) continue;
    stack.sort((a, b) => (b.value ?? -1) - (a.value ?? -1));
    const step = Math.min(0.26, STACK_SPAN / (stack.length - 1));
    // Order 0, +1, -1, +2, -2 ... so the biggest sits in the center.
    stack.forEach((m, i) => {
      const slot = i === 0 ? 0 : Math.ceil(i / 2) * (i % 2 ? 1 : -1);
      m.y = m.lane + 0.5 + slot * step;
    });
  }

  return markers.sort((a, b) => a.time - b.time);
}

/** Plain-language summary for screen readers (the chart's aria-label). */
export function summarize(events: TimelineEvent[], ticker: string, rangeText: string): string {
  let buys = 0;
  let sells = 0;
  let planned = 0;
  for (const e of events) {
    const lane = laneOf(e);
    if (lane === 0) buys += 1;
    else if (lane === 1) sells += 1;
    else if (lane === 2) planned += 1;
  }
  if (buys + sells + planned === 0) return `Event timeline for ${ticker} over ${rangeText}: no insider trades or planned sales.`;
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  return (
    `Event timeline for ${ticker} over ${rangeText}: ` +
    `${plural(buys, "open-market insider buy", "open-market insider buys")}, ` +
    `${plural(sells, "open-market insider sale", "open-market insider sales")} and ` +
    `${plural(planned, "planned sale notice (Form 144)", "planned sale notices (Form 144)")}. ` +
    `The same events are listed in the table view.`
  );
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** Tooltip text for one marker: who, what, when, how much (all HTML-escaped). */
export function tooltipHtml(m: Marker): string {
  const glyph = LANES[m.lane]!.glyph;
  const lines = [`<strong>${glyph} ${escapeHtml(m.label)}</strong>`];
  if (m.lane === 2) {
    lines.push(`Planned for about ${day(m.day)}`);
    // A market value of 0 is no estimate, never "~$0" (plannedAmount; 0001959173-26-007025).
    const estimate = plannedAmount(m.value);
    lines.push(estimate === "–" ? "Value not given" : `Estimated value ${estimate}`);
    lines.push("A notice of intent, not a completed sale");
  } else {
    lines.push(`Traded ${day(m.day)}`);
    // A total of 0 is no value (trades filed at a price of 0), never "+$0".
    lines.push(!m.value ? "Value not reported" : `${m.lane === 0 ? "+" : "−"}${moneyCompact(m.value)}`);
    if (m.count > 1) lines.push(`${m.count} trades that day, combined`);
    if (m.transactionId !== null) lines.push(m.count > 1 ? "Click to open the largest one" : "Click to open the details");
  }
  return lines.join("<br/>");
}

export function buildOption({
  markers,
  prices,
  start,
  end,
  colors,
  font,
  animate,
  phone,
  tooltipFor,
  unloaded = [],
}: {
  markers: Marker[];
  prices: PricePoint[] | null;
  start: number;
  end: number;
  colors: ChartColors;
  font: string;
  animate: boolean;
  phone: boolean;
  /** Tooltip HTML for the point at (series id, index in that series); "" shows nothing. */
  tooltipFor: (seriesId: string, dataIndex: number) => string;
  /**
   * Stretches whose filings aren't loaded (coverage gaps, and the time before the
   * first loaded filing). They're shaded and labelled, so an empty stretch isn't
   * read as "no insider activity".
   */
  unloaded?: UnloadedSpan[];
}): TimelineOption {
  const hasPrices = Boolean(prices && prices.length > 1);
  const left = phone ? 56 : 128;
  const right = phone ? 12 : 24;
  const lanesGrid = hasPrices ? 1 : 0;
  const axisText = { color: colors.dim, fontFamily: font, fontSize: 12 };

  const grids: GridComponentOption[] = hasPrices
    ? [
        { left, right, top: 12, height: "36%" },
        { left, right, top: "50%", bottom: 28 },
      ]
    : [{ left, right, top: 8, bottom: 28 }];

  const timeAxis = (gridIndex: number, showLabels: boolean): XAXisComponentOption => ({
    type: "time",
    gridIndex,
    min: start,
    max: end,
    axisLine: { lineStyle: { color: colors.rule } },
    axisTick: { show: showLabels, lineStyle: { color: colors.rule } },
    axisLabel: { show: showLabels, hideOverlap: true, ...axisText },
    splitLine: { show: true, lineStyle: { color: colors.hairline } },
  });

  const xAxis: XAXisComponentOption[] = hasPrices ? [timeAxis(0, false), timeAxis(1, true)] : [timeAxis(0, true)];

  const yAxis: YAXisComponentOption[] = [];
  if (hasPrices) {
    yAxis.push({
      type: "value",
      gridIndex: 0,
      scale: true,
      axisLabel: { ...axisText, formatter: (v: number) => price(v).replace(/\.00$/, "") },
      splitLine: { lineStyle: { color: colors.hairline } },
    });
  }
  // Lanes: a value axis from 0 to 3 whose grid lines (at 0, 1, 2, 3) are the lane borders...
  yAxis.push({
    type: "value",
    gridIndex: lanesGrid,
    min: 0,
    max: 3,
    interval: 1,
    inverse: true,
    axisLabel: { show: false },
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { show: true, lineStyle: { color: colors.hairline } },
  });
  // ...and a label-only category axis on top of it that names the three lanes at their centers.
  yAxis.push({
    type: "category",
    gridIndex: lanesGrid,
    inverse: true,
    data: LANES.map((l) => `${l.glyph} ${phone ? l.short : l.name}`),
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { ...axisText, color: colors.text, fontSize: phone ? 12 : 13 },
  });
  const laneAxisIndex = hasPrices ? 1 : 0;

  const laneSeries = (lane: Lane): ScatterSeriesOption => {
    const color = lane === 0 ? colors.buy : lane === 1 ? colors.sell : colors.planned;
    const hollow = lane === 2;
    return {
      id: `lane-${lane}`,
      name: LANES[lane]!.name,
      type: "scatter",
      xAxisIndex: lanesGrid,
      yAxisIndex: laneAxisIndex,
      symbol: hollow ? "circle" : "triangle",
      symbolRotate: lane === 1 ? 180 : 0,
      cursor: hollow ? "default" : "pointer",
      itemStyle: hollow
        ? { color: "rgba(0,0,0,0)", borderColor: color, borderWidth: 1.5 }
        : { color, opacity: 0.9, borderColor: colors.panel, borderWidth: 1 },
      emphasis: { scale: false, itemStyle: { borderColor: colors.text, borderWidth: 2 } },
      data: markers.filter((m) => m.lane === lane).map((m) => ({ value: [m.time, m.y], symbolSize: m.size })),
    };
  };

  // Shaded "not loaded yet" stretches: an empty, silent series per grid that only
  // carries the mark areas, drawn under the markers (z 0). The label is written only
  // where the stretch is wide enough to hold it.
  const spans = unloaded
    .map((u) => ({ from: Math.max(u.from, start), to: Math.min(u.to, end), partial: u.partial }))
    .filter((u) => u.to > u.from);
  const minLabelled = (end - start) * (phone ? 0.16 : 0.08);
  const unloadedSeries = (gridIndex: number, yAxisIndex: number, labelled: boolean): ScatterSeriesOption => ({
    id: `unloaded-${gridIndex}`,
    type: "scatter",
    xAxisIndex: gridIndex,
    yAxisIndex,
    silent: true,
    z: 0,
    data: [],
    markArea: {
      silent: true,
      itemStyle: { color: colors.hairline, opacity: 0.55 },
      label: { show: labelled, position: "insideTop", color: colors.dim, fontFamily: font, fontSize: 11 },
      data: spans.map((u) => [
        {
          xAxis: u.from,
          name: labelled && u.to - u.from >= minLabelled ? (u.partial ? "partly loaded" : "not loaded yet") : "",
        },
        { xAxis: u.to },
      ]),
    },
  });

  const series: (ScatterSeriesOption | LineSeriesOption)[] = [laneSeries(0), laneSeries(1), laneSeries(2)];
  if (spans.length) {
    // The label goes in the top grid only (the price grid when there is one).
    series.unshift(unloadedSeries(lanesGrid, laneAxisIndex, !hasPrices));
    if (hasPrices) series.unshift(unloadedSeries(0, 0, true));
  }
  if (hasPrices && prices) {
    series.push({
      id: "price",
      name: "Closing price",
      type: "line",
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      silent: true,
      lineStyle: { color: colors.price, width: 1.5 },
      data: prices.map((p) => [p.time, p.close]),
    });
  }

  return {
    animation: animate,
    textStyle: { fontFamily: font },
    grid: grids,
    xAxis,
    yAxis,
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: colors.panel,
      borderColor: colors.rule,
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: colors.text, fontFamily: font, fontSize: 12, lineHeight: 16 },
      extraCssText: "box-shadow: var(--shadow-float); border-radius: 4px;",
      // trigger "item" hands one point; the array form never happens here, but is handled.
      formatter: (params: TooltipComponentFormatterCallbackParams) => {
        const item = Array.isArray(params) ? params[0] : params;
        return item ? tooltipFor(String(item.seriesId ?? ""), item.dataIndex) : "";
      },
    },
    series,
  };
}

/** Price bars from the private endpoint -> chart points (bars without a close are skipped, never zero). */
export function pricePoints(bars: { day: string; close: string | number | null }[], start: number, end: number): PricePoint[] {
  const points: PricePoint[] = [];
  for (const bar of bars) {
    const close = toNumber(bar.close);
    const time = dayToTime(bar.day);
    if (close !== null && time >= start && time <= end) points.push({ time, close });
  }
  return points.sort((a, b) => a.time - b.time);
}
