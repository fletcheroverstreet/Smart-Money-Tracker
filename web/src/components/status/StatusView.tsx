"use client";

/**
 * The Status page's moving part (spec §8 and §11): is each source fresh, what
 * did each job do on its last run, what was set aside for review, and what we
 * hold.
 *
 * It refreshes itself every 60 seconds (TanStack Query's refetchInterval),
 * which pauses while the tab is hidden. The server renders the first copy, so
 * the page is complete before any JavaScript runs.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { ExternalLink } from "@/components/info/ExternalLink";
import { ErrorState, SkeletonRows } from "@/components/ui/States";
import { apiGet } from "@/lib/api/client";
import type {
  Coverage,
  DataCoverage,
  DetectionDelay,
  FeedItem,
  FeedPage,
  JobStatus,
  QuarantineRow,
  SourceOut,
  StatusReport,
} from "@/lib/api/types";
import { datesHeldText } from "@/lib/coverage";
import { ago, dateTimeET, day } from "@/lib/format";

import { NEWEST_QUERIES, type NewestKind } from "./queries";
import { HEALTH, type Health, errorLine, healthDetail, healthOf, jobInfo, sortJobs, statsText } from "./jobs";
import { duration, durationWithin, hoursTotal, relative, secondsBetween, useNow } from "./time";
import { WatchdogSection } from "./Watchdog";

const REFRESH_MS = 60_000;

export interface StatusInitial {
  status: StatusReport | null;
  coverage: Coverage | null;
  newestInsider: FeedPage | null;
  newestPlan: FeedPage | null;
  /** Which filing dates the site holds for Forms 3, 4 and 5, with the gaps. */
  datesHeld: DataCoverage | null;
  /** When the server fetched the data above (milliseconds since 1970). */
  fetchedAt: number;
}

/** Which job keeps each source fresh, and which "newest filing" query belongs to it. */
const SOURCE_JOB: Record<string, { job: string; newest?: NewestKind }> = {
  edgar_form4: { job: "edgar.poll_ownership", newest: "insider" },
  edgar_form144: { job: "edgar.poll_form144", newest: "plan" },
  sec_tickers: { job: "edgar.refresh_company_tickers" },
};

// ---------------------------------------------------------------- small pieces

