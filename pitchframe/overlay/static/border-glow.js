/* Pitchframe overlay — BorderGlow, the light on a panel's boundary.
 *
 * A panel whose edge lights up where the pointer is near it, in a cone that follows the
 * pointer round the boundary. The overlay's panels are the page: a match bar, a feed, the
 * check, a ledger, two rail panels, the ratings. This is what tells a reader, without a
 * cursor change anywhere, which of those surfaces the pointer is on and which edge of it
 * they are closest to.
 *
 * A port of the React Bits component of the same name (https://reactbits.dev), with the
 * React half replaced by a module lifecycle: `createBorderGlow(element, options)` returns
 * `{ set, destroy }`, the same shape `createSlats()` and `createInfiniteMenu()` return. The
 * React half was a ref, an effect that ran the intro sweep, and a pointer handler; the
 * geometry, the glow-variable ladder and the mesh-gradient masks are the component's own
 * and are transcribed here with their values intact.
 *
 * Ported from React Bits, Copyright (c) 2026 David Haz.
 * MIT + Commons Clause License Condition v1.0. The full notice ships beside this file as
 * vendor/react-bits-LICENSE.md, and the Commons Clause covers "a ported version" as well
 * as the components themselves: this file is part of this application and is not
 * redistributed on its own.
 *
 * Three things are different here, and each is because this page's panels already exist:
 *
 *   It adorns an element rather than wrapping one. Upstream renders a card and renders the
 *   caller's children inside it. The overlay's panels are markup with headings, tables,
 *   lists and a `<dialog>` in them, and a port that wrapped one would be rebuilding the
 *   page to draw a border on it — moving every node app.js holds a reference to. This one
 *   takes the element, adds the component's class, its two pseudo-element layers and one
 *   `<span class="edge-light">`, and leaves everything else — the element's display,
 *   padding, border, radius and background — exactly as the stylesheet wrote it. The layers
 *   are absolutely positioned, so the span is not a flex item in any of these panels.
 *
 *   The colours come from the stylesheet. Upstream takes the card's background, the halo's
 *   HSL and three hex colours as props. Here each is read from a token first (`--card-bg`,
 *   `--glow-hsl`, `--glow-one`…`--glow-three`), so that a colour is decided in one place,
 *   the way slats.js takes the sea's palette — and a caller that passes a prop still wins.
 *   The radius is read off the element rather than taken as a prop, for the same reason:
 *   upstream's 28px is a card's corner, and these panels are the page's 4px.
 *
 *   Keyboard focus lights the same edge, aimed at whatever took focus. A pointer-only
 *   decoration is a decoration half the readers cannot see, and there are controls inside
 *   every panel here. On `focusin` the cone is aimed at the focused control's own box and
 *   the proximity is taken to the component's full, on `focusout` it is handed back to the
 *   pointer. The angle is measured from the focused element rather than chosen, which is
 *   the same rule the pointer follows.
 *
 * The intro sweep is the component's `animated` prop and this page passes nothing: seven
 * panels sweeping at once on load is decoration the page does not want. It is kept because
 * it is the component's, and it is cancelled the moment reduced motion is turned on —
 * like the sea and the sphere, this port reads that setting through the shared runtime
 * rather than sampling it once.
 *
 * And the light is one light. Upstream's component decides for itself that the pointer is
 * near it, which on a page of one card is the whole question and on a page of eight panels
 * is eight hopes: every panel the pointer happened to be inside would light at full
 * strength, the boundary it lit would be the one it is nearest *that* pointer's bearing to
 * its own centre, and the gaps between them would be unlit seams. Here every instance
 * registers with one document-level listener and is measured against the same pointer, in
 * the panel's own frame: the bearing from its centre aims the cone at whatever the pointer
 * is nearest on *that* panel's boundary, and how near the pointer is to the panel decides
 * whether it is lit at all — full while the pointer is inside it, fading with the distance
 * from the pointer to its nearest edge, and out of the picture at :data:`REACH_PX`. The
 * result reads as a single lamp travelling across the page: the boundary nearest the
 * pointer is the brightest of them, the panels around it are lit less, and a panel behind
 * the reader's back is dark without anything having to say so.
 *
 * Eight cones, then, but one source: what each panel draws is its own arc of the same
 * light, and the arc it draws is the one facing the pointer.
 *
 * No network, no images, no build step, and the only import is the shared motion runtime.
 */

