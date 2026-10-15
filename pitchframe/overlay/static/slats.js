/* Pitchframe overlay — the sea behind the page.
 *
 * A port of the MicroSlats component (React Bits, JavaScript + CSS) to a module with no
 * framework. The React parts of that component were only a lifecycle — a container ref,
 * an effect that owns the WebGL scene, and an effect that pushes new props into it — and
 * that is exactly what `createSlats(container, options)` returns: a mount, a `set()`, and
 * a `destroy()`. Everything below the exports is the component's own code and shaders.
 *
 * `ogl` is vendored next to it (see `vendor/ogl.mjs`) because this project has no package
 * manager and no build step; the import below is a relative path to a file that ships with
 * the page, so the overlay keeps working with no network.
 *
 * Three things are added for this page rather than for the effect:
 *
 *   Colour is read from the palette in styles.css. The sea is scenery, and the stylesheet
 *   reserves green and vermilion for published and caught — reading the tokens keeps the
 *   scenery out of the verdicts by construction instead of by remembering.
 *
 *   `prefers-reduced-motion` is watched, not sampled once at load. This page already stops
 *   its own animation for that setting, and a reader who turns it on while the sea is
 *   rolling should not have to reload to stop the water.
 *
 *   `set()` merges instead of replacing. Upstream a prop left out reverts to the preset,
 *   which is right for React re-renders and wrong for an imperative call: `set({speed: 0})`
 *   must pause the sea, not also reset the slat size. A `preset` change still re-derives
 *   every prop the caller has not named.
 *
 * The canvas is a background, so it is inert — `pointer-events: none` in the stylesheet.
 * The fluid still follows the cursor, because the component listens on `window` rather
 * than on the canvas. If WebGL2 is unavailable the container stays empty and the page is
 * exactly what it was; this module is the only thing that can fail, and it loads separately
 * from the replay so that it cannot take the replay down with it.
 */

import { Renderer, Program, Mesh, Triangle, RenderTarget, Texture } from "./vendor/ogl.mjs";

/* The four looks the component ships, unchanged. Signal flattens the field into an
   equalizer wall; the other three are sea states. */
const PRESETS = {
  swell: {
    scale: 1.5, speed: 0.6, direction: 250, chop: 0.55, stretch: 0,
    glint: 0.7, contrast: 1.25, perspective: 0.55, fog: 0.55
  },
  tide: {
    scale: 1.3, speed: 0.5, direction: 262, chop: 0.2, stretch: 0.12,
    glint: 0.45, contrast: 1.1, perspective: 0.5, fog: 0.4
  },
  storm: {
    scale: 0.55, speed: 1.6, direction: 236, chop: 1, stretch: 0.3,
    glint: 1.3, contrast: 1.8, perspective: 0.8, fog: 0.35
  },
  signal: {
    scale: 0.85, speed: 1.2, direction: 180, chop: 0.6, stretch: 0.85,
    glint: 0.25, contrast: 1.2, perspective: 0, fog: 0
  }
};

const FLUID_SIZE = 96;
const PRESSURE_STEPS = 16;
const SPLAT_FORCE = 6900;
const SPLASH_JETS = 3;

/* Upper bound on the pixels the slat pass is allowed to shade. The sea is background, so
   it yields resolution to whatever else is on the page rather than the other way round. */
const PIXEL_BUDGET = 4.5e6;

/* What the page asks for unless something else is set. Kept here, the way app.js keeps its
   constants at the top, so the tuning is in one place instead of scattered through markup. */
const BACKGROUND = {
  preset: "tide",
  slatWidth: 9,
  slatHeight: 26,
  gap: 4,
  roundness: 0.9,
  speed: 0.4,
  chop: 0.24,
  stretch: 0.14,
  /* Crests kept calm on purpose. The brightest thing on this page has to be the claim
     being checked, so the sea is tuned to sit under the cards rather than beside them. */
  glint: 0.28,
  contrast: 1,
  perspective: 0.6,
  fog: 0.7,
  lean: 0.35,
  swirl: 0.3,
  trail: 1.8,
  cursorStrength: 0.9,
  cursorSize: 54,
  /* Behind the cards, not behind a black rectangle: the slats are opaque and everything
     between them shows the page's own night through. */
  backgroundColor: "transparent",
  interactive: true,
  intro: true
};

const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

/* Any CSS colour to [r, g, b, a], through the browser's own parser rather than a table of
   hex digits, so a token in styles.css can be a name, a hex, or an rgb() and still work. */
