"use client";

/**
 * The quick-look drawer (spec §8), mounted once in the root layout.
 *
 * - `?trade=123` shows one trade: the three timestamps, footnotes, owners and
 *   a link to the original filing.
 * - `?event=456` shows a Live event that isn't a single trade: a Form 144
 *   notice of a planned sale (with what became of it) or a Form 3 (the
 *   holdings a new insider listed).
 *
 * Both are driven by the URL, so a quick look is a shareable link and Back
 * closes it (see quickLookNav.tsx). Closing hands focus back to whatever
 * opened the drawer (WCAG 2.4.3).
 */
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Dialog } from "radix-ui";
import { Suspense, useRef, useState } from "react";

import { CodeTag } from "@/components/ui/CodeTag";
import { Direction, SignedAmount } from "@/components/ui/Direction";
import { LateFlag, deadlineRuleText } from "@/components/ui/LateFlag";
import {
  AMENDMENT_UNPAIRED,
  DataIssueNotes,
  ISSUE_TEXT,
  notCountedText,
  priceShown,
  tradeAmount,
  valueText,
} from "@/components/ui/TradeValue";
import { ErrorState, SkeletonRows } from "@/components/ui/States";
import { ApiError, apiGet } from "@/lib/api/client";
import type { FeedEventDetail, FeedPayload, HoldingOut, PlannedSaleOut, TradeDetail } from "@/lib/api/types";
import { focusedElement, restoreFocus } from "@/lib/focus";
import { dateTimeET, day, delayText, ownerTypeText, price, shares, toNumber } from "@/lib/format";
import { usePageReady } from "@/lib/pageReady";

import { QuickLookLink, closeQuickLook } from "./quickLookNav";

// Existing callers import these from here.
export { useOpenEvent, useOpenTrade, useTradeHref } from "./quickLookNav";

export function QuickLook() {
  return (
    <Suspense fallback={null}>
      <QuickLookInner />
    </Suspense>
  );
}

type Shown = { kind: "trade"; id: number } | { kind: "event"; id: number } | null;

function idFrom(raw: string | null): number | null {
  return raw && /^\d+$/.test(raw) ? Number(raw) : null;
}

