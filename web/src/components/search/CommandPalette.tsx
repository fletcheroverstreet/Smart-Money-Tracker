"use client";

/**
 * Ctrl+K (or "/" when not typing in a field) command palette: jump to a person, fund, company or ticker.
 *
 * - Results come from /api/search (tickers by prefix, names by fuzzy match).
 * - Typing is debounced 150 ms, so a fast typist sends one request, not ten.
 * - Arrow keys move, Enter opens, Esc closes. The first result is selected, so
 *   typing a name and pressing Enter opens the top hit.
 * - Enter only ever acts on results for the text in the box now. While the
 *   results for that text are still on the way (the 150 ms pause, or the
 *   request is out), the previous list stays on screen dimmed and can't be
 *   chosen. Enter pressed then is remembered, and opens the first result once
 *   it arrives (or the palette says nothing matches). Without this, typing
 *   "SNOW" quickly after a "tesla" search opened Tesla.
 * - With an empty box, a few "Go to" pages are listed. They go as soon as
 *   anything is typed, so Enter can't open one by accident.
 * - Each time the palette opens it starts empty (the box and list live inside
 *   the dialog, which is removed when it closes).
 * - It waits for the page to finish hydrating before it opens (usePageReady).
 *   The open dialog marks the rest of the page aria-hidden, and doing that to
 *   server HTML React hasn't hydrated yet makes React report a mismatch.
 *
 * Accessibility (the WAI-ARIA combobox pattern): the input is a combobox, the
 * list is a listbox, and aria-activedescendant on the input names the selected
 * option, so a screen reader says which result Enter will open. A polite live
 * region says how many results there are and which is first.
 */
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Dialog } from "radix-ui";
import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";

import { apiGet } from "@/lib/api/client";
import type { SearchHit, SearchResults } from "@/lib/api/types";
import { expectNavigationFocus, focusedElement, navigationFocusPending, restoreFocus } from "@/lib/focus";
import { usePageReady } from "@/lib/pageReady";

const PaletteContext = createContext<{ open: () => void }>({ open: () => {} });

/** Any component can open the palette (e.g. the search button in a page header). */
export function usePalette() {
  return useContext(PaletteContext);
}

const PAGES: { label: string; href: string; hint: string }[] = [
  { label: "Live", href: "/", hint: "New disclosures as they arrive" },
  { label: "People", href: "/people", hint: "Insider directory" },
  { label: "Tickers", href: "/tickers", hint: "Companies by recent insider activity" },
  { label: "Status", href: "/status", hint: "Is every source up to date?" },
  { label: "Methodology", href: "/methodology", hint: "How numbers are computed" },
  { label: "Sources", href: "/sources", hint: "Where the data comes from" },
];

const KIND_LABEL: Record<string, string> = {
  ticker: "Ticker",
  person: "Person",
  fund: "Fund",
  bank: "Bank",
  company: "Company",
  index_manager: "Index manager",
};

function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

/** One row of the list: a search result or a "Go to" page. */
interface Option {
  href: string;
  /** The kind column ("Person", "Ticker"); empty for a "Go to" page. */
  kind: string;
  label: string;
  /** Dim text after the label: a person's role and company, or a page's description. */
  detail: string | null;
  bold: boolean;
}

function hitOption(hit: SearchHit): Option {
  const kind = kindLabel(hit.kind);
  return {
    href: hit.href,
    kind,
    label: hit.label,
    // People and funds: their most senior role and its company, the one the People
    // directory shows ("Chief Executive Officer, Apple Inc."), which tells namesakes
    // apart. A sublabel that only repeats the kind column adds nothing.
    detail: hit.sublabel && hit.sublabel !== kind ? hit.sublabel : null,
    bold: hit.kind === "ticker",
  };
}

const PAGE_OPTIONS: Option[] = PAGES.map((page) => ({
  href: page.href,
  kind: "",
  label: page.label,
  detail: page.hint,
  bold: false,
}));

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

/** True when a key press is typing into something: a field, a menu, editable text or a combobox. */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return target.closest("input, select, textarea, [contenteditable]:not([contenteditable='false']), [role='combobox'], [role='textbox']") !== null;
}

export function CommandPaletteProvider({ children }: { children: React.ReactNode }) {
  // `wanted`: the reader asked for the palette. It opens once the page has hydrated
  // (see the file comment); a Ctrl+K pressed before that is kept, not lost.
  const [wanted, setWanted] = useState(false);
  const pageReady = usePageReady();
  const open = useCallback(() => setWanted(true), []);
  const value = useMemo(() => ({ open }), [open]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.key === "k" || event.key === "K") && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        setWanted((v) => !v);
      } else if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !isTypingTarget(event.target)) {
        // A bare "/" only works where it can't be text (WCAG 2.1.4, character key shortcuts).
        event.preventDefault();
        setWanted(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <PaletteContext.Provider value={value}>
      {children}
      <Palette open={wanted && pageReady} onOpenChange={setWanted} />
    </PaletteContext.Provider>
  );
}

