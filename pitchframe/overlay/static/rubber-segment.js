/* RubberSegment — a segmented control whose thumb stretches between two slots.
 *
 * A port of the React Bits component of the same name (https://reactbits.dev), with the
 * React half replaced by a module lifecycle: `createRubberSegment(host, options)` returns
 * `{ set, destroy }`. Everything under those two methods is the component's own logic —
 * the dilation, the squash, the rubber band against the ends of the track, the flick
 * projection — transcribed rather than reinvented.
 *
 * Ported from React Bits, Copyright (c) 2026 David Haz.
 * MIT + Commons Clause License Condition v1.0. The full notice ships beside this file as
 * vendor/react-bits-LICENSE.md, and the Commons Clause covers "a ported version" as well as
 * the components themselves: this file is part of this application and is not redistributed
 * on its own.
 *
 * Four deliberate differences from the component, each because this page is not a React
 * app:
 *
 *   - `motion` is not imported. The springs and the one cubic-bezier tween come from
 *     ./motion.js, which is the part of that package this component uses and nothing else.
 *   - Colours come from the stylesheet rather than from props. The stylesheet's tokens sit
 *     beside the verdict colours for the same reason the sea's do: a control must not be
 *     able to borrow the green and vermilion that mean published and caught.
 *   - A slot is a string or `{ value, label }`. The component also accepts an icon node;
 *     neither control on this page has one, so the port takes a DOM node and appends it,
 *     and draws nothing else.
 *   - `prefers-reduced-motion` is watched rather than sampled once at mount, so turning the
 *     setting on while a thumb is travelling stops it there.
 */

import { Value, cubicBezier, prefersReducedMotion, watchReducedMotion } from "./motion.js";

const EASE_OUT = cubicBezier(0.23, 1, 0.32, 1);
const SPRING_UI = { duration: 0.3, bounce: 0 };
const SPRING_MOMENTUM = { duration: 0.4, bounce: 0.2 };
const SPRING_RELAX = { duration: 0.16, bounce: 0 };
const DILATE = 0.19;
const HANDOFF = 0.15;
const FLICK = 110;
const MAX_VELOCITY = 2000;
const DEADZONE = 4;
const SLOP = 10;
const RUBBER = 0.55;
const SIZES = {
  sm: { height: 28, font: 12, pad: 10, min: 36 },
  md: { height: 36, font: 13, pad: 14, min: 44 },
  lg: { height: 44, font: 14, pad: 18, min: 48 }
};

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const rubber = (over, dim) => (over * dim * RUBBER) / (dim + RUBBER * Math.abs(over));
const project = (v, glide) => {
  const d = 1 - 0.1 * Math.pow(0.05, glide / 100);
  return ((v / 1000) * d) / (1 - d);
};
const velocityOf = (hist, now) => {
  const recent = hist.filter(([t]) => now - t <= 100);
  if (recent.length < 2) return 0;
  const [t0, x0] = recent[0];
  const [t1, x1] = recent[recent.length - 1];
  return t1 - t0 >= 8 ? ((x1 - x0) / (t1 - t0)) * 1000 : 0;
};
const nearestSlot = (slots, x) => {
  let best = 0;
  for (let i = 1; i < slots.length; i++) {
    if (Math.abs((slots[i].l + slots[i].r) / 2 - x) < Math.abs((slots[best].l + slots[best].r) / 2 - x)) {
      best = i;
    }
  }
  return best;
};

const asItem = (item) =>
  typeof item === "string" ? { value: item, label: item, icon: null } : { icon: null, ...item };

