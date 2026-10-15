"use client";

/**
 * Media-query hooks shared by every page: one definition of "phone" and of
 * "the reader asked for less motion", so layouts can't disagree.
 *
 * The server can't know the screen, so it renders as if the answer were "no"
 * (desktop, motion allowed) and the browser corrects it right after hydration.
 */
import { useCallback, useSyncExternalStore } from "react";

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (callback: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", callback);
      return () => list.removeEventListener("change", callback);
    },
    [query],
  );
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches, () => false);
}

/** The phone breakpoint: below Tailwind's md (768 px). */
export const PHONE_QUERY = "(max-width: 767px)";

/** True below the md breakpoint (768 px). */
export function useIsPhone(): boolean {
  return useMediaQuery(PHONE_QUERY);
}

/** True when the reader asked the system for less motion (no smooth scrolling, no animation). */
export function useReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}

/** The same check outside React (event handlers), for one-off decisions such as a scroll. */
export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
