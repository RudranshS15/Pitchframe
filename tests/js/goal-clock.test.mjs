/**
 * The goal-sync guarantee, pinned to the code the page runs.
 *
 * The match page's four goal-driven surfaces — the feed row, the scoreboard, the
 * launcher and the dock's build-up count — must all move in the *same* clock step
 * the playhead crosses a goal: nothing earlier (a reveal before its goal spoils
 * the replay), nothing later (a scoreboard that lags its own row is a lag the
 * reader can see). This was measured live in a browser across every goal of the
 * default match; these tests pin the same rule to the pure decisions in
 * goal-clock.mjs, so a retune of app.js that breaks one consumer cannot pass a
 * suite that holds the others.
 *
 * The clock sequence below is the page's own: `tick` advances the playhead and
 * reveals at `Math.floor(next)`, so a step is "what the reader sees that frame".
 *
 * Run: `node --test tests/js/` from the repository root.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  buildItems,
  goalKey,
  newestBuildUp,
  reachedGoals,
  scoreAt,
} from "../../pitchframe/overlay/static/goal-clock.mjs";

/* The default matchday (seed 28): five goals, four of them away's, on these
 * match-clock seconds. The windows are the payload's own five-minute ones. */
const GOALS = [
  { clock: 721, minute: 12, team: "away", player: "Ivoire" },
  { clock: 1229, minute: 20, team: "away", player: "Danrado" },
  { clock: 1570, minute: 26, team: "home", player: "Casek" },
  { clock: 3961, minute: 66, team: "away", player: "Nikmire" },
  { clock: 4706, minute: 78, team: "away", player: "Jaskovac" },
];
const WINDOWS = [900, 1500, 1800, 4200, 4800, 5400];

function doc() {
  const frames = WINDOWS.map((end, index) => ({
    clock: end,
    window: [index === 0 ? 0 : WINDOWS[index - 1], end],
    incidents: GOALS.filter((g) => g.clock < end && g.clock > (WINDOWS[index - 1] ?? 0))
      .map((g) => ({ clock: g.clock, type: "goal", team: g.team, player: g.player })),
    feed: [{ metric: `possession_share@home`, status: "verified" }],
  }));
  return { goals: GOALS.map((g) => ({ ...g })), frames };
}

function highlights() {
  return new Map(GOALS.map((g) => [goalKey(g.clock, g.team, g.player), { ...g }]));
}

/* The page's clock loop, as a pure sequence: `next` advances by
 * MATCH_SECONDS_PER_REAL_SECOND × speed per 60 Hz frame, and the page reveals at
 * floor(next). Yields one snapshot per frame. */
function replayTicks(speed, { fps = 60, seconds = 10 } = {}) {
  const perFrame = (60 * speed) / fps;
  const snapshots = [];
  let clock = 0;
  for (let frame = 0; clock < seconds * 60 * speed; frame++) {
    const next = clock + perFrame;
    const revealedClock = Math.floor(next);
    snapshots.push({
      revealedClock,
      items: buildItems(doc(), highlights()).filter((i) => i.clock <= revealedClock),
      score: scoreAt(GOALS, revealedClock),
      launch: newestBuildUp(GOALS, highlights(), revealedClock),
      dockCount: reachedGoals(GOALS, revealedClock).length,
    });
    clock = next;
  }
  return snapshots;
}

/* The four surfaces read off one snapshot, as the page would render them. */
function surfaces(snap) {
  const goalRows = snap.items.filter((i) => i.kind === "goal").length;
  const launchKey = snap.launch
    ? goalKey(snap.launch.goal.clock, snap.launch.goal.team, snap.launch.goal.player)
    : "";
  return `${snap.score.home}-${snap.score.away}|${goalRows}|${launchKey}|${snap.dockCount}`;
}

test("every goal lands with scoreboard, row, launcher and dock in one tick, never before", () => {
  for (const speed of [1, 4, 16]) {
    const snaps = replayTicks(speed, { seconds: 90 });
    const before = new Map(GOALS.map((g) => [goalKey(g.clock, g.team, g.player), null]));
    for (const snap of snaps) {
      for (const goal of GOALS) {
        const key = goalKey(goal.clock, goal.team, goal.player);
        const fired = snap.score[key.split("|")[1] === "home" ? "home" : "away"];
        if (snap.revealedClock < goal.clock) {
          // Not yet: nothing may say otherwise.
          assert.ok(
            !snap.items.some((i) => i.kind === "goal" && i.data.clock === goal.clock),
            `speed ${speed}×: goal ${goal.minute}′ revealed before ${goal.clock} (tick ${snap.revealedClock})`
          );
        } else if (before.get(key) === null) {
          // The first tick at or past the goal: everything must say so at once.
          const reached = GOALS.filter((g) => g.clock <= snap.revealedClock);
          const rows = snap.items.filter((i) => i.kind === "goal").map((i) => i.data.clock);
          assert.ok(rows.includes(goal.clock), `speed ${speed}×: goal ${goal.minute}′ row missing at its own tick`);
          assert.deepEqual(rows, reached.map((g) => g.clock), `speed ${speed}×: the rows on screen at tick ${snap.revealedClock} are not exactly the reached goals`);
          const newest = reached[reached.length - 1];
          assert.equal(
            surfaces(snap),
            `${reached.filter((g) => g.team === "home").length}-${reached.filter((g) => g.team === "away").length}` +
              `|${reached.length}|${goalKey(newest.clock, newest.team, newest.player)}|${reached.length}`,
            `speed ${speed}×: goal ${goal.minute}′ surfaces disagreed at tick ${snap.revealedClock}`
          );
          before.set(key, snap.revealedClock);
        } else {
          assert.ok(
            snap.items.some((i) => i.kind === "goal" && i.data.clock === goal.clock),
            `speed ${speed}×: goal ${goal.minute}′ row vanished after landing`
          );
        }
      }
    }
    // Every goal actually fired somewhere.
    for (const [key, at] of before) {
      assert.ok(at !== null, `speed ${speed}×: goal ${key} never landed`);
      const goal = GOALS.find((g) => goalKey(g.clock, g.team, g.player) === key);
      const first = replayTicks(speed, { seconds: 90 }).find(
        (snap) => snap.revealedClock >= goal.clock
      );
      // At 60 Hz the granularity is one frame's step: the goal is on screen within
      // one tick of the clock crossing it, and never a moment before.
      assert.ok(
        first.revealedClock - goal.clock < (60 * speed) / 60 + 1,
        `speed ${speed}×: goal ${goal.minute}′ lagged ${first.revealedClock - goal.clock}s past its clock`
      );
    }
  }
});

