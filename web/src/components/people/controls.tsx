"use client";

/**
 * The People pages' tab strip. It works with the keyboard (arrow keys, Home,
 * End) and has 44 px touch targets. The segmented control lives in
 * components/ui/Segmented.tsx.
 */
import { useRef } from "react";

/** Move focus and selection with the arrow keys, as radio groups and tab lists do. */
function arrowIndex(key: string, current: number, count: number): number | null {
  if (key === "ArrowRight" || key === "ArrowDown") return (current + 1) % count;
  if (key === "ArrowLeft" || key === "ArrowUp") return (current - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

/**
 * A tab strip. `idPrefix` ties each tab to its panel: the panel must have
 * id `${idPrefix}-panel` and aria-labelledby `${idPrefix}-tab-${selected}`.
 * On phones the strip scrolls sideways instead of wrapping.
 */
export function Tabs<T extends string>({
  label,
  idPrefix,
  tabs,
  selected,
  onSelect,
}: {
  label: string;
  idPrefix: string;
  tabs: { id: T; label: string; count?: string }[];
  selected: T;
  onSelect: (id: T) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = Math.max(0, tabs.findIndex((t) => t.id === selected));
  return (
    <div
      role="tablist"
      aria-label={label}
      className="-mb-px flex gap-1 overflow-x-auto"
      onKeyDown={(event) => {
        const next = arrowIndex(event.key, current, tabs.length);
        if (next === null) return;
        event.preventDefault();
        onSelect(tabs[next]!.id);
        refs.current[next]?.focus();
      }}
    >
      {tabs.map((t, i) => (
        <button
          key={t.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          id={`${idPrefix}-tab-${t.id}`}
          type="button"
          role="tab"
          aria-selected={i === current}
          aria-controls={`${idPrefix}-panel`}
          tabIndex={i === current ? 0 : -1}
          onClick={() => onSelect(t.id)}
          className={`flex h-11 shrink-0 items-center gap-2 border-b-2 px-3 ${
            i === current ? "border-paper text-paper" : "border-transparent text-dim hover:text-paper"
          }`}
        >
          {t.label}
          {t.count ? <span className="num text-[12px] text-dim">{t.count}</span> : null}
        </button>
      ))}
    </div>
  );
}