const parseColor = (value, fallback) => {
  try {
    const ctx = document.createElement("canvas").getContext("2d");
    if (!ctx) return fallback;
    ctx.fillStyle = "#000000";
    ctx.fillStyle = value;
    const resolved = ctx.fillStyle;
    if (resolved.startsWith("#")) {
      const n = parseInt(resolved.slice(1), 16);
      return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255, 1];
    }
    const parts = resolved.match(/[\d.]+/g);
    if (!parts || parts.length < 3) return fallback;
    return [
      Number(parts[0]) / 255,
      Number(parts[1]) / 255,
      Number(parts[2]) / 255,
      parts[3] ? Number(parts[3]) : 1
    ];
  } catch {
    return fallback;
  }
};

const passVertex = `#version 300 es
in vec2 position;
void main() {
  gl_Position = vec4(position, 0.0, 1.0);
}
`;

const fluidVertex = `#version 300 es
in vec2 position;
uniform vec2 uTexel;
out vec2 vUv;
out vec2 vL;
out vec2 vR;
out vec2 vT;
out vec2 vB;
void main() {
  vUv = position * 0.5 + 0.5;
  vL = vUv - vec2(uTexel.x, 0.0);
  vR = vUv + vec2(uTexel.x, 0.0);
  vT = vUv + vec2(0.0, uTexel.y);
  vB = vUv - vec2(0.0, uTexel.y);
  gl_Position = vec4(position, 0.0, 1.0);
}
`;

const fluidHead = `#version 300 es
precision highp float;
in vec2 vUv;
in vec2 vL;
in vec2 vR;
in vec2 vT;
in vec2 vB;
out vec4 fragColor;
`;

const splatFragment = `${fluidHead}
uniform sampler2D uTarget;
uniform float uAspect;
uniform vec2 uPoint;
uniform vec3 uValue;
uniform float uRadius;
void main() {
  vec2 offset = vUv - uPoint;
  offset.x *= uAspect;
  float falloff = exp(-dot(offset, offset) / uRadius);
  fragColor = vec4(texture(uTarget, vUv).xyz + uValue * falloff, 1.0);
}
`;

const curlFragment = `${fluidHead}
uniform sampler2D uVelocity;
void main() {
  float left = texture(uVelocity, vL).y;
  float right = texture(uVelocity, vR).y;
  float top = texture(uVelocity, vT).x;
  float bottom = texture(uVelocity, vB).x;
  fragColor = vec4(0.5 * (right - left - top + bottom), 0.0, 0.0, 1.0);
}
`;

const vorticityFragment = `${fluidHead}
uniform sampler2D uVelocity;
uniform sampler2D uCurl;
uniform float uSwirl;
uniform float uDt;
void main() {
  float left = texture(uCurl, vL).x;
  float right = texture(uCurl, vR).x;
  float top = texture(uCurl, vT).x;
  float bottom = texture(uCurl, vB).x;
  float middle = texture(uCurl, vUv).x;
  vec2 force = 0.5 * vec2(abs(top) - abs(bottom), abs(right) - abs(left));
  force = force / (length(force) + 0.0001) * uSwirl * middle;
  force.y = -force.y;
  vec2 velocity = texture(uVelocity, vUv).xy + force * uDt;
  fragColor = vec4(clamp(velocity, -1000.0, 1000.0), 0.0, 1.0);
}
`;

const divergenceFragment = `${fluidHead}
uniform sampler2D uVelocity;
void main() {
  vec2 middle = texture(uVelocity, vUv).xy;
  float left = vL.x < 0.0 ? -middle.x : texture(uVelocity, vL).x;
  float right = vR.x > 1.0 ? -middle.x : texture(uVelocity, vR).x;
  float top = vT.y > 1.0 ? -middle.y : texture(uVelocity, vT).y;
  float bottom = vB.y < 0.0 ? -middle.y : texture(uVelocity, vB).y;
  fragColor = vec4(0.5 * (right - left + top - bottom), 0.0, 0.0, 1.0);
}
`;

const pressureFragment = `${fluidHead}
uniform sampler2D uPressure;
uniform sampler2D uDivergence;
void main() {
  float left = texture(uPressure, vL).x;
  float right = texture(uPressure, vR).x;
  float top = texture(uPressure, vT).x;
  float bottom = texture(uPressure, vB).x;
  float divergence = texture(uDivergence, vUv).x;
  fragColor = vec4((left + right + top + bottom - divergence) * 0.25, 0.0, 0.0, 1.0);
}
`;

const projectFragment = `${fluidHead}
uniform sampler2D uPressure;
uniform sampler2D uVelocity;
void main() {
  float left = texture(uPressure, vL).x;
  float right = texture(uPressure, vR).x;
  float top = texture(uPressure, vT).x;
  float bottom = texture(uPressure, vB).x;
  vec2 velocity = texture(uVelocity, vUv).xy - vec2(right - left, top - bottom);
  fragColor = vec4(velocity, 0.0, 1.0);
}
`;

const advectFragment = `${fluidHead}
uniform sampler2D uVelocity;
uniform sampler2D uSource;
uniform vec2 uTexel;
uniform float uDt;
uniform float uFade;
void main() {
  vec2 from = vUv - uDt * texture(uVelocity, vUv).xy * uTexel;
  fragColor = texture(uSource, from) / (1.0 + uFade * uDt);
}
`;

