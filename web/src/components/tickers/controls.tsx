"use client";

/**
 * The ticker pages' URL updater. The segmented control is in
 * components/ui/Segmented.tsx and the media-query hooks in lib/useMediaQuery.ts.
 */
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

/**
 * Returns `set({ name: value })`, which rewrites those URL parameters in place
 * (null removes one) and keeps every other parameter, including an open quick
 * look (?trade=). It replaces the history entry and keeps the scroll position,
 * so changing a filter doesn't jump the page or flood the Back button.
 */
export function useSetParams() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  return useCallback(
    (changes: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [name, value] of Object.entries(changes)) {
        if (value === null || value === "") next.delete(name);
        else next.set(name, value);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );
}
