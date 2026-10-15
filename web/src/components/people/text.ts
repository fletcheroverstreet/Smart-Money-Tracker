/**
 * Small wording and number helpers used only by the People pages. Copy is
 * plain and sentence case, for a reader who isn't a finance expert.
 */
import type { RoleOut } from "@/lib/api/types";
import { toNumber } from "@/lib/format";

/** What kind of filer this is, in words. */
export function kindText(kind: string): string {
  switch (kind) {
    case "person":
      return "Person";
    case "fund":
      return "Fund or investment manager";
    case "bank":
      return "Bank";
    case "company":
      return "Company";
    case "index_manager":
      return "Index fund manager";
    case "politician":
      return "Member of Congress";
    default:
      return "Filer";
  }
}

/** One word for dense tables: "Fund", "Company", "Bank". People get no tag. */
export function kindShort(kind: string): string | null {
  switch (kind) {
    case "person":
      return null;
    case "fund":
    case "index_manager":
      return "Fund";
    case "bank":
      return "Bank";
    case "company":
      return "Company";
    default:
      return kindText(kind);
  }
}

/** "1.2M", "48k", "950": share counts for chart axes, where full digits don't fit. */
export function sharesCompact(value: string | number | null | undefined): string {
  const n = toNumber(value);
  if (n === null) return "–";
  const abs = Math.abs(n);
  const sign = n < 0 ? "−" : "";
  const fixed = (x: number) => (x >= 100 ? Math.round(x).toString() : x.toFixed(1).replace(/\.0$/, ""));
  if (abs >= 1e9) return `${sign}${fixed(abs / 1e9)}bn`;
  if (abs >= 1e6) return `${sign}${fixed(abs / 1e6)}M`;
  if (abs >= 1e3) return `${sign}${fixed(abs / 1e3)}k`;
  return `${sign}${Math.round(abs)}`;
}

/** "1 day", "1.5 days", "–" for a median delay that may be fractional. */
export function daysText(days: number | null | undefined): string {
  if (days === null || days === undefined) return "–";
  const rounded = Math.round(days * 10) / 10;
  return `${rounded} ${rounded === 1 ? "day" : "days"}`;
}

/** "1 filing", "3 filings". */
export function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/**
 * A person's roles grouped by company, newest first: a CEO who is also a
 * director and a 10% owner of the same company is one line, not three.
 */
export interface RoleGroup {
  key: string;
  organization: RoleOut["organization"];
  titles: string[];
  /** Earliest start date among the roles (from the first Form 3), when known. */
  since: string | null;
  /** Latest date a filing showed any of these roles. */
  lastSeen: string | null;
}

export function groupRoles(roles: RoleOut[]): RoleGroup[] {
  const groups = new Map<string, RoleGroup>();
  for (const role of roles) {
    const key = role.organization?.slug ?? "(none)";
    let group = groups.get(key);
    if (!group) {
      group = { key, organization: role.organization, titles: [], since: null, lastSeen: null };
      groups.set(key, group);
    }
    if (!group.titles.includes(role.title)) group.titles.push(role.title);
    // ISO dates (YYYY-MM-DD) compare correctly as strings.
    if (role.start_date && (!group.since || role.start_date < group.since)) group.since = role.start_date;
    if (role.last_seen_on && (!group.lastSeen || role.last_seen_on > group.lastSeen)) group.lastSeen = role.last_seen_on;
  }
  return [...groups.values()];
}

/** The SEC's list of every filing by this CIK. */
export function edgarFilerUrl(cik: string): string {
  return `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(cik)}&owner=include&type=&dateb=&count=40`;
}