const scaleFragment = `${fluidHead}
uniform sampler2D uSource;
uniform float uValue;
void main() {
  fragColor = texture(uSource, vUv) * uValue;
}
`;

const fieldFragment = `#version 300 es
precision highp float;
uniform vec2 uSize;
uniform vec4 uGrid;
uniform vec2 uOrigin;
uniform vec2 uSlat;
uniform float uTime;
uniform float uScale;
uniform float uDirection;
uniform float uChop;
uniform float uStretch;
uniform float uGlint;
uniform float uContrast;
uniform float uPerspective;
uniform float uFog;
uniform float uIntro;
uniform sampler2D tVelocity;
uniform sampler2D tInk;
uniform vec2 uFluidTexel;
uniform float uFluid;
uniform float uInk;
uniform float uLean;
out vec4 fragColor;

const float TURN[4] = float[4](0.0, -0.95, 0.78, 1.62);
const float LENGTH[4] = float[4](2.6, 1.7, 1.12, 0.76);
const float HEIGHT[4] = float[4](1.0, 0.75, 0.5, 0.3);
const float SHIFT[4] = float[4](0.0, 2.3, 4.1, 1.1);

void main() {
  vec2 cell = floor(gl_FragCoord.xy);
  float row = uGrid.y - 1.0 - cell.y;
  vec2 center = uOrigin + vec2(cell.x, row) * uGrid.zw + uSlat * 0.5;
  float v = clamp(center.y / uSize.y, 0.0, 1.0);

  vec2 flow = vec2(0.0);
  float ink = 0.0;
  if (uFluid > 0.5) {
    vec2 fluidUv = clamp(vec2(center.x / uSize.x, 1.0 - center.y / uSize.y), 0.0, 1.0);
    flow = texture(tVelocity, fluidUv).xy * uFluidTexel * uSize;
    ink = texture(tInk, fluidUv).x;
  }

  float horizon = mix(8.0, 0.5, uPerspective);
  float depth = (1.0 + horizon) / (v + horizon);
  vec2 world = vec2((center.x - uSize.x * 0.5) / uSize.y * depth, (depth - 1.0) * horizon * 2.2);
  world -= flow * 0.00018 * depth;
  world /= max(uScale, 0.05);

  float meander = 0.55 * sin(dot(world, vec2(0.23, 0.41)) * 0.9 + uTime * 0.13)
    + 0.3 * sin(dot(world, vec2(-0.37, 0.19)) * 1.4 - uTime * 0.09);

  float steep = uChop * 0.45;
  float height = 0.0;
  float total = 0.0;
  for (int i = 0; i < 4; i++) {
    float angle = uDirection + TURN[i];
    vec2 heading = vec2(cos(angle), sin(angle));
    float k = 6.2831853 / LENGTH[i];
    float omega = sqrt(9.81 * k) * 0.35;
    float theta = k * dot(world, heading) - omega * uTime + SHIFT[i] + meander * (0.6 + 0.3 * float(i));
    height += HEIGHT[i] * (cos(theta) + steep * cos(2.0 * theta));
    total += HEIGHT[i] * (1.0 + steep);
  }
  float level = clamp(0.5 + 0.5 * height / (total * 0.85), 0.0, 1.0);
  float crest = smoothstep(0.6, 1.0, level);
  float glint = crest * crest * uGlint;

  float haze = mix(1.0, 1.0 - uFog, pow(1.0 - v, 1.4));
  float light = (pow(level, uContrast) + glint * 0.8) * haze;
  float glow = 1.0 - exp(-max(ink, 0.0) * uInk * 1.6);
  light = 1.0 - (1.0 - clamp(light, 0.0, 1.0)) * (1.0 - glow);

  float front = uIntro * 1.35;
  float reveal = 1.0 - smoothstep(front - 0.35, front, v);
  float sweep = exp(-pow((v - front + 0.22) / 0.07, 2.0)) * (1.0 - uIntro);
  light = (light + sweep * 0.8) * reveal;
  light = max(light, 0.045 * reveal);
  float span = mix(1.0 - uStretch, 1.0, level) + glow * 0.3;
  span = clamp(span, 0.08, 1.0) * mix(0.1, 1.0, reveal);
  float tint = clamp(max(glint * 1.4, glow * 0.9) + sweep, 0.0, 1.0);
  float lean = clamp(flow.x / 1400.0, -1.0, 1.0) * uLean * reveal;

  fragColor = vec4(clamp(light, 0.0, 1.0), span, tint, 0.5 + 0.5 * lean);
}
`;

