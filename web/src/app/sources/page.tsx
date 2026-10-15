import type { Metadata } from "next";

import { ExternalLink } from "@/components/info/ExternalLink";
import { DocLink, DocSection, Note, Prose, Toc, type TocItem } from "@/components/docs/Doc";
import { SourceCard } from "@/components/docs/SourceList";
import { PRICE_PROVIDERS } from "@/components/docs/sourceTerms";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorState } from "@/components/ui/States";
import { apiGetOrNull } from "@/lib/api/server";
import type { SourceOut } from "@/lib/api/types";

/*
 * The source list comes from GET /api/meta/sources, so it can't drift from the
 * backend. The fair-access section describes backend/src/smt/net/client.py,
 * net/ratelimit.py and config.py (SEC_USER_AGENT, SEC_RATE_LIMIT). The prices
 * section summarises docs/plan/source-verification.md ("prices-and-quotes")
 * and PLAN.md decision 1. Keep them in step with that code and evidence.
 */

export const metadata: Metadata = {
  title: "Sources",
  description:
    "Every data source behind Smart Money Tracker: what it shows, its legal deadline, how often we check it, its terms of use, and why market prices aren't shown publicly.",
};

// The list is read from the API on each request, like every other page.
export const dynamic = "force-dynamic";

const SECTIONS: TocItem[] = [
  { id: "in-use", label: "Sources in use" },
  { id: "planned", label: "Planned sources" },
  { id: "edgar", label: "What we read from EDGAR" },
  { id: "fair-access", label: "How we treat the SEC’s servers" },
  { id: "prices", label: "Why there are no market prices" },
];

/** Planned sources after live ones; within each group, by phase (the API's order breaks ties). */
function byPhase(a: SourceOut, b: SourceOut): number {
  return a.phase - b.phase;
}

