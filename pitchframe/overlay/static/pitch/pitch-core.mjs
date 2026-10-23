/**
 * The pitch view's pure core: pitch geometry, the match clock, and interpolation.
 *
 * No DOM, no canvas, no timers — everything here takes in numbers and gives back
 * numbers so `tests/js` can run the same code the browser runs, without a
 * playwright pass standing in for a unit test.
 *
 * Coordinates throughout are normalised 0<!-- -->–1 with x along the pitch length
 * (the side home attacks from the left by default) and y across the width, then
 * converted to metres on the 105 × 68 m proportions and to canvas pixels by
 * {@link fitPitchTo}.
 */

const PITCH_LENGTH_M = 105;
const PITCH_WIDTH_M = 68;

/** Period boundaries on the match clock, in milliseconds. */
const HALFTIME_MS = 45 * 60_000;
const FULLTIME_MS = 90 * 60_000;
const ET1_END_MS = 105 * 60_000;
const ET2_END_MS = 120 * 60_000;

/**
 * Which half (or extra time) a spot on the match clock belongs to.
 *
 * The mock recording folds stoppage time into the half, so the clock is
 * continuous across the break and never crosses into extra time — but the
 * periods the data contract names are all mapped here so a feed that does
 * carry them is described honestly.
 */
export function periodFor(ms) {
  if (ms < HALFTIME_MS) return "1H";
  if (ms < FULLTIME_MS) return "2H";
  if (ms < ET1_END_MS) return "ET1";
  if (ms < ET2_END_MS) return "ET2";
  return "FT";
}

