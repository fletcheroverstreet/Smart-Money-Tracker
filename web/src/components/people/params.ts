/**
 * URL parameters for the People pages, shared by the server pages (first
 * render) and the client views (URL updates). Plain functions, safe on either
 * side. Everything that changes what a page shows lives in the URL, so every
 * view is a shareable link.
 *
 *   /people?q=smith&role=director&sort=buys     the insider directory
 *   /people?tab=notables                        the Notables tab
 *   /people/jane-doe-123?tab=positions          a profile's positions chart
 *   /people/jane-doe-123?codes=all              a profile's trades, every code
 */

type ParamReader = { get(name: string): string | null };

/** Build a reader from a server page's `searchParams` object. */
export function readerOf(raw: Record<string, string | string[] | undefined>): ParamReader {
  return { get: (name) => (typeof raw[name] === "string" ? (raw[name] as string) : null) };
}

// ---------------------------------------------------------------- directory

/** The four orders the API supports. Each has one fixed direction. */
export type DirectorySort = "recent" | "buys" | "sells" | "name";
export type DirectoryRole = "officer" | "director" | "ten_pct_owner" | "other_insider";
export type PeopleTab = "insiders" | "notables";

export interface DirectoryFilters {
  tab: PeopleTab;
  /** Name search; empty string when not searching. */
  q: string;
  role: DirectoryRole | null;
  sort: DirectorySort;
}

const SORTS: DirectorySort[] = ["recent", "buys", "sells", "name"];
const ROLES: DirectoryRole[] = ["officer", "director", "ten_pct_owner", "other_insider"];

export const ROLE_OPTIONS: { value: DirectoryRole | null; label: string }[] = [
  { value: null, label: "All roles" },
  { value: "officer", label: "Officers" },
  { value: "director", label: "Directors" },
  { value: "ten_pct_owner", label: "10% owners" },
  { value: "other_insider", label: "Other insiders" },
];

/** The API caps searches at 80 characters. */
export const MAX_QUERY = 80;

export function directoryFromParams(params: ParamReader): DirectoryFilters {
  const rawSort = params.get("sort");
  const rawRole = params.get("role");
  return {
    tab: params.get("tab") === "notables" ? "notables" : "insiders",
    q: (params.get("q") ?? "").trim().slice(0, MAX_QUERY),
    role: ROLES.includes(rawRole as DirectoryRole) ? (rawRole as DirectoryRole) : null,
    sort: SORTS.includes(rawSort as DirectorySort) ? (rawSort as DirectorySort) : "recent",
  };
}

/** Query parameters for GET /api/people/insiders. */
export function directoryApiParams(filters: DirectoryFilters, cursor?: string | null) {
  return {
    q: filters.q || undefined,
    role: filters.role ?? undefined,
    sort: filters.sort,
    limit: 50,
    cursor: cursor ?? undefined,
  };
}

/** The page URL for a set of filters. Defaults are left out, so links stay short. */
export function directoryHref(filters: DirectoryFilters): string {
  const q = new URLSearchParams();
  if (filters.tab === "notables") {
    q.set("tab", "notables");
    return `/people?${q.toString()}`;
  }
  if (filters.q) q.set("q", filters.q);
  if (filters.role) q.set("role", filters.role);
  if (filters.sort !== "recent") q.set("sort", filters.sort);
  const qs = q.toString();
  return qs ? `/people?${qs}` : "/people";
}

export function sameDirectory(a: DirectoryFilters, b: DirectoryFilters): boolean {
  return a.q === b.q && a.role === b.role && a.sort === b.sort;
}

// ---------------------------------------------------------------- profile

export type ProfileTab = "trades" | "positions";
/** "ps": open-market buys (P) and sells (S) only, the default. "all": every transaction code. */
export type CodesFilter = "ps" | "all";

export interface ProfileView {
  tab: ProfileTab;
  codes: CodesFilter;
}

export function profileFromParams(params: ParamReader): ProfileView {
  return {
    tab: params.get("tab") === "positions" ? "positions" : "trades",
    codes: params.get("codes") === "all" ? "all" : "ps",
  };
}

/** Query parameters for GET /api/people/{slug}/trades. */
export function tradesApiParams(codes: CodesFilter, cursor?: string | null) {
  return { codes: codes === "all" ? "all" : "P,S", limit: 100, cursor: cursor ?? undefined };
}

/**
 * The profile URL for a view. `keep` carries parameters owned by other parts of
 * the page (such as ?trade=, which keeps the quick-look drawer open).
 */
export function profileHref(slug: string, view: ProfileView, keep?: URLSearchParams): string {
  const q = new URLSearchParams(keep?.toString());
  q.delete("tab");
  q.delete("codes");
  if (view.tab !== "trades") q.set("tab", view.tab);
  if (view.codes !== "ps") q.set("codes", view.codes);
  const qs = q.toString();
  return qs ? `/people/${slug}?${qs}` : `/people/${slug}`;
}
