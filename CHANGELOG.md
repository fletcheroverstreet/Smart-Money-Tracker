# Changelog

## [Unreleased]

## Phase 1 sign-off (2026-10-14)

- Independent sign-off review: product and frontend 9.5/10 (round 7: data
  and backend 9/10).
- **Light theme** at the owner's request: white page, light grey panels and
  tables, dark text; the side menu, the phone's bottom bar and its More sheet
  stay navy. Every text colour passes WCAG AA on its surface; the alert colour
  is darker than the sell colour so colour-blind readers can tell them apart;
  open warnings on /status get a coloured left edge. Design sheet §2 lists the
  palette with its ratios.

## Phase 1 hardening (2026-09-25 to 10-13)

Seven rounds of independent review (4/10, 7/10, 7/10, 8/10, then 9/10 for
product and frontend in rounds 5 and 6, and 9/10 for data and backend in
round 7) found real problems in the data, the pipeline and the pages. Fixed:

- **Coverage:** every day is compared with the SEC's daily index and counts
  as loaded at 99% of its filings; days short of that are filled
  automatically (every 10 minutes), and pages say plainly when a window
  crosses days not loaded (charts shade them).
- **Live detection:** Form 4 polled every 20 s; a poll processes only the
  filings it found (backfill work once delayed new filings by 10-28 minutes);
  paging continues while a page still has new filings; a missing daily index
  is retried hourly. `/status` shows Form 4 detection time in filing hours,
  with worker outages set apart and filings the feed missed counted.
- **Totals:** duplicates only between linked filings; amendment rows that add
  a trade count; stricter amendment pairing; ADS, foreign-currency,
  proportional "total as price", Form 144 yardstick, option and derivative
  value checks; flags recomputed from the current rules every 30 minutes.
- **Names and tickers:** O'/D' surnames, compound and Brazilian surnames,
  signatures deciding double surnames, company forms (GmbH, SARL, A/S...);
  placeholder and non-ticker symbols no longer make ticker pages.
- **Form 144:** a notice family settles to one current version in any load
  order; superseded notices keep no matches.
- **Pages:** rows left out of totals are labelled; Form 3 option terms;
  Ctrl+K opens the first result; Live dates; one role per person everywhere;
  plain public wording on `/status`; hydration-safe shared links; server
  pages forward the visitor's IP to the rate limiter.
- **Values that can't be right (rounds 4-5):** a price in another currency
  (read from the footnotes, conversions to dollars understood) is shown as
  filed, marked "Other currency" and left out of dollar totals; a trade worth
  over 10 times the company's reported public float, or priced over 1,000
  times its class's own prices of the past year, is flagged.
- **Pipeline (rounds 4-5):** polling pages back to the last watermark, wakes
  at the 06:00 opening, and a filing EDGAR removed after accepting it is
  marked "gone", not failed. `/status` counts the nightly check's finds only
  on days the live feed was collecting.