const slatFragment = `#version 300 es
precision highp float;
uniform sampler2D tField;
uniform vec2 uSize;
uniform float uDpr;
uniform vec4 uGrid;
uniform vec2 uOrigin;
uniform vec2 uSlat;
uniform float uRound;
uniform float uTilt;
uniform vec3 uColor;
uniform vec3 uGlintColor;
uniform vec4 uBackground;
out vec4 fragColor;

float pill(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

void main() {
  vec2 p = vec2(gl_FragCoord.x, uSize.y * uDpr - gl_FragCoord.y) / uDpr;
  vec2 local = p - uOrigin;
  vec2 cell = floor(local / uGrid.zw);
  vec4 background = vec4(uBackground.rgb * uBackground.a, uBackground.a);
  vec4 slat = vec4(0.0);
  int reach = uTilt > 0.0 ? 1 : 0;
  for (int dx = -1; dx <= 1; dx++) {
    if (abs(dx) > reach) continue;
    vec2 neighbour = cell + vec2(float(dx), 0.0);
    if (neighbour.x < 0.0 || neighbour.y < 0.0 || neighbour.x >= uGrid.x || neighbour.y >= uGrid.y) continue;
    vec4 field = texelFetch(tField, ivec2(int(neighbour.x), int(uGrid.y - 1.0 - neighbour.y)), 0);
    vec2 offset = local - neighbour * uGrid.zw - uSlat * 0.5;
    float angle = (field.a - 0.5) * 2.0 * uTilt;
    float c = cos(angle);
    float s = sin(angle);
    vec2 turned = vec2(c * offset.x + s * offset.y, c * offset.y - s * offset.x);
    vec2 halfSize = vec2(uSlat.x * 0.5, uSlat.y * 0.5 * field.g);
    float radius = uRound * min(halfSize.x, halfSize.y);
    float alpha = clamp(0.5 - pill(turned, halfSize, radius) * uDpr, 0.0, 1.0) * field.r;
    if (alpha > slat.a) slat = vec4(mix(uColor, uGlintColor, field.b) * alpha, alpha);
  }
  fragColor = slat + background * (1.0 - slat.a);
}
`;

/**
 * Put the sea inside `container`.
 *
 * Returns a handle — `set(options)` for live changes, `destroy()` to take it away — and a
 * handle that does nothing at all when the browser has no WebGL2, so a caller never has to
 * branch on the environment.
 */
