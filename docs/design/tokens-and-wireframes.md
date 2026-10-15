# Design tokens and wireframes

Status: **proposed, waiting for your approval.** No UI code gets written until
you approve this sheet (spec §9).

Concept: a deep-space tracking station. People and institutions are objects
under track. Filings are telemetry arriving on a downlink. The disclosure lag
is the signal delay. The boldness goes in one place, the Live feed's downlink
band. Everything else stays quiet, precise and dense.

---

## 1. What I changed from your brief, and why

1. **lodestar no longer looks the way the brief describes.** The brief says
   lodestar uses "phosphor amber and oscilloscope green". Its current theme
   (`lodestar/lodestar/ui/theme.py`) is navy `#0a1628` / `#10203a` with teal
   `#2dd4bf` and red `#ff4d5a`, set in Inter and JetBrains Mono. Your proposed
   Night `#0D1526` and Console `#152038` sit within 4–5 RGB units of lodestar's
   page and panel colors, so they are effectively the same surfaces.
   **I kept them anyway.** Shared surfaces are what make the two apps look
   related. The differences come from four places instead: warm Paper ink
   (lodestar's text is cool), a teal/orange pair (lodestar pairs teal with red),
   condensed display type (lodestar uses Inter), and flat panels separated by
   hairlines (lodestar uses cards). If you want more distance, §2.4 gives an
   indigo-shifted alternative. It passes the same contrast checks.
2. **Added a control-border token.** Hairline `#25324F` is 1.27:1 on Console.
   That's fine for decorative rules, but WCAG 1.4.11 needs input and button
   outlines at 3:1 or more. The new token is Rule-strong `#6878A0`, which
   measures 3.69:1.
3. **No "faint" text color.** Anything dimmer than Dim fails AA for text.
   Secondary text uses Dim. Nothing goes below it.
4. **Errors use Beacon plus a glyph and words.** The palette has no red. A
   red-orange error color would read as "sell".
5. **Chart fills use deeper steps of Inflow and Outflow.** The text colors are
   too light for filled marks (OKLCH L 0.75, above the 0.48–0.67 band for dark
   surfaces). The deeper steps below pass all six palette checks.
6. **Non-polarity series get three reserved-free chart hues:** blue, magenta
   and violet. Teal, orange and yellow already mean buy, sell and alert, so a
   "series 3" in teal would lie. That caps simple categorical charts at three
   series. That's why sector mix over time is drawn as **small multiples**
   (one small area chart per sector) instead of an 11-color stack.
7. **Buy and sell never rely on color.** Every buy has a ▲ and a `+`, every
   sell has a ▼ and a `−`, and tables spell out "Buy" / "Sell". Chart
   markers use different shapes per layer (§4).
8. **Late-filing flags use Dim, not Beacon.** They can appear on many rows,
   and Beacon only works as an alert if it stays rare.
9. **Motion can be paused and codes don't need a mouse.** A visible "Pause
   live updates" control (WCAG 2.2.2), and code explanations that open on
   hover, focus or tap (WCAG 1.4.13).
10. **Mockups use fictional names and show the public view.** This follows
   the licence finding in PLAN.md decision 1. Private-mode additions are
   noted under each frame.
11. **New rows don't push the table down.** New filings land in a
   fixed-height downlink band above the table. The table only takes them in
   when you're scrolled to the top, or when you click "12 new". This keeps
   the table still while you read it, satisfies §10's rule that the stream
   never degrades scrolling, and keeps CLS near zero.

---

## 2. Color

All contrast ratios were computed, not eyeballed (WCAG 2.x relative luminance).
Chart hues were run through the palette validator: OKLCH band, chroma floor,
and CVD separation under simulated protanopia and deuteranopia.

### 2.1 Surfaces and ink

**Light content, navy menu** (the owner's choice on 2026-10-14). The page and
everything in it are light with dark ink; the side rail, the phone's bottom bar
and its "More" sheet stay navy (§2.4). History, so older notes aren't
"restored": navy page `#0D1526` in the first sheet, black `#000000` from
2026-09-24, this light look from 2026-10-14. Every ratio below is WCAG
contrast, computed.

| Token | Hex | Use | Paper on it | Dim on it |
|---|---|---|---|---|
| `night` | `#FFFFFF` | page background | 17.34 | 6.52 |
| `console` | `#F5F7FA` | panels, tables, tiles, dialogs | 16.16 | 6.07 |
| `raised` | `#E9EEF5` | table header, row hover, selected tab, inset wells | 14.87 | 5.59 |
| `hairline` | `#DCE2EB` | decorative rules and panel borders (1.30:1 on white, never a control's only boundary) | – | – |
| `rule-strong` | `#7B869C` | input, select and button outlines; 3.66 on white, 3.41 on console, 3.14 on raised | – | – |
| `paper` | `#111A2E` | primary text (the ink), focus ring | – | – |
| `dim` | `#535E74` | secondary text, axis labels, timestamps; nothing lighter is used for text | – | – |

Depth: dialogs and tooltips float on `--shadow-float` (a soft navy shadow) over a
scrim of `rgb(17 26 46 / 0.42)`; panels are flat, set apart by `console` and a
hairline border.

### 2.2 Meaning colors (reserved)

| Token | Hex | Meaning | On white / console / raised | Rule |
|---|---|---|---|---|
| `inflow` | `#08756B` | buys, additions, new positions (text and glyphs) | 5.57 / 5.19 / 4.78 | always with ▲ / `+` / "Buy" |
| `outflow` | `#AD4A0B` | sells, trims, exits (text and glyphs) | 5.58 / 5.20 / 4.79 | always with ▼ / `−` / "Sell" |
| `inflow-mark` | `#14958A` | chart fills and markers for buys | 3.69 / 3.44 / 3.16 | ≥ 3:1 |
| `outflow-mark` | `#D9692A` | chart fills and markers for sells | 3.50 / 3.26 / 3.00 | ≥ 3:1 |
| `neutral-mark` | `#7D889E` | "unchanged" fill, diverging midpoint | 3.57 / 3.32 / – | deliberately gray |
| `beacon` | `#6B4A00` | alerts, errors, stale-source warnings, possibly-incomplete filings (text; open warnings also get a 3 px left edge). Sparingly. Late-filing flags use Dim ⚑ plus words instead, because they can appear on many rows. | 8.06 / 7.51 / 6.92 | always with a glyph and words |

Distances under simulated color blindness (Machado 2009, full severity; CIE76
ΔE in Lab): Inflow vs Outflow text 44.8 protanopia, 57.7 deuteranopia, 91.0
tritanopia (86.0 normal); the marks 47.3 / 62.7 / 101.8. Beacon vs Outflow 8.1
protanopia, 15.9 deuteranopia (32.3 normal): Beacon is darker than Outflow so
the two stay apart by lightness even where their hues merge, and each always
carries its own glyph and words.

### 2.3 Chart series (non-polarity only)

| Slot | Hex | Typical use | On white / console |
|---|---|---|---|
| 1 | `#2F6FD6` blue | the subject (fund clone, person's portfolio) | 4.81 / 4.48 |
| 2 | `#C23E70` magenta | benchmark (SPY) | 4.98 / 4.64 |
| 3 | `#6E5BD8` violet | second benchmark (sector ETF) | 5.06 / 4.71 |

Slots 1 and 2, and 2 and 3, stay far apart under every simulation (ΔE ≥ 46).
**Cap:** blue and violet are too close for colorblind readers (ΔE 0.9 under
deuteranopia, 7.4 under protanopia), so wherever any two marks can sit side by
side (scatter plots), use two slots, or split into small multiples. Every chart
with two or more series has a legend and direct labels. Every chart has a
"View as table" toggle.

Sequential magnitude, such as a heat grid of holder counts, uses a single-hue
blue ramp. Diverging charts use inflow-mark / neutral-mark / outflow-mark.

### 2.4 The navy menu

The rail, the bottom bar and the "More" sheet carry the class `theme-navy`,
which sets the same tokens back to navy, so their classes (`text-paper`,
`text-dim`, `bg-raised`, the focus ring) read light on navy:

| Token | Hex | Paper on it | Dim on it |
|---|---|---|---|
| `console` | `#152038` | 13.86 | 6.23 |
| `raised` | `#1F2F50` | 11.36 | 5.11 |
| `paper` | `#F1EDE2` | – | – |
| `dim` | `#94A1BB` | – | – |

Beacon `#F4CE5A` (10.67 on console), Inflow `#4FC3B8` (7.59) and Outflow
`#F08A4B` (6.52) keep their navy values there.

---

## 3. Type

Three families, all on Google Fonts and self-hosted through `next/font`.
No sci-fi display faces.

| Role | Family | Weight | Size / line | Where |
|---|---|---|---|---|
| `readout-xl` | Barlow Condensed | 600 | 44 / 48 | Smart Money Score, portfolio value: one per page at most |
| `title` | Barlow Condensed | 600 | 32 / 36 | page title (person, fund, ticker name) |
| `section` | Barlow Condensed | 500 | 22 / 28 | section headings |
| `readout` | Barlow Condensed | 500 | 26 / 30 | key-stat numbers in the header strip |
| `body-l` | IBM Plex Sans | 400 | 16 / 24 | generated summaries, Methodology prose |
| `body` | IBM Plex Sans | 400 | 14 / 20 | default text, comfortable tables |
| `body-dense` | IBM Plex Sans | 400 | 13 / 18 | dense tables (default on desktop) |
| `label` | IBM Plex Sans | 500 | 12 / 16 | column headers, field labels (sentence case, never all caps) |
| `caption` | IBM Plex Sans | 400 | 12 / 16 | timestamps, footnote markers, attributions |
| `id` | IBM Plex Mono | 400 | 12 / 16 | CIKs, accession numbers, DocIDs, CUSIPs only |

- Every numeric column and every readout uses
  `font-variant-numeric: tabular-nums lining-nums` and is right-aligned.
- 12 px is the floor. Nothing smaller.
- Headings are sentence case. No eyebrow labels above them.

---

## 4. Shape, space, depth, motion

**Spacing.** 4 px grid: `0.5`=2, `1`=4, `2`=8, `3`=12, `4`=16, `5`=24, `6`=32,
`7`=48, `8`=64. Row height is 32 px (dense) or 40 px (comfortable). Touch
targets are at least 44 px at phone width.

**Radii scale with hierarchy.** Outer containers are the roundest and inner
pieces are tighter, so nesting reads at a glance.

| Level | Radius |
|---|---|
| App shell, rail, full-bleed tables | 0 |
| Panel | 8 |
| Block nested in a panel (chart well, stat group) | 6 |
| Control (button, input, select, segmented control) | 4 |
| Chip / badge (form type, owner type) | 3 |
| Identifier pill, portrait | 2 |
| Focus ring | element radius + 2 |

**Depth.** Surfaces step Night → Console → Raised. Panels have no shadows.
Overlays (the quick-look drawer, dialogs, the Ctrl+K palette, chart tooltips)
get one shadow token, `--shadow-float` (`0 1px 2px rgb(17 26 46 / .06), 0 12px
32px rgb(17 26 46 / .16)`), a 1 px `rule-strong` border, and the
`--color-scrim` scrim, `rgb(17 26 46 / .42)`. (On the black page they were
`0 24px 64px rgb(3 6 14 / .6)` over `rgb(5 9 18 / .72)`.)

**Focus.** A 2 px Paper outline with a 2 px offset on every focusable element.
It's visible on all three surfaces (≥ 11:1).

**Chart marker shapes, so layers never depend on color:**
insider ▲ / ▼, congressional ■, 13D/13G ◆, Form 144 planned sale ○ (hollow),
13F quarterly net flow as bars under the price pane.

**Motion.** Only the downlink moves by itself.

| Token | Value | Use |
|---|---|---|
| `dur-fast` | 120 ms | hover, press, focus |
| `dur-base` | 180 ms | drawer, tab change, popover |
| `dur-downlink` | 1,100 ms total | new-filing arrival (below) |
| `ease-out` | `cubic-bezier(.16,1,.3,1)` | entrances |
| `ease-in-out` | `cubic-bezier(.65,0,.35,1)` | the carrier sweep |

The arrival sequence runs once per batch, and batches come at most once a
second:

1. The new row fades in inside the downlink band (opacity 0 → 1 and a 6 px
   drop, 240 ms). It only uses `transform` and `opacity`, so nothing is
   re-laid-out.
2. A 1 px carrier line in the row's direction color (inflow-mark or
   outflow-mark) sweeps from left to right under the row (700 ms), like a
   signal locking on.
3. The signal-delay readout ("Filed 2 days after the trade") settles in
   last (160 ms).

With `prefers-reduced-motion`, none of that runs. The row simply appears with
a static "New" marker that clears after 60 s.

---

## 5. Wireframes

Desktop frames are 1440 px wide. Phone notes are for 390 px. `[ ]` is a
control, `▾` a menu, `│` a hairline.

**Every name, ticker and number below is a placeholder.** The companies (ACME,
BOLT, ZNTH) and the people are fictional, so no mockup can be mistaken for real
data. Each frame shows the **public view**. What private mode adds (decision 1
in PLAN.md) is noted under the frame.

### 5.1 Live (home)

```
┌────────┬──────────────────────────────────────────────────────────────────────────────────────┬──────────────────────┐
│ ◎ SMT  │ Live                           [ Search people, funds, tickers    Ctrl K ]           │ Watchlist            │
│        │                                           ● Connected   updated 12 s ago             │                      │
│ Live   ├──────────────────────────────────────────────────────────────────────────────────────┤ ACME                 │
│ People │ Today, Wednesday 23 September                                                        │  2 new filings today │
│ Instit.│ ┌ Largest insider buys ────┐┌ New activist stakes ─┐┌ Congress today ──────┐         │ Doe, Jane            │
│ Tickers│ │ ▲ ACME  R. Ortiz, CEO    ││ ◆ ZNTH  Example Value││ 14 trades disclosed  │         │  1 new trade         │
│ Signals│ │   +$494k   12,000 sh     ││   9.8% of class      ││ by 6 members         │         │ Example Capital      │
│ Watch  │ │ ▲ BOLT  L. Chen, Director││ ◆ QRSX  Sample Ptnrs ││ Median delay 31 days │         │  13F filed today     │
│ Status │ └──────────────────────────┘└──────────────────────┘└──────────────────────┘         │                      │
│        │ ┌ 13F filing season, Q2 2026 ────────────────────────────────────────────────┐       │ Quotes show here in  │
│        │ │ 5,912 of ~8,100 managers have filed. 31 of 44 tracked. Deadline 14 Aug.    │       │ private mode or with │
│        │ └────────────────────────────────────────────────────────────────────────────┘       │ a licensed feed      │
│        │                                                                                      │                      │
│        │ Downlink ────────────────────────────────── 3 arriving now    [ Pause live ]         │                      │
│        │ 16:42:07  Form 4  ▲ Buy   ACME  R. Ortiz, CEO          +$494k    Filed 2 days later  │                      │
│        │ 16:41:55  PTR     ▼ Sell  BOLT  Rep. A. Jones, spouse  $15k–$50k  Filed 38 days later│                      │
│        │ 16:41:30  13D/A   ◆ Up    ZNTH  Example Value LP       9.8% (was 7.1%)               │                      │
│        │ ──────────────────────────────────────────────────────────────────────────────────── │                      │
│        │ [Source ▾] [Section ▾] [Size ▾] [Codes: P and S ▾]  ○ All codes      [ 12 new ]      │                      │
│        │ ┌─────────┬────────┬────────┬──────────────────────┬──────┬─────────┬────────┐       │                      │
│        │ │Detected▾│ Form   │ Action │ Who                  │Ticker│     Size│ Delay  │       │                      │
│        │ ├─────────┼────────┼────────┼──────────────────────┼──────┼─────────┼────────┤       │                      │
│        │ │ 16:40   │ 4      │ ▲ Buy  │ Ortiz, Rosa (CEO)    │ ACME │  +$2.1M │ 2 d    │       │                      │
│        │ │ 16:38   │ 144    │ ○ Plan │ Doe, Jane (Officer)  │ BOLT │  ~$1.4M │  –     │       │                      │
│        │ │  …  virtualized, keyset-paged, j/k to move, Enter opens quick look         │       │                      │
│        │ └─────────┴────────┴────────┴──────────────────────┴──────┴─────────┴────────┘       │                      │
├────────┴──────────────────────────────────────────────────────────────────────────────────────┴──────────────────────┤
│ Research tool, not investment advice. Disclosures lag trades by law: usually 2 days for                              │
│ insiders, up to 45 days for Congress, longer for late or annual filings.                                             │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- Trade delays are in **days**, because disclosures give a trade date and no
  trade time. Only "detected N min after EDGAR accepted it" uses minutes.
- Clicking a row opens the **quick-look drawer** from the right, 480 px wide.
  It shows all three timestamps (traded, disclosed, detected by us), the
  footnotes, the owner type, and a link to the original filing.
- Codes such as "P" have an explanation that opens on hover, keyboard focus
  or tap, and closes with Esc.
- **Private mode adds:** live quotes in the watchlist panel.
- **Phone:** the rail becomes a bottom bar (Live, People, Institutions,
  Tickers, More). The "Today" panels become a horizontal snap-scroll row. The
  watchlist moves under the feed. Table rows become two-line rows: action,
  ticker and size on line one; who and delay on line two. No cards.

### 5.2 Person (a member of Congress; insiders use the same frame)

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ People / Congress / Senate                                                          [ Follow ] │
│ ┌──────┐  Jones, Alice                                                                         │
│ │portr.│  Senator from Washington. Party: Democratic. In office since 2019.                    │
│ │ 2px  │  Committees: Banking, Housing, and Urban Affairs; Armed Services.                     │
│ └──────┘  Public-domain official portrait                                                      │
│                                                                                                │
│ "Alice Jones is a Democratic senator from Washington, in office since 2019, and sits on the    │
│  Banking and Armed Services committees. 212 trades disclosed since 2020; 64% were in a         │
│  spouse or joint account."   Built from stored filings. Sources below.                         │
│                                                                                                │
│  Trades     Spouse/joint   Median delay   Late filings   Paper reports (not machine-read)      │
│  212        64%            31 days        9 of 212       3                                     │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [Trades] [Timeline] [Positions over time] [Committees] [Signals] [Filings]                     │
│ [Owner ▾] [Asset type ▾] [Bracket ▾] [Date range ▾]                                  n = 212   │
│ ┌───────────┬───────────┬──────────┬─────────────────────────┬────────┬────────────┬──────┐    │
│ │ Traded ▾  │ Disclosed │ Owner    │ Asset                   │ Type   │ Amount     │ Doc  │    │
│ ├───────────┼───────────┼──────────┼─────────────────────────┼────────┼────────────┼──────┤    │
│ │ 2026-08-02│ 2026-09-10│ Spouse's │ Acme Corp (ACME)        │ ▼ Sell │ $15k–$50k  │ PTR  │    │
│ │           │ 39 days   │ account  │ Stock                   │        │            │      │    │
│ │ 2026-07-15│ 2026-09-10│ Joint    │ BOLT Call 190, 2027-01  │ ▲ Buy  │ $1k–$15k   │ PTR  │    │
│ │           │ 57 days ⚑ │          │ Option: details in desc │        │            │      │    │
│ └───────────┴───────────┴──────────┴─────────────────────────┴────────┴────────────┴──────┘    │
│ ⚑ Filed later than the legal limit (45 days after the trade, or 30 after notification).        │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- The ⚑ late flag uses Dim plus words, not Beacon. Beacon stays rare.
- The Senate site keeps about 6 years of reports, so this Senate example's
  history starts in 2020. The coverage note on the profile says so.
- **Insider variant.** The header lists roles by company, taken from Forms 3
  and 4 ("Chief Financial Officer, ACME Corp, insider since 2019-03-04, from
  the first Form 3"), plus board seats elsewhere and the share of sales made
  under 10b5-1 plans. The Positions tab plots the reported holdings after
  each transaction, one series per ownership form (direct, and each indirect
  holder such as a trust).
- **Private mode adds** a Performance tab and a stat for follower excess
  return, shown with n. It gives both measures side by side: return from the
  trade date and from the first close after disclosure, each with n and a
  confidence interval.
- **Phone:** the portrait shrinks to 56 px beside the name. The stat strip
  becomes a 2 × 3 grid. The tabs scroll sideways.

### 5.3 Fund

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Institutions / Hedge funds                                     [ Q2 2026 ▾ ]   [ Follow ]      │
│ Example Capital Management, L.P.                                                               │
│ Hedge fund in New York, NY. Managed by Pat Example.                                            │
│ Filer CIK 000XXXXXXX (mono), formerly 000YYYYYYY through 2026 Q1                               │
│ Regulatory AUM (Form ADV): $18.9bn        13F value: $12.4bn in 11 positions                   │
│                                                                                                │
│  13F value   Positions   Top-10 weight   Turnover   Style                                      │
│  $12.4bn     11          98.1%           14%        Concentrated, long holding periods         │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [Holdings] [Changes this quarter] [Sector mix] [13D/13G] [Form 4 as 10% owner] [Filings]       │
│ ┌ Holdings ──────────────────────────────────────────────┐ ┌ Treemap (size = value) ──────────┐│
│ │ Security        Weight  Value    Shares  QoQ     Held  │ │ ┌──────────┬──────┬────┐         ││
│ │ Acme (ACME)     19.8%   $2.46bn  30.3M   ▲ Add   6 q   │ │ │ ACME  ▲  │ BOLT │ ZN │         ││
│ │ Bolt (BOLT)     16.9%   $2.10bn  38.1M   = Hold  9 q   │ │ │          │  =   │ ▼  │         ││
│ │ Zenith (ZNTH)   …       …        …       ▼ Trim  …     │ │ └──────────┴──────┴────┘         ││
│ │ Options rows are listed separately;                    │ │                                  ││
│ │ never added to long exposure.                          │ │                                  ││
│ └────────────────────────────────────────────────────────┘ └──────────────────────────────────┘│
│ Filed 2026-08-14, 45 days after quarter end. This is a quarter-end snapshot, not trades.       │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- The header shows how a CIK chain is displayed. A manager that moved its
  13F filing to a new entity keeps one continuous history.
- In the treemap, color means the QoQ action (inflow-mark, neutral-mark or
  outflow-mark), each cell also carries its ▲ = ▼ glyph, and the treemap has
  a table view.
- A share count that changed by a whole-number ratio with flat value reads
  "shares changed; possibly a split", not "Add +100%".
- The **Banks** tab shows this note in its header: "Bank 13Fs mostly reflect
  client, wealth-management and market-making positions, not directional
  bets. Excluded from smart-money consensus."
- A filing with omitted confidential positions shows a Beacon flag: "This
  filing may be incomplete: the manager requested confidential treatment for
  some positions."
- **Private mode adds** a "Clone performance" tab and a "Clone vs SPY" stat,
  with n quarters.
- **Phone:** the treemap drops below the table and becomes a ranked bar list.
  Quarter selection is a full-width select.

### 5.4 Ticker

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ACME  Acme Corp                                                                     [ Follow ] │
│ Sector: Information Technology                                                                 │
│                                                                                                │
│ ┌ Smart-money events, 2 years   [1M][6M][1Y][2Y][5Y][Max] ─────────────────┐ ┌ Smart Money ──┐ │
│ │ Layers: [▲▼ Insiders ✓] [■ Congress ✓] [◆ 13D/13G ✓] [○ 144] [▮ 13F]     │ │ Score  71     │ │
│ │                                                                          │ │ of 100        │ │
│ │ Insiders  ▲  ▲          ▼ ▼                      ▲                       │ │ Research      │ │
│ │ Congress        ■                  ■  ■                                  │ │ heuristic,    │ │
│ │ 13D/13G                 ◆                                                │ │ not advice    │ │
│ │ 13F flow  ▮▮ ▮  ▮▮▮  ▮   (net shares, all filers)                        │ │ Breakdown:    │ │
│ │           Oct   Jan   Apr   Jul   Oct   Jan   Apr   Jul                  │ │ Cluster buy   │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │  +22, 9 d ago │ │
│                                                                              │ …see all      │ │
│                                                                              └───────────────┘ │
│ [Insiders] [Congress] [Institutional holders] [Planned sales] [Signals]                        │
│  Institutional holders, Q2 2026 (all 13F filers): 4,812 holders, +212 new, −96 exited          │
│  Tracked funds: 9 hold it. Largest: Example Capital (▲ Add 12%), Sample Partners (= Hold)      │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Public view:** an event timeline. Each layer has its own lane on a shared
  date axis, and there's no price line. **Private mode, or a licensed feed,
  adds:** the price line from Lightweight Charts with the same markers drawn
  on it, a quote with its time and source, and the TradingView attribution.
- Hovering or focusing a marker shows who, what, when and the delay.
  Clicking it opens the quick-look drawer.
- Markers on the same day stack vertically so they never overlap.
- **Phone:** the chart stays full width at 260 px tall, and the layer toggles
  become a single "Layers ▾" menu. The score card moves under the chart.
