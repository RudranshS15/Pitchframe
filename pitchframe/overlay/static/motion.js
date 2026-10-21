/* The animation primitives the ported control modules need, in the shape `motion` gave them.
 *
 * `RubberSegment`, `Dock` and `WakeSlider` are ports of React Bits components, and the one
 * dependency those components declare is `motion`. This project has no React, no package
 * manager and no build step, so the package cannot be imported. What is here is the part of
 * it the three components actually use, and nothing else:
 *
 *   cubicBezier(x1, y1, x2, y2)   an easing curve, as one function
 *   new Value(initial, { onChange })   one animated number, moved by either
 *     value.animateTo(target, { type: "spring", duration, bounce })      a preset, or
 *     value.animateTo(target, { type: "spring", mass, stiffness, damping })   its numbers, or
 *     value.animateTo(target, { type: "tween", duration, easing })       a curve
 *   prefersReducedMotion() / watchReducedMotion(fn)
 *
 * The segmented control drives both edges of its thumb through `Value`, and the wake slider
 * drives its handle and the smoothed speed of that handle the same way. The dock keeps its
 * own loop instead — it integrates every tile together and rescales them when the bar's own
 * size changes, which is one simulation rather than one value per tile — and takes only the
 * reduced-motion watch from here.
 *
 * A value is stepped per frame rather than handed to the Web Animations API. That is not a
 * preference: both components read a number while it is still travelling — the thumb is
 * clipped between two moving edges, and a released one hands its spring the velocity the
 * finger was moving at — and neither is expressible as a keyframe.
 *
 * The spring is integrated with the same implicit step the Dock's own loop used upstream,
 * so the port settles the way the component settled. A duration-and-bounce preset is the
 * same second-order system written differently, and is read as such: the duration sets the
 * frequency and the bounce sets the damping ratio (bounce b means zeta = 1 - b, so 0 is
 * critically damped and no preset here overshoots by more than about 2%).
 */

const REST_SPEED = 0.01;
const REST_DISTANCE = 0.01;

/* One frame loop for every value on the page, started on demand and stopped as soon as
   nothing is moving: an idle control must not hold the main thread awake. */
const live = new Set();
let frame = 0;
let previous = 0;

/* Whether this loop is inside its own step right now.

   A value's `onChange` is allowed to move another value — the wake slider hands its own
   handle's velocity to a second spring on every step, which is how the wake is drawn from
   the speed the handle is travelling at — and that lands back in `wake()` while the loop
   is mid-step. Without this flag `wake()` cannot tell that the loop is already here: it
   clears `frame` on entry, so the re-entry schedules a *second* chain on top of the one
   the end of the step is about to schedule. Two chains become four, four become eight, and
   the page ends up running its controls hundreds of times a second instead of once a
   frame — a replay that cannot hold sixty frames and answers a click a fifth of a second
   late, from a loop that was meant to cost nothing when nobody was touching it.

   While the flag is up, the step in progress owns the next frame; `wake()` has nothing to
   do. */
let draining = false;

function drain(now) {
  frame = 0;
  draining = true;
  const dt = previous ? Math.min((now - previous) / 1000, 1 / 20) : 1 / 60;
  previous = now;
  /* `finally` for the next frame as much as for the step: a value that throws must not
     leave the flag up, because a loop that thinks it is still stepping never steps again. */
  try {
    for (const value of Array.from(live)) value.step(dt);
  } finally {
    draining = false;
    if (live.size) frame = requestAnimationFrame(drain);
    else previous = 0;
  }
}

function wake() {
  if (frame || draining) return;
  previous = 0;
  frame = requestAnimationFrame(drain);
}

export function cubicBezier(x1, y1, x2, y2) {
  const coefficient = (a, b) => 1 - 3 * b + 3 * a;
  const curve = (t, a, b) => ((coefficient(a, b) * t + (3 * b - 6 * a)) * t + 3 * a) * t;
  const slope = (t, a, b) => 3 * coefficient(a, b) * t * t + 2 * (3 * b - 6 * a) * t + 3 * a;

  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;

    /* Newton first because it is exact in one or two steps for these curves, then a
       bisection to bound the answer when the slope is near flat — a cubic-bezier with a
       near-horizontal middle segment sends Newton anywhere, and a wrong t here is a thumb
       that jumps. */
    let t = x;
    for (let i = 0; i < 6; i++) {
      const error = curve(t, x1, x2) - x;
      if (Math.abs(error) < 1e-6) return curve(t, y1, y2);
      const gradient = slope(t, x1, x2);
      if (Math.abs(gradient) < 1e-6) break;
      t -= error / gradient;
      if (t < 0 || t > 1) break;
    }
    let low = 0;
    let high = 1;
    t = x;
    for (let i = 0; i < 24; i++) {
      const at = curve(t, x1, x2);
      if (Math.abs(at - x) < 1e-6) break;
      if (at < x) low = t;
      else high = t;
      t = (low + high) / 2;
    }
    return curve(t, y1, y2);
  };
}

