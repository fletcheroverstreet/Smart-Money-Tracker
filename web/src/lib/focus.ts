/**
 * Focus hand-back for dialogs that open without a trigger button (the quick
 * look, the search palette). WCAG 2.4.3: when a dialog closes, focus goes back
 * to what opened it, never to <body>.
 */

/** The element that has focus now, or null when nothing does (focus on <body>). */
export function focusedElement(): HTMLElement | null {
  const el = document.activeElement;
  return el instanceof HTMLElement && el !== document.body ? el : null;
}

/**
 * Focus `opener` again if it's still on the page. Otherwise fall back to the
 * page's marked return point (the Live grid has data-focus-return), then to
 * <main>. preventScroll: the page is where the reader left it; don't jump.
 */
export function restoreFocus(opener: HTMLElement | null): void {
  const target =
    opener && opener.isConnected
      ? opener
      : (document.querySelector<HTMLElement>("[data-focus-return]") ?? document.getElementById("main"));
  target?.focus({ preventScroll: true });
}

/*
 * Focus after a jump from the search palette. The palette closes before the new
 * route renders, so handing focus back to the old page's element would leave it
 * on <body> once that element is gone. Instead the palette records where it's
 * going, and AppShell focuses <main> when that pathname arrives. Plain link
 * clicks don't record anything, so they keep the browser's usual behaviour.
 */
let pendingJump: { pathname: string; at: number } | null = null;

/** Only a jump that arrives within this long takes focus; a failed navigation expires. */
const JUMP_TTL_MS = 15_000;

function pathnameOf(href: string): string {
  return new URL(href, window.location.href).pathname;
}

/** The palette is about to navigate to `href`. */
export function expectNavigationFocus(href: string): void {
  pendingJump = { pathname: pathnameOf(href), at: Date.now() };
}

/**
 * True when a recorded jump goes to a different page than the one showing. A
 * jump to the current page has no route change, so it's cleared here and the
 * caller restores focus itself.
 */
export function navigationFocusPending(): boolean {
  if (!pendingJump) return false;
  if (pendingJump.pathname === window.location.pathname) {
    pendingJump = null;
    return false;
  }
  return true;
}

/** Called on every route change: true (once) when this pathname is the palette's jump. */
export function takeNavigationFocus(pathname: string): boolean {
  const jump = pendingJump;
  if (!jump || jump.pathname !== pathname) return false;
  pendingJump = null;
  return Date.now() - jump.at < JUMP_TTL_MS;
}
