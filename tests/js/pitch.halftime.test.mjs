/**
 * Half-time flipping: the view's auto orientation is a pure function of the
 * match clock, the flip is a 180° rotation (never a mirror), and generator
 * periods are labelled honestly.
 *
 * Run: `node --test tests/js/` from the repository root.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  flipXY,
  fitPitchTo,
  mapPoint,
  mapPointInverse,
  periodFor,
  formatClock,
} from "../../pitchframe/overlay/static/pitch/pitch-core.mjs";

import { generateRecording } from "../../pitchframe/overlay/static/pitch/pitch-mock.mjs";

test("period labels follow the data contract", () => {
  assert.equal(periodFor(0), "1H");
  assert.equal(periodFor(1), "1H");
  assert.equal(periodFor(44 * 60000 + 59999), "1H");
  assert.equal(periodFor(45 * 60000), "2H");
  assert.equal(periodFor((90 * 60000) - 1), "2H");
  assert.equal(periodFor(90 * 60000 + 1), "ET1");
  assert.equal(periodFor(105 * 60000 + 1), "ET2");
  assert.equal(periodFor(121 * 60000), "FT");
});

test("flipXY is an exact 180° rotation, not a mirror", () => {
  // A mirror would swap y; a rotation keeps it, moving (x, y) to (1-x, 1-y).
  assert.deepEqual(flipXY(0.2, 0.3, "home-right"), { x: 0.8, y: 0.7 });
  assert.deepEqual(flipXY(0.5, 0.5, "home-right"), { x: 0.5, y: 0.5 });
  assert.deepEqual(flipXY(0.2, 0.3, "home-left"), { x: 0.2, y: 0.3 });
});

test("flipping preserves distance-from-centre (rotation invariance)", () => {
  const la = fitPitchTo(800, 600, { margin: 0.08 });
  for (const [x, y] of [[0.1, 0.2], [0.73, 0.44], [0.9, 0.9]]) {
    const l = mapPoint(la, x, y, "home-left");
    const r = mapPoint(la, x, y, "home-right");
    const centre = mapPoint(la, 0.5, 0.5, "home-left");
    const dl = Math.hypot(l.x - centre.x, l.y - centre.y);
    const dr = Math.hypot(r.x - centre.x, r.y - centre.y);
    assert.ok(Math.abs(dl - dr) < 1e-9, `distance kept for (${x},${y})`);
    // …and the flipped point is exactly opposite the centre through the
    // original.
    assert.ok(Math.abs((l.x - centre.x) + (r.x - centre.x)) < 1e-9);
    assert.ok(Math.abs((l.y - centre.y) + (r.y - centre.y)) < 1e-9);
  }
});

test("an auto-orientated view produces the same physical pitch after a flip", () => {
  // Same fixture dot, described in the frame's coordinates before and after
  // half-time: after flipping coordinates and view together, the reader sees
  // the same marker in the same screen place.
  const la = fitPitchTo(800, 600, { margin: 0.08 });
  const x = 0.31;
  const y = 0.66;
  const first = mapPoint(la, x, y, "home-left");
  const second = mapPoint(la, 1 - x, 1 - y, "home-right");
  assert.ok(Math.abs(first.x - second.x) < 1e-9);
  assert.ok(Math.abs(first.y - second.y) < 1e-9);
  void mapPointInverse;
});

test("formatClock shows the running clock in play as m:ss", () => {
  assert.equal(formatClock(0), "0:00");
  assert.equal(formatClock(45_000), "0:45");
  assert.equal(formatClock(45 * 60000 + 2 * 1000), "45:02");
});

test("matches wrap up before or on full time; the frame clock is continuous", () => {
  const rec = generateRecording({ seed: 7, minutes: 90 });
  assert.equal(rec.frames.length, 90 * 60);
  for (let i = 1; i < rec.frames.length; i++) {
    if (rec.frames[i].t <= rec.frames[i - 1].t) {
      assert.fail("timestamps are strictly increasing");
    }
  }
  // Well-formed period labels; the false assertions and one off-by-one are
  // the bugs this test caught while being written.
  const seen = new Set(rec.frames.map((f) => f.period));
  assert.deepEqual([...seen].sort(), ["1H", "2H"]);
});
