/**
 * Whose filings a profile under /people is written from, in words.
 *
 * Plain functions with no React or "@/" imports, so Node's own test runner can
 * test them (whose.test.ts).
 */

/**
 * "this person's" for a person (a member of Congress is one too), "this organization's" for
 * a company, fund, bank or index manager. Organisations have /people profiles when they file
 * as insiders or Form 144 sellers: Akastor ASA (/people/akastor-asa-2037057, a company) and
 * Metropolitan Life Insurance Co read "Written automatically from this person's filings"
 * before pre-review 7.
 */
export function whoseText(kind: string | null | undefined): string {
  return kind === "person" || kind === "politician" ? "this person's" : "this organization's";
}