function Section({
  id,
  title,
  lead,
  children,
}: {
  id: string;
  title: string;
  lead?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-4 px-4 pt-8 md:px-6">
      <h2 id={`${id}-title`} className="font-display text-[22px] font-medium leading-7">
        {title}
      </h2>
      {lead ? <p className="mt-1 max-w-[72ch] text-dim">{lead}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function HealthLabel({ health, detail }: { health: Health; detail?: string | null }) {
  const h = HEALTH[health];
  return (
    <span className="inline-flex flex-col">
      <span className={`inline-flex items-center gap-1.5 ${h.className}`}>
        <span aria-hidden className="inline-block w-3 text-center font-medium">
          {h.glyph}
        </span>
        {h.word}
      </span>
      {detail ? <span className="text-[12px] leading-4 text-dim">{detail}</span> : null}
    </span>
  );
}

/** An Eastern time with "3 min ago" under it (the second line appears once the page is running). */
function When({ iso, now, empty = "–" }: { iso: string | null | undefined; now: number | null; empty?: string }) {
  if (!iso) return <span className="text-dim">{empty}</span>;
  return (
    <span className="inline-flex flex-col">
      <time dateTime={iso}>{dateTimeET(iso)}</time>
      {/* A non-breaking space holds the line's height until the clock starts, so nothing shifts. */}
      <span className="text-[12px] leading-4 text-dim">{relative(iso, now) ?? " "}</span>
    </span>
  );
}

function NextRun({ job, now }: { job: JobStatus; now: number | null }) {
  if (!job.enabled) return <span className="text-dim">–</span>;
  if (job.running) return <span>Running now</span>;
  if (now !== null && Date.parse(job.next_run_at) <= now) {
    return (
      <span className="inline-flex flex-col">
        <time dateTime={job.next_run_at}>{dateTimeET(job.next_run_at)}</time>
        <span className="text-[12px] leading-4 text-dim">Due now</span>
      </span>
    );
  }
  return <When iso={job.next_run_at} now={now} />;
}

const RUN_RESULT: Record<string, string> = {
  ok: "Succeeded",
  failed: "Failed",
  partial: "Partly succeeded",
  running: "Still running",
};

function LastRun({ job }: { job: JobStatus }) {
  if (!job.last_status) return <span className="text-dim">No runs recorded yet</span>;
  const stats = statsText(job.name, job.last_stats);
  const error = errorLine(job.last_error);
  const failed = job.last_status === "failed";
  return (
    <span className="inline-flex min-w-0 flex-col gap-0.5">
      <span className={failed ? "text-beacon" : undefined}>
        {failed ? <span aria-hidden>! </span> : null}
        {RUN_RESULT[job.last_status] ?? "Finished"}
        {stats ? <span className="text-dim">: {stats}</span> : null}
      </span>
      {error ? <span className="text-[12px] leading-4 text-dim [overflow-wrap:anywhere]">{error}</span> : null}
    </span>
  );
}

function Tile({ label, value, sub, note }: { label: string; value: React.ReactNode; sub?: React.ReactNode; note: string }) {
  return (
    <div className="flex flex-col rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3">
      <p className="text-[12px] font-medium leading-4 text-dim">{label}</p>
      <p className="num mt-1 font-display text-[26px] font-medium leading-[30px]">{value}</p>
      <p className="num min-h-5 text-[13px] leading-5">{sub ?? " "}</p>
      <p className="mt-auto pt-2 text-[12px] leading-4 text-dim">{note}</p>
    </div>
  );
}

const th = "px-3 py-2 text-left text-[12px] font-medium leading-4 text-dim";
const td = "px-3 py-2.5 align-top";

// ---------------------------------------------------------------- summary

/** "1 filing" / "3 filings". */
function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/**
 * The live goal in words (backend api/routers/status.py, DetectionDelay): how
 * long new Forms 4 and 4/A accepted in filing hours took to reach this site.
 * The 2-minute goal is claimed only when the 95th percentile is within it, and
 * the 95th percentile is rounded up (durationWithin), so 135 s shows as
 * "2 min 15 s", never as "2 min" beside a 2-minute goal it missed.
 * Filings accepted while the worker was down (or within the 2-minute target
 * before it stopped) are counted apart and left out of the figures. Ones the
 * live check missed while it ran count, with their whole wait. The text says both.
 */
function detectionTile(d: DetectionDelay, generatedAt: string): { value: string; sub: string; note: string } {
  const goal = duration(d.target_seconds);
  let sub: string;
  if (d.p95_seconds === null || d.p95_seconds === undefined) sub = "No Form 4s measured yet";
  else if (d.target_met) sub = `95% within ${durationWithin(d.p95_seconds)}, inside the ${goal} goal`;
  else sub = `95% within ${durationWithin(d.p95_seconds)}; the goal is ${goal}`;

  // The window starts at the worker's first heartbeat if that is less than 7 days ago.
  const weekAgo = Date.parse(generatedAt) - d.window_days * 86_400_000;
  const period =
    d.since && Date.parse(d.since) > weekAgo ? `since ${dateTimeET(d.since)}` : `over the last ${d.window_days} days`;
  const parts = [
    `Time from EDGAR accepting a Form 4 or 4/A to it being on this site, for ${plural(d.filings, "filing")} ` +
      `accepted between 6 AM and 10 PM ET on business days ${period}.`,
  ];
  // Outages are the worker's stops that reach into filing hours. A filing can also have
  // waited through a stop outside them (accepted at 9:59 PM, worker stopped overnight), so
  // the filings set apart are told separately and may be there even with no outages.
  if (d.outages) {
    parts.push(
      `The worker was down ${d.outages === 1 ? "once" : `${d.outages.toLocaleString("en-US")} times`} ` +
        `during filing hours (${hoursTotal(d.outage_seconds)} of filing time in all).`,
    );
  }
  const waited = d.filings_during_outages;
  if (waited) {
    parts.push(
      `${plural(waited, "filing")} accepted while the worker was down (or just before it stopped) ` +
        `${waited === 1 ? "isn't" : "aren't"} counted.`,
    );
  } else if (d.outages) {
    parts.push("No filings were accepted while it was down.");
  }
  if (d.found_later) {
    // A filing the live check missed while the worker ran counts, with its whole wait.
    parts.push(
      `${plural(d.found_later, "filing")} the live check missed ${d.found_later === 1 ? "was" : "were"} found later ` +
        `by a check against the SEC's daily index; ${d.found_later === 1 ? "its" : "their"} full wait is counted.`,
    );
  }
  return { value: duration(d.p50_seconds), sub, note: parts.join(" ") };
}

function Summary({ status }: { status: StatusReport }) {
  const behind = status.jobs.filter((j) => j.enabled && j.stale);
  const failed = status.jobs.filter((j) => j.enabled && !j.stale && j.last_status === "failed");
  // An API older than these figures sends no `detection`: show a dash rather than break the page.
  const detection = status.detection
    ? detectionTile(status.detection, status.generated_at)
    : { value: "–", sub: "Not measured yet", note: "How long new Form 4s take to reach this site." };
  const filings = status.counts["filings"];
  const transactions = status.counts["transactions"];

  // The tile is about job timing. Open watchdog problems (prices behind, a source not
  // answering) and runs that finished only partly are named under it, so a tick here is never
  // read as "nothing is wrong" while the list below says otherwise.
  const problems = status.watchdog?.open?.length ?? 0;
  const incomplete = status.jobs.filter((j) => j.enabled && !j.stale && j.last_status === "partial").length;
  const alsoOpen = [
    problems ? `${plural(problems, "open problem")} listed below` : "",
    incomplete ? `${plural(incomplete, "latest run")} incomplete` : "",
  ].filter(Boolean);

  let overall: React.ReactNode;
  let overallSub: React.ReactNode;
  if (behind.length) {
    overall = (
      <span className="text-beacon">
        <span aria-hidden>! </span>
        {behind.length} {behind.length === 1 ? "job" : "jobs"} behind
      </span>
    );
    overallSub = behind.map((j) => jobInfo(j.name).name).join(", ");
  } else if (failed.length) {
    overall = (
      <span className="text-beacon">
        <span aria-hidden>! </span>
        {failed.length} last {failed.length === 1 ? "run" : "runs"} failed
      </span>
    );
    overallSub = failed.map((j) => jobInfo(j.name).name).join(", ");
  } else {
    overall = (
      <span>
        <span aria-hidden>✓ </span>All jobs on schedule
      </span>
    );
    if (alsoOpen.length) {
      overallSub = (
        <a href="#watchdog" className="text-beacon underline decoration-hairline underline-offset-2 hover:decoration-beacon">
          <span aria-hidden>! </span>
          {alsoOpen.join(", ")}
        </a>
      );
    }
  }

  return (
    <div className="grid grid-cols-1 gap-3 px-4 pt-5 sm:grid-cols-2 md:px-6 xl:grid-cols-4">
      <Tile
        label="Overall"
        value={overall}
        sub={overallSub}
        note="Each job has a limit on how long it may go without a successful run. Past it, the job shows as behind. Problems the watchdog found are listed under “Watchdog”."
      />
      <Tile label="New Form 4s, median delay" value={detection.value} sub={detection.sub} note={detection.note} />
      <Tile
        label="Filings held"
        value={filings === undefined ? "–" : filings.toLocaleString("en-US")}
        sub={transactions === undefined ? undefined : `${transactions.toLocaleString("en-US")} transactions in them`}
        note="Every filing stored, including history loaded from the SEC's quarterly data sets."
      />
      <Tile
        label="Set aside for review"
        value={status.quarantine_open.toLocaleString("en-US")}
        sub={status.quarantine_open === 1 ? "open item" : "open items"}
        note="Rows that failed a check. They're kept and listed below, never dropped or guessed at."
      />
    </div>
  );
}

// ---------------------------------------------------------------- live detection by day

/**
 * The detection figures for each business day in the window (backend DetectionDay), newest
 * first, so a change to the collector shows from the day it ran instead of being averaged
 * into the week. Same rules as the tile: filings accepted while the worker was down are
 * set apart; ones the live feed missed while it ran count, with their whole wait.
 */
function DetectionByDay({ detection }: { detection: DetectionDelay | null | undefined }) {
  const days = detection?.days ?? [];
  if (!days.length) return null;
  const goal = duration(detection?.target_seconds ?? 120);
  const secs = (n: number | null | undefined) => (n === null || n === undefined ? "–" : duration(n));
  return (
    <Section
      id="detection"
      title="New Form 4s, day by day"
      lead={`How long Forms 4 and 4/A accepted between 6 AM and 10 PM ET took to reach this site, for each business day. The goal is 95% within ${goal}.`}
    >
      <div className="rounded-[var(--radius-panel)] border border-hairline bg-console md:max-w-4xl">
        {/* Seven columns don't fit a phone (446 px of table in a 358 px box cut the last two
            off): under 768 px each day is a short block with every figure labelled. */}
        <ul className="num divide-y divide-hairline text-[13px] leading-[18px] md:hidden" aria-label="Form 4 detection time by business day, newest first">
          {days.map((d) => {
            const missed = d.p95_seconds !== null && d.p95_seconds !== undefined && d.p95_seconds > (detection?.target_seconds ?? 120);
            return (
              <li key={d.day} className="space-y-0.5 px-3 py-2.5">
                <p className="flex items-baseline justify-between gap-3">
                  <span className="font-medium">{day(d.day)}</span>
                  <span className="text-dim">{d.filings.toLocaleString("en-US")} measured</span>
                </p>
                <p>
                  <span className="text-dim">Median </span>
                  {secs(d.p50_seconds)}
                  <span className="text-dim">, 95% within </span>
                  {d.p95_seconds === null || d.p95_seconds === undefined ? (
                    "–"
                  ) : (
                    <span className={missed ? "text-beacon" : undefined}>
                      {missed ? <span aria-hidden>! </span> : null}
                      {durationWithin(d.p95_seconds)}
                      {missed ? <span className="sr-only">, over the {goal} goal</span> : null}
                    </span>
                  )}
                  <span className="text-dim">, slowest </span>
                  {secs(d.max_seconds)}
                </p>
                <p>
                  <span className="text-dim">Worker down: </span>
                  {d.filings_during_outages ? d.filings_during_outages.toLocaleString("en-US") : "None"}
                  <span className="text-dim">. Found by the check: </span>
                  {d.found_later ? d.found_later.toLocaleString("en-US") : "None"}
                </p>
              </li>
            );
          })}
        </ul>
        <table className="num hidden w-full border-collapse text-[13px] leading-[18px] md:table">
          <caption className="sr-only">Form 4 detection time by business day, newest first</caption>
          <thead className="bg-raised">
            <tr>
              <th scope="col" className={th}>Day</th>
              <th scope="col" className={`${th} text-right`}>Measured</th>
              <th scope="col" className={`${th} text-right`}>Median</th>
              <th scope="col" className={`${th} text-right`}>95% within</th>
              <th scope="col" className={`${th} text-right`}>Slowest</th>
              <th scope="col" className={`${th} text-right`}>Worker down</th>
              <th scope="col" className={`${th} text-right`}>Found by the check</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d) => {
              const missed = d.p95_seconds !== null && d.p95_seconds !== undefined && d.p95_seconds > (detection?.target_seconds ?? 120);
              return (
                <tr key={d.day} className="border-t border-hairline">
                  <th scope="row" className={`${td} text-left font-normal`}>
                    {day(d.day)}
                  </th>
                  <td className={`${td} text-right`}>{d.filings.toLocaleString("en-US")}</td>
                  <td className={`${td} text-right`}>{secs(d.p50_seconds)}</td>
                  <td className={`${td} text-right`}>
                    {d.p95_seconds === null || d.p95_seconds === undefined ? (
                      "–"
                    ) : missed ? (
                      <span className="text-beacon">
                        <span aria-hidden>! </span>
                        {durationWithin(d.p95_seconds)}
                        {/* The "!" and the colour are visual only: say the goal was missed in words. */}
                        <span className="sr-only">, over the {goal} goal</span>
                      </span>
                    ) : (
                      durationWithin(d.p95_seconds)
                    )}
                  </td>
                  <td className={`${td} text-right`}>{secs(d.max_seconds)}</td>
                  <td className={`${td} text-right`}>{d.filings_during_outages ? d.filings_during_outages.toLocaleString("en-US") : "None"}</td>
                  <td className={`${td} text-right`}>{d.found_later ? d.found_later.toLocaleString("en-US") : "None"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="border-t border-hairline px-3 py-2 text-[13px] text-dim">
          Worker down: filings accepted while the collector wasn&apos;t running (or just before it stopped), left
          out of the figures. Found by the check: filings the live feed missed while it ran, found by the check
          against the SEC&apos;s daily index and counted with their whole wait.
        </p>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------- sources

function NewestFiling({ item }: { item: FeedItem | undefined }) {
  if (!item) return <span className="text-dim">None on Live yet</span>;
  // The payload is loosely typed by the API; read the filing link only if it really is a string.
  const url = item.payload["url"];
  const accepted = item.disclosed_at ?? item.detected_at;
  if (typeof url !== "string") return <span>Form {item.form_type}, accepted {dateTimeET(accepted)}</span>;
  return (
    <ExternalLink href={url}>
      Form {item.form_type}, accepted {dateTimeET(accepted)}
    </ExternalLink>
  );
}

function pickupDelay(item: FeedItem | undefined): string {
  if (!item) return "–";
  const seconds = secondsBetween(item.disclosed_at, item.detected_at);
  return seconds === null ? "–" : duration(seconds);
}

function Sources({
  sources,
  jobs,
  newest,
  now,
}: {
  sources: SourceOut[] | null;
  jobs: JobStatus[];
  newest: Record<NewestKind, FeedItem | undefined>;
  now: number | null;
}) {
  // Without the source list (it comes from /api/meta/sources) fall back to the jobs we know.
  const rows: Pick<SourceOut, "key" | "name" | "status" | "phase">[] =
    sources ??
    Object.entries(SOURCE_JOB).map(([key, s]) => ({ key, name: jobInfo(s.job).name, status: "live", phase: 1 }));
  const byName = new Map(jobs.map((j) => [j.name, j]));

  const view = rows.map((source) => {
    const link = SOURCE_JOB[source.key];
    const job = link ? byName.get(link.job) : undefined;
    const item = link?.newest ? newest[link.newest] : undefined;
    return { source, link, job, item };
  });

  return (
    <Section
      id="sources"
      title="Sources"
      lead="When each source was last checked, and the newest filing from it that reached the Live feed. “Picked up after” is how long that filing took to reach this site after EDGAR accepted it."
    >
      {/* Desktop: a table */}
      <div className="hidden overflow-hidden rounded-[var(--radius-panel)] border border-hairline bg-console md:block">
        <table className="num w-full border-collapse text-[13px] leading-[18px]">
          <caption className="sr-only">Freshness of each data source</caption>
          <thead className="bg-raised">
            <tr>
              <th scope="col" className={th}>Source</th>
              <th scope="col" className={th}>State</th>
              <th scope="col" className={th}>Last checked</th>
              <th scope="col" className={th}>Newest filing on Live</th>
              <th scope="col" className={`${th} text-right`}>Picked up after</th>
            </tr>
          </thead>
          <tbody>
            {view.map(({ source, link, job, item }) => (
              <tr key={source.key} className="border-t border-hairline">
                <th scope="row" className={`${td} text-left font-medium`}>
                  <Link href={`/sources#${source.key}`} className="hover:underline">
                    {source.name}
                  </Link>
                </th>
                {source.status !== "live" ? (
                  <>
                    <td className={td}>
                      <HealthLabel health="planned" />
                    </td>
                    <td className={`${td} text-dim`} colSpan={3}>
                      Planned for Phase {source.phase}. Nothing from this source is on the site yet.
                    </td>
                  </>
                ) : (
                  <>
                    <td className={td}>
                      {job ? <HealthLabel health={healthOf(job)} detail={healthDetail(job)} /> : <span className="text-dim">No job found</span>}
                    </td>
                    <td className={td}>
                      <When iso={job?.last_ok_at} now={now} empty="Never" />
                    </td>
                    <td className={td}>
                      {link?.newest ? (
                        <NewestFiling item={item} />
                      ) : (
                        <span className="text-dim">{job ? statsText(job.name, job.last_stats) || "–" : "–"}</span>
                      )}
                    </td>
                    <td className={`${td} text-right`}>{link?.newest ? pickupDelay(item) : <span className="text-dim">–</span>}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Phones: two-line rows */}
      <ul className="num divide-y divide-hairline rounded-[var(--radius-panel)] border border-hairline bg-console text-[13px] leading-[18px] md:hidden">
        {view.map(({ source, link, job, item }) => (
          <li key={source.key} className="px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <Link href={`/sources#${source.key}`} className="font-medium hover:underline">
                {source.name}
              </Link>
              <span className="shrink-0">
                {source.status !== "live" ? (
                  <HealthLabel health="planned" />
                ) : job ? (
                  <HealthLabel health={healthOf(job)} />
                ) : null}
              </span>
            </div>
            {source.status !== "live" ? (
              <p className="mt-1 text-dim">Planned for Phase {source.phase}.</p>
            ) : (
              <div className="mt-1 space-y-1 text-dim">
                <p>
                  Last checked{" "}
                  <span className="text-paper">{job?.last_ok_at ? dateTimeET(job.last_ok_at) : "never"}</span>
                  {job?.last_ok_at && relative(job.last_ok_at, now) ? ` (${relative(job.last_ok_at, now)})` : ""}
                </p>
                {link?.newest ? (
                  <p>
                    Newest: <NewestFiling item={item} />
                    {item ? `, picked up after ${pickupDelay(item)}` : ""}
                  </p>
                ) : job ? (
                  <p>{statsText(job.name, job.last_stats)}</p>
                ) : null}
              </div>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}

// ---------------------------------------------------------------- jobs

function Jobs({ jobs, now, alertChannel }: { jobs: JobStatus[]; now: number | null; alertChannel: string }) {
  const sorted = sortJobs(jobs);
  return (
    <Section
      id="jobs"
      title="Scheduled jobs and their latest run"
      lead="The background worker runs these on a timer. “Last success” is the last run that finished without an error; “Latest run” is what the most recent run did."
    >
      {sorted.length === 0 ? (
        <p className="text-dim">The worker hasn&apos;t registered any jobs yet. It does so the first time it starts.</p>
      ) : (
        <>
          <div className="hidden overflow-hidden rounded-[var(--radius-panel)] border border-hairline bg-console lg:block">
            <table className="num w-full border-collapse text-[13px] leading-[18px]">
              <caption className="sr-only">Scheduled jobs, their state and their latest run</caption>
              <thead className="bg-raised">
                <tr>
                  <th scope="col" className={`${th} w-[30%]`}>Job</th>
                  <th scope="col" className={th}>State</th>
                  <th scope="col" className={th}>Last success</th>
                  <th scope="col" className={th}>Next run</th>
                  <th scope="col" className={`${th} w-[30%]`}>Latest run</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((job) => {
                  const info = jobInfo(job.name, alertChannel);
                  return (
                    <tr key={job.name} className="border-t border-hairline">
                      <th scope="row" className={`${td} text-left font-normal`}>
                        <span className="block font-medium">{info.name}</span>
                        {info.what ? <span className="block text-dim">{info.what}</span> : null}
                        <span className="block text-[12px] leading-4 text-dim">{info.schedule}</span>
                      </th>
                      <td className={td}>
                        <HealthLabel health={healthOf(job)} detail={healthDetail(job)} />
                      </td>
                      <td className={td}>
                        <When iso={job.last_ok_at} now={now} empty="Never" />
                      </td>
                      <td className={td}>
                        <NextRun job={job} now={now} />
                      </td>
                      <td className={td}>
                        <LastRun job={job} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <ul className="num divide-y divide-hairline rounded-[var(--radius-panel)] border border-hairline bg-console text-[13px] leading-[18px] lg:hidden">
            {sorted.map((job) => {
              const info = jobInfo(job.name);
              return (
                <li key={job.name} className="space-y-1 px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <span className="font-medium">{info.name}</span>
                    <span className="shrink-0">
                      <HealthLabel health={healthOf(job)} />
                    </span>
                  </div>
                  <p className="text-dim">{info.schedule}</p>
                  <p className="text-dim">
                    Last success{" "}
                    <span className="text-paper">{job.last_ok_at ? dateTimeET(job.last_ok_at) : "never"}</span>
                    {job.last_ok_at && relative(job.last_ok_at, now) ? ` (${relative(job.last_ok_at, now)})` : ""}
                  </p>
                  <LastRun job={job} />
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- reconciliation

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Five columns at phone width: the cells keep one line each ("Sep 28, 2026" had wrapped to three
 * lines at 390 px) and the padding narrows below md so the table fits a 390 px screen (336 px of
 * 356). On a narrower screen, or with larger counts, the wrapper scrolls sideways instead of
 * clipping.
 */
const recTh = th.replace("px-3", "px-2 md:px-3");
const recTd = `${td.replace("px-3", "px-2 md:px-3")} whitespace-nowrap`;

function Reconciliation({ rows, goalSeconds }: { rows: StatusReport["reconciliation"]; goalSeconds: number }) {
  const view = rows.map((r) => ({
    key: `${String(r["source"])}:${String(r["day"])}`,
    day: typeof r["day"] === "string" ? r["day"] : null,
    expected: num(r["expected"]),
    // What the checks against the index found that the live feed hadn't, split filing by
    // filing by when EDGAR accepted each one (backend routers/status.py, reconciliation_finds):
    // while the worker was collecting (a real miss), or while it was stopped. Both are null on
    // a day the worker wasn't collecting at all (its filings were loaded afterwards).
    missedLive: num(r["missing_at_check"]),
    whileDown: num(r["found_while_down"]),
    stillMissing: num(r["missing"]),
  }));
  const fmt = (n: number | null) => (n === null ? "–" : n.toLocaleString("en-US"));
  const none = (n: number | null) => (n === 0 ? "None" : fmt(n));
  // The outage rule's grace before a stop is the detection goal (backend IN_OUTAGE uses
  // DETECTION_TARGET_SECONDS), so the footnote states the same figure the Form 4 table does.
  const grace = durationWithin(goalSeconds);
  // The two "Found by the check" columns count only what the check found, not every filing
  // accepted during a stop: the live feed's catch-up loads most of those after a restart.
  // Sep 25 read "Accepted while down: None" beside the Form 4 table's "Worker down: 202"
  // (the catch-up had loaded all 202), and Sep 28 148 beside 226 (pre-review 7).
  return (
    <Section
      id="reconciliation"
      title="Nightly reconciliation"
      lead="Every night the worker reads the SEC's official daily index of Forms 3, 4, 5 and 144 and checks each filing against what we hold (and again every hour until the index is posted). Anything missing is queued and fetched. 'Found by the check' counts the filings the check found that the live feed, including its catch-up after a restart, hadn't loaded, split by whether the worker was running when EDGAR accepted each one. A filing accepted while the worker was running is a real miss, and the goal is zero. Filings accepted during a stop that the catch-up loaded after the restart are in neither column. 'Still missing' is what isn't held now."
    >
      {view.length === 0 ? (
        <p className="max-w-[72ch] rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3 text-dim">
          No reconciliation has finished yet. It runs at 11 PM ET, after the SEC publishes the day&apos;s index
          (usually between 10 and 10:30 PM ET). If the index isn&apos;t out yet, that day is checked again every
          hour.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-[var(--radius-panel)] border border-hairline bg-console md:max-w-3xl">
          <table className="num w-full border-collapse text-[13px] leading-[18px]">
            <caption className="sr-only">Reconciliation against the SEC daily index, newest day first</caption>
            {/* Column groups, so the two-column header below (scope="colgroup") has a group to name. */}
            <colgroup span={1} />
            <colgroup span={1} />
            <colgroup span={2} />
            <colgroup span={1} />
            <thead className="bg-raised">
              <tr>
                <th scope="col" rowSpan={2} className={`${recTh} align-bottom`}>Day</th>
                <th scope="col" rowSpan={2} className={`${recTh} text-right align-bottom`}>In the SEC index</th>
                <th scope="colgroup" colSpan={2} className={`${recTh} text-center`}>Found by the check</th>
                <th scope="col" rowSpan={2} className={`${recTh} text-right align-bottom`}>Still missing</th>
              </tr>
              <tr>
                <th scope="col" className={`${recTh} pt-0 text-right`}>Worker running</th>
                <th scope="col" className={`${recTh} pt-0 text-right`}>Worker down</th>
              </tr>
            </thead>
            <tbody>
              {view.map((r) => (
                <tr key={r.key} className="border-t border-hairline">
                  <th scope="row" className={`${recTd} text-left font-normal`}>
                    {day(r.day)}
                  </th>
                  <td className={`${recTd} text-right`}>{fmt(r.expected)}</td>
                  {r.missedLive === null ? (
                    <td className={`${recTd} text-right`} colSpan={2}>
                      <span className="text-dim">Loaded afterwards</span>
                    </td>
                  ) : (
                    <>
                      <td className={`${recTd} text-right`}>
                        {r.missedLive > 0 ? (
                          <span className="text-beacon">
                            <span aria-hidden>! </span>
                            {fmt(r.missedLive)}
                            {/* The "!" and the colour are visual only: say the goal was missed in
                                words, as the day-by-day detection table does. */}
                            <span className="sr-only">, over the goal of zero</span>
                          </span>
                        ) : (
                          none(r.missedLive)
                        )}
                      </td>
                      <td className={`${recTd} text-right`}>{none(r.whileDown)}</td>
                    </>
                  )}
                  <td className={`${recTd} text-right`}>
                    {r.stillMissing === null ? (
                      "–"
                    ) : r.stillMissing > 0 ? (
                      <span className="text-beacon">
                        <span aria-hidden>! </span>
                        {fmt(r.stillMissing)}
                      </span>
                    ) : (
                      <span>
                        <span aria-hidden className="text-dim">✓ </span>None
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="border-t border-hairline px-3 py-2 text-[13px] text-dim">
            {view.some((r) => r.missedLive === null) && (
              <>
                Loaded afterwards: the worker wasn&apos;t collecting at any point of that day&apos;s filing hours, so
                its filings were loaded later from the SEC&apos;s index and there is no live result to compare.{" "}
              </>
            )}
            Worker running or down: the split uses the same rule as the Form 4 figures above (a filing accepted
            while the worker was stopped, or within {grace} before it stopped, counts as down), but these columns
            count only what the check found, so they are not the Form 4 table&apos;s counts. A filing the check
            found is counted once it has loaded; until then it is still missing. The reconciliation job&apos;s own line counts every filing not
            yet held when it ran, including ones the live feed had already queued.
          </p>
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- quarantine

/** Plain words for the reasons the loaders record (backend smt/ingest and smt/sources). */
function reasonText(reason: string): { title: string; explain: string } {
  if (reason.startsWith("4/A row could not be paired"))
    return {
      title: "Amendment row not matched",
      explain:
        "A Form 4/A has a row we couldn't match to exactly one row of the filing it corrects, often because the correction adds a row or renames the security. Both rows stay visible; neither is marked as replaced.",
    };
  if (reason.startsWith("4/A amendment ambiguous"))
    return {
      title: "Amendment not linked",
      explain:
        "More than one earlier filing fits the amendment (same company, owner and filing date), so we didn't guess which one it corrects.",
    };
  if (reason.startsWith("parse error"))
    return {
      title: "Document couldn't be read",
      explain: "The filing's XML didn't match the expected format. The original is stored, so it can be read again once the reader is fixed.",
    };
  if (reason.startsWith("data set load failed"))
    return {
      title: "History row didn't load",
      explain: "A filing in the SEC's quarterly data set failed a check while loading.",
    };
  return { title: reason, explain: "" };
}

function Quarantine({ open, items }: { open: number; items: QuarantineRow[] | null }) {
  const groups = new Map<string, { explain: string; n: number }>();
  for (const item of items ?? []) {
    const { title, explain } = reasonText(item.reason);
    const g = groups.get(title);
    if (g) g.n += 1;
    else groups.set(title, { explain, n: 1 });
  }
  // How many of the loaded items are Form 4/A corrections (both amendment reasons start "4/A").
  const amendments = (items ?? []).filter((i) => i.reason.startsWith("4/A")).length;
  return (
    <Section
      id="quarantine"
      title="Set aside for review"
      lead="When a filing or a row fails a check, it is set aside here instead of being dropped or fixed by guesswork. The rest of the filing still loads."
    >
      <p className="num">
        <span className="font-display text-[26px] font-medium leading-[30px]">{open.toLocaleString("en-US")}</span>{" "}
        <span className="text-dim">{open === 1 ? "item is" : "items are"} waiting for review.</span>
      </p>
      {/* Matches the loader in backend smt/ingest/ownership.py: only unmatched rows and
          ambiguous originals are set aside; a correction whose original we don't hold is not.
          The "most of" line is only shown when the loaded list really says so. */}
      {amendments > 0 && items && amendments * 2 > items.length ? (
        <p className="mt-2 max-w-[72ch] text-[13px] leading-[18px] text-dim">
          {amendments === items.length ? "All" : "Most"} of the latest {items.length.toLocaleString("en-US")} are
          corrections (Form 4/A) we couldn&apos;t tie to the filing they correct with certainty: a corrected row
          didn&apos;t match exactly one original row, or more than one earlier filing could be the original. Rather
          than guess, the site links neither, and both versions stay on the site. A correction whose original is
          older than the history we hold isn&apos;t set aside; it&apos;s shown on its own.
        </p>
      ) : null}
      {items === null ? (
        <p className="mt-2 text-dim">The list of items didn&apos;t load. The count above is current.</p>
      ) : items.length > 0 ? (
        <>
          <h3 className="mt-3 text-[12px] font-medium leading-4 text-dim">
            {open > items.length
              ? `Why, for the latest ${items.length.toLocaleString("en-US")}`
              : "Why they were set aside"}
          </h3>
          <ul className="mt-2 grid gap-3 md:grid-cols-2">
            {[...groups.entries()].map(([title, g]) => (
              <li key={title} className="rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3">
                <p className="num flex items-baseline justify-between gap-3">
                  <span className="font-medium">{title}</span>
                  <span className="shrink-0 text-dim">{g.n.toLocaleString("en-US")}</span>
                </p>
                {g.explain ? <p className="mt-1 text-[13px] leading-[18px] text-dim">{g.explain}</p> : null}
              </li>
            ))}
          </ul>
          <details className="mt-3 rounded-[var(--radius-panel)] border border-hairline bg-console">
            <summary className="flex min-h-11 cursor-pointer items-center px-4 text-[13px]">
              Show the latest {items.length.toLocaleString("en-US")}
              {open > items.length ? ` of ${open.toLocaleString("en-US")}` : ""}
            </summary>
            <ul className="num divide-y divide-hairline border-t border-hairline text-[13px] leading-[18px]">
              {items.map((item) => (
                <li key={item.id} className="grid gap-1 px-4 py-2 md:grid-cols-[200px_minmax(0,1fr)_170px] md:gap-3">
                  {item.filing_url ? (
                    <a
                      href={item.filing_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-[12px] text-dim underline decoration-hairline underline-offset-2 hover:text-paper"
                    >
                      {item.filing_ref ?? "The filing"}
                      <span className="sr-only"> on EDGAR (opens in a new tab)</span>
                    </a>
                  ) : (
                    <span className="font-mono text-[12px] text-dim">{item.filing_ref ?? "No accession"}</span>
                  )}
                  <span className="min-w-0 [overflow-wrap:anywhere]">{item.reason}</span>
                  <span className="text-dim md:text-right">{dateTimeET(item.created_at)}</span>
                </li>
              ))}
            </ul>
          </details>
        </>
      ) : open === 0 ? null : (
        <p className="mt-2 text-dim">The items were resolved since the count was taken.</p>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- coverage

const PARSE_WORDS: Record<string, string> = {
  parsed: "read and loaded",
  quarantined: "set aside",
  pending: "waiting to be read",
  failed: "failed",
};
const QUEUE_WORDS: Record<string, string> = {
  pending: "waiting",
  done: "done",
  failed: "gave up after retries",
  gone: "removed by EDGAR after it was accepted",
};

function CoverageSection({
  coverage,
  datesHeld,
  status,
  now,
}: {
  coverage: Coverage | null;
  datesHeld: DataCoverage | null;
  status: StatusReport;
  now: number | null;
}) {
  const held = datesHeld ? datesHeldText(datesHeld) : null;
  const filings = status.counts["filings"];
  const transactions = status.counts["transactions"];
  const bySource = new Map<string, { status: string; n: number }[]>();
  for (const row of coverage?.documents ?? []) {
    const list = bySource.get(row.source) ?? [];
    list.push({ status: row.parse_status, n: row.documents });
    bySource.set(row.source, list);
  }
  const pending = coverage?.queue.find((q) => q.status === "pending");

  return (
    <Section id="coverage" title="Coverage" lead="What the site collects today, and what happened to each document fetched.">
      <div className="grid gap-3 lg:grid-cols-3">
        <div className="rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3">
          <h3 className="text-[12px] font-medium leading-4 text-dim">Collected now</h3>
          <ul className="mt-2 space-y-1">
            <li>Forms 3, 4 and 5, and their amendments</li>
            <li>Form 144 notices of planned sales, and their amendments</li>
            <li>The SEC&apos;s ticker-to-company list</li>
          </ul>
          <p className="num mt-2 text-[13px] leading-[18px] text-dim">
            {filings === undefined ? "–" : filings.toLocaleString("en-US")} filings held, with{" "}
            {transactions === undefined ? "–" : transactions.toLocaleString("en-US")} transactions.
          </p>
          {held ? (
            <p className="num mt-2 text-[13px] leading-[18px] text-dim">
              Dates held: <span className="text-paper">filed {held}</span>{" "}
              {datesHeld?.gaps.length
                ? "Those business days are being loaded from the SEC's daily indexes; totals for a window that crosses them say so."
                : null}
            </p>
          ) : null}
          <p className="mt-2 text-[13px] leading-[18px] text-dim">
            Not yet: congressional trades, 13F holdings, 13D and 13G stakes.{" "}
            <Link href="/sources" className="underline decoration-rule-strong underline-offset-2 hover:text-paper">
              See what&apos;s planned
            </Link>
            .
          </p>
        </div>

        <div className="rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3">
          <h3 className="text-[12px] font-medium leading-4 text-dim">Documents fetched from EDGAR</h3>
          {coverage === null ? (
            <p className="mt-2 text-dim">This part didn&apos;t load.</p>
          ) : bySource.size === 0 ? (
            <p className="mt-2 text-dim">None yet.</p>
          ) : (
            [...bySource.entries()].map(([source, list]) => {
              const total = list.reduce((s, r) => s + r.n, 0);
              return (
                <div key={source} className="mt-2">
                  <p className="num">
                    <span className="font-display text-[22px] font-medium leading-7">{total.toLocaleString("en-US")}</span>{" "}
                    <span className="text-dim">{source === "edgar" ? "filing documents" : `${source} documents`}</span>
                  </p>
                  <ul className="num mt-1 text-[13px] leading-[18px]">
                    {list.map((r) => (
                      <li key={r.status} className="flex justify-between gap-3">
                        <span className="text-dim">{PARSE_WORDS[r.status] ?? r.status}</span>
                        <span>{r.n.toLocaleString("en-US")}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })
          )}
          <p className="mt-2 text-[12px] leading-4 text-dim">
            Counts the filings fetched one by one. History from the SEC&apos;s quarterly data sets isn&apos;t
            included here.
          </p>
        </div>

        <div className="rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3">
          <h3 className="text-[12px] font-medium leading-4 text-dim">Fetch queue</h3>
          {coverage === null ? (
            <p className="mt-2 text-dim">This part didn&apos;t load.</p>
          ) : coverage.queue.length === 0 ? (
            <p className="mt-2 text-dim">Empty.</p>
          ) : (
            <ul className="num mt-2 text-[13px] leading-[18px]">
              {coverage.queue.map((q) => (
                <li key={q.status} className="flex justify-between gap-3">
                  <span className="text-dim">{QUEUE_WORDS[q.status] ?? q.status}</span>
                  <span>{q.items.toLocaleString("en-US")}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-[12px] leading-4 text-dim">
            {pending && pending.items > 0 && pending.oldest
              ? `The oldest waiting filing was found ${relative(pending.oldest, now) ?? dateTimeET(pending.oldest)}.`
              : "Every filing the checks find waits here until it's fetched, stored and read. Failures are retried with growing gaps."}
          </p>
        </div>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------- the view

export function StatusView({ initial, sources }: { initial: StatusInitial; sources: SourceOut[] | null }) {
  const queryClient = useQueryClient();
  const now = useNow(1000);
  const common = { initialDataUpdatedAt: initial.fetchedAt, refetchInterval: REFRESH_MS } as const;

  const status = useQuery({
    queryKey: ["status", "report"],
    queryFn: () => apiGet<StatusReport>("/api/status"),
    initialData: initial.status ?? undefined,
    ...common,
  });
  const coverage = useQuery({
    queryKey: ["status", "coverage"],
    queryFn: () => apiGet<Coverage>("/api/status/coverage"),
    initialData: initial.coverage ?? undefined,
    ...common,
  });
  const newestInsider = useQuery({
    queryKey: ["status", "newest", "insider"],
    queryFn: () => apiGet<FeedPage>("/api/feed", NEWEST_QUERIES.insider),
    initialData: initial.newestInsider ?? undefined,
    ...common,
  });
  const newestPlan = useQuery({
    queryKey: ["status", "newest", "plan"],
    queryFn: () => apiGet<FeedPage>("/api/feed", NEWEST_QUERIES.plan),
    initialData: initial.newestPlan ?? undefined,
    ...common,
  });

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["status"] });

  if (!status.data) {
    if (status.isPending) {
      return (
        <div aria-busy="true" className="pt-3">
          <span className="sr-only">Loading the latest status…</span>
          <SkeletonRows rows={8} height={44} />
        </div>
      );
    }
    return <ErrorState title="The status report didn't load" detail="The data service didn't answer, so we can't say how fresh the data is right now." />;
  }

  const report = status.data;
  const updatedAt = status.dataUpdatedAt;
  const refreshFailed = status.isError;

  return (
    <div className="pb-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 pt-4 md:px-6">
        <p className="num text-[13px] text-dim">
          Updated{" "}
          <span className="text-paper">{now ? ago(updatedAt, now) : `at ${dateTimeET(new Date(updatedAt).toISOString())}`}</span>.
          This page checks again every minute.
        </p>
        <button
          type="button"
          onClick={refresh}
          disabled={status.isFetching}
          className="inline-flex h-9 items-center rounded-[var(--radius-control)] border border-rule-strong px-3 text-[13px] hover:bg-raised disabled:text-dim max-md:h-11"
        >
          {status.isFetching ? "Checking…" : "Check now"}
        </button>
        <p role="status" className="text-[13px] text-beacon">
          {refreshFailed ? (
            <>
              <span aria-hidden>! </span>The last check didn&apos;t get an answer. The numbers below are from the last
              good one.
            </>
          ) : null}
        </p>
      </div>

      <Summary status={report} />
      <WatchdogSection report={report.watchdog} />
      <DetectionByDay detection={report.detection} />
      <Sources
        sources={sources}
        jobs={report.jobs}
        newest={{ insider: newestInsider.data?.items[0], plan: newestPlan.data?.items[0] }}
        now={now}
      />
      <Jobs jobs={report.jobs} now={now} alertChannel={report.watchdog.alert_channel} />
      <Reconciliation rows={report.reconciliation} goalSeconds={report.detection?.target_seconds ?? 120} />
      <Quarantine open={report.quarantine_open} items={coverage.data?.quarantine ?? null} />
      <CoverageSection coverage={coverage.data ?? null} datesHeld={initial.datesHeld} status={report} now={now} />
    </div>
  );
}
