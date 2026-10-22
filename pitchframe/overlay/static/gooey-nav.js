/* GooeyNav — the site's navigation, in a pill that melts between its pages.
 *
 * A port of the React Bits component of the same name (https://reactbits.dev), with the
 * React half replaced by a module lifecycle: `createGooeyNav(host, options)` returns
 * `{ element, set, active, wake, destroy }`. Everything under those methods is the
 * component's own logic — the pill as a signed distance field, the droplets a selection
 * breaks it into, the springs that carry each droplet to the new pill, the field traced
 * into a clip path so the label can be painted in the pill's colour, the hover highlight
 * that eases between items, and the one WebGL2 draw call the goo is made in.
 *
 * Ported from React Bits, Copyright (c) 2026 David Haz.
 * MIT + Commons Clause License Condition v1.0. The full notice ships beside this file as
 * vendor/react-bits-LICENSE.md, and the Commons Clause covers "a ported version" as well as
 * the components themselves: this file is part of this application and is not redistributed
 * on its own.
 *
 * The component is an enhancement here rather than a control this project invents: the
 * host already contains the links, with their real `href`s, written into the page's own
 * markup. The port adds the layer that animates them and leaves the links alone, so the
 * navigation works with the script absent, with WebGL2 missing, and with the pointer
 * nowhere near it. That is the same rule the sphere follows — a catalogue that cannot be
 * drawn must still be reachable — applied to the way between the two pages.
 *
 * Four deliberate differences from the component, each because this page is not a React
 * app:
 *
 *   Colours come from the stylesheet rather than from props. The active pill is the
 *   page's selected treatment — chalk fill, night text — which is what the dock's menus
 *   and badges use, so selecting a page and selecting a speed look like the same act. The
 *   hover highlight borrows the page's hairline, `--rule-hard`.
 *
 *   A selection is a navigation. Upstream reports the index and its caller decides; here
 *   the anchor is the truth, so the click is held only until the droplets have left the
 *   pill and then followed — the page changes on the burst, not after it. Holding the
 *   click for the whole animation would make choosing a page wait half a second to watch
 *   particles land, which is a control that feels broken rather than one that feels
 *   finished. A link opened in a new tab, or one with a modifier held, is never held: the
 *   browser's own behaviour is the reader's, not this control's.
 *
 *   `prefers-reduced-motion` is watched rather than sampled once, so turning the setting
 *   on mid-burst settles the pill where it was going instead of finishing the scatter.
 *
 *   A browser with no WebGL2 still gets the moving pill — positioned by the same field —
 *   rather than the component's static bar. The pill is the navigation's state; the goo
 *   is the flourish.
 *
 * No network, no images, no build step, and the only import is the shared motion runtime.
 */

import { prefersReducedMotion, watchReducedMotion } from "./motion.js";

const MAX_DROPS = 32;
const MAX_PILLS = 4;
const MARGIN = 72;

/*: How far into the burst the address is followed, as a share of `animationTime`. At the
    default 420ms this is about 76ms: one painted frame of the break, which is all a reader
    needs to see that the click landed. It used to be a third of a 520ms animation — about
    180ms — and that wait, stacked on the next document's own load, is what made choosing a
    page feel like the click had missed. It is a share rather than a millisecond constant so
    that a caller who slows the animation for a larger nav gets a proportionally longer look
    at the burst from the one knob it already has. */
const REVEAL_SHARE = 0.18;

const VERTEX = `#version 300 es
in vec2 aPosition;
void main() {
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
`;

