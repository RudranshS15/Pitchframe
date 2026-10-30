/**
 * The frame link's round trip, pinned to the code the page runs.
 *
 * A link the match page writes must name the frame it was written at, and a link
 * the page reads must open exactly the frame it names. That is a round trip, and
 * the only way to pin a round trip is to test the two sides against each other:
 * build a frame, read it back, demand the same frame. Before frame-link.mjs these
 * rules lived apart in app.js (`shareUrl` writing, `restoreFrame` reading), so a
 * change on one side could break the other and nothing unit-run would notice —
 * the served page was the only check. Now the agreement is the test.
 *
 * Run: `node --test tests/js/` from the repository root.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  buildFrameQuery,
  parseFrameQuery,
} from "../../pitchframe/overlay/static/frame-link.mjs";

/* The round trip the page depends on: build a query from a frame, read it back
 * with the same parser the page boots with, and the frame must come back the
 * same — for every shape of frame the page can be standing in. */
test("build then parse returns the same frame, for every shape the page writes", () => {
  const frames = [
    // A numbered match, payload seed in hand, paused mid-replay.
    { named: false, seed: 28, matchId: "PF-001", fault: "none", view: "fan", at: 4321.7, goal: null },
    // A named match: the name is the seed, so no seed is written — whichever
    // value the caller holds.
    { named: true, seed: "denlies", matchId: "PF-001", fault: "none", view: "analyst", at: 900, goal: "1570|home|Casek" },
    // A build-up open: the goal key travels in the payload's own vocabulary.
    { named: false, seed: 7, matchId: "PF-002", fault: "late_pressure", view: "fan", at: 1000, goal: "721|away|Ivoire" },
    // An unopened build-up and a fraction of a second: neither survives in a
    // form the reader never saw.
    { named: false, seed: 28, matchId: "PF-001", fault: "none", view: "fan", at: 3.2, goal: null },
  ];
  for (const frame of frames) {
    const query = buildFrameQuery(frame);
    // `URL.get` on each param, the way app.js's own `params.get` reads them.
    const params = new Map(new URLSearchParams(query));
    const back = parseFrameQuery({
      at: params.get("at"),
      goal: params.get("goal"),
    });
    assert.equal(Math.round(frame.at), back.position, `position round-tripped: ${query}`);
    assert.equal(frame.goal, back.goal, `goal round-tripped: ${query}`);
  }
});

/* The seed rule: a numbered link names the match it opened by carrying the seed
 * the payload *actually built* from. A link that omitted it would inherit the
 * server's default and build a different match — the one failure a reproducible
 * link cannot have. */
test("a numbered match carries its seed; a named match leaves nothing about the match in the query", () => {
  const numbered = buildFrameQuery({ named: false, seed: 28, matchId: "PF-001", fault: "none", view: "fan", at: 4321.7, goal: null });
  assert.ok(numbered.includes("seed=28"), `a numbered link must name its match: ${numbered}`);

  const named = buildFrameQuery({ named: true, seed: "denlies", matchId: "PF-001", fault: "none", view: "fan", at: 4321.7, goal: null });
  assert.ok(!named.includes("seed"), `a named link states nothing about the match in the query: ${named}`);
});

/* The three failures of "only a positive number is a position": absent must not
 * read as zero (Number(null) and Number("") are both 0), and neither must a
 * truncated or negative value — a link to zero seconds asks for the same thing
 * as no link at all, the match from the beginning. */
test("only a positive number is a position", () => {
  for (const raw of [null, undefined, "", "abc", "0", "-12", "-3.5"]) {
    const { position } = parseFrameQuery({ at: raw, goal: null });
    assert.ok(Number.isNaN(position), `at ${JSON.stringify(raw)} read as a position, not NaN`);
  }
  // And what a real position survives: a plain string number, as params carry them.
  assert.equal(parseFrameQuery({ at: "4322", goal: null }).position, 4322);
  assert.equal(parseFrameQuery({ at: "3.9", goal: null }).position, 3.9);
});

/* A build-up only travels while it is open, and when it is closed the link
 * reads as having none. */
test("an absent build-up is absent, not an empty promise", () => {
  assert.equal(parseFrameQuery({ at: "4322", goal: null }).goal, null);
  assert.equal(parseFrameQuery({ at: "4322", goal: undefined }).goal, null);
  assert.equal(parseFrameQuery({ at: "4322", goal: "721|away|Ivoire" }).goal, "721|away|Ivoire");
});

/* Position rounding happens once, in the build: what the page displays is whole
 * seconds, and the link must name the second the reader is standing on, not the
 * fraction its playhead happens to hold. */
test("the link names a second, not the playhead's fraction", () => {
  assert.equal(
    new URLSearchParams(buildFrameQuery({ named: true, seed: "x", matchId: "M", fault: "none", view: "fan", at: 4321.7, goal: null })).get("at"),
    "4322",
  );
  assert.equal(
    new URLSearchParams(buildFrameQuery({ named: true, seed: "x", matchId: "M", fault: "none", view: "fan", at: 4321.2, goal: null })).get("at"),
    "4321",
  );
});

/* A recorded match has no seed. Its stable description is the feed's match id, and a
 * link that wrote `seed=0` would come back as the first generated match instead. */
test("a recorded match carries its feed id, never a seed", () => {
  const query = buildFrameQuery({
    named: false, statsbomb: "8658", seed: 0, matchId: "8658",
    fault: "none", view: "analyst", at: 1200, goal: null,
  });
  const params = new URLSearchParams(query);
  assert.equal(params.get("statsbomb"), "8658");
  assert.equal(params.get("seed"), null, `a recorded link must not name a seed: ${query}`);
});

/* A recorded match opened from the archive is not yet downloaded. Its competition and
 * season are what let the recipient fetch it, so both travel with the id — and half a
 * pair names nothing, so it is never written. A generated match ignores the pair. */
test("a recorded link from the archive carries the pair that can fetch it", () => {
  const query = buildFrameQuery({
    named: false, statsbomb: "8658", competition: 43, season: 3, seed: 0,
    matchId: "8658", fault: "none", view: "fan", at: 1200, goal: null,
  });
  const params = new URLSearchParams(query);
  assert.equal(params.get("competition"), "43");
  assert.equal(params.get("season"), "3");

  const half = new URLSearchParams(buildFrameQuery({
    named: false, statsbomb: "8658", competition: 43, season: null, seed: 0,
    matchId: "8658", fault: "none", view: "fan", at: 1200, goal: null,
  }));
  assert.equal(half.get("competition"), null, "half a pair names nothing");
  assert.equal(half.get("season"), null);

  const generated = new URLSearchParams(buildFrameQuery({
    named: false, seed: 28, competition: 43, season: 3, matchId: "PF-001",
    fault: "none", view: "fan", at: 1200, goal: null,
  }));
  assert.equal(generated.get("competition"), null);
  assert.equal(generated.get("season"), null);
});
