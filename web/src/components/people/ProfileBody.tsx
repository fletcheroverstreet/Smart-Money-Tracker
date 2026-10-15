"use client";

/**
 * The lower half of a person's page: the Trades and "Positions over time"
 * tabs. The tab and the codes filter live in the URL (?tab=positions,
 * ?codes=all), so any view can be shared. A click switches at once and the
 * URL follows; Back and Forward are followed too.
 */
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import type { DataCoverage, PositionSeries, TradePage } from "@/lib/api/types";

import { Tabs } from "./controls";
import { type CodesFilter, type ProfileTab, type ProfileView, profileFromParams, profileHref } from "./params";
import { PositionsPanel } from "./PositionsPanel";
import { TradesPanel } from "./TradesPanel";

export function ProfileBody({
  slug,
  initialView,
  initialTrades,
  positions,
  positionsFailed,
  tickers,
  totalTransactions,
  coverage,
}: {
  slug: string;
  initialView: ProfileView;
  initialTrades: TradePage | null;
  positions: PositionSeries[] | null;
  positionsFailed: boolean;
  /** Company slug → ticker, from this person's trades (for links and chart labels). */
  tickers: Record<string, string>;
  totalTransactions: number;
  /** Which filing dates the site holds, for the note under the positions chart. */
  coverage: DataCoverage | null;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const urlView = profileFromParams(params);

  // The view shown right now; it follows the URL when the URL changes on its own (Back, Forward).
  const [view, setViewState] = useState<ProfileView>(urlView);
  const [seen, setSeen] = useState<ProfileView>(urlView);
  if (seen.tab !== urlView.tab || seen.codes !== urlView.codes) {
    setSeen(urlView);
    setViewState(urlView);
  }

  const setView = (next: ProfileView) => {
    setViewState(next);
    // Keep any other parameters (such as ?trade=, the open quick look).
    router.replace(profileHref(slug, next, new URLSearchParams(params.toString())), { scroll: false });
  };

  return (
    <section aria-label="Activity" className="pt-2">
      <div className="border-b border-hairline px-4 md:px-6">
        <Tabs<ProfileTab>
          label="Activity"
          idPrefix="profile"
          selected={view.tab}
          onSelect={(tab) => setView({ ...view, tab })}
          tabs={[
            { id: "trades", label: "Trades" },
            { id: "positions", label: "Positions over time" },
          ]}
        />
      </div>
      <div id="profile-panel" role="tabpanel" aria-labelledby={`profile-tab-${view.tab}`}>
        {view.tab === "trades" ? (
          <TradesPanel
            slug={slug}
            codes={view.codes}
            onCodesChange={(codes: CodesFilter) => setView({ ...view, codes })}
            initial={initialTrades}
            initialCodes={initialView.codes}
            totalTransactions={totalTransactions}
          />
        ) : (
          <PositionsPanel series={positions} tickers={tickers} failed={positionsFailed} coverage={coverage} />
        )}
      </div>
    </section>
  );
}
