/* WakeSlider — a slider whose track lifts into a wake around the handle as it moves.
 *
 * A port of the React Bits component of the same name (https://reactbits.dev), with the
 * React half replaced by a module lifecycle: `createWakeSlider(host, options)` returns
 * `{ set, destroy }`. Everything under those two methods is the component's own logic —
 * the snapping grid, the wake's reach and the skew behind the handle, the two springs, the
 * flatness test that decides when the track has nothing left to draw — transcribed rather
 * than reinvented.
 *
 * Ported from React Bits, Copyright (c) 2026 David Haz.
 * MIT + Commons Clause License Condition v1.0. The full notice ships beside this file as
 * vendor/react-bits-LICENSE.md, and the Commons Clause covers "a ported version" as well as
 * the components themselves: this file is part of this application and is not redistributed
 * on its own.
 *
 * Four deliberate differences from the component, each because this page is not a React app:
 *
 *   - `motion` is not imported. Both springs come from ./motion.js, the part of that package
 *     this component uses: a `Value` already integrates a spring per frame and already
 *     reports the velocity of that integration, which is the pair the wake is drawn from.
 *     The component samples velocity through `useVelocity`; here it is `getVelocity()`, so
 *     the number is the spring's own rather than a difference of two samples.
 *   - Colours come from the stylesheet rather than from props, as they do for the other
 *     ports here. `--ws-fill`, `--ws-track` and `--ws-crest` are set in the page's own
 *     section of the stylesheet, beside the verdict colours, so a control cannot reach the
 *     green and vermilion that mean published and caught. `tokens` still overrides any of
 *     them, which is how the component's colours stay reachable.
 *   - The crest layer is always built. The component renders it only when a `crestColor` is
 *     given; here the tint is a stylesheet decision, so the layer exists and a page that
 *     wants no tint sets `--ws-crest: transparent`.
 *   - `prefers-reduced-motion` is watched rather than sampled once at mount, so turning the
 *     setting on flattens a wake that is already raised.
 *
 * Two properties of the component are load-bearing and are kept as they are. The handle is
 * a real `role="slider"` button, because the wake is decoration over a control that has to
 * announce its own value; and a commit is a *snapped* value, so the handle lands on the grid
 * rather than wherever a finger stopped.
 */

import { Value, prefersReducedMotion, watchReducedMotion } from "./motion.js";

/* How many times the spring's own time constant fits into a glide: 9.23 is where a
   critically damped spring is within about a hundredth of its target. */
const SETTLE = 9.23;
/* Percentage of the track per second at which the wake is fully raised. */
const FULL_SPEED = 320;
/* Half-width of the wake at rest and at full speed, in bars. */
const MIN_REACH = 1.5;
/* Below this the wake is not merely small, it is flat, and the track stops being written
   to until something moves again. */
const FLAT = 0.002;
/* How much a bar's lift has to change before its height is rewritten. The wake is a
   handful of bars wide and the track is sixty-four, so writing all of them every frame is
   mostly writing where nothing is lifted — and each of those writes is a style
   invalidation. Below this the difference is under a hundredth of a pixel at the sizes
   this control is drawn at. */
const LIFT_EPS = 0.002;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const smoothstep = (s) => s * s * (3 - 2 * s);
const toPct = (v, min, max) => (max > min ? ((v - min) / (max - min)) * 100 : 0);

/* The component's grid, transcribed: a value snaps to the nearest step measured from `min`,
   except that the *last whole step* wins when the end of the range is closer than the grid
   position it would otherwise round to. Without that clause a range of 600–5400 in steps of
   60 could never reach 5400, and a reader dragging to the end of the match would settle a
   minute short of it. */
const snap = (v, min, max, step) => {
  if (!(max > min)) return min;
  if (!(step > 0)) return clamp(v, min, max);
  const lastWhole = min + Math.floor(+((max - min) / step).toFixed(6)) * step;
  const grid = clamp(Math.round((v - min) / step) * step + min, min, lastWhole);
  return +(lastWhole < max && Math.abs(v - max) <= Math.abs(v - grid) ? max : grid).toFixed(6);
};