export function createRubberSegment(host, options = {}) {
  const list = (options.items || []).map(asItem);
  if (!list.length) throw new Error("createRubberSegment needs at least one item");

  const preset = SIZES[options.size] || SIZES.md;
  const radius = options.radius === undefined ? 10 : options.radius;
  const inset = options.inset === undefined ? 3 : options.inset;
  const thumbRadius = Math.max(0, radius - inset);
  const equal = options.equalSlots !== false;
  const stretch = (options.stretch === undefined ? 100 : options.stretch) / 100;
  const squash = options.squash === undefined ? 3 : options.squash;
  const speed = Math.max(0.01, options.speed === undefined ? 1 : options.speed);
  const glide = options.glide === undefined ? 75 : options.glide;
  const draggable = options.draggable !== false;
  const t = (seconds) => seconds / speed;

  let disabled = Boolean(options.disabled);
  let reduce = prefersReducedMotion();
  let index = Math.max(
    0,
    list.findIndex((item) => item.value === (options.value === undefined ? list[0].value : options.value))
  );
  let committed = index;
  let destroyed = false;

  /* ------------------------------------------------------------------- the DOM -- */

  const track = document.createElement("div");
  track.className = "rubber-segment";
  track.setAttribute("role", "radiogroup");
  track.setAttribute("aria-label", options.label || "Segmented control");
  if (equal) track.dataset.equal = "";
  if (draggable && !disabled) track.dataset.draggable = "";
  if (disabled) track.setAttribute("aria-disabled", "true");

  /* Geometry comes from the options, as it does from the component's props; colour comes
     from the stylesheet, which may override any of these through `tokens`. */
  const geometry = {
    "--rs-radius": `${radius}px`,
    "--rs-inset": `${inset}px`,
    "--rs-thumb-radius": `${thumbRadius}px`,
    "--rs-h": `${preset.height}px`,
    "--rs-font": `${preset.font}px`,
    "--rs-pad": `${preset.pad}px`,
    "--rs-min": `${preset.min}px`
  };
  for (const [name, value] of Object.entries({ ...geometry, ...(options.tokens || {}) })) {
    track.style.setProperty(name, value);
  }

  const buttons = list.map((item, i) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rubber-segment__item";
    button.setAttribute("role", "radio");
    button.tabIndex = -1;
    if (item.icon) button.append(item.icon);
    button.append(document.createTextNode(item.label));
    button.addEventListener("pointerdown", (event) => onPointerDown(event, i));
    button.addEventListener("keydown", onKeyDown);
    /* A tap reaches this control through the pointer events above, which is why upstream's
       `onClick` has no pointer job to do here — but it is also what Space, Enter and a
       screen reader's own activation fire, and a `radiogroup` whose slots a keyboard
       cannot choose is radio in name only. A pointer click and the activation above both
       arrive on a slot that is already committed, so this cannot choose twice. */
    button.addEventListener("click", (event) => {
      if (disabled || event.detail !== 0) return;
      choose(i);
    });
    return button;
  });

  const thumb = document.createElement("div");
  thumb.className = "rubber-segment__thumb";
  thumb.setAttribute("aria-hidden", "true");
  for (const item of list) {
    const copy = document.createElement("span");
    copy.className = "rubber-segment__item rubber-segment__copy";
    if (item.icon) copy.append(item.icon.cloneNode ? item.icon.cloneNode(true) : item.icon);
    copy.append(document.createTextNode(item.label));
    thumb.append(copy);
  }

  track.append(...buttons, thumb);
  host.append(track);

  /* ----------------------------------------------------------------- the values -- */

  let box = null;
  let slots = [];
  let handoff = 0;
  let generation = 0;
  let drag = null;

  const paint = () => {
    const left = Math.max(0, edges.left.value);
    const right = Math.max(0, innerWidth.value - edges.right.value);
    thumb.style.clipPath = `inset(0 ${right}px 0 ${left}px round ${thumbRadius}px)`;
  };

  const innerWidth = new Value(0);
  const edges = {
    left: new Value(0, { onChange: paint }),
    right: new Value(0, { onChange: paint })
  };

  const markCurrent = () => {
    buttons.forEach((button, i) => {
      button.setAttribute("aria-checked", String(i === index));
      button.tabIndex = i === index && !disabled ? 0 : -1;
      button.disabled = disabled;
    });
  };

  /* A value arriving from outside — a link that opens on "analyst" — jumps rather than
     slides, because a slide would animate towards a state the reader already chose. */
  const jumpTo = (i) => {
    const slot = slots[i];
    if (!slot) return;
    window.clearTimeout(handoff);
    generation += 1;
    edges.left.jump(slot.l);
    edges.right.jump(slot.r);
  };

  function measure() {
    if (destroyed) return;
    const rect = track.getBoundingClientRect();
    /* Hidden — a closed dialog, an unloaded font — measures as nothing. Jumping to a
       degenerate slot would park the thumb in the corner, so nothing is measured until
       there is something to measure; the observer calls again when there is. */
    if (!rect.width) return;
    box = rect;
    slots = list.map((_, i) => {
      const element = buttons[i];
      if (!element) return { l: 0, r: 0 };
      const own = element.getBoundingClientRect();
      return { l: own.left - rect.left - inset, r: own.right - rect.left - inset };
    });
    innerWidth.set(Math.max(0, rect.width - inset * 2));
    jumpTo(committed);
    paint();
  }

  /* ------------------------------------------------------------------ the motion -- */

  const commit = (i) => {
    committed = i;
    if (i === index) return;
    index = i;
    /* Focus follows the value on a key press, and only there: a drag must not move focus
       out from under a finger. */
    markCurrent();
    if (options.onChange) options.onChange(list[i].value, i);
  };

  const land = (to, velocity, flick, withSquash) => {
    const target = slots[to];
    if (!target) return;
    const mine = ++generation;
    const centre = (edges.left.value + edges.right.value) / 2;
    const dir = Math.sign((target.l + target.r) / 2 - centre) || 1;
    const leading = dir > 0 ? "right" : "left";
    const trailing = dir > 0 ? "left" : "right";
    const leadTo = dir > 0 ? target.r : target.l;
    const trailTo = dir > 0 ? target.l : target.r;
    const preset_ = flick ? SPRING_MOMENTUM : SPRING_UI;
    const velocityFor = (edge) => clamp(velocity === null ? edges[edge].getVelocity() : velocity, -MAX_VELOCITY, MAX_VELOCITY);

    edges[leading].animateTo(leadTo, {
      type: "spring",
      duration: t(preset_.duration),
      bounce: preset_.bounce,
      velocity: velocityFor(leading)
    });

    const trailVelocity = velocityFor(trailing);
    if (!withSquash || squash <= 0) {
      edges[trailing].animateTo(trailTo, {
        type: "spring",
        duration: t(SPRING_UI.duration),
        bounce: SPRING_UI.bounce,
        velocity: trailVelocity
      });
      return;
    }
    edges[trailing].animateTo(trailTo + dir * squash, {
      type: "spring",
      duration: t(SPRING_UI.duration),
      bounce: SPRING_UI.bounce,
      velocity: trailVelocity,
      onDone: () => {
        if (generation !== mine) return;
        edges[trailing].animateTo(trailTo, {
          type: "spring",
          duration: t(SPRING_RELAX.duration),
          bounce: SPRING_RELAX.bounce
        });
      }
    });
  };

  /* The stretched phase: both edges leave the old slot together and reach past the new
     one by `stretch`, and the squash release takes over 150ms later. */
  const travel = (from, to) => {
    const a = slots[from];
    const b = slots[to];
    if (!a || !b) return;
    window.clearTimeout(handoff);
    generation += 1;
    if (reduce) {
      edges.left.jump(b.l);
      edges.right.jump(b.r);
      return;
    }
    const tween = { type: "tween", duration: t(DILATE), easing: EASE_OUT };
    edges.left.animateTo(b.l + (Math.min(a.l, b.l) - b.l) * stretch, tween);
    edges.right.animateTo(b.r + (Math.max(a.r, b.r) - b.r) * stretch, tween);
    handoff = window.setTimeout(() => land(to, null, false, true), t(HANDOFF) * 1000);
  };

  /* ----------------------------------------------------------------- the pointer -- */

  const localX = (event) => event.clientX - (box ? box.left : 0) - inset;

  function onPointerDown(event, i) {
    if (disabled || drag || event.button !== 0) return;
    box = track.getBoundingClientRect();
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* A pointer that has already gone cannot be captured; the drag below still works. */
    }
    const x = localX(event);
    const onThumb = draggable && x >= edges.left.value && x <= edges.right.value;
    drag = { id: event.pointerId, x0: x, slot: i, onThumb, live: false, offset: 0, width: 0, hist: [[event.timeStamp, x]] };
    if (onThumb) {
      window.clearTimeout(handoff);
      generation += 1;
      edges.left.stop();
      edges.right.stop();
    } else if (!reduce) {
      event.currentTarget.dataset.pressed = "";
    }
  }

  function onPointerMove(event) {
    const current = drag;
    if (!current || event.pointerId !== current.id || !current.onThumb) return;
    const x = localX(event);
    current.hist.push([event.timeStamp, x]);
    if (current.hist.length > 8) current.hist.shift();
    if (!current.live) {
      if (Math.abs(x - current.x0) < DEADZONE) return;
      current.live = true;
      current.offset = x - edges.left.value;
      current.width = edges.right.value - edges.left.value;
      track.dataset.held = "";
    }
    const width = innerWidth.value;
    const left = x - current.offset;
    const maxLeft = width - current.width;
    if (reduce) {
      const held = clamp(left, 0, maxLeft);
      edges.left.set(held);
      edges.right.set(held + current.width);
    } else if (left < 0) {
      edges.left.set(0);
      edges.right.set(current.width - rubber(-left, current.width));
    } else if (left > maxLeft) {
      edges.right.set(width);
      edges.left.set(maxLeft + rubber(left - maxLeft, current.width));
    } else {
      edges.left.set(left);
      edges.right.set(left + current.width);
    }
  }

  const release = () => {
    const current = drag;
    drag = null;
    delete track.dataset.held;
    if (current) delete buttons[current.slot].dataset.pressed;
    return current;
  };

  function onPointerUp(event) {
    const current = drag;
    if (!current || event.pointerId !== current.id) return;
    release();
    const x = localX(event);
    if (!current.live) {
      /* A tap, not a drag: the slack allowance keeps a shaky finger on the same slot from
         being read as a choice of another one. */
      if (Math.abs(x - current.x0) <= SLOP && current.slot !== committed) {
        const from = committed;
        commit(current.slot);
        travel(from, current.slot);
      }
      return;
    }
    const velocity = velocityOf(current.hist, event.timeStamp);
    const flick = Math.abs(velocity) > FLICK;
    let to = nearestSlot(slots, (edges.left.value + edges.right.value) / 2 + project(velocity, glide));
    /* A flick that would land on the slot it started from has to carry: otherwise a hard
       flick on the current slot does nothing at all, which reads as a dead control. */
    if (flick && to === committed) to = clamp(to + Math.sign(velocity), 0, list.length - 1);
    commit(to);
    if (reduce) jumpTo(to);
    else land(to, velocity, flick, flick);
  }

  function onPointerCancel(event) {
    const current = drag;
    if (!current || event.pointerId !== current.id) return;
    release();
    if (!current.live) return;
    if (reduce) jumpTo(committed);
    else land(committed, null, false, false);
  }

  /* ----------------------------------------------------------------- the keyboard -- */

  /* Space, Enter and a screen reader's activation all arrive as a click on the slot, and
     they commit through the two calls a tap uses — so a slot chosen from the keyboard
     travels to its place exactly the way a slot chosen by a finger does. The arrow keys
     do not come through here: they carry the focus with them, and the thumb follows the
     focus without a journey. */
  function choose(to) {
    if (to < 0 || to === committed) return;
    const from = committed;
    commit(to);
    if (reduce) jumpTo(to);
    else travel(from, to);
  }

  function onKeyDown(event) {
    if (disabled) return;
    const last = list.length - 1;
    let next = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = Math.min(last, index + 1);
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = Math.max(0, index - 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next === null) return;
    event.preventDefault();
    if (next === index) return;
    commit(next);
    jumpTo(next);
    buttons[next].focus();
  }

  track.addEventListener("pointermove", onPointerMove);
  track.addEventListener("pointerup", onPointerUp);
  track.addEventListener("pointercancel", onPointerCancel);
  track.addEventListener("lostpointercapture", onPointerCancel);

  /* ------------------------------------------------------------------- lifecycle -- */

  const observer = new ResizeObserver(measure);
  observer.observe(track);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(measure);

  const unwatch = watchReducedMotion((matches) => {
    reduce = matches;
    /* Nothing may keep travelling for a reader who has just asked it not to. */
    if (reduce) jumpTo(committed);
  });

  markCurrent();
  measure();

  return {
    /* `value` is the only property the page can set from outside, and it is a jump: the
       component's own contract for a controlled change. */
    set(changes = {}) {
      if (changes.value !== undefined) {
        const next = list.findIndex((item) => item.value === changes.value);
        if (next >= 0 && next !== index) {
          index = next;
          committed = next;
          markCurrent();
          jumpTo(next);
        }
      }
      if (changes.disabled !== undefined) {
        disabled = Boolean(changes.disabled);
        track.toggleAttribute("aria-disabled", disabled);
        if (disabled) delete track.dataset.draggable;
        else if (draggable) track.dataset.draggable = "";
        markCurrent();
      }
      if (changes.tokens) {
        for (const [name, value] of Object.entries(changes.tokens)) track.style.setProperty(name, value);
      }
    },
    destroy() {
      destroyed = true;
      window.clearTimeout(handoff);
      edges.left.stop();
      edges.right.stop();
      observer.disconnect();
      unwatch();
      track.remove();
    }
  };
}
