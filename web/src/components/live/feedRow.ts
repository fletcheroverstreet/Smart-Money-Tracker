/** Turns a feed event into the fields every Live view shows, in one place. */
import type { FeedItem, FeedPayload } from "@/lib/api/types";
import { tradeAmount } from "@/lib/dataIssues";

export interface FeedRowView {
  id: number;
  eventType: string;
  form: string;
  detectedAt: string;
  direction: string;
  actionLabel: string;
  code: string | null;
  codeLabel: string | undefined;
  who: string;
  whoSlug: string;
  role: string;
  ticker: string | null;
  issuer: string | null;
  sizeText: string;
  sizeApprox: boolean;
  delayDays: number | null;
  tradeId: number | null;
  filingUrl: string;
  isPlan: boolean;
  jointOwners: number;
  /** Codes for figures that can't be right as filed (see components/ui/TradeValue.tsx). */
  dataIssues: string[];
  /** Filed after the legal deadline (trades only; false when it can't be judged). */
  isLate: boolean;
  /** The deadline rule behind isLate, for the flag's explanation. */
  deadlineRule: string | null;
}

export function viewOf(item: FeedItem): FeedRowView {
  const p = item.payload as unknown as FeedPayload;
  const base = {
    id: item.id,
    eventType: item.event_type,
    form: item.form_type,
    detectedAt: item.detected_at,
    who: p.owner?.name ?? "Unknown filer",
    whoSlug: p.owner?.slug ?? "",
    role: p.owner?.role ?? "",
    ticker: p.ticker ?? null,
    issuer: p.issuer?.name ?? null,
    filingUrl: p.url,
    tradeId: item.transaction_id ?? null,
    jointOwners: p.joint_owners ?? 1,
    dataIssues: p.data_issues ?? [],
    isLate: p.is_late === true,
    deadlineRule: p.deadline_rule ?? null,
  };
  if (item.event_type === "planned_sale") {
    return {
      ...base, direction: "planned_sale", actionLabel: "Plans to sell", code: null, codeLabel: undefined,
      sizeText: tradeAmount(p.market_value), sizeApprox: true, delayDays: null, isPlan: true,
    };
  }
  if (item.event_type === "insider_holding") {
    return {
      ...base, direction: "holding", actionLabel: "New insider", code: null, codeLabel: undefined,
      sizeText: "–", sizeApprox: false, delayDays: null, isPlan: false,
    };
  }
  return {
    ...base,
    direction: p.direction ?? "other",
    actionLabel: p.exercise_and_sell && p.code === "S" ? "Exercise and sell" : p.code_short ?? p.code ?? "Other",
    code: p.code ?? null,
    codeLabel: p.code_label,
    // "–" for a missing or zero value: a price of 0 is no price (CLMT tx 332672 read "−$0").
    sizeText: tradeAmount(p.value),
    sizeApprox: false,
    delayDays: p.delay_days ?? null,
    isPlan: false,
  };
}

/** Client-side version of the feed filters, applied to live arrivals so they match the table. */
export function matchesFilters(item: FeedItem, codes: string[] | null, types: string[] | null, minSize: number | null): boolean {
  if (types && !types.includes(item.event_type)) return false;
  const p = item.payload as unknown as FeedPayload;
  if (codes && item.event_type === "insider_trade" && !codes.includes(p.code ?? "")) return false;
  if (minSize !== null && (item.size_usd ?? 0) < minSize) return false;
  return true;
}