function Palette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  // Focus goes back to what had it before the palette opened, or to the new page after a jump.
  const opener = useRef<HTMLElement | null>(null);
  const navigated = useRef(false);

  const go = useCallback(
    (href: string) => {
      navigated.current = true;
      // The new page takes focus once it's on screen (AppShell), not the old page's element.
      expectNavigationFocus(href);
      onOpenChange(false);
      router.push(href);
    },
    [onOpenChange, router],
  );

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-[var(--color-scrim)]" />
        <Dialog.Content
          aria-describedby={undefined}
          onOpenAutoFocus={() => {
            opener.current = focusedElement();
            navigated.current = false;
          }}
          onCloseAutoFocus={(event) => {
            // No trigger button to return to (Ctrl K, "/" or a search box elsewhere), so do it by hand.
            event.preventDefault();
            // After a jump, don't hand focus back to the old page's element (it's about to
            // go). <main> stays across routes, so it holds focus while the new page loads,
            // and AppShell confirms it when the route changes. A jump to the page already
            // open has no route change, so focus goes to that page's return point.
            if (!navigated.current) restoreFocus(opener.current);
            else if (navigationFocusPending()) document.getElementById("main")?.focus({ preventScroll: true });
            else restoreFocus(null);
            opener.current = null;
          }}
          className="fixed left-1/2 top-[12vh] z-50 w-[min(640px,calc(100vw-32px))] -translate-x-1/2 overflow-hidden rounded-[var(--radius-panel)] border border-rule-strong bg-console shadow-[var(--shadow-float)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
        >
          <Dialog.Title className="sr-only">Search people, funds and tickers</Dialog.Title>
          <PaletteBody onGo={go} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** The search box, the list and the keyboard handling. Mounted fresh each time the palette opens. */
