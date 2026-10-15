/**
 * The top of a person's page (design sheet §5.2, insider variant): name,
 * what kind of filer they are, their roles by company, the Watch button, a
 * one-sentence summary built only from their filings, and the key numbers.
 *
 * A server component: it renders once with the page and has no interactivity
 * of its own (the Watch button is a small client component inside it).
 */
import Link from "next/link";

import { CoverageNote } from "@/components/ui/CoverageNote";
import { WatchButton } from "@/components/ui/WatchButton";
import type { DataCoverage, PersonProfile } from "@/lib/api/types";
import { day, moneyCompact, pct } from "@/lib/format";

import { daysText, edgarFilerUrl, groupRoles, kindText, plural } from "./text";
import { whoseText } from "./whose";

/** Roles shown before the "show all" toggle. Funds can be 10% owners of dozens of companies. */
const ROLES_SHOWN = 4;

function Initials({ name }: { name: string }) {
  // No photos for insiders (none are published with the filings), so a plain monogram.
  const letters = name
    .replace(/[^A-Za-z ]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
  return (
    <div
      aria-hidden
      className="flex size-14 shrink-0 items-center justify-center rounded-[var(--radius-id)] border border-hairline bg-raised font-display text-[22px] font-medium text-dim md:size-[72px] md:text-[26px]"
    >
      {letters || "?"}
    </div>
  );
}

function RoleLine({
  group,
  tickers,
}: {
  group: ReturnType<typeof groupRoles>[number];
  tickers: Record<string, string>;
}) {
  const org = group.organization;
  const ticker = org ? tickers[org.slug] : undefined;
  return (
    <li className="leading-5">
      <span className="text-paper">{group.titles.join(", ")}</span>
      {org ? (
        <>
          {" at "}
          {ticker ? (
            <Link href={`/tickers/${ticker}`} className="underline decoration-hairline underline-offset-2 hover:decoration-paper">
              {org.name} ({ticker})
            </Link>
          ) : (
            <span>{org.name}</span>
          )}
        </>
      ) : null}
      <span className="num text-dim">
        {group.since ? `, insider since ${day(group.since)} (first Form 3)` : ""}
        {/* lastSeen is the period of report of the latest filing naming this role (the date its
            events happened), not the day it was filed: "reported for", not "filed on". */}
        {group.lastSeen ? `, last reported for ${day(group.lastSeen)}` : ""}
      </span>
    </li>
  );
}

function Stat({ label, value, note }: { label: string; value: React.ReactNode; note: React.ReactNode }) {
  return (
    <div className="min-w-0 border-hairline px-4 py-3 md:px-5 xl:border-l xl:first:border-l-0">
      <dt className="text-[12px] font-medium leading-4 text-dim">{label}</dt>
      <dd className="num mt-1 font-display text-[26px] font-medium leading-[30px]">{value}</dd>
      <dd className="num text-[12px] leading-4 text-dim">{note}</dd>
    </div>
  );
}

/**
 * "$1.2M reported", "no dollar values reported", or "none on record". A missing value is never $0.
 * `leftOut` rows have a value that can't be right as filed or isn't in dollars: they are in the
 * count but not the sum, and their trades are listed below with a note.
 */
function valueNote(count: number, value: string | null, leftOut: number): string {
  if (count === 0) return "none on record";
  if (value === null) return leftOut > 0 ? "not in dollar totals (see trades)" : "no dollar values reported";
  return leftOut > 0 ? `${moneyCompact(value)} reported, ${leftOut} left out` : `${moneyCompact(value)} reported`;
}

export function ProfileHeader({
  profile,
  tickers,
  coverage,
}: {
  profile: PersonProfile;
  tickers: Record<string, string>;
  /** Which filing dates the site holds, to flag missing days inside this person's record. */
  coverage: DataCoverage | null;
}) {
  const { entity, stats } = profile;
  const cik = profile.identifiers.sec_cik;
  const groups = groupRoles(profile.roles);
  const shown = groups.slice(0, ROLES_SHOWN);
  const hidden = groups.slice(ROLES_SHOWN);
  const isTenPct = profile.tags.includes("ten_pct_owner");

  return (
    <header className="border-b border-hairline">
      <div className="px-4 pt-4 md:px-6">
        <nav aria-label="Breadcrumb" className="text-[13px] text-dim">
          <ol className="flex gap-1.5">
            <li>
              <Link href="/people" className="hover:text-paper hover:underline">
                People
              </Link>
            </li>
            <li aria-hidden>/</li>
            <li>
              <Link href="/people" className="hover:text-paper hover:underline">
                Insiders
              </Link>
            </li>
          </ol>
        </nav>

        <div className="mt-3 flex flex-wrap items-start gap-4">
          <Initials name={entity.name} />
          <div className="min-w-0 flex-1">
            <h1 className="font-display text-[32px] font-semibold leading-9 [overflow-wrap:anywhere]">{entity.name}</h1>
            <p className="mt-1 flex flex-wrap gap-x-2 text-dim">
              <span>
                {kindText(entity.kind)}
                {isTenPct ? ", owns more than 10% of a company" : ""}
                {cik ? "." : ""}
              </span>
              {cik ? (
                <span>
                  SEC CIK <span className="rounded-[var(--radius-id)] bg-raised px-1 font-mono text-[12px] text-paper">{cik}</span>{" "}
                  <a
                    href={edgarFilerUrl(cik)}
                    target="_blank"
                    rel="noreferrer"
                    className="underline decoration-hairline underline-offset-2 hover:text-paper hover:decoration-paper"
                  >
                    Filings on EDGAR<span aria-hidden> ↗</span>
                    <span className="sr-only"> (opens the SEC site)</span>
                  </a>
                </span>
              ) : null}
            </p>
          </div>
          <div className="shrink-0">
            <WatchButton item={{ kind: "person", id: entity.slug, label: entity.name }} />
          </div>
        </div>

        {groups.length ? (
          <section aria-label="Roles" className="mt-3">
            <ul className="space-y-1">
              {shown.map((g) => (
                <RoleLine key={g.key} group={g} tickers={tickers} />
              ))}
            </ul>
            {hidden.length ? (
              <details className="mt-1">
                <summary className="inline-flex h-11 cursor-pointer items-center text-dim hover:text-paper md:h-8">
                  {plural(hidden.length, "more role", "more roles")}
                </summary>
                <ul className="space-y-1">
                  {hidden.map((g) => (
                    <RoleLine key={g.key} group={g} tickers={tickers} />
                  ))}
                </ul>
              </details>
            ) : null}
          </section>
        ) : null}

        <p className="mt-4 max-w-[72ch] text-[16px] leading-6">{profile.summary}</p>
        <p className="mt-1 text-[12px] leading-4 text-dim">
          {profile.summary_source === "template"
            ? `Written automatically from ${whoseText(entity.kind)} filings. No outside sources, no opinions.`
            : `Source: ${profile.summary_source}.`}
        </p>
      </div>

      <dl className="mt-4 grid grid-cols-2 border-t border-hairline md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Transactions" value={stats.trades.toLocaleString("en-US")} note="every code, as filed" />
        <Stat
          label="Open-market buys"
          value={
            <span className={stats.open_market_buys ? "text-inflow" : ""}>
              {stats.open_market_buys ? <span aria-hidden>▲ </span> : null}
              {stats.open_market_buys.toLocaleString("en-US")}
            </span>
          }
          note={valueNote(stats.open_market_buys, stats.buy_value, stats.buys_left_out ?? 0)}
        />
        <Stat
          label="Open-market sells"
          value={
            <span className={stats.open_market_sells ? "text-outflow" : ""}>
              {stats.open_market_sells ? <span aria-hidden>▼ </span> : null}
              {stats.open_market_sells.toLocaleString("en-US")}
            </span>
          }
          note={valueNote(stats.open_market_sells, stats.sell_value, stats.sells_left_out ?? 0)}
        />
        <Stat label="Median delay" value={daysText(stats.median_delay_days)} note="calendar days, trade to filing" />
        <Stat
          label="Late disclosures"
          value={stats.late_filings.toLocaleString("en-US")}
          note={stats.late_filings === 1 ? "filing after the deadline" : "filings after the deadline"}
        />
        <Stat
          label="Sales under a 10b5-1 plan"
          value={pct(stats.sales_under_10b5_1_pct)}
          note={stats.sales_under_10b5_1_pct === null ? "not stated on the filings" : "pre-arranged trading plans"}
        />
      </dl>
      <p className="num border-t border-hairline px-4 py-2 text-[12px] leading-4 text-dim md:px-6">
        {stats.first_filed
          ? `Filings on record here: ${
              stats.first_filed === stats.last_filed ? day(stats.first_filed) : `${day(stats.first_filed)} to ${day(stats.last_filed)}`
            }. Older filings appear as history is loaded.`
          : "No filings on record here yet."}
      </p>
      <CoverageNote
        coverage={coverage}
        from={stats.first_filed}
        to={stats.last_filed}
        what="the counts above may be"
        className="border-t border-hairline px-4 py-2 md:px-6"
      />
    </header>
  );
}
