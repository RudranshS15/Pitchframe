/**
 * Interpolation + glide tests: markers move continuously through frames,
 * never snap, and walk — not teleport — to a late-arriving snapshot.
 *
 * Run: `node --test tests/js/` from the repository root.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  GLIDE_CAPS,
  buildEntityIndex,
  glideTowards,
  playerPositionAt,
  ballPositionAt,
} from "../../pitchframe/overlay/static/pitch/pitch-core.mjs";

function frame(t, players, ball = { x: 0.5, y: 0.5, z: 0 }) {
  return { t, period: "1H", ball, players };
}

function p(id, x, y, extra = {}) {
  return { id, teamId: "home", shirtNumber: 1, name: id, x, y, isGoalkeeper: false, ...extra };
}

test("a player inside a frame interval interpolates linearly", () => {
  const frames = [frame(0, [p("a", 0.1, 0.1)]), frame(1000, [p("a", 0.2, 0.3)])];
  const idx = buildEntityIndex(frames);
  const mid = playerPositionAt(idx, "a", 500, { holdMs: 2000, fadeMs: 800 });
  assert.ok(Math.abs(mid.x - 0.15) < 1e-9);
  assert.ok(Math.abs(mid.y - 0.2) < 1e-9);
  assert.equal(mid.alpha, 1);
});

test("the playhead inside a frame is clamped, never overshoots to the next", () => {
  const frames = [frame(0, [p("a", 0.1, 0)]), frame(1000, [p("a", 0.9, 0)])];
  const idx = buildEntityIndex(frames);
  const at899 = playerPositionAt(idx, "a", 899, { holdMs: 2000, fadeMs: 800 });
  assert.ok(at899.x < 0.9);
  const at1000 = playerPositionAt(idx, "a", 1000, { holdMs: 2000, fadeMs: 800 });
  assert.ok(Math.abs(at1000.x - 0.9) < 1e-9);
});

test("a missing player holds their last position while inside the stale window", () => {
  // Player "gone" appears only in frame 0; at t=1500 (within holdMs) they are
  // still shown, held where they were last seen.
  const frames = [frame(0, [p("gone", 0.4, 0.4), p("ok", 0.1, 0.1)]), frame(1000, [p("ok", 0.2, 0.1)])];
  const idx = buildEntityIndex(frames);
  const held = playerPositionAt(idx, "gone", 1500, { holdMs: 2000, fadeMs: 800 });
  assert.ok(Math.abs(held.x - 0.4) < 1e-9);
  assert.equal(held.alpha, 1);
  // Past the window the same held position fades out; the position itself
  // never changes while it fades.
  const fading = playerPositionAt(idx, "gone", 2400, { holdMs: 2000, fadeMs: 800 });
  assert.ok(Math.abs(fading.x - 0.4) < 1e-9);
  assert.ok(fading.alpha < 1, "fading");
  assert.equal(fading.alpha, 1 - (2400 - 2000) / 800);
  const goneForGood = playerPositionAt(idx, "gone", 3200, { holdMs: 2000, fadeMs: 800 });
  assert.equal(goneForGood.alpha, 0);
  assert.equal(goneForGood.state, "gone");
});

test("one-sighting players hold dead still rather than being guessed forward", () => {
  const frames = [frame(0, [p("a", 0.5, 0.5)])];
  const idx = buildEntityIndex(frames);
  const q = playerPositionAt(idx, "a", 1500, { holdMs: 2000, fadeMs: 800 });
  assert.ok(Math.abs(q.x - 0.5) < 1e-9);
  assert.ok(Math.abs(q.y - 0.5) < 1e-9);
});

test("sparse feeds still see one player per query, ball included", () => {
  const frames = [
    frame(0, [p("a", 0.1, 0.1)], { x: 0.5, y: 0.5 }),
    frame(1000, [p("b", 0.4, 0.4)], { x: 0.55, y: 0.5, z: 0.5 }),
  ];
  const idx = buildEntityIndex(frames);
  const a = playerPositionAt(idx, "a", 500, { holdMs: 2000, fadeMs: 800 });
  // a is missing from frame 1000, so at 500 they hold at their last seen spot.
  assert.ok(Math.abs(a.x - 0.1) < 1e-9);
  // b's only sighting is at t=1000 with no previous sighting to drift on, so
  // it holds dead still at 0.4 through the 2s window, fades to 0.375 alpha at
  // t=3500 (500ms into the fade), and the spot never changes.
  const b = playerPositionAt(idx, "b", 3500, { holdMs: 2000, fadeMs: 800 });
  assert.ok(Math.abs(b.x - 0.4) < 1e-9);
  assert.ok(b.alpha === 1 - 500 / 800);
  const ball = ballPositionAt(idx, 500, { holdMs: 2000, fadeMs: 800 });
  assert.ok(Math.abs(ball.x - 0.525) < 1e-9);
});

test("glideTowards rate-limits movement: a snapshot jump becomes a walk", () => {
  const cap = GLIDE_CAPS.player;
  let cur = { x: 0, y: 0 };
  // One big 0.5-unit jump, walked through at 16ms frames until it's arrived.
  let steps = 0;
  while (Math.hypot(cur.x - 0.5, cur.y - 0.5) > 1e-6 && steps < 10_000) {
    cur = glideTowards(cur, { x: 0.5, y: 0.5 }, 16, cap);
    steps++;
  }
  assert.ok(cur.x === 0.5 && cur.y === 0.5, "arrived");
  // The whole walk takes a bounded, finite number of frames — a smooth
  // recovery, not a snap and not a crawl.
  const expectedFrames = Math.ceil(Math.hypot(0.5, 0.5) / (cap * 16));
  assert.ok(steps <= expectedFrames + 1, `steps ${steps} <= ${expectedFrames}`);
});

test("glideTowards never moves slower than needed for small per-frame steps", () => {
  const cap = GLIDE_CAPS.player;
  const cur = { x: 0.5, y: 0.5 };
  const next = glideTowards(cur, { x: 0.501, y: 0.5 }, 16, cap);
  // Ordinary play moves far below the cap, so exactly the target is reached.
  assert.ok(Math.abs(next.x - 0.501) < 1e-9);
  assert.ok(Math.abs(next.y - 0.5) < 1e-9);
});
