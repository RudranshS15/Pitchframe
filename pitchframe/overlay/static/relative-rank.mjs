/**
 * The relative-movement ranking, as a pure decision — the board's half of a rule
 * the narrator's demo agent owns the other half of.
 *
 * The analyst column sorts its readings by relative movement — |delta| divided by
 * the baseline's magnitude — because the metrics do not share a scale: a momentum
 * delta of 10 and a possession delta of 0.3 say very different things about which
 * is the bigger story, and comparing raw deltas would rank whichever metric
 * happens to have the largest units first. The narrator ranks the *same* way,
 * because the board is drawn to answer "what moved most" exactly as the claims
 * publish it: the authority lives in `pitchframe.agents.demo.rank_by_relative_
 * movement`, and this module is the same formula in the page's language. Neither
 * side copies from the other in a way a test can miss — tests/js/rank-agreement
 * .test.mjs feeds identical fixtures to both runtimes and holds them to one order.
 *
 * No DOM, no state: a list of readings in, a new sorted list out. The input is
 * never touched (the board re-renders per frame and the caller keeps its list), and
 * the sort is stable, so readings that tie keep the payload's order — the same
 * guarantee Python's sort gives the narrator.
 */

/** The epsilon padded onto every baseline before the division. Shared by name
 *  with `RELATIVE_MOVEMENT_EPSILON` in demo.py: without it a metric the baseline
 *  window never recorded (baseline exactly zero) would divide by zero and vanish
 *  from a ranking it might top. */
export const RELATIVE_MOVEMENT_EPSILON = 1e-6;

/** The weight a reading carries in the ranking: |delta| / (|baseline| + ε). */
export function relativeMovement(reading) {
  return (
    Math.abs(reading.delta) /
    (Math.abs(reading.baseline_value) + RELATIVE_MOVEMENT_EPSILON)
  );
}

/**
 * Readings sorted strongest-first by relative movement.
 *
 * Every row is kept — flat readings included — because the board renders what the
 * window measured, and "did not move" is information too; the narrator's variant
 * drops them before ranking (a claim about a reading that did not move is a claim
 * with nothing in it), which is the one place the two callers differ, and the
 * agreement test holds both orders anyway.
 */
export function rankByRelativeMovement(readings) {
  return (readings || []).slice().sort((a, b) => relativeMovement(b) - relativeMovement(a));
}
