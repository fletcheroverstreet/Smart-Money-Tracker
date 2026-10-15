import Link from "next/link";

import type { PeopleTab } from "./params";

/**
 * The People sections. Each is its own URL (/people, /people?tab=notables),
 * so these are links that mark the current one, not in-page tabs.
 * Congress and the executive branch join in later phases.
 */
export function PeopleTabs({ current }: { current: PeopleTab }) {
  const tabs: { id: PeopleTab; label: string; href: string }[] = [
    { id: "insiders", label: "Insiders", href: "/people" },
    { id: "notables", label: "Notables", href: "/people?tab=notables" },
  ];
  return (
    <nav aria-label="People sections" className="border-b border-hairline px-4 md:px-6">
      <ul className="-mb-px flex gap-1 overflow-x-auto">
        {tabs.map((t) => (
          <li key={t.id} className="shrink-0">
            <Link
              href={t.href}
              aria-current={t.id === current ? "page" : undefined}
              className={`flex h-11 items-center border-b-2 px-3 ${
                t.id === current ? "border-paper text-paper" : "border-transparent text-dim hover:text-paper"
              }`}
            >
              {t.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/**
 * The Notables tab. Featured people (well-known investors and executives) are
 * added by hand from config/notables.yaml, and only after the site owner has
 * checked that each one is matched to the right SEC filer (PLAN.md Phase 1).
 * None have been reviewed yet, so this says so plainly instead of guessing.
 */
export function NotablesNote() {
  return (
    <section aria-labelledby="notables-title" className="px-4 py-8 md:px-6">
      <div className="max-w-prose rounded-[var(--radius-panel)] border border-hairline bg-console px-5 py-4">
        <h2 id="notables-title" className="font-display text-[22px] font-medium leading-7">
          No featured people yet
        </h2>
        <p className="mt-2 text-[16px] leading-6">
          This tab will list well-known investors and executives. Each one is added by hand, and only after the
          site&apos;s owner has checked that the person is matched to the right SEC filer, so that nobody&apos;s
          trades are shown under the wrong name. None have been reviewed yet.
        </p>
        <p className="mt-3 text-dim">
          In the meantime, every insider who files with the SEC is in the{" "}
          <Link href="/people" className="text-paper underline decoration-hairline underline-offset-2 hover:decoration-paper">
            Insiders
          </Link>{" "}
          tab, and the search box (Ctrl K) finds anyone by name.
        </p>
      </div>
    </section>
  );
}
