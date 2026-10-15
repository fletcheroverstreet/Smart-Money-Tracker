/**
 * The daily prices job's line on Status. Runs with Node's own test runner (`npm test`).
 *
 * The cases are real runs of prices.refresh_daily (ingestion_runs): their "current" is a
 * per-provider object, {} in every run so far, which the line used to drop.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { pricesText, providerTotal } from "./prices.ts";

test("a run with nothing up to date says '0 up to date' and why it was partial", () => {
  // Run 24590 (partial): 4,887 targets, current {}, 659 stale, 4,228 unavailable, mostly_stale.
  const run24590 = { targets: 4887, current: {}, stale: 659, unavailable: 4228, failed: 0, reloaded: 0, mostly_stale: true };
  assert.equal(
    pricesText(run24590),
    "4,887 tickers checked, 0 up to date, 659 behind and 4,228 with no prices available; most prices are out of date",
  );
});

test("a healthy run counts every provider's up-to-date series", () => {
  assert.equal(
    pricesText({ targets: 5099, current: { finlake: 400, yfinance: 23 }, stale: 3, unavailable: 4673, failed: 0, reloaded: 0 }),
    "5,099 tickers checked, 423 up to date, 3 behind and 4,673 with no prices available",
  );
  assert.equal(providerTotal({ finlake: 400, yfinance: 23 }), 423);
  assert.equal(providerTotal(7), 7);
  assert.equal(providerTotal("x"), null);
});

test("a run with nothing to price, one stopped early, and one with no source", () => {
  assert.equal(pricesText({ targets: 0, current: {}, stale: 0 }), "nothing new");
  assert.equal(pricesText({ stopped_early: true }), "nothing loaded; stopped early to respect the provider's limits");
  assert.equal(pricesText({ skipped: "no price provider is configured" }), "no price source is set up");
});
