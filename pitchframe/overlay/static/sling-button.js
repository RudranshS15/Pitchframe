/* SlingButton — a pad that sits in a well, stretches a band when it is pulled away, and
 * launches a burst of particles through the well when it is released loaded.
 *
 * A port of the React Bits component of the same name (https://reactbits.dev), with the
 * React half replaced by a module lifecycle: `createSlingButton(host, options)` returns
 * `{ set, destroy }`. Everything under those two methods is the component's own logic —
 * the rubber-banding of the pull, the band drawn as the two tangents from the well to the
 * pad, the power arc that fills as the pull passes `armAt`, the aim of the pad's glyph,
 * the arm-and-release rule, the particle burst and its cone, and the spring that carries
 * the pad back through its seat — transcribed rather than reinvented.
 *
 * Ported from React Bits, Copyright (c) 2026 David Haz.
 * MIT + Commons Clause License Condition v1.0. The full notice ships beside this file as
 * vendor/react-bits-LICENSE.md, and the Commons Clause covers "a ported version" as well as
 * the components themselves: this file is part of this application and is not redistributed
 * on its own.
 *
 * Five deliberate differences from the component, each because this page is not a React app:
 *
 *   - `motion` is not imported. Both axes of the pull come from ./motion.js: a `Value`
 *     already integrates a spring per frame and takes a velocity on release, which is the
 *     pair this component needs — the pad is thrown back through the well at the speed the
 *     finger was moving when it let go.
 *   - There is no `children` prop and no icon dependency. The component's sample draws its
 *     glyph with `@hugeicons/react` and this page has no icon package, so the default is an
 *     arrow drawn here, and a caller can pass any node instead.
 *   - Props that arrive as React state -- `held`, `armed`, `sent` -- are attributes on the
 *     root (`data-held`, `data-armed`, `data-sent`), because they exist to drive the
 *     stylesheet and a reader of the DOM should be able to see them.
 *   - Colours come from the stylesheet as they do for every other port here. `--sl-pad`,
 *     `--sl-well` and the rest are declared in the page's own section of the stylesheet,
 *     beside the verdict colours, so a control cannot reach the green and vermilion that
 *     mean published and caught by accident; `set({ tokens })` still overrides any of them,
 *     which is how the component's own palette stays reachable.
 *   - The pad is disabled by an attribute rather than by unmounting it, so the launcher on
 *     this page can say *why* it cannot be pulled yet from one place instead of two.
 *
 * Two properties of the component are load-bearing and are kept as they are. The pad is a
 * real `<button>`, so Enter and Space send without any key handling of ours, and a tap that
 * never moved the pad sends too — the sling is a gesture over a control, not a replacement
 * for one. And the burst is thrown along the direction the pad was pulled, which is what
 * makes the gesture read as *aiming* rather than as a button with an effect.
 */

import { Value, prefersReducedMotion, watchReducedMotion } from "./motion.js";

const GAP = 4;
const SLOP = { fine: 4, coarse: 8 };
const FINGER_MAX = 3000;
const HAND_MAX = 6000;
const CANCEL = 0.5;
const POWER_CAP = 1.5;
const DOT_MS = 300;
const EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";
const SENT_MS = 200;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const rubberband = (o, dim, c = 0.55) => (o * dim * c) / (dim + c * Math.abs(o));

/* The pad's default glyph: an arrow pointing away from the well, which is the direction
   the burst travels. Drawn here rather than taken from the icon package the component's
   sample uses — see the note at the top of this file. */
const ARROW = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
  stroke-linecap="round" stroke-linejoin="round" width="100%" height="100%" aria-hidden="true">
  <path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>
