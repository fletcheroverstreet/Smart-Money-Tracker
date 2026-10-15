/**
 * Live-page filters, shared by the server page (first render) and the client
 * view (URL updates). Plain functions: safe to import on either side.
 */

export interface LiveFilters {
  /** "ps": open-market buys and sells only (the default). "all": every insider code. */
  codes: "ps" | "all";
  type: "all" | "insider_trade" | "planned_sale" | "insider_holding";
  min: number | null;
}

export function filtersFromParams(params: { get(name: string): string | null }): LiveFilters {
  const codes = params.get("codes") === "all" ? "all" : "ps";
  const rawType = params.get("type");
  const type =
    rawType === "insider_trade" || rawType === "planned_sale" || rawType === "insider_holding" ? rawType : "all";
  const rawMin = Number(params.get("min"));
  const min = Number.isFinite(rawMin) && rawMin > 0 ? rawMin : null;
  return { codes, type, min };
}

export function feedParams(filters: LiveFilters, cursor?: string | null) {
  return {
    limit: 100,
    codes: filters.codes === "all" ? "all" : "P,S",
    types: filters.type === "all" ? undefined : filters.type,
    min_size: filters.min ?? undefined,
    cursor: cursor ?? undefined,
  };
}

export function filtersToQuery(filters: LiveFilters): string {
  const q = new URLSearchParams();
  if (filters.codes === "all") q.set("codes", "all");
  if (filters.type !== "all") q.set("type", filters.type);
  if (filters.min) q.set("min", String(filters.min));
  return q.toString();
}
