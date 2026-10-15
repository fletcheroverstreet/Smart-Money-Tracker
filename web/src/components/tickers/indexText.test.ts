/**
 * The /tickers ranking's words for trades its dollar totals don't include.
 * Runs with Node's own test runner (`npm test`).
 *
 * The golden case is UMC over 90 days (round 6): four sales, every price in New
 * Taiwan dollars, so the API returns sells 4, sell_value null, trades_left_out 4,
 * sells_left_out 4 and trades_without_price 0.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { incompleteFootnote, incompleteText, sideText } from "./indexText.ts";

const UMC = { trades_without_price: 0, trades_left_out: 4 };

test("UMC's NT$ sales are 'not in dollar totals', never 'no price'", () => {
  assert.deepEqual(sideText("sell", null, 4), { text: "(not in dollar totals)", isNote: true });
  assert.equal(
    incompleteText(UMC),
    "4 left out of dollar totals (other currency or a figure that can't be right)",
  );
  const footnote = incompleteFootnote([UMC]);
  assert.ok(footnote?.startsWith("* Some of these trades are left out of dollar totals"));
  assert.ok(!footnote?.includes("no price"));
});

test("'reported no price' is kept for trades that give no price", () => {
  assert.deepEqual(sideText("buy", null, 0), { text: "(no price reported)", isNote: true });
  const row = { trades_without_price: 2, trades_left_out: 0 };
  assert.equal(incompleteText(row), "2 reported no price");
  assert.equal(incompleteFootnote([row]), "* Some of these trades reported no price, so that company's dollar totals are incomplete.");
});

test("both reasons on one page are both named", () => {
  const row = { trades_without_price: 1, trades_left_out: 3 };
  assert.equal(
    incompleteText(row),
    "1 reported no price; 3 left out of dollar totals (other currency or a figure that can't be right)",
  );
  const footnote = incompleteFootnote([row, { trades_without_price: 0, trades_left_out: 0 }]);
  assert.ok(footnote?.includes("reported no price, and some are left out of dollar totals"));
});

test("a complete company has no mark, and a page of them no footnote", () => {
  const clean = { trades_without_price: 0, trades_left_out: 0 };
  assert.equal(incompleteText(clean), null);
  assert.equal(incompleteFootnote([clean]), null);
  assert.equal(incompleteFootnote([]), null);
});

test("a side's sum is signed, and a sum of 0 is never '+$0'", () => {
  assert.deepEqual(sideText("sell", "16786000.00", 0), { text: "(−$17M)", isNote: false });
  assert.deepEqual(sideText("buy", "58812.00", 1), { text: "(+$59k)", isNote: false });
  // Three purchases filed with 0 shares (e.g. tx 274428) add up to 0: no dollar value, not "+$0".
  assert.deepEqual(sideText("buy", "0.00", 0), { text: "(no dollar value reported)", isNote: true });
});
