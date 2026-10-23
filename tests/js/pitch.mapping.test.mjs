/**
 * Coordinate mapping tests for the pitch view's pure core.
 *
 * Run: `node --test tests/js/` from the repository root.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  PITCH,
  fitPitchTo,
  flipXY,
  mapPoint,
  mapPointInverse,
} from "../../pitchframe/overlay/static/pitch/pitch-core.mjs";

test("fitPitchTo keeps the 105×68 proportions at any container width", () => {
  for (const [w, h] of [[320, 240], [900, 700], [1400, 500], [180, 1000]]) {
    const la = fitPitchTo(w, h, { margin: 0.04 });
    assert.ok(la.scale > 0);
    // The drawn rect keeps the pitch's proportions within a pixel.
    const ratio = la.drawnW / la.drawnH;
    const expected = PITCH.LENGTH_M / PITCH.WIDTH_M;
    assert.ok(Math.abs(ratio - expected) < 1e-6, `ratio ${ratio} at ${w}×${h}`);
    // It fits inside the box with the requested margin.
    assert.ok(la.drawnW <= w - 2 * 0.04 * Math.min(w, h) + 1e-6);
    assert.ok(la.drawnH <= h - 2 * 0.04 * Math.min(w, h) + 1e-6);
    // And it is centred.
    assert.ok(Math.abs(la.ox - (w - la.drawnW) / 2) < 1e-9);
    assert.ok(Math.abs(la.oy - (h - la.drawnH) / 2) < 1e-9);
  }
});

test("mapPoint places (0,0) at the top-left corner of the drawn rect", () => {
  const la = fitPitchTo(1050, 680, { margin: 0 });
  const p = mapPoint(la, 0, 0, "home-left");
  assert.equal(p.x, la.ox);
  assert.equal(p.y, la.oy);
  const q = mapPoint(la, 1, 1, "home-left");
  assert.ok(Math.abs(q.x - (la.ox + la.drawnW)) < 1e-9);
  assert.ok(Math.abs(q.y - (la.oy + la.drawnH)) < 1e-9);
});

test("mapPoint is linear: the midpoint is the midpoint of the extremes", () => {
  const la = fitPitchTo(500, 400, { margin: 0.1 });
  const a = mapPoint(la, 0, 0, "home-left");
  const b = mapPoint(la, 1, 1, "home-left");
  const m = mapPoint(la, 0.5, 0.5, "home-left");
  assert.ok(Math.abs(m.x - (a.x + b.x) / 2) < 1e-9);
  assert.ok(Math.abs(m.y - (a.y + b.y) / 2) < 1e-9);
});

test("mapPointInverse round-trips mapPoint in both orientations", () => {
  const la = fitPitchTo(720, 560, { margin: 0.15 });
  for (const orientation of ["home-left", "home-right"]) {
    for (const [x, y] of [[0.1, 0.2], [0.5, 0.5], [0.93, 0.87]]) {
      const p = mapPoint(la, x, y, orientation);
      const back = mapPointInverse(la, p.x, p.y, orientation);
      assert.ok(Math.abs(back.x - x) < 1e-9, `${orientation} x:${x}`);
      assert.ok(Math.abs(back.y - y) < 1e-9, `${orientation} y:${y}`);
    }
  }
});

test("points keep their pitch distances across phones of any width", () => {
  // Two points 0.5 apart along the length map 0.5 × 105 m apart, whatever the
  // container — that's what "drawn to scale" means here.
  for (const w of [320, 700, 1600]) {
    const la = fitPitchTo(w, Math.round(w * 0.72), { margin: 0.05 });
    const a = mapPoint(la, 0.2, 0.3, "home-left");
    const b = mapPoint(la, 0.7, 0.3, "home-left");
    const metres = Math.hypot(b.x - a.x, b.y - a.y) / la.scale;
    assert.ok(Math.abs(metres - 0.5 * PITCH.LENGTH_M) < 1e-9, `w=${w} metres=${metres}`);
  }
});
