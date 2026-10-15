/**
 * The watchdog's findings on /status (backend smt/worker/watchdog.py).
 *
 * The watchdog runs every 5 minutes and records problems it notices: a source
 * gone quiet in filing hours, a backlog of filings waiting, new items set
 * aside, a stuck job, a failed run. Open problems come first; ones that
 * cleared in the last 7 days are listed below so a brief outage is still
 * visible afterwards. Severity is shown as a word and a glyph, never color alone.
 *
 * The page is public, so a finding never shows error class names, web
 * addresses or the worker's machine name. The backend writes plain sentences;
 * this file also checks, because findings written by an older version stay
 * listed for 7 days.
 */
import type { WatchdogFindingOut, WatchdogReport } from "@/lib/api/types";
import { dateTimeET } from "@/lib/format";

import { TECHNICAL_FALLBACK, looksTechnical, plainJobNames } from "./jobs";

/** `edge`: a bar down the left side of an open problem, so warnings stand out from notes at a glance. */
const SEVERITY: Record<string, { glyph: string; word: string; className: string; edge: string }> = {
  critical: { glyph: "!!", word: "Critical", className: "text-beacon", edge: "border-l-[3px] border-l-beacon" },
  warning: { glyph: "!", word: "Warning", className: "text-beacon", edge: "border-l-[3px] border-l-beacon" },
  info: { glyph: "i", word: "Note", className: "text-dim", edge: "" },
};

const ALERT_WORDS: Record<string, string> = {
  sent: "alert sent",
  logged: "written to the log",
  pending: "alert waiting to send",
  failed: "alert failed to send",
  skipped: "no alert needed",
  none: "",
};

/** A finding's sentence as the page shows it: job keys become job names, and anything that
 * still reads like a log line (see looksTechnical) is replaced by a plain fallback. */
function plainText(text: string, fallback: string): string {
  return looksTechnical(text) ? fallback : plainJobNames(text);
}

function Finding({ finding }: { finding: WatchdogFindingOut }) {
  const sev = SEVERITY[finding.severity] ?? SEVERITY.info!;
  // An alert state this page doesn't know is left out rather than shown by its internal name.
  const alert = ALERT_WORDS[finding.open_alert] ?? "";
  const title = plainText(finding.title, "The watchdog found a problem.");
  const detail = finding.detail ? plainText(finding.detail, TECHNICAL_FALLBACK) : "";
  return (
    <li className={`rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3 ${finding.cleared_at ? "" : sev.edge}`}>
      <p className="flex items-start gap-2">
        <span className={`w-14 shrink-0 text-[12px] font-medium leading-5 ${sev.className}`}>
          <span aria-hidden>{sev.glyph} </span>
          {sev.word}
        </span>
        <span className="font-medium">{title}</span>
      </p>
      {detail ? <p className="mt-1 text-[13px] leading-[18px] text-dim md:pl-16">{detail}</p> : null}
      <p className="num mt-1 text-[12px] leading-4 text-dim md:pl-16">
        {finding.cleared_at
          ? dateTimeET(finding.opened_at) === dateTimeET(finding.cleared_at)
            ? `At ${dateTimeET(finding.opened_at)}`
            : `From ${dateTimeET(finding.opened_at)} to ${dateTimeET(finding.cleared_at)}`
          : `Since ${dateTimeET(finding.opened_at)}, last seen ${dateTimeET(finding.last_seen_at)}`}
        {alert ? `. ${alert.charAt(0).toUpperCase()}${alert.slice(1)}` : ""}
        {finding.alert_error ? ` (${finding.alert_error})` : ""}
      </p>
    </li>
  );
}

export function WatchdogSection({ report }: { report: WatchdogReport }) {
  return (
    <section id="watchdog" aria-labelledby="watchdog-title" className="scroll-mt-4 px-4 pt-8 md:px-6">
      <h2 id="watchdog-title" className="font-display text-[22px] font-medium leading-7">
        Watchdog
      </h2>
      <p className="mt-1 max-w-[72ch] text-dim">
        Every 5 minutes a check looks for trouble: a source gone quiet during filing hours, filings piling up, items
        set aside, stuck or failed jobs.{" "}
        {report.alert_channel === "apprise"
          ? "Problems are also sent to the owner as alerts."
          : "Problems are written to the worker's log (no alert channel is set up)."}
      </p>
      <p className="num mt-2 text-[13px] text-dim">
        {report.last_run_at ? `Last check ${dateTimeET(report.last_run_at)}.` : "The watchdog hasn't run yet."}
      </p>
      {report.open.length === 0 ? (
        <p className="mt-3 flex items-center gap-2">
          <span aria-hidden className="text-inflow">✓</span>
          No open problems.
        </p>
      ) : (
        <ul className="mt-3 grid gap-3" aria-label="Open problems">
          {report.open.map((f) => (
            <Finding key={f.id} finding={f} />
          ))}
        </ul>
      )}
      {report.recent.length ? (
        <details className="mt-3 rounded-[var(--radius-panel)] border border-hairline bg-console">
          <summary className="flex min-h-11 cursor-pointer items-center px-4 text-[13px]">
            Resolved in the last 7 days ({report.recent.length.toLocaleString("en-US")})
          </summary>
          <ul className="grid gap-3 border-t border-hairline p-3">
            {report.recent.map((f) => (
              <Finding key={f.id} finding={f} />
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