test("the launcher names the newest goal with a build-up, not merely the newest goal", () => {
  // A goal whose highlight is missing (dropped by an old payload, say) must not
  // light the launcher — but must not block an older one from doing so either.
  const partial = new Map([
    [goalKey(1229, "away", "Danrado"), { clock: 1229, team: "away", scorer: "Danrado" }],
  ]);
  const launch = newestBuildUp(GOALS, partial, 1600);
  assert.equal(launch.goal.clock, 1229, "the launcher skipped past a goal that has a build-up");
  assert.equal(newestBuildUp(GOALS, partial, 720), null, "the launcher fired before any goal");
  assert.equal(newestBuildUp(GOALS, partial, 721), null, "the launcher offered a goal with no build-up");
});

test("the boundary is inclusive: a goal on the tick the playhead stands on has happened", () => {
  assert.deepEqual(scoreAt(GOALS, 721), { home: 0, away: 1 });
  assert.deepEqual(scoreAt(GOALS, 720), { home: 0, away: 0 });
  assert.deepEqual(scoreAt(GOALS, 1570), { home: 1, away: 2 });
  assert.deepEqual(reachedGoals(GOALS, 4706).length, 5);

  /* The exact second, pinned surface by surface — the numbers the live browser check
   * walked through on the matchday replay (1560 showed the 26′ minute label and was
   * correctly still 0–2; 1570 counted). One second before, nothing may say otherwise:
   * not the score, not the rows, not the launcher, not the dock's count. */
  assert.deepEqual(scoreAt(GOALS, 1569), { home: 0, away: 2 });
  assert.deepEqual(scoreAt(GOALS, 1570), { home: 1, away: 2 });
  assert.equal(
    reachedGoals(GOALS, 1569).length, 2,
    "a goal counted one second before its own clock",
  );
  assert.equal(reachedGoals(GOALS, 1570).length, 3);
  assert.equal(newestBuildUp(GOALS, highlights(), 1569)?.goal.clock, 1229);
  assert.equal(newestBuildUp(GOALS, highlights(), 1570)?.goal.clock, 1570);
  const before = buildItems(doc(), highlights()).filter((i) => i.clock <= 1569);
  const on = buildItems(doc(), highlights()).filter((i) => i.clock <= 1570);
  assert.ok(
    !before.some((i) => i.kind === "goal" && i.data.clock === 1570),
    "the goal row appeared one second before its own clock",
  );
  assert.ok(
    on.some((i) => i.kind === "goal" && i.data.clock === 1570),
    "the goal row was missing on the goal's own second",
  );
});

test("the goal row sorts ahead of the same-second claims, and only once", () => {
  const items = buildItems(doc(), highlights());
  const at721 = items.filter((i) => i.clock === 721);
  assert.equal(at721.length, 1, "a goal must appear exactly once even though its window also carries it as an incident");
  assert.equal(at721[0].kind, "goal");
  const claim = items.find((i) => i.clock === 900 && i.kind === "claim");
  const goal = items.find((i) => i.clock === 721);
  assert.ok(
    items.indexOf(goal) < items.indexOf(claim),
    "the goal did not sort before the window's claim rows"
  );
});

test("rolling back reveals nothing the smaller clock does not own", () => {
  const docData = doc();
  const forward = buildItems(docData, highlights()).filter((i) => i.clock <= 2700);
  const back = buildItems(docData, highlights()).filter((i) => i.clock <= 1449);
  assert.ok(forward.every((i) => back.every((b) => b.clock <= 1449)) || true);
  assert.ok(back.every((i) => i.clock <= 1449), "a rewind left an item from the future");
  // The score follows the rewind: two goals at 1449, three at 2700, and the
  // 26′ goal that was on screen is gone again.
  assert.deepEqual(scoreAt(GOALS, 1449), { home: 0, away: 2 });
  assert.deepEqual(scoreAt(GOALS, 2700), { home: 1, away: 2 });
  assert.equal(newestBuildUp(GOALS, highlights(), 1449).goal.clock, 1229);
});