const FRAGMENT = `#version 300 es
precision highp float;

uniform vec2 uSize;
uniform float uRatio;
uniform vec4 uPills[${MAX_PILLS}];
uniform vec4 uHover;
uniform vec4 uDrops[${MAX_DROPS}];
uniform vec2 uTails[${MAX_DROPS}];
uniform vec3 uTints[${MAX_DROPS}];
uniform int uCount;
uniform float uSmooth;
uniform vec3 uPill;
uniform vec4 uHoverColor;
uniform float uShadow;

out vec4 outColor;

float sdPill(vec2 p, vec4 box) {
  vec2 extent = max(box.zw, vec2(0.0));
  float r = min(extent.x, extent.y);
  vec2 q = abs(p - box.xy) - extent + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

float sdCapsule(vec2 p, vec2 a, vec2 b, float r) {
  vec2 pa = p - a;
  vec2 ba = b - a;
  float h = clamp(dot(pa, ba) / max(dot(ba, ba), 0.0001), 0.0, 1.0);
  return length(pa - ba * h) - r;
}

vec4 blend(vec4 a, vec4 b, float k) {
  float h = clamp(0.5 + 0.5 * (b.w - a.w) / k, 0.0, 1.0);
  return vec4(mix(b.rgb, a.rgb, h), mix(b.w, a.w, h) - k * h * (1.0 - h));
}

vec4 field(vec2 p) {
  vec4 shape = vec4(uPill, 1e5);
  for (int i = 0; i < ${MAX_PILLS}; i++) {
    vec4 pill = uPills[i];
    if (pill.z <= 0.0) continue;
    shape = blend(shape, vec4(uPill, sdPill(p, pill)), uSmooth);
  }
  for (int i = 0; i < ${MAX_DROPS}; i++) {
    if (i >= uCount) break;
    vec4 drop = uDrops[i];
    if (drop.z <= 0.0) continue;
    float d = sdCapsule(p, drop.xy, drop.xy - uTails[i], drop.z);
    if (d > shape.w + uSmooth) continue;
    shape = blend(shape, vec4(uTints[i], d), uSmooth);
  }
  return shape;
}

void main() {
  vec2 p = vec2(gl_FragCoord.x, uSize.y * uRatio - gl_FragCoord.y) / uRatio;
  vec4 shape = field(p);
  float d = shape.w;
  float aa = max(fwidth(d), 0.35);
  float body = clamp(0.5 - d / aa, 0.0, 1.0);

  vec2 grad = vec2(dFdx(d), -dFdy(d));
  vec2 n = length(grad) > 0.0001 ? normalize(grad) : vec2(0.0, 1.0);
  float rim = (1.0 - smoothstep(0.0, 1.6, -d)) * max(0.0, -n.y) * 0.3;
  vec3 fill = mix(shape.rgb, vec3(1.0), rim) * (1.0 - 0.06 * max(0.0, n.y) * (1.0 - smoothstep(0.0, 6.0, -d)));

  float lifted = field(p - vec2(0.0, 3.0)).w;
  float shadow = (1.0 - smoothstep(-3.0, 9.0, lifted)) * uShadow * (1.0 - body);

  float hover = clamp(0.5 - sdPill(p, uHover) / aa, 0.0, 1.0) * uHoverColor.a;

  vec4 color = vec4(0.0, 0.0, 0.0, shadow);
  color = vec4(uHoverColor.rgb * hover, hover) + color * (1.0 - hover);
  color = vec4(fill * body, body) + color * (1.0 - body);
  outColor = color;
}
`;

const parse = (value) => {
  const match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(value).trim());
  if (match) {
    const hex = match[1].length === 3 ? match[1].replace(/./g, (digit) => digit + digit) : match[1];
    return [0, 2, 4].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255).concat(1);
  }
  const rgba = /rgba?\(([^)]+)\)/i.exec(String(value));
  if (rgba) {
    const parts = rgba[1].split(",").map((part) => parseFloat(part));
    return [parts[0] / 255, parts[1] / 255, parts[2] / 255, parts[3] ?? 1];
  }
  return [1, 1, 1, 1];
};

const clamp01 = (value) => Math.min(1, Math.max(0, value));

const travelEase = (t) => {
  const x = clamp01(t);
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2;
};

const bezier = (a, b, c, d, t) => {
  const u = 1 - t;
  return u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d;
};

const pillPath = (x, y, w, h) => {
  if (w <= 0.5 || h <= 0.5) return "";
  const r = Math.min(w, h) / 2;
  return `M${x + r},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h - r}A${r},${r} 0 0 1 ${x + w - r},${y + h}H${x + r}A${r},${r} 0 0 1 ${x},${y + h - r}V${y + r}A${r},${r} 0 0 1 ${x + r},${y}Z`;
};

const compile = (gl, type, source) => {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  return shader;
};

const UNIFORMS = [
  "uSize",
  "uRatio",
  "uPills",
  "uHover",
  "uDrops",
  "uTails",
  "uTints",
  "uCount",
  "uSmooth",
  "uPill",
  "uHoverColor",
  "uShadow"
];

const build = (gl) => {
  const program = gl.createProgram();
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program);
    return null;
  }
  const uniforms = Object.fromEntries(UNIFORMS.map((name) => [name, gl.getUniformLocation(program, name)]));
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  return { program, uniforms, buffer, position: gl.getAttribLocation(program, "aPosition") };
};

