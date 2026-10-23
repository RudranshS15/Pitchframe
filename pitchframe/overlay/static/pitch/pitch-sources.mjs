/**
 * The two data sources the pitch view consumes.
 *
 * Both produce the *same thing from the view's side* — an array of frames in
 * match-clock order that the view re-indexes when it changes — and differ only
 * in how the array evolves:
 *
 * - `createReplaySource`: a finished recording of the whole match, handed over
 *   at once. The playhead is entirely the view's business: play/pause, speed
 *   and the scrubber are view controls, and the source never goes stale.
 * - `createLiveSource`: frames arrive over time as the "feed" published them.
 *   `injectDrop(seconds)` simulates a dropped connection so the delayed-state
 *   and reconnect paths are exercisable offline — after the gap the source
 *   publishes a full snapshot and carries on, exactly what a real feed's
 *   resync looks like.
 */

/**
 * Wrap a whole recording for playback. `frames` must be sorted by `t`.
 */
export function createReplaySource(recording) {
  return {
    kind: "replay",
    frames: recording.frames,
    teams: recording.teams,
    durationMs: recording.durationMs,
    isLive: false,
    capabilities: recording.capabilities ?? {},
    dispose() {},
  };
}

/**
 * Stream the same generator as a live feed.
 *
 * `frameIntervalMs` is how often a frame is *published* (real feeds update far
 * less often than the screen). A `dropAfter` — frames after which to go silent
 * for `dropSeconds` — is armed at construction and can also be fired by hand
 * via `injectDrop(seconds, afterN)`. The resynchronisation burst after a gap
 * carries a fresh snapshot of every player, which is the reconnect path.
 */
export function createLiveSource(recording, { frameIntervalMs = 1000, dropAfter = 40, dropSeconds = 3 } = {}) {
  const frames = recording.frames;
  const capabilities = recording.capabilities ?? {};
  let emitted = 0;
  let timer = null;
  let dropUntil = -Infinity;
  let onChange = () => {};
  let pendingDrop = null;

  function publish() {
    if (dropUntil !== -Infinity) return;
    if (emitted >= frames.length) {
      stop();
      return;
    }
    emitted++;
    // Hand over only the tail, so a long match does not grow the array the
    // view keeps without bound.
    const from = Math.max(0, emitted - 40);
    onChange(frames.slice(from, emitted), false);
    if (dropAfter > 0 && emitted === dropAfter) {
      dropUntil = now() + dropSeconds * 1000;
      pendingDrop = setTimeout(resync, dropSeconds * 1000);
    }
  }

  function resync() {
    const wasGone = dropUntil !== -Infinity;
    dropUntil = -Infinity;
    if (!wasGone) return;
    // A full snapshot: everything the feed knows right now, in one burst.
    emitted = Math.min(emitted + 1, frames.length);
    const from = Math.max(0, emitted - 40);
    onChange(frames.slice(from, emitted), true);
  }

  function now() {
    return typeof performance !== "undefined" ? performance.now() : Date.now();
  }

  function start() {
    stop();
    timer = setInterval(publish, frameIntervalMs);
  }

  function stop() {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  return {
    kind: "live",
    frames,
    durationMs: recording.durationMs,
    isLive: true,
    capabilities,
    set onChange(fn) {
      onChange = fn ?? onChange;
    },
    get onChange() {
      return onChange;
    },
    start,
    stop,
    /** Silently test-fire the drop/resync cycle, keeping the timer running. */
    injectDrop(seconds) {
      dropUntil = now() + seconds * 1000;
      if (pendingDrop !== null) clearTimeout(pendingDrop);
      pendingDrop = setTimeout(resync, seconds * 1000);
    },
    dispose() {
      stop();
      if (pendingDrop !== null) clearTimeout(pendingDrop);
    },
  };
}
