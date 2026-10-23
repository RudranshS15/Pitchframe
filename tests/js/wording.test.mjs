/**
 * The copy rules, pinned where a browser cannot reach.
 *
 * The replay-rate note and the ledger's plurals were written inline at their
 * render sites; these tests hold the decisions themselves — the boundary that
 * says "1 match minute" instead of "60 match seconds", the singular that fires
 * exactly once, and zero's plural. Run: `node --test tests/js/` from the
 * repository root.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { plural, rateNote } from "../../pitchframe/overlay/static/wording.mjs";

/* The page's own speeds — 0.25, 1, 4, 16 times one match minute per second. The
 * default (1x = 60) is the boundary case, and the sentence must be the minute. */
test("the rate note says a minute at the boundary, seconds below it, minutes above it", () => {
  assert.equal(rateNote(60), "1 second = 1 match minute", "the boundary read as a raw number");
  assert.equal(rateNote(15), "1 second = 15 match seconds"); // 0.25×
  assert.equal(rateNote(240), "1 second = 4 match minutes"); // 4×
  assert.equal(rateNote(960), "1 second = 16 match minutes"); // 16×
});

/* The exact rule: === 60 for the minute, < 60 for seconds, everything else
 * rounded to whole minutes. The rounding is the copy's own decision — the note
 * is a sentence for a reader, not a precision instrument. */
test("the minute takes its own sentence by equality, not by rounding", () => {
  assert.equal(rateNote(59.9), "1 second = 59.9 match seconds");
  assert.equal(rateNote(90), "1 second = 2 match minutes", "90 match seconds rounds to a minute and a half of reading");
  assert.equal(rateNote(120), "1 second = 2 match minutes");
});

/* One is singular; zero is plural — it is what the ledger prints when a window
 * caught nothing, and "0 claim" reads as a typo, not a count. */
test("the singular fires exactly once, and zero is plural", () => {
  assert.equal(plural(1, "claim"), "1 claim");
  assert.equal(plural(2, "claim"), "2 claims");
  assert.equal(plural(17, "claim"), "17 claims");
  assert.equal(plural(0, "claim"), "0 claims", "zero read as singular");
  assert.equal(plural(1, "line"), "1 line");
  assert.equal(plural(0, "line"), "0 lines");
});