import { prefersReducedMotion, watchReducedMotion } from "./motion.js";

/* The component's own values, as the fallbacks for a page that has not set a token. */
const FALLBACK_GLOW_HSL = "262 83% 70%";
const FALLBACK_COLORS = ["#3B2A86", "#7C5CFF", "#B9C8FF"];
const FALLBACK_CARD_BG = "#0E141C";

/*
 * The one light.
 *
 * The pointer is a single source, so the page has a single listener for it and every
 * mounted panel is measured against it. Three things about how it works are deliberate:
 *
 *   The reach is measured from the panel's *edge*, not its centre. A panel the pointer is
 *   inside is at full strength wherever inside it the pointer is, and two panels a pixel
 *   apart light together at their shared boundary. Measuring from the centre would instead
 *   make a panel dim as the reader moved towards it, which is the opposite of a lamp.
 *
 *   The measurement waits for a frame. A pointer reports dozens of moves a second and each
 *   one would otherwise read a rectangle for every panel on the page, forcing a layout each
 *   time; one batch per frame is the same picture at a fraction of the cost. A pointer move
 *   implies a window that is being drawn, so a frame is there to wait for — unlike the
 *   seek, which is allowed no such assumption and says so.
 *
 *   Focus outranks the pointer for the panel it is in. A reader working the keyboard is
 *   the light for that panel, and neither the pointer's absence nor its distance may leave
 *   it dark behind their back.
 */
const REACH_PX = 260;
const OFF_PAGE = -1e5;
const light = {
  panels: new Set(),
  x: OFF_PAGE,
  y: OFF_PAGE,
  frame: 0,
  listening: false,

  /** How lit a panel is: 1 with the pointer inside it, 0 beyond the reach. */
  falloff(rect, reach) {
    const dx = Math.max(rect.left - light.x, 0, light.x - rect.right);
    const dy = Math.max(rect.top - light.y, 0, light.y - rect.bottom);
    return Math.max(0, 1 - Math.hypot(dx, dy) / reach);
  },

  measure() {
    light.frame = 0;
    for (const instance of light.panels) instance.measure();
  },

  /* Coalesced to one batch of layout reads per frame. */
  wake() {
    if (light.frame) return;
    light.frame = requestAnimationFrame(() => light.measure());
  },

  onPointerMove(event) {
    light.x = event.clientX;
    light.y = event.clientY;
    light.wake();
  },

  /* The window is not a panel, so a pointer that has left it is a light that has left the
     page: the panels go out rather than keeping the last reading they were given. Named
     rather than inline, because a listener cannot be taken off by an anonymous function. */
  onPointerLeave(event) {
    /* Into a child of the root, rather than out of the window. */
    if (event.relatedTarget) return;
    light.x = OFF_PAGE;
    light.y = OFF_PAGE;
    light.wake();
  },

  add(instance) {
    light.panels.add(instance);
    if (light.listening) return;
    /* `pointerover` as well as `pointermove`, because a page that loaded under a pointer
       which has not moved since would otherwise wait for a jiggle to light anything: the
       browser restates the hover as the pointer crosses the elements it is already over. */
    document.addEventListener("pointermove", light.onPointerMove, { passive: true });
    document.addEventListener("pointerover", light.onPointerMove, { passive: true });
    document.addEventListener("pointerleave", light.onPointerLeave);
    /* The page scrolls under the pointer without the pointer moving at all, and a layout
       that changed under it does the same: both are the light's business, and neither
       sends a pointermove. */
    document.addEventListener("scroll", light.wake, { passive: true, capture: true });
    window.addEventListener("resize", light.wake, { passive: true });
    light.listening = true;
  },

  remove(instance) {
    light.panels.delete(instance);
    if (light.panels.size || !light.listening) return;
    document.removeEventListener("pointermove", light.onPointerMove);
    document.removeEventListener("pointerover", light.onPointerMove);
    document.removeEventListener("pointerleave", light.onPointerLeave);
    document.removeEventListener("scroll", light.wake, { capture: true });
    window.removeEventListener("resize", light.wake);
    light.listening = false;
  }
};