</svg>`;

export function createSlingButton(host, options = {}) {
  let config = { ...options };
  let size = number(config.size, 56);
  let strokeWidth = number(config.strokeWidth, 3);
  let maxPull = number(config.maxPull, 160);
  let axis = config.axis || "any";
  let launchSpeed = number(config.launchSpeed, 2600);
  let recoil = number(config.recoil, 0.2);
  let flight = number(config.flight, 120);
  let spread = number(config.spread, 60);
  let tapSends = config.tapSends === undefined ? true : Boolean(config.tapSends);
  let disabled = Boolean(config.disabled);
  let onSend = config.onSend || null;

  let armAt = Math.min(number(config.armAt, 48), 0.8 * maxPull);
  let wellR = size / 2 + GAP + strokeWidth;
  let padR = size / 2 - strokeWidth / 2;
  let extent = wellR + strokeWidth + 2;
  let dotSize = Math.max(6, Math.round(size / 7));
  let dotCount = Math.max(0, Math.round(number(config.particles, 14)));

  let reduce = prefersReducedMotion();
  let held = false;
  let armed = false;
  let grip = null;
  let skipClick = false;
  let dotPending = false;
  let dotTimer = 0;
  let sentTimer = 0;
  let paintQueued = false;
  let alive = true;
  let power = 0;
  let dir = { ux: 0, uy: -1 };

  const root = document.createElement("span");
  root.className = `sling-button${config.className ? ` ${config.className}` : ""}`;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "sling-button__fx");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = `
    <g class="sling-button__tension" style="opacity: 0">
      <path class="sling-button__band"/>
      <path class="sling-button__band sling-button__band--hot"/>
    </g>
    <circle class="sling-button__well"/>
    <circle class="sling-button__arc" pathLength="1" stroke-dasharray="0 1" style="opacity: 0"/>`;
  const tension = svg.querySelector(".sling-button__tension");
  const band = svg.querySelectorAll(".sling-button__band")[0];
  const hot = svg.querySelectorAll(".sling-button__band")[1];
  const arc = svg.querySelector(".sling-button__arc");
  const well = svg.querySelector(".sling-button__well");

  const dots = [];
  const move = document.createElement("span");
  move.className = "sling-button__move";
  const pad = document.createElement("button");
  pad.type = "button";
  pad.className = "sling-button__pad";
  pad.setAttribute("data-sling-pad", "");
  const face = document.createElement("span");
  face.className = "sling-button__face";
  const icon = document.createElement("span");
  icon.className = "sling-button__icon";
  face.append(icon);
  pad.append(face);
  move.append(pad);

  root.append(svg);
  for (let i = 0; i < dotCount; i++) {
    const dot = document.createElement("span");
    dot.className = "sling-button__dot";
    dot.setAttribute("aria-hidden", "true");
    dots.push(dot);
    root.append(dot);
  }
  const hint = document.createElement("span");
  hint.className = "sling-button__sr";
  root.append(move, hint);
  host.append(root);

  /* The pull, on both axes. Setting either value schedules one paint for the frame
     rather than painting per change: a pointer reports dozens of moves a second and a
     band path is four attributes. */
  const px = new Value(0, { onChange: schedulePaint });
  const py = new Value(0, { onChange: schedulePaint });

  function number(value, fallback) {
    return typeof value === "number" && Number.isFinite(value) ? value : fallback;
  }

  function setAttribute(name, on) {
    if (on) root.setAttribute(name, "");
    else root.removeAttribute(name);
  }

  /* Every measurement a pull depends on, derived in one place. The component derives
     these from its props on every render; a port has to do it when the props change, and
     a control resized mid-drag has a stale radius for exactly one frame if it does not. */
  function measure() {
    armAt = Math.min(number(config.armAt, 48), 0.8 * maxPull);
    wellR = size / 2 + GAP + strokeWidth;
    padR = size / 2 - strokeWidth / 2;
    extent = wellR + strokeWidth + 2;
    dotSize = Math.max(6, Math.round(size / 7));
  }

  function applyConfig() {
    measure();
    root.style.setProperty("--sl-size", `${size}px`);
    root.style.setProperty("--sl-svg", `${2 * extent}px`);
    root.style.setProperty("--sl-stroke", `${strokeWidth}px`);
    root.style.setProperty("--sl-dot", `${dotSize}px`);
    svg.setAttribute("viewBox", `${-extent} ${-extent} ${2 * extent} ${2 * extent}`);
    well.setAttribute("r", String(wellR));
    arc.setAttribute("r", String(wellR));
    for (const [name, token] of Object.entries(config.tokens || {})) {
      root.style.setProperty(name, token);
    }
    if (config.icon !== undefined) {
      icon.replaceChildren(config.icon);
    } else if (!icon.childNodes.length) {
      icon.innerHTML = ARROW;
    }
    pad.setAttribute("aria-label", config.ariaLabel || "Send");
    pad.setAttribute("aria-describedby", hint.id || (hint.id = uniqueId()));
    hint.textContent = "Press Enter to send, or drag away and release.";
    setDisabled(disabled);
    px.jump(0);
    py.jump(0);
    paint();
  }

  function uniqueId() {
    uniqueId.seq = (uniqueId.seq || 0) + 1;
    return `sling-hint-${uniqueId.seq}`;
  }

  function setDisabled(next) {
    disabled = next;
    pad.setAttribute("aria-disabled", String(Boolean(disabled)));
    root.toggleAttribute("data-disabled", Boolean(disabled));
    if (disabled && grip) release(grip.id, true);
  }

  function aimIcon(ux, uy, distance) {
    const angle = (Math.atan2(-uy, -ux) * 180) / Math.PI + 90;
    icon.style.transition = "none";
    icon.style.transform = `rotate(${angle * clamp(distance / 12, 0, 1)}deg)`;
  }

  function relaxIcon() {
    icon.style.transition = reduce ? "none" : "transform 360ms cubic-bezier(0.23, 1, 0.32, 1)";
    icon.style.transform = "rotate(0deg)";
  }

  function paint() {
    paintQueued = false;
    if (!alive) return;
    const x = px.value;
    const y = py.value;
    const { ux, uy } = dir;
    const proj = x * ux + y * uy;
    const p = clamp(proj / armAt, 0, 1);
    const distance = Math.hypot(x, y);

    /* The band is the two tangents from the well's edge to the pad's, which is what a
       real band does when it is stretched: it leaves the seat in a straight line on each
       side rather than following the rim. */
    let d = "";
    if (distance > 0.5) {
      const a = Math.atan2(y, x);
      const b = Math.acos(clamp((wellR - padR) / distance, -1, 1));
      d = [a + b, a - b]
        .map((t) => {
          const cx = Math.cos(t);
          const cy = Math.sin(t);
          return `M${(wellR * cx).toFixed(2)},${(wellR * cy).toFixed(2)}L${(x + padR * cx).toFixed(2)},${(y + padR * cy).toFixed(2)}`;
        })
        .join("");
    }
    band.setAttribute("d", d);
    hot.setAttribute("d", d);
    hot.style.opacity = String(p);
    tension.style.opacity = String(clamp(proj / 6, 0, 1));
    arc.setAttribute("stroke-dasharray", `${p} ${1 - p}`);
    arc.setAttribute("stroke-dashoffset", String(p / 2));
    arc.style.opacity = p > 0.01 ? "1" : "0";
    arc.setAttribute("transform", `rotate(${(Math.atan2(-uy, -ux) * 180) / Math.PI})`);
    move.style.transform = `translate(${x}px, ${y}px)`;
    if (grip) aimIcon(ux, uy, distance);
    if (dotPending && proj <= size / 4) launchDot();
  }

  function schedulePaint() {
    if (paintQueued || !alive) return;
    paintQueued = true;
    requestAnimationFrame(paint);
  }

  function launchDot() {
    dotPending = false;
    window.clearTimeout(dotTimer);
    const { ux, uy } = dir;
    relaxIcon();
    const base = Math.atan2(-uy, -ux);
    const cone = (spread * Math.PI) / 180;
    const push = 0.85 + 0.35 * power;
    dots.forEach((dot, i) => {
      const lead = i === 0;
      const angle = base + (lead ? 0 : (Math.random() + Math.random() - 1) * (cone / 2));
      const cx = Math.cos(angle);
      const cy = Math.sin(angle);
      const reach = (lead ? flight : flight * (0.3 + Math.random())) * push;
      const drift = lead ? 0 : (Math.random() - 0.5) * flight * 0.4;
      const scale = lead ? 1 : 0.3 + Math.random() * 0.6;
      const shrink = lead ? 0.6 : scale * (0.2 + Math.random() * 0.4);
      const duration = lead ? DOT_MS : DOT_MS * (0.7 + Math.random());
      const delay = lead ? 0 : Math.random() * 70;
      const to = wellR + reach;
      if (!dot.animate) return;
      dot.animate(
        [
          { transform: `translate(${cx * wellR}px, ${cy * wellR}px) scale(${scale})` },
          { transform: `translate(${cx * to - cy * drift}px, ${cy * to + cx * drift}px) scale(${shrink})` },
        ],
        { duration, delay, easing: EASE_OUT, fill: "none" }
      );
      dot.animate(
        [
          { opacity: 1, offset: 0 },
          { opacity: 1, offset: 0.55 },
          { opacity: 0, offset: 1 },
        ],
        { duration, delay, easing: "linear", fill: "none" }
      );
    });
  }

  function settle(velocity) {
    if (reduce) {
      tension.style.transition = "opacity 200ms ease";
      tension.style.opacity = "0";
      window.setTimeout(() => {
        px.jump(0);
        py.jump(0);
        tension.style.transition = "";
      }, 200);
      return;
    }
    /* `duration` and `bounce` are the component's preset; ./motion.js reads them as the
       same second-order system, and the velocity is the finger's, which is what makes a
       fast flick throw the pad further than a slow one. */
    px.animateTo(0, { type: "spring", duration: 0.4, bounce: recoil, velocity: velocity.x });
    py.animateTo(0, { type: "spring", duration: 0.4, bounce: recoil, velocity: velocity.y });
  }

  function onPointerDown(event) {
    if (disabled || grip || event.button !== 0) return;
    const rect = root.getBoundingClientRect();
    const scale = rect.width / (root.offsetWidth || rect.width) || 1;
    px.stop();
    py.stop();
    const x = px.value;
    const y = py.value;
    const now = Math.hypot(x, y);
    const clamped = Math.min(now, 0.95 * maxPull);
    const raw = now > 0.5 ? (maxPull * clamped) / (maxPull - clamped) : 0;
    grip = {
      id: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scale,
      moved: false,
      hist: [],
      origin: now > 0.5 ? { x: (raw * x) / now, y: (raw * y) / now } : { x: 0, y: 0 },
      slop: event.pointerType === "touch" ? SLOP.coarse : SLOP.fine,
    };
    try {
      pad.setPointerCapture(event.pointerId);
    } catch {
      /* A capture that fails is a capture the element cannot take; the drag still works. */
    }
    held = true;
    setAttribute("data-held", true);
  }

  function onPointerMove(event) {
    if (!grip || grip.id !== event.pointerId) return;
    const dx = (event.clientX - grip.startX) / grip.scale;
    const dy = (event.clientY - grip.startY) / grip.scale;
    let rx = grip.origin.x + dx;
    let ry = grip.origin.y + dy;
    /* One axis, with a little give across it: the component's `axis` prop, which is how a
       pull down a menu or along a bar stays on its rail. */
    if (axis === "horizontal") ry = rubberband(ry, size / 4);
    else if (axis === "vertical") rx = rubberband(rx, size / 4);
    if (!grip.moved && Math.hypot(dx, dy) > grip.slop) grip.moved = true;
    const raw = Math.hypot(rx, ry);
    if (raw < 0.01) return;
    /* The pull is a rubber band rather than a rope: past `maxPull` the distance
       approaches a limit instead of growing, which is what stops a fast drag across the
       page from throwing the pad out of its seat. */
    const d = (maxPull * raw) / (maxPull + raw);
    const ux = rx / raw;
    const uy = ry / raw;
    dir = { ux, uy };
    px.set(d * ux);
    py.set(d * uy);
    const now = performance.now();
    grip.hist.push({ x: d * ux, y: d * uy, t: now });
    while (grip.hist.length > 4 || now - grip.hist[0].t > 80) grip.hist.shift();
    const isArmed = d >= armAt;
    if (isArmed !== armed) {
      armed = isArmed;
      setAttribute("data-armed", armed);
    }
  }

  function release(pointerId, cancelled) {
    if (!grip || grip.id !== pointerId) return;
    const taken = grip;
    grip = null;
    /* A click follows the pointerup, and a drag that sent has already been answered:
       without this the same gesture would send twice. */
    skipClick = true;
    try {
      pad.releasePointerCapture(pointerId);
    } catch {
      /* Already released, or never captured. */
    }

    const distance = Math.hypot(px.value, py.value);
    const p = distance / armAt;
    const { ux, uy } = dir;
    let vx = 0;
    let vy = 0;
    if (!cancelled && taken.hist.length > 1) {
      const first = taken.hist[0];
      const last = taken.hist[taken.hist.length - 1];
      const dt = last.t - first.t;
      if (dt > 0 && performance.now() - last.t < 50) {
        vx = ((last.x - first.x) / dt) * 1000;
        vy = ((last.y - first.y) / dt) * 1000;
      }
    }
    const finger = Math.hypot(vx, vy);
    if (finger > FINGER_MAX) {
      vx *= FINGER_MAX / finger;
      vy *= FINGER_MAX / finger;
    }

    if (!taken.moved) {
      relaxIcon();
      if (tapSends && !cancelled) fire();
    } else {
      const loaded = armed && !cancelled;
      /* Loaded, the band adds its release speed along the pull; unloaded, a drag that was
         never armed is cancelled rather than sent, and the pad still gets a push — a
         control that ignores a gesture answers nothing. */
      const launch = loaded ? launchSpeed * Math.min(p, POWER_CAP) : CANCEL * launchSpeed * Math.min(p, 1);
      let v0x = vx - ux * launch;
      let v0y = vy - uy * launch;
      const hand = Math.hypot(v0x, v0y);
      if (hand > HAND_MAX) {
        v0x *= HAND_MAX / hand;
        v0y *= HAND_MAX / hand;
      }
      if (!loaded) relaxIcon();
      if (loaded) {
        fire();
        if (reduce) {
          flashSent();
        } else {
          power = clamp((Math.min(p, POWER_CAP) - 1) / (POWER_CAP - 1), 0, 1);
          /* Held back until the pad is on its way back through the seat, so the burst
             leaves the well rather than the pad's displaced position. */
          dotPending = true;
          dotTimer = window.setTimeout(launchDot, 150);
        }
      }
      settle({ x: v0x, y: v0y });
    }

    armed = false;
    held = false;
    setAttribute("data-armed", false);
    setAttribute("data-held", false);
  }

  function fire() {
    if (disabled) return;
    if (onSend) onSend();
  }

  function flashSent() {
    setAttribute("data-sent", true);
    window.clearTimeout(sentTimer);
    sentTimer = window.setTimeout(() => setAttribute("data-sent", false), SENT_MS);
  }

  function onClick() {
    if (skipClick) {
      skipClick = false;
      return;
    }
    if (!disabled) fire();
  }

  function onKeyDown(event) {
    if (event.key === "Escape" && grip) release(grip.id, true);
  }

  pad.addEventListener("pointerdown", onPointerDown);
  pad.addEventListener("pointermove", onPointerMove);
  pad.addEventListener("pointerup", (event) => release(event.pointerId, false));
  pad.addEventListener("pointercancel", (event) => release(event.pointerId, true));
  pad.addEventListener("lostpointercapture", (event) => release(event.pointerId, true));
  pad.addEventListener("click", onClick);
  pad.addEventListener("keydown", onKeyDown);

  /* Watched rather than sampled once, like every other port here: a reader who turns the
     setting on while the pad is out of its seat gets the pad put back, not a spring. */
  const stopWatching = watchReducedMotion((matches) => {
    reduce = matches;
    if (matches) {
      px.jump(0);
      py.jump(0);
      tension.style.opacity = "0";
    }
  });

  applyConfig();

  return {
    /* `set` merges. Turning the pad off and on again is one attribute, and re-mounting a
       control to change a colour would lose the swell under the pointer. */
    set(next = {}) {
      config = { ...config, ...next };
      if (next.size !== undefined) size = number(next.size, size);
      if (next.strokeWidth !== undefined) strokeWidth = number(next.strokeWidth, strokeWidth);
      if (next.maxPull !== undefined) maxPull = number(next.maxPull, maxPull);
      if (next.axis !== undefined) axis = next.axis;
      if (next.launchSpeed !== undefined) launchSpeed = number(next.launchSpeed, launchSpeed);
      if (next.recoil !== undefined) recoil = number(next.recoil, recoil);
      if (next.flight !== undefined) flight = number(next.flight, flight);
      if (next.spread !== undefined) spread = number(next.spread, spread);
      if (next.particles !== undefined) {
        const wanted = Math.max(0, Math.round(number(next.particles, dotCount)));
        while (dots.length > wanted) dots.pop().remove();
        while (dots.length < wanted) {
          const dot = document.createElement("span");
          dot.className = "sling-button__dot";
          dot.setAttribute("aria-hidden", "true");
          dots.push(dot);
          root.insertBefore(dot, move);
        }
      }
      if (next.tapSends !== undefined) tapSends = Boolean(next.tapSends);
      if (next.disabled !== undefined) disabled = Boolean(next.disabled);
      if (next.onSend !== undefined) onSend = next.onSend;
      if (next.ariaLabel !== undefined) config.ariaLabel = next.ariaLabel;
      applyConfig();
    },
    destroy() {
      alive = false;
      px.stop();
      py.stop();
      window.clearTimeout(dotTimer);
      window.clearTimeout(sentTimer);
      stopWatching();
      pad.removeEventListener("pointerdown", onPointerDown);
      pad.removeEventListener("pointermove", onPointerMove);
      pad.removeEventListener("click", onClick);
      pad.removeEventListener("keydown", onKeyDown);
      root.remove();
    },
  };
}
