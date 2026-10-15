/**
 * One card per data source, from GET /api/meta/sources (backend
 * smt/api/routers/meta.py). The API text is shown as-is; only the terms come
 * from the web side (sourceTerms.ts), because the API doesn't carry them yet.
 *
 * Each card's id is the source key, so links such as /sources#edgar_form4
 * (used by the Status page) land on it.
 */
import { ExternalLink } from "@/components/info/ExternalLink";
import type { SourceOut } from "@/lib/api/types";

import { SOURCE_TERMS } from "./sourceTerms";

/** "sec.gov" from "https://www.sec.gov/..."; the raw text if it isn't a valid URL. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Link style for the links in a card: 44px tall on phones (touch target), inline on larger screens. */
const cardLink =
  "inline-flex min-h-11 items-center gap-1 underline decoration-rule-strong underline-offset-2 hover:decoration-paper md:min-h-0";

/** Live or planned, in words with a glyph (no color-only meaning). */
function StatusChip({ source }: { source: SourceOut }) {
  const live = source.status === "live";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-[var(--radius-chip)] border px-2 py-0.5 text-[12px] leading-4 ${
        live ? "border-rule-strong text-paper" : "border-hairline text-dim"
      }`}
    >
      <span aria-hidden>{live ? "●" : "○"}</span>
      {live ? "In use" : `Planned for phase ${source.phase}`}
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-x-4 gap-y-0.5 py-2 md:grid-cols-[11rem_minmax(0,1fr)]">
      <dt className="text-[12px] font-medium leading-5 text-dim md:text-[13px]">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function SourceCard({ source }: { source: SourceOut }) {
  const terms = SOURCE_TERMS[source.key];
  return (
    <article
      id={source.key}
      aria-labelledby={`${source.key}-title`}
      className="relative scroll-mt-4 rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 id={`${source.key}-title`} className="font-display text-[18px] font-medium leading-6">
          {source.name}
        </h3>
        <StatusChip source={source} />
      </div>
      <dl className="mt-1 divide-y divide-hairline text-[14px] leading-5">
        <Row label="What it shows">{source.reveals}</Row>
        <Row label="Legal deadline">{source.legal_lag}</Row>
        <Row label="How often we check">{source.our_cadence}</Row>
        <Row label="Terms of use">
          {terms ? (
            <>
              <p>{terms.summary}</p>
              <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                {terms.links.map((link) => (
                  <li key={link.href}>
                    <ExternalLink href={link.href} className={cardLink}>
                      {link.label}
                    </ExternalLink>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <span className="text-dim">Not reviewed yet.</span>
          )}
        </Row>
        <Row label="Where it comes from">
          <ExternalLink href={source.url} className={cardLink}>
            Open on {hostOf(source.url)}
          </ExternalLink>
        </Row>
      </dl>
    </article>
  );
}
