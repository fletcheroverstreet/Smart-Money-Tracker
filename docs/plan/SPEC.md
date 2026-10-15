# Smart Money Tracker — build spec for Claude Code

> Saved verbatim from the user's request of 2026-09-23 so later sessions can read it.
> Where this spec and `PLAN.md` disagree, `PLAN.md` §14 explains the proposed change; the spec wins until the user approves it.

Build me a production-quality web app that tracks what "smart money" is buying and selling, using official public disclosures that refresh automatically. Two main sections:

- **People** — members of Congress, CEOs and other corporate insiders, and other power players.
- **Institutions** — hedge funds and investment banks (plus activists).

Every person and institution gets a profile that explains who they are and shows their full trading history over time. Every number links back to the filing it came from.

Working title: **Smart Money Tracker** (repo `smart-money-tracker`). If you want to propose a short brand name that fits the design concept in §9, give me three with a quick collision check. Don't rename without asking.

It must be GitHub-ready, deployable to my own .com domain, and keep itself up to date with no manual upkeep.

---

## 0. How to work with me

1. Read this whole file before doing anything.
2. Find my Python data-layer package **finlake** (ask me for its path if it isn't importable; if I've renamed it, use the new name). Read its public API. Reuse what it already solves (SEC request conventions, ticker↔CIK normalization, daily price bars) through a thin adapter. `SEC_USER_AGENT` is already set in my Windows user environment. **Do not modify finlake.** If it lacks something, write an ISSUE note and build the minimum inside this project. This app must still run on a fresh server where finlake's local data doesn't exist.
3. If my **lodestar** project has its curated `industry_map.csv` (SIC fallback), reuse it for sector/industry labels. No licensed GICS data.
4. Verify every source in §2 is still live (URLs, formats, rate limits) before designing around it. Popular community datasets have gone dark (§2.10), and the bill in §2.8 may have moved. Report anything that changed.
5. Write the build plan and **stop for my approval**: Mermaid architecture diagram, database schema, ingestion design per source, phase plan, risks, and anything in this spec you think is wrong. No feature code until I approve.
6. Build phase by phase (§14). End each phase with tests green, a click-through demo checklist, a short changelog, and a git commit. Wait for my go before the next phase.
7. Keep a `CLAUDE.md` in the repo root (commands, conventions, decisions) so later sessions pick up cleanly. Mirror decisions and known issues into my Obsidian vault at `Documents\FinanceVault\01-Projects\smart-money-tracker\` using the same structure as finlake: an index note, `Decisions/DEC-###`, `Log/YYYY-MM-DD`, `Assets/`, and `ISSUE-###` notes. If you can't reach that folder, write them to `docs/vault/` and tell me.
8. My setup: Windows, PowerShell, VS Code, Python 3.14. Give every command as PowerShell. My Python is intermediate and I don't know TypeScript, so write readable, typed, docstringed code, explain the finance and legal logic in comments, and skip clever one-liners.
9. **Never fabricate data.** No mock or sample rows outside test fixtures. If a source is down, the UI shows the last good data with its timestamp.
10. Ask before adding any paid service or deviating from this spec.

**Non-goals for v1:** user accounts, brokerage integration or trading, paid data by default, OCR of scanned or handwritten filings, non-US politicians, price targets or predictions.

---

## 1. The product

Top-level areas:

- **Live** (home) — a real-time feed of every new disclosure across all sources, plus a watchlist panel with live quotes.
- **People** — politicians, executives and other power players: directory, leaderboards, profiles.
- **Institutions** — hedge funds, investment banks, activists: holdings, quarter-over-quarter changes, profiles.
- **Tickers** — one page per stock showing all smart-money activity on a single price chart.
- **Signals** — screens that surface unusual activity (cluster buys, activist stakes, late disclosures…).
- **Watchlist** — follow people, funds and tickers; alerts.
- **Status & Methodology** — data freshness, coverage, formulas, caveats.

### What "live" means here — design around this

Disclosure law decides how fast anyone can know about a trade. The app must be as fast as the law allows and honest about the rest.

| Disclosure | Legal deadline | Our target once published |
|---|---|---|
| Form 4 (insiders, incl. funds owning >10%) | 2 business days after the trade | on screen < 2 min after EDGAR publishes |
| Schedule 13D (activists) | 5 business days; amendments 2 business days | < 2 min |
| Congress PTRs | up to 45 days after the trade | < 90 min after the House/Senate index shows it |
| 13F holdings | up to 45 days after quarter end (a snapshot, not trades) | < 2 min |
| Prices | real time (subject to data plan) | seconds |

Every trade row shows three timestamps — **traded**, **disclosed**, **detected by us** — plus the disclosure delay in days. Prices can be live; disclosures can't. Say this plainly in the UI.

### What public data can't tell you (Methodology page — never fake these)

