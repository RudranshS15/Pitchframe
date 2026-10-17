/* InfiniteMenu — a sphere of tiles that a reader spins by hand, with the front one named
 * beside it, and a click on that front tile opening what it stands for.
 *
 * A port of the React Bits component of the same name (https://reactbits.dev), with the
 * React half replaced by a module lifecycle: `createInfiniteMenu(container, options)`
 * returns `{ set, setItems, active, wake, destroy }`, the same shape `createSlats()` returns
 * for the sea. The React parts of the component were only a lifecycle — a container ref, an
 * effect that owns the WebGL scene, and an effect that pushes new props into it — and
 * everything under those methods is the component's own logic: the two shaders, the
 * Fibonacci sphere, the quaternion drag with its stretch, the neighbour walk the arrows and
 * the autoplay both step along, the settle that decides when a tile has arrived, and the
 * atlas of tiles sampled by one instanced draw call.
 *
 * Ported from React Bits, Copyright (c) 2026 David Haz.
 * MIT + Commons Clause License Condition v1.0. The full notice ships beside this file as
 * vendor/react-bits-LICENSE.md, and the Commons Clause covers "a ported version" as well as
 * the components themselves: this file is part of this application and is not redistributed
 * on its own.
 *
 * The scene is WebGL2 written by hand, not a 3D library. Tiles are a texture atlas drawn
 * once into a 2D canvas and sampled per instance, so the whole sphere is one
 * `drawElementsInstanced` call and there is no per-tile element to lay out.
 *
 * Three things are added for this page rather than for the effect:
 *
 *   Tiles can be *drawn* rather than loaded. Upstream every tile is an image URL and the
 *   atlas is filled by an `Image` per item, which is right for a gallery and wrong here:
 *   this project ships no binary assets, has no build step, and must work with no network,
 *   so a card is drawn from its own numbers by a `draw(ctx, size, index)` the caller
 *   supplies, with the context already translated and clipped to that item's cell.
 *   `item.image` still works, and is still what happens when `draw` is absent.
 *
 *   `prefers-reduced-motion` is taken from ./motion.js rather than from the component's own
 *   one-off `matchMedia`, so the intro spin and the autoplay honour the setting through the
 *   same watched runtime every other port uses, and a reader who turns it on does not have
 *   to reload.
 *
 *   A container the browser cannot give WebGL2 to gets a plain list of the same items
 *   instead of an empty box. Upstream returns nothing and the caller is left with a blank
 *   rectangle; here the sphere is the *interface* to a catalogue, so failing to draw it
 *   must not also fail to reach the matches. The list carries the same items, the same
 *   names and the same click behaviour, and the handle's `fallback` says which of the two
 *   was built, so a caller can label it honestly instead of pretending.
 *
 * One more difference worth stating, because it is visible in the CSS: `data-moving` and
 * `data-layout` are written onto the info container even when the caller has replaced the
 * built-in block with `renderInfo`. Upstream only writes them for its own block, so a
 * custom overlay cannot animate on the same signal. This page's overlay can, and does.
 *
 * And the arrow keys are answered on the document rather than on the sphere's own element,
 * guarded so a key aimed at a link or a button is left to it. Upstream's handler waits for
 * the component to be focused, and a page that prints "Arrow keys step" before the reader
 * has focused anything would be promising something it does not do; the reason is at the
 * handler.
 *
 * No network, no images, no build step, and the only import is the shared motion runtime.
 */

import { prefersReducedMotion, watchReducedMotion } from "./motion.js";

const VERTEX = `#version 300 es
in vec2 aCorner;
in vec4 aTile;
uniform vec4 uOrientation;
uniform mat4 uProjection;
uniform float uCamera;
uniform float uRadius;
uniform float uTileRadius;
uniform vec4 uSpin;
uniform float uStretch;
uniform float uShift;
out vec2 vUv;
out float vFront;
out float vItem;

vec3 rotate(vec4 q, vec3 v) {
  vec3 t = 2.0 * cross(q.xyz, v);
  return v + q.w * t + cross(q.xyz, t);
}

void main() {
  vec3 center = normalize(rotate(uOrientation, aTile.xyz));
  vec3 reference = abs(center.y) > 0.999 ? vec3(0.0, 0.0, 1.0) : vec3(0.0, 1.0, 0.0);
  vec3 tangent = normalize(cross(reference, center));
  vec3 bitangent = cross(center, tangent);
  float front = max(center.z, 0.0);
  vec3 offset = (tangent * aCorner.x + bitangent * aCorner.y) * uTileRadius * mix(0.5, 1.0, front);
  vec3 motion = cross(uSpin.xyz, center);
  float sweep = length(motion);
  if (sweep > 0.0001) {
    vec3 direction = motion / sweep;
    float amount = min(0.9, uSpin.w * uStretch * 0.12) * sweep;
    offset += direction * dot(offset, direction) * amount;
  }
  vec3 world = normalize(center * uRadius + offset) * uRadius * (1.0 + 0.003 * center.z);
  gl_Position = uProjection * vec4(world.xy, world.z - uCamera, 1.0);
  gl_Position.y += uShift * gl_Position.w;
  vUv = aCorner;
  vFront = center.z;
  vItem = aTile.w;
}`;