/** Match clock rendered as `mm:ss` of the running clock in play, e.g. `73:02`. */
export function formatClock(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/* ---------------------------------------------------------------- mapping */

/**
 * Fit the (105 × 68 m) pitch into any container box, keeping the proportions
 * and a margin around the field, and return everything mapping needs.
 *
 * The margin is proportional to the smaller axis of the container so the
 * whitespace stays constant on a phone and never crowds the labels.
 */
export function fitPitchTo(boxWidth, boxHeight, { margin = 0.08 } = {}) {
  const insets = margin * Math.min(boxWidth, boxHeight);
  const scale = Math.min(
    (boxWidth - 2 * insets) / PITCH_LENGTH_M,
    (boxHeight - 2 * insets) / PITCH_WIDTH_M,
  );
  const drawnW = PITCH_LENGTH_M * scale;
  const drawnH = PITCH_WIDTH_M * scale;
  return {
    scale,
    ox: (boxWidth - drawnW) / 2,
    oy: (boxHeight - drawnH) / 2,
    drawnW,
    drawnH,
    boxWidth,
    boxHeight,
  };
}

/** 180° rotation in normalised space: neither axis is mirrored, so numbers, names and shirt strips stay upright. */
export function flipXY(x, y, orientation) {
  if (orientation === "home-right") return { x: 1 - x, y: 1 - y };
  return { x, y };
}

/** Normalised coordinates to pitch metres, honouring the view's orientation. */
export function toMetres(layout, x, y, orientation) {
  const f = flipXY(x, y, orientation);
  return { m: f.x * PITCH_LENGTH_M, n: f.y * PITCH_WIDTH_M };
}

/** Normalised coordinates to container pixels, honouring the view's orientation. */
export function mapPoint(layout, x, y, orientation) {
  const { m, n } = toMetres(layout, x, y, orientation);
  return { x: layout.ox + m * layout.scale, y: layout.oy + n * layout.scale };
}

/** Inverse of `mapPoint`: container pixels back to normalised coordinates. */
export function mapPointInverse(layout, px, py, orientation) {
  const m = (px - layout.ox) / layout.scale;
  const n = (py - layout.oy) / layout.scale;
  const x = m / PITCH_LENGTH_M;
  const y = n / PITCH_WIDTH_M;
  if (orientation === "home-right") return { x: 1 - x, y: 1 - y };
  return { x, y };
}

/* ----------------------------------------------------------- interpolation */

/**
 * Index every entity once per source change so lookups stay O(log n).
 *
 * Frames are never rewritten after indexing (the page treats a frame array as
 * immutable), so one index answers every query the view makes.
 */
export function buildEntityIndex(frames) {
  const playersBy = new Map();
  for (let f = 0; f < frames.length; f++) {
    const frame = frames[f];
    const list = frame.players || [];
    for (let p = 0; p < list.length; p++) {
      const pl = list[p];
      if (pl == null || pl.id == null) continue;
      let arr = playersBy.get(pl.id);
      if (arr === undefined) {
        arr = [];
        playersBy.set(pl.id, arr);
      }
      arr.push({ t: frame.t, p: pl });
    }
    // The ball is an entity like any other, kept under a reserved id no squad
    // can collide with, so queries answer through the same path as players.
    if (frame.ball) {
      let arr = playersBy.get("__ball__");
      if (arr === undefined) {
        arr = [];
        playersBy.set("__ball__", arr);
      }
      arr.push({ t: frame.t, p: frame.ball });
    }
  }
  return { frames, playersBy, length: frames.length };
}

/**
 * Interpolated position for one player on the match clock.
 *
 * Splits three cases:
 * - with two bracketing sightings → linear blend, never arrives before `a`;
 * - past the final sighting → drift on last-known velocity up to `holdMs`,
 *   then the alpha ramps out (`fadeMs`) in the "live data delayed" state;
 * - id never seen → `null` (the caller decides what "absent" means, e.g. a
 *   sub whose entrance is in the future).
 *
 * `speed`/`distance` follow the *latest* sighting (they are scalars, not a
 * direction to blend), z blends like x/y when both sides carry it.
 */
export function playerPositionAt(index, id, t, { holdMs = 2000, fadeMs = 800 } = {}) {
  const sightings = index.playersBy.get(id);
  if (!sightings || sightings.length === 0) return null;
  const i = lastAtOrBefore(sightings, t);
  if (i < 0) return null; // Sub on the bench: nothing to show yet.
  const at = sightings[i];
  const next = sightings[i + 1];

  let x = at.p.x;
  let y = at.p.y;
  let z = at.p.z;
  let speed = at.p.speed;
  let distance = at.p.distance;
  let extrapolatedMs = Math.max(0, t - at.t);

  if (next && next.t !== at.t) {
    const k = clamp01((t - at.t) / (next.t - at.t));
    x = at.p.x + (next.p.x - at.p.x) * k;
    y = at.p.y + (next.p.y - at.p.y) * k;
    if (z != null && next.p.z != null) z = at.p.z + (next.p.z - at.p.z) * k;
    if (next.p.speed != null) speed = next.p.speed;
    if (next.p.distance != null) distance = next.p.distance;
  } else {
    // A live feed whose frames stopped: drift on the velocity from this
    // sighting to the one before it, capped at one *previous* interval's
    // displacement — bounded drift toward the last known positions, never a
    // velocity flung forward forever. One sighting alone is held dead still
    // rather than guessed from nothing.
    //
    // A zero hold (an acknowledged departure — a substitution in a recorded
    // match) holds the position exactly: the exit is a fact, not a gap.
    const prev = sightings[i - 1];
    if (holdMs > 0 && prev && at.t !== prev.t) {
      const dt = at.t - prev.t;
      x += (at.p.x - prev.p.x) * Math.min(1, extrapolatedMs / dt);
      y += (at.p.y - prev.p.y) * Math.min(1, extrapolatedMs / dt);
    }
  }

  // Past `holdMs` without a sighting, the marker fades out over `fadeMs`;
  // `holdMs: 0` starts the fade on the last sighting itself, which is what an
  // acknowledged departure (a substitution in a recorded match) uses.
  const over = Math.max(0, extrapolatedMs - holdMs);
  const alpha = clamp01(1 - over / fadeMs);
  return {
    x,
    y,
    z,
    speed,
    distance,
    seenAt: at.t,
    alpha,
    state: alpha <= 0 ? "gone" : alpha < 1 ? "fading" : "ok",
  };
}

/** Same query for the ball, which lives in the index under a reserved id. */
export function ballPositionAt(index, t, opts) {
  return playerPositionAt(index, "__ball__", t, opts);
}

function lastAtOrBefore(sightings, t) {
  let lo = 0;
  let hi = sightings.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (sightings[mid].t <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/**
 * One motion step toward a target, capped in speed.
 *
 * This is what removes jumps: the *drawn* position chases each interpolated
 * target but can only cover `maxStepPerMs * dt`, so a reconnection snapshot or
 * a scrub lands at the same place the markers walked to. Anything the feed
 * produces *inside* a frame interval moves slower than the cap on both players
 * and the ball, so the cap cannot visibly distort ordinary play.
 */
export function glideTowards(current, target, dt, maxStepPerMs) {
  const dx = target.x - current.x;
  const dy = target.y - current.y;
  const d = Math.hypot(dx, dy);
  if (d <= maxStepPerMs * dt || d === 0) return { x: target.x, y: target.y };
  const k = (maxStepPerMs * dt) / d;
  return { x: current.x + dx * k, y: current.y + dy * k };
}

/** Default per-entity glide caps in normalised units per millisecond. */
export const GLIDE_CAPS = {
  player: 0.36 / 1000, // ≈ 38 m/s of correction; no feed move reaches this.
  ball: 1.2 / 1000,
};

export const STALE = { holdMs: 2000, fadeMs: 800 };

export function nearestTo(x, y, entries) {
  let best = null;
  let bestD = Infinity;
  for (const e of entries) {
    if (e == null || e.x == null || e.y == null) continue;
    const d = Math.hypot(e.x - x, e.y - y);
    if (d < bestD) {
      bestD = d;
      best = e;
    }
  }
  return best;
}

export const PITCH = { LENGTH_M: PITCH_LENGTH_M, WIDTH_M: PITCH_WIDTH_M };