function PaletteBody({ onGo }: { onGo: (href: string) => void }) {
  const [text, setText] = useState("");
  const typed = text.trim();
  const query = useDebounced(typed, 150);

  // No placeholderData: `results.data` always belongs to `query`, never to an earlier search.
  const results = useQuery({
    queryKey: ["search", query],
    queryFn: () => apiGet<SearchResults>("/api/search", { q: query }),
    enabled: query.length > 0,
    staleTime: 60_000,
  });

  // Where the results stand against the text in the box right now:
  //   answer:  the results on hand, only when they are for exactly this text (Enter may act on them);
  //   failed:  the search for this text didn't answer;
  //   waiting: the results for this text aren't here yet (the pause, or the request is out).
  const answer = typed !== "" && query === typed ? results.data : undefined;
  const failed = typed !== "" && query === typed && results.isError && answer === undefined;
  const waiting = typed !== "" && answer === undefined && !failed;
  const hits: SearchHit[] = answer?.hits ?? [];

  // The last answered list, kept on screen (dimmed, not choosable) while the next one loads,
  // so the list doesn't flash empty between keystrokes. React's pattern for keeping a value
  // from an earlier render: update state during render, only when it changed.
  const [lastHits, setLastHits] = useState<SearchHit[]>([]);
  if (answer && lastHits !== answer.hits) setLastHits(answer.hits);

  // What the list shows, and whether its rows can be chosen.
  const options: Option[] = typed === "" ? PAGE_OPTIONS : (answer ? hits : waiting ? lastHits : []).map(hitOption);
  const choosable = typed === "" || answer !== undefined;

  // Which row is selected. The reader's choice holds while the list is unchanged; a new
  // list starts on its first row. Nothing is selected in a list that can't be chosen.
  const listKey = `${typed === "" ? "pages" : "hits"}:${options.map((o) => o.href).join("|")}`;
  const [choice, setChoice] = useState<{ list: string; index: number } | null>(null);
  let active = -1;
  if (choosable && options.length > 0) active = choice && choice.list === listKey ? choice.index : 0;

  // Enter pressed while waiting: the text it was pressed for (see the file comment).
  const pendingEnter = useRef<string | null>(null);

  // Once the results for that text arrive, open the first one. With no results the list
  // already says "Nothing matches", and the live region announces it.
  useEffect(() => {
    if (pendingEnter.current === null) return;
    if (pendingEnter.current !== typed || failed) {
      pendingEnter.current = null;
    } else if (answer) {
      pendingEnter.current = null;
      const first = answer.hits[0];
      if (first) onGo(first.href);
    }
  }, [typed, answer, failed, onGo]);

  const ids = useId();
  const listId = `${ids}-list`;
  const headingId = `${ids}-heading`;
  const optionId = (index: number) => `${ids}-option-${index}`;
  const activeId = active >= 0 ? optionId(active) : undefined;

  // Keep the selected row in view as the arrow keys move through a long list.
  useEffect(() => {
    if (activeId) document.getElementById(activeId)?.scrollIntoView({ block: "nearest" });
  }, [activeId, listKey]);

  const move = (step: number) => {
    if (active < 0) return;
    const count = options.length;
    setChoice({ list: listKey, index: (active + step + count) % count }); // wraps around at both ends
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    // Keys that finish composing a character (Japanese or Chinese input, for example) aren't commands.
    if (event.nativeEvent.isComposing) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (active >= 0) onGo(options[active]!.href);
      else if (waiting) pendingEnter.current = typed;
    }
  };

  // Read out politely once results for the current text are in (never for a list still loading).
  let announcement = "";
  if (answer) {
    const first = hits[0];
    announcement = first
      ? `${hits.length} ${hits.length === 1 ? "result" : "results"}, first: ${first.label}, ${kindLabel(first.kind)}.`
      : `Nothing matches ${typed}.`;
  } else if (failed) {
    announcement = "Search didn't answer. Try again in a moment.";
  }

  const heading = typed === "" ? "Go to" : waiting ? "Searching…" : "Results";

  return (
    <>
      <div className="flex items-center gap-2 border-b border-hairline px-4">
        <svg aria-hidden width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-dim">
          <circle cx="7" cy="7" r="4.5" />
          <path d="M10.5 10.5 14 14" />
        </svg>
        <input
          type="text"
          role="combobox"
          aria-label="Search people, funds and tickers"
          aria-autocomplete="list"
          aria-expanded="true"
          aria-controls={listId}
          // The selected row, so a screen reader says what Enter will open. None while loading.
          aria-activedescendant={activeId}
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            // An Enter pressed for the earlier text doesn't carry over to new text.
            pendingEnter.current = null;
          }}
          onKeyDown={onKeyDown}
          placeholder="Search people, funds, tickers"
          className="h-12 min-w-0 flex-1 bg-transparent text-[15px] text-paper outline-none placeholder:text-dim"
        />
        <kbd className="hidden rounded-[var(--radius-chip)] border border-hairline px-1.5 font-mono text-[12px] text-dim sm:inline">
          Esc
        </kbd>
      </div>

      <div className="max-h-[min(60vh,440px)] overflow-y-auto p-2">
        <div id={headingId} className="px-3 py-1.5 text-[12px] text-dim">
          {heading}
        </div>
        {/* Dimmed while waiting: those rows answer the earlier text, so they can't be chosen. */}
        <div
          id={listId}
          role="listbox"
          aria-labelledby={headingId}
          aria-busy={waiting}
          className={waiting ? "opacity-50" : undefined}
        >
          {options.map((option, index) => (
            <div
              key={`${option.kind}:${option.href}:${option.label}`}
              id={optionId(index)}
              role="option"
              aria-selected={index === active}
              aria-disabled={choosable ? undefined : true}
              // Pointer: hovering selects (like the arrow keys), clicking opens. Pressing the
              // mouse button doesn't move focus out of the search box.
              onMouseMove={() => {
                if (choosable && index !== active) setChoice({ list: listKey, index });
              }}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                if (choosable) onGo(option.href);
              }}
              className={`flex items-center gap-3 rounded-[var(--radius-control)] px-3 py-2 ${
                choosable ? "cursor-pointer" : "cursor-default"
              } ${index === active ? "bg-raised" : ""}`}
            >
              {option.kind ? (
                <span className="w-24 shrink-0 text-[12px] text-dim">
                  {option.kind}
                  <span className="sr-only">: </span>
                </span>
              ) : null}
              <span className={option.kind ? (option.bold ? "font-medium" : "") : "w-24 shrink-0"}>{option.label}</span>
              {option.detail ? (
                <span className="min-w-0 truncate text-dim">
                  <span className="sr-only">, </span>
                  {option.detail}
                </span>
              ) : null}
            </div>
          ))}
        </div>
        {answer && hits.length === 0 ? (
          <p className="px-3 py-6 text-center text-dim">Nothing matches &quot;{typed}&quot;.</p>
        ) : null}
        {failed ? <p className="px-3 py-6 text-center text-dim">Search didn&apos;t answer. Try again in a moment.</p> : null}
      </div>

      {/* Screen readers: how many results, and which one Enter opens. */}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <p className="border-t border-hairline px-4 py-2 text-[12px] leading-4 text-dim">
        ↑ ↓ to move, Enter to open. Ctrl K opens search anywhere; so does / when you aren&apos;t typing in a field.
      </p>
    </>
  );
}

/** The search box in page headers: looks like an input, opens the palette. */
export function SearchButton() {
  const { open } = usePalette();
  return (
    <button
      type="button"
      onClick={open}
      className="flex h-11 w-full min-w-0 items-center gap-2 rounded-[var(--radius-control)] border border-rule-strong bg-night px-3 text-left text-dim hover:text-paper sm:w-72 md:h-9"
    >
      <svg aria-hidden width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="7" cy="7" r="4.5" />
        <path d="M10.5 10.5 14 14" />
      </svg>
      <span className="flex-1 truncate">Search people, funds, tickers</span>
      <kbd className="hidden rounded-[var(--radius-chip)] border border-hairline px-1.5 font-mono text-[12px] sm:inline">Ctrl K</kbd>
    </button>
  );
}