const FRAGMENT = `#version 300 es
precision highp float;
uniform sampler2D uAtlas;
uniform float uCells;
uniform float uInset;
uniform float uRoundness;
uniform float uGrayscale;
uniform float uDim;
uniform float uReveal;
uniform vec3 uPlaceholder;
uniform vec3 uTone;
in vec2 vUv;
in float vFront;
in float vItem;
out vec4 outColor;

void main() {
  float corner = clamp(uRoundness, 0.0, 1.0);
  vec2 q = abs(vUv) - (1.0 - corner);
  float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - corner;
  float aa = max(fwidth(d), 0.0001);
  float mask = 1.0 - smoothstep(-aa, aa, d);
  float alpha = mask * smoothstep(0.02, 0.4, vFront) * uReveal;
  if (alpha < 0.002) discard;
  vec2 cell = vec2(mod(vItem, uCells), floor(vItem / uCells));
  vec2 local = vec2(vUv.x, -vUv.y) * 0.5 + 0.5;
  vec4 sampled = texture(uAtlas, (cell + uInset + local * (1.0 - 2.0 * uInset)) / uCells);
  vec3 color = mix(uPlaceholder, sampled.rgb, sampled.a);
  float focus = smoothstep(0.93, 0.997, vFront);
  float gray = dot(color, vec3(0.2126, 0.7152, 0.0722));
  color = mix(color, vec3(gray), uGrayscale * (1.0 - focus));
  color = mix(color, uTone, uDim * (1.0 - focus));
  outColor = vec4(color * alpha, alpha);
}`;

const GRID = 12;
const RADIUS = 2;
const FOV = (30 * Math.PI) / 180;
const FRONT = [0, 0, 1];

/* The component's own defaults, unchanged: a caller that names nothing gets what
   reactbits.dev shows. */
const DEFAULTS = {
  count: 160,
  tileSize: 0.63,
  roundness: 0.2,
  zoom: 1.65,
  pullBack: 1,
  stretch: 0.6,
  inertia: 0.6,
  autoplay: 3,
  grayscale: true,
  dim: 0.5,
  intro: true,
  showInfo: true,
  theme: "dark"
};

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const normalize = (v) => {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
};
const quatMultiply = (a, b) => [
  a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
  a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
  a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
  a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]
];
const quatAxisAngle = (axis, angle) => {
  const s = Math.sin(angle / 2);
  return [axis[0] * s, axis[1] * s, axis[2] * s, Math.cos(angle / 2)];
};
const quatNormalize = (q) => {
  const length = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / length, q[1] / length, q[2] / length, q[3] / length];
};
const rotate = (q, v) => {
  const t = cross(q, v).map((value) => value * 2);
  const u = cross(q, t);
  return [v[0] + q[3] * t[0] + u[0], v[1] + q[3] * t[1] + u[1], v[2] + q[3] * t[2] + u[2]];
};

/* Fibonacci sphere. Even spacing without a spiral's seam, which is what lets the tiles
   read as a ball rather than as a ribbon. */
const sphere = (count) => {
  const golden = Math.PI * (3 - Math.sqrt(5));
  return Array.from({ length: count }, (_, i) => {
    const y = 1 - (2 * (i + 0.5)) / count;
    const ring = Math.sqrt(1 - y * y);
    return [Math.cos(golden * i) * ring, y, Math.sin(golden * i) * ring];
  });
};