export function prefersReducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

/* Watched rather than sampled once. A reader who turns the setting on while something is
   mid-flight should see it stop, not wait for a reload. */
export function watchReducedMotion(listener) {
  const query = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");
  if (!query) return () => {};
  const onChange = (event) => listener(event.matches);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

export class Value {
  constructor(initial = 0, { onChange = null } = {}) {
    this.current = initial;
    this.velocity = 0;
    this.onChange = onChange;
    this.spec = null;
    this.target = initial;
    this.from = initial;
    this.elapsed = 0;
    this.onDone = null;
  }

  get value() {
    return this.current;
  }

  getVelocity() {
    return this.velocity;
  }

  /* An interruption, not a move: whatever was running is abandoned where it stands. */
  stop() {
    live.delete(this);
    this.spec = null;
    this.onDone = null;
    this.velocity = 0;
  }

  /* Jump without animating, which is what a value set from outside the control means —
     a link that opens on "analyst" puts the thumb there rather than sliding it. */
  jump(next) {
    this.stop();
    this.write(next);
  }

  /* Move without disturbing a run in progress, for a drag: the finger is the clock. */
  set(next) {
    if (next === this.current) return;
    this.write(next);
  }

  animateTo(target, spec = {}) {
    this.target = target;
    this.from = this.current;
    this.elapsed = 0;
    /* Upstream passes a velocity on release and reads the current one otherwise, so an
       explicit 0 is meaningful: it means "start stationary". */
    if (spec.velocity !== undefined && spec.velocity !== null) this.velocity = spec.velocity;
    this.spec = spec;
    this.onDone = spec.onDone || null;

    if (spec.type === "spring") {
      const { mass, stiffness, damping } = springConstants(spec);
      this.mass = mass;
      this.stiffness = stiffness;
      this.damping = damping;
    } else {
      this.ease = spec.easing || ((t) => t);
      this.duration = Math.max(1e-4, spec.duration || 0);
    }
    live.add(this);
    wake();
    return this;
  }

  step(dt) {
    const spec = this.spec;
    if (!spec) return;

    if (spec.type === "spring") {
      const k = this.stiffness / this.mass;
      const c = this.damping / this.mass;
      /* Substepped at the rate the Dock used upstream, so a slow frame cannot make a
         spring unstable. */
      const steps = Math.max(1, Math.ceil(dt * 240));
      const h = dt / steps;
      for (let i = 0; i < steps; i++) {
        this.velocity =
          (this.velocity + h * k * (this.target - this.current)) / (1 + h * c + h * h * k);
        this.current += this.velocity * h;
      }
      if (Math.abs(this.velocity) < REST_SPEED && Math.abs(this.target - this.current) < REST_DISTANCE) {
        this.settle();
        return;
      }
      this.notify();
      return;
    }

    this.elapsed += dt;
    const progress = Math.min(1, this.elapsed / this.duration);
    const next = this.from + (this.target - this.from) * this.ease(progress);
    /* Kept so that a tween interrupted by a flick hands the spring a real velocity: a
       released thumb has to carry on at the speed the finger was moving. */
    this.velocity = dt > 0 ? (next - this.current) / dt : 0;
    this.current = next;
    if (progress >= 1) {
      this.settle();
      return;
    }
    this.notify();
  }

  settle() {
    const done = this.onDone;
    this.current = this.target;
    this.velocity = 0;
    this.stop();
    this.notify();
    if (done) done();
  }

  write(next) {
    this.current = next;
    this.notify();
  }

  notify() {
    if (this.onChange) this.onChange(this.current);
  }
}

/* A preset is the same spring as a stiffness and a damping; this is the translation. */
function springConstants(spec) {
  if (spec.stiffness) {
    return { mass: spec.mass || 1, stiffness: spec.stiffness, damping: spec.damping || 0 };
  }
  const omega = 5.5 / Math.max(spec.duration || 0.3, 1e-4);
  const zeta = 1 - Math.min(1, Math.max(0, spec.bounce || 0));
  return { mass: 1, stiffness: omega * omega, damping: 2 * zeta * omega };
}
