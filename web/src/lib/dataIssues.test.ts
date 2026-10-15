/**
 * How a trade's price and value are shown when the filing's figures carry an issue code.
 * Runs with Node's own test runner (`npm test`), no extra packages.
 *
 * The cases are real rows: UMC's Form 4 gives prices in New Taiwan dollars (a footnote
 * says so), and IHT's filer typed each trade's total into the price field.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  CURRENCY_UNSTATED,
  DATE_AFTER_FILING,
  PRICE_NOT_USD,
  VALUE_IMPLAUSIBLE,
  VALUE_LABEL,
  currencyUnstated,
  inOtherCurrency,
  issueNotes,
  plannedAmount,
  priceShown,
  tradeAmount,
  valueCell,
  valueText,
} from "./dataIssues.ts";

// The backend always sets both codes on a price in another currency (smt/ingest/quality.py).
const CURRENCY = [VALUE_IMPLAUSIBLE, PRICE_NOT_USD];

test("a price in another currency is shown as filed, with no dollar sign", () => {
  assert.equal(priceShown("163.00", CURRENCY), "163.00 (filing's currency)");
  assert.equal(priceShown("163.00", []), "$163.00");
  assert.ok(inOtherCurrency(CURRENCY));
  assert.ok(!inOtherCurrency([VALUE_IMPLAUSIBLE]));
});

test("a small dollar price keeps its decimals, and a missing price is a dash", () => {
  assert.equal(priceShown("0.0095", null), "$0.0095");
  assert.equal(priceShown(null, CURRENCY), "– (filing's currency)");
  assert.equal(priceShown(undefined, undefined), "–");
});

test("an open-market trade filed at a price of 0 has no stated price; an award keeps its $0.00", () => {
  // CLMT's cash-settled units (tx 332672): "sold" at 0. The value reads "–", so the price does too.
  assert.equal(priceShown("0.0000", [], "S"), "–");
  assert.equal(priceShown("0", null, "P"), "–");
  assert.equal(tradeAmount("0.00"), "–");
  // An award or gift at 0 is as filed: nothing was paid.
  assert.equal(priceShown("0.0000", [], "A"), "$0.00");
  assert.equal(priceShown("0.0000", [], "G"), "$0.00");
  // A real price is unchanged, whatever the code.
  assert.equal(priceShown("163.00", [], "S"), "$163.00");
  assert.equal(priceShown("163.00", CURRENCY, "S"), "163.00 (filing's currency)");
});

test("the value cell says 'Other currency' for a currency row, not 'Check filing'", () => {
  assert.deepEqual(valueCell("sell", "2445000000", CURRENCY), { kind: "other_currency" });
  assert.deepEqual(valueCell("buy", "5000000000", [VALUE_IMPLAUSIBLE]), { kind: "check_filing" });
});

test("a counted value is an amount; a listed but uncounted one keeps its sign in Dim ink", () => {
  assert.deepEqual(valueCell("buy", "58812", []), { kind: "amount", text: "$59k" });
  assert.deepEqual(valueCell("sell", "1678600", [], false), { kind: "not_counted", text: "−$1.7M" });
  assert.deepEqual(valueCell("buy", null, [], false), { kind: "not_counted", text: "–" });
});

test("a foreign listing with no currency stated says so, and has no dollar sign", () => {
  // Bank Bradesco's "Preference shares - BBDC4" at 17.98: the B3 price, in reais, with no note.
  const unstated = [VALUE_IMPLAUSIBLE, CURRENCY_UNSTATED];
  assert.equal(priceShown("17.98", unstated), "17.98 (currency not stated)");
  assert.deepEqual(valueCell("buy", "5557815.78", unstated), { kind: "currency_unstated" });
  assert.ok(currencyUnstated(unstated));
  // A named currency is the more specific finding.
  assert.ok(!currencyUnstated([VALUE_IMPLAUSIBLE, PRICE_NOT_USD, CURRENCY_UNSTATED]));
});

// ---------------------------------------------------------------- a missing value is "–", never $0

test("a value of 0 or none reads '–', never '+$0' or '−$0'", () => {
  // CLMT tx 332672: 24,555 cash-settled units "sold" at a price of 0 read "−$0" in Live.
  assert.equal(tradeAmount("0.00"), "–");
  assert.equal(tradeAmount(0), "–");
  assert.equal(tradeAmount(null), "–");
  assert.equal(tradeAmount("1630000.00"), "$1.6M");
  assert.deepEqual(valueCell("sell", "0.00", []), { kind: "amount", text: "–" });
  assert.deepEqual(valueCell("buy", "0", [], false), { kind: "not_counted", text: "–" });
  assert.deepEqual(valueText("0.00", []), { kind: "amount", text: "–" });
  assert.deepEqual(valueText(null, []), { kind: "amount", text: "–" });
});

test("a Form 144 notice's market value of 0 reads '–', never '~$0'", () => {
  // Snowflake notice 0001959173-26-007025 (Frederick G Thiel, ~Sep 17, 2026) is filed with a
  // market value of 0 and 0 shares: the Planned sales list, the chart tooltip and the Status
  // spot check read "~$0" while its quick look read "–" (pre-review 7).
  assert.equal(plannedAmount("0.00"), "–");
  assert.equal(plannedAmount(0), "–");
  assert.equal(plannedAmount(null), "–");
  // An estimate keeps its tilde, once.
  assert.equal(plannedAmount("1404000.00"), "~$1.4M");
});

// ---------------------------------------------------------------- the quick look's notes box and Value line

test("a price in another currency: the box says so, without the 'can't be right' note", () => {
  // UMC 0001193125-26-397195: 10,000 shares at 163 New Taiwan dollars (footnote F1).
  const box = issueNotes([VALUE_IMPLAUSIBLE, PRICE_NOT_USD]);
  assert.equal(box?.heading, "This price isn't in dollars");
  assert.deepEqual(box?.notes.map((n) => n.code), [PRICE_NOT_USD]);
  assert.ok(box?.notes.every((n) => !n.text.includes("can't")));
  assert.deepEqual(valueText("1630000.00", [VALUE_IMPLAUSIBLE, PRICE_NOT_USD]), {
    kind: "as_filed",
    text: "1,630,000.00",
    note: "in the filing's currency, not in dollar totals (see above)",
  });
});

test("a foreign listing with no currency stated: the box says so, without the 'can't be right' note", () => {
  // Bank Bradesco's "Preference shares - BBDC4" at 17.98 (reais, with no note on the row).
  const box = issueNotes([VALUE_IMPLAUSIBLE, CURRENCY_UNSTATED]);
  assert.equal(box?.heading, "This price's currency isn't stated");
  assert.deepEqual(box?.notes.map((n) => n.code), [CURRENCY_UNSTATED]);
  assert.ok(box?.notes.every((n) => !n.text.includes("can't")));
  assert.deepEqual(valueText("5557815.78", [VALUE_IMPLAUSIBLE, CURRENCY_UNSTATED]), {
    kind: "as_filed",
    text: "5,557,815.78",
    note: "in a currency the filing doesn't state, not in dollar totals (see above)",
  });
});

test("a figure that can't be right plus a date after the filing: both notes, the general heading", () => {
  const box = issueNotes([VALUE_IMPLAUSIBLE, DATE_AFTER_FILING]);
  assert.equal(box?.heading, "This filing has a figure that can't be right");
  assert.deepEqual(box?.notes.map((n) => n.code), [VALUE_IMPLAUSIBLE, DATE_AFTER_FILING]);
  // A total typed into the price field (the SNOW test case: 50,000 x 16,786,000), shown as filed.
  assert.deepEqual(valueText("839300000000", [VALUE_IMPLAUSIBLE, DATE_AFTER_FILING]), {
    kind: "as_filed",
    text: "$839,300,000,000",
    note: "as filed (can't be right, see above)",
  });
});

test("a currency row with a second issue keeps that note, under the general heading", () => {
  const box = issueNotes([VALUE_IMPLAUSIBLE, PRICE_NOT_USD, DATE_AFTER_FILING]);
  assert.equal(box?.heading, "This filing has a figure that can't be right");
  assert.deepEqual(box?.notes.map((n) => n.code), [PRICE_NOT_USD, DATE_AFTER_FILING]);
});

test("a clean trade has no notes box, and codes outside the box are ignored", () => {
  assert.equal(issueNotes([]), null);
  assert.equal(issueNotes(null), null);
  assert.equal(issueNotes(["amendment_unpaired"]), null);
  assert.deepEqual(valueText("58812", []), { kind: "amount", text: "$59k" });
});

// ---------------------------------------------------------------- the cell words live in one place

/** Every .ts/.tsx file under web/src, except this helper, the tests and generated types. */
function sourceFiles(): string[] {
  const src = join(import.meta.dirname, "..");
  return readdirSync(src, { recursive: true, encoding: "utf8" })
    .filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith(".test.ts") && !f.endsWith(".d.ts"))
    .filter((f) => f.replaceAll("\\", "/") !== "lib/dataIssues.ts")
    .map((f) => join(src, f));
}

test("the value cell's words are spelled only in lib/dataIssues.ts", () => {
  // Round 6: the Live band hard-coded "Check filing" for UMC's New Taiwan dollar rows, while
  // the grid below said "Other currency". Every page must draw these words through valueCell
  // and TradeValue. Comments, and prose quoting a label in curly quotes (the Methodology page
  // explains “Check filing”), don't render a cell, so they are allowed.
  const labels = Object.values(VALUE_LABEL);
  const files = sourceFiles();
  assert.ok(files.some((f) => f.endsWith("TradeValue.tsx")), "the scan must reach the components");
  const offenders: string[] = [];
  for (const file of files) {
    const code = readFileSync(file, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "") // block and JSX comments
      .replace(/(^|[^:])\/\/.*$/gm, "$1") // line comments (not the // in "https://")
      .replace(/“[^”]*”/g, ""); // prose quoting a label
    for (const label of labels) if (code.includes(label)) offenders.push(`${file}: ${label}`);
  }
  assert.deepEqual(offenders, []);
});