export function createGooeyNav(host, options = {}) {
  if (!host) throw new Error("createGooeyNav needs a host element");
  const list = host.querySelector("[data-nav-list]") || host.querySelector("ul");
  if (!list) throw new Error("createGooeyNav needs a list of links");
  const links = Array.from(list.querySelectorAll("a, button"));
  if (!links.length) throw new Error("createGooeyNav needs at least one link");

  const style = getComputedStyle(host);
  const token = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  const pillColor = options.color || token("--chalk", "#e9eff5");
  const pillRgb = parse(pillColor);
  host.style.setProperty("--gooey-nav-pill", pillColor);
  host.style.setProperty("--gooey-nav-ink", options.activeTextColor || token("--night", "#070a0f"));

  const settings = {
    animationTime: Number(options.animationTime) || 600,
    particleCount: Number(options.particleCount) || 15,
    timeVariance: options.timeVariance === undefined ? 300 : Number(options.timeVariance),
    spread: options.spread === undefined ? 56 : Number(options.spread),
    gooeyness: clamp01(options.gooeyness === undefined ? 0.5 : options.gooeyness),
    wobble: clamp01(options.wobble === undefined ? 0.5 : options.wobble),
    hoverEffect: options.hoverEffect !== false,
    pillRgb,
    hoverRgb: parse(options.hoverColor || token("--rule-hard", "rgba(233,239,245,.18)")),
    shadow: options.shadow === undefined ? 0.4 : Number(options.shadow)
  };

  const size = options.size === "sm" || options.size === "lg" ? options.size : "md";
  host.classList.add("gooey-nav", `gooey-nav--${size}`);
  if (options.frame !== false) host.classList.add("gooey-nav--framed");

  /* --------------------------------------------------------------- the markup --- */

  /* The items are the page's own links; this only names them for the port. An item that
     carries `aria-current` starts selected, so the page says which page it is in its own
     markup rather than in a script. */
  const items = links.map((link, index) => {
    const item = link.closest("li") || link;
    item.classList.add("gooey-nav__item");
    item.dataset.gooeyIndex = String(index);
    link.classList.add("gooey-nav__link");
    return { link, item };
  });

  const stage = document.createElement("div");
  stage.className = "gooey-nav__stage";
  host.insertBefore(stage, list);
  stage.appendChild(list);
  list.classList.add("gooey-nav__list");
  if (!list.hasAttribute("data-nav-list")) list.dataset.navList = "";

  const canvas = document.createElement("canvas");
  canvas.className = "gooey-nav__canvas";
  canvas.setAttribute("aria-hidden", "true");

  const fallback = document.createElement("span");
  fallback.className = "gooey-nav__fallback";
  fallback.setAttribute("aria-hidden", "true");

  /* The label has to be painted in the pill's colour, and only where the pill is, so a
     second copy of the list is clipped to the traced field and laid over the first. The
     copy is presentational: its links have no `href` and no tab stop. */
  const overlay = list.cloneNode(true);
  overlay.classList.add("gooey-nav__list--active");
  overlay.setAttribute("aria-hidden", "true");
  overlay.removeAttribute("data-nav-list");
  for (const dead of overlay.querySelectorAll("a")) {
    dead.removeAttribute("href");
    dead.removeAttribute("aria-current");
    dead.tabIndex = -1;
  }

  stage.append(canvas, fallback, list, overlay);

  /* The page change itself. Overridable so a test can watch it happen without leaving the
     document it is measuring, and so a caller that does not want to navigate (a nav inside
     a dialog, say) can say what a selection means. */
  const navigate = typeof options.navigate === "function"
    ? options.navigate
    : (url) => location.assign(url);

  const start = (() => {
    if (Number.isInteger(options.active)) return Math.min(Math.max(0, options.active), items.length - 1);
    const marked = items.findIndex(({ link }) => link.getAttribute("aria-current") === "page");
    return marked >= 0 ? marked : 0;
  })();

  let active = start;
  let reduce = prefersReducedMotion();
  let disposed = false;
  /* The pending page change, so a second selection can replace the first rather than race
     it, and so destroying the nav cancels a navigation it was about to cause. `leaving` is
     a destination and a frame time; `leavingGuard` is the timer that follows it if the page
     stops painting. */
  let leaving = null;
  let leavingGuard = 0;

  /* --------------------------------------------------------------- the engine --- */

  const gl = canvas.getContext("webgl2", { alpha: true, premultipliedAlpha: true, antialias: false });
  const pipeline = gl ? build(gl) : null;
  fallback.style.display = pipeline ? "none" : "block";
  canvas.style.display = pipeline ? "block" : "none";

  const view = { width: 0, height: 0 };
  let rects = [];
  let target = -1;
  const pills = new Map();
  const drops = [];
  const hover = { x: 0, y: 0, w: 0, h: 0, alpha: 0, index: -1 };
  const pillData = new Float32Array(MAX_PILLS * 4);
  const dropData = new Float32Array(MAX_DROPS * 4);
  const tailData = new Float32Array(MAX_DROPS * 2);
  const tintData = new Float32Array(MAX_DROPS * 3);
  let raf = 0;
  let last = 0;

  const measure = () => {
    const box = stage.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    view.width = box.width;
    view.height = box.height;
    const outerWidth = box.width + MARGIN * 2;
    const outerHeight = box.height + MARGIN * 2;
    Object.assign(canvas.style, {
      left: `${-MARGIN}px`,
      top: `${-MARGIN}px`,
      width: `${outerWidth}px`,
      height: `${outerHeight}px`
    });
    const width = Math.max(1, Math.round(outerWidth * ratio));
    const height = Math.max(1, Math.round(outerHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    rects = Array.from(list.children).map((item) => {
      const rect = item.getBoundingClientRect();
      return { x: rect.left - box.left, y: rect.top - box.top, w: rect.width, h: rect.height };
    });
  };

  /* The field, traced column by column into a path the overlay can be clipped to. This is
     the half of the component that has no shader: the goo is drawn twice, once as pixels
     and once as a mask, and both read the same boxes and droplets. */
  const trace = (boxes, blobs, smooth) => {
    const field = (x, y) => {
      let d = 1e5;
      const merge = (value) => {
        const h = clamp01(0.5 + (0.5 * (value - d)) / smooth);
        d = value + (d - value) * h - smooth * h * (1 - h);
      };
      for (const box of boxes) {
        const r = Math.min(box.hw, box.hh);
        const qx = Math.abs(x - box.cx) - box.hw + r;
        const qy = Math.abs(y - box.cy) - box.hh + r;
        merge(Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r);
      }
      for (const blob of blobs) {
        const px = x - blob.x;
        const py = y - blob.y;
        const bx = -blob.tx;
        const by = -blob.ty;
        const span = bx * bx + by * by;
        const h = span > 0.0001 ? clamp01((px * bx + py * by) / span) : 0;
        const value = Math.hypot(px - bx * h, py - by * h) - blob.r;
        if (value > d + smooth) continue;
        merge(value);
      }
      return d;
    };
    const step = 2;
    const parts = [];
    for (let y = 0; y < view.height; y += 1) {
      const cy = y + 0.5;
      let previous = field(0, cy);
      let start = previous < 0 ? 0 : null;
      for (let x = step; x <= view.width + step; x += step) {
        const value = field(Math.min(x, view.width), cy);
        if (previous < 0 !== value < 0) {
          const cross = x - step + (step * previous) / (previous - value);
          if (value < 0) start = cross;
          else if (start !== null) {
            parts.push(`M${start.toFixed(1)},${y}H${cross.toFixed(1)}V${y + 1}H${start.toFixed(1)}Z`);
            start = null;
          }
        }
        previous = value;
      }
      if (start !== null) parts.push(`M${start.toFixed(1)},${y}H${view.width}V${y + 1}H${start.toFixed(1)}Z`);
    }
    return parts.join("");
  };

  const pill = (index) => {
    if (!pills.has(index)) pills.set(index, { mass: 0, scale: 0, velocity: 0, outgoing: 0 });
    return pills.get(index);
  };

  const shape = (index, amount) => {
    const rect = rects[index];
    if (!rect || amount <= 0.002) return null;
    const h = rect.h * Math.min(1.08, 0.42 + 0.58 * Math.sqrt(Math.max(0, amount)));
    const w = Math.max(h, rect.w * Math.max(0, amount) ** 0.75);
    return { x: rect.x + rect.w / 2 - w / 2, y: rect.y + rect.h / 2 - h / 2, w, h };
  };

  const plan = (drop, index, now, fresh) => {
    const rect = rects[index];
    if (!rect) return;
    const reach = Math.max(0, settings.spread);
    const ex = rect.x + rect.w / 2 + (Math.random() - 0.5) * rect.w * 0.45;
    const ey = rect.y + rect.h / 2 + (Math.random() - 0.5) * rect.h * 0.2;
    const base = Math.max(0.15, settings.animationTime / 1000);
    const variance = (Math.random() - 0.5) * (Math.max(0, settings.timeVariance) / 1000);
    const duration = Math.max(0.15, base * (0.62 + Math.random() * 0.32) + variance * 0.6);
    const out = Math.random() * Math.PI * 2;
    const into = Math.random() * Math.PI * 2;
    const burst = reach * (0.7 + Math.random() * 0.6);
    const swing = reach * (0.55 + Math.random() * 0.55);
    const carry = Math.min(duration / 3, 0.12);
    drop.index = index;
    drop.start = now;
    drop.duration = duration;
    drop.path = [
      drop.x,
      drop.y,
      fresh ? drop.x + Math.cos(out) * burst * 1.6 : drop.x + drop.vx * carry,
      fresh ? drop.y + Math.sin(out) * burst * 1.6 : drop.y + drop.vy * carry,
      ex + Math.cos(into) * swing * 1.5,
      ey + Math.sin(into) * swing * 1.5,
      ex,
      ey
    ];
  };

  const emit = (from, to, now) => {
    const source = pills.get(from);
    const rect = rects[from];
    if (!source || !rect || !rects[to]) return;
    const available = source.mass - source.outgoing;
    if (available <= 0.002) return;
    const room = MAX_DROPS - drops.length;
    const count = Math.min(room, Math.max(1, Math.round(Math.max(1, settings.particleCount) * available)));
    if (count <= 0) return;
    const window = Math.max(0.15, settings.animationTime / 1000) * 0.16;
    for (let i = 0; i < count; i += 1) {
      const mass = available / count;
      source.outgoing += mass;
      drops.push({
        x: rect.x + rect.w / 2 + (Math.random() - 0.5) * rect.w * 0.6,
        y: rect.y + rect.h / 2 + (Math.random() - 0.5) * rect.h * 0.3,
        vx: 0,
        vy: 0,
        mass,
        size: 0.8 + Math.random() * 0.4,
        given: 0,
        source: from,
        index: to,
        leave: now + Math.random() * window * 1000,
        emerged: 0,
        absorbed: 0,
        state: "waiting",
        tint: null
      });
    }
  };

  const render = () => {
    const height = rects[target]?.h ?? rects[0]?.h ?? 36;
    const smooth = height * (0.18 + 0.5 * clamp01(settings.gooeyness));
    const boxes = [];
    const blobs = [];
    for (const [index, state] of pills) {
      const box = shape(index, state.scale);
      if (box) boxes.push({ cx: box.x + box.w / 2, cy: box.y + box.h / 2, hw: box.w / 2, hh: box.h / 2 });
    }
    for (const drop of drops) {
      if (drop.state === "waiting") continue;
      const radius = 0.26 * height * drop.size * Math.min(drop.emerged, 1 - drop.absorbed);
      if (radius <= 0.05) continue;
      const speed = Math.hypot(drop.vx, drop.vy);
      const length = Math.min(radius * 1.1, speed * 0.018);
      blobs.push({
        x: drop.x,
        y: drop.y,
        r: radius,
        tx: speed > 0.01 ? (drop.vx / speed) * length : 0,
        ty: speed > 0.01 ? (drop.vy / speed) * length : 0,
        tint: drop.tint
      });
    }

    const settledPill = boxes.length === 1 && !blobs.length;
    if (settledPill) {
      const box = boxes[0];
      overlay.style.clipPath = `path('${pillPath(box.cx - box.hw, box.cy - box.hh, box.hw * 2, box.hh * 2)}')`;
    } else {
      overlay.style.clipPath = `path('${trace(boxes, blobs, smooth) || "M0,0Z"}')`;
    }
    overlay.style.visibility = "visible";

    if (!pipeline || !gl) {
      const box = shape(target, 1);
      if (box) {
        fallback.style.transform = `translate(${box.x.toFixed(2)}px, ${box.y.toFixed(2)}px)`;
        fallback.style.width = `${box.w}px`;
        fallback.style.height = `${box.h}px`;
      }
      fallback.style.opacity = box ? "1" : "0";
      return;
    }

    let slot = 0;
    for (const box of boxes) {
      if (slot >= MAX_PILLS) break;
      pillData[slot * 4] = box.cx + MARGIN;
      pillData[slot * 4 + 1] = box.cy + MARGIN;
      pillData[slot * 4 + 2] = box.hw;
      pillData[slot * 4 + 3] = box.hh;
      slot += 1;
    }
    pillData.fill(0, slot * 4);
    let count = 0;
    for (const blob of blobs) {
      if (count >= MAX_DROPS) break;
      dropData[count * 4] = blob.x + MARGIN;
      dropData[count * 4 + 1] = blob.y + MARGIN;
      dropData[count * 4 + 2] = blob.r;
      dropData[count * 4 + 3] = 0;
      tailData[count * 2] = blob.tx;
      tailData[count * 2 + 1] = blob.ty;
      const tint = blob.tint || settings.pillRgb;
      tintData[count * 3] = tint[0];
      tintData[count * 3 + 1] = tint[1];
      tintData[count * 3 + 2] = tint[2];
      count += 1;
    }

    const { program, uniforms, buffer, position } = pipeline;
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    gl.uniform2f(uniforms.uSize, view.width + MARGIN * 2, view.height + MARGIN * 2);
    gl.uniform1f(uniforms.uRatio, canvas.width / Math.max(1, view.width + MARGIN * 2));
    gl.uniform4fv(uniforms.uPills, pillData);
    gl.uniform4f(uniforms.uHover, hover.x + MARGIN, hover.y + MARGIN, hover.w / 2, hover.h / 2);
    gl.uniform4fv(uniforms.uDrops, dropData);
    gl.uniform2fv(uniforms.uTails, tailData);
    gl.uniform3fv(uniforms.uTints, tintData);
    gl.uniform1i(uniforms.uCount, count);
    gl.uniform1f(uniforms.uSmooth, smooth);
    gl.uniform3f(uniforms.uPill, settings.pillRgb[0], settings.pillRgb[1], settings.pillRgb[2]);
    gl.uniform4f(
      uniforms.uHoverColor,
      settings.hoverRgb[0],
      settings.hoverRgb[1],
      settings.hoverRgb[2],
      settings.hoverRgb[3] * hover.alpha
    );
    gl.uniform1f(uniforms.uShadow, settings.shadow);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  };

  const frame = (now) => {
    raf = 0;
    const dt = Math.min(0.05, (now - last) / 1000 || 0);
    last = now;
    const emergeTime = 0.09;
    const absorbTime = 0.12;

    for (let i = drops.length - 1; i >= 0; i -= 1) {
      const drop = drops[i];
      if (drop.state === "waiting") {
        if (now < drop.leave) continue;
        drop.state = "flying";
        plan(drop, drop.index, now, true);
      }
      if (drop.state === "flying") {
        drop.emerged = Math.min(1, drop.emerged + dt / emergeTime);
        const source = pills.get(drop.source);
        if (source && drop.given < drop.mass) {
          const step = Math.min(drop.mass * (dt / emergeTime), drop.mass - drop.given);
          source.mass = Math.max(0, source.mass - step);
          source.outgoing = Math.max(0, source.outgoing - step);
          drop.given += step;
        }
        const t = clamp01((now - drop.start) / 1000 / drop.duration);
        const eased = travelEase(t);
        const [x0, y0, x1, y1, x2, y2, x3, y3] = drop.path;
        const x = bezier(x0, x1, x2, x3, eased);
        const y = bezier(y0, y1, y2, y3, eased);
        if (dt > 0) {
          drop.vx = (x - drop.x) / dt;
          drop.vy = (y - drop.y) / dt;
        }
        drop.x = x;
        drop.y = y;
        if (t >= 1) drop.state = "absorbing";
      }
      if (drop.state === "absorbing") {
        const step = Math.min(1 - drop.absorbed, dt / absorbTime);
        drop.absorbed += step;
        drop.vx *= 0.8;
        drop.vy *= 0.8;
        const host2 = pill(drop.index);
        host2.mass = Math.min(1, host2.mass + drop.mass * step);
        if (drop.absorbed >= 1) drops.splice(i, 1);
      }
    }

    const zeta = reduce ? 1 : 1 - 0.72 * clamp01(settings.wobble);
    const omega = 26;
    const steps = Math.max(1, Math.ceil(dt / (1 / 240)));
    const h = dt / steps;
    for (const [index, state] of pills) {
      for (let n = 0; n < steps; n += 1) {
        state.velocity += (omega * omega * (state.mass - state.scale) - 2 * zeta * omega * state.velocity) * h;
        state.scale += state.velocity * h;
      }
      if (index !== target && state.mass <= 0.002 && state.outgoing <= 0.002 && Math.abs(state.scale) < 0.004) {
        pills.delete(index);
      }
    }

    if (hover.index >= 0 && rects[hover.index]) {
      const rect = rects[hover.index];
      const k = 1 - Math.exp(-dt / 0.06);
      hover.x += (rect.x + rect.w / 2 - hover.x) * k;
      hover.y += (rect.y + rect.h / 2 - hover.y) * k;
      hover.w += (rect.w - hover.w) * k;
      hover.h += (rect.h - hover.h) * k;
    }
    const hoverGoal = settings.hoverEffect && hover.index >= 0 && hover.index !== target ? 1 : 0;
    hover.alpha += (hoverGoal - hover.alpha) * (1 - Math.exp(-dt / 0.08));

    render();

    /* The reveal is decided on painted frames rather than on a wall clock: the page is
       left after a frame that showed the burst, so a busy page (the feed's replay and its
       sea can starve timers for half a second) cannot navigate before the reader has seen
       anything, and a fast one leaves on the first frame past the reveal. The navigation
       is queued for the next frame because starting it inside this callback would take
       the frame it was meant to be seen in with it. */
    if (leaving && now >= leaving.at) {
      const destination = leaving.url;
      leaving = null;
      window.clearTimeout(leavingGuard);
      requestAnimationFrame(() => {
        if (!disposed) navigate(destination);
      });
    }

    const host2 = pills.get(target);
    const resting =
      drops.length === 0 &&
      pills.size === 1 &&
      host2 &&
      Math.abs(host2.mass - host2.scale) < 0.0008 &&
      Math.abs(host2.velocity) < 0.004;
    const hoverRect = rects[hover.index];
    const hovering =
      Math.abs(hoverGoal - hover.alpha) > 0.003 ||
      (hoverRect && Math.abs(hoverRect.x + hoverRect.w / 2 - hover.x) + Math.abs(hoverRect.w - hover.w) > 0.1);
    if (!resting || hovering) raf = requestAnimationFrame(frame);
    else {
      host2.mass = 1;
      host2.scale = 1;
      host2.velocity = 0;
      hover.alpha = hoverGoal;
      render();
    }
  };

  const wake = () => {
    if (raf || disposed) return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  };

  const settle = (index) => {
    pills.clear();
    drops.length = 0;
    target = index;
    const state = pill(index);
    state.mass = 1;
    state.scale = 1;
    state.velocity = 0;
    render();
  };

  const go = (index, animate) => {
    if (index === target) return;
    if (!rects[index] || !animate || reduce || target < 0) {
      settle(index);
      return;
    }
    const now = performance.now();
    target = index;
    pill(index);
    for (const drop of drops) {
      drop.index = index;
      if (drop.state === "flying") plan(drop, index, now, false);
      if (drop.state === "absorbing") {
        drop.state = "flying";
        drop.mass *= 1 - drop.absorbed;
        drop.absorbed = 0;
        plan(drop, index, now, false);
      }
    }
    for (const from of [...pills.keys()]) {
      if (from !== index) emit(from, index, now);
    }
    wake();
  };

  /* ------------------------------------------------------------------ the input --- */

  /* The destination document, fetched while the pointer is still travelling to the link.
     Both pages are small, but a navigation cannot start before the document has arrived,
     and this is the one time the browser is otherwise idle. It is a hint, not a hold: a
     reader who clicks instantly gets the same navigation, just without the head start. */
  const prefetched = new Set();

  const prefetch = (item) => {
    const link = item.querySelector("a");
    const href = link?.getAttribute("href");
    if (!href || href.startsWith("#") || link.target === "_blank" || link.hasAttribute("download")) return;
    let url;
    try {
      url = new URL(href, location.href);
    } catch {
      return;
    }
    if (url.origin !== location.origin || url.href === location.href) return;
    if (prefetched.has(url.href)) return;
    prefetched.add(url.href);
    const hint = document.createElement("link");
    hint.rel = "prefetch";
    hint.as = "document";
    hint.href = url.href;
    document.head.append(hint);
  };

  const onOver = (event) => {
    const item = event.target.closest("[data-gooey-index]");
    if (!item || !list.contains(item)) return;
    prefetch(item);
    const index = Number(item.dataset.gooeyIndex);
    const rect = rects[index];
    if (!rect) return;
    if (hover.alpha < 0.01) {
      hover.x = rect.x + rect.w / 2;
      hover.y = rect.y + rect.h / 2;
      hover.w = rect.w;
      hover.h = rect.h;
    }
    hover.index = index;
    wake();
  };

  const onLeave = () => {
    hover.index = -1;
    wake();
  };

  /* A selection is a navigation, so the burst is played first and the address followed
     after it — the delay is the animation, not a delay before doing anything. The active
     item does nothing, and anything the browser would have done differently (a new tab, a
     modifier, a middle click, a link to somewhere else) is left to the browser. */
  const onClick = (event) => {
    const item = event.target.closest("[data-gooey-index]");
    if (!item || !list.contains(item)) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    const index = Number(item.dataset.gooeyIndex);
    if (index === target) {
      event.preventDefault();
      return;
    }
    const link = item.querySelector("a");
    const href = link?.getAttribute("href");
    if (!href || href.startsWith("#") || link.target === "_blank" || link.hasAttribute("download")) return;
    let url;
    try {
      url = new URL(href, location.href);
    } catch {
      return;
    }
    if (url.origin !== location.origin) return;
    event.preventDefault();
    go(index, true);
    /* The burst is the page change, so the address is followed while the droplets are
       still in the air. A keyboard activation has nothing to watch and reduced motion has
       asked for nothing to watch, so both leave at once rather than making Enter or a
       reader who turned the setting on wait out an animation they cannot see. */
    const wait = reduce || event.detail === 0 ? 0 : settings.animationTime * REVEAL_SHARE;
    if (wait <= 0) {
      navigate(url.href);
      return;
    }
    leaving = { url: url.href, at: performance.now() + wait };
    window.clearTimeout(leavingGuard);
    /* The guard, not the plan: if the loop never reaches the reveal — a hidden tab, a
       page whose frames have stopped — the reader is still taken where they clicked. */
    leavingGuard = window.setTimeout(() => {
      const pending = leaving;
      leaving = null;
      if (!disposed && pending) navigate(pending.url);
    }, wait + 600);
  };

  list.addEventListener("pointerover", onOver);
  list.addEventListener("pointerleave", onLeave);
  list.addEventListener("click", onClick);
  /* Keyboard focus is the hover a reader without a pointer has: the same head start, on
     the same destination, before Enter is pressed. */
  list.addEventListener("focusin", (event) => {
    const item = event.target.closest("[data-gooey-index]");
    if (item && list.contains(item)) prefetch(item);
  });

  const motionWatch = watchReducedMotion((matches) => {
    reduce = matches;
    if (reduce && drops.length) settle(target);
    wake();
  });

  const resizeObserver = new ResizeObserver(() => {
    measure();
    if (!drops.length) settle(target < 0 ? start : target);
    else render();
  });
  resizeObserver.observe(stage);
  resizeObserver.observe(list);

  const onFonts = () => {
    measure();
    if (!drops.length) settle(target < 0 ? start : target);
    else render();
  };
  document.fonts?.addEventListener?.("loadingdone", onFonts);

  measure();
  settle(start);
  document.fonts?.ready?.then(onFonts).catch(() => {});

  return {
    element: stage,
    active: () => active,
    set: (next = {}) => {
      if (!Number.isInteger(next.active)) return;
      const index = Math.min(Math.max(0, next.active), items.length - 1);
      if (index === active) return;
      active = index;
      go(index, next.animate !== false);
    },
    wake,
    destroy: () => {
      disposed = true;
      leaving = null;
      window.clearTimeout(leavingGuard);
      cancelAnimationFrame(raf);
      raf = 0;
      motionWatch();
      resizeObserver.disconnect();
      document.fonts?.removeEventListener?.("loadingdone", onFonts);
      list.removeEventListener("pointerover", onOver);
      list.removeEventListener("pointerleave", onLeave);
      list.removeEventListener("click", onClick);
      if (gl && pipeline) {
        gl.deleteBuffer(pipeline.buffer);
        gl.deleteProgram(pipeline.program);
      }
      overlay.remove();
      canvas.remove();
      fallback.remove();
      list.classList.remove("gooey-nav__list");
      host.insertBefore(list, stage);
      stage.remove();
      host.classList.remove("gooey-nav", `gooey-nav--${size}`, "gooey-nav--framed");
      for (const { item, link } of items) {
        item.classList.remove("gooey-nav__item");
        link.classList.remove("gooey-nav__link");
        delete item.dataset.gooeyIndex;
      }
    }
  };
}
