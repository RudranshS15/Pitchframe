/**
 * The ranking agreement: one rule, two languages, one order.
 *
 * The analyst column ranks its rows by relative movement, and the narrator ranks
 * its claims the same way — deliberately, because the board is drawn to show
 * "what moved most" exactly as the claims publish it. Until now the two sides
 * carried the same formula, down to the same 1e-6 baseline epsilon, with no test
 * forcing them to agree: an epsilon retuned on one side, a sign dropped from the
 * delta, or a `keep_flat` divergence would have left the board ranking one story
 * and the claims publishing another, and nothing unit-run would have noticed.
 *
 * This test closes that: identical fixtures walk both runtimes — the JS modules
 * above and `pitchframe.agents.demo.rank_by_relative_movement`, spawned through
 * the same Python the core job runs — and the two orders must be equal, key for
 * key, flat readings included in the board's variant and dropped in the
 * narrator's. It lives in tests/js rather than the Python suite because the
 * overlay's own unit tests are the frame that can spawn a sibling runtime, and
 * the browser job already carries Node.
 *
 * Run: `node --test tests/js/` from the repository root.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  relativeMovement,
  rankByRelativeMovement,
} from "../../pitchframe/overlay/static/relative-rank.mjs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

/* The Python twin. Spawned per fixture through `python -c`, so the test exercises
 * the very copy the agents run — not a fixture file that could drift away from
 * demo.py — and a bare interpreter is enough, because demo.py is standard library
 * only on purpose. A missing Python skips: the environment may be JS-only, and a
 * skipped agreement is truthful, silent agreement is not. */
const PY_ROOT = fileURLToPath(new URL("../..", import.meta.url));
const PY_SCRIPT = `
import json, sys
from pitchframe.agents.demo import rank_by_relative_movement

rows = json.load(sys.stdin)

class R:
    # The three properties MetricReading derives by arithmetic, arithmetic here too.
    @property
    def key(self):
        return self.metric if self.team is None else f"{self.metric}@{self.team}"

readings = [type("R_", (R,), row)() for row in rows]
ranked = rank_by_relative_movement(
    readings,
    keep_flat=sys.argv[1] == "keep",
)
json.dump([r.key for r in ranked], sys.stdout)
`;

/* Readings as either runtime builds them: metric, team for a side-scoped one,
 * the current and baseline values. Python reconstructs MetricReading-shaped
 * objects with the same names, so `delta`, `direction` and `is_flat` agree —
 * the properties are arithmetic in both languages. */
function reading(metric, team, value, baseline_value) {
  return { metric, team, value, baseline_value };
}

function keyOf(r) {
  return r.team === null ? r.metric : `${r.metric}@${r.team}`;
}

/* The narrator drops flat readings; a Python probe for a fixture is one spawn. */
let python = null;
try {
  execFileSync("python", ["--version"], { cwd: PY_ROOT });
  python = "python";
} catch {
  try {
    execFileSync("py", ["--version"], { cwd: PY_ROOT });
    python = "py";
  } catch {
    python = null;
  }
}

let skipped = 0;
function pythonOrder(readings, { keepFlat = false } = {}) {
  if (python === null) {
    skipped += 1;
    return null;
  }
  const payload = JSON.stringify(
    readings.map((r) => ({
      metric: r.metric,
      team: r.team,
      value: r.value,
      baseline_value: r.baseline_value,
      // The two properties Python derives as arithmetic on the same names.
      delta: r.value - r.baseline_value,
      is_flat: Math.abs(r.value - r.baseline_value) < 1e-9,
      direction: r.value - r.baseline_value > 0 ? "up" : "down",
    })),
  );
  const out = execFileSync(python, ["-c", PY_SCRIPT, keepFlat ? "keep" : "drop"], {
    cwd: PY_ROOT,
    input: payload,
    encoding: "utf8",
  });
  return JSON.parse(out);
}

/* The scale-spanning fixture, the exact one raw deltas mis-rank: momentum moves
 * in tens, pressure in hundredths, possession in fractions — the biggest *raw*
 * delta must not be the biggest story because its units happen to be largest. */
const SCALE_SPANNING = [
  reading("momentum", null, 72.0, 60.0), // raw delta 12, relative 0.2
  reading("shot_rate", null, 0.115, 0.09), // raw delta 0.025, relative ~0.28
  reading("possession_share", "home", 0.616, 0.5), // raw delta 0.116, relative ~0.232
  reading("pressure", null, 0.55, 0.3), // raw delta 0.25, relative ~0.833 — the story
  reading("territory", null, 42.0, 50.0), // raw delta -8, direction flipped, relative 0.16
].map((r) => ({
  ...r,
  delta: r.value - r.baseline_value,
  // From the values, not from r.delta: `r` is still the source row here, which
  // carries no delta yet — reading it would make every flag NaN < 1e-9, i.e. false.
  is_flat: Math.abs(r.value - r.baseline_value) < 1e-9,
}));

const THE_STORY = "pressure"; // the reading raw deltas would bury

/* Ties and flats probe the guarantee the board leans on: stable equal weights
 * keep the payload's order, and a flat row stays in the board's table while
 * never reaching a claim. */