Real-time hedge-fund trades, most short positions, the holdings of wealthy people who aren't insiders or officials, and the intraday timing of congressional trades. 13F shows long US-listed positions only, at quarter end.

---

## 2. Data sources

### 2.0 Catalog

| Source | What it reveals | Lag | Access | Phase |
|---|---|---|---|---|
| SEC Form 4 (+ Forms 3, 5) | Every trade by officers, directors and >10% owners — including funds | ≤ 2 business days | Free, EDGAR | 1 |
| SEC Form 144 | Affiliates' notice of intent to sell restricted/control stock | filed at sale order | Free, EDGAR | 1 |
| House PTRs | Member / spouse / dependent trades | ≤ 45 days | Free, House Clerk | 2 |
| Senate PTRs | Same | ≤ 45 days | Free, Senate eFD | 2 |
| congress-legislators | Who they are, terms, IDs, committees | daily | Free, GitHub | 2 |
| 13F-HR | Quarter-end long holdings of managers with >$100M in 13(f) securities | ≤ 45 days after quarter | Free, EDGAR + SEC bulk data sets | 3 |
| Schedule 13D / 13G | >5% stakes; activist intent (13D Item 4) | 5 / 2 business days | Free, EDGAR (XML since Dec 18 2024) | 3 |
| OpenFIGI | CUSIP → ticker | on demand | Free API key | 3 |
| Prices | Returns, charts, live quotes | daily / real time | finlake, yfinance, Finnhub; paid optional | 1 |
| Form ADV | Adviser AUM, HQ, ownership | annual + amendments | Free, SEC | 5 |
| Congress.gov API | Sponsored bills, leadership roles | near real time | Free api.data.gov key | 5 |
| USAspending API | Federal contract awards by company | daily | Free | 5 |
| Lobbying Disclosure Act API | Who lobbies on which issues and bills | quarterly | Free | 5 |
| FEC API | PAC contributions to members | varies | Free key | stretch |
| OGE Form 278-T | Executive-branch officials' trades | ~30–45 days | Free PDFs | stretch |
| Congressional annual disclosures | Holdings snapshots, asset ranges | annual | Free | stretch |
| FINRA short interest, daily short-sale volume | Short-pressure context on ticker pages | twice monthly / daily | Free | stretch |
| N-PORT, active-ETF daily holdings | Fund holdings | quarterly / daily | Free | stretch |
| Options flow, dark-pool prints | "Unusual" activity | real time | **Paid only** | adapter only, ask me |
| H.R. 7008 intent-to-sell notices | Member sales announced 7–14 days *before* they happen | forward-looking | only if enacted | see §2.8 |

### 2.1 SEC EDGAR — shared plumbing

- **Fair access:** ≤ 10 requests/second total, descriptive User-Agent with contact email (read `SEC_USER_AGENT`). One shared async client with a global token-bucket limiter around 8 req/s, jittered backoff on 403/429/5xx, gzip, and an on-disk raw-document cache.
- **Hours:** EDGAR accepts filings 06:00–22:00 ET on business days. Poll the latest-filings Atom feed every 60 s in that window and every 10 min outside it:
  `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=<FORM>&owner=include&count=100&output=atom`
  Page when a poll returns 100 new entries. The type filter can match by prefix, so filter exact form types yourself. The same filing can appear once per filer role (issuer and reporting owner) — dedupe by accession number.
