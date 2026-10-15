/**
 * Friendly names for the API's response types. These are GENERATED from the
 * Python API (schema.d.ts, via OpenAPI), never written by hand, so a backend
 * change shows up here as a type error instead of a silent mismatch.
 *
 * Regenerate after changing backend/src/smt/api/schemas.py:
 *   npm run gen:openapi && npm run gen:api
 */
import type { components } from "./schema";

type S = components["schemas"];

export type FeedItem = S["FeedItem"];
export type FeedPage = S["FeedPage"];
export type TodaySummary = S["TodaySummary"];
export type TodayEntry = S["TodayEntry"];
export type TradeRow = S["TradeRow"];
export type TradePage = S["TradePage"];
export type TradeDetail = S["TradeDetail"];
export type PersonProfile = S["PersonProfile"];
export type PersonStats = S["PersonStats"];
export type RoleOut = S["RoleOut"];
export type PositionSeries = S["PositionSeries"];
export type PositionPoint = S["PositionPoint"];
export type DirectoryPage = S["DirectoryPage"];
export type DirectoryRow = S["DirectoryRow"];
export type TickerProfile = S["TickerProfile"];
export type TickerIndexPage = S["TickerIndexPage"];
export type TickerActivityRow = S["TickerActivityRow"];
export type TimelineEvent = S["TimelineEvent"];
export type PlannedSaleOut = S["PlannedSaleOut"];
export type SearchResults = S["SearchResults"];
export type SearchHit = S["SearchHit"];
export type StatusReport = S["StatusReport"];
export type DetectionDelay = S["DetectionDelay"];
export type Coverage = S["Coverage"];
export type SpotCheckRow = S["SpotCheckRow"];
export type JobStatus = S["JobStatus"];
export type CoverageRow = S["CoverageRow"];
export type QueueRow = S["QueueRow"];
export type QuarantineRow = S["QuarantineRow"];
export type WatchdogReport = S["WatchdogReport"];
export type WatchdogFindingOut = S["WatchdogFindingOut"];
export type SessionOut = S["SessionOut"];
export type PriceSeriesOut = S["PriceSeriesOut"];
export type CodeOut = S["CodeOut"];
export type SourceOut = S["SourceOut"];
export type EntityLink = S["EntityLink"];
export type FeedEventDetail = S["FeedEventDetail"];
export type PlannedSaleMatchOut = S["PlannedSaleMatchOut"];
export type HoldingOut = S["HoldingOut"];
export type TradeCopy = S["TradeCopy"];
export type FootnoteOut = S["FootnoteOut"];
export type DataCoverage = S["DataCoverage"];
export type DateSpan = S["DateSpan"];

/** Fields the loaders put in a feed event payload (see backend smt/ingest/ownership.py). */
export interface FeedPayload {
  accession: string;
  form: string;
  filed_on: string | null;
  accepted_at: string | null;
  url: string;
  ticker: string | null;
  issuer: { name: string; slug: string } | null;
  owner: { name: string; slug: string; kind: string; role: string };
  joint_owners?: number;
  code?: string | null;
  code_short?: string | null;
  code_label?: string;
  direction?: string;
  open_market?: boolean;
  derivative?: boolean;
  security_title?: string | null;
  shares?: string | null;
  price?: string | null;
  price_is_weighted_avg?: boolean;
  value?: string | null;
  market_value?: string | null;
  traded_on?: string | null;
  approx_sale_on?: string | null;
  delay_days?: number | null;
  exercise_and_sell?: boolean;
  owner_type?: string;
  is_10b5_1?: boolean | null;
  holdings?: number;
  no_securities_owned?: boolean | null;
  /** Filing typos flagged by the backend (see components/ui/TradeValue.tsx). */
  data_issues?: string[];
  /** The late-filing check for this trade (null when it can't be judged). */
  is_late?: boolean | null;
  deadline_rule?: string | null;
}
