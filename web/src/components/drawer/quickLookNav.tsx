"use client";

/**
 * Opening and closing the quick look through the URL, without a server render.
 *
 * The drawer is driven by `?trade=<id>` (one trade) or `?event=<id>` (a Live
 * event that isn't a single trade: a Form 144 notice or a Form 3), so every
 * quick look is a shareable link and the browser's Back button closes it.
 *
 * Why the native history API instead of router.push: the pages are rendered
 * on every request (force-dynamic), so router.push would re-render the whole
 * page on the server just to open a drawer. Next.js 16 keeps usePathname and
 * useSearchParams in sync with window.history.pushState/replaceState, so the
 * drawer still reacts, and nothing else reloads.
 *
 * History rules:
 * - Opening from inside the site pushes one entry, marked in history.state,
 *   so closing can simply go Back (no duplicate entries piling up).
 * - Switching what an open drawer shows (a superseded trade, a matched sale)
 *   replaces the entry, so one Back always closes the drawer.
 * - A drawer that arrived in the URL (a shared link) closes by replacing the
 *   entry, so Back doesn't leave the site.
 */
import { usePathname, useSearchParams } from "next/navigation";
import { useCallback } from "react";

export type QuickLookKind = "trade" | "event";

/** The history.state key that marks an entry pushed by openQuickLook. */
const FLAG = "smtQuickLook";

/** `search` with the quick-look parameter set to this item (and any other one removed). */
function searchWith(search: string, kind: QuickLookKind, id: number | null): string {
  const next = new URLSearchParams(search);
  next.delete("trade");
  next.delete("event");
  if (id !== null) next.set(kind, String(id));
  const qs = next.toString();
  return qs ? `?${qs}` : "";
}

function drawerIsOpen(): boolean {
  const params = new URLSearchParams(window.location.search);
  return params.has("trade") || params.has("event");
}

function openedHere(): boolean {
  const state: unknown = window.history.state;
  return typeof state === "object" && state !== null && (state as Record<string, unknown>)[FLAG] === true;
}

/** Show a trade or a feed event in the quick look, keeping every other URL parameter. */
export function openQuickLook(kind: QuickLookKind, id: number): void {
  const url = `${window.location.pathname}${searchWith(window.location.search, kind, id)}`;
  // Only our own key goes in: Next.js copies its internal state over itself.
  if (drawerIsOpen()) window.history.replaceState({ [FLAG]: openedHere() }, "", url);
  else window.history.pushState({ [FLAG]: true }, "", url);
}

/** Close the quick look: Back when this page opened it, otherwise drop the parameter in place. */
export function closeQuickLook(): void {
  if (!drawerIsOpen()) return;
  if (openedHere()) {
    window.history.back();
    return;
  }
  window.history.replaceState({}, "", `${window.location.pathname}${searchWith(window.location.search, "trade", null)}`);
}

/** Open a trade's quick look (keeps the page's other parameters, such as filters). */
export function useOpenTrade(): (tradeId: number) => void {
  return useCallback((tradeId: number) => openQuickLook("trade", tradeId), []);
}

/** Open a feed event's quick look (Form 144 notices and Form 3 statements on Live). */
export function useOpenEvent(): (eventId: number) => void {
  return useCallback((eventId: number) => openQuickLook("event", eventId), []);
}

/** The link address of a quick look on this page, built from the current parameters. */
export function useQuickLookHref(): (kind: QuickLookKind, id: number) => string {
  const pathname = usePathname();
  const params = useSearchParams();
  return useCallback((kind: QuickLookKind, id: number) => `${pathname}${searchWith(params.toString(), kind, id)}`, [pathname, params]);
}

/** The link address of a trade's quick look on this page. */
export function useTradeHref(): (tradeId: number) => string {
  const href = useQuickLookHref();
  return useCallback((tradeId: number) => href("trade", tradeId), [href]);
}

/**
 * A real link to a quick look: it can be opened in a new tab or copied, and a
 * plain click opens the drawer in place without a page load.
 */
export function QuickLookLink({
  kind = "trade",
  id,
  className,
  children,
}: {
  kind?: QuickLookKind;
  id: number;
  className?: string;
  children: React.ReactNode;
}) {
  const href = useQuickLookHref();
  return (
    <a
      href={href(kind, id)}
      className={className}
      onClick={(event) => {
        // Let the browser handle new-tab and new-window clicks.
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        openQuickLook(kind, id);
      }}
    >
      {children}
    </a>
  );
}
