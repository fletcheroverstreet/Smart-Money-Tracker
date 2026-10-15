/**
 * The profile's wording by filer kind. Runs with Node's own test runner (`npm test`).
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { whoseText } from "./whose.ts";

test("an organisation's profile isn't written from 'this person's' filings", () => {
  // Akastor ASA (/people/akastor-asa-2037057) and Metropolitan Life Insurance Co are companies.
  assert.equal(whoseText("company"), "this organization's");
  assert.equal(whoseText("fund"), "this organization's");
  assert.equal(whoseText("bank"), "this organization's");
  assert.equal(whoseText("index_manager"), "this organization's");
  assert.equal(whoseText("person"), "this person's");
  assert.equal(whoseText("politician"), "this person's");
});
