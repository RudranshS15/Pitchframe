/* TextPressure — a word whose letters swell under the pointer.
 *
 * A port of the React Bits component of the same name (https://reactbits.dev), with the
 * React half replaced by a module lifecycle: `createTextPressure(host, options)` returns
 * `{ element, setText, set, wake, destroy }`. Everything under those methods is the
 * component's own logic — the pressure field around the pointer, the soft follow of the
 * cursor, the idle wave and pulse, the ripple a click sends out through the letters, the
 * per-letter variation settings written on every frame, and the settling that stops the
 * loop once the word has arrived.
 *
 * Ported from React Bits, Copyright (c) 2026 David Haz.
 * MIT + Commons Clause License Condition v1.0. The full notice ships beside this file as
 * vendor/react-bits-LICENSE.md, and the Commons Clause covers "a ported version" as well as
 * the components themselves: this file is part of this application and is not redistributed
 * on its own.
 *
 * The component's whole effect is a variable font being asked for a weight and a width it
 * can answer. That is not universal: the faces this page ships with are requested by
 * weight, a fallback face has no axes at all, and a browser that has not yet loaded the
 * face has nothing to vary. So the port measures the face before it animates it — two
 * probe renders at opposite ends of each axis, compared — and writes only the axes that
 * measurably moved the text. A word set in a face with no axes is left exactly as it was
 * written, with `data-pressure="flat"` saying so, rather than being animated against a
 * font that ignores it. That is the one addition the page needs; the rest is upstream's.
 *
 * Three smaller differences, each because this page is not a React app:
 *
 *   The resting state is flat. Upstream seeds the pointer at the centre of the container,
 *   so a word with no pointer over it still has its middle swollen. Here nothing is
 *   swollen until the pointer has actually been seen, and a pointer that leaves the
 *   document returns the word to rest — a wordmark is not a control, and type that starts
 *   mid-gesture reads as type that failed to finish loading.
 *
 *   The accessible text is the word, once. The letters are spans so that each can be
 *   varied on its own, which makes them useless to a screen reader, so the visual copy is
 *   `aria-hidden` and set beside one unbroken copy in the host's own reading order. The
 *   visible copy is unselectable, so a selection copies the word rather than the word
 *   twice.
 *
 *   `prefers-reduced-motion` comes from ./motion.js rather than from the component's own
 *   one-off `matchMedia`, so the idle wave honours the setting through the same watched
 *   runtime every other port uses, and it is read rather than sampled once. The pressure
 *   the pointer itself asks for is kept: that is direct manipulation, not motion the page
 *   started on its own.
 *
 * The idle wave is off by default here rather than being the component's default, because
 * this page is measured with nothing moving — see tools/browser_check.mjs. A caller that
 * wants it asks for `idle: "wave"`.
 *
 * No network, no images, no build step, and the only import is the shared motion runtime.
 */

import { prefersReducedMotion, watchReducedMotion } from "./motion.js";

const RIPPLE_LIFE = 1.3;
const MAX_RIPPLES = 6;
const WAVE_PERIOD = 3.2;
const PULSE_PERIOD = 2.8;
const PULSE_DEPTH = 0.42;

/*: Where the pointer has to be before the word can swell, in CSS pixels. A word without a
    bounding box yet (a hidden panel, a font still loading) has nothing to measure. */
const MIN_REACH = 1;

const clamp01 = (value) => Math.min(1, Math.max(0, value));

const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);

