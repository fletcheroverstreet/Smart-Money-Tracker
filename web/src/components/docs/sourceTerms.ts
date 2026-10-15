/**
 * Terms of use for each data source, and the price-provider terms that keep
 * market prices off the public site.
 *
 * GET /api/meta/sources doesn't carry terms yet, so they live here, keyed by
 * the source's `key`. Every statement comes from the live checks recorded in
 * docs/plan/source-verification.md (2026-09-23) and PLAN.md §1 (decisions 1
 * and 2). Don't add a claim here without evidence there.
 */

export interface Terms {
  /** Plain-English summary of the rules that apply. */
  summary: string;
  /** Where the rules are published. */
  links: { label: string; href: string }[];
}

const SEC_FAIR_ACCESS: Terms["links"] = [
  { label: "SEC: accessing EDGAR data", href: "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data" },
  { label: "SEC: privacy and information security", href: "https://www.sec.gov/about/privacy-information" },
];

const SEC_SUMMARY =
  "Official filings, published free by the SEC. Automated access must follow the SEC’s fair-access rules: " +
  "say who you are in every request and stay under 10 requests per second in total.";

// 5 U.S.C. app. §105(c), printed on both chambers' disclosure sites (source-verification.md, house-clerk and senate-efd).
const CONGRESS_SUMMARY =
  "By law (5 U.S.C. app. §105(c)) these reports may not be used for any commercial purpose other than news " +
  "reporting to the general public, to set anyone’s credit rating, or to ask for money. Civil penalties go up to $10,000.";

export const SOURCE_TERMS: Record<string, Terms> = {
  edgar_form4: { summary: SEC_SUMMARY, links: SEC_FAIR_ACCESS },
  edgar_form144: { summary: SEC_SUMMARY, links: SEC_FAIR_ACCESS },
  edgar_13f: { summary: SEC_SUMMARY, links: SEC_FAIR_ACCESS },
  edgar_13dg: { summary: SEC_SUMMARY, links: SEC_FAIR_ACCESS },
  sec_tickers: {
    summary: `${SEC_SUMMARY} The SEC says it does not guarantee this file’s accuracy or scope.`,
    links: SEC_FAIR_ACCESS,
  },
  house_ptr: {
    summary: CONGRESS_SUMMARY,
    links: [{ label: "House Clerk: financial disclosure", href: "https://disclosures-clerk.house.gov/FinancialDisclosure" }],
  },
  senate_ptr: {
    summary: `${CONGRESS_SUMMARY} The Senate site also asks every visitor to accept an agreement before searching; this site hasn’t accepted it.`,
    links: [{ label: "Senate eFD search", href: "https://efdsearch.senate.gov/search/" }],
  },
};

export interface PriceProvider {
  name: string;
  /** What its terms (or its site) say, in plain words. */
  finding: string;
  /** The page the finding comes from, if there is one worth linking. */
  href?: string;
}

/** Every free price source checked on 2026-09-23 (source-verification.md, "prices-and-quotes"). */
export const PRICE_PROVIDERS: PriceProvider[] = [
  {
    name: "Yahoo Finance (through yfinance)",
    finding:
      "Yahoo’s terms forbid commercial use, automated collection without permission, and publicly displaying or redistributing its data. yfinance itself says Yahoo’s data is for personal use only.",
    href: "https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html",
  },
  {
    name: "Finnhub (free plan)",
    finding:
      "Personal use only. Its data, and results derived from it, may not be shared with anyone else without written approval.",
    href: "https://finnhub.io/terms-of-service",
  },
  {
    name: "Massive, formerly Polygon.io (free plan)",
    finding: "For personal, non-commercial and non-business use only.",
    href: "https://massive.com/legal/individuals-terms-of-service",
  },
  {
    name: "Tiingo (free plan)",
    finding: "Internal use only. Redistribution needs special permission and extra fees.",
    href: "https://app.tiingo.com/tos/",
  },
  {
    name: "Financial Modeling Prep",
    finding: "Displaying or redistributing its data needs a separate display and licensing agreement.",
    href: "https://site.financialmodelingprep.com/pricing-plans",
  },
  {
    name: "Alpha Vantage (free plan)",
    finding: "Personal, non-commercial use only. Letting other people see the data counts as commercial use.",
    href: "https://www.alphavantage.co/terms_of_service/",
  },
  {
    name: "Stooq",
    finding: "Its download now answers scripts with a browser check. Getting around that would mean defeating bot protection, so it isn’t used.",
  },
];
