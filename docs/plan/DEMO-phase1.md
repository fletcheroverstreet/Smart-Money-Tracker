# Phase 1 demo checklist

A walk-through to confirm Phase 1 works, in about 15 minutes. Start the stack
with `.\scripts\dev.ps1` and open <http://localhost:3000>. Each step says what
you should see. If something differs, `/status` usually says why.

## Live (home)

1. **Today strip.** The largest open-market insider buys and sells disclosed
   today (US Eastern date), plus how many filings arrived by form. Each list
   names up to five different insiders: one person's lots in one company are
   added up into one entry, with the total and "N lots". Click a dollar amount:
   the quick-look drawer opens on the largest lot, and the URL gains `?trade=`.
   On a weekend or SEC holiday the strip shows the latest business day
   instead, titled with that day's date, and says EDGAR is closed today.
2. **Live stream.** The dot next to the search box reads "Live (updated N s
   ago)". During filing hours (6 AM to 10 PM ET on business days), new filings
   drop into the Downlink band with a short sweep, and a "Show N new" button
   appears at the end of the filter bar; press it to add them to the table.
   In the table's Detected column, today's rows show a time only; older rows
   also show "Yesterday" or the date.
3. **Pause.** Press "Pause live", reload the page: it stays paused, and the
   button shows as pressed. Press it again to resume.
4. **Filters.** Choose "All codes", "Planned sales", "$1M+". The URL changes,
   and copying it into a new tab gives the same view. The filters also work
   with the arrow keys: from "Any", pressing Right twice quickly lands on
   "$1M+".
5. **Keyboard.** Press `Tab` until the table has focus (the whole table is one
   tab stop), then press `j`/`k` to move and `Enter` to open the quick look.
   `Esc` closes it, and Back also closes it.
6. **Search.** Press `Ctrl+K` (or `/`), type a ticker such as `UXIN` or a name
   such as `dageville`, and press Enter straight away, without waiting. Enter
   opens the first result for exactly what you typed: while that search is
   still loading, the list is dimmed and Enter waits for it, then opens
   `/tickers/UXIN` or Benoit Dageville's profile. Keyboard focus moves to the
   new page. The palette opens empty each time, so a search for `tesla`
   followed by `SNOW` opens `/tickers/SNOW`, not Tesla. A search with no
   results says "Nothing matches". A company's name opens the company:
   `apple` + Enter opens `/tickers/AAPL` (people named Apple follow), and
   `goldman` lists Goldman Sachs Group once, under GS.

## A trade's quick look

7. It shows three dates (traded, disclosed, detected by us), the transaction
   code with its meaning, footnotes from the filing, joint owners, and a link
   to the original filing on EDGAR.
8. **Filing typos.** Open <http://localhost:3000/people?trade=141209> (REEMF).
   A note explains the figure can't be right. The value is shown exactly as
   filed and marked "as filed". On tables this trade shows "Check filing"
   instead of a dollar amount.

## People

9. `/people`: a sortable directory with search and role filters in the URL.
   The Notables tab explains that featured people are added only after you
   review them.
10. A profile, for example `/people/bin-william-li-2115037`, shows:
    - roles with company links;
    - a plain summary written only from filings;
    - stats, a trades table with a codes filter;
    - a positions chart with a "Table view" toggle. Each figure links to its
      filing. If the drawn period runs over dates whose filings aren't loaded
      yet, a note under the chart says so.
    - "Planned sales (Form 144)" when they have filed notices. A seller with
      nothing else on record, for example
      `/people/metropolitan-life-insurance-co-728618`, shows its notices
      first, each linking to the company's ticker page.

## Tickers

11. `/tickers`: companies ranked by open-market insider activity over 30, 90
    or 365 days.
12. `/tickers/UXIN`: an event timeline (▲ buys, ▼ sells, ○ Form 144 notices).
    Clicking a marker opens the quick look. It also has 3M/1Y/All in the URL,
    a "Table view" toggle, insider trades and planned sales. On 1Y or All,
    dates whose filings aren't loaded yet are shaded and labelled "not loaded
    yet", with a note under the chart. Prices aren't shown publicly.

## Trust pages

13. `/status`:
    - freshness per source and job;
    - watchdog findings;
    - reconciliation, with items set aside for review (each linking to its
      filing);
    - coverage and a spot-check of random rows linked to EDGAR.

    It refreshes every minute.
14. `/methodology`: every rule in plain English (collection timing, late
    flags, amendments, joint filings, the same trade in several filings,
    Form 144 matching, filing typos, history).
    - `/sources`: the terms and cadence of each source, and why prices are
      private-only.

## Private mode (optional)

15. Set `PRIVATE_MODE_PASSPHRASE` and `SESSION_SECRET` in `.env` and restart
    the API. Sign in at `/private`. A ticker page then draws a closing-price
    line if finlake has prices for it.

## Phone

16. Narrow the window to about 390 px, or use a phone on your network. The
    rail becomes a bottom bar (Live, People, Tickers, Watchlist, More). Tables
    become two-line rows, and no page scrolls sideways.

## Behind the scenes

17. `cd backend; uv run python -m smt.cli jobs` lists every scheduled job. The worker log is
    in `%USERPROFILE%\.smart-money-tracker\logs\worker.log`.
18. Stop Postgres (`.\scripts\db.ps1 stop`) for a minute, then start it again.
    The worker logs `scheduler.database_unavailable`, then carries on by itself.
19. `.\scripts\test.ps1` runs every check CI runs.
