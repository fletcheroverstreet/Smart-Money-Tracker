/**
 * "Has React finished hydrating the page?" for things that must not run while
 * React is still hydrating the server HTML.
 *
 * Why: a quick look opened from a shared link (?trade=) is a modal dialog, and
 * Radix marks everything outside it aria-hidden. If that happens before a
 * streamed Suspense section (the People directory, say) has hydrated, React
 * finds attributes the server didn't send and reports a hydration mismatch.
 * Timing guesses (one frame, the load event plus an idle moment) weren't
 * enough: React can hydrate a streamed section later than that.
 *
 * So this checks the fact directly. React marks every element it has hydrated
 * with an own property whose name starts with "__reactFiber$". The page is
 * ready once every element inside <main id="main"> carries one. Exceptions:
 *   - elements inside a container marked data-unmanaged-dom (a chart that a
 *     library such as ECharts draws itself) are skipped, as React never owns them;
 *   - React's own streaming placeholders (<template id="B:0">, hidden "S:0"
 *     buffers) are skipped;
 *   - after MAX_WAIT_MS of the page being VISIBLE it counts as ready anyway, so
 *     a missing marker can only delay a shared link, never block it. A tab
 *     opened in the background doesn't hydrate until it is shown, so it waits.
 *
 * usePageReady() returns false on the server, during hydration and until the
 * check passes; after that it stays true for the rest of the visit (client-side
 * navigation doesn't hydrate server HTML again). It is built on
 * useSyncExternalStore, so the server render and the hydration pass both see
 * false (no mismatch from this hook itself).
 */
import { useSyncExternalStore } from "react";

/** How often to check, and the longest wait before going ahead anyway. */
const POLL_MS = 50;
const MAX_WAIT_MS = 5000;
const FIBER_PREFIX = "__reactFiber$";

let ready = false;
let started = false;
const listeners = new Set<() => void>();

function markReady(): void {
  if (ready) return;
  ready = true;
  for (const listener of listeners) listener();
}

function isHydrated(element: Element): boolean {
  return Object.keys(element).some((key) => key.startsWith(FIBER_PREFIX));
}

/** Streaming placeholders React inserts and removes itself: <template id="B:0">, <div hidden id="S:0">. */
function isStreamingPlaceholder(element: Element): boolean {
  return element.tagName === "TEMPLATE" || (element.hasAttribute("hidden") && /^S:/.test(element.id));
}

/** True when React has hydrated every element it manages inside <main>. */
function mainHydrated(): boolean {
  const main = document.getElementById("main");
  if (!main) return true;
  if (!isHydrated(main)) return false;
  for (const element of main.querySelectorAll("*")) {
    if (isStreamingPlaceholder(element) || element.closest("template, [hidden][id^='S:']")) continue;
    const unmanaged = element.parentElement?.closest("[data-unmanaged-dom]");
    if (unmanaged && main.contains(unmanaged)) continue; // drawn by a chart library
    if (!isHydrated(element)) return false;
  }
  return true;
}

/** Starts checking the first time any component asks. */
function start(): void {
  if (started) return;
  started = true;
  // The cap counts only time the page is visible: a tab opened in the background doesn't
  // hydrate until it is shown, and it must keep waiting until then.
  let visibleWaited = 0;
  const check = (): void => {
    if (document.visibilityState === "visible") visibleWaited += POLL_MS;
    if (mainHydrated() || visibleWaited > MAX_WAIT_MS) markReady();
    else window.setTimeout(check, POLL_MS);
  };
  check();
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  start();
  return () => {
    listeners.delete(onChange);
  };
}

const getSnapshot = (): boolean => ready;
const getServerSnapshot = (): boolean => false;

/** True once React has hydrated the page (see the file comment); false on the server. */
export function usePageReady(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
