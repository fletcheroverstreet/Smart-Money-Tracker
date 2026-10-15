"use client";

/**
 * The Watchlist page: the tickers and people this browser follows
 * (lib/watchlist.ts keeps them in localStorage; nothing is sent to the server
 * except the requests for their latest filings).
 *
 * Each followed item gets a card with its three most recent disclosures:
 * - ticker: GET /api/tickers/{symbol}/insiders?codes=all&limit=3
 * - person: GET /api/people/{slug}/trades?codes=all&limit=3
 * codes=all so grants, option exercises and gifts count as "latest" too, not
 * only open-market buys and sells.
 */
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSyncExternalStore } from "react";

import { useOpenTrade } from "@/components/drawer/QuickLook";
import { ExternalLink } from "@/components/info/ExternalLink";
import { Direction } from "@/components/ui/Direction";
import { LateFlag } from "@/components/ui/LateFlag";
import { NotCountedTag, TradeValue, notCountedText, priceShown } from "@/components/ui/TradeValue";
import { ApiError, apiGet } from "@/lib/api/client";
import type { TradePage, TradeRow } from "@/lib/api/types";
import { day, shares } from "@/lib/format";
import { type WatchItem, useWatchlist } from "@/lib/watchlist";

const LATEST = 3;

/**
 * False during the server render and hydration, true once running in the
 * browser. The watchlist lives in localStorage, which the server can't see, so
 * until then we show a skeleton instead of a wrong "you follow nothing".
 */
function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

function apiPath(item: WatchItem): string {
  const id = encodeURIComponent(item.id);
  return item.kind === "ticker" ? `/api/tickers/${id}/insiders` : `/api/people/${id}/trades`;
}

function pageHref(item: WatchItem): string {
  const id = encodeURIComponent(item.id);
  return item.kind === "ticker" ? `/tickers/${id}` : `/people/${id}`;
}

// ---------------------------------------------------------------- one disclosure

/** "same day", "1 day later", "3 days later": calendar days from the trade to the filing. */
function delayWords(days: number | null | undefined): string {
  if (days === null || days === undefined) return "delay unknown";
  if (days <= 0) return "same day";
  return days === 1 ? "1 day later" : `${days} days later`;
}

function TradeLine({ trade, kind }: { trade: TradeRow; kind: WatchItem["kind"] }) {
  const openTrade = useOpenTrade();
  // On a ticker's card the interesting part is who traded; on a person's card, which company.
  const who = kind === "ticker" ? trade.owner.name : trade.ticker ?? trade.issuer?.name ?? "–";
  const market = trade.direction === "buy" || trade.direction === "sell";
  return (
    <li className="flex items-stretch border-t border-hairline">
      <button
        type="button"
        onClick={() => openTrade(trade.id)}
        className="num block min-h-14 min-w-0 flex-1 px-4 py-2 text-left text-[13px] leading-[18px] hover:bg-raised"
      >
        <span className="flex items-baseline justify-between gap-3">
          <span className="flex min-w-0 items-baseline gap-2">
            {market ? (
              <Direction direction={trade.direction} />
            ) : (
              <span className="shrink-0 text-dim">
                <span className="font-mono">{trade.code ?? "–"}</span> {trade.code_label}
              </span>
            )}
            <span className="truncate font-medium">{who}</span>
          </span>
          <span className="shrink-0">
            <TradeValue
              direction={trade.direction}
              value={trade.value}
              issues={trade.data_issues}
              counted={notCountedText(trade) === null}
            />
          </span>
        </span>
        <span className="flex justify-between gap-3 text-dim">
          <span className="truncate">
            {day(trade.traded_on)}, filed {delayWords(trade.delay_days)}
            {trade.is_late ? <LateFlag rule={trade.deadline_rule} className="ml-1.5" /> : null}
          </span>
          <span className="shrink-0">
            {shares(trade.shares)} sh at {priceShown(trade.price, trade.data_issues, trade.code)}
          </span>
        </span>
        <NotCountedTag trade={trade} />
      </button>
      <ExternalLink
        href={trade.filing_url}
        className="flex shrink-0 items-center border-l border-hairline px-3 text-[13px] text-dim underline decoration-rule-strong underline-offset-2 hover:text-paper"
      >
        Filing
      </ExternalLink>
    </li>
  );
}

// ---------------------------------------------------------------- one followed item

