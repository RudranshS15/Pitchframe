/**
 * The shareable frame link, as pure decisions — both directions.
 *
 * The match page's URL is a frame pointer: which match, which seed, which second,
 * which build-up open. Before this module the build side (`shareUrl`) and the read
 * side (`restoreFrame`) lived apart in app.js, and the three rules that make a link
 * reproducible were enforced on one side only, with nothing stopping them drifting
 * apart. Together here they are round-trippable: whatever the page builds it can
 * read back, and a link the page cannot read faithfully is a test failure (see
 * tests/js/frame-link.test.mjs).
 *
 * No DOM, no `location`, no state: the build takes the frame as values and hands
 * back the query string; the read takes the raw params and hands back what they
 * name. `at` and `goal` place the reader rather than parameterise the request, so
 * they never reach the server — the fetch sends only match_id, seed and fault.
 */

/**
 * The query string for the frame `{ matchId, seed, fault, view }` paused `at` a
 * match second, with an optional build-up `goal` open (its goalKey).
 *
 * The three rules:
 * - **A named match is named in the path, and nothing about it goes in the query:**
 *   the name *is* the seed. Only a numbered match carries a `seed` — and it carries
 *   the seed the payload was *actually built* from, not the one the page asked for,
 *   or opening the link later would build a different match.
 * - **A position is a positive whole number.** `at` is rounded here, so a link names
 *   a second, the resolution the page displays anyway; a pause at second 4321.7
 *   writes `at=4322`, not a fraction the reader never saw.
 * - **A build-up goes in only while it is open.** `goal` is passed as the same key
 *   the timeline keys goals by, so the link speaks the payload's vocabulary.
 */
export function buildFrameQuery({ named, seed, matchId, fault, view, at, goal }) {
  const share = new URLSearchParams();
  if (!named) share.set("seed", String(seed));
  share.set("match_id", matchId);
  share.set("fault", fault);
  share.set("view", view);
  share.set("at", String(Math.round(at)));
  if (goal) share.set("goal", goal);
  return `${share}`;
}

/**
 * What the raw `at` and `goal` params name.
 *
 * `position` is a number, or NaN when the link names no second at all: absent,
 * empty, a truncated value, zero or negative. Only a positive finite number is a
 * position — absent must not read as zero, because Number(null) and Number("") are
 * both 0 and a link to zero seconds asks for the same thing as no link at all: the
 * match from the beginning. `goal` passes through as-is when present, null
 * otherwise; what second a goal-only link opens on is decided by the caller, which
 * owns the payload that knows the goal's clock.
 */
export function parseFrameQuery(query) {
  const seconds = Number(query.at);
  const position =
    query.at !== null && query.at !== undefined && query.at !== "" &&
      Number.isFinite(seconds) && seconds > 0
      ? seconds
      : NaN;
  return { position, goal: query.goal || null };
}
