/**
 * Stale feeds, snapshots and substitutions — the view-level rules that answer:
 * what does a marker do when the feed stops, comes back, or swaps a player.
 *
 * Run: `node --test tests/js/` from the repository root.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  STALE,
  buildEntityIndex,
  playerPositionAt,
} from "../../pitchframe/overlay/static/pitch/pitch-core.mjs";

import { generateRecording } from "../../pitchframe/overlay/static/pitch/pitch-mock.mjs";

function p(id, x, y, extra = {}) {
  return { id, teamId: "home", shirtNumber: 1, name: id, x, y, isGoalkeeper: false, ...extra };
}

function frame(t, players) {
  return { t, period: "1H", ball: { x: 0.5, y: 0.5 }, players };
}

test("the stale window is the documented 2s hold + 800ms fade", () => {
  assert.equal(STALE.holdMs, 2000);
  assert.equal(STALE.fadeMs, 800);
  // A marker gone quiet for exactly 2s is still fully visible; at 2s + 1ms the
  // fade has started; the fade is done by 2.8s.
  const frames = [frame(0, [p("a", 0.3, 0.3)])];
  const idx = buildEntityIndex(frames);
  const hold = playerPositionAt(idx, "a", 2000, { ...STALE });
  assert.equal(hold.alpha, 1);
  const fading = playerPositionAt(idx, "a", 2001, { ...STALE });
  assert.equal(fading.alpha, 1 - 1 / STALE.fadeMs);
  const done = playerPositionAt(idx, "a", 2800, { ...STALE });
  assert.equal(done.alpha, 0);
  assert.equal(done.state, "gone");
});

test("while fading, markers are frozen — drift happens inside the hold, then they stop", () => {
  const frames = [
    frame(0, [p("a", 0.3, 0.3)]),
    frame(1000, [p("a", 0.5, 0.5)]),
  ];
  const idx = buildEntityIndex(frames);
  // Inside the hold window the marker carries its last interval's velocity
  // once — it drifts from (0.5, 0.5) to (0.7, 0.7) (one previous interval's
  // displacement, both axes) and then holds. The fade never moves a marker:
  // at 2300 and 2700 the spot is exactly where the hold left it.
  const first = playerPositionAt(idx, "a", 2001, { ...STALE });
  assert.ok(Math.abs(first.x - 0.7) < 1e-9, `drifted to 0.7, got ${first.x}`);
  assert.equal(first.alpha, 1);
  for (const t of [2300, 2700]) {
    const q = playerPositionAt(idx, "a", t, { ...STALE });
    assert.ok(Math.abs(q.x - 0.7) < 1e-9, `frozen at 0.7 at t=${t}, got ${q.x}`);
    assert.ok(Math.abs(q.y - 0.7) < 1e-9);
  }
});

test("an acknowledged departure (a sub, in a recording) fades from the last sighting", () => {
  // holdMs=0 + fadeMs=600 turns the last sighting into the start of a fade:
  // exactly what a recorded match means by a disappeared id. The position is
  // held exactly (no velocity drift — the exit is a fact), and the id is gone
  // FADE_MS after its final sighting.
  const frames = [
    frame(0, [p("out", 0.2, 0.2)]),
    frame(1000, [p("out", 0.25, 0.2)]),
    frame(1300, [p("in", 0.25, 0.2)]), // sub: new id, same slot
  ];
  const idx = buildEntityIndex(frames);
  const q = playerPositionAt(idx, "out", 1300, { holdMs: 0, fadeMs: 600 });
  assert.ok(Math.abs(q.x - 0.25) < 1e-9, "held at the outgoing player's last spot");
  assert.equal(q.alpha, 1 - 300 / 600);
  const mid = playerPositionAt(idx, "out", 1450, { holdMs: 0, fadeMs: 600 });
  assert.equal(mid.alpha, 1 - 450 / 600);
  const gone = playerPositionAt(idx, "out", 1700, { holdMs: 0, fadeMs: 600 });
  assert.equal(gone.state, "gone");
  // The newcomer fades in over the same window; the view handles that side.
  const arriving = playerPositionAt(idx, "in", 1300, { holdMs: 0, fadeMs: 600 });
  assert.ok(Math.abs(arriving.x - 0.25) < 1e-9);
  assert.equal(arriving.alpha, 1);
});

test("recording substitutions: the outgoing id never appears again after the sub minute", () => {
  const rec = generateRecording({ seed: 11, minutes: 90, substitutions: [[58, "home", 7], [74, "away", 9]] });
  const outHomeBefore = rec.frames[58 * 60 - 1].players.map((x) => x.id);
  const outHomeAfter = rec.frames[58 * 60].players.map((x) => x.id);
  assert.ok(outHomeBefore.includes("home08"), "outgoing player present the minute before");
  assert.ok(!outHomeAfter.includes("home08"), "outgoing player gone at the sub minute");
  assert.ok(outHomeAfter.includes("homeb1"), "incoming player present at the sub minute");
  // …and the incoming player starts where the outgoing one stood, within the
  // distance one sampling step can move a sprinting player (no teleport).
  const last = rec.frames[58 * 60 - 1].players.find((x) => x.id === "home08");
  const first = rec.frames[58 * 60].players.find((x) => x.id === "homeb1");
  const moved = Math.hypot((last.x - first.x) * 105, (last.y - first.y) * 68);
  assert.ok(moved <= 8.5 + 1e-6, `moved ${moved}m, within one sprint-second`);
  // The bench entry that came in is no longer on the bench.
  assert.ok(!rec.squads.home.bench.some((b) => b.id === "homeb1"));
});

test("the same seed produces the same match (determinism), different seeds do not", () => {
  const a = generateRecording({ seed: 5, minutes: 3 });
  const b = generateRecording({ seed: 5, minutes: 3 });
  const c = generateRecording({ seed: 6, minutes: 3 });
  assert.equal(JSON.stringify(a), JSON.stringify(b));
  assert.notEqual(JSON.stringify(a), JSON.stringify(c));
  // Sanity on content: 22 players, no NaN leaking through the model.
  assert.equal(a.frames[0].players.length, 22);
  for (const f of a.frames) {
    assert.ok(Number.isFinite(f.ball.x) && Number.isFinite(f.ball.y));
    for (const q of f.players) {
      assert.ok(Number.isFinite(q.x) && Number.isFinite(q.y));
      assert.ok(q.x >= 0 && q.x <= 1);
      assert.ok(q.y >= 0 && q.y <= 1);
    }
  }
});
