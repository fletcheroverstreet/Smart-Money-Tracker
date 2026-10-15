/**
 * The ticker page header: symbol, company, sector, a Watch button and a strip
 * of key counts. Renders on the server (the Watch button hydrates on its own).
 */
import { CoverageNote } from "@/components/ui/CoverageNote";
import { WatchButton } from "@/components/ui/WatchButton";
import type { DataCoverage, TickerProfile } from "@/lib/api/types";
import { day } from "@/lib/format";

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[12px] font-medium leading-4 text-dim" title={hint}>
        {label}
      </dt>
      <dd className="num mt-1 font-display text-[26px] font-medium leading-[30px]">{value}</dd>
    </div>
  );
}

/** " trades" after a count, so "139" doesn't read as a dollar amount or a share count. */
function TradesUnit({ n }: { n: number }) {
  return (
    <>
      {" "}
      <span className="text-[14px] font-normal text-dim">{n === 1 ? "trade" : "trades"}</span>
    </>
  );
}

const SECTOR_SOURCE: Record<string, string> = {
  curated: "",
  sic: " (from the company's SEC industry code)",
};

export function TickerHeader({
  profile,
  symbol,
  coverage,
  span,
}: {
  profile: TickerProfile;
  symbol: string;
  /** Which filing dates the site holds, to flag a 90-day window with missing days. */
  coverage: DataCoverage | null;
  /** The 90-day window the counts cover (ISO dates, Eastern time). */
  span: { from: string; to: string };
}) {
  const ticker = profile.security.ticker ?? symbol;
  const name = profile.security.name;
  const otherIds = Object.entries(profile.identifiers).filter(([scheme]) => scheme !== "ticker");
  const facts = [
    profile.security.sector ? `Sector: ${profile.security.sector}${SECTOR_SOURCE[profile.sector_source] ?? ""}` : null,
    profile.exchange ? `Listed on ${profile.exchange}` : null,
  ].filter(Boolean);

  return (
    <header className="border-b border-hairline px-4 pb-5 pt-5 md:px-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-[32px] font-semibold leading-9">
            {ticker}
            <span className="ml-3 font-medium text-dim">{name}</span>
          </h1>
          {facts.length ? <p className="mt-1 text-dim">{facts.join(". ")}.</p> : null}
          {otherIds.length ? (
            <p className="mt-1 flex flex-wrap gap-x-3 text-[12px] leading-4 text-dim">
              {otherIds.map(([scheme, value]) => (
                <span key={scheme}>
                  {scheme.toUpperCase()}{" "}
                  <span className="rounded-[var(--radius-id)] font-mono text-paper">{value}</span>
                </span>
              ))}
            </p>
          ) : null}
        </div>
        <WatchButton item={{ kind: "ticker", id: ticker, label: name }} />
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:flex lg:gap-10">
        <Stat
          label="Open-market buys, 90 days"
          value={
            <span className={profile.open_market_buys_90d ? "text-inflow" : ""}>
              <span aria-hidden className="text-[18px]">▲ </span>
              {profile.open_market_buys_90d.toLocaleString("en-US")}
              <TradesUnit n={profile.open_market_buys_90d} />
            </span>
          }
          hint="Purchases on the open market (code P) with a trade date in the last 90 days"
        />
        <Stat
          label="Open-market sales, 90 days"
          value={
            <span className={profile.open_market_sells_90d ? "text-outflow" : ""}>
              <span aria-hidden className="text-[18px]">▼ </span>
              {profile.open_market_sells_90d.toLocaleString("en-US")}
              <TradesUnit n={profile.open_market_sells_90d} />
            </span>
          }
          hint="Sales on the open market (code S) with a trade date in the last 90 days"
        />
        <Stat
          label="Open Form 144 notices"
          value={profile.planned_sales_open}
          hint="Planned sales not yet matched to a Form 4"
        />
        <Stat label="Insider trade rows on file" value={profile.insider_trades.toLocaleString("en-US")} hint="Every code, every date we hold" />
        <Stat label="Last insider filing" value={profile.last_filed ? day(profile.last_filed) : "–"} />
      </dl>
      <CoverageNote coverage={coverage} from={span.from} to={span.to} what="the 90-day counts are" className="mt-3" />
    </header>
  );
}
