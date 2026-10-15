"use client";

/**
 * The one segmented control (a row of radio buttons that looks like one
 * control), used by every filter bar on the site.
 *
 * - Radio-group keyboard pattern (roving tabindex): Tab reaches the checked
 *   option only; the arrow keys, Home and End move and select as they go.
 * - Most filters live in the URL, so `value` updates only after router.replace
 *   lands. Moves start from the focused option, and the chosen option shows as
 *   checked straight away (an optimistic "pending" value) until `value` catches
 *   up. Otherwise two quick arrow presses would both start from the old value
 *   and move one step.
 * - 44 px tall on phones (touch target), 32 px from the md breakpoint.
 * - The selected option is marked by more than a fill: a 2 px Paper underline
 *   (11:1 against the control) and Paper text, so it never relies on the
 *   1.2:1 difference between Console and Raised.
 */
import { useRef, useState } from "react";

export interface SegmentOption<T> {
  value: T;
  label: string;
  /** A longer explanation, shown as a tooltip. */
  long?: string;
}

/** The next option index for a key, or null when the key isn't a move. */
function moveIndex(key: string, current: number, count: number): number | null {
  if (key === "ArrowRight" || key === "ArrowDown") return (current + 1) % count;
  if (key === "ArrowLeft" || key === "ArrowUp") return (current - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

export function Segmented<T extends string | number | null>({
  label,
  value,
  options,
  onChange,
  hideLabel = false,
}: {
  label: string;
  value: T;
  options: readonly SegmentOption<T>[];
  onChange: (value: T) => void;
  /** Hide the visible label (the group keeps it as its accessible name). */
  hideLabel?: boolean;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  // The option chosen here that `value` hasn't reflected yet. Wrapped in an object
  // because null is a real option value.
  const [pending, setPending] = useState<{ value: T } | null>(null);
  // When `value` changes (the URL caught up, or something else changed it), the
  // prop is the truth again. Adjusting state during render is React's documented
  // way to reset state on a prop change, without an extra effect pass.
  const [seenValue, setSeenValue] = useState(value);
  if (seenValue !== value) {
    setSeenValue(value);
    setPending(null);
  }
  const shown = pending ? pending.value : value;
  // A value the options don't list (a stale URL) still leaves one option reachable by Tab.
  const selected = Math.max(0, options.findIndex((o) => o.value === shown));

  const choose = (next: T) => {
    if (next !== shown) setPending({ value: next });
    onChange(next);
  };

  return (
    <div className="flex shrink-0 items-center gap-2">
      {hideLabel ? null : (
        <span aria-hidden className="text-[12px] font-medium text-dim">
          {label}
        </span>
      )}
      <div
        role="radiogroup"
        aria-label={label}
        className="flex rounded-[var(--radius-control)] border border-rule-strong p-0.5"
        onKeyDown={(event) => {
          // Start from the option that has focus, not from `value`, which may lag.
          const focused = refs.current.findIndex((el) => el !== null && el === document.activeElement);
          const next = moveIndex(event.key, focused >= 0 ? focused : selected, options.length);
          if (next === null) return;
          event.preventDefault();
          choose(options[next]!.value);
          refs.current[next]?.focus();
        }}
      >
        {options.map((o, i) => {
          const checked = i === selected;
          return (
            <button
              key={String(o.value)}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked ? 0 : -1}
              title={o.long}
              onClick={() => choose(o.value)}
              className={`h-11 min-w-11 whitespace-nowrap rounded-[var(--radius-chip)] px-2.5 text-[13px] md:h-8 md:min-w-0 ${
                checked
                  ? "bg-raised text-paper shadow-[inset_0_-2px_0_var(--color-paper)]"
                  : "text-dim hover:text-paper"
              }`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
