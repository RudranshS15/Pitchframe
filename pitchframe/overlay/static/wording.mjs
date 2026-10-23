/**
 * The page's copy decisions, as pure functions — the two sentences that are
 * arithmetic wearing words.
 *
 * Both rules were written inline where they happened to be rendered: the replay
 * rate chose between seconds and minutes, and the ledger chose singular or
 * plural, inside the functions that write to the DOM. They are decisions about
 * wording, not rendering, and the kind that break quietly — a rate note that
 * says "60 match seconds" where it should say "1 match minute", a ledger that
 * pluralises a lone line. Here they are testable without a page (see
 * tests/js/wording.test.mjs), and the render sites read them instead of
 * re-deriving them.
 *
 * No DOM, no state: numbers and counts in, strings out.
 */

/**
 * The replay-rate note: what one real second of playback is worth in match time.
 *
 * Exactly one match minute gets its own sentence — "60 match seconds" is true
 * and unreadable, and the boundary case is the one the default speed hits — so
 * the comparison is against 60, not against an interval.
 *
 * @param {number} secondsPerSecond - match seconds per real second.
 */
export function rateNote(secondsPerSecond) {
  return secondsPerSecond === 60
    ? "1 second = 1 match minute"
    : secondsPerSecond < 60
      ? `1 second = ${secondsPerSecond} match seconds`
      : `1 second = ${Math.round(secondsPerSecond / 60)} match minutes`;
}

/**
 * A count with its noun, singular only when the count is one.
 *
 * Zero is plural — "0 claims caught" — because zero is not a lone thing; the
 * test pins that, since it is the case a `count === 1 ? "" : "s"` instinct
 * otherwise gets wrong twice in one sentence.
 *
 * @param {number} count
 * @param {string} noun
 */
export function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}