const DEFAULTS = {
  text: "",
  /* Layout. `fit` is upstream's rule — the word fills its container's width — and it
     needs a container whose width the layout decides rather than the word itself: a block
     element, or the caller's own `fontSize`. `fit: false` keeps the page's type scale and
     varies only the axes, which is what a wordmark inside a masthead wants. */
  fit: true,
  fontSize: null,
  minFontSize: 24,
  maxFontSize: Infinity,
  scale: false,
  flex: true,
  /* Which axes to ask for, and the range each is mapped into. Upstream's ranges are the
     font's full design space; a condensed face answers a narrower one, and a caller that
     knows its face says so rather than having its word pinned at the axis ends. */
  width: true,
  weight: true,
  italic: true,
  alpha: false,
  weightRange: [100, 1000],
  widthRange: [5, 205],
  /* The gesture. */
  radius: 0.5,
  smoothing: 0.5,
  idle: "none",
  idleSpeed: 1,
  clickRipple: true,
  /* Colour. Empty `pressureColor` keeps one colour throughout. */
  pressureColor: ""
};

const getIdlePressure = (mode, position, clock, spread) => {
  if (mode === "wave") {
    const crest = 0.5 + 0.5 * Math.cos(Math.PI * 2 * (position / spread - clock / WAVE_PERIOD));
    return crest * crest;
  }
  if (mode === "pulse") {
    const lag = Math.abs(position - 0.5) * 0.4;
    const breath = 0.5 - 0.5 * Math.cos(Math.PI * 2 * (clock / PULSE_PERIOD - lag));
    return PULSE_DEPTH * breath * breath * (3 - 2 * breath);
  }
  return 0;
};

const getRipplePressure = (ripples, point, now, span) => {
  const band = Math.max(span * 0.14, 32);
  let total = 0;
  for (const ripple of ripples) {
    const age = (now - ripple.start) / 1000;
    if (age <= 0 || age >= RIPPLE_LIFE) continue;
    const offset = (dist(ripple, point) - age * span * 1.25) / band;
    if (offset <= -1 || offset >= 1) continue;
    const fade = 1 - age / RIPPLE_LIFE;
    const swell = (1 - offset * offset) ** 2 * fade * fade * Math.min(1, age / 0.06);
    total = 1 - (1 - total) * (1 - swell);
  }
  return total;
};

/**
 * Which variation axes the face on `host` actually answers.
 *
 * One probe span, rendered twice at opposite ends of each axis and compared. A static face
 * — a fallback, a font still loading, a browser that has synthesis switched off — returns
 * the same width twice, and that axis is not offered. Measured rather than assumed because
 * assuming is what makes this component quietly do nothing: `font-variation-settings` on a
 * face with no matching axis is accepted and ignored, with no error to notice.
 *
 * The probe is deliberately large, so a difference of one design unit is not lost to
 * rounding, and it is removed in a `finally` so a throw cannot leave it in the document.
 */
function measureAxes(host) {
  const style = getComputedStyle(host);
  const probe = document.createElement("span");
  probe.textContent = "Hamburgefonstiv";
  probe.setAttribute("aria-hidden", "true");
  probe.style.cssText = [
    "position:absolute",
    "left:-9999px",
    "top:0",
    "visibility:hidden",
    "white-space:nowrap",
    "pointer-events:none",
    `font-family:${style.fontFamily}`,
    `font-size:100px`,
    `font-weight:${style.fontWeight || 400}`,
    "font-synthesis:none"
  ].join(";");
  document.body.appendChild(probe);
  try {
    const widthAt = (settings) => {
      probe.style.fontVariationSettings = settings;
      return probe.getBoundingClientRect().width;
    };
    const axes = [];
    if (Math.abs(widthAt("'wght' 100") - widthAt("'wght' 900")) > 0.5) axes.push("wght");
    if (Math.abs(widthAt("'wght' 400, 'wdth' 25") - widthAt("'wght' 400, 'wdth' 151")) > 0.5) {
      axes.push("wdth");
    }
    /* Italic is measured by advance width like the others, which is the only thing a
       probe can see. A face whose italic keeps every advance identical is not asked for
       one — the word stays upright rather than being slanted by the browser's synthesis,
       which is a shape the page never chose. */
    if (Math.abs(widthAt("'ital' 0") - widthAt("'ital' 1")) > 0.5) axes.push("ital");
    return axes;
  } finally {
    probe.remove();
  }
}