export function createWakeSlider(host, options = {}) {
  const min0 = options.min === undefined ? 0 : options.min;
  const max0 = options.max === undefined ? 100 : options.max;
  let min = min0;
  let max = max0;
  let step = options.step === undefined ? 1 : options.step;
  const bars = Math.max(2, options.bars === undefined ? 32 : options.bars);
  const height = options.height === undefined ? 56 : options.height;
  const restHeight = options.restHeight === undefined ? 12 : options.restHeight;
  const gap = options.gap === undefined ? 4 : options.gap;
  const sensitivity = options.sensitivity === undefined ? 1 : options.sensitivity;
  const reach = options.reach === undefined ? 6 : options.reach;
  const skew = options.skew === undefined ? 0.6 : options.skew;
  const glide = Math.max(0.01, options.glide === undefined ? 0.3 : options.glide);
  const smoothing = Math.max(1, options.smoothing === undefined ? 100 : options.smoothing);
  const showValue = Boolean(options.showValue);
  const format = options.formatValue || String;
  const ariaLabel = options.ariaLabel || "Value";
  const onChange = options.onChange || null;

  const rest = Math.min(restHeight, height - 1) / height;
  let reduce = prefersReducedMotion();
  let disabled = Boolean(options.disabled);

  /* The value is this module's, unless the caller drives it: `set({ value })` is that
     caller's voice, and a commit from the pointer or the keyboard is this one's. */
  let value = clamp(options.value === undefined ? (options.defaultValue === undefined ? 50 : options.defaultValue) : options.value, min, max);

  const root = document.createElement("div");
  root.className = `wake-slider${options.className ? ` ${options.className}` : ""}`;
  for (const [name, token] of Object.entries(options.tokens || {})) root.style.setProperty(name, token);
  root.style.setProperty("--ws-height", `${height}px`);
  root.style.setProperty("--ws-gap", `${gap}px`);
  root.style.setProperty("--ws-rest", String(rest));

  const track = document.createElement("div");
  track.className = "wake-slider__track";

  const barNodes = [];
  const crestNodes = [];
  for (let i = 0; i < bars; i++) {
    const bar = document.createElement("span");
    bar.className = "wake-slider__bar";
    const crest = document.createElement("span");
    crest.className = "wake-slider__crest";
    bar.append(crest);
    barNodes.push(bar);
    crestNodes.push(crest);
    track.append(bar);
  }

  const handle = document.createElement("button");
  handle.type = "button";
  handle.className = "wake-slider__handle";
  handle.setAttribute("role", "slider");
  handle.setAttribute("aria-label", ariaLabel);
  handle.setAttribute("aria-valuemin", String(min));
  handle.setAttribute("aria-valuemax", String(max));
  track.append(handle);

  /* The readout is beside the track rather than over it, and carries the same formatting
     the handle announces, so a reader who can see the number and a reader who is told it
     are told the same thing. */
  const readout = document.createElement("span");
  readout.className = "wake-slider__value";
  readout.setAttribute("aria-hidden", "true");

  root.append(track);
  if (showValue) root.append(readout);
  host.append(root);

  /* ---------------------------------------------------------------- the springs -- */

  /* Two springs, as the component has them: the handle's own position in percent of the
     track, and the smoothed speed it is travelling at, which is what the wake is drawn
     from. `Value` steps both on the page's one frame loop and stops contributing as soon
     as it has settled, so an untouched slider costs nothing. */
  const w0 = SETTLE / glide;
  const HEAD_SPRING = { type: "spring", mass: 1, stiffness: w0 * w0, damping: 2 * w0 };
  const wv = 2000 / smoothing;
  const SPEED_SPRING = { type: "spring", mass: 1, stiffness: wv * wv, damping: 2.5 * wv };

  let target = toPct(value, min, max);
  let lastAmp = 0;
  /* The lift each bar was last drawn at, so that the paint below can skip the ones whose
     height did not change. `-1` is "never drawn", which is what makes the first paint
     write every bar and no later one repeat it. */
  const drawn = new Float32Array(bars).fill(-1);

  const speed = new Value(0, { onChange: () => paint() });
  const head = new Value(target, {
    onChange: () => {
      /* The component reads this through `useVelocity` and feeds it to the second spring;
         here it is the velocity of the integration itself. */
      speed.animateTo(head.getVelocity(), SPEED_SPRING);
      paint();
    }
  });

  /* --------------------------------------------------------------- the drawing -- */

  /* The component's paint, transcribed. `force` redraws even when the track is flat: a
     value that arrived from outside moves the lit bars without the handle travelling, and
     skipping that write would leave the wake drawn where the handle used to be. */
  function paint(force = false) {
    const h = ((reduce ? target : head.value) / 100) * (bars - 1);
    const v = reduce ? 0 : speed.value;
    const amp = smoothstep(clamp((Math.abs(v) * sensitivity) / FULL_SPEED, 0, 1));
    const dir = Math.sign(v) || 1;
    const r = MIN_REACH + (reach - MIN_REACH) * amp;
    const behind = r * (1 + skew);
    const ahead = r * (1 - 0.5 * skew);
    const lit = Math.round(h);
    const flat = !force && amp < FLAT && lastAmp < FLAT;

    for (let i = 0; i < bars; i++) {
      const bar = barNodes[i];
      const on = i <= lit ? "true" : "false";
      if (bar.dataset.on !== on) bar.dataset.on = on;
      if (flat) continue;
      const d = i - h;
      const R = d * dir < 0 ? behind : ahead;
      const lift = Math.abs(d) < R ? amp * Math.cos((Math.PI * d) / (2 * R)) ** 2 : 0;
      /* The wake is a handful of bars wide, so the same two writes for the same sixty-four
         bars every frame is mostly writing where nothing is lifted. A bar leaves the wake
         as another enters it, and both are written here because both changed: the one that
         dropped back to rest is written at zero rather than merely left alone. */
      if (Math.abs(lift - drawn[i]) < LIFT_EPS) continue;
      drawn[i] = lift;
      bar.style.transform = `scaleY(${rest + lift * (1 - rest)})`;
      crestNodes[i].style.opacity = String(lift);
    }
    lastAmp = amp;
  }

  /* A value this control owns: snapped, announced, and — when the caller asked to be told —
     reported back. The component draws no line between a tap and a drag, and both commit on
     every move, so a reader who drags across the match sees the page follow them. */
  function move(next, tell = true) {
    const clean = clamp(next, min, max);
    if (clean === value) return;
    value = clean;
    announce();
    drive(toPct(value, min, max));
    if (tell && onChange) onChange(value);
  }

  /* The spring carries the handle, exactly as it does when the component's controlled
     `value` prop changes; a handle already standing on the target is left alone. */
  function drive(pct) {
    target = pct;
    if (head.value !== pct) head.animateTo(pct, HEAD_SPRING);
  }

  /* The readout and the announced value come from one formatter, so what a reader sees and
     what is read out to them are the same string. */
  function announce() {
    const text = format(value);
    handle.setAttribute("aria-valuenow", String(value));
    handle.setAttribute("aria-valuetext", text);
    readout.textContent = text;
  }

  /* Where a pointer is on the track, measured the way the component measures it: from the
     track's own box, so the bars' width and the gap between them are already accounted for. */
  function commitFromX(x) {
    const rect = track.getBoundingClientRect();
    if (!rect.width) return;
    let ratio = clamp((x - rect.left) / rect.width, 0, 1);
    if (getComputedStyle(track).direction === "rtl") ratio = 1 - ratio;
    move(snap(min + ratio * (max - min), min, max, step), true);
  }

  handle.tabIndex = disabled ? -1 : 0;
  if (disabled) root.setAttribute("aria-disabled", "true");
  announce();
  paint(true);

  /* --------------------------------------------------------------- the pointer -- */

  /* Capture goes on the track rather than on the bar under the finger, which is what the
     component does and what keeps a drag alive when the pointer leaves the track: the
     handle follows the number, not the pointer's whereabouts. */
  let pointerId = null;

  track.addEventListener("pointerdown", (event) => {
    if (disabled || pointerId !== null) return;
    pointerId = event.pointerId;
    try {
      track.setPointerCapture(event.pointerId);
    } catch {
      /* A pointer that has already gone cannot be captured; the drag below still works. */
    }
    delete root.dataset.instant;
    /* Focused so that the arrows keep working straight after a click, as a native slider
       does — and flagged, because a focus a pointer asked for is not the focus a reader
       navigated to: the stylesheet draws the ring for the second and not the first. */
    root.dataset.pointer = "";
    handle.focus({ preventScroll: true });
    commitFromX(event.clientX);
  });

  track.addEventListener("pointermove", (event) => {
    if (event.pointerId === pointerId) commitFromX(event.clientX);
  });

  const release = (event) => {
    if (event.pointerId !== pointerId) return;
    try {
      track.releasePointerCapture(event.pointerId);
    } catch {
      /* Already released with the pointer, or captured elsewhere since. */
    }
    pointerId = null;
  };
  track.addEventListener("pointerup", release);
  track.addEventListener("pointercancel", release);
  track.addEventListener("lostpointercapture", release);

  /* -------------------------------------------------------------- the keyboard -- */

  /* A step from the keyboard is a decision rather than a journey, so the component drops
     both springs onto the new position instead of animating into it, and tells the track
     not to colour its bars in slowly. `PageUp` and `PageDown` move ten steps, `Home` and
     `End` go to the ends of the match. */
  const STEPS = {
    ArrowRight: 1,
    ArrowUp: 1,
    ArrowLeft: -1,
    ArrowDown: -1,
    PageUp: 10,
    PageDown: -10
  };

  handle.addEventListener("keydown", (event) => {
    if (disabled) return;
    let next;
    if (event.key === "Home") next = min;
    else if (event.key === "End") next = max;
    else if (event.key in STEPS) next = value + STEPS[event.key] * step;
    else return;
    event.preventDefault();
    const clean = snap(next, min, max, step);
    const pct = toPct(clean, min, max);
    delete root.dataset.pointer;
    root.dataset.instant = "true";
    target = pct;
    head.jump(pct);
    speed.jump(0);
    lastAmp = 0;
    paint(true);
    move(clean, true);
  });

  /* ------------------------------------------------------------- the lifecycle -- */

  /* Watched rather than sampled once: a wake that is already raised has to flatten where it
     stands, not on the next move, because the setting is a request about now. */
  const unwatch = watchReducedMotion((matches) => {
    reduce = matches;
    lastAmp = 0;
    paint(true);
  });

  function destroy() {
    unwatch();
    head.stop();
    speed.stop();
    root.remove();
  }

  return {
    /* The caller's voice, which is not a commit: the replay's own clock, and a link that
       opens on a minute, both arrive here, and neither is reported back as a change the
       reader made. A new range is a jump rather than a move — a match re-ranged under a
       travelling handle is a different match — and `tokens` overrides any colour the
       stylesheet set, which is how the component's own colours stay reachable. */
    set(changes = {}) {
      let reRange = false;
      if (changes.min !== undefined) {
        min = changes.min;
        reRange = true;
      }
      if (changes.max !== undefined) {
        max = changes.max;
        reRange = true;
      }
      if (changes.step !== undefined) {
        step = changes.step;
        reRange = true;
      }
      if (reRange) {
        handle.setAttribute("aria-valuemin", String(min));
        handle.setAttribute("aria-valuemax", String(max));
        value = clamp(value, min, max);
        target = toPct(value, min, max);
        head.jump(target);
        speed.jump(0);
        lastAmp = 0;
        paint(true);
        announce();
      }
      if (changes.value !== undefined) move(changes.value, false);
      if (changes.disabled !== undefined) {
        disabled = Boolean(changes.disabled);
        if (disabled) root.setAttribute("aria-disabled", "true");
        else root.removeAttribute("aria-disabled");
        handle.tabIndex = disabled ? -1 : 0;
        if (disabled) pointerId = null;
      }
      if (changes.tokens) {
        for (const [name, token] of Object.entries(changes.tokens)) root.style.setProperty(name, token);
      }
    },
    destroy
  };
}
