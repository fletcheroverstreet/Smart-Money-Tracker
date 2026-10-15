/**
 * API query parameters shared by the Status page's server render and its
 * client refreshes.
 *
 * This module deliberately has no "use client" line: a server component that
 * imports a plain value from a client module gets a placeholder, not the value.
 */

/**
 * "When did we last see something new?" is answered by the newest filing of
 * each kind that reached the Live feed. codes=all, so grants and exercises count.
 */
export const NEWEST_QUERIES = {
  insider: { types: "insider_trade,insider_holding", codes: "all", limit: 1 },
  plan: { types: "planned_sale", codes: "all", limit: 1 },
} as const;

export type NewestKind = keyof typeof NEWEST_QUERIES;