export function createTextPressure(host, options = {}) {
  if (!host) throw new Error("createTextPressure needs a host element");

  const named = { ...DEFAULTS, ...options };
  if (!named.text) named.text = (host.textContent || "").trim();

  const settings = {};
  const apply = (next = {}) => {
    Object.assign(named, next);
    settings.text = String(named.text ?? "");
    settings.fit = named.fit !== false;
    settings.minFontSize = Math.max(1, Number(named.minFontSize) || 24);
    settings.maxFontSize = Number(named.maxFontSize) || Infinity;
    settings.scale = !!named.scale;
    settings.flex = named.flex !== false;
    settings.width = named.width !== false;
    settings.weight = named.weight !== false;
    settings.italic = named.italic !== false;
    settings.alpha = !!named.alpha;
    settings.weightRange = Array.isArray(named.weightRange) ? named.weightRange : [100, 1000];
    settings.widthRange = Array.isArray(named.widthRange) ? named.widthRange : [5, 205];
    settings.radius = Math.max(0, Number(named.radius) || 0.5);
    settings.smoothing = clamp01(Number(named.smoothing) || 0);
    settings.idle = named.idle === "wave" || named.idle === "pulse" ? named.idle : "none";
    settings.idleSpeed = Math.max(0, Number(named.idleSpeed) || 1);
    settings.clickRipple = named.clickRipple !== false;
    settings.pressureColor = named.pressureColor ? String(named.pressureColor) : "";
  };
  apply();

  /* ------------------------------------------------------------------- the DOM --- */

  const root = document.createElement("span");
  root.className = "text-pressure";
  root.setAttribute("aria-hidden", "true");

  /* The one copy a reader hears, and the one a selection takes: the visual letters are
     unselectable and hidden from the accessibility tree. */
  const say = document.createElement("span");
  say.className = "text-pressure__say";
  say.textContent = settings.text;

  host.classList.add("text-pressure-host");
  host.replaceChildren(say, root);

  const letters = [];
  let widths = [];

  function render() {
    root.replaceChildren();
    letters.length = 0;
    widths = [];
    for (const char of Array.from(settings.text)) {
      const span = document.createElement("span");
      span.className = "text-pressure__letter";
      span.dataset.char = char;
      /* `pre` so a space between two words keeps the width the page gave it, rather than
         collapsing to nothing inside a flex row. */
      span.style.whiteSpace = "pre";
      span.textContent = char;
      root.appendChild(span);
      letters.push(span);
    }
    say.textContent = settings.text;
    applied.length = 0;
    size();
    wake();
  }

  /* ------------------------------------------------------------------ the sizing --- */

  /* The width `fit` fills. Measured once per layout rather than per frame: for an inline
     host the width is a consequence of the font size, so measuring it every frame is a
     loop with no fixed point — each frame would make the word bigger. One measurement per
     resize is a word that fits at the size the layout gave it. */
  let fitWidth = 0;

  function measureFit() {
    const box = host.getBoundingClientRect();
    const width = box.width || host.parentElement?.getBoundingClientRect().width || 0;
    if (width > MIN_REACH) fitWidth = width;
  }

  function size() {
    if (named.fontSize) {
      root.style.fontSize = `${Number(named.fontSize)}px`;
    } else if (settings.fit && fitWidth > MIN_REACH) {
      const chars = Math.max(1, Array.from(settings.text).length);
      const computed = fitWidth / (chars / 2);
      root.style.fontSize = `${Math.min(settings.maxFontSize, Math.max(settings.minFontSize, computed))}px`;
    } else {
      root.style.fontSize = "";
    }
    root.style.display = settings.fit && settings.flex ? "flex" : "inline-block";
    root.style.justifyContent = settings.fit && settings.flex ? "space-between" : "";
    root.style.whiteSpace = settings.fit ? "nowrap" : "";
    root.style.transform = "";
  }

  /* ------------------------------------------------------------------ the pointer --- */

  let reduce = prefersReducedMotion();
  let axes = [];
  let flat = false;
  let visible = true;
  let raf = 0;
  let clock = 0;
  let engage = 0;
  let last = 0;

  const pointer = { x: 0, y: 0, seen: false };
  const mouse = { x: 0, y: 0 };
  const ripples = [];
  const applied = [];

  function setFlat(next) {
    flat = next;
    host.dataset.pressure = next ? "flat" : "live";
    host.dataset.axes = axes.join(" ");
    if (next) {
      /* Give the word back to the page's own type: no settings, no stray opacity. */
      for (const span of letters) {
        span.style.fontVariationSettings = "";
        span.style.opacity = "";
        span.style.color = "";
      }
      applied.length = 0;
    } else {
      wake();
    }
  }

  function measure() {
    axes = measureAxes(host);
    setFlat(!axes.length);
  }

  /* ------------------------------------------------------------------ the loop --- */

  function tick(now) {
    raf = 0;
    const elapsed = last ? Math.min(now - last, 64) : 1000 / 60;
    last = now;

    const idleMode = settings.idle !== "none" && !reduce ? settings.idle : "none";
    const lag = 1000 * settings.smoothing * settings.smoothing;
    const follow = lag > 1 ? 1 - Math.exp(-elapsed / lag) : 1;
    mouse.x += (pointer.x - mouse.x) * follow;
    mouse.y += (pointer.y - mouse.y) * follow;

    let present = 0;
    if (idleMode !== "none" && pointer.seen) {
      const box = host.getBoundingClientRect();
      const inside =
        pointer.x >= box.left && pointer.x <= box.right && pointer.y >= box.top && pointer.y <= box.bottom;
      present = inside ? 1 : 0;
    }
    engage += (present - engage) * (1 - Math.exp(-elapsed / 320));
    if (Math.abs(present - engage) < 0.002) engage = present;
    const idling = idleMode !== "none" && engage < 1;
    if (idling) clock += (elapsed / 1000) * settings.idleSpeed;

    while (ripples.length && now - ripples[0].start > RIPPLE_LIFE * 1000) ripples.shift();

    const box = host.getBoundingClientRect();
    const reach = Math.max(box.width * settings.radius, MIN_REACH);
    const spread = Math.max(settings.radius * 2, 0.2);
    const centers = letters.map((span) => {
      const rect = span.getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    });

    let changed = false;
    for (let i = 0; i < letters.length; i += 1) {
      const center = centers[i];
      if (!center) continue;

      /* Nothing is swollen until the pointer has been seen, which is the resting state
         the page counts on: a word at rest is the word the page was designed with. */
      let pressure = pointer.seen ? clamp01(1 - dist(mouse, center) / reach) : 0;
      if (idling) {
        const position = (center.x - box.left) / Math.max(box.width, 1);
        const resting = getIdlePressure(idleMode, position, clock, spread);
        pressure = resting + (pressure - resting) * engage;
      }
      if (ripples.length) {
        const swell = getRipplePressure(ripples, center, now, box.width);
        pressure = 1 - (1 - pressure) * (1 - swell);
      }

      const [weightMin, weightMax] = settings.weightRange;
      const [widthMin, widthMax] = settings.widthRange;
      const parts = [];
      if (settings.weight && axes.includes("wght")) {
        parts.push(`'wght' ${Math.round(weightMin + (weightMax - weightMin) * pressure)}`);
      }
      if (settings.width && axes.includes("wdth")) {
        parts.push(`'wdth' ${Math.round(widthMin + (widthMax - widthMin) * pressure)}`);
      }
      if (settings.italic && axes.includes("ital")) parts.push(`'ital' ${pressure.toFixed(2)}`);
      const variations = parts.join(", ") || "normal";
      const alphaValue = settings.alpha ? pressure.toFixed(2) : "";
      const tint = Math.round((1 - (1 - pressure) * (1 - pressure)) * 100);
      const color = settings.pressureColor
        ? `color-mix(in oklab, ${settings.pressureColor} ${tint}%, currentColor)`
        : "";

      const previous = applied[i];
      if (
        !previous ||
        previous.variations !== variations ||
        previous.alphaValue !== alphaValue ||
        previous.color !== color
      ) {
        const span = letters[i];
        span.style.fontVariationSettings = variations;
        span.style.opacity = settings.alpha ? alphaValue : "";
        span.style.color = color;
        applied[i] = { variations, alphaValue, color };
        changed = true;
      }
    }

    const moving = pointer.seen && (Math.abs(pointer.x - mouse.x) > 0.1 || Math.abs(pointer.y - mouse.y) > 0.1);
    const animating = moving || idling || engage !== present || ripples.length > 0;
    if (animating || changed) {
      raf = requestAnimationFrame(tick);
    } else {
      last = 0;
    }
  }

  function wake() {
    if (raf || flat || !visible) return;
    last = 0;
    raf = requestAnimationFrame(tick);
  }

  /* -------------------------------------------------------------- the listeners --- */

  function track(point) {
    pointer.x = point.clientX;
    pointer.y = point.clientY;
    pointer.seen = true;
    wake();
  }

  function onPointerMove(event) {
    track(event);
  }

  function onPointerDown(event) {
    track(event);
    if (!settings.clickRipple) return;
    ripples.push({ x: event.clientX, y: event.clientY, start: performance.now() });
    if (ripples.length > MAX_RIPPLES) ripples.shift();
    wake();
  }

  function onLeave() {
    pointer.seen = false;
    wake();
  }

  const onFonts = () => {
    measureFit();
    measure();
    size();
    wake();
  };

  function onTouchStart(event) {
    const touch = event.touches[0];
    if (touch) track(touch);
  }

  window.addEventListener("pointermove", onPointerMove, { passive: true });
  host.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("touchstart", onTouchStart, { passive: true });
  document.documentElement.addEventListener("mouseleave", onLeave);
  window.addEventListener("scroll", wake, { passive: true, capture: true });
  window.addEventListener("resize", onFonts, { passive: true });
  document.fonts?.addEventListener?.("loadingdone", onFonts);

  const motionWatch = watchReducedMotion((matches) => {
    reduce = matches;
    wake();
  });

  const resizeObserver = new ResizeObserver(() => {
    measureFit();
    size();
    wake();
  });
  resizeObserver.observe(host);

  const showObserver = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    if (visible) wake();
  });
  showObserver.observe(host);

  /* ------------------------------------------------------------------- the start --- */

  measureFit();
  measure();
  render();
  /* Whatever the face does, the word is written and readable before the first frame: a
     module that threw here would leave the masthead without a name. */
  document.fonts?.ready?.then(onFonts).catch(() => {});

  return {
    element: root,
    /** Which axes the port found on the face: `[]` when the word cannot swell. */
    axes: () => [...axes],
    flat: () => flat,
    setText: (text) => {
      /* Through `apply`, so the resolved settings are what the letters are built from:
         writing the caller's word without resolving it would leave the previous word on
         screen and the new one in the options, which is the scoreboard disagreeing with
         the document it is describing. */
      apply({ text: String(text ?? "") });
      render();
      measureFit();
      measure();
    },
    set: (next = {}) => {
      const previous = settings.text;
      apply(next);
      if (settings.text !== previous || next.fit !== undefined) render();
      else {
        size();
        wake();
      }
    },
    wake,
    destroy: () => {
      cancelAnimationFrame(raf);
      raf = 0;
      motionWatch();
      resizeObserver.disconnect();
      showObserver.disconnect();
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("touchstart", onTouchStart);
      host.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("scroll", wake, { capture: true });
      window.removeEventListener("resize", onFonts);
      document.documentElement.removeEventListener("mouseleave", onLeave);
      document.fonts?.removeEventListener?.("loadingdone", onFonts);
      host.classList.remove("text-pressure-host");
      delete host.dataset.pressure;
      delete host.dataset.axes;
      host.replaceChildren(say);
      root.remove();
    }
  };
}
