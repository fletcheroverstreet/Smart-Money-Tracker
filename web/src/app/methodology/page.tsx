import type { Metadata } from "next";

import { ExternalLink } from "@/components/info/ExternalLink";
import { CodeTable } from "@/components/docs/CodeTable";
import { DocLink, DocSection, Example, Note, Prose, Toc, type TocItem } from "@/components/docs/Doc";
import { PageHeader } from "@/components/ui/PageHeader";
import { apiGetOrNull } from "@/lib/api/server";
import type { CodeOut, DataCoverage } from "@/lib/api/types";
import { datesHeldText } from "@/lib/coverage";

/*
 * Every statement on this page describes what the code does today. When the
 * code changes, change the text in the same commit. Where each part comes from
 * (all under backend/src/smt/):
 *
 *   collection, Live window        sources/edgar/jobs.py, pipeline.py, atom.py, worker/jobs.py, worker/schedules.py
 *   nightly and 10-minute checks   sources/edgar/jobs.py (reconcile_daily_index, fill_gaps), daily_index.py,
 *                                  sources/edgar/coverage.py (the 99% rule)
 *   "today", default filter        api/routers/feed.py, web components/live/filters.ts
 *   business days, filing dates    domain/business_days.py, config/calendars.yaml
 *   codes                          domain/codes.py (table served by GET /api/meta/codes)
 *   late flags, joint filings,
 *   owner types, amendments,
 *   exercise and sell              ingest/ownership.py, ingest/entities.py, domain/names.py
 *   planned sales                  ingest/form144.py (match_planned_sale)
 *   amendment pairing              ingest/ownership.py (_apply_amendment, _pair_rows)
 *   the same trade in two filings  ingest/quality.py (mark_duplicates)
 *   deemed-date cap                domain/business_days.py (form4_deadline)
 *   older filings                  sources/edgar/backfill.py, insider_dataset.py
 *   filing typos                   ingest/quality.py (job quality.flag_transactions)
 *   people's names                 ingest/entities.py, sources/edgar/names.py (Form 144 names)
 *
 * The worked dates in the examples were checked against form4_deadline() and
 * filing_date_for() in domain/business_days.py.
 */

export const metadata: Metadata = {
  title: "Methodology",
  description:
    "How Smart Money Tracker collects insider filings from the SEC, what each label means, how late disclosures, amendments and planned sales are handled, and what the site does not do.",
};

// The code table is read from the API on each request, like every other page.
export const dynamic = "force-dynamic";

const SECTIONS: TocItem[] = [
  { id: "what-we-collect", label: "What we collect" },
  { id: "three-dates", label: "Traded, disclosed, detected" },
  { id: "collection", label: "How new filings get here" },
  { id: "nightly-check", label: "Checks against the daily index" },
  { id: "today", label: "What “today” means on Live" },
  { id: "codes", label: "Transaction codes" },
  { id: "exercise-and-sell", label: "Exercise and sell" },
  { id: "owners", label: "Joint filings, roles and owner types" },
  { id: "same-trade", label: "The same trade in several filings" },
  { id: "business-days", label: "Business days and filing dates" },
  { id: "late", label: "Late disclosures" },
  { id: "amendments", label: "Amendments (Form 4/A)" },
  { id: "planned-sales", label: "Planned sales (Form 144)" },
  { id: "history", label: "Older filings" },
  { id: "typos", label: "Figures that can’t be right" },
  { id: "limits", label: "Known limits" },
  { id: "does-not-do", label: "What this site does not do" },
];

