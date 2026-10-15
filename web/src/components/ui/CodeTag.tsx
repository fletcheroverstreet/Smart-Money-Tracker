"use client";

import { Tooltip } from "radix-ui";

/**
 * A transaction code with its plain-English meaning, shown on hover, keyboard
 * focus or tap (WCAG 1.4.13: dismissable with Esc, hoverable, persistent).
 *
 * Clicks and keys stay on the tag: it often sits inside a clickable table row,
 * and tapping it to read the meaning must not also open the row's quick look.
 * Inside a single-tab-stop grid, pass tabIndex={-1} so it adds no tab stop.
 *
 * At least 24 x 24 CSS px (WCAG 2.5.8 target size): a one-letter code in a
 * 12 px font would otherwise be about 22 px tall, and the tag often sits next
 * to other links.
 */
export function CodeTag({
  code,
  label,
  tabIndex,
}: {
  code: string | null | undefined;
  label: string | undefined;
  tabIndex?: number;
}) {
  if (!code) return <span className="text-dim">–</span>;
  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button
            type="button"
            tabIndex={tabIndex}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") event.stopPropagation();
            }}
            className="inline-flex min-h-6 min-w-6 items-center justify-center rounded-[var(--radius-chip)] border border-hairline px-1.5 py-0.5 font-mono text-[12px] leading-4 text-dim hover:border-rule-strong hover:text-paper"
            aria-label={`Code ${code}: ${label ?? ""}`}
          >
            {code}
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            sideOffset={6}
            className="z-50 max-w-64 rounded-[var(--radius-control)] border border-rule-strong bg-console px-2.5 py-1.5 text-[12px] leading-4 text-paper shadow-[var(--shadow-float)]"
          >
            <span className="font-mono">{code}</span>: {label}
            <Tooltip.Arrow className="fill-rule-strong" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
