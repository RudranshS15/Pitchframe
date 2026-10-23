/**
 * The replay's goal sync, as pure decisions.
 *
 * Every goal-driven surface on the match page — the feed row, the scoreboard, the
 * dock's build-up badge and menu, the launcher — answers the same question from the
 * same inputs: which goals has the playhead reached? Before this module, that
 * question was answered by four separate filters and one builder scattered through
 * app.js; they agreed because each was written beside the others. Extracting them
 * here gives the agreement a place to be tested: one clock step must move the row,
 * the score, the launcher and the dock together or the page spoils or lags its own
 * replay (see tests/js/goal-clock.test.mjs).
 *
 * No DOM, no timers, no state: everything takes the payload's own lists and a match
 * clock and hands back what the page should show, so `tests/js` runs the exact code
 * the browser runs. Clocks are match seconds; the boundary rule is inclusive
 * (`clock <= G`), matching the server's `_score_at` — a goal on the exact second the
 * playhead stands on has happened, not "almost".
 */

/** One key for "which goal is this", used on both sides of the highlight lookup:
 *  clock plus side plus scorer is unique within a match. */
export const goalKey = (clock, team, player) => `${clock}|${team}|${player}`;

/** The goals the playhead has reached, in match order. */
export function reachedGoals(goals, clock) {
  return (goals || []).filter((goal) => goal.clock <= clock);
}

/** The score as it stood at `clock`, counting a goal on that exact second — the
 *  same inclusive boundary the server's frame scores are built with, so the
 *  scoreboard can never disagree with the payload it came from. */
export function scoreAt(goals, clock) {
  const reached = reachedGoals(goals, clock);
  return {
    home: reached.filter((goal) => goal.team === "home").length,
    away: reached.filter((goal) => goal.team === "away").length,
  };
}

/** The newest reached goal that has a build-up to open — what the launcher lights,
 *  and what the launcher's key is derived from so it changes exactly when the
 *  scoreboard's newest goal does. A goal without a highlight is skipped rather
 *  than offered, because a pad that opened nothing is a control that lies. */
export function newestBuildUp(goals, highlightsByKey, clock) {
  const reached = reachedGoals(goals, clock);
  for (let index = reached.length - 1; index >= 0; index -= 1) {
    const goal = reached[index];
    const highlight = (highlightsByKey || new Map()).get(
      goalKey(goal.clock, goal.team, goal.player)
    );
    if (highlight) return { goal, highlight };
  }
  return null;
}

/**
 * The page's one chronological list, from the document.
 *
 * Goals come from the uncapped goal list, not from the incident lists, which are
 * capped for legibility: a goal dropped by that cap would vanish from the timeline
 * while the scoreboard still moved, and it would leave nothing to click. Taking
 * them from here also means each goal appears exactly once. Incidents and the
 * analysis of a window share a minute often; the goal sorts first (order 0),
 * because it is the event the window's rows are about.
 */
export function buildItems(doc, highlightsByKey) {
  const items = [];
  (doc.goals || []).forEach((goal) => {
    items.push({
      clock: goal.clock,
      order: 0,
      kind: "goal",
      data: goal,
      highlight: (highlightsByKey || new Map()).get(
        goalKey(goal.clock, goal.team, goal.player)
      ) || null,
    });
  });
  (doc.frames || []).forEach((frame) => {
    (frame.incidents || []).forEach((incident) => {
      if (incident.type === "goal") return;
      items.push({ clock: incident.clock, order: 1, kind: "incident", data: incident, frame });
    });
    (frame.feed || []).forEach((row) => {
      items.push({ clock: frame.clock, order: 2, kind: "claim", data: row, frame });
    });
  });
  items.sort((a, b) => a.clock - b.clock || a.order - b.order);
  return items;
}