export default async function SourcesPage() {
  const sources = await apiGetOrNull<SourceOut[]>("/api/meta/sources");
  const live = (sources ?? []).filter((s) => s.status === "live").sort(byPhase);
  const planned = (sources ?? []).filter((s) => s.status !== "live").sort(byPhase);

  return (
    <div className="pb-10">
      <PageHeader
        title="Sources"
        subtitle="Where every number on this site comes from, how quickly each source publishes, and the rules for using it."
      />
      <Toc items={SECTIONS} />

      <DocSection
        id="in-use"
        title="Sources in use"
        lead="Everything on the site today comes from these. Each trade links to its own filing, so any number can be checked at the source."
      >
        {sources === null ? (
          <ErrorState
            title="The source list didn't load"
            detail="The data service didn't answer, so the list of sources can't be shown right now."
          />
        ) : live.length === 0 ? (
          <p className="text-dim">No source is marked as in use.</p>
        ) : (
          <div className="grid gap-3 xl:grid-cols-2">
            {live.map((source) => (
              <SourceCard key={source.key} source={source} />
            ))}
          </div>
        )}
      </DocSection>

      <DocSection
        id="planned"
        title="Planned sources"
        lead="Nothing from these sources is on the site yet. The cards describe what each will add and the rules that will apply."
      >
        {sources === null ? (
          <p className="text-dim">Shown once the data service answers.</p>
        ) : planned.length === 0 ? (
          <p className="text-dim">No sources are planned.</p>
        ) : (
          <div className="grid gap-3 xl:grid-cols-2">
            {planned.map((source) => (
              <SourceCard key={source.key} source={source} />
            ))}
          </div>
        )}
      </DocSection>

      <DocSection id="edgar" title="What we read from EDGAR">
        <Prose>
          <ul>
            <li>
              <strong>The latest-filings feed</strong>, for new filings: the newest page for Form 4 every 20
              seconds, and for Forms 3 and 5 and Form 144 every 60 seconds, from 6 AM to 10 PM Eastern on SEC business
              days, with catch-up reads further back every one to two minutes; every 10 minutes at other times.{" "}
              <ExternalLink href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent">Latest filings</ExternalLink>
            </li>
            <li>
              <strong>Each filing’s document list</strong>, to find the right XML document. File names vary, so the
              document is recognised by its contents, never by guessing its name.
            </li>
            <li>
              <strong>The daily index</strong>, which the SEC posts each business-day evening. A nightly check
              compares the last few days with it, and a check every 10 minutes goes through every day we hold that
              isn’t fully loaded and fetches whatever is missing (a day counts as loaded when at least 99% of the
              filings in its index are held).{" "}
              <ExternalLink href="https://www.sec.gov/Archives/edgar/daily-index/">Daily index</ExternalLink>
            </li>
            <li>
              <strong>The company tickers list</strong>, daily: which current ticker and exchange belong to which
              company.{" "}
              <ExternalLink href="https://www.sec.gov/files/company_tickers_exchange.json">Company tickers</ExternalLink>
            </li>
            <li>
              <strong>Each company’s submissions file</strong>, refreshed daily in batches: its current and former
              names, industry code (used for the sector label) and tickers. The same file gives the name EDGAR has on
              record for a person named only in a Form 144’s free text; stored copies are re-read when the naming rules
              change, without asking the SEC again.{" "}
              <ExternalLink href="https://www.sec.gov/search-filings/edgar-application-programming-interfaces">
                EDGAR APIs
              </ExternalLink>
            </li>
            <li>
              <strong>Each company’s reported public float</strong> (company facts, XBRL), for companies with large
              trades, refreshed every 90 days: the market value of its shares not held by insiders, as stated on its
              annual report. It is used only to catch a trade value that can’t be right (see the{" "}
              <DocLink href="/methodology#typos">value checks</DocLink>).{" "}
              <ExternalLink href="https://www.sec.gov/search-filings/edgar-application-programming-interfaces">
                EDGAR APIs
              </ExternalLink>
            </li>
            <li>
              <strong>The quarterly Insider Transactions Data Sets</strong>, for older filings. They round every number
              to the cent, so a filing’s own XML always replaces them when we have it.{" "}
              <ExternalLink href="https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets">
                Data sets
              </ExternalLink>
            </li>
          </ul>
          <p>
            The <DocLink href="/methodology#collection">Methodology page</DocLink> explains the timing, and the{" "}
            <DocLink href="/status">Status page</DocLink> shows when each source was last checked.
          </p>
        </Prose>
      </DocSection>

      <DocSection
        id="fair-access"
        title="How we treat the SEC’s servers"
        lead="The SEC allows automated tools at most 10 requests per second in total, and only if they say who they are. A tool that goes over the limit is blocked until it has stayed under it for 10 minutes."
      >
        <Prose>
          <ul>
            <li>
              <strong>We say who we are.</strong> Every request to the SEC carries a User-Agent naming this app and a
              contact email address. Without one configured, the collector refuses to contact the SEC at all, because
              the SEC blocks undeclared tools even at low rates.
            </li>
            <li>
              <strong>We stay under the limit.</strong> All requests to SEC sites share one budget, 8 per second by
              default. The collector won’t start with a setting of 10 or more.
            </li>
            <li>
              <strong>Live news goes first.</strong> When requests queue up, checking for new filings is served
              first. Fetching filings found by the nightly check or the fill of missing days comes next, then
              reference data, then history loads.
            </li>
            <li>
              <strong>We back off.</strong> If the SEC answers “forbidden” (HTTP 403), all SEC requests pause for 11
              minutes, longer than that 10-minute wait, and the error is logged. Busy or failing responses are
              retried after growing waits.
            </li>
            <li>
              <strong>We don’t fetch twice.</strong> Every document is stored, compressed, before it is read, so reading
              it again later doesn’t mean downloading it again.
            </li>
          </ul>
          <p>
            <ExternalLink href="https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data">
              The SEC’s fair-access policy
            </ExternalLink>
          </p>
        </Prose>
      </DocSection>

      <DocSection
        id="prices"
        title="Why there are no market prices"
        lead="Public pages show only the prices written in the filings themselves. There are no price charts, live quotes or returns."
      >
        <Prose>
          <p>
            Before building the site we read the terms of every free source of stock prices (checked on 23 September
            2026). Each one allows its data for personal or internal use only, or blocks automated access:
          </p>
        </Prose>
        <ul className="mt-3 max-w-[72ch] divide-y divide-hairline rounded-[var(--radius-panel)] border border-hairline bg-console">
          {PRICE_PROVIDERS.map((provider) => (
            <li key={provider.name} className="relative px-4 py-3">
              <p className="font-medium">{provider.name}</p>
              <p className="mt-0.5 text-dim">{provider.finding}</p>
              {provider.href ? (
                <p className="mt-1">
                  <ExternalLink
                    href={provider.href}
                    className="inline-flex min-h-11 items-center gap-1 text-[13px] underline decoration-rule-strong underline-offset-2 hover:decoration-paper md:min-h-0"
                  >
                    Read the terms
                  </ExternalLink>
                </p>
              ) : null}
            </li>
          ))}
        </ul>
        <div className="mt-4">
          <Prose>
            <p>
              Returns and performance figures are calculated from those prices, and at least one provider’s terms
              cover results derived from its data too, so they stay off public pages as well. Filing prices are
              different: an insider’s reported price per share is part of the public disclosure, so it is shown.
            </p>
            <p>
              The private daily closes, and the stock splits the price source reports, are also used behind the scenes by
              one of the <DocLink href="/methodology#typos">value checks</DocLink>: a filing’s price far above the
              market’s is flagged. Only the flag is shown, never the close.
            </p>
          </Prose>
        </div>
        <Note title="What would change this">
          <p>
            Every stored price is tagged with its source and licence. If a licence that allows public display is
            bought later, prices can be switched on for that source. Until then, prices are only for the site owner’s
            personal research, in a private mode that visitors can’t see.
          </p>
        </Note>
      </DocSection>
    </div>
  );
}