export default async function MethodologyPage() {
  const [codes, coverage] = await Promise.all([
    apiGetOrNull<CodeOut[]>("/api/meta/codes"),
    apiGetOrNull<DataCoverage>("/api/meta/coverage"),
  ]);
  const held = coverage ? datesHeldText(coverage) : null;

  return (
    <div className="pb-10">
      <PageHeader
        title="Methodology"
        subtitle="How the data is collected, what each label means, and where the limits are. Written from the code that does the work."
      />
      <Toc items={SECTIONS} />

      <DocSection id="what-we-collect" title="What we collect">
        <Prose>
          <p>
            Everything on this site comes from official filings published on the SEC’s EDGAR system. Right now that
            means two kinds of filing:
          </p>
          <ul>
            <li>
              <strong>Forms 3, 4 and 5</strong>, and their amendments, filed by company insiders: officers, directors
              and anyone owning more than 10% of a company’s stock, which includes investment funds. A Form 3 is the
              first statement when someone becomes an insider and lists what they hold. A Form 4 reports a change,
              such as a purchase, sale, grant or gift. A Form 5 is an annual form for items that were allowed to wait,
              or that were missed during the year.
            </li>
            <li>
              <strong>Form 144</strong> notices, and their amendments, in which an affiliate of a company says they
              intend to sell restricted or control shares.
            </li>
          </ul>
          <p>
            We also load the SEC’s list of company tickers once a day, to connect each company to its current ticker.
          </p>
          <p>
            We read each filing’s machine-readable XML and keep an exact copy of it before reading it. Numbers are shown
            as the filer reported them. A trade’s dollar value is shares × price, rounded to the cent, and only when
            the filing gives both. A number the filing doesn’t give is shown as “–”, never as $0, and so is a value of
            exactly $0: an award or gift at a price of 0 has no dollar value, and an open-market purchase or sale filed
            at a price of 0 (one filer’s cash-settled units) has no stated price, so its price reads “–” too. An
            award or gift keeps the $0.00 its filing gives, because nothing was paid. When a price’s
            footnote says it is a weighted average of several trades, the price is marked that way. Every trade links
            to its filing on EDGAR so you can check it.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="three-dates" title="Traded, disclosed, detected">
        <Prose>
          <p>Every trade carries three moments, because the law allows a gap between a trade and its disclosure:</p>
          <ul>
            <li>
              <strong>Traded:</strong> the transaction date written on the form. Forms give a date, never a time of
              day.
            </li>
            <li>
              <strong>Disclosed:</strong> when EDGAR accepted the filing, in US Eastern time. Filings loaded from the
              SEC’s quarterly data sets (see <DocLink href="#history">Older filings</DocLink>) only carry a filing date,
              so for them this is a date without a time.
            </li>
            <li>
              <strong>Detected:</strong> when this site first stored the row.
            </li>
          </ul>
          <p>
            “Filed 3 days after the trade” counts calendar days from the trade date to the filing’s official filing
            date. Late flags use SEC business days instead; see <DocLink href="#late">Late disclosures</DocLink>.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="collection" title="How new filings get here">
        <Prose>
          <p>
            A background worker reads EDGAR’s latest-filings feed. From <strong>6:00 AM to 10:00 PM Eastern on SEC
            business days</strong>, which are EDGAR’s filing hours, it reads the newest page of the{" "}
            <strong>Form 4</strong> feed <strong>every 20 seconds</strong>, and of the <strong>Forms 3 and 5</strong>{" "}
            and <strong>Form 144</strong> feeds <strong>every 60 seconds</strong>. At all other times each read runs{" "}
            <strong>every 10 minutes</strong>, which still catches the odd late acceptance.
          </p>
          <ul>
            <li>
              The reads keep to that timetable even while EDGAR is slow: at busy times the feed can take 20 to 60
              seconds to answer, so a new read starts on time while up to two earlier ones are still waiting. A read
              that hasn’t answered after 10 seconds is asked for a second time, and whichever copy answers first is
              used (a copy that fails is replaced at once, at most three copies, each waiting up to a minute).
            </li>
            <li>
              Each read takes the newest 100 filings. Separate catch-up jobs page further back through the feed
              every one to two minutes, until they reach the newest filing the last completed catch-up saw (less 10
              minutes of slack) and a page holds nothing new, or after 25 pages. That covers a burst of more than 100
              filings, and any read that failed or was cut short. After a long stop the feed can hold more than 25
              pages of new filings; the catch-up then says so in its record, and the check against the daily index
              loads the rest. Only the exact form types above are kept: the
              feed’s own filter for “4” also returns unrelated forms such as 424B2 prospectuses.
            </li>
            <li>
              New filings go into a queue. Each read loads the filings it found itself, newest first, up to 20 at
              once (Form 144: up to 100); a filing two reads both find is queued and loaded once. A second job runs every 15 seconds and
              works through up to 200 more.
            </li>
            <li>
              For each filing we read EDGAR’s list of the filing’s documents, pick the XML document that actually is
              the ownership form or Form 144 (file names vary from filer to filer, so they are never guessed), store an
              exact copy, then read it. Everything the filing adds (the trades, holdings, people, company and the Live
              entry) is saved in one step, so the site never shows half a filing.
            </li>
            <li>
              Nothing is dropped silently. A filing that can’t be read is marked failed, and its document is set aside
              for review; both are counted on the <DocLink href="/status#quarantine">Status page</DocLink>. When EDGAR
              doesn’t answer or asks us to slow down, the filing is tried again every 5 minutes for up to 3 days,
              without using up an attempt. Any other error is retried after 1, 2, 4, 8 and 16 minutes, and the filing
              is marked failed after its sixth attempt; a document that can’t be read is marked failed at once.
            </li>
          </ul>
          <p>
            When everything is working, a new filing should be on screen within a minute or two of EDGAR publishing
            it. The Status page shows how long the newest filings actually took.
          </p>
          <h3>Which filings appear on Live</h3>
          <p>
            A filing is announced on the Live page only if it was found through the latest-filings feed{" "}
            <strong>and</strong> EDGAR accepted it less than <strong>6 hours</strong> before we loaded it. Older
            filings, such as those found by the nightly or 10-minute checks or loaded from history, are saved and appear on people
            and ticker pages, but not on Live. Each filing is announced once; loading it again later doesn’t announce
            it again.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="nightly-check" title="Checks against the daily index">
        <Prose>
          <h3>Every night</h3>
          <p>
            The latest-filings feed only reaches back a limited time, and that window shrinks during the day, so a
            filing could be missed, for example while the worker was stopped. To catch those, the worker runs a check{" "}
            <strong>every night at 11:00 PM Eastern</strong>:
          </p>
          <ol>
            <li>
              It downloads EDGAR’s official daily index for each SEC business day among the last five days, today
              included. The SEC posts a day’s index at about 10:00 to 10:30 PM Eastern.
            </li>
            <li>It compares every Form 3, 4, 5 and 144 in the index, amendments included, with what we hold.</li>
            <li>
              Anything missing is queued and loaded. The number expected, found and missing for each day is recorded
              and shown on the <DocLink href="/status#reconciliation">Status page</DocLink>. What the check found
              (filings neither the live feed nor its catch-up had loaded) is split in two: found with the{" "}
              <strong>worker running</strong> when EDGAR accepted the filing (a real miss by the live feed, where the
              goal is zero), or with the <strong>worker down</strong> (it was stopped then, hadn’t started yet, or
              stopped within 2 minutes after, the same rule as the Form 4 detection times). Filings accepted during a
              stop that the catch-up loaded after the restart are in neither count. A day with no time running in
              filing hours reads “Loaded afterwards”.
            </li>
          </ol>
          <p>
            An index that isn’t posted yet at the nightly check is checked again every hour until it is, for up to five
            days. Filings found this way are saved but not announced on Live.
          </p>
          <h3>Every 10 minutes: days that aren’t fully loaded</h3>
          <p>
            Every 10 minutes the worker also goes through the SEC business days from the first day we hold up to
            yesterday and compares each day that isn’t fully loaded with that day’s daily index. A day counts as{" "}
            <strong>loaded</strong> when at least <strong>99%</strong> of the Forms 3, 4, 5 and 144 listed in its index
            are held (a few filings can fail for good without hiding a day), <strong>partly loaded</strong> from half
            of them, and <strong>not loaded</strong> below that. A day that was never compared, such as a day from the
            quarterly data sets, counts as loaded when it holds at least 50 Form 4s: EDGAR receives hundreds on every
            business day, so fewer means the day wasn’t collected. Today is left out until its index is posted in the
            evening.
          </p>
          <p>
            For a day short of 99%, whatever is missing is queued and fetched from its XML, newest days first, behind
            live work and within the same SEC request budget. A day whose filings are still waiting isn’t compared
            again, and a day that stays short (usually a filing that can’t be read) is compared again after 12 hours.
            It stops adding days while a large batch is still waiting. These filings are saved but never announced on
            Live. The same rule decides the “not loaded yet” notes on this site.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="today" title="What “today” means on Live">
        <Prose>
          <p>
            The “Today” summary on the <DocLink href="/">Live page</DocLink> uses the calendar date in US Eastern
            time, the clock EDGAR runs on. A filing belongs to today when its <strong>official filing date</strong> is
            today, whatever the trade date was. EDGAR takes no filings on weekends and SEC holidays, so on those days
            the summary shows the latest business day instead, titled with that day’s date, and says EDGAR is closed
            today.
          </p>
          <ul>
            <li>
              Filing dates follow the SEC’s evening cutoff (see{" "}
              <DocLink href="#business-days">Business days and filing dates</DocLink>). A Form 4 accepted at 10:15 PM is
              dated the next business day and counts toward that day instead.
            </li>
            <li>
              <strong>Largest buys and sells</strong> are the five biggest open-market purchases (code P) and sales
              (code S) in today’s filings, by dollar value, one entry per insider in each company. Rows without a
              dollar value are left out, and so are rows that an amendment has since replaced, second copies of a trade
              that is already counted, amendment rows that might repeat a row of the original, and{" "}
              <DocLink href="#typos">figures that can’t be right</DocLink>. A Form 4 often reports one sale as several
              lots at different prices, and an insider may file more than once a day, so all of one insider’s lots in
              one company are added up: the entry shows the total and the number of lots, and ranks by the total. Its
              amount opens the largest lot.
            </li>
            <li>
              <strong>Insiders buying</strong> counts the different insiders credited with at least one open-market
              purchase in today’s filings.
            </li>
            <li>
              <strong>Planned sales</strong> counts the Form 144 notices filed today.
            </li>
          </ul>
          <h3>The Live feed’s default filter</h3>
          <p>
            By default the Live feed shows only open-market buys and sells (codes P and S) among insider trades. Choose
            “All codes” to also see grants, option exercises, tax withholding, gifts and the rest. Form 144 notices and
            Form 3 statements are shown either way. The feed is ordered by when we detected each item, newest first. A
            minimum dollar size hides rows that have no dollar value.
          </p>
        </Prose>
      </DocSection>

      <DocSection
        id="codes"
        title="Transaction codes"
        lead="Every Form 4 row carries a one-letter code saying what kind of transaction it was. Only P and S are buying and selling on this site; everything else is stored, labelled and shown when you ask for all codes."
      >
        <CodeTable codes={codes} />
        <div className="mt-4">
          <Prose>
            <p>
              Grants, option exercises, tax withholding and gifts are mostly compensation or paperwork, not a decision
              to put money in or take it out, so they never count as buying or selling here. The code is stored exactly
              as the filer wrote it. The meanings follow the SEC’s instructions for Form 4.
            </p>
          </Prose>
        </div>
      </DocSection>

      <DocSection id="exercise-and-sell" title="Exercise and sell">
        <Prose>
          <p>
            When one filing reports an option exercise or conversion (code M or X) <strong>and</strong> a sale (code
            S) on the same date, every transaction line in that filing dated that day is grouped and labelled
            “exercise and sell”. That lets you tell a same-day exercise and sale apart from a sale of shares the insider
            already held.
          </p>
          <p>
            The sale keeps its code S and still counts as a sale. Grouping only looks inside one filing: an exercise
            reported in one filing and a sale reported in another are not grouped.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="owners" title="Joint filings, roles and owner types">
        <Prose>
          <h3>Joint filings</h3>
          <p>
            Several owners can file one form together, for example a fund, its general partner and its managers. The
            trades in it happened once, so they are stored once and <strong>credited to one owner</strong>: the first
            listed owner that we classify as a fund, or the first listed owner if none is. Every owner on the filing is
            linked to the trade, so it appears on each of their pages, while company-level totals count it once.
          </p>
          <h3>People and funds</h3>
          <p>
            EDGAR doesn’t say whether a filer is a person or an organization, so we decide from the name, and check
            it again whenever a filing brings EDGAR’s registered name, and once a day. Legal forms (Inc, LLC, LP, Corp,
            S.a r.l., Berhad and others), words such as Trust, Fund, Plan, Financial or Holdings, and digits mark an
            organization, unless the filer’s own signatures name every word of it in the usual order (see{" "}
            <DocLink href="#limits">Known limits</DocLink>). An organization whose name has a word such as Fund,
            Partners, Capital, Management, Investors, Advisors, Ventures, Equity or LP is treated as a fund. Everyone
            else is treated as a person. This is a rule of thumb and can be wrong.
          </p>
          <h3>Roles</h3>
          <p>
            Roles come from the boxes the filer ticks: officer (with the title they give), director, 10% owner, or
            other. A role label such as “Chief Financial Officer, Director” lists them in that order. For each role
            at each company we record the latest report that shows it; when there is a Form 3, its date marks when the
            role began.
          </p>
          <h3>Direct and indirect ownership</h3>
          <p>
            Each row says whether the shares are held <strong>directly</strong> by the owner or{" "}
            <strong>indirectly</strong>, through a trust, a family member, a fund or a company. Indirect rows keep the
            filer’s own words for how they’re held, such as “By Family Trust”.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="same-trade" title="The same trade in several filings">
        <Prose>
          <p>
            A fund and its general partner, or other related owners, often file <strong>separate</strong> Form 4s for
            one trade. Two open-market rows are treated as the same trade when all of these hold:
          </p>
          <ul>
            <li>
              they have the same company, trade date, code, number of shares, price and holding after the trade;
            </li>
            <li>they come from different filings;</li>
            <li>
              and the filings are linked: they share a reporting owner (then they can be any time apart), or at least
              one of the rows is reported as indirect ownership and they were filed within 3 days of each other.
            </li>
          </ul>
          <p>
            Totals and rankings count such a trade <strong>once</strong>: the first copy stored counts, and the others
            point to it. Each owner’s page still lists the trade. Two people who each bought the same amount directly
            are never merged, because identical allotments (in an offering, for example) are common. Two identical
            lines inside one filing are separate lots, and both count.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="business-days" title="Business days and filing dates">
        <Prose>
          <p>
            <strong>SEC business days</strong> are Monday to Friday, except US federal holidays (on the day they are
            observed) and one-off closures such as national days of mourning, which we list by hand (5 December 2018
            and 9 January 2025 so far). Federal holidays include Columbus Day and Veterans Day, when the stock market is
            open. Good Friday is the reverse: the market is closed but the SEC is open.
          </p>
          <p>
            <strong>Filing dates.</strong> EDGAR gives every filing an official date. Forms 3, 4, 5 and 144 (and
            Schedules 13D and 13G) accepted before <strong>10:00 PM Eastern</strong> on a business day get that day’s
            date. Other forms, including 13F, cut off at <strong>5:30 PM</strong>. Anything accepted later, or on
            a weekend or holiday, is dated the next business day. We use the date EDGAR gives, and work it out this way
            only when EDGAR doesn’t give one.
          </p>
          <Example
            rows={[
              { label: "Form 4 accepted Tue 22 Sep 2026, 9:59 PM ET", value: "Filing date Tue 22 Sep" },
              { label: "Form 4 accepted Tue 22 Sep 2026, 10:15 PM ET", value: "Filing date Wed 23 Sep" },
            ]}
          />
        </Prose>
      </DocSection>

      <DocSection id="late" title="Late disclosures">
        <Prose>
          <p>
            A Form 4 is due by the end of the <strong>second SEC business day after the trade</strong>. Some trades
            (for example many made under a pre-arranged 10b5-1 plan) have a “deemed execution date”, the day the
            broker told the insider about the trade; when the form gives one, the two days count from that date
            instead. The rule caps it: a deemed date can be no later than the <strong>third business day after the
            trade</strong>, so a later one counts as that third day. A deemed date before the trade counts as the
            trade date.
          </p>
          <p>
            A row is flagged as a <strong>late disclosure</strong> when its filing date is after the deadline, and we
            count how many business days late it was.
          </p>
          <Example
            rows={[
              { label: "Trade Thu 17 Sep 2026", value: "Due Mon 21 Sep" },
              { label: "Trade Mon 9 Nov 2026", value: "Due Thu 12 Nov (Veterans Day, Wed 11 Nov, isn’t counted)" },
              { label: "That trade filed Mon 16 Nov", value: "Late by 2 business days" },
              { label: "Trade Thu 17 Sep 2026, deemed date Fri 18 Sep", value: "Due Tue 22 Sep" },
              {
                label: "Trade Thu 17 Sep 2026, deemed date given as Mon 28 Sep",
                value: "Counted from Tue 22 Sep (the cap), due Thu 24 Sep",
              },
            ]}
          />
          <h3>What is never flagged on a Form 4</h3>
          <ul>
            <li>Codes L and W, which the rules allow to wait for the annual Form 5.</li>
            <li>Rows the filer marked as reported early.</li>
            <li>Gifts (code G) made before 27 February 2023, when gifts could still wait for Form 5.</li>
            <li>Holdings, Form 3 statements, and rows without a trade date.</li>
          </ul>
          <h3>Amendments and Form 5</h3>
          <ul>
            <li>
              A Form 4/A is judged by the <strong>original</strong> filing’s date, so correcting a filing that was on
              time doesn’t make it late.
            </li>
            <li>
              On a Form 5, a row counts as late when the filer says it is a Form 4 transaction being reported on Form 5,
              or marks it as late. Other Form 5 rows are not flagged, and no deadline date is shown for them.
            </li>
          </ul>
          <Note title="Why some late flags say “approximate”">
            <p>
              When the deadline is counted from a deemed execution date, the flag is labelled approximate. The site
              applies the cap described above, but the deemed date itself is the filer’s own report, which nothing in
              the filing lets us check, so such a flag could be off.
            </p>
          </Note>
          <p>
            A late flag describes timing only. It says nothing about why a filing was late.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="amendments" title="Amendments (Form 4/A)">
        <Prose>
          <p>
            An amendment corrects an earlier filing, but EDGAR doesn’t say which one. The amendment only gives the date
            the original was filed. So we look for the original as a filing that is <strong>not</strong> itself an
            amendment, is about the same company, shares at least one reporting owner (by SEC number), and was filed on
            that date.
          </p>
          <ul>
            <li>
              <strong>Exactly one match:</strong> the two are linked.
            </li>
            <li>
              <strong>No match yet:</strong> nothing is linked. If the original arrives later, which is common while
              history is loading, the link is made then.
            </li>
            <li>
              <strong>More than one match:</strong> the amendment is linked to one of them only if every one of its
              rows matches a row of that filing by the first two rules below (same date and shares, or same shares).
              Otherwise nothing is linked and the amendment is set aside for review on the Status page; its rows that
              might repeat a row of one of the candidates (the same table and transaction code) are shown but left out
              of totals. An amendment that doesn’t give the original’s date isn’t linked either.
            </li>
          </ul>
          <h3>Which rows are replaced</h3>
          <p>
            An amendment usually re-reports only the lines it changes. Each of its rows is paired with a row of the
            original. The rules are tried in this order, and each rule is tried on every row before the next rule is
            used, so a looser rule can’t take a row that a stricter one would give to another. A rule pairs two rows
            only when it finds exactly one partner. Rules 1 to 5 also need the same table (shares or derivatives),
            security and transaction code.
          </p>
          <ol>
            <li>the same trade date and number of shares (the price or holding was corrected);</li>
            <li>the same number of shares (the date was corrected);</li>
            <li>the same trade date and price (the number of shares was corrected);</li>
            <li>the only row with that trade date;</li>
            <li>
              the only row within 5 days of it whose number of shares is within a factor of two (no more than double,
              no less than half);
            </li>
            <li>
              finally, the same position in the same table, but only when both filings have the same number of rows.
            </li>
          </ol>
          <p>
            A paired original row stops counting and points to its correction. Original rows the amendment doesn’t
            re-report stay as they are. An amendment with no rows, for example one that only adds a power of attorney,
            replaces nothing.
          </p>
          <p>An amendment row that can’t be paired is handled in one of two ways:</p>
          <ul>
            <li>
              <strong>It might repeat a row of the original.</strong> If the original still has a current, unpaired
              row of the same kind (same table and transaction code), the two may describe one trade. The amendment
              row is shown, but left out of totals and rankings, and set aside for review on the Status page.
            </li>
            <li>
              <strong>It’s a trade the original left out.</strong> If the original has no such row, there is nothing
              it could repeat, and it counts like any other row.
            </li>
          </ul>
          <p>
            A Form 144 amendment names the notice it amends. That notice is marked as replaced, and ticker pages show
            only the latest version.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="planned-sales" title="Planned sales (Form 144)">
        <Prose>
          <p>
            A Form 144 says an affiliate <strong>intends</strong> to sell. It is not a sale, so the site shows it as
            “plans to sell” with the market value the filer stated, marked approximate (“~$”). When a notice lists
            several lines of securities, their shares and values are added up, and the earliest approximate sale date
            is used.
          </p>
          <h3>Linking a notice to the sale</h3>
          <p>
            When a later Form 4 reports the sale, it is linked to the notice as “likely executed by”. A Form 4 row is a
            candidate when:
          </p>
          <ul>
            <li>the seller is the same (by SEC number; any owner on a joint Form 4 counts);</li>
            <li>the company is the same;</li>
            <li>
              its code is S (a sale) of shares, not of options or other derivatives, no amendment has replaced it, and
              it isn’t a second copy of a trade already counted (see{" "}
              <DocLink href="#same-trade">The same trade in several filings</DocLink>);
            </li>
            <li>it was traded between one day before the notice’s filing date and 90 calendar days after it;</li>
            <li>it isn’t already linked to another current notice. Each Form 4 sale is linked to one notice at most.</li>
          </ul>
          <p>
            One planned sale is often carried out in several trades. Candidates are taken{" "}
            <strong>closest to the notice’s approximate sale date first</strong> (its filing date when it gives
            none), and their shares are added up until they reach the planned number of shares. The last trade taken
            can pass the plan, but the matched total shown never exceeds the planned shares. If the notice doesn’t
            state a number of shares, every candidate is linked.
          </p>
          <p>A notice that a Form 144/A has replaced keeps no links; the amendment is matched instead.</p>
          <h3>Confidence</h3>
          <ul>
            <li>
              Each linked sale is rated <strong>90%</strong> if it was traded within 3 days of the notice’s approximate
              sale date, and <strong>70%</strong> otherwise.
            </li>
            <li>
              The notice as a whole is rated <strong>90%</strong> when the linked shares add up exactly to the planned
              shares, and <strong>70%</strong> otherwise.
            </li>
          </ul>
          <p>
            Matching runs when the notice arrives and again whenever a new Form 4 with sales by that seller at that
            company is loaded from EDGAR.
          </p>
          <p>
            Once a day, a notice filed more than 90 days ago with no linked sale is marked “no Form 4 found”.
          </p>
          <Note title="No match doesn’t mean no sale">
            <p>
              Many Form 144 filers are affiliates who never file a Form 4, so a notice without a linked sale doesn’t
              mean the sale didn’t happen.
            </p>
          </Note>
          <h3>The seller’s name</h3>
          <p>
            A notice spells the seller’s name in whatever order the filing agent chose (“Timothy Cutt” in one,
            “Dageville Benoit” in another). So people are named from EDGAR’s own registered name for their SEC number,
            which the live feed and the daily index supply. If only the notice’s spelling is available, the name is
            corrected later from the person’s own Form 3, 4 or 5, or from their SEC record, by a daily check.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="history" title="Older filings">
        <Prose>
          <p>
            Filings from before the live collector started come from the SEC’s quarterly{" "}
            <ExternalLink href="https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets">
              Insider Transactions Data Sets
            </ExternalLink>
            , which the SEC publishes shortly after each quarter ends (the second quarter of 2026 was posted on 9
            July). They are loaded by the same code as live filings, and a filing is identified the same way on both
            paths, so it is never stored twice.
          </p>
          <p>The data sets are a less exact copy than the filing’s own XML:</p>
          <ul>
            <li>
              <strong>Every number is rounded to 2 decimal places.</strong> A $0.005 exercise price becomes $0.01.
            </li>
            <li>
              <strong>“Nature of ownership” text can land on the wrong row.</strong> When only some rows of a table
              have it, the SEC’s files can shift the text onto the wrong rows. In those tables we can’t tell which row it
              belongs to, so we store it as missing rather than attach “By Family Trust” to the wrong line. The direct
              or indirect mark itself isn’t affected and is kept.
            </li>
            <li>They give a filing date but not the time EDGAR accepted the filing.</li>
          </ul>
          <p>
            So the <strong>XML always wins</strong>. A filing we already loaded from its XML is skipped when history is
            loaded, and a filing loaded from a data set is overwritten when its XML is later read. Trades that still come
            from a data set say so on their detail view. History is never announced on the Live page.
          </p>
          <p>
            <strong>Not every date is loaded yet.</strong>{" "}
            {held ? `Forms 3, 4 and 5 are held for filing dates ${held} ` : null}
            Wherever a page counts trades over a window of dates (the Tickers ranking, the 90-day counts on a ticker
            page, a person&apos;s record), it says so when the window runs over missing days, because its totals are
            then too low. The <DocLink href="/status#coverage">Status page</DocLink> keeps the current list.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="typos" title="Figures that can’t be right">
        <Prose>
          <p>
            EDGAR accepts filings with typing mistakes, and some make a figure impossible. The site never changes what a
            filing says, but it marks these figures and keeps them out of anything that adds up or ranks trades:
            dollar totals, the largest trades, “last trade” dates, size filters and chart marker sizes. Where the value
            would appear, the site shows “Check filing”, and the trade’s detail view explains why.
          </p>
          <ul>
            <li>
              <strong>A trade dated after its own filing.</strong> A filing can only report trades that already
              happened, so a trade date later than the filing date (a 2029 date on a Form 4 filed in March 2026) is a
              typo. Its delay and late-filing check can’t be worked out, so neither is shown.
            </li>
            <li>
              <strong>A price that can’t be a price per share:</strong> above $1,000,000 a share, a value above $10
              billion for a single trade, or the share count repeated in the price field (4,000,000 shares at
              “$4,000,000”). The most common cause is the trade’s total typed as its price.
            </li>
            <li>
              <strong>A misplaced decimal point:</strong> a price more than 50 times the median price of the same
              company’s open-market trades in the same share class that quarter (at least 4 of them), for example
              $15,159 for a share that traded near $150. Only open-market trades set the yardstick, and only within one
              share class, because a super-voting or preferred share can legitimately cost far more than the common
              stock.
            </li>
            <li>
              <strong>A price per American Depositary Share against shares.</strong> Some foreign companies trade in
              the US as ADSs (or ADRs), each standing for several shares. A row that counts shares but is priced per
              ADS overstates the value by that ratio (60 times, for one filer). This applies to any share class, not
              just “ordinary shares” (one airline reports “Series A shares” priced per ADS). A row is flagged when its
              footnotes mention ADSs and a footnote says the price is per ADS. When the filing only says how many
              shares one ADS represents, or when the row’s notes don’t mention ADSs at all but the company’s filings
              within a year state a ratio of N shares per ADS, the price is compared with the class’s price per share
              from open-market trades within 180 days (365 when nothing is closer; for a class with no open-market
              trades at all, the company’s ADS trades ÷ the ratio): a price at or above that figure × √N, nearer the
              per-ADS price, is flagged. One company’s sale of
              692,992 ordinary shares at the $94.92 ADS price, 8 shares per ADS, was about $58 million too high. With
              nothing to compare against, a row whose filing only states a ratio is flagged. Form 144 prices aren’t
              used here: most are per ADS. The ratio is read in full, however it is written (“one hundred (100)”,
              “thirty-five”, “each 10 ADSs represent 23 ordinary shares”). A filing that says one ADS is one share
              isn’t flagged, and neither is a row whose footnote gives its own price per share, a row whose note gives
              an ADS price that the ratio converts to the row’s price ($1.6908 per ADS ÷ 2.3 = $0.74 a share), or a
              grant with no price. A ratio below 1 (several ADSs per share) flags a row only when a note says the
              price is per ADS.
            </li>
            <li>
              <strong>A total typed as the price on every row.</strong> The check above compares a price with the
              company’s other prices, so it can’t see a filer who makes the same mistake on every trade. Four more
              checks measure the price against something else. First, when one owner’s trades in a company have “prices” that grow
              in step with the number of shares (so that price ÷ shares is nearly the same, and looks like a share
              price), the prices are totals: one trust reported 34,800 shares at “$52,316.70”, which is $1.50 a share.
              Second, a price of $1,000 or more is compared with the price implied by the company’s own Form 144
              notices from within 90 days (a notice’s market value ÷ its shares) and, for common shares, with the
              median stored daily closing price within 90 days: more than 50 times either for the same class, or more
              than 2,000 times the Form 144 price for any class, is flagged. Stored closes are adjusted for later stock
              splits while a filing’s price is as traded, so a close counts only when it was recorded within 10 days
              of its session, or when the price source’s record of splits, read after the close, shows none between
              its session and when it was read; and never a close on the other side of a split from the trade (after
              a 25-for-1 split, a January price would read 25 times too high). Third, a trade worth more than 10 times
              the company’s whole public float (the market value of its shares not held by insiders, as the company
              reports it to the SEC on its annual report, looked up for companies with large trades and refreshed every
              90 days) is flagged when its price is $1,000 or more. Companies sometimes type the float in thousands, so
              it only judges the large “prices” a typed total produces; a float of zero, which new listings report, is
              ignored. Fourth, a price of $1,000 or
              more that is over 1,000 times the same class’s median price in the year before (from any transaction
              with a price) is flagged: one filer’s preferred shares changed hands at $1.00 in May, then a filing
              showed 1,000 of them bought at “$100,000” each.
            </li>
            <li>
              <strong>A price in another currency.</strong> Some companies report the price in their home currency (New
              Taiwan dollars, Mexican or Argentine pesos, yen and others) and say so in a footnote. Such a figure isn’t
              a dollar amount, so it is shown as filed, marked “Other currency”, and left out of dollar totals. A
              footnote saying the price was converted into U.S. dollars, or stating the dollar price, clears it.
            </li>
            <li>
              <strong>A foreign listing with no currency stated.</strong> When the security is named by its listing
              outside the US (Brazil’s “BBDC4” or “BVMF: CSAN3”, Taiwan’s “2330.TW”) and nothing on the row says what
              currency the price is in, the figure can’t be taken as dollars: it is shown as filed, marked “Currency
              not stated”, and left out of dollar totals. One bank reports its São Paulo price in reais this way,
              including its odd-lot listings (“BBDC4F”).
            </li>
            <li>
              <strong>An open-market trade for 0 shares with a price.</strong> Shares × price is $0 but the filing
              names a price, so the figure is something else: one fund’s “Limited Liability Company Interests”, which
              aren’t split into units, carry the amount invested in the price field. It is flagged and has no value.
            </li>
            <li>
              <strong>An option or warrant priced above the share itself:</strong> a bought or sold option, warrant,
              call or put whose price is more than twice the company’s typical share price that quarter (the median of
              at least 4 open-market trades in its shares). Such a “price” is usually the total premium typed in the
              price field. Convertible notes, which are legitimately priced near $1,000, aren’t checked this way.
            </li>
            <li>
              <strong>Any derivative worth far more than its shares:</strong> a bought or sold option, warrant, note
              or other derivative whose value is more than 10 times what its underlying shares are worth, at the
              company’s typical share price that quarter (or, when there is none, the row’s own exercise or
              conversion price). One filer reported a note convertible into about $30,000 of stock as $281 million.
            </li>
          </ul>
          <p>
            These checks run as filings load and again every 30 minutes over everything stored. Each 30-minute run
            works the marks out again from the current rules, so a mark a corrected rule no longer supports is removed.
            It works them out on a copy and then writes only the marks that changed, in one short step, so loading new
            filings never waits for it. A row that was loaded again in the meantime is left for the next run.
          </p>
        </Prose>
      </DocSection>

      <DocSection id="limits" title="Known limits">
        <Prose>
          <ul>
            <li>
              The same trade filed separately by related owners is counted once only when the filings are linked as
              described in <DocLink href="#same-trade">The same trade in several filings</DocLink>. Copies that
              differ in any figure, or that aren’t linked that way, each count.
            </li>
            <li>
              Whether an owner is a person or an organisation is worked out from the name: legal forms (LLC, L.P.,
              S.a r.l., SCSp, Berhad, ASA, S.L. and others), words such as Trust, Fund, Plan or Financial, and digits.
              A person whose name contains such a word (“Jane E. Trust”) stays a person when a signature on their
              filings names every word of it in the usual order. It can still be wrong.
            </li>
            <li>
              EDGAR lists people surname first (“COOK TIMOTHY D”). Where a surname ends is clear for most names, but
              not for a name with two surnames (“Paz Montoya Esther”) or one a filer typed in the usual order (“Nick
              Mosich”, “Robert W. Sharps”). A signature on the person’s filings settles it when it spells the same
              name in the usual order; when signatures disagree, the one on more filings wins. A patronymic such as
              Gomez or Rodriguez in second place is read as the second half of a double surname, and how often each
              word is a first name or a surname across all filers decides whether a name was typed first-name first.
              With no such signature, those word counts settle three shapes only: a French surname that starts with
              “Le” (“Le Peuch Olivier” is Olivier Le Peuch, while the Vietnamese “Le Thuy Thi Thu” keeps Le as the
              whole surname), a double surname whose second word is a surname elsewhere and never a first name
              (“Soranno Keating Valerie”), and a first name, one initial and a surname typed in that order (“Daniel
              K Weiss”). Any other such name can still be shown in the wrong order. Two spelling rules: a particle
              keeps the lower case the filer gave it (“van der Valk”, and the Dutch “’t Hart”, filed under H), and
              an apostrophe EDGAR drops is put back for surnames always written with one (“O’Brien”,
              “D’Alessandro”).
            </li>
            <li>
              Late flags counted from a deemed execution date are approximate, because the deemed date is the
              filer’s own report.
            </li>
            <li>
              An amendment with more than one possible original, whose rows don’t settle which one it corrects, isn’t
              applied; it is set aside for review.
            </li>
            <li>A Form 144 without a linked Form 4 doesn’t mean the seller didn’t sell.</li>
            <li>The SEC’s ticker list has current tickers only.</li>
          </ul>
        </Prose>
      </DocSection>

      <DocSection id="does-not-do" title="What this site does not do">
        <Prose>
          <ul>
            <li>
              <strong>It doesn’t give investment advice.</strong> It reports what was disclosed. It doesn’t tell you
              what to buy or sell, and a filing is not a recommendation.
            </li>
            <li>
              <strong>It shows no market prices on public pages</strong>: no price charts, live quotes or returns. The
              prices you see are the ones filers wrote on their forms. Free price data may not be shown publicly;{" "}
              <DocLink href="/sources#prices">the Sources page explains why</DocLink>.
            </li>
            <li>
              <strong>It doesn’t cover Congress yet.</strong> House and Senate trade reports are planned for a later
              phase.
            </li>
            <li>
              <strong>It doesn’t cover 13F holdings or Schedules 13D and 13G yet.</strong> They are planned too. See{" "}
              <DocLink href="/sources">Sources</DocLink> for what is live and what is planned.
            </li>
            <li>
              <strong>It can’t show what public filings don’t contain</strong>: hedge funds’ trades as they happen,
              most short positions, the holdings of wealthy people who aren’t insiders, or the time of day of a trade.
            </li>
            <li>
              <strong>It doesn’t accuse anyone.</strong> Labels such as “late disclosure” describe timing and nothing
              more.
            </li>
          </ul>
          <p>
            To check any number, follow its link to the filing on EDGAR, or see the random{" "}
            <DocLink href="/status#spot-check">spot check</DocLink> on the Status page.
          </p>
        </Prose>
      </DocSection>
    </div>
  );
}