- **Form types:** 3, 4, 4/A, 5, 144, 13F-HR, 13F-HR/A, 13F-NT, and Schedule 13D/13G. Handle both the legacy `SC 13D` / `SC 13G` names and the structured-XML form types used since Dec 18 2024 (check EDGAR's current form list for exact names).
- **Documents:** read each filing's `index.json` to find the primary XML. Never guess filenames.
- **Reconciliation:** nightly, compare against the EDGAR daily index (`/Archives/edgar/daily-index/`) and report anything the poller missed.
- **Reference:** `https://www.sec.gov/files/company_tickers.json` (ticker↔CIK) and `https://data.sec.gov/submissions/CIK##########.json` (filer history, former names, SIC).
- **Backfill:** use the SEC's bulk flat files instead of crawling millions of filings — the *Insider Transactions Data Sets* (Forms 3/4/5, quarterly) and the *Form 13F Data Sets* (quarterly since 2013, roughly 80 MB zipped each). Make backfills resumable with progress output.

### 2.2 Corporate insiders — Forms 3/4/5 and 144

Parse the `ownershipDocument` XML: reporting owner(s) (CIK, name, isDirector, isOfficer, officerTitle, isTenPercentOwner, isOther), issuer (CIK, ticker), non-derivative and derivative tables, transaction code, shares, price, acquired/disposed, shares owned after, direct/indirect and nature of ownership, footnotes, and the Rule 10b5-1 checkbox (added in 2023 — check the schema for the element name).

Gotchas to handle and test:

- **Codes:** P (open-market purchase) and S (open-market sale) carry the signal. A, M, F, G, C, X and the rest are compensation or mechanics. Store everything; default views show P and S with a toggle for all. Label same-day M + S as "exercise and sell", not bearish selling.
- **Joint filings** list several reporting owners (e.g. a fund, its GP and its manager). Attribute the trade once; never double count.
- **4/A** supersedes the original. Filings after the 2-business-day deadline get a late flag.
- Prices can be weighted averages with ranges in footnotes. Keep footnotes visible.
- **>10% owners are often funds** — link them to their Institution profile. This is the fastest public signal of what a fund is doing.
- Form 3 marks when someone became an insider (use it for tenure). Reconstruct holdings over time from `sharesOwnedFollowingTransaction`, per ownership form.
- **Form 144:** show as "intends to sell ~$X", and link it to the Form 4 that later executes it, if one appears.

### 2.3 Congress — House

- **Index:** yearly ZIP `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{YEAR}FD.ZIP` containing a tab-delimited text file and an XML index of every filing (name, state/district, filing type, date, DocID). Filing type `P` = periodic transaction report. Poll hourly with conditional GET.
- **Documents:** `https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{YEAR}/{DocID}.pdf`. Electronically filed PTRs have extractable text (pdfplumber). Scanned or handwritten ones: don't OCR in v1 — list them as "not machine-readable — view original" and count them in the coverage report.
- **Fields:** owner code (SP spouse, JT joint, DC dependent child, blank = self), asset name, ticker (often in parentheses), asset-type code in brackets ([ST] stock, [OP] option, etc.), transaction type (P, S, S (partial), E exchange), transaction and notification dates, amount bracket, and description text (option details live here).
- **Amounts are brackets** ($1,001–$15,000 … "Over $50,000,000", plus the spouse/dependent "over $1,000,000" category). Store low and high. Never show a single dollar figure unless it's labeled "midpoint estimate".
- Tickers are sometimes missing or wrong: fall back to fuzzy matching the asset name against `company_tickers.json`, store a match confidence, and keep the raw text.

### 2.4 Congress — Senate

- `https://efdsearch.senate.gov` requires acknowledging the terms before searching (session + CSRF token). Try plain HTTP first; use Playwright only if you must. Search PTRs by date range, every 30–60 min.
- Electronic reports are HTML tables at `/search/view/ptr/<id>/` (transaction date, owner, ticker, asset name, asset type, type — Purchase / Sale (Full) / Sale (Partial) / Exchange — amount, comment). Paper reports (`/search/view/paper/...`) are images: flag them as not machine-readable.
- Amendments supersede originals.
- Federal law bars using congressional disclosure reports for commercial purposes (news media excepted), for credit ratings, or for fundraising — the eFD makes you acknowledge this. This project stays non-commercial; say so in the README.

### 2.5 People reference and enrichment

- **unitedstates/congress-legislators** (JSON on the project's GitHub Pages: `legislators-current`, `legislators-historical`, `committees-current`, `committee-membership-current`): terms, party, state/district, and IDs (bioguide, FEC, OpenSecrets, Wikipedia, Wikidata). Match House/Senate filer names to bioguide IDs using name + state + district + filing date. Keep a manual `config/overrides/member_matches.yaml` for ambiguous cases and log every unmatched name.
- **Portraits:** unitedstates/images (public-domain official portraits keyed by bioguide ID). For everyone else use an initials avatar — don't scrape photos.
- **Wikipedia/Wikidata:** only when an identifier links the person (e.g. the Wikidata ID in congress-legislators, or a manual mapping). Never match by name alone. Attribute (CC BY-SA) and link.
- **Congress.gov API** and **Form ADV** arrive in Phase 5.
- Show only public-role facts. Don't display personal bio fields unrelated to the office (e.g. religion) even if a dataset includes them.

### 2.6 Institutions — 13F and 13D/13G

**13F**

- Parse the information table: issuer, class, CUSIP, value, shares or principal (SH/PRN), put/call, investment discretion, voting authority.
- **Units:** filings made from January 2023 onward report value in whole dollars; earlier filings report thousands. Confirm the exact cutoff against the SEC's 13F guidance, normalize, and test both sides.
- **Amendments:** 13F-HR/A is either a RESTATEMENT (replaces) or NEW HOLDINGS (adds to) — read the amendment type on the cover page. 13F-NT means another manager reports the holdings; follow "other managers" so parents and subsidiaries aren't double counted.
- **Options:** put/call rows reflect the underlying's value. Keep them separate; never add them into long exposure.
- Confidential treatment can omit positions — mark such filings "may be incomplete".
- **CUSIP → ticker:** OpenFIGI mapping API with a free key (25 requests per 6 s, 100 jobs per request with a key). Cache permanently. Unmapped CUSIPs stay visible with the issuer name.
- **Per filer per quarter:** weights, action (new / add / trim / exit / unchanged), share and value deltas (split-aware), concentration (top-10 weight, HHI), turnover, sector mix, holding period per position.
- **Banks:** their 13Fs mostly reflect client, wealth-management and market-making positions, not directional bets. Label the Banks tab accordingly and exclude banks from smart-money consensus by default.
- **Bulk:** process the 13F data sets with DuckDB. Load full holdings only for tracked filers into Postgres, plus per-security aggregates across *all* filers per quarter (holder count, net shares bought/sold, new holders).

**13D / 13G**

- 13D = >5% with intent to influence (activists); 13G = passive. Parse the structured XML: filer, issuer, shares, percent of class, and for 13D the Item 4 "purpose of transaction" text. Flag 13G→13D switches and stake increases.

### 2.7 Prices

A `PriceProvider` interface with swappable implementations:

- finlake's price store, if it can serve arbitrary tickers.
- yfinance — free but unofficial; isolate it, cache aggressively, and fail gracefully when it breaks.
- Optional paid providers behind a flag: Massive (formerly Polygon.io; its free tier is end-of-day at 5 calls/min), Tiingo, Financial Modeling Prep.
- Live quotes: Finnhub's free tier (60 REST calls/min, one WebSocket connection with real-time IEX trades) for watchlist and on-screen tickers only.

Daily bars after the close for every referenced ticker. Use adjusted closes for returns. Keep delisted tickers and mark their prices unavailable — never drop them (survivorship bias).

### 2.8 Pending law to design for

**H.R. 7008, the Stop Insider Trading Act**, passed the House on July 22, 2026 (232–198) and was placed on the Senate calendar in August; as of late September 2026 it had not passed the Senate. If enacted, members, spouses and dependent children could no longer buy covered stocks and would have to file public notice 7–14 days before each sale, published online by the House Clerk or Senate Secretary. Design the Congress module so "intent-to-sell notices" can be added as a new source with a forward-looking "scheduled sale" signal. Check the bill's status when you start.

### 2.9 Overlays (Phase 5+)

- **USAspending:** federal contract awards → map recipients to tickers (parent matching plus a curated mapping file) → show awards on ticker pages and flag members who traded a stock around a large award. Keep award ingestion reusable; I plan a defense-contract tracker later.
- **Lobbying Disclosure Act REST API** (served from lda.gov / lda.senate.gov — use whichever is current): which companies lobby on which issues and bills.
- **Stretch:** FEC PAC contributions, OGE 278-T PDFs, congressional annual disclosures (to anchor estimated positions), FINRA short data, N-PORT / active-ETF holdings.
- **Options flow / dark pools:** paid only. Design the adapter interface; don't implement without my OK.

### 2.10 Don't use

- House Stock Watcher / Senate Stock Watcher datasets — dead (their S3 buckets return 403). Go to the House Clerk and Senate eFD directly.
- Scraping aggregators whose terms forbid it (Capitol Trades, OpenInsider, etc.). Fine for my manual spot-checks, not as sources.
- Paid congressional APIs (Quiver, FMP, EODHD, Unusual Whales) — optional cross-check adapters only, behind a flag, after asking me.

---

## 3. Architecture and stack

```
smart-money-tracker/
  backend/   Python: FastAPI API, ingestion worker, parsers, analytics
  web/       Next.js front end
  config/    YAML: tracked funds, notables, signal thresholds, committee→industry map, overrides
  infra/     docker-compose, Caddyfile, backup scripts
  scripts/   PowerShell: dev.ps1, backfill.ps1, reprocess.ps1
  docs/      architecture, methodology, data sources, deploy guide, screenshots
```

- **Services (Docker Compose):** `db` (PostgreSQL with pg_trgm), `api` (FastAPI/Uvicorn), `worker` (one async ingestion + scheduler process), `web` (Next.js standalone), `caddy` (reverse proxy; automatic HTTPS in production).
- **Backend:** Python 3.14 in the container if every dependency supports it — otherwise 3.13, and tell me (don't silently downgrade). FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, psycopg 3 or asyncpg, httpx (async), lxml, selectolax or BeautifulSoup, pdfplumber, DuckDB (bulk jobs only), APScheduler or a small asyncio scheduler, structlog, Apprise (alerts), pytest + respx, ruff + mypy. Pin versions.
- **No Redis.** Postgres LISTEN/NOTIFY carries live events to an SSE endpoint. Add Redis only if you can show a need.
- **Front end:** Next.js (App Router) + strict TypeScript + Tailwind; TanStack Query (server state); TanStack Table + TanStack Virtual (large tables); TradingView Lightweight Charts for price charts with buy/sell markers (keep the attribution its license requires); Apache ECharts (canvas) for other charts — no SVG chart libraries for large series; cmdk command palette; Radix primitives for accessible menus and dialogs; next/font for self-hosted fonts.
- If you believe a Python-only front end (FastAPI + HTMX) would hit the same UX bar with less complexity, argue it in the plan. Otherwise use the stack above.
- **Raw first, parse second:** store every fetched document (compressed) with URL, hash and fetch time *before* parsing. Parsers are versioned, and `reprocess` re-parses from raw when a parser improves. This is how the app survives format changes.
- Config in `.env` (with a complete `.env.example`); tunables in versioned YAML under `config/`.
- `.gitattributes` enforcing LF for shell scripts and Dockerfiles — Windows CRLF breaks them inside Linux containers.

---

## 4. Data model (starting point — refine in the plan)

- `entities` — kind (person, politician, fund, bank, company), name, slug, summary, photo, links.
- `entity_ids` — (entity, scheme, value): sec_cik, bioguide, fec, wikidata, lda, crd…
- `roles` — entity ↔ organization, role, title, start/end, source (CEO of X from Forms 3/4; senator from congress-legislators; committee seats).
- `entity_links` — e.g. manager → fund.
- `securities` — ticker history, issuer CIK, CUSIPs, FIGI, name, sector/industry, active flag.
- `raw_documents` — source, URL, fetched_at, hash, storage path, parse status, parser version.
- `filings` — source, form type, accession number or DocID, filer, filed/accepted time, period of report, URL, amendment links.
- `transactions` — one unified trade table for Form 4, PTRs and later 278-T: entity, security (nullable), raw asset text, direction, code, shares, price, value **or** value_low/value_high, owner (self/spouse/joint/dependent/indirect), is_10b5_1, is_derivative, traded_on, disclosed_at, detected_at, delay_days, late flag, supersedes / superseded_by, dedupe key.
- `holdings_13f`, `holdings_13f_changes` (materialized per filer-quarter), `ownership_13dg`, `planned_sales` (Form 144; later H.R. 7008 notices).
- `prices_daily`; live quotes stay in memory.
- `signals` — type, security, entities, score, detected_at, inputs (jsonb), plain-English explanation.
- `committees`, `committee_members`; later `contracts`, `lobbying_filings`.
- `ingestion_runs` — per source: start/end, status, new rows, parse failures, max lag.

Idempotent upserts on natural keys (accession number + table + line; DocID + row). Ingesting the same document twice must never create duplicates.

---

## 5. Live pipeline and freshness

| Job | Cadence | Freshness target |
|---|---|---|
| EDGAR latest filings (3/4/5/144/13D/13G/13F) | 60 s, 06:00–22:00 ET weekdays; 10 min otherwise | on screen < 2 min after EDGAR publishes |
| EDGAR daily-index reconciliation | nightly | zero unexplained misses |
| House yearly ZIP index | hourly, conditional GET | < 90 min after it updates |
| Senate eFD search | every 30–60 min | < 90 min |
| Daily prices | after the close | same evening |
| Live quotes | stream, or 60 s poll for on-screen tickers | seconds |
| congress-legislators | daily | 1 day |
| Aggregates and signals | incremental on each new event + nightly full recompute | minutes |

- The worker emits each new or updated record via NOTIFY; the API streams it at `/api/stream` (typed events, 15 s heartbeat). Configure Caddy not to buffer SSE.
- Front end: EventSource with auto-reconnect and backoff; batch incoming events (~1 s) so streams never cause re-render storms; invalidate the right TanStack Query caches; fall back to polling if SSE drops; pause while the tab is hidden and resync on return. Show connection state and "updated 12 s ago".
- **Watchdog:** every source has an SLA. If a source goes stale, or a run's parse-failure rate spikes (usually a format change), alert me and show it on `/status`.

---

## 6. Analytics and signals

**Performance** (per trade, per person, per fund):

- Returns at +30 / +90 / +180 / +365 days, measured two ways: from the **trade date** (their edge) and from the **first close after disclosure** (what a follower could actually get). Filings accepted after the close count from the next trading day. Show excess return vs SPY, and vs the sector ETF where mapped.
- For sells, a subsequent decline counts as a good call.
- Congress brackets: report both equal-weighted and bracket-midpoint-weighted results, always labeled.
- Funds: a "13F clone" that rebalances on the filing date (not quarter end) vs SPY, plus hit rate on new positions.
- Leaderboards require a minimum sample and always show n. Nobody gets crowned on three trades.

**Signals** — thresholds live in `config/signals.yaml`; every signal shows its inputs and links to its source filings:

- **Cluster buy:** ≥ 3 distinct insiders making open-market purchases in the same company within 14 days.
- **Executive conviction buy:** CEO or CFO open-market purchase ≥ $100k, or ≥ 10% increase in direct holdings.
- **First purchase in 2+ years** by that insider at that company.
- **Buy after drawdown:** purchase ≥ 25% below the 52-week high.
- **Discretionary selling cluster:** several officers selling outside 10b5-1 plans within 30 days.
- **Late disclosure:** PTR filed > 45 days after the trade; Form 4 past its 2-business-day deadline.
- **Committee overlap:** a member trades a company in an industry their committee oversees (curated committee→industries map in config, visible to users).
- **Activist stake:** new 13D, a 13G→13D switch, or a 13D stake increase.
- **Fund 10%-owner trade:** a Form 4 filed by a tracked fund.
- **Consensus add / crowded exit:** ≥ 5 tracked hedge funds opening or adding ≥ 20% (or exiting) in the same quarter.
- **Contract-timing overlap** (Phase 5): a member traded within 30 days of a large federal award to that company.
- **Smart Money Score** per ticker (0–100): a transparent weighted blend of recent signals with time decay. The ticker page shows the full breakdown. Label it a research heuristic, not advice.
- **Event studies:** for each signal type, the historical average excess return after it fired, with n and a confidence interval. Each signal carries its own measured track record, and the UI explains each one in a single plain sentence.

---

## 7. Profiles — "who is this?"

Every entity page opens with a factual header and a plain-language summary **built only from stored data**:

- **Politicians:** office, party, state/district, years in office, committees and leadership roles, official portrait. Trading profile from their filings: number of trades, share via spouse or joint accounts, most-traded sectors, typical bracket, median disclosure delay, late filings.
- **Insiders:** current and past roles by company (from Forms 3/4), insider since (first Form 3), board seats elsewhere, holdings over time, share of sales under 10b5-1 plans.
- **Funds and banks:** managers, HQ, regulatory AUM (Form ADV), 13F portfolio value and position count, concentration, turnover, derived style (concentrated vs diversified, average holding period, sector and size tilts), activist history (13Ds).
- Summary templates, e.g. *"{name} is a {party} senator from {state}, in office since {year}, and sits on {committees}. {n} trades disclosed since {year}; {pct}% were in a spouse or joint account."*
- Wikipedia extract only with an identifier-based match, attributed.
- **Optional (Phase 5):** if `ANTHROPIC_API_KEY` is set, polish summaries with Claude (model from env, default `claude-haiku-4-5-20251001`) using only the facts passed in. Store with the input hash, label it "AI-written from the data above", and forbid motives, allegations, or any fact not in the input.
- Every entity has a chronological **timeline** (roles, filings, trades, holdings changes) and a **positions-over-time** chart.

**Tracked lists** (YAML I can edit):

- `config/notables.yaml` — power players beyond Congress (mega-cap founders and CEOs, famous investors, executive-branch officials once 278-T exists), each resolved to identifiers.
- `config/funds.yaml` — resolve each name below to its 13F filer CIK via EDGAR (don't trust CIKs from memory), confirm it filed a 13F-HR in the last two quarters, and **show me the resolved list before ingesting**:
  - *Hedge funds and famous managers:* Berkshire Hathaway, Pershing Square, Appaloosa, Duquesne Family Office, Third Point, Greenlight, Baupost, Elliott, Icahn, Soros Fund Management, Tiger Global, Coatue, Viking Global, Lone Pine, Bridgewater, Renaissance Technologies, Citadel Advisors, Millennium, Two Sigma, D. E. Shaw, Point72, AQR, Himalaya Capital, Starboard Value, ValueAct, Trian, Jana Partners, Oaktree, ARK Investment Management, Gates Foundation Trust.
  - *Investment banks:* Goldman Sachs, Morgan Stanley, JPMorgan Chase, Bank of America, Citigroup, Wells Fargo, UBS, Barclays, Deutsche Bank, Jefferies.
  - *Index giants (context only, excluded from smart-money aggregates):* BlackRock, Vanguard, State Street, FMR.
- Every member of Congress and every Form 4 filer is ingested automatically. "Tracked" only means featured.

---

## 8. Pages and UX

**Global:** left rail (Live, People, Institutions, Tickers, Signals, Watchlist, Status); Ctrl+K command palette across people, funds, tickers and committees; URL-driven filters so any view is a shareable link; sticky filter bar; a quick-look drawer for any trade with a link to the original filing; keyboard navigation in tables.

- **Live:** the feed of new disclosures across all sources, newest first, filterable by source, section and size. A "today" strip: largest insider buys, new activist stakes, new congressional trades, 13F filing-season progress. Watchlist panel with live quotes.
- **People:** tabs for Congress (House / Senate), Insiders, Notables, and later Executive branch. Directory filters: chamber, party, state, committee, sector traded, date range, bracket, owner type, role, trade size. Leaderboards (most active, largest buyers, best follower returns — with n).
- **Person page:** header and summary; key stats; trades table; timeline; positions over time; performance vs SPY (both measures); committees; signals involving them; source filings.
- **Institutions:** tabs for Hedge funds, Banks (labeled as flows), Activists (13D), Index giants (context). Quarter selector. This quarter's most bought, most sold, new positions, exits and crowding. A 13F filing-season tracker showing who has filed and who hasn't.
- **Fund page:** header and summary; holdings table (weight, value, shares, QoQ action, holding period); holdings treemap; QoQ changes; sector mix over time; clone performance; 13D/13G history; Form 4s as a 10% owner; filing history.
- **Ticker page:** price chart with toggleable marker layers (insider buys/sells, congressional trades, 13D/13G events, quarterly 13F net flows); Smart Money Score with breakdown; insider table; congressional table; institutional holders (latest quarter + change); planned sales (Form 144); later, contracts and lobbying.
- **Signals:** screener across all signal types with filters and URL-saved screens.
- **Watchlist:** follow people, funds and tickers, stored in the browser in v1 (no accounts). My own alerts go out through Apprise (Discord / Telegram / email), configured server-side.
- **Status:** per-source freshness, last run, rows, parse coverage, reconciliation misses, quarantined rows. **Methodology:** lags, caveats, formulas, what's unknowable. **Sources:** links to everything.

---

## 9. Visual design

My brief: **dark, retro, somewhat space-themed, but still professional** — this is a financial research tool first. My ranking engine (lodestar) uses a 1970s mission-control look with phosphor amber and oscilloscope green; this one should feel like a sibling, not a copy.

**Concept to develop:** a deep-space tracking station. People and institutions are objects under track; filings are telemetry arriving on a downlink; the disclosure lag is the signal delay. Spend the boldness in one place — the Live feed — and keep everything else quiet, precise and dense.

**Starting palette** (propose refinements):

- Night `#0D1526` — background (deep navy, not near-black)
- Console `#152038` — panels; Hairline `#25324F` — rules and borders
- Paper `#E9E4D4` — primary text (warm, like aged mission documents); Dim `#8A97B2` — secondary text
- Inflow `#4FC3B8` (buys) / Outflow `#F08A4B` (sells) — a colorblind-safe pair; never encode buy/sell by color alone (always a sign, glyph or label too)
- Beacon `#F4CE5A` — alerts only, used sparingly

**Type:** a DIN-style condensed face for page titles and large readouts (e.g. Barlow Condensed), a clean engineering sans for body and tables (e.g. IBM Plex Sans) with tabular figures wherever numbers stack, and monospace only for true identifiers (CIKs, accession numbers). No sci-fi display fonts.

**Avoid** (these read as generic, template-made UI): all-caps eyebrow labels above headings, "A · B · C" metadata strings, arrows appended to link text, identical rounded cards with the same soft shadow, gradient washes, one highlighted word in a headline, emoji, scrolling ticker marquees, fade-up animations on every section. Don't color-code by party — show party as text.

**Motion:** one orchestrated moment — new filings arriving in the Live feed. Everything else moves only in response to the user. Respect `prefers-reduced-motion`.

**Copy:** plain, sentence case, written from the reader's side ("Filed 38 days after the trade", "Spouse's account"). Explain codes on hover (P = open-market purchase). Empty and error states say what happened and what to do next.

**Process:** before any UI code, give me a token sheet (colors, type scale, spacing, radii that vary by hierarchy), ASCII wireframes for Live, Person, Fund and Ticker, and a note on what you changed from this brief and why. **Stop for approval.** While building, use Playwright to screenshot key pages at 1440 px and 390 px and critique them before calling a page done.

**Quality floor:** responsive down to phone width, visible keyboard focus, WCAG AA contrast, proper table semantics (`aria-sort`), skeleton loaders instead of spinners for tables, no layout shift, works in current Chrome, Edge, Firefox and Safari (incl. iOS).

---

## 10. Performance — "runs smooth"

- Tables: server-side filtering and sorting with keyset pagination plus virtualized rendering. A 50k-row view scrolls without jank.
- API: p95 < 200 ms for list endpoints on the production VPS. Indexes for every filter, materialized views for heavy aggregates, ETag/Cache-Control on read endpoints, compression.
- Front end: server components for first paint, code-split charts, prefetch on hover. LCP < 2.5 s, CLS ≈ 0, Lighthouse ≥ 90 for performance and accessibility on Live, Person, Fund and Ticker.
- The live stream never degrades typing or scrolling.
- Load-test the API (e.g. 50 concurrent users) before deploying.

---

## 11. Accuracy and verification

I verify my tools by tying numbers back to source filings by hand. Build that into the app:

- **Golden-file tests** with real documents saved as fixtures and exact expected output: a Form 4 with footnotes, joint filers, derivative rows and the 10b5-1 box; a 4/A; a House PTR PDF with spouse, option and partial-sale rows; a Senate electronic PTR; a Senate paper PTR (must be flagged, not parsed); a pre-2023 13F (thousands) and a post-2023 13F (dollars); a 13F-HR/A restatement and a new-holdings amendment; a 13D with Item 4 text; a 13G→13D switch.
- Idempotency tests (ingest twice → identical counts), amendment supersession, business-day deadline math, bracket parsing, CUSIP-cache behavior.
- **Spot-check page** (`/status/spot-check`): 20 random recent rows per source beside a link to the original document, so I can verify by eye.
- **Coverage report** per source: documents found vs parsed vs not machine-readable vs failed, with reasons.
- **Reconciliation:** daily counts vs the EDGAR daily index and the House/Senate indexes.
- Rows that fail validation are quarantined and shown on `/status` — never silently dropped.

---

## 12. Neutrality, legal, disclaimers

- Facts, not accusations. Use "timing overlap" and "late disclosure" — never "insider trading" or implied wrongdoing. Identical rules for every party.
- Footer and Methodology: "Research tool, not investment advice." Explain lags; link sources.
- SEC fair-access compliance; Senate eFD terms; congressional disclosure data stays non-commercial (§2.4).
- Wikipedia text: CC BY-SA attribution. Lightweight Charts: keep the required attribution.
- Public-role facts only: no home addresses, no personal details beyond what a filing's owner field states.

---

## 13. Deployment and upkeep

- **Local:** Docker Desktop (WSL2) + `docker compose up` brings everything up with a small recent backfill. `scripts\backfill.ps1` runs full history — resumable, with progress.
- **Production:** one small VPS (2 vCPU / 4 GB to start) running the same compose file. Build images in CI and push to GitHub Container Registry so the server never compiles anything. Caddy provides automatic HTTPS on my .com domain; `docs/deploy.md` walks me through DNS step by step.
- **No manual upkeep:** restart policies, healthchecks, log rotation, nightly `pg_dump` with retention, staleness alerts, an external uptime check, and a short guide to unattended security updates.
- **Security:** the public API is read-only and rate-limited; admin actions (backfill, reprocess) are bound to localhost or require an admin token. Secrets only in `.env` on the server.
- **GitHub:** Actions CI runs lint, type-check, tests and image builds on every push, plus a manually triggered deploy workflow (SSH pull + restart). README covers what it is, screenshots/GIF, architecture diagram, data sources with lags, PowerShell setup, deploy, methodology summary, limitations, license (MIT for code) and the disclaimer.
- Before calling any phase done, confirm the code is actually on the remote (`git status`, `git log origin/main`). My last project's repo ended up holding only `.gitattributes`.

---

## 14. Phases

Each phase ends with tests green, a demo checklist, a changelog, a commit, and my approval.

**Phase 0 — Plan and skeleton.** Plan approved; `docker compose up` serves a placeholder page and `/healthz`; CI green; design token sheet and wireframes approved.

**Phase 1 — People, part 1: insiders + Live.** EDGAR client, Forms 3/4/5/144 live plus a 2-year backfill (full history optional), prices, Live page, insider profiles, basic ticker page.
*Done when:* a Form 4 appears on Live within 2 minutes of EDGAR publishing it during filing hours; 20 random rows tie out on the spot-check page; a 50k-row insider table scrolls smoothly.

**Phase 2 — People, part 2: Congress.** House and Senate PTRs back to 2012 (or as far as sources allow), member matching, committees, politician profiles, leaderboards with n, late-disclosure and committee-overlap flags, coverage report.
*Done when:* every electronic PTR from the last 12 months is parsed or explained in the coverage report, and 20 random rows tie out.

**Phase 3 — Institutions.** Fund resolution (show me first), 13F live + backfill since 2013 for tracked funds + all-filer aggregates, QoQ changes, amendments, CUSIP mapping, fund profiles, labeled Banks tab, 13D/13G feed, 10%-owner Form 4 linking, filing-season tracker.
*Done when:* QoQ changes for Berkshire, Pershing Square and one bank match my manual check of their filings, and values are consistent across the 2023 units change.

**Phase 4 — Analytics and signals.** Performance (both measures), leaderboards, every signal in §6, Smart Money Score with breakdown, event studies, screener, watchlist and alerts.
*Done when:* every signal and score shows its inputs and source links, and event studies report n and confidence intervals.

**Phase 5 — Profiles and overlays.** Summaries, Wikipedia/Wikidata, Form ADV, Congress.gov, USAspending, LDA lobbying; optional AI summaries; optional 278-T.
*Done when:* every tracked entity has a summary built only from stored facts.

**Phase 6 — Polish and launch.** Screenshot-driven design pass, Lighthouse targets, load test, deploy to VPS + domain, README with real screenshots.
*Done when:* the site is live on my domain and has refreshed itself for 7 straight days with `/status` all green and no manual intervention.

---

## 15. Done when (the whole project)

I open my domain and within a minute can see what a CEO bought today, what a senator's spouse sold last month and how long it took to disclose, what Berkshire and Pershing Square changed last quarter, and every smart-money event on one stock's chart — with each number one click away from the filing it came from.
