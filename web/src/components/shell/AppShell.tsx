"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Dialog } from "radix-ui";
import { useEffect, useState } from "react";

import { takeNavigationFocus } from "@/lib/focus";

import { MORE_LINKS, NAV_ITEMS, NavIcon } from "./nav";

/**
 * App frame: a left rail on desktop, a bottom bar on phones, and the page.
 * The footer disclaimer appears on every page (spec §12).
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  const [moreOpen, setMoreOpen] = useState(false);
  const moreItems = NAV_ITEMS.filter((i) => !i.mobile);
  const moreActive = moreItems.some((i) => isActive(i.href)) || MORE_LINKS.some((l) => isActive(l.href));

  // After a jump from the search palette, the new page takes focus: <main>, unless the
  // page has already put focus somewhere inside itself. Ordinary link clicks are left alone.
  useEffect(() => {
    if (!takeNavigationFocus(pathname)) return;
    const main = document.getElementById("main");
    const active = document.activeElement;
    const pageFocusedItself = active !== null && active !== main && main?.contains(active);
    if (main && !pageFocusedItself) main.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <div className="flex min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:bg-console focus:px-3 focus:py-2"
      >
        Skip to content
      </a>

      <nav
        aria-label="Primary"
        className="theme-navy sticky top-0 hidden h-dvh w-[88px] shrink-0 flex-col border-r border-hairline bg-console md:flex"
      >
        <Link href="/" className="flex h-14 items-center gap-2 px-4 font-display text-lg font-semibold tracking-wide">
          <span aria-hidden className="text-dim">◎</span>
          <span>SMT</span>
        </Link>
        <ul className="flex flex-col gap-0.5 px-2">
          {NAV_ITEMS.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={isActive(item.href) ? "page" : undefined}
                className={`flex flex-col items-center gap-1 rounded-[var(--radius-control)] px-1 py-2 text-[12px] leading-4 ${
                  isActive(item.href) ? "bg-raised text-paper" : "text-dim hover:bg-raised/60 hover:text-paper"
                }`}
              >
                <NavIcon name={item.icon} />
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col pb-16 md:pb-0">
        {/* tabIndex -1: the skip link and closing dialogs can move focus here. */}
        <main id="main" tabIndex={-1} className="min-w-0 flex-1">
          {children}
        </main>
        <footer className="border-t border-hairline px-4 py-3 text-[12px] leading-4 text-dim md:px-6">
          Research tool, not investment advice. Disclosures lag trades by law: usually 2 business days for
          insiders, up to 45 days for Congress, longer for late or annual filings.{" "}
          <Link href="/methodology" className="underline decoration-hairline underline-offset-2 hover:text-paper">
            How it works
          </Link>
          <Link href="/sources" className="ml-3 underline decoration-hairline underline-offset-2 hover:text-paper">
            Sources
          </Link>
        </footer>
      </div>

      <nav
        aria-label="Primary (mobile)"
        className="theme-navy fixed inset-x-0 bottom-0 z-40 flex h-16 border-t border-hairline bg-console pb-[env(safe-area-inset-bottom)] md:hidden"
      >
        {NAV_ITEMS.filter((i) => i.mobile).map((item) => (
          <Link
            key={item.href}
            href={item.href}
            aria-current={isActive(item.href) ? "page" : undefined}
            className={`flex min-w-0 flex-1 flex-col items-center justify-center gap-1 text-[12px] ${
              isActive(item.href) ? "text-paper" : "text-dim"
            }`}
          >
            <NavIcon name={item.icon} />
            {item.label}
          </Link>
        ))}
        <Dialog.Root open={moreOpen} onOpenChange={setMoreOpen}>
          <Dialog.Trigger
            className={`flex min-w-0 flex-1 flex-col items-center justify-center gap-1 text-[12px] ${
              moreActive ? "text-paper" : "text-dim"
            }`}
          >
            <NavIcon name="more" />
            More
          </Dialog.Trigger>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-50 bg-[var(--color-scrim)]" />
            <Dialog.Content
              aria-describedby={undefined}
              className="theme-navy fixed inset-x-0 bottom-0 z-50 rounded-t-[var(--radius-panel)] border-t border-rule-strong bg-console px-2 pb-[calc(16px+env(safe-area-inset-bottom))] pt-3 shadow-[var(--shadow-float)] outline-none"
            >
              <Dialog.Title className="px-3 pb-2 text-[12px] font-medium text-dim">More pages</Dialog.Title>
              <ul>
                {[...moreItems.map((i) => ({ href: i.href, label: i.label })), ...MORE_LINKS].map((link) => (
                  <li key={link.href}>
                    <Link
                      href={link.href}
                      onClick={() => setMoreOpen(false)}
                      aria-current={isActive(link.href) ? "page" : undefined}
                      className={`flex h-12 items-center rounded-[var(--radius-control)] px-3 ${
                        isActive(link.href) ? "bg-raised text-paper" : "text-paper hover:bg-raised"
                      }`}
                    >
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      </nav>
    </div>
  );
}