export function createSlats(container, options = {}) {
  /* What the caller has named, kept separately from the resolved values so that a later
     `preset` change re-derives everything the caller has *not* named. */
  const named = {};
  /* Every value the frame loop reads, resolved. Flat by design: the loop runs 60 times a
     second and should not be walking a fallback chain. */
  const settings = { pixelBudget: PIXEL_BUDGET };

  let wake = null;
  let teardown = null;

  const palette = getComputedStyle(document.documentElement);
  const token = (name, fallback) => palette.getPropertyValue(name).trim() || fallback;

  function applyOptions(next = {}) {
    Object.assign(named, next);
    const base = PRESETS[named.preset] || PRESETS.swell;
    const value = (key) => (named[key] === undefined || named[key] === null ? base[key] : named[key]);

    settings.slatWidth = value("slatWidth") ?? 10;
    settings.slatHeight = value("slatHeight") ?? 25;
    settings.gap = value("gap") ?? 3;
    settings.roundness = value("roundness") ?? 0.75;
    settings.scale = value("scale");
    settings.speed = value("speed") ?? 0;
    settings.direction = value("direction");
    settings.chop = value("chop");
    settings.stretch = value("stretch");
    settings.glint = value("glint");
    settings.contrast = value("contrast");
    settings.perspective = value("perspective");
    settings.fog = value("fog");
    settings.interactive = value("interactive") ?? true;
    settings.cursorStrength = value("cursorStrength") ?? 1;
    settings.cursorSize = value("cursorSize") ?? 40;
    settings.swirl = value("swirl") ?? 0;
    settings.trail = value("trail") ?? 1.4;
    settings.lean = value("lean") ?? 0;
    settings.intro = value("intro") ?? true;
    settings.introDuration = value("introDuration") ?? 1.5;
    settings.paused = value("paused") ?? false;
    settings.pixelBudget = value("pixelBudget") ?? PIXEL_BUDGET;

    settings.color = parseColor(
      value("color") ?? token("--slats", "#6E4BD0"),
      [0.43, 0.29, 0.82, 1]
    );
    settings.glintColor = parseColor(
      value("glintColor") ?? token("--slats-glint", "#D9E2FF"),
      [1, 1, 1, 1]
    );
    settings.backgroundColor = parseColor(value("backgroundColor") ?? "#000000", [0, 0, 0, 1]);
  }

  applyOptions(options);

  const handle = {
    set(next = {}) {
      applyOptions(next);
      if (wake) wake();
    },
    destroy() {
      if (teardown) teardown();
      teardown = null;
      wake = null;
    }
  };

  const renderer = new Renderer({ alpha: true, premultipliedAlpha: true, antialias: false, depth: false });
  const gl = renderer.gl;

  /* No WebGL2 means no sea. Nothing is drawn, nothing is reported, and the page keeps the
     plain night it had before: a background is not worth an error message. */
  if (!renderer.isWebgl2) {
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return handle;
  }

  gl.clearColor(0, 0, 0, 0);
  const canvas = gl.canvas;
  canvas.style.display = "block";
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  /* Decorative: the replay beside it is the content, and a screen reader has no use for a
     moving picture of water. */
  canvas.setAttribute("aria-hidden", "true");
  container.appendChild(canvas);

  const geometry = new Triangle(gl);
  const blank = new Texture(gl);
  const floatTargets = !!(
    gl.getExtension("EXT_color_buffer_float") || gl.getExtension("EXT_color_buffer_half_float")
  );

  const dispose = (target) => {
    gl.deleteFramebuffer(target.buffer);
    gl.deleteTexture(target.texture.texture);
  };

  let fieldTarget = new RenderTarget(gl, {
    width: 1,
    height: 1,
    depth: false,
    minFilter: gl.NEAREST,
    magFilter: gl.NEAREST
  });

  const fieldUniforms = {
    uSize: { value: [1, 1] },
    uGrid: { value: [1, 1, 1, 1] },
    uOrigin: { value: [0, 0] },
    uSlat: { value: [1, 1] },
    uTime: { value: 0 },
    uScale: { value: 1 },
    uDirection: { value: 0 },
    uChop: { value: 0.5 },
    uStretch: { value: 0.3 },
    uGlint: { value: 1 },
    uContrast: { value: 1 },
    uPerspective: { value: 0.7 },
    uFog: { value: 0.5 },
    uIntro: { value: 0 },
    tVelocity: { value: blank },
    tInk: { value: blank },
    uFluidTexel: { value: [1, 1] },
    uFluid: { value: 0 },
    uInk: { value: 1 },
    uLean: { value: 0.5 }
  };
  const fieldMesh = new Mesh(gl, {
    geometry,
    program: new Program(gl, {
      vertex: passVertex,
      fragment: fieldFragment,
      uniforms: fieldUniforms,
      depthTest: false,
      depthWrite: false
    })
  });

  const slatUniforms = {
    tField: { value: fieldTarget.texture },
    uSize: { value: [1, 1] },
    uDpr: { value: 1 },
    uGrid: { value: [1, 1, 1, 1] },
    uOrigin: { value: [0, 0] },
    uSlat: { value: [1, 1] },
    uRound: { value: 1 },
    uTilt: { value: 0 },
    uColor: { value: [1, 1, 1] },
    uGlintColor: { value: [1, 1, 1] },
    uBackground: { value: [0, 0, 0, 1] }
  };
  const slatMesh = new Mesh(gl, {
    geometry,
    program: new Program(gl, {
      vertex: passVertex,
      fragment: slatFragment,
      uniforms: slatUniforms,
      depthTest: false,
      depthWrite: false
    })
  });

  const fluidTexel = { value: [1, 1] };
  const fluidPass = (fragment, uniforms) =>
    new Mesh(gl, {
      geometry,
      program: new Program(gl, {
        vertex: fluidVertex,
        fragment,
        uniforms: { uTexel: fluidTexel, ...uniforms },
        depthTest: false,
        depthWrite: false
      })
    });
  const splatPass = fluidPass(splatFragment, {
    uTarget: { value: blank },
    uAspect: { value: 1 },
    uPoint: { value: [0, 0] },
    uValue: { value: [0, 0, 0] },
    uRadius: { value: 0.01 }
  });
  const curlPass = fluidPass(curlFragment, { uVelocity: { value: blank } });
  const vorticityPass = fluidPass(vorticityFragment, {
    uVelocity: { value: blank },
    uCurl: { value: blank },
    uSwirl: { value: 20 },
    uDt: { value: 0.016 }
  });
  const divergencePass = fluidPass(divergenceFragment, { uVelocity: { value: blank } });
  const pressurePass = fluidPass(pressureFragment, { uPressure: { value: blank }, uDivergence: { value: blank } });
  const projectPass = fluidPass(projectFragment, { uPressure: { value: blank }, uVelocity: { value: blank } });
  const advectPass = fluidPass(advectFragment, {
    uVelocity: { value: blank },
    uSource: { value: blank },
    uDt: { value: 0.016 },
    uFade: { value: 1 }
  });
  const scalePass = fluidPass(scaleFragment, { uSource: { value: blank }, uValue: { value: 0 } });

  /* Watched rather than sampled: the setting can change while the page is open, and this
     page honours it for its own animation too. */
  const motion = window.matchMedia?.("(prefers-reduced-motion: reduce)") ?? null;
  let reducedMotion = motion ? motion.matches : false;
  const onMotionChange = (event) => {
    reducedMotion = event.matches;
    start();
  };
  motion?.addEventListener("change", onMotionChange);

  let width = 1;
  let height = 1;
  let raf = 0;
  let last = performance.now();
  let time = 0;
  let introClock = 0;
  let visible = true;
  let alive = true;
  let fluid = null;
  let fluidUntil = 0;
  let fluidDirty = false;
  let splashTurn = 0;
  const splats = [];
  const pointer = { x: 0, y: 0, tracked: false };

  const run = (mesh, target) => renderer.render({ scene: mesh, target, clear: false });

  const floatTarget = (w, h) =>
    new RenderTarget(gl, {
      width: w,
      height: h,
      depth: false,
      type: gl.HALF_FLOAT,
      format: gl.RGBA,
      internalFormat: gl.RGBA16F,
      minFilter: gl.LINEAR,
      magFilter: gl.LINEAR
    });

  const swapPair = (w, h) => ({
    read: floatTarget(w, h),
    write: floatTarget(w, h),
    swap() {
      const next = this.read;
      this.read = this.write;
      this.write = next;
    }
  });

  const disposeFluid = () => {
    if (!fluid) return;
    [fluid.velocity, fluid.ink, fluid.pressure].forEach((pair) => {
      dispose(pair.read);
      dispose(pair.write);
    });
    dispose(fluid.divergence);
    dispose(fluid.curl);
    fluid = null;
  };

  const buildFluid = () => {
    if (!floatTargets) return;
    const aspect = width / height;
    const w = Math.max(8, Math.round(aspect >= 1 ? FLUID_SIZE * aspect : FLUID_SIZE));
    const h = Math.max(8, Math.round(aspect >= 1 ? FLUID_SIZE : FLUID_SIZE / aspect));
    if (fluid && fluid.width === w && fluid.height === h) return;
    disposeFluid();
    fluid = {
      width: w,
      height: h,
      velocity: swapPair(w, h),
      ink: swapPair(w, h),
      pressure: swapPair(w, h),
      divergence: floatTarget(w, h),
      curl: floatTarget(w, h)
    };
    fluidTexel.value = [1 / w, 1 / h];
  };

  const resize = () => {
    width = Math.max(1, container.clientWidth);
    height = Math.max(1, container.clientHeight);
    renderer.dpr = Math.min(
      window.devicePixelRatio || 1,
      2,
      Math.sqrt(settings.pixelBudget / (width * height))
    );
    renderer.setSize(width, height);
    buildFluid();
    start();
  };

  const layout = (s) => {
    const slatW = clamp(s.slatWidth, 1, 64);
    const slatH = clamp(s.slatHeight, 2, 240);
    const gapSize = clamp(s.gap, 0, 64);
    const pitchX = slatW + gapSize;
    const pitchY = slatH + gapSize;
    const cols = Math.min(2048, Math.ceil((width + gapSize) / pitchX) + 1);
    const rows = Math.min(2048, Math.ceil((height + gapSize) / pitchY) + 1);
    const originX = (width - (cols * pitchX - gapSize)) / 2;
    const originY = (height - (rows * pitchY - gapSize)) / 2;
    if (fieldTarget.width !== cols || fieldTarget.height !== rows) {
      dispose(fieldTarget);
      fieldTarget = new RenderTarget(gl, {
        width: cols,
        height: rows,
        depth: false,
        minFilter: gl.NEAREST,
        magFilter: gl.NEAREST
      });
      slatUniforms.tField.value = fieldTarget.texture;
    }
    const tilt = Math.min(0.42, Math.asin(clamp((pitchX * 1.5 - slatW * 0.5) / (slatH * 0.5), 0, 1)));
    return { slatW, slatH, tilt, grid: [cols, rows, pitchX, pitchY], origin: [originX, originY] };
  };

  const splat = (pair, point, value, radius) => {
    const u = splatPass.program.uniforms;
    u.uTarget.value = pair.read.texture;
    u.uAspect.value = width / height;
    u.uPoint.value = point;
    u.uValue.value = value;
    u.uRadius.value = radius;
    run(splatPass, pair.write);
    pair.swap();
  };

  const stepFluid = (f, dt, s) => {
    if (s.swirl > 0) {
      curlPass.program.uniforms.uVelocity.value = f.velocity.read.texture;
      run(curlPass, f.curl);

      const vorticity = vorticityPass.program.uniforms;
      vorticity.uVelocity.value = f.velocity.read.texture;
      vorticity.uCurl.value = f.curl.texture;
      vorticity.uSwirl.value = clamp(s.swirl, 0, 2) * 40;
      vorticity.uDt.value = dt;
      run(vorticityPass, f.velocity.write);
      f.velocity.swap();
    }

    divergencePass.program.uniforms.uVelocity.value = f.velocity.read.texture;
    run(divergencePass, f.divergence);

    scalePass.program.uniforms.uSource.value = f.pressure.read.texture;
    scalePass.program.uniforms.uValue.value = 0.8;
    run(scalePass, f.pressure.write);
    f.pressure.swap();

    pressurePass.program.uniforms.uDivergence.value = f.divergence.texture;
    for (let i = 0; i < PRESSURE_STEPS; i++) {
      pressurePass.program.uniforms.uPressure.value = f.pressure.read.texture;
      run(pressurePass, f.pressure.write);
      f.pressure.swap();
    }

    projectPass.program.uniforms.uPressure.value = f.pressure.read.texture;
    projectPass.program.uniforms.uVelocity.value = f.velocity.read.texture;
    run(projectPass, f.velocity.write);
    f.velocity.swap();

    const fade = 1 / clamp(s.trail, 0.1, 10);
    const advect = advectPass.program.uniforms;
    advect.uDt.value = dt;
    advect.uVelocity.value = f.velocity.read.texture;
    advect.uSource.value = f.velocity.read.texture;
    advect.uFade.value = fade * 1.4;
    run(advectPass, f.velocity.write);
    f.velocity.swap();

    advect.uVelocity.value = f.velocity.read.texture;
    advect.uSource.value = f.ink.read.texture;
    advect.uFade.value = fade;
    run(advectPass, f.ink.write);
    f.ink.swap();
  };

  const clearFluid = (f) => {
    scalePass.program.uniforms.uValue.value = 0;
    [f.velocity, f.ink, f.pressure].forEach((pair) => {
      scalePass.program.uniforms.uSource.value = pair.read.texture;
      run(scalePass, pair.write);
      pair.swap();
    });
  };

  const frame = (now) => {
    raf = 0;
    if (!alive) return;
    const s = settings;
    const dt = Math.min(0.05, Math.max(0, (now - last) / 1000));
    last = now;

    if (!s.paused && !reducedMotion) time += dt * s.speed;
    introClock = s.intro && !reducedMotion ? introClock + dt / Math.max(0.2, s.introDuration) : 1;
    const introLinear = Math.min(introClock, 1);
    const introEase =
      introLinear < 0.5 ? 4 * introLinear * introLinear * introLinear : 1 - Math.pow(-2 * introLinear + 2, 3) / 2;

    if (fluid && splats.length) {
      const radius = Math.pow(clamp(s.cursorSize, 4, 600) / height, 2) * 0.35;
      const aspect = width / height;
      for (const [u, v, du, dv, turn] of splats) {
        if (turn === null) {
          splat(fluid.velocity, [u, v], [du * SPLAT_FORCE, dv * SPLAT_FORCE, 0], radius);
          splat(fluid.ink, [u, v], [0.13, 0, 0], radius);
          continue;
        }
        const jet = radius * 0.55;
        const reach = Math.sqrt(jet * 0.5) * 1.9;
        const push = SPLAT_FORCE * 0.08;
        for (let i = 0; i < SPLASH_JETS; i++) {
          const angle = turn + (i / SPLASH_JETS) * Math.PI * 2;
          const dx = Math.cos(angle);
          const dy = Math.sin(angle);
          const point = [u + (dx * reach) / aspect, v + dy * reach];
          const spin = [(dx - dy) * Math.SQRT1_2 * push, (dy + dx) * Math.SQRT1_2 * push, 0];
          splat(fluid.velocity, point, spin, jet);
          splat(fluid.ink, point, [0.75, 0, 0], jet);
        }
        splat(fluid.ink, [u, v], [0.45, 0, 0], radius);
      }
      splats.length = 0;
      fluidDirty = true;
    }
    const fluidActive = fluid !== null && now < fluidUntil;
    if (fluid) {
      if (fluidActive) stepFluid(fluid, Math.min(Math.max(dt, 1 / 240), 1 / 30), s);
      else if (fluidDirty) {
        clearFluid(fluid);
        fluidDirty = false;
      }
    }

    const grid = layout(s);

    fieldUniforms.uSize.value = [width, height];
    fieldUniforms.uGrid.value = grid.grid;
    fieldUniforms.uOrigin.value = grid.origin;
    fieldUniforms.uSlat.value = [grid.slatW, grid.slatH];
    fieldUniforms.uTime.value = time;
    fieldUniforms.uScale.value = s.scale;
    fieldUniforms.uDirection.value = (s.direction * Math.PI) / 180;
    fieldUniforms.uChop.value = clamp(s.chop, 0, 1.5);
    fieldUniforms.uStretch.value = clamp(s.stretch, 0, 0.95);
    fieldUniforms.uGlint.value = Math.max(0, s.glint);
    fieldUniforms.uContrast.value = clamp(s.contrast, 0.2, 4);
    fieldUniforms.uPerspective.value = clamp(s.perspective, 0, 1);
    fieldUniforms.uFog.value = clamp(s.fog, 0, 1);
    fieldUniforms.uIntro.value = introEase;
    fieldUniforms.uFluid.value = fluidActive ? 1 : 0;
    fieldUniforms.tVelocity.value = fluid ? fluid.velocity.read.texture : blank;
    fieldUniforms.tInk.value = fluid ? fluid.ink.read.texture : blank;
    fieldUniforms.uFluidTexel.value = fluidTexel.value;
    fieldUniforms.uInk.value = clamp(s.cursorStrength, 0, 3);
    fieldUniforms.uLean.value = clamp(s.lean, 0, 1);
    renderer.render({ scene: fieldMesh, target: fieldTarget });

    slatUniforms.uSize.value = [width, height];
    slatUniforms.uDpr.value = renderer.dpr;
    slatUniforms.uGrid.value = grid.grid;
    slatUniforms.uOrigin.value = grid.origin;
    slatUniforms.uSlat.value = [grid.slatW, grid.slatH];
    slatUniforms.uRound.value = clamp(s.roundness, 0, 1);
    slatUniforms.uTilt.value = fluidActive && s.lean > 0 ? grid.tilt : 0;
    slatUniforms.uColor.value = s.color.slice(0, 3);
    slatUniforms.uGlintColor.value = s.glintColor.slice(0, 3);
    slatUniforms.uBackground.value = s.backgroundColor;
    renderer.render({ scene: slatMesh });

    /* Nothing to draw means no frame: a still sea costs nothing until the cursor stirs
       it, which matters next to a page that is already animating a replay. */
    const moving = (!s.paused && !reducedMotion && s.speed !== 0) || introClock < 1;
    if (visible && (moving || fluidActive || fluidDirty)) raf = requestAnimationFrame(frame);
  };

  const start = () => {
    if (raf || !visible || !alive) return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  };
  wake = start;

  const locate = (event) => {
    const rect = container.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const w = Math.max(1, rect.width);
    const h = Math.max(1, rect.height);
    return { x, y, u: x / w, v: 1 - y / h, h, inside: x >= 0 && y >= 0 && x <= w && y <= h };
  };

  const queue = (u, v, du, dv, turn) => {
    if (splats.length > 32) splats.shift();
    splats.push([u, v, du, dv, turn]);
    fluidUntil = performance.now() + Math.max(1.5, settings.trail * 5) * 1000;
    start();
  };

  const onPointerMove = (event) => {
    const s = settings;
    const spot = locate(event);
    if (!fluid || !s.interactive || reducedMotion || !spot.inside) {
      pointer.tracked = false;
      return;
    }
    if (pointer.tracked && (spot.x !== pointer.x || spot.y !== pointer.y)) {
      queue(spot.u, spot.v, (spot.x - pointer.x) / spot.h, (pointer.y - spot.y) / spot.h, null);
    }
    pointer.x = spot.x;
    pointer.y = spot.y;
    pointer.tracked = true;
  };

  const onPointerDown = (event) => {
    const s = settings;
    const spot = locate(event);
    if (!fluid || !s.interactive || reducedMotion || !spot.inside) return;
    splashTurn += 2.39996;
    queue(spot.u, spot.v, 0, 0, splashTurn);
  };

  const onPointerLeave = () => {
    pointer.tracked = false;
  };

  const onPointerOut = (event) => {
    if (!event.relatedTarget) onPointerLeave();
  };

  const onVisibility = () => {
    if (!document.hidden) start();
  };

  /* On `window`, not on the canvas: the canvas is inert so it can never take a click from a
     control, and the fluid still follows the cursor wherever it is on the page. */
  window.addEventListener("pointermove", onPointerMove, { passive: true });
  window.addEventListener("pointerdown", onPointerDown, { passive: true });
  window.addEventListener("pointerout", onPointerOut, { passive: true });
  window.addEventListener("blur", onPointerLeave);
  document.addEventListener("visibilitychange", onVisibility);

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(container);
  const intersectionObserver = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    start();
  });
  intersectionObserver.observe(container);

  resize();

  teardown = () => {
    alive = false;
    visible = false;
    cancelAnimationFrame(raf);
    resizeObserver.disconnect();
    intersectionObserver.disconnect();
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerdown", onPointerDown);
    window.removeEventListener("pointerout", onPointerOut);
    window.removeEventListener("blur", onPointerLeave);
    document.removeEventListener("visibilitychange", onVisibility);
    motion?.removeEventListener("change", onMotionChange);
    disposeFluid();
    dispose(fieldTarget);
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
  };

  return handle;
}

/* The page's own copy. `data-slats` is the markup's way of saying "the sea goes here";
   everything about how it looks and moves is decided in BACKGROUND above. */
const host = document.querySelector("[data-slats]");
if (host) createSlats(host, BACKGROUND);