const GRADIENT_POSITIONS = ["80% 55%", "69% 34%", "8% 6%", "41% 38%", "86% 85%", "82% 18%", "51% 4%"];
const GRADIENT_KEYS = [
  "--gradient-one",
  "--gradient-two",
  "--gradient-three",
  "--gradient-four",
  "--gradient-five",
  "--gradient-six",
  "--gradient-seven"
];
const COLOR_MAP = [0, 1, 2, 0, 1, 2, 1];

/** The halo's HSL triple, as the component writes it: `"<h> <s>% <l>%"`. */
function parseHSL(hslText) {
  const match = String(hslText).match(/([\d.]+)\s*([\d.]+)%?\s*([\d.]+)%?/);
  if (!match) return { h: 262, s: 83, l: 70 };
  return { h: parseFloat(match[1]), s: parseFloat(match[2]), l: parseFloat(match[3]) };
}

/** The ladder of glow colours, one per shadow in the stylesheet's ring. */
function buildGlowVars(glowColor, intensity) {
  const { h, s, l } = parseHSL(glowColor);
  const base = `${h}deg ${s}% ${l}%`;
  const opacities = [100, 60, 50, 40, 30, 20, 10];
  const keys = ["", "-60", "-50", "-40", "-30", "-20", "-10"];
  const vars = {};
  for (let i = 0; i < opacities.length; i++) {
    vars[`--glow-color${keys[i]}`] = `hsl(${base} / ${Math.min(opacities[i] * intensity, 100)}%)`;
  }
  return vars;
}

/** The mesh gradient's seven stops, from three colours. */
function buildGradientVars(colors) {
  const vars = {};
  for (let i = 0; i < 7; i++) {
    const color = colors[Math.min(COLOR_MAP[i], colors.length - 1)];
    vars[GRADIENT_KEYS[i]] = `radial-gradient(at ${GRADIENT_POSITIONS[i]}, ${color} 0px, transparent 50%)`;
  }
  vars["--gradient-base"] = `linear-gradient(${colors[0]} 0 100%)`;
  return vars;
}

/**
 * A colour as `{ r, g, b, a }`, or null for anything else.
 *
 * The component's own helper reads hex only, because its background arrives as a prop. This
 * port reads the background *off the element* — `getComputedStyle` answers in `rgb()` — so
 * both forms are read here, and the luminance is then the component's formula unchanged.
 */
function parseColor(text) {
  const value = String(text).trim();
  const hex = value.match(/^#([\da-f]{3}|[\da-f]{6})$/i);
  if (hex) {
    const digits = hex[1].length === 3 ? hex[1].split("").map((char) => char + char).join("") : hex[1];
    return {
      r: parseInt(digits.slice(0, 2), 16),
      g: parseInt(digits.slice(2, 4), 16),
      b: parseInt(digits.slice(4, 6), 16),
      a: 1
    };
  }
  const rgb = value.match(/^rgba?\(([^)]+)\)$/i);
  if (!rgb) return null;
  const parts = rgb[1].split(/[\s,/]+/).filter(Boolean).map(Number);
  if (parts.length < 3 || parts.some(Number.isNaN)) return null;
  return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
}

/** Whether a background is light enough that the glow has to be blended the other way. */
function isLightColor(color) {
  if (!color) return false;
  return color.r * 0.2126 + color.g * 0.7152 + color.b * 0.0722 > 180;
}

function easeOutCubic(x) { return 1 - Math.pow(1 - x, 3); }
function easeInCubic(x) { return x * x * x; }

/**
 * The component's own tween, returning a cancel so a sweep can be stopped mid-flight.
 *
 * Upstream returns nothing, which is fine for a mount-time effect that always runs to the
 * end. Here the sweep has to stop the moment reduced motion is turned on, so the handle is
 * returned and the frame is dropped when it is cancelled.
 */