const luminance = (hex) => {
  const value = String(hex).replace("#", "");
  const full = value.length === 3 ? value.replace(/./g, (c) => c + c) : value.slice(0, 6);
  const n = parseInt(full, 16);
  if (Number.isNaN(n)) return 0;
  return (0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
};

const compile = (gl, type, source) => {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
  gl.deleteShader(shader);
  return null;
};

/**
 * Mount the sphere in `container`.
 *
 * @param {HTMLElement} container the slot to build in. The root this module creates is
 *   appended to it, so the page keeps ownership of its own layout.
 * @param {object} [options] the component's props under the same names and with the same
 *   meanings as upstream, plus the three callbacks it carries: `onActiveChange(item,
 *   index)` when a tile settles in front, `onItemClick(item, index)` when the front tile is
 *   clicked, its action button is pressed, or Enter is pressed while the sphere has focus,
 *   and `renderInfo(item, index, moving)` to replace the built-in title/description/action
 *   block with a node of the caller's own. `draw(ctx, size, index)` on an item fills that
 *   item's tile; see the module docstring.
 * @returns {{element: HTMLElement|null, fallback: boolean, set: Function, setItems: Function,
 *   active: Function, wake: Function, destroy: Function}} a handle. `fallback` is true when
 *   WebGL2 was unavailable and the list was built instead of the sphere.
 */
export function createInfiniteMenu(container, options = {}) {
  const named = { ...DEFAULTS, ...options };
  const items = Array.isArray(options.items) ? [...options.items] : [];
  /* Read live by the frame loop, like the component's `settingsRef`. Resolved and flat,
     because the loop runs sixty times a second and should not walk a fallback chain. */
  const settings = {};

  let reduce = prefersReducedMotion();

  const root = document.createElement("div");
  root.className = "infinite-menu";
  root.tabIndex = 0;
  root.setAttribute("role", "group");
  root.setAttribute("aria-roledescription", "carousel");

  const dom = {
    canvas: document.createElement("canvas"),
    info: document.createElement("div"),
    list: null
  };
  dom.canvas.className = "infinite-menu__canvas";
  dom.info.className = "infinite-menu__info";
  dom.info.dataset.moving = "true";
  dom.info.dataset.layout = "side";
  root.append(dom.canvas, dom.info);
  container.appendChild(root);

  /* The front item, and the one the overlay last described. `shown` is what stops the
     overlay being rebuilt on every frame of a spin. */
  let active = 0;
  let shown = -1;
  let infoKey = "";
  let engine = null;

  /* ---------------------------------------------------------------- options --- */

  function apply(next = {}) {
    Object.assign(named, next);
    settings.count = clamp(Math.round(named.count), 6, 400);
    settings.tileSize = clamp(named.tileSize, 0.2, 1.2);
    settings.roundness = clamp(named.roundness, 0, 1);
    settings.zoom = clamp(named.zoom, 0.2, 4);
    settings.pullBack = clamp(named.pullBack, 0, 3);
    settings.stretch = clamp(named.stretch, 0, 3);
    settings.inertia = clamp(named.inertia, 0, 1);
    settings.autoplay = Math.max(0, named.autoplay);
    settings.grayscale = named.grayscale;
    settings.dim = clamp(named.dim, 0, 1);
    settings.intro = named.intro;
    settings.showInfo = named.showInfo;
    settings.theme = named.theme === "light" ? "light" : "dark";

    const ink = settings.theme === "light" ? "#120f17" : "#ffffff";
    const accent = named.accentColor || ink;
    root.style.setProperty("--infinite-menu-text", ink);
    root.style.setProperty("--infinite-menu-accent", accent);
    root.style.setProperty(
      "--infinite-menu-on-accent",
      luminance(accent) > 0.5 ? "#120f17" : "#ffffff"
    );
    root.style.setProperty(
      "--infinite-menu-shadow",
      settings.theme === "light"
        ? "0 2px 18px rgba(255, 255, 255, 0.7)"
        : "0 2px 18px rgba(0, 0, 0, 0.45)"
    );
    if (named.backgroundColor) root.style.backgroundColor = named.backgroundColor;
  }

  apply(options);

  /* ------------------------------------------------------------- the overlay --- */

  /* What the page is told when a tile is chosen. One function, because the canvas, the
     action button, the fallback list and the Enter key can all choose a tile and a second
     path would be a second definition of "open it". */
  function emit(item, index) {
    if (!item) return;
    if (typeof named.onItemClick === "function") named.onItemClick(item, index);
    else if (item.link) {
      window.open(item.link, item.link.startsWith("http") ? "_blank" : "_self", "noopener");
    }
  }

  function setActive(index) {
    active = index;
    paintInfo();
    if (typeof named.onActiveChange === "function") {
      named.onActiveChange(items[index], index);
    }
  }

  function stateMoving() {
    return dom.info.dataset.moving === "true";
  }

  function infoNode(item, index) {
    if (typeof named.renderInfo === "function") {
      const custom = document.createElement("div");
      custom.className = "infinite-menu__custom";
      const rendered = named.renderInfo(item, index, stateMoving());
      if (rendered) custom.append(rendered);
      return custom;
    }
    const block = document.createDocumentFragment();
    if (item && item.title) {
      const title = document.createElement("h2");
      title.className = "infinite-menu__title";
      title.textContent = item.title;
      block.append(title);
    }
    if (item && item.description) {
      const description = document.createElement("p");
      description.className = "infinite-menu__description";
      description.textContent = item.description;
      block.append(description);
    }
    if (item && (item.link || named.onItemClick)) {
      const label = item.title ? `Open ${item.title}` : "Open";
      let action;
      if (item.link) {
        action = document.createElement("a");
        action.href = item.link;
        action.rel = "noreferrer";
        if (item.link.startsWith("http")) action.target = "_blank";
      } else {
        action = document.createElement("button");
        action.type = "button";
      }
      action.className = "infinite-menu__action";
      action.setAttribute("aria-label", label);
      action.innerHTML =
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
        '<path d="M7 17L17 7M17 7H9M17 7V15" stroke="currentColor" stroke-width="2" ' +
        'stroke-linecap="round" stroke-linejoin="round"/></svg>';
      action.addEventListener("click", () => emit(item, index));
      block.append(action);
    }
    return block;
  }

  function paintInfo() {
    const item = items.length ? items[active % items.length] : null;
    const key = `${active}|${dom.info.dataset.moving}|${settings.showInfo}|${items.length}`;
    if (key === infoKey) return;
    infoKey = key;
    /* Rebuilt rather than patched: the block is three elements and it is rebuilt at most
       once per settle, which is far less often than the sphere moves. */
    dom.info.replaceChildren();
    if (!settings.showInfo || !item) return;
    dom.info.append(infoNode(item, active));
    root.setAttribute("aria-label", item.title || item.label || "Infinite menu");
  }

  /* ----------------------------------------------------------------- the list --- */

  /* The no-WebGL2 path. Same items, same order, same click behaviour, and the caller is
     told which of the two it got — see the module docstring. */
  function buildFallback() {
    const list = document.createElement("ol");
    list.className = "infinite-menu__fallback";
    items.forEach((item, index) => {
      const row = document.createElement("li");
      row.className = "infinite-menu__fallback-row";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "infinite-menu__fallback-btn";
      button.textContent = item.title || item.label || `Item ${index + 1}`;
      button.addEventListener("click", () => emit(item, index));
      const note = document.createElement("span");
      note.className = "infinite-menu__fallback-note";
      note.textContent = item.description || "";
      row.append(button, note);
      list.append(row);
    });
    dom.list = list;
    root.classList.add("infinite-menu--fallback");
    root.append(list);
  }

  /* ----------------------------------------------------------------- the ball --- */

  const gl = dom.canvas.getContext("webgl2", {
    alpha: true,
    premultipliedAlpha: true,
    antialias: true
  });

  function build(context) {
    const vertex = compile(context, context.VERTEX_SHADER, VERTEX);
    const fragment = compile(context, context.FRAGMENT_SHADER, FRAGMENT);
    const program = context.createProgram();
    if (!vertex || !fragment || !program) return null;
    context.attachShader(program, vertex);
    context.attachShader(program, fragment);
    context.linkProgram(program);
    if (!context.getProgramParameter(program, context.LINK_STATUS)) return null;
    context.useProgram(program);

    const uniforms = {};
    for (const name of [
      "uOrientation", "uProjection", "uCamera", "uRadius", "uTileRadius", "uSpin", "uStretch",
      "uShift", "uAtlas", "uCells", "uInset", "uRoundness", "uGrayscale", "uDim", "uReveal",
      "uPlaceholder", "uTone"
    ]) {
      uniforms[name] = context.getUniformLocation(program, name);
    }

    const corners = [];
    const indices = [];
    for (let y = 0; y <= GRID; y++) {
      for (let x = 0; x <= GRID; x++) corners.push((x / GRID) * 2 - 1, (y / GRID) * 2 - 1);
    }
    for (let y = 0; y < GRID; y++) {
      for (let x = 0; x < GRID; x++) {
        const i = y * (GRID + 1) + x;
        indices.push(i, i + 1, i + GRID + 1, i + 1, i + GRID + 2, i + GRID + 1);
      }
    }

    const vao = context.createVertexArray();
    context.bindVertexArray(vao);
    const cornerBuffer = context.createBuffer();
    context.bindBuffer(context.ARRAY_BUFFER, cornerBuffer);
    context.bufferData(context.ARRAY_BUFFER, new Float32Array(corners), context.STATIC_DRAW);
    const cornerLocation = context.getAttribLocation(program, "aCorner");
    context.enableVertexAttribArray(cornerLocation);
    context.vertexAttribPointer(cornerLocation, 2, context.FLOAT, false, 0, 0);
    const indexBuffer = context.createBuffer();
    context.bindBuffer(context.ELEMENT_ARRAY_BUFFER, indexBuffer);
    context.bufferData(context.ELEMENT_ARRAY_BUFFER, new Uint16Array(indices), context.STATIC_DRAW);
    const tileBuffer = context.createBuffer();
    context.bindBuffer(context.ARRAY_BUFFER, tileBuffer);
    const tileLocation = context.getAttribLocation(program, "aTile");
    context.enableVertexAttribArray(tileLocation);
    context.vertexAttribPointer(tileLocation, 4, context.FLOAT, false, 0, 0);
    context.vertexAttribDivisor(tileLocation, 1);
    context.bindVertexArray(null);

    const atlasTexture = context.createTexture();
    context.activeTexture(context.TEXTURE0);
    context.bindTexture(context.TEXTURE_2D, atlasTexture);
    context.texParameteri(context.TEXTURE_2D, context.TEXTURE_WRAP_S, context.CLAMP_TO_EDGE);
    context.texParameteri(context.TEXTURE_2D, context.TEXTURE_WRAP_T, context.CLAMP_TO_EDGE);
    context.texParameteri(context.TEXTURE_2D, context.TEXTURE_MIN_FILTER, context.LINEAR_MIPMAP_LINEAR);
    context.texParameteri(context.TEXTURE_2D, context.TEXTURE_MAG_FILTER, context.LINEAR);
    context.texImage2D(
      context.TEXTURE_2D, 0, context.RGBA, 1, 1, 0, context.RGBA, context.UNSIGNED_BYTE,
      new Uint8Array([0, 0, 0, 0])
    );
    context.uniform1i(uniforms.uAtlas, 0);

    const atlas = { canvas: document.createElement("canvas"), cells: 1, cell: 512, request: 0, dirty: false };
    const drag = { active: false, id: -1, x: 0, y: 0, dx: 0, dy: 0, moved: 0, start: 0 };
    const state = {
      width: 1,
      height: 1,
      tiles: [],
      assign: [],
      count: 0,
      orientation: quatNormalize([0.08, -0.3, 0, 1]),
      axis: [0, 1, 0],
      speed: settings.intro && !reduce ? 3.2 : 0,
      camera: 0,
      target: -1,
      front: 0,
      moving: true,
      reveal: 0,
      hovered: false,
      focused: false,
      idle: 0,
      settle: 0,
      tilePx: 0,
      layout: "side",
      shift: 0,
      started: false
    };
    let raf = 0;
    let timer = 0;
    let last = 0;
    let visible = true;
    let alive = true;

    /* The tiles, spread over the sphere with no two neighbours carrying the same item.
       Upstream's assignment walk, kept as it is: `near` is the six closest tiles, and a
       candidate sharing one of their items is skipped, so the same card never lands twice
       in a row where a reader is looking. */
    const layout = () => {
      if (settings.count === state.count) return;
      state.count = settings.count;
      state.tiles = sphere(settings.count);
      const total = Math.max(1, items.length);
      const usage = new Array(total).fill(0);
      state.assign = state.tiles.map(() => -1);
      state.tiles.forEach((tile, i) => {
        const near = state.tiles
          .map((other, j) => [j === i ? -2 : dot(tile, other), j])
          .sort((a, b) => b[0] - a[0])
          .slice(0, 6)
          .map(([, j]) => state.assign[j]);
        let pick = -1;
        for (let k = 0; k < total; k++) {
          const candidate = (i + k) % total;
          if (near.includes(candidate)) continue;
          if (pick < 0 || usage[candidate] < usage[pick]) pick = candidate;
        }
        if (pick < 0) pick = i % total;
        state.assign[i] = pick;
        usage[pick] += 1;
      });
      const data = new Float32Array(settings.count * 4);
      state.tiles.forEach((tile, i) => data.set([tile[0], tile[1], tile[2], state.assign[i]], i * 4));
      context.bindBuffer(context.ARRAY_BUFFER, tileBuffer);
      context.bufferData(context.ARRAY_BUFFER, data, context.STATIC_DRAW);
      let first = -1;
      let firstZ = -2;
      state.tiles.forEach((tile, i) => {
        const z = rotate(state.orientation, tile)[2];
        if (state.assign[i] === 0 && z > firstZ) {
          firstZ = z;
          first = i;
        }
      });
      state.target = first;
    };

    const uploadAtlas = () => {
      atlas.dirty = false;
      context.activeTexture(context.TEXTURE0);
      context.bindTexture(context.TEXTURE_2D, atlasTexture);
      context.texImage2D(context.TEXTURE_2D, 0, context.RGBA, context.RGBA, context.UNSIGNED_BYTE, atlas.canvas);
      context.generateMipmap(context.TEXTURE_2D);
    };

    /* The atlas. One square cell per item, filled by `draw`, a ready-made canvas, or an
       image URL. A cell that draws nothing stays transparent and the shader shows the
       placeholder colour through it, which is why a half-filled atlas is a legible state
       rather than a broken one. */
    const loadItems = () => {
      const request = ++atlas.request;
      const total = Math.max(1, items.length);
      atlas.cells = Math.ceil(Math.sqrt(total));
      atlas.cell = clamp(Math.floor(4096 / atlas.cells), 64, 512);
      atlas.canvas.width = atlas.cells * atlas.cell;
      atlas.canvas.height = atlas.cells * atlas.cell;
      const ctx = atlas.canvas.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, atlas.canvas.width, atlas.canvas.height);
      uploadAtlas();
      state.count = 0;
      layout();
      /* Only an image can be late. A drawn atlas is complete by the time this line runs,
         so an item set that needs no network reveals on the first frame. */
      let pending = 0;
      items.forEach((item, index) => {
        const size = atlas.cell;
        if (!ctx) return;
        const x = (index % atlas.cells) * size;
        const y = Math.floor(index / atlas.cells) * size;
        ctx.save();
        ctx.beginPath();
        ctx.rect(x, y, size, size);
        ctx.clip();
        ctx.translate(x, y);
        if (typeof item.draw === "function") {
          item.draw(ctx, size, index);
        } else if (item.canvas) {
          ctx.drawImage(item.canvas, 0, 0, size, size);
        } else if (item.image) {
          pending += 1;
          const image = new Image();
          image.crossOrigin = "anonymous";
          image.decoding = "async";
          const done = () => {
            pending -= 1;
            if (pending <= 0) state.started = true;
            wake();
          };
          image.onload = () => {
            if (!alive || request !== atlas.request) return;
            const scale = Math.max(size / image.naturalWidth, size / image.naturalHeight);
            const width = image.naturalWidth * scale;
            const height = image.naturalHeight * scale;
            ctx.drawImage(image, (size - width) / 2, (size - height) / 2, width, height);
            atlas.dirty = true;
            done();
          };
          image.onerror = () => {
            if (!alive || request !== atlas.request) return;
            done();
          };
          image.src = item.image;
        }
        ctx.restore();
        atlas.dirty = true;
      });
      if (!pending) state.started = true;
      wake();
    };

    const tileRadius = () =>
      0.5 * RADIUS * Math.sqrt((4 * Math.PI) / Math.max(1, state.count)) * settings.tileSize;

    const restDistance = () => {
      const aspect = state.width / state.height;
      const span = Math.tan(FOV / 2) * Math.min(1, aspect);
      return RADIUS + tileRadius() / (0.46 * settings.zoom * span);
    };

    /* How big a tile lands on screen, published as `--infinite-menu-tile` so the page's
       text block can sit clear of the ball. The stacked layout is the narrow-screen one. */
    const measure = () => {
      const rest = restDistance();
      const radius = (tileRadius() / ((rest - RADIUS) * Math.tan(FOV / 2))) * (state.height / 2);
      const rounded = Math.round(radius);
      if (rounded !== state.tilePx) {
        state.tilePx = rounded;
        root.style.setProperty("--infinite-menu-tile", `${rounded}px`);
      }
      const mode = state.width / 2 - radius < 120 ? "stacked" : "side";
      if (mode !== state.layout) {
        state.layout = mode;
        dom.info.dataset.layout = mode;
      }
    };

    const nearest = () => {
      let best = 0;
      let bestZ = -2;
      for (let i = 0; i < state.tiles.length; i++) {
        const z = rotate(state.orientation, state.tiles[i])[2];
        if (z > bestZ) {
          bestZ = z;
          best = i;
        }
      }
      return best;
    };

    /* One step in a screen direction: the nearest tile on the front hemisphere whose
       offset points that way. Arrow keys and autoplay both walk this rather than jumping
       to a sphere index, so a step always lands on a neighbour the reader can see. */
    const neighbor = (from, direction) => {
      const origin = rotate(state.orientation, state.tiles[from]);
      let best = -1;
      let bestScore = Infinity;
      for (let i = 0; i < state.tiles.length; i++) {
        if (i === from) continue;
        const c = rotate(state.orientation, state.tiles[i]);
        if (c[2] < 0) continue;
        const dx = c[0] - origin[0];
        const dy = c[1] - origin[1];
        const length = Math.hypot(dx, dy);
        if (length < 0.0001) continue;
        const align = (dx * direction[0] + dy * direction[1]) / length;
        if (align < 0.55) continue;
        const score = length * (2.2 - align);
        if (score < bestScore) {
          bestScore = score;
          best = i;
        }
      }
      return best;
    };

    const goTo = (index) => {
      if (index < 0) return;
      state.target = index;
      state.speed = 0;
      state.idle = 0;
      wake();
    };

    /* Which tile is under the pointer: project every front tile the way the shader does
       and take the closest one within that tile's own on-screen reach. */
    const pick = (clientX, clientY) => {
      const rect = dom.canvas.getBoundingClientRect();
      const aspect = rect.width / rect.height;
      const span = Math.tan(FOV / 2);
      const x = (((clientX - rect.left) / rect.width) * 2 - 1) * aspect;
      const y = 1 - ((clientY - rect.top) / rect.height) * 2 - state.shift;
      const radius = tileRadius();
      let best = -1;
      let bestDistance = Infinity;
      for (let i = 0; i < state.tiles.length; i++) {
        const c = rotate(state.orientation, state.tiles[i]);
        if (c[2] < 0.2) continue;
        const depth = state.camera - c[2] * RADIUS;
        const sx = (c[0] * RADIUS) / (depth * span);
        const sy = (c[1] * RADIUS) / (depth * span);
        const reach = (radius * (0.5 + 0.5 * c[2])) / (depth * span);
        const distance = Math.hypot(sx - x, sy - y);
        if (distance < reach && distance < bestDistance) {
          bestDistance = distance;
          best = i;
        }
      }
      return best;
    };

    const report = () => {
      const index = items.length ? (state.assign[state.front] ?? 0) % items.length : 0;
      if (!state.moving && index !== shown) {
        shown = index;
        setActive(index);
      }
    };

    const frame = (now) => {
      raf = 0;
      if (!alive || !visible) return;
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60;
      last = now;
      layout();
      if (atlas.dirty) uploadAtlas();
      if (state.started) state.reveal = Math.min(1, state.reveal + dt / (reduce ? 0.01 : 0.7));
      const minSide = Math.min(state.width, state.height);

      if (drag.active) {
        if (drag.dx || drag.dy) {
          const length = Math.hypot(drag.dx, drag.dy);
          const angle = (length / minSide) * 2.6;
          const axis = normalize([drag.dy, drag.dx, 0]);
          state.orientation = quatNormalize(quatMultiply(quatAxisAngle(axis, angle), state.orientation));
          const blend = 1 - Math.exp(-dt * 30);
          state.axis = normalize(state.axis.map((value, i) => value + (axis[i] - value) * blend));
          state.speed += (angle / dt - state.speed) * blend;
          drag.dx = 0;
          drag.dy = 0;
        } else {
          state.speed *= Math.exp(-dt * 14);
        }
      } else {
        if (state.speed > 0.0005) {
          state.orientation = quatNormalize(
            quatMultiply(quatAxisAngle(state.axis, state.speed * dt), state.orientation)
          );
          state.speed *= Math.exp(-dt * (6 - 5 * settings.inertia));
        } else {
          state.speed = 0;
        }
        const goal = state.target >= 0 ? state.target : nearest();
        const current = rotate(state.orientation, state.tiles[goal]);
        const angle = Math.acos(clamp(dot(current, FRONT), -1, 1));
        const pull = clamp(1 - state.speed / 1.4, 0, 1);
        if (angle > 0.0002 && pull > 0) {
          const axis = cross(current, FRONT);
          const step = angle * (1 - Math.exp(-dt * (state.target >= 0 ? 6 : 8))) * pull;
          if (Math.hypot(axis[0], axis[1], axis[2]) > 1e-6) {
            state.orientation = quatNormalize(quatMultiply(quatAxisAngle(normalize(axis), step), state.orientation));
          }
        } else if (state.target >= 0 && state.speed === 0) {
          state.target = -1;
        }
        state.front = goal;
      }

      const settled = Math.acos(clamp(rotate(state.orientation, state.tiles[state.front])[2], -1, 1));
      const moving = drag.active || state.speed > 0.08 || settled > 0.01;
      if (moving !== state.moving) {
        state.moving = moving;
        dom.info.dataset.moving = String(moving);
        paintInfo();
      }
      state.settle += ((moving ? 0 : 1) - state.settle) * (1 - Math.exp(-dt * (moving ? 10 : 3)));
      if (!moving) report();

      const rest = restDistance();
      const pullAmount = clamp(drag.active ? 1 : state.speed / 2.2, 0, 1);
      const target =
        rest + settings.pullBack * pullAmount * RADIUS * 1.25 + (1 - state.reveal) * RADIUS * 1.5;
      if (!state.camera) state.camera = target + RADIUS;
      state.camera += (target - state.camera) * (1 - Math.exp(-dt * 4.5));

      if (settings.autoplay > 0 && !moving && !state.hovered && !state.focused && !reduce) {
        state.idle += dt;
        if (state.idle >= settings.autoplay) {
          state.idle = 0;
          goTo(neighbor(state.front, [1, 0]));
        }
      } else {
        state.idle = 0;
      }

      measure();
      const shiftGoal = state.layout === "stacked" ? Math.min(0.3, 72 / state.height) : 0;
      state.shift += (shiftGoal - state.shift) * (1 - Math.exp(-dt * 6));
      render();

      /* Idle means no frames. The point of the check is that a sphere nobody is touching
         and nothing is approaching costs nothing, and the autoplay timer is what wakes it
         again rather than a permanent animation loop. */
      const busy =
        moving ||
        Math.abs(target - state.camera) > 0.001 ||
        Math.abs(shiftGoal - state.shift) > 0.0005 ||
        Math.abs((moving ? 0 : 1) - state.settle) > 0.002 ||
        state.reveal < 1 ||
        atlas.dirty ||
        !state.started;
      if (busy) {
        raf = requestAnimationFrame(frame);
      } else {
        last = 0;
        if (settings.autoplay > 0 && !state.hovered && !state.focused && !reduce) {
          clearTimeout(timer);
          timer = window.setTimeout(wake, Math.max(16, (settings.autoplay - state.idle) * 1000));
        }
      }
    };

    const render = () => {
      const aspect = state.width / state.height;
      const f = 1 / Math.tan(FOV / 2);
      const near = 0.05;
      const far = 50;
      const projection = new Float32Array([
        f / aspect, 0, 0, 0,
        0, f, 0, 0,
        0, 0, (far + near) / (near - far), -1,
        0, 0, (2 * far * near) / (near - far), 0
      ]);
      context.viewport(0, 0, dom.canvas.width, dom.canvas.height);
      context.clearColor(0, 0, 0, 0);
      context.clear(context.COLOR_BUFFER_BIT | context.DEPTH_BUFFER_BIT);
      context.enable(context.DEPTH_TEST);
      context.enable(context.BLEND);
      context.blendFunc(context.ONE, context.ONE_MINUS_SRC_ALPHA);
      context.useProgram(program);
      const [qx, qy, qz, qw] = state.orientation;
      context.uniform4f(uniforms.uOrientation, qx, qy, qz, qw);
      context.uniformMatrix4fv(uniforms.uProjection, false, projection);
      context.uniform1f(uniforms.uCamera, state.camera);
      context.uniform1f(uniforms.uRadius, RADIUS);
      context.uniform1f(uniforms.uTileRadius, tileRadius());
      context.uniform4f(uniforms.uSpin, state.axis[0], state.axis[1], state.axis[2], reduce ? 0 : state.speed);
      context.uniform1f(uniforms.uStretch, settings.stretch);
      context.uniform1f(uniforms.uShift, state.shift);
      context.uniform1f(uniforms.uCells, atlas.cells);
      context.uniform1f(uniforms.uInset, 2 / atlas.cell);
      context.uniform1f(uniforms.uRoundness, settings.roundness);
      context.uniform1f(uniforms.uGrayscale, settings.grayscale ? 1 : 0);
      context.uniform1f(uniforms.uDim, settings.dim * (0.45 + 0.55 * state.settle));
      context.uniform1f(uniforms.uReveal, state.reveal);
      if (settings.theme === "light") {
        context.uniform3f(uniforms.uPlaceholder, 0.9, 0.9, 0.91);
        context.uniform3f(uniforms.uTone, 1, 1, 1);
      } else {
        context.uniform3f(uniforms.uPlaceholder, 0.13, 0.12, 0.15);
        context.uniform3f(uniforms.uTone, 0.04, 0.035, 0.05);
      }
      context.activeTexture(context.TEXTURE0);
      context.bindTexture(context.TEXTURE_2D, atlasTexture);
      context.bindVertexArray(vao);
      context.drawElementsInstanced(
        context.TRIANGLES, indices.length, context.UNSIGNED_SHORT, 0, state.tiles.length
      );
      context.bindVertexArray(null);
    };

    const wake = () => {
      clearTimeout(timer);
      if (raf || !alive || !visible) return;
      raf = requestAnimationFrame(frame);
    };

    const onPointerDown = (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      dom.canvas.setPointerCapture?.(event.pointerId);
      drag.active = true;
      drag.id = event.pointerId;
      drag.x = event.clientX;
      drag.y = event.clientY;
      drag.dx = 0;
      drag.dy = 0;
      drag.moved = 0;
      drag.start = performance.now();
      state.target = -1;
      wake();
    };

    const onPointerMove = (event) => {
      if (!drag.active || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      drag.x = event.clientX;
      drag.y = event.clientY;
      drag.dx += dx;
      drag.dy += dy;
      drag.moved += Math.hypot(dx, dy);
      wake();
    };

    /* A press that did not turn into a drag is a click: on the front tile it activates the
       item, on any other tile it steps the sphere round to it. */
    const onPointerUp = (event) => {
      if (!drag.active || event.pointerId !== drag.id) return;
      drag.active = false;
      dom.canvas.releasePointerCapture?.(event.pointerId);
      if (drag.moved < 6 && performance.now() - drag.start < 450) {
        const index = pick(event.clientX, event.clientY);
        if (index >= 0 && items.length) {
          const itemIndex = (state.assign[index] ?? 0) % items.length;
          if (index === state.front) emit(items[itemIndex], itemIndex);
          else goTo(index);
        }
      }
      wake();
    };

    /* A key aimed at a control belongs to that control, and a key held with a modifier
       belongs to the browser — the same guard the feed's own key handler uses. A link needs
       no entry here: ``Enter`` is only taken from the body or from the sphere itself, so the
       key that activates a focused link is left to the link. */
    const onAControl = (target) =>
      target instanceof HTMLButtonElement ||
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement ||
      (target instanceof HTMLElement && target.isContentEditable);

    /* Bound to the document rather than to the sphere, because this component is the whole
       of the page that builds it. A reader lands on the front door and presses an arrow
       without having clicked or tabbed into anything, and a handler on the sphere's own
       element answers nothing until that element has focus, which nothing gives it on
       arrival — so the page's own line, "Arrow keys step", would be a promise it breaks on
       the way in. Answering the arrows here is also what takes them away from scrolling
       this page, which is the trade the sphere being the page's subject buys: the page
       scrolls with Space, PageUp and PageDown still. */
    const onKeyDown = (event) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (onAControl(event.target)) return;
      const directions = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
      if (directions[event.key]) {
        event.preventDefault();
        goTo(neighbor(state.target >= 0 ? state.target : state.front, directions[event.key]));
      } else if (
        event.key === "Enter" &&
        (event.target === root || event.target === document.body) &&
        items.length
      ) {
        event.preventDefault();
        emit(items[(state.assign[state.front] ?? 0) % items.length], active);
      }
    };

    const onEnter = () => { state.hovered = true; };
    const onLeave = () => { state.hovered = false; wake(); };
    const onFocusIn = () => { state.focused = true; };
    const onFocusOut = (event) => {
      if (root.contains(event.relatedTarget)) return;
      state.focused = false;
      wake();
    };

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      state.width = Math.max(1, root.clientWidth);
      state.height = Math.max(1, root.clientHeight);
      dom.canvas.width = Math.round(state.width * dpr);
      dom.canvas.height = Math.round(state.height * dpr);
      if (!raf) render();
      wake();
    };

    dom.canvas.addEventListener("pointerdown", onPointerDown);
    dom.canvas.addEventListener("pointermove", onPointerMove);
    dom.canvas.addEventListener("pointerup", onPointerUp);
    dom.canvas.addEventListener("pointercancel", onPointerUp);
    document.addEventListener("keydown", onKeyDown);
    root.addEventListener("pointerenter", onEnter);
    root.addEventListener("pointerleave", onLeave);
    root.addEventListener("focusin", onFocusIn);
    root.addEventListener("focusout", onFocusOut);
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(root);
    /* Off screen means stop: a sphere nobody can see should not hold a frame budget. */
    const visibility = new IntersectionObserver((entries) => {
      visible = entries.some((entry) => entry.isIntersecting);
      if (visible) {
        last = 0;
        wake();
      }
    });
    visibility.observe(root);
    layout();
    resize();
    loadItems();

    return {
      wake,
      setItems: loadItems,
      destroy() {
        alive = false;
        cancelAnimationFrame(raf);
        clearTimeout(timer);
        dom.canvas.removeEventListener("pointerdown", onPointerDown);
        dom.canvas.removeEventListener("pointermove", onPointerMove);
        dom.canvas.removeEventListener("pointerup", onPointerUp);
        dom.canvas.removeEventListener("pointercancel", onPointerUp);
        document.removeEventListener("keydown", onKeyDown);
        root.removeEventListener("pointerenter", onEnter);
        root.removeEventListener("pointerleave", onLeave);
        root.removeEventListener("focusin", onFocusIn);
        root.removeEventListener("focusout", onFocusOut);
        resizeObserver.disconnect();
        visibility.disconnect();
        context.deleteTexture(atlasTexture);
        context.deleteBuffer(cornerBuffer);
        context.deleteBuffer(indexBuffer);
        context.deleteBuffer(tileBuffer);
        context.deleteVertexArray(vao);
        context.deleteProgram(program);
        context.deleteShader(vertex);
        context.deleteShader(fragment);
      }
    };
  }

  if (gl) engine = build(gl);
  else buildFallback();
  paintInfo();

  /* Watched, not sampled once: the sphere is mid-flight at the moment a reader turns the
     setting on, and it should stop then rather than at the next reload. */
  const stopWatchingMotion = watchReducedMotion((matches) => {
    reduce = matches;
    if (engine) engine.wake();
  });

  return {
    element: root,
    fallback: !engine,
    set: (next = {}) => {
      apply(next);
      if (engine) engine.wake();
      paintInfo();
    },
    setItems: (next) => {
      items.length = 0;
      items.push(...(next || []));
      active = 0;
      shown = -1;
      infoKey = "";
      if (engine) {
        engine.setItems();
      } else {
        if (dom.list) dom.list.remove();
        buildFallback();
      }
      paintInfo();
    },
    active: () => active,
    wake: () => { if (engine) engine.wake(); },
    destroy: () => {
      stopWatchingMotion();
      if (engine) engine.destroy();
      root.remove();
    }
  };
}
