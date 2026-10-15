/**
 * The nightly reconciliation job's line on Status. Runs with Node's own test runner (`npm test`).
 *
 * The case is real: run 14238 (2026-09-28 22:12 CDT) stored 155 missing for Sep 28, while the
 * reconciliation table counted the 148 filings the check itself found. The other 7 were already
 * queued by the live feed. Round 6 read the two as conflicting counts.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { reconcileText } from "./reconcile.ts";

test("the job line says 'not held yet', not 'missing', so it can't be read as the table's count", () => {
  const run14238 = {
    "2026-09-24": { missing: 0, expected: 432 },
    "2026-09-25": { missing: 0, expected: 443 },
    "2026-09-28": { missing: 155, expected: 468 },
  };
  assert.equal(
    reconcileText(run14238),
    "Sep 24: 432 in the index, all held; Sep 25: 443 in the index, all held; Sep 28: 468 in the index, 155 not held yet",
  );
});

test("a day whose index couldn't be read says why", () => {
  assert.equal(
    reconcileText({
      "2026-09-24": { error: "no answer", http_status: 503 },
      "2026-09-25": { error: "not posted", http_status: 404 },
      rows_new: 3,
    }),
    "Sep 24: EDGAR didn't answer (HTTP 503); Sep 25: index not posted yet",
  );
});