- **Consistency (round 5):** company names read the same on every page
  ("United Microelectronics Corp"); the ticker header and the tickers list
  share one 90-day window; a person's header says when flagged values are
  left out. The web app has its first tests (`npm test`, Node's own runner).
- **Live collection (round 6):** the newest page of each feed is read on a
  fixed timetable (Form 4 every 20 s) even while up to two earlier reads are
  waiting on a slow EDGAR; catch-up jobs (`edgar.reach_back_form4`,
  `edgar.reach_back_other`) page back every 1-2 minutes and alone move the
  watermark; a feed read waits up to 60 s, a second copy goes out after 10 s
  and a failed copy is replaced at once (at most 3). The scheduler no longer
  cancels a run twice.
- **Value checks (round 6):** an issuer-level ADS check (Alibaba's per-ADS
  price on ordinary shares, about $60M too high, is out of totals); ADS checks
  for any share class (Volaris, Telecom Argentina); a 365-day fallback for the
  per-share yardstick (Kanzhun counted); Bradesco's odd-lot codes (BBDC4F);
  'exchange ratio ... U.S. dollars' read as a conversion; an open-market trade
  at a price of 0 or for 0 shares has no value ('–', never $0; 247 rows) and
  one for 0 shares with a price is flagged; stored closes judge prices only
  when no stock split lies between the close and the trade (migration 0012,
  splits read from finlake). The 30-minute recompute works flags out on a
  copy and writes only the changes, so live loading never waits for it.
- **Names (round 6):** 173 filers corrected: organisations shown as people
  (MetLife, Thrivent, Sport City Cadiz, Cantor Fitzgerald) and people shown as
  companies (Jane E. Trust); names from the SEC record re-derived; signatures
  weighted by how many filings carry them; names typed first-name first
  ("Robert W. Sharps"). Search finds organisations that file as insiders and
  have no ticker.
- **Status (round 6):** reconciliation finds are split per filing into
  'missed while running' (true misses) and 'accepted while down'; the price
  job reports 'partial' when nothing is current; the stale-prices finding is
  measured per security; the spot check uses cased company names; every job
  has a staleness limit (a test enforces it).
- **Pages (round 6):** the tickers list says 'not in dollar totals' for
  foreign-currency and flagged rows ('no price reported' only for missing
  prices; `/api/tickers` replaces trades_without_value with
  trades_without_price and trades_left_out); the Live band uses the grid's
  value words.
- **Pre-review before round 7:** a hedged feed read uses a copy that answers
  while the second copy waits for its rate-limit token; `run-job` no longer
  frees the running worker's leases; ADS ratios are read in full ("one
  hundred (100)", "each 10 ADSs represent 23 shares") and a converted ADS
  price counts as per share (six NovaBridge rows corrected); the recompute
  writes a row only if nothing touched it since the compute; search finds
  organisations that file Form 144s and have no ticker (MetLife); the Status
  reconciliation columns read 'Found by the check: worker running / worker
  down'; a Form 144 value of 0 reads '–'; organisation profiles say
  'organization', not 'person'.
- **Round 7 (2026-10-13):** a profile lists the Form 144 notices its owner
  filed as the seller, and counts them as filings on record (sellers with
  nothing else, such as Metropolitan Life Insurance Co, said "No filings on
  record here yet"; `/api/people/{slug}/planned-sales`). Search puts a company
  whose name starts with the query before people who share the word ("apple"
  opens Apple Inc.) and lists a company once, under its common stock. The
  Status day-by-day table is a labelled list on phones; the outage total is in
  hours of filing time; the Overall tile names open watchdog problems; a
  partly succeeded run reads "Ran, incomplete". An open-market trade filed at
  a price of 0 shows no price. Names: the Dutch 't ("Cees 't Hart"), French
  "Le" surnames ("Olivier Le Peuch"), double surnames and "given, initial,
  surname" names settled by word counts (22 names, 87 sort keys). The
  daily-index check queues in accession order, so it can't deadlock with the
  feed.
- **Tooling:** the CLI runs as `python -m smt.cli` in every script (Windows
  Smart App Control began blocking the venv's `smt.exe` launcher on
  2026-09-29). `scripts/screenshots.py` takes the demo screenshots
  (docs/screenshots/phase1) with Edge's headless mode.

## Phase 1: insiders and Live (2026-09-24)

### Added

- **EDGAR collection:**
  - The Atom feed is polled every 60 s in filing hours (every 10 minutes
    otherwise) for Forms 3, 4, 5, 144 and their amendments. The form is
    matched exactly, with owner-only feeds for 3/4/5.
  - Each filing resolves through `index.json` into a job queue, fetched at
    no more than 8 requests/s under a declared User-Agent.
  - Nightly reconciliation against the SEC daily index re-queues anything
    missed.
- **Raw-first storage:** every document is stored (zstd, content-addressed)
  before parsing. Versioned parsers; a document that won't parse is
  quarantined, not dropped.
- **Parsers:**
  - Forms 3/4/5, golden-tested on 8 real filings;
  - Form 144, golden-tested on 3 real filings;
  - the SEC's quarterly Insider Transactions data sets, cross-checked
    against the XML of 54 filings.
- **Loaders** (idempotent upserts on natural keys):
  - joint-filer attribution;
  - 4/A amendment pairing (ambiguous pairings are set aside, never guessed);
  - exercise-and-sell grouping;
  - approximate late-filing flags from SEC business days and Reg S-T
    cutoffs;
  - Form 144 → Form 4 matching with a running total and confidence;
  - after 90 days, "no Form 4 found".
- **History:** `smt backfill` / `scripts\backfill.ps1` load the quarterly
  data sets. Loading is resumable, and the XML always wins over the rounded
  data-set copy. 2026 Q1 and Q2 are loaded: 125,361 filings, 0 quarantined.
- **Security master:**
  - SEC tickers with exchanges;
  - submissions profiles (SIC, former names);
  - sectors from the curated industry map, with a SIC fallback.
- **Data-quality flags:** figures that can't be right as filed (trade dated
  after its filing, a total typed as the price, a misplaced decimal within
  one share class) are kept as filed. They are left out of totals and
  rankings, and shown as "Check filing".
- **Form 144 seller names** come from EDGAR's conformed names. Anyone named
  from free text is corrected by a daily job, and old links keep working.
- **Worker:**
  - watchdog every 5 minutes (stale sources, backlog, quarantine, stuck
    leases, failed runs), with Apprise alerts sent once when a problem opens
    and again when it clears;
  - the scheduler waits out database outages instead of exiting.
- **Prices (private mode only):** a PriceProvider with licence tags, finlake
  read-only and yfinance behind the private session. Nothing priced is shown
  publicly.
- **API:**
  - feed and today;
  - people directory, profiles, trades and positions;
  - ticker index and ticker pages;
  - trade detail and search;
  - status, coverage, spot-check;
  - codes and sources;
  - private session and prices;
  - an SSE stream with a replay window.
- **Web:**
  - Live: today strip, downlink band, virtualized feed, pause, URL filters,
    watchlist panel;
  - People and profiles;
  - Tickers and ticker pages, with an ECharts event timeline and table
    alternatives;
  - Status, Methodology, Sources, Private mode, Watchlist;
  - honest Institutions and Signals placeholders;
  - quick-look drawer (`?trade=`), Ctrl+K palette, phone layouts;
  - black page background.

### Fixed during the build

- A process kill at a usage-limit cutoff took Postgres down, and the worker
  exited with it. The worker now retries until the database is back.
- A screen-reader-only label inside a sideways-scrolling strip widened the
  phone page to 570 px.
- "$1000k" rounding, and sub-dollar prices shown as "$0.01".

## Phase 0: skeleton (2026-09-24)

### Added

- Repository layout from the plan: `backend/`, `web/`, `config/`, `infra/`,
  `scripts/`, `docs/`.
- `.gitattributes` enforces LF endings, so shell scripts, Dockerfiles and YAML
  survive Linux containers. PowerShell scripts use CRLF.
- **Backend core:**
  - settings from the environment (read lazily, never at import);
  - structured logging;
  - SQLAlchemy 2 models for the Phase 0/1 tables (identity, securities,
    documents, trades, operations);
  - the Alembic baseline migration with `pg_trgm`.
- **Business-day logic** (`smt.domain.business_days`):
  - SEC business days (federal holidays plus one-off closures from
    `config/calendars.yaml`);
  - the NYSE trading calendar with 13:00 early closes;
  - Reg S-T filing-date cutoffs (22:00 ET for Forms 3/4/5, 144 and
    13D/13G; 17:30 ET for everything else);
  - Form 4, 13F, initial 13D and congressional PTR deadlines;
  - "first close after disclosure" for follower returns.
- **Test isolation guards:**
  - tests refuse any database not named `*_test`, and any non-local host
    outside CI;
  - every data path is redirected into a temp directory before import, and
    the run fails if finlake resolves to the real `~/.finlake`;
  - tests prove each guard trips.
- **Worker:** a lease-based asyncio scheduler. One job never overlaps
  itself, every run is recorded in `ingestion_runs`, and a crash resumes from
  state stored in the database. Includes the worker entry point and a
  heartbeat job.
- **API:** read-only FastAPI with `/healthz`, `/api/status` (per-job
  freshness against SLAs, counts, quarantine, reconciliation, pipeline lag)
  and OpenAPI at `/api/openapi.json`.
- **CLI:** `smt migrate | api | worker | jobs | run-job | backfill`.
- **Web:** Next.js 16 (App Router, strict TypeScript, Tailwind v4) with the
  design tokens, self-hosted Barlow Condensed and IBM Plex fonts, and an app
  shell (left rail on desktop, bottom bar on phones, skip link, visible
  focus, reduced-motion support).
- **Infra:**
  - Docker Compose (db, migrate, api, worker, web, caddy) with health checks,
    restart policies and log rotation;
  - a production override that uses GHCR images;
  - a Caddyfile that keeps the SSE stream uncompressed and unbuffered.
- **Scripts:** `db.ps1` (portable Postgres, no Docker needed), `dev.ps1`,
  `test.ps1`, `backfill.ps1`.
- **CI:** backend (ruff, mypy, pytest against a Postgres 18 service), web
  (eslint, tsc, next build), and image builds that push to GHCR from `main`.

### Notes

- Docker Desktop isn't installed on the development machine yet. Local
  development runs natively with portable PostgreSQL 18.6 and Node 24.21
  (the official, signed builds) in `%USERPROFILE%\.smart-money-tracker`. The
  Docker files are written but not yet run.
- There's no git remote yet. Pushing waits until the GitHub repo exists
  (`docs/plan/SETUP.md`).