const WITH_TIES_AND_FLATS = [
  reading("share", "home", 0.52, 0.5), // relative 0.04
  reading("share", "away", 0.48, 0.5), // same magnitude — a tie; payload order holds
  reading("tempo", null, 14.0, 14.0), // flat: the narrator must drop it
  reading("metres_gained", null, 8.0, 4.0), // relative 2.0, plain winner
  reading("recoveries", null, 0.0, 0.0), // flat, zero baseline: the epsilon case
].map((r) => ({
  ...r,
  delta: r.value - r.baseline_value,
  is_flat: Math.abs(r.value - r.baseline_value) < 1e-9,
}));

/* One board row, flat zero-baseline included, in the analyst column's own span. */
const BOARD_SPAN_FIXTURE = [
  reading("possession_share", "home", 0.44, 0.5),
  reading("possession_share", "away", 0.56, 0.5),
  reading("momentum", "home", 64.0, 60.0),
  reading("duels_won", null, 0.31, 0.29),
  reading("corner_rate", null, 0.0, 0.0),
].map((r) => ({
  ...r,
  delta: r.value - r.baseline_value,
  is_flat: Math.abs(r.value - r.baseline_value) < 1e-9,
}));

/* The JS order. `rankByRelativeMovement` keeps every row. */
const jsOrder = (readings) => rankByRelativeMovement(readings).map(keyOf);

/* The narrator's order, as the demo computes it: the non-flat readings,
 * strongest first. Python's `rank_by_relative_movement` is the authority this
 * mirrors — the spawned probe holds the two to the same list. */
const narratorJsOrder = (readings) =>
  rankByRelativeMovement(readings.filter((r) => !r.is_flat)).map(keyOf);

test("relative movement ranks by story, not by units", () => {
  const order = jsOrder(SCALE_SPANNING);
  assert.equal(order[0], THE_STORY, `the raw-delta trap escalated a big-unit metric: ${order}`);
  assert.ok(
    relativeMovement(SCALE_SPANNING.find((r) => keyOf(r) === "momentum")) <
      relativeMovement(SCALE_SPANNING.find((r) => keyOf(r) === THE_STORY)),
  );
  // A drop in a rising metric ranks like a rise of the same size: the weight is
  // the magnitude, not the direction.
  const mirrored = SCALE_SPANNING.map((r) => ({
    ...r,
    value: r.value === r.baseline_value ? r.value : 2 * r.baseline_value - r.value,
  }));
  assert.deepEqual(
    jsOrder(mirrored),
    jsOrder(SCALE_SPANNING).map((k) => k),
    "flipping every direction changed the ranking",
  );
});

test("the board's order and the narrator's Python order agree, key for key", () => {
  for (const [name, readings] of [
    ["scale-spanning", SCALE_SPANNING],
    ["ties and flats", WITH_TIES_AND_FLATS],
    ["board span", BOARD_SPAN_FIXTURE],
  ]) {
    const actual = jsOrder(readings);
    const expected = pythonOrder(readings, { keepFlat: true });
    if (expected === null) {
      skipped += 1;
      continue;
    }
    assert.deepEqual(
      actual,
      expected,
      `${name}: the board ranked ${actual.join(" > ")} but Python ranked ${expected.join(" > ")}`,
    );
  }
  assert.ok(skipped === 0 || python !== null, "no Python was probed despite being reachable");
});

test("ties keep the payload's order in both languages", () => {
  const actual = jsOrder(WITH_TIES_AND_FLATS);
  const expected = pythonOrder(WITH_TIES_AND_FLATS, { keepFlat: true });
  assert.ok(expected !== null, "the tie order needs the Python probe");
  assert.deepEqual(actual, expected);
  const home = actual.indexOf("share@home");
  const away = actual.indexOf("share@away");
  assert.ok(home !== -1 && away !== -1, `the tied pair vanished: ${actual}`);
  assert.ok(home < away, `the tie broke to the payload's disadvantage: ${actual.join(" > ")}`);
});

test("flat readings stay in the board's table, never reach a claim, both runtimes", () => {
  const kept = jsOrder(WITH_TIES_AND_FLATS);
  assert.equal(kept.length, WITH_TIES_AND_FLATS.length, "the board dropped a flat row");
  const narratedOrder = narratorJsOrder(WITH_TIES_AND_FLATS);
  assert.ok(!narratedOrder.includes("tempo"), `a still metric received a claim: ${narratedOrder}`);
  assert.ok(!narratedOrder.includes("recoveries"), "a zero-delta metric received a claim");

  // The narrator's order is exactly the board's minus the flats.
  assert.deepEqual(
    narratedOrder,
    kept.filter((key) => key !== "tempo" && key !== "recoveries"),
  );

  // And the Python side agrees on both.
  const pyKept = pythonOrder(WITH_TIES_AND_FLATS, { keepFlat: true });
  const pyNarrated = pythonOrder(WITH_TIES_AND_FLATS, { keepFlat: false });
  assert.deepEqual(pyNarrated, pyKept.filter((key) => key !== "tempo" && key !== "recoveries"),
    `Python's two variants disagreed beyond the flats: ${pyNarrated} vs ${pyKept}`);
});

test("the strictest form: the narrator's Python order matches the narrator's JS order", () => {
  const expected = pythonOrder(SCALE_SPANNING, { keepFlat: false });
  assert.ok(expected !== null, "the Python probe is the point of this test");
  const actual = narratorJsOrder(SCALE_SPANNING);
  assert.deepEqual(actual, expected, `narrator ranks diverged: ${actual} vs ${expected}`);
});
