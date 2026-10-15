/**
 * The "today" strip on the Live page (spec §8): the largest open-market
 * insider buys and sells disclosed today, and how many filings arrived.
 * "Today" is the US Eastern date, the regulators' clock.
 *
 * EDGAR takes no filings on weekends and SEC holidays. On those days the API
 * sends the latest business day's figures instead (`edgar_open_today` false),
 * and the strip is titled with that day and says EDGAR is closed today.
 *
 * Phones: the panels become a horizontal snap-scroll row.
 *
 * One Form 4 often reports a sale as several lots at different prices, and an
 * owner may file more than once a day. The API (/api/feed/today) already adds
 * up each owner's lots in one company into one entry, so each list names up to
 * five different owners. An entry shows the total; "16 lots" says it adds up
 * several, and screen readers hear each lot's amount. The amount opens the
 * largest lot's quick look in place (a real ?trade= link, so it can also be
 * opened in a new tab).
 *
 * Links are at least 24 px tall on phones (WCAG 2.5.8 target size): each gets
 * 3 px of padding above and below its 18 px line.
 */
import Link from "next/link";

import { QuickLookLink } from "@/components/drawer/quickLookNav";
import type { TodayEntry, TodaySummary } from "@/lib/api/types";
import { tradeAmount } from "@/lib/dataIssues";
import { shares } from "@/lib/format";

const dayTitle = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" });

/** "Friday, September 25" for "2026-09-25". */
function dayName(isoDay: string): string {
  const [y, m, d] = isoDay.split("-").map(Number);
  return dayTitle.format(new Date(Date.UTC(y!, (m ?? 1) - 1, d ?? 1)));
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      aria-label={title}
      className="relative w-[78vw] max-w-[340px] shrink-0 snap-start rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3 md:w-auto md:max-w-none"
    >
      <h3 className="mb-2 text-[12px] font-medium leading-4 text-dim">{title}</h3>
      {children}
    </section>
  );
}

/** Phones: 3 px above and below an 18 px line makes a 24 px link box; desktop keeps the tight line. */
const TARGET = "inline-block py-[3px] md:py-0";

/** Each lot's amount, largest first: "$1.2M, $900K, $40K". A lot filed at a price of 0 reads "–", never $0. */
function lotList(entry: TodayEntry): string {
  return entry.lot_values.map((value) => tradeAmount(value)).join(", ");
}