function animateValue({ start = 0, end = 100, duration = 1000, delay = 0,
                        ease = easeOutCubic, onUpdate, onEnd }) {
  let raf = 0;
  let timer = window.setTimeout(() => {
    const t0 = performance.now();
    const tick = () => {
      const t = Math.min((performance.now() - t0) / duration, 1);
      onUpdate(start + (end - start) * ease(t));
      if (t < 1) raf = requestAnimationFrame(tick);
      else if (onEnd) onEnd();
    };
    raf = requestAnimationFrame(tick);
  }, delay);
  return () => {
    window.clearTimeout(timer);
    cancelAnimationFrame(raf);
  };
}

export function createBorderGlow(element, options = {}) {
  if (!element) return null;

  const token = (name, fallback) => {
    const value = getComputedStyle(element).getPropertyValue(name).trim();
    return value || fallback;
  };

  const settings = {};
  let detachMotion = null;
  let cancelSweep = null;
  /* Whether a control inside this panel holds focus: the one thing that outranks the
     pointer for this panel's share of the light. */
  let hasFocus = false;
  /* Sampled once and then watched, the way every other port here reads it: the watcher
     alone only ever fires on a change, so a reader who loaded with the setting already on
     would get the sweep. */
  let reduced = prefersReducedMotion();

  /* -------------------------------------------------------------- the layers --- */

  const edge = document.createElement("span");
  edge.className = "edge-light";
  /* Decoration over the whole panel, so it is hidden from the accessibility tree and can
     never take a pointer that was aimed at the panel underneath it. */
  edge.setAttribute("aria-hidden", "true");
  element.prepend(edge);
  element.classList.add("border-glow");

  /* ------------------------------------------------------------------ geometry -- */

  const centreOf = (rect) => [rect.width / 2, rect.height / 2];

  /* How close to the edge the pointer is, as 0 at the centre and 1 at the boundary. The
     component's own measure: the smaller of the two axis ratios, inverted. */
  function edgeProximity(rect, x, y) {
    const [cx, cy] = centreOf(rect);
    const dx = x - cx;
    const dy = y - cy;
    let kx = Infinity;
    let ky = Infinity;
    if (dx !== 0) kx = cx / Math.abs(dx);
    if (dy !== 0) ky = cy / Math.abs(dy);
    return Math.min(Math.max(1 / Math.min(kx, ky), 0), 1);
  }

  /* The pointer's bearing from the centre, in the conic gradient's own frame: 0deg points
     up, so the atan2 result is turned a quarter turn and wrapped. */
  function cursorAngle(rect, x, y) {
    const [cx, cy] = centreOf(rect);
    const dx = x - cx;
    const dy = y - cy;
    if (dx === 0 && dy === 0) return 0;
    let degrees = Math.atan2(dy, dx) * (180 / Math.PI) + 90;
    if (degrees < 0) degrees += 360;
    return degrees;
  }

  function lightEdge(proximity, angle) {
    element.style.setProperty("--edge-proximity", proximity.toFixed(3));
    element.style.setProperty("--cursor-angle", `${angle.toFixed(3)}deg`);
  }

  /*
   * Everything this panel draws from the one light: which boundary it aims at, how near
   * the pointer is to that boundary, and how much of the light reaches the panel at all.
   *
   * The rectangle is read here rather than taken from the tracker because it is this
   * panel's own, and read fresh rather than cached because a cached rectangle goes wrong
   * the moment the feed grows a row — which, on this page, it does every few seconds. It
   * is one read per panel per frame, batched by the tracker.
   *
   * A panel the pointer is not inside is at the component's full proximity, and upstream's
   * measure says so by itself: the ratio it takes is the pointer's distance from the
   * centre, which past the boundary is larger than the half-width it is divided by. That
   * is the right value here as well as the arithmetical one — a panel being lit from
   * outside is lit along an edge the pointer is somewhere beyond, not fading towards a
   * centre the pointer has nothing to do with — and it leaves the falloff as the only
   * thing that says how far away the pointer is.
   */
  function measure() {
    /* The sweep owns these two variables while it runs, and two writers to one property is
       a tween fighting the pointer at 60Hz. It ends by writing the resting state itself. */
    if (element.classList.contains("sweep-active")) return;
    /* Focus is this panel's light: the pointer's position, and its absence, are not this
       panel's business until the reader has gone back to it. */
    if (hasFocus) {
      element.style.setProperty("--glow-falloff", "1");
      return;
    }
    const rect = element.getBoundingClientRect();
    const x = light.x - rect.left;
    const y = light.y - rect.top;
    lightEdge(edgeProximity(rect, x, y) * 100, cursorAngle(rect, x, y));
    element.style.setProperty(
      "--glow-falloff",
      light.falloff(rect, settings.reach).toFixed(3)
    );
  }

  /* ------------------------------------------------------------------ keyboard -- */

  /* The edge is lit for a reader who has not touched a pointer at all, aimed at the control
     that took focus — measured from that control's own box, the same way the pointer's
     bearing is measured from it. */
  function onFocusIn(event) {
    const focused = event.target;
    if (!(focused instanceof HTMLElement) || focused === element) return;
    const rect = element.getBoundingClientRect();
    const box = focused.getBoundingClientRect();
    const angle = cursorAngle(rect, box.left - rect.left + box.width / 2, box.top - rect.top + box.height / 2);
    element.dataset.lit = "true";
    hasFocus = true;
    lightEdge(100, angle);
    measure();
  }

  function onFocusOut(event) {
    /* Moving between two controls in the same panel is not a blur: the cone follows the
       focus that arrived rather than flickering off and on. */
    if (element.contains(event.relatedTarget)) return;
    delete element.dataset.lit;
    hasFocus = false;
    /* The light goes back to the pointer, from wherever it has got to since — including
       off the page, which measurement reads as nothing reaching this panel. */
    measure();
  }

  /* ------------------------------------------------------------------- painting -- */

  function radiusOf() {
    if (typeof settings.borderRadius === "number") return `${settings.borderRadius}px`;
    if (typeof settings.borderRadius === "string") return settings.borderRadius;
    /* The element's own corner, so a panel keeps its shape: upstream's 28px is a card's. */
    return getComputedStyle(element).borderRadius || "4px";
  }

  /**
   * The colour the panel is masked with, which is the colour of its own interior.
   *
   * The gradient's padding box is filled with this so that only the 1px boundary shows the
   * mesh; a panel whose interior is not one flat colour (the mode browser's stage is a
   * radial gradient) is given the colour its gradient ends at by the caller, because an
   * empty `--card-bg` would let the mesh paint a wedge across the panel's inside.
   */
  function backgroundOf() {
    if (settings.backgroundColor) return settings.backgroundColor;
    const computed = parseColor(getComputedStyle(element).backgroundColor);
    if (computed && computed.a === 1) {
      return `rgb(${computed.r} ${computed.g} ${computed.b})`;
    }
    return token("--card-bg", FALLBACK_CARD_BG);
  }

  function paint() {
    const background = backgroundOf();
    const pale = isLightColor(parseColor(background)) || isLightColor(parseColor(settings.backgroundColor || ""));
    element.classList.toggle("border-glow--light", pale);
    const vars = {
      "--edge-sensitivity": String(settings.edgeSensitivity),
      "--border-radius": radiusOf(),
      "--glow-padding": `${settings.glowRadius}px`,
      "--cone-spread": String(settings.coneSpread),
      "--fill-opacity": String(settings.fillOpacity),
      "--card-bg": background,
      ...buildGlowVars(settings.glowColor, settings.glowIntensity),
      ...buildGradientVars(settings.colors)
    };
    for (const [name, value] of Object.entries(vars)) {
      element.style.setProperty(name, value);
    }
  }

  /* ------------------------------------------------------------------- settings -- */

  function apply(next = {}) {
    const merged = {
      edgeSensitivity: 30,
      /* How far the one light reaches, in pixels: the distance from the pointer at which a
         panel's boundary stops being lit at all. 260 is about two of this page's panel gaps
         — near enough that the light crosses a gutter, far enough that the page does not
         glow from end to end. */
      reach: REACH_PX,
      glowColor: token("--glow-hsl", FALLBACK_GLOW_HSL),
      backgroundColor: "",
      borderRadius: null,
      glowRadius: Number(token("--glow-padding", "40").replace("px", "")) || 40,
      glowIntensity: 1,
      coneSpread: 25,
      fillOpacity: 0.5,
      colors: [
        token("--glow-one", FALLBACK_COLORS[0]),
        token("--glow-two", FALLBACK_COLORS[1]),
        token("--glow-three", FALLBACK_COLORS[2])
      ]
    };
    for (const key of Object.keys(merged)) {
      if (next[key] !== undefined) merged[key] = next[key];
    }
    Object.assign(settings, merged);
    paint();
    if (next.animated) sweep();
  }

  /* ------------------------------------------------------------------- the sweep -- */

  function sweep() {
    stopSweep();
    if (reduced) return;
    const angleStart = 110;
    const angleEnd = 465;
    element.classList.add("sweep-active");
    element.style.setProperty("--cursor-angle", `${angleStart}deg`);
    const at = (degrees) => element.style.setProperty("--cursor-angle", `${degrees}deg`);
    const cancels = [
      animateValue({ duration: 500, onUpdate: (v) => element.style.setProperty("--edge-proximity", v.toFixed(3)) }),
      animateValue({ ease: easeInCubic, duration: 1500, end: 50,
        onUpdate: (v) => at((angleEnd - angleStart) * (v / 100) + angleStart) }),
      animateValue({ ease: easeOutCubic, delay: 1500, duration: 2250, start: 50, end: 100,
        onUpdate: (v) => at((angleEnd - angleStart) * (v / 100) + angleStart) }),
      animateValue({ ease: easeInCubic, delay: 2500, duration: 1500, start: 100, end: 0,
        onUpdate: (v) => element.style.setProperty("--edge-proximity", v.toFixed(3)),
        /* The sweep was the last writer to both variables, so it hands the panel back
           rather than leaving the pointer's light reading as the tween left it. */
        onEnd: () => {
          element.classList.remove("sweep-active");
          measure();
        } })
    ];
    cancelSweep = () => cancels.forEach((cancel) => cancel());
  }

  function stopSweep() {
    if (cancelSweep) cancelSweep();
    cancelSweep = null;
    element.classList.remove("sweep-active");
  }

  /* --------------------------------------------------------------------- mount --- */

  /* No pointer listener of its own: the one light has the page's, and a panel that read
     the pointer itself would be the eight lamps this port exists to replace. The panel's
     own listeners are the two keyboard ones, which are about focus rather than the
     pointer, and the tracker's, which arrive through `measure`. */
  const instance = {
    element,
    set: (next = {}) => apply(next),
    measure,
    sweep,
    destroy() {
      stopSweep();
      light.remove(instance);
      if (detachMotion) detachMotion();
      element.removeEventListener("focusin", onFocusIn);
      element.removeEventListener("focusout", onFocusOut);
      element.classList.remove("border-glow", "border-glow--light", "sweep-active");
      element.style.removeProperty("--glow-falloff");
      element.style.removeProperty("--edge-proximity");
      element.style.removeProperty("--cursor-angle");
      edge.remove();
    }
  };

  apply(options);
  element.addEventListener("focusin", onFocusIn);
  element.addEventListener("focusout", onFocusOut);
  /* Registered with the one light, and measured once at mount: the pointer may already be
     over this panel by the time it exists (a reload under a resting cursor), and a panel
     that waited for the next move would be dark until the reader jiggled the mouse. */
  light.add(instance);
  measure();

  /* A setting turned on while a panel is mid-sweep has to stop it then, not at the next
     load: the sweep is the one thing here that moves on its own. */
  detachMotion = watchReducedMotion((matches) => {
    reduced = matches;
    if (matches) {
      stopSweep();
      /* The two variables the sweep was writing go back to the light — unless focus is
         what is lighting this panel, in which case the focus path already has them. */
      if (!element.dataset.lit) measure();
    }
  });

  return instance;
}