function WatchCard({ item, onRemove }: { item: WatchItem; onRemove: () => void }) {
  const latest = useQuery({
    queryKey: ["watchlist", item.kind, item.id],
    queryFn: () => apiGet<TradePage>(apiPath(item), { codes: "all", limit: LATEST }),
    // A 404 (ticker renamed, person merged) won't fix itself on retry.
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
    staleTime: 60_000,
  });
  const notFound = latest.error instanceof ApiError && latest.error.status === 404;
  const label = item.label || item.id;

  return (
    <li className="overflow-hidden rounded-[var(--radius-panel)] border border-hairline bg-console">
      <div className="flex items-center justify-between gap-3 px-4 py-2">
        <div className="min-w-0">
          <Link href={pageHref(item)} className="block truncate font-medium hover:underline">
            {item.kind === "ticker" ? <span className="font-mono">{item.id}</span> : label}
          </Link>
          <p className="truncate text-[12px] leading-4 text-dim">
            {item.kind === "ticker" ? (label !== item.id ? label : "Ticker") : "Person"}
          </p>
        </div>
        <button
          type="button"
          onClick={onRemove}
          className="inline-flex h-11 shrink-0 items-center gap-1.5 rounded-[var(--radius-control)] border border-rule-strong px-3 text-[13px] text-dim hover:text-paper md:h-9"
        >
          <span aria-hidden>★</span>
          Remove<span className="sr-only"> {item.kind === "ticker" ? item.id : label} from the watchlist</span>
        </button>
      </div>

      {latest.isPending ? (
        <div aria-hidden className="space-y-1 border-t border-hairline px-4 py-2">
          {Array.from({ length: LATEST }, (_, i) => (
            <div key={i} className="skeleton h-12" />
          ))}
        </div>
      ) : notFound ? (
        <p className="border-t border-hairline px-4 py-3 text-[13px] text-dim">
          {item.kind === "ticker"
            ? `We have no record of the ticker ${item.id} any more. It may have changed; search for the company to follow it again.`
            : "We have no record of this person any more. Their page may have moved; search for them to follow them again."}
        </p>
      ) : latest.isError ? (
        <p role="alert" className="border-t border-hairline px-4 py-3 text-[13px]">
          <span aria-hidden className="text-beacon">! </span>
          The latest filings didn&apos;t load.{" "}
          <button
            type="button"
            onClick={() => void latest.refetch()}
            className="inline-flex min-h-11 items-center underline decoration-rule-strong underline-offset-2 hover:text-paper md:min-h-0"
          >
            Try again
          </button>
        </p>
      ) : latest.data.items.length === 0 ? (
        <p className="border-t border-hairline px-4 py-3 text-[13px] text-dim">No disclosures on file yet.</p>
      ) : (
        <ul aria-label={`Latest disclosures for ${item.kind === "ticker" ? item.id : label}`}>
          {latest.data.items.map((t) => (
            <TradeLine key={t.id} trade={t} kind={item.kind} />
          ))}
        </ul>
      )}
    </li>
  );
}

// ---------------------------------------------------------------- the page body

function Group({ title, items, onRemove }: { title: string; items: WatchItem[]; onRemove: (item: WatchItem) => void }) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title} className="px-4 pt-6 md:px-6">
      <h2 className="font-display text-[22px] font-medium leading-7">
        {title} <span className="num text-dim">{items.length}</span>
      </h2>
      <ul className="mt-3 grid gap-3 lg:grid-cols-2">
        {items.map((item) => (
          <WatchCard key={`${item.kind}:${item.id}`} item={item} onRemove={() => onRemove(item)} />
        ))}
      </ul>
    </section>
  );
}

export function WatchlistView() {
  const hydrated = useHydrated();
  const { items, remove } = useWatchlist();

  if (!hydrated) {
    return (
      <div aria-busy="true" className="space-y-3 px-4 pt-6 md:px-6">
        <span className="sr-only">Loading your watchlist…</span>
        <div className="skeleton h-7 w-40" />
        <div className="skeleton h-40" />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="px-4 py-10 md:px-6">
        <p className="font-display text-[22px] font-medium leading-7">You aren&apos;t watching anything yet</p>
        <div className="mt-2 max-w-prose space-y-2 text-dim">
          <p>
            Open a ticker or a person and press <span className="text-paper">☆ Watch</span> at the top of their page.
            They&apos;ll show up here with their latest filings.
          </p>
          <p>
            Start from{" "}
            <Link href="/tickers" className="underline decoration-rule-strong underline-offset-2 hover:text-paper">
              Tickers
            </Link>
            ,{" "}
            <Link href="/people" className="underline decoration-rule-strong underline-offset-2 hover:text-paper">
              People
            </Link>{" "}
            or the{" "}
            <Link href="/" className="underline decoration-rule-strong underline-offset-2 hover:text-paper">
              Live
            </Link>{" "}
            feed.
          </p>
        </div>
      </div>
    );
  }

  const tickers = items.filter((i) => i.kind === "ticker");
  const people = items.filter((i) => i.kind === "person");
  return (
    <div>
      <Group title="Tickers" items={tickers} onRemove={(i) => remove(i.kind, i.id)} />
      <Group title="People" items={people} onRemove={(i) => remove(i.kind, i.id)} />
    </div>
  );
}