function QuickLookInner() {
  const params = useSearchParams();
  const tradeId = idFrom(params.get("trade"));
  const eventId = idFrom(params.get("event"));
  const shown: Shown = tradeId !== null ? { kind: "trade", id: tradeId } : eventId !== null ? { kind: "event", id: eventId } : null;

  // Keep the last content while the drawer closes, so it doesn't go blank mid-exit.
  const [last, setLast] = useState<Shown>(shown);
  if (shown && (shown.kind !== last?.kind || shown.id !== last.id)) setLast(shown);
  const body = shown ?? last;

  // The element that had focus when the drawer opened (a row, a link, the Live grid).
  const opener = useRef<HTMLElement | null>(null);

  // A quick look named in the URL (a shared link) opens only once the whole page has loaded and
  // hydrated (lib/pageReady.ts). The dialog marks the rest of the page aria-hidden; doing that
  // while a streamed section is still hydrating is a hydration mismatch. Once the page is ready
  // this stays true, so a quick look opened by a click in the app opens at once.
  const pageReady = usePageReady();

  return (
    <Dialog.Root open={pageReady && shown !== null} onOpenChange={(open) => (open ? null : closeQuickLook())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-[var(--color-scrim)]" />
        <Dialog.Content
          aria-describedby={undefined}
          onOpenAutoFocus={() => {
            opener.current = focusedElement();
          }}
          onCloseAutoFocus={(event) => {
            // Radix would focus a trigger button; this drawer has none, so focus would drop to <body>.
            event.preventDefault();
            restoreFocus(opener.current);
            opener.current = null;
          }}
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[480px] flex-col border-l border-rule-strong bg-console shadow-[var(--shadow-float)]"
        >
          {body?.kind === "trade" ? <TradeBody tradeId={body.id} /> : null}
          {body?.kind === "event" ? <EventBody eventId={body.id} /> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ---------------------------------------------------------------- shared frame

function Frame({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <>
      <div className="flex items-start justify-between gap-3 border-b border-hairline px-5 py-4">
        <Dialog.Title className="font-display text-[22px] font-medium leading-7">{title}</Dialog.Title>
        <Dialog.Close
          className="-mr-2 flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-control)] text-dim hover:bg-raised hover:text-paper"
          aria-label="Close quick look"
        >
          <svg aria-hidden width="16" height="16" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="1.5">
            <path d="M3 3l10 10M13 3L3 13" />
          </svg>
        </Dialog.Close>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[132px_minmax(0,1fr)] gap-3 py-1.5">
      <dt className="text-dim">{label}</dt>
      <dd className="num min-w-0">{children}</dd>
    </div>
  );
}

/** A section with a sentence-case label (design sheet: no uppercase eyebrows). */
function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id}>
      <h3 id={id} className="mb-1 text-[13px] font-medium leading-[18px] text-dim">
        {title}
      </h3>
      {children}
    </section>
  );
}

const linkClass = "underline decoration-hairline underline-offset-2 hover:decoration-paper";

/** A link to the SEC. The ↗ is decoration only: it stays out of the link's accessible name. */
function FilingLinks({ filingUrl, document }: { filingUrl: string; document?: { url: string; label: string } | null }) {
  return (
    <div className="flex flex-wrap gap-3 border-t border-hairline pt-4">
      <a
        href={filingUrl}
        target="_blank"
        rel="noreferrer"
        className="inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-3 hover:bg-raised"
      >
        Original filing on EDGAR
        <span aria-hidden className="ml-1">
          ↗
        </span>
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
      {document ? (
        <a
          href={document.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-11 items-center rounded-[var(--radius-control)] px-3 text-dim hover:bg-raised hover:text-paper"
        >
          {document.label}
          <span aria-hidden className="ml-1">
            ↗
          </span>
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      ) : null}
    </div>
  );
}

function Company({ issuer, ticker }: { issuer: { name: string } | null | undefined; ticker: string | null | undefined }) {
  if (!issuer) return <>–</>;
  if (!ticker) return <>{issuer.name}</>;
  return (
    <Link href={`/tickers/${ticker}`} className={linkClass}>
      {issuer.name} ({ticker})
    </Link>
  );
}

function titleOf(who: string, ticker: string | null | undefined): string {
  return ticker ? `${who}, ${ticker}` : who;
}

// ---------------------------------------------------------------- one trade

function TradeBody({ tradeId }: { tradeId: number }) {
  const query = useQuery({
    queryKey: ["trade", tradeId],
    queryFn: () => apiGet<TradeDetail>(`/api/trades/${tradeId}`),
  });
  const t = query.data?.trade;

  return (
    <Frame title={t ? titleOf(t.owner.name, t.ticker) : "Trade"}>
      {query.isPending ? <SkeletonRows rows={10} height={28} /> : null}
      {query.isError ? (
        <ErrorState
          title="This trade didn't load"
          detail={query.error instanceof ApiError && query.error.status === 404 ? "No trade has this id." : undefined}
        />
      ) : null}
      {query.data ? <TradeFacts detail={query.data} /> : null}
    </Frame>
  );
}

function TradeFacts({ detail }: { detail: TradeDetail }) {
  const t = detail.trade;
  const rule = deadlineRuleText(t.deadline_rule);
  return (
    <div className="space-y-5 px-5 py-4">
      {!t.is_current ? (
        <p role="note" className="rounded-[var(--radius-block)] border border-hairline bg-raised px-3 py-2">
          <span aria-hidden className="text-beacon">! </span>
          An amendment replaced this row.{" "}
          {detail.superseded_by ? (
            <QuickLookLink id={detail.superseded_by} className="underline underline-offset-2">
              See the corrected trade
            </QuickLookLink>
          ) : null}
        </p>
      ) : null}

      <DataIssueNotes issues={t.data_issues} />
      <NotCountedNote detail={detail} />

      <Section id="ql-what" title="What happened">
        <dl>
          <Fact label="Action">
            <span className="flex flex-wrap items-center gap-2">
              <Direction direction={t.direction} label={t.exercise_and_sell && t.code === "S" ? "Exercise and sell" : undefined} />
              <CodeTag code={t.code} label={t.code_label} />
            </span>
          </Fact>
          <Fact label="Value">
            <ValueFact trade={t} />
          </Fact>
          <Fact label="Shares">{shares(t.shares)}</Fact>
          <Fact label="Price">
            {priceShown(t.price, t.data_issues, t.code)}
            {t.price_is_weighted_avg ? <span className="text-dim"> weighted average</span> : null}
          </Fact>
          <Fact label="Owned after">{shares(t.shares_owned_after)}</Fact>
          <Fact label="Security">
            {t.security_title ?? "–"}
            {t.is_derivative ? <span className="text-dim"> (derivative)</span> : null}
          </Fact>
          <Fact label="Account">{ownerTypeText(t.owner_type)}</Fact>
          <Fact label="10b5-1 plan">
            {t.is_10b5_1 === null ? "Not stated" : t.is_10b5_1 ? "Yes, under a pre-arranged trading plan" : "No"}
          </Fact>
        </dl>
      </Section>

      <Section id="ql-when" title="When">
        <dl>
          <Fact label="Traded">{day(t.traded_on)}</Fact>
          <Fact label="Disclosed">{t.accepted_at ? dateTimeET(t.accepted_at) : day(t.filed_on)}</Fact>
          <Fact label={detail.source_note ? "Loaded from history" : "Detected by us"}>{dateTimeET(t.detected_at)}</Fact>
          <Fact label="Delay">
            {delayText(t.delay_days)}
            {t.is_late ? <LateFlag rule={t.deadline_rule} className="ml-2" /> : null}
          </Fact>
          {rule ? <Fact label="Deadline">{rule}</Fact> : null}
        </dl>
      </Section>

      <Section id="ql-who" title="Who">
        <dl>
          <Fact label="Company">
            <Company issuer={t.issuer} ticker={t.ticker} />
          </Fact>
          <Fact label={detail.owners.length > 1 ? "Filed jointly by" : "Filed by"}>
            <ul className="space-y-0.5">
              {detail.owners.map((o) => (
                <li key={o.slug}>
                  <Link href={`/people/${o.slug}`} className={linkClass}>
                    {o.name}
                  </Link>
                </li>
              ))}
            </ul>
          </Fact>
        </dl>
        {detail.owners.length > 1 ? (
          <p className="mt-1 text-[12px] leading-4 text-dim">
            A joint filing reports the same shares once for every owner. Totals count the trade once, under the first
            owner listed (or the fund, if one of them is a fund).
          </p>
        ) : null}
        <OtherCopiesNote detail={detail} />
      </Section>

      {detail.footnotes.length || detail.remarks ? (
        <Section id="ql-notes" title="From the filing">
          <ol className="space-y-2">
            {detail.footnotes.map((f) => (
              <li key={f.id} className="grid grid-cols-[32px_minmax(0,1fr)] gap-1">
                <span className="font-mono text-[12px] text-dim">{f.id}</span>
                <span className="text-[13px] leading-[18px]">{f.text}</span>
              </li>
            ))}
          </ol>
          {detail.remarks ? <p className="mt-2 text-[13px] leading-[18px] text-dim">Remarks: {detail.remarks}</p> : null}
        </Section>
      ) : null}

      {detail.review_note ? (
        <p role="note" className="text-[12px] leading-4 text-dim">
          {detail.review_note}
        </p>
      ) : null}
      {detail.source_note ? (
        <p role="note" className="text-[12px] leading-4 text-dim">
          {detail.source_note}
        </p>
      ) : null}

      <FilingLinks
        filingUrl={t.filing_url}
        document={detail.raw_document_url ? { url: detail.raw_document_url, label: `Form ${t.form_type} document` } : null}
      />
      <p className="text-[12px] leading-4 text-dim">Accession {t.accession}</p>
    </div>
  );
}

/**
 * The Value line (the words come from valueText in lib/dataIssues.ts, tested there):
 * a signed dollar amount, "–" when the filing gives none, or the figure as filed with
 * a note when its price isn't a dollar price per share.
 */
function ValueFact({ trade: t }: { trade: TradeDetail["trade"] }) {
  const shown = valueText(t.value, t.data_issues);
  if (shown.kind === "as_filed") {
    return (
      <span>
        {shown.text} <span className="text-dim">{shown.note}</span>
      </span>
    );
  }
  return (
    <>
      <SignedAmount direction={t.direction} text={shown.text} />
      {/* A second copy is counted once, on the other copy; an unpaired amendment row isn't counted. */}
      {notCountedText(t) ? (
        <span className="text-dim">{t.duplicate_of ? " counted once (see above)" : " not in totals (see above)"}</span>
      ) : null}
    </>
  );
}

/**
 * Why a current open-market buy or sell isn't in the site's totals, when it isn't
 * (the same test as the tables' tag, notCountedText in ui/TradeValue.tsx):
 *
 * - A second copy of a trade another filing reports (`duplicate_of`), for example
 *   a fund and its general partner each filing the fund's sale, or one person
 *   filing the same trade twice. Totals count the trade once, on the copy named
 *   here (backend smt/ingest/quality.py, mark_duplicates); the link opens it.
 * - An amendment row that couldn't be matched to the row it corrects: it may
 *   repeat that row, so totals leave it out.
 *
 * A row an amendment replaced already says so above, and grants, exercises and
 * other non-market rows are never in buy and sell totals, so they get no note.
 */
function NotCountedNote({ detail }: { detail: TradeDetail }) {
  const t = detail.trade;
  if (!notCountedText(t)) return null;
  const copy = t.duplicate_of;
  if (copy) {
    return (
      <p role="note" className="rounded-[var(--radius-block)] border border-hairline bg-raised px-3 py-2">
        {copy.owner.slug === t.owner.slug
          ? `${copy.owner.name} also reported the same trade in another filing; totals count it once. `
          : `The same trade is also reported by ${copy.owner.name}; totals count it once. `}
        <QuickLookLink id={copy.trade_id} className="underline underline-offset-2">
          See the counted copy
        </QuickLookLink>
      </p>
    );
  }
  if (t.data_issues?.includes(AMENDMENT_UNPAIRED)) {
    return (
      <p role="note" className="rounded-[var(--radius-block)] border border-hairline bg-raised px-3 py-2">
        <span aria-hidden className="text-beacon">! </span>
        {ISSUE_TEXT[AMENDMENT_UNPAIRED]}
      </p>
    );
  }
  return null;
}

/**
 * On the copy that totals count: the other filings that report the same trade
 * (their rows point here through `duplicate_of`), so a reader who saw the trade
 * twice in a table knows it is counted once, here.
 */
function OtherCopiesNote({ detail }: { detail: TradeDetail }) {
  const t = detail.trade;
  const copies = detail.copies ?? [];
  if (!copies.length || !t.is_current) return null;
  // One name per owner: a fund can file the same trade more than once.
  const others = [...new Map(copies.map((c) => [c.owner.slug, c.owner])).values()];
  const onlySameOwner = others.length === 1 && others[0]?.slug === t.owner.slug;
  const names = others.map((o) => o.name);
  const who = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
  const where = onlySameOwner ? "is also in another filing by this owner" : `is also reported by ${who}`;
  // Only a counted row can say it is the one counted (a duplicated grant, say, is in no buy or sell total).
  const counting = t.in_totals ? "; totals count it once, on this row." : ".";
  return <p className="mt-1 text-[12px] leading-4 text-dim">{`The same trade ${where}${counting}`}</p>;
}

// ---------------------------------------------------------------- a feed event (Form 144, Form 3)

function EventBody({ eventId }: { eventId: number }) {
  const query = useQuery({
    queryKey: ["feed-event", eventId],
    queryFn: () => apiGet<FeedEventDetail>(`/api/feed/events/${eventId}`),
  });
  const detail = query.data;
  const p = detail ? (detail.item.payload as unknown as FeedPayload) : null;
  const fallbackTitle = detail?.item.event_type === "planned_sale" ? "Planned sale" : "Filing";

  return (
    <Frame title={p?.owner ? titleOf(p.owner.name, p.ticker) : fallbackTitle}>
      {query.isPending ? <SkeletonRows rows={10} height={28} /> : null}
      {query.isError ? (
        <ErrorState
          title="This filing didn't load"
          detail={query.error instanceof ApiError && query.error.status === 404 ? "No Live event has this id." : undefined}
        />
      ) : null}
      {detail && p ? (
        <div className="space-y-5 px-5 py-4">
          {detail.item.event_type === "planned_sale" ? (
            <PlannedSaleFacts detail={detail} p={p} />
          ) : detail.item.event_type === "insider_holding" ? (
            <HoldingFacts detail={detail} p={p} />
          ) : (
            <p className="text-dim">This Live event has no details beyond its filing.</p>
          )}
          <FilingLinks filingUrl={detail.filing_url} />
          <p className="text-[12px] leading-4 text-dim">Accession {detail.accession}</p>
        </div>
      ) : null}
    </Frame>
  );
}

function Filed({ detail }: { detail: FeedEventDetail }) {
  return <>{detail.accepted_at ? dateTimeET(detail.accepted_at) : day(detail.filed_on)}</>;
}

function PlannedSaleFacts({ detail, p }: { detail: FeedEventDetail; p: FeedPayload }) {
  const sale = detail.planned_sale ?? null;
  const seller = sale?.seller ?? p.owner;
  const relationship = sale?.relationships.length ? sale.relationships.join(", ") : p.owner.role || null;
  // A market value of 0 gives no estimate (notices filed that way mostly list 0 shares too,
  // e.g. 0001959173-26-007025), so it reads "–", never "~$0".
  const value = tradeAmount(sale?.market_value ?? p.market_value);
  return (
    <>
      <p>
        <Direction direction="planned_sale" />
        <span className="mt-1 block text-dim">
          A Form 144 is a notice that someone intends to sell restricted or control shares. It isn&apos;t a sale: the
          sale, if it happens, is reported later on a Form 4.
        </span>
      </p>

      {/* A 144/A replaces the notice it amends. The API then sends the amended notice (its own
          accession), whose figures and Form 4 links are the ones that stand. */}
      {sale && sale.accession !== detail.accession ? (
        <p role="note" className="rounded-[var(--radius-block)] border border-hairline bg-raised px-3 py-2">
          <span aria-hidden className="text-beacon">! </span>
          A Form 144/A{sale.filed_on ? ` filed ${day(sale.filed_on)}` : ""} amended this notice. The figures and the Form
          4 follow-up below are the amended notice&apos;s.{" "}
          <a href={sale.filing_url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
            The 144/A on EDGAR
            <span aria-hidden className="ml-1">
              ↗
            </span>
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        </p>
      ) : null}

      <Section id="ql-notice" title="The notice">
        <dl>
          <Fact label="Seller">
            <Link href={`/people/${seller.slug}`} className={linkClass}>
              {seller.name}
            </Link>
          </Fact>
          <Fact label="Relationship">{relationship ?? <span className="text-dim">Not stated</span>}</Fact>
          <Fact label="Company">
            <Company issuer={p.issuer} ticker={p.ticker} />
          </Fact>
          {p.security_title ? <Fact label="Security">{p.security_title}</Fact> : null}
          <Fact label="Plans to sell">{shares(sale?.shares ?? p.shares)} shares</Fact>
          <Fact label="Estimated value">
            {value === "–" ? (
              <span className="text-dim">Not stated</span>
            ) : (
              <>
                ~{value} <span className="text-dim">(approximate: the seller&apos;s estimate on the notice)</span>
              </>
            )}
          </Fact>
          <Fact label="Sale date">
            {sale?.approx_sale_on ?? p.approx_sale_on ? (
              <>
                Around {day(sale?.approx_sale_on ?? p.approx_sale_on)} <span className="text-dim">(approximate)</span>
              </>
            ) : (
              <span className="text-dim">Not stated</span>
            )}
          </Fact>
          <Fact label="Filed">
            <Filed detail={detail} />
          </Fact>
          {sale?.plan_adoption_dates.length ? (
            <Fact label="10b5-1 plan">Adopted {sale.plan_adoption_dates.map((d) => day(d)).join(", ")}</Fact>
          ) : null}
        </dl>
      </Section>

      <Section id="ql-followup" title="Did a Form 4 follow?">
        <FollowUp sale={sale} detail={detail} />
      </Section>
    </>
  );
}

/**
 * What became of the notice. A match is a code-S sale by the same person in
 * the same company within 90 days (backend smt/ingest/form144.py), so it's
 * "likely", never certain.
 */
function FollowUp({ sale, detail }: { sale: PlannedSaleOut | null; detail: FeedEventDetail }) {
  const matches = detail.matches ?? [];
  if (!sale) return <p className="text-dim">This notice is still being read. Check back in a minute.</p>;

  if (sale.status === "matched" || matches.length) {
    const planned = toNumber(sale.shares);
    const matched = toNumber(sale.matched_shares);
    const part = planned !== null && matched !== null && matched < planned;
    return (
      <>
        <p>
          <span aria-hidden>✓ </span>
          {part
            ? `Likely carried out in part: ${shares(matched)} of ${shares(planned)} shares on Form 4`
            : `Likely carried out: ${shares(matched ?? planned)} shares on Form 4`}
        </p>
        {matches.length ? (
          <ul className="num mt-2 space-y-1">
            {matches.map((m) => (
              <li key={m.transaction_id}>
                <QuickLookLink id={m.transaction_id} className={linkClass}>
                  Sold {shares(m.shares)} shares on {day(m.traded_on)}
                  {m.price ? ` at ${price(m.price)}` : ""}
                </QuickLookLink>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="mt-2 text-[12px] leading-4 text-dim">
          Matched by seller, company and a sale within 90 days of the notice, so the link is likely, not certain.
        </p>
      </>
    );
  }
  if (sale.status === "no_form4_found") {
    return (
      <>
        <p>No Form 4 found within 90 days.</p>
        <p className="mt-1 text-[12px] leading-4 text-dim">
          Many people who file a Form 144 don&apos;t have to file a Form 4, so this doesn&apos;t mean the sale didn&apos;t
          happen.
        </p>
      </>
    );
  }
  if (sale.status === "withdrawn") return <p>The notice was withdrawn.</p>;
  return (
    <>
      <p>Waiting for a Form 4.</p>
      <p className="mt-1 text-[12px] leading-4 text-dim">
        A sale is usually reported within two business days. We look for one for 90 days after the notice.
      </p>
    </>
  );
}

function HoldingFacts({ detail, p }: { detail: FeedEventDetail; p: FeedPayload }) {
  const holdings = detail.holdings ?? [];
  return (
    <>
      <p>
        <Direction direction="holding" />
        <span className="mt-1 block text-dim">
          A Form 3 is the first statement a new insider files, listing what they already own. It records holdings, not
          trades.
        </span>
      </p>

      <Section id="ql-insider" title="The insider">
        <dl>
          <Fact label="Insider">
            <Link href={`/people/${p.owner.slug}`} className={linkClass}>
              {p.owner.name}
            </Link>
          </Fact>
          <Fact label="Role">{p.owner.role || <span className="text-dim">Not stated</span>}</Fact>
          <Fact label="Company">
            <Company issuer={p.issuer} ticker={p.ticker} />
          </Fact>
          <Fact label="Filed">
            <Filed detail={detail} />
          </Fact>
        </dl>
      </Section>

      <Section id="ql-holdings" title="Holdings listed">
        {p.no_securities_owned ? (
          <p>This statement says they own no securities of the company.</p>
        ) : holdings.length === 0 ? (
          <p className="text-dim">No holdings are listed on this statement.</p>
        ) : (
          <table className="num w-full border-collapse text-[13px] leading-[18px]">
            <caption className="sr-only">Holdings listed on this Form 3</caption>
            <thead className="text-left text-[12px] leading-4 text-dim">
              <tr className="border-b border-hairline">
                <th scope="col" className="py-1.5 pr-3 font-medium">Security</th>
                <th scope="col" className="py-1.5 pr-3 text-right font-medium">Shares</th>
                <th scope="col" className="py-1.5 font-medium">Held</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((h, i) => {
                const terms = h.is_derivative ? derivativeTerms(h) : null;
                // Form 3 has no count of the derivatives themselves; their size is the shares they are on.
                const underlying = h.is_derivative && h.shares == null && h.underlying_shares != null;
                const notes = h.footnote_ids ?? [];
                return (
                  <tr key={i} className="border-b border-hairline align-top">
                    <td className="py-1.5 pr-3">
                      {h.security_title ?? "–"}
                      {h.is_derivative ? <span className="text-dim"> (derivative)</span> : null}
                      {notes.length ? (
                        <span className="font-mono text-[11px] text-dim">
                          {" "}
                          <span className="sr-only">footnotes </span>
                          {notes.join(", ")}
                        </span>
                      ) : null}
                      {terms ? <span className="block text-[12px] leading-4 text-dim">{terms}</span> : null}
                    </td>
                    <td className="whitespace-nowrap py-1.5 pr-3 text-right">
                      {underlying ? (
                        <>
                          {shares(h.underlying_shares)}
                          <span className="block text-[12px] leading-4 text-dim">underlying</span>
                        </>
                      ) : (
                        shares(h.shares)
                      )}
                    </td>
                    <td className="py-1.5">
                      {h.ownership === "Direct" ? "Direct" : <>Indirect<span className="text-dim">: {h.ownership}</span></>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Section>

      {detail.holding_footnotes?.length ? (
        <Section id="ql-holding-notes" title="From the filing">
          <ol className="space-y-2">
            {detail.holding_footnotes.map((f) => (
              <li key={f.id} className="grid grid-cols-[32px_minmax(0,1fr)] gap-1">
                <span className="font-mono text-[12px] text-dim">{f.id}</span>
                <span className="text-[13px] leading-[18px]">{f.text}</span>
              </li>
            ))}
          </ol>
        </Section>
      ) : null}
    </>
  );
}

/**
 * The terms of a derivative holding in one line, as filed, e.g. "Options on
 * 10,000 shares at $12.50, exercisable from Mar 1, 2027, expiring Mar 1, 2031".
 *
 * - The size is the number of underlying shares: an option on 10,000 shares
 *   gives the right to buy 10,000 shares (Form 3 has no count of the options).
 * - The price is the exercise price (what the holder pays per share to use an
 *   option) or, for a convertible security, the conversion price.
 * - "Exercisable from" is the first date it can be used, often a vesting date;
 *   many filings leave it blank and explain vesting in a footnote instead.
 * - Parts the filing leaves out are left out here, never guessed.
 * - The underlying security is named only when it isn't plain common stock
 *   (e.g. "Class A Common Stock"), which is what almost all of them are on.
 * - When the title says what the securities are (options, warrants, units such
 *   as restricted stock units, stock appreciation rights), the line starts with
 *   that word. Otherwise (convertible preferred stock, notes and the like) it
 *   lists the terms plainly, e.g. "8,000 underlying shares, conversion or
 *   exercise price $4.00" ("conversion or exercise price" is the form's own
 *   name for the field).
 * - When the size is the only term, the line would only repeat the Security and
 *   Shares columns next to it (restricted stock units often give nothing else),
 *   so it returns null; likewise when the filing gives none of them.
 */
function derivativeTerms(h: HoldingOut): string | null {
  // The underlying security, named only when it isn't plain common stock.
  const rawUnderlying = h.underlying_title?.trim() ?? "";
  const underlying = rawUnderlying && !/^common stock$/i.test(rawUnderlying) ? rawUnderlying : null;
  const hasSize = h.underlying_shares != null;
  // Units and grants often carry an exercise price of 0: that means none, not "at $0.00".
  const hasPrice = h.exercise_price != null && Number(h.exercise_price) > 0;
  const exercisable = h.exercise_date ? `exercisable from ${day(h.exercise_date)}` : null;
  const expiring = h.expiration_date ? `expiring ${day(h.expiration_date)}` : null;
  if (!hasPrice && !exercisable && !expiring && !underlying) return null;

  const title = h.security_title ?? "";
  const noun = /option/i.test(title)
    ? "Options"
    : /warrant/i.test(title)
      ? "Warrants"
      : /\bunits?\b|\b[RP]SUs?\b/i.test(title)
        ? "Units"
        : /appreciation right|\bSARs?\b/i.test(title)
          ? "Rights"
          : null;

  let head: string[];
  if (noun) {
    // "Options on 10,000 shares of Class A Common Stock at $12.50"
    const on = hasSize
      ? ` on ${shares(h.underlying_shares)} shares${underlying ? ` of ${underlying}` : ""}`
      : underlying
        ? ` on ${underlying}`
        : "";
    head = [`${noun}${on}${hasPrice ? ` at ${price(h.exercise_price)}` : ""}`];
  } else {
    // "8,000 underlying shares of Class B Common Stock, conversion or exercise price $4.00"
    head = [
      hasSize
        ? `${shares(h.underlying_shares)} underlying shares${underlying ? ` of ${underlying}` : ""}`
        : underlying
          ? `underlying security: ${underlying}`
          : null,
      hasPrice ? `conversion or exercise price ${price(h.exercise_price)}` : null,
    ].filter((part): part is string => part !== null);
  }
  const text = [...head, exercisable, expiring].filter((part): part is string => part !== null).join(", ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