function TradeList({ entries, direction, isToday }: { entries: TodayEntry[]; direction: "buy" | "sell"; isToday: boolean }) {
  if (!entries.length) {
    const what = direction === "buy" ? "open-market insider buys" : "open-market insider sells";
    return <p className="text-dim">{isToday ? `No ${what} disclosed yet today.` : `No ${what} were disclosed that day.`}</p>;
  }
  const glyph = direction === "buy" ? "▲" : "▼";
  const color = direction === "buy" ? "text-inflow" : "text-outflow";
  const sign = direction === "buy" ? "+" : "−";
  const verb = direction === "buy" ? "Bought" : "Sold";
  return (
    <ol className="space-y-1 md:space-y-2">
      {entries.map((e) => {
        // "–" with no sign when the lots add up to nothing (filed at a price of 0), never "+$0".
        const total = tradeAmount(e.value);
        return (
          <li key={e.largest_transaction_id} className="num leading-[18px]">
            <div className="flex items-baseline justify-between gap-2">
              <span className="flex min-w-0 items-baseline gap-2">
                <span aria-hidden className={color}>
                  {glyph}
                </span>
                {e.ticker ? (
                  <Link href={`/tickers/${e.ticker}`} className={`${TARGET} font-medium hover:underline`}>
                    {e.ticker}
                  </Link>
                ) : (
                  <span className="truncate font-medium">{e.issuer}</span>
                )}
              </span>
              {/* The total; the link opens the largest lot (a quick look shows one trade). */}
              <QuickLookLink id={e.largest_transaction_id} className={`${TARGET} ${color} shrink-0 hover:underline`}>
                <span className="sr-only">
                  {verb}
                  {e.lots > 1 ? ` in ${e.lots} lots, a total of` : ""}{" "}
                </span>
                {/* The sign is for the eye; screen readers already hear "Sold" or "Bought". */}
                {total === "–" ? null : <span aria-hidden>{sign}</span>}
                {total}
                {e.lots > 1 ? <span className="sr-only">. Opens the largest lot, {tradeAmount(e.lot_values[0])}</span> : null}
              </QuickLookLink>
            </div>
            <div className="flex justify-between gap-2 text-[13px] text-dim">
              <Link href={`/people/${e.owner.slug}`} className={`${TARGET} min-w-0 truncate hover:text-paper hover:underline`}>
                {e.owner.name}
                {e.role ? `, ${e.role}` : ""}
              </Link>
              {e.lots > 1 ? (
                <span className="shrink-0 self-center" title={`${e.lots} lots: ${lotList(e)}. The amount opens the largest lot.`}>
                  {e.lots} lots
                  <span className="sr-only">: {lotList(e)}</span>
                </span>
              ) : (
                <span className="shrink-0 self-center">{shares(e.shares)} sh</span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

const FORM_ORDER = ["4", "4/A", "3", "3/A", "5", "5/A", "144", "144/A"];
const formRank = (form: string) => (FORM_ORDER.includes(form) ? FORM_ORDER.indexOf(form) : FORM_ORDER.length);

export function TodayStrip({ today }: { today: TodaySummary }) {
  const forms = Object.entries(today.filings_by_form).sort(([a], [b]) => formRank(a) - formRank(b));
  const total = forms.reduce((sum, [, n]) => sum + n, 0);
  // False on a weekend or SEC holiday: the figures are then the latest business day's. Only
  // an explicit false counts, so an API from before this field reads as an ordinary day.
  const isToday = today.edgar_open_today !== false;
  return (
    <section aria-labelledby="today-title" className="px-4 pt-5 md:px-6">
      <h2 id="today-title" className="font-display text-[22px] font-medium leading-7">
        {isToday ? `Today, ${dayName(today.day)}` : dayName(today.day)}
      </h2>
      {isToday ? null : (
        <p className="mt-1 text-dim">
          EDGAR is closed today (a weekend or SEC holiday), so no filings arrive. This is the latest business day.
        </p>
      )}
      <div className="-mx-4 mt-3 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-1 md:mx-0 md:grid md:grid-cols-3 md:overflow-visible md:px-0">
        <Panel title="Largest insider buys">
          <TradeList entries={today.largest_buys} direction="buy" isToday={isToday} />
        </Panel>
        <Panel title="Largest insider sells">
          <TradeList entries={today.largest_sells} direction="sell" isToday={isToday} />
        </Panel>
        <Panel title="Filings received">
          {total === 0 ? (
            <p className="text-dim">
              {isToday
                ? "No insider filings yet today. EDGAR accepts filings from 6 AM to 10 PM ET on business days."
                : "No insider filings arrived that day."}
            </p>
          ) : (
            <>
              <p className="num font-display text-[28px] font-medium leading-8">{total.toLocaleString("en-US")}</p>
              <ul className="num mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[13px] text-dim">
                {forms.map(([form, n]) => (
                  <li key={form}>
                    Form {form}: <span className="text-paper">{n.toLocaleString("en-US")}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[13px] leading-[18px] text-dim">
                <span className="text-paper">{today.insiders_buying.toLocaleString("en-US")}</span>{" "}
                {today.insiders_buying === 1 ? "insider" : "insiders"} bought on the open market.{" "}
                <span className="text-paper">{today.planned_sales.toLocaleString("en-US")}</span>{" "}
                {today.planned_sales === 1 ? "notice" : "notices"} of a planned sale (Form 144).
              </p>
            </>
          )}
        </Panel>
      </div>
    </section>
  );
}
