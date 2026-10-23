/**
 * The pitch view: a canvas widget that draws 23 markers and interpolates
 * between whatever frames its data source hands it.
 *
 * Two source shapes meet one interface (`setFrames` / `reload`): a replay
 * source delivers the whole match at once and never goes stale; a live source
 * delivers a growing tail and can stop delivering. Live mode owns the stale
 * path — the playhead drifts on last-known positions for up to `STALE.holdMs`,
 * then markers fade and the "live data delayed" banner appears; a reconnect
 * snapshot glides in instead of snapping, because every drawn step is
 * rate-limited (`glideTowards`) and a feed jump becomes a walk.
 *
 * Everything per frame is a canvas op: no DOM re-render, so 23 moving entities
 * stay inside the frame budget on a phone. DOM work is limited to the chip
 * row (on click) and the card (while one is open).
 */

import {
  GLIDE_CAPS,
  STALE,
  ballPositionAt,
  buildEntityIndex,
  fitPitchTo,
  glideTowards,
  mapPoint,
  mapPointInverse,
  nearestTo,
  periodFor,
  playerPositionAt,
} from "./pitch-core.mjs";

/** Marker palettes. The default "midnight" pair puts electric violet and sun yellow on the
 *  view's midnight-black field, where both sit far from every other colour on it; okabe is
 *  the colour-blind-safe blue / orange pair, classic the traditional red / blue that does
 *  not survive the common colour-vision deficiencies and is offered for people who prefer it. */
const PALETTES = {
  midnight: { label: "midnight", home: "#8f00ff", homeText: "#ffffff", away: "#ffd900", awayText: "#141414" },
  okabe: { label: "colour-blind safe", home: "#0072b2", homeText: "#ffffff", away: "#e69f00", awayText: "#141414" },
  classic: { label: "classic", home: "#d9484f", homeText: "#ffffff", away: "#2563eb", awayText: "#ffffff" },
};
const PALETTE_KEYS = Object.keys(PALETTES);

const FADE_MS = 600; // substitutions fade out/in over this much match clock

function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

function makeEl(tag, cls, parent) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (parent) parent.appendChild(el);
  return el;
}

/**
 * `createPitchView(container, source, options)` — `source` is anything shaped
 * like `{ frames, teams, durationMs, isLive, capabilities }` (see
 * `pitch-sources.mjs`). `options.missing` decides how a vanished id behaves:
 * `"hold"` for live feeds (hold the last position, then fade — the spec's
 * dropped-frame case), `"fade"` for recorded matches where the future is
 * known and a disappearance *is* a substitution. Defaults from `isLive`.
 */
export function createPitchView(container, source, options = {}) {
  const state = {
    source,
    missing: options.missing ?? (source?.isLive ? "hold" : "fade"),
    orientation: options.orientation ?? "auto", // auto | fixed-left | fixed-right
    palette: options.palette ?? "midnight",
    label: options.label ?? "number", // number|name|position|speed|distance|none
    playing: options.playing ?? true,
    rate: options.rate ?? 1,
    selected: null,
  };

  /* ------------------------------------------------------------ dom shell */
  container.classList.add("pf-root");
  const wrap = makeEl("div", "pf-wrap", container);
  const canvas = document.createElement("canvas");
  canvas.className = "pf-canvas";
  wrap.appendChild(canvas);
  const chipbar = makeEl("div", "pf-chips", container);
  /* The banner and the card are position:absolute and must sit *on the pitch*, so they
     are children of the positioned wrap — appended to the container they would anchor
     to the nearest positioned ancestor, which is the viewport once nothing above them
     is positioned, i.e. they would float at the window's edges outside the pitch box. */
  const banner = makeEl("div", "pf-banner", wrap);
  banner.textContent = "live data delayed";
  const card = makeEl("div", "pf-card", wrap);

  /* ------------------------------------------------------------ animation */
  const anim = {
    raf: 0,
    lastWall: 0,
    tailArrivedAt: 0,
    prevTailT: -Infinity,
    head: 0, // replay playhead, match-clock ms
    drawn: new Map(), // id -> keyframe {x, y, alpha, name, pos, shirt, isGK, teamId, speed, distance}
    ball: { x: 0.5, y: 0.5, z: 0 },
    trail: [],
    lastTrailAt: -Infinity,
  };
  let index = null;
  let meta = new Map(); // id -> { firstSeenAt, lastSeenAt, last }
  let dpr = 1;
  let layout = fitPitchTo(320, 260);

  const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => resize()) : null;
  if (ro) ro.observe(wrap);

  function resize() {
    dpr = Math.max(1, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
    const w = Math.max(120, wrap.clientWidth || 320);
    const h = Math.max(90, wrap.clientHeight || Math.round(w * 0.72));
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    layout = fitPitchTo(w, h);
    // Pixel-space markers restart from their true positions after a relayout;
    // gliding across a re-scaled field would look like the bug it is.
    anim.drawn.clear();
  }

  /** Feed frames in (the live path). `snapshot: true` marks a resync burst. */
  function setFrames(next, { snapshot = false } = {}) {
    const list = next ?? [];
    index = list.length ? buildEntityIndex(list) : null;
    frames = list;
    const tailT = list.length ? list[list.length - 1].t : -Infinity;
    if (tailT !== anim.prevTailT) {
      anim.prevTailT = tailT;
      anim.tailArrivedAt = now();
    }
    if (snapshot) anim.tailArrivedAt = now();
    for (const f of list) {
      const seen = f.t;
      for (const p of f.players || []) {
        if (p == null || p.id == null) continue;
        let m = meta.get(p.id);
        if (!m) {
          m = { firstSeenAt: seen, lastSeenAt: seen, last: p };
          meta.set(p.id, m);
        }
        if (seen < m.firstSeenAt) m.firstSeenAt = seen;
        if (seen >= m.lastSeenAt) {
          m.lastSeenAt = seen;
          m.last = p;
        }
      }
    }
  }

  let frames = [];

  /** Re-read `source.frames` from scratch (the replay path). */
  function reload() {
    meta = new Map();
    anim.drawn.clear();
    anim.trail.length = 0;
    anim.prevTailT = -Infinity;
    setFrames(Array.from(state.source?.frames ?? []), { snapshot: true });
  }

  function now() {
    return typeof performance !== "undefined" ? performance.now() : Date.now();
  }

  /* ------------------------------------------------------------- playhead */
  function playheadTarget() {
    if (state.source?.isLive) {
      const tail = frames.length ? frames[frames.length - 1] : null;
      if (!tail) return 0;
      const since = Math.max(0, now() - anim.tailArrivedAt);
      // The clock runs ahead of the feed on the wall clock and stops advancing
      // no later than the fade finishes, so the banner and the faded markers
      // settle into their delayed state together.
      return tail.t + Math.min(since, STALE.holdMs + STALE.fadeMs + 400);
    }
    const tail = frames.length ? frames[frames.length - 1].t : 0;
    return Math.min(anim.head, tail);
  }

  /* ------------------------------------------------------------- the loop */
  function tick(wallNow) {
    const dt = Math.min(64, wallNow - anim.lastWall || 16);
    anim.lastWall = wallNow;
    const tail = frames.length ? frames[frames.length - 1].t : 0;

    if (!state.source?.isLive && state.playing && anim.head < tail) {
      anim.head = Math.min(tail, anim.head + dt * state.rate);
      if (api.emitPlayhead) api.emitPlayhead(anim.head);
    }

    const t = playheadTarget();
    const targets = sampleAt(t);
    const ballQ = index ? ballPositionAt(index, t, liveParams()) : null;

    // Glide: the drawn position chases each keyframe, rate-limited, so neither
    // a live resync nor a scrubber jump can teleport a marker.
    for (const [id, tr] of targets) {
      const cur = anim.drawn.get(id);
      const base = cur && cur.alpha > 0 ? { x: cur.x, y: cur.y } : { x: tr.x, y: tr.y };
      const g = glideTowards(base, tr, dt, GLIDE_CAPS.player);
      const next = Object.assign({}, tr, g);
      anim.drawn.set(id, next);
      if (tr.alpha <= 0) anim.drawn.delete(id);
    }
    for (const id of Array.from(anim.drawn.keys())) {
      if (!targets.has(id)) anim.drawn.delete(id);
    }
    if (ballQ && ballQ.alpha > 0) {
      const g = glideTowards(anim.ball, ballQ, dt, GLIDE_CAPS.ball);
      anim.ball = { x: g.x, y: g.y, z: ballQ.z ?? 0 };
      if (t - anim.lastTrailAt >= 40) {
        anim.trail.push({ t, x: g.x, y: g.y });
        anim.lastTrailAt = t;
      }
    } else if (ballQ && ballQ.state === "gone") {
      anim.trail.length = 0;
      anim.ballVisible = false;
    }
    if (ballQ && ballQ.alpha > 0) anim.ballVisible = true;
    while (anim.trail.length && t - anim.trail[0].t > 2000) anim.trail.shift();

    draw(t);
    syncBanner();
    anim.raf = requestAnimationFrame(tick);
  }

  function liveParams() {
    return state.source?.isLive || state.missing === "hold"
      ? { holdMs: STALE.holdMs, fadeMs: STALE.fadeMs }
      : { holdMs: 0, fadeMs: FADE_MS };
  }

  /**
   * Target keyframes for every visible entity at match-clock time `t`, alpha
   * included (fades run through the glide, so a sub slides while it fades).
   */
  function sampleAt(t) {
    const out = new Map();
    if (!index) return out;
    for (const [id, sightings] of index.playersBy) {
      if (id === "__ball__") continue;
      const q = playerPositionAt(index, id, t, liveParams());
      if (!q) continue; // a sub still on the bench
      const m = meta.get(id) ?? { firstSeenAt: q.seenAt, lastSeenAt: q.seenAt, last: null };
      let alpha;
      const departed = state.missing === "fade" && t > m.lastSeenAt + 0.5;
      if (q.state === "gone") {
        out.set(id, keyframe(q, 0, id, m));
        continue;
      }
      if (departed) alpha = clamp01(1 - (t - q.seenAt) / FADE_MS);
      else alpha = q.alpha;
      if (t - m.firstSeenAt < FADE_MS) alpha = Math.min(alpha, clamp01((t - m.firstSeenAt) / FADE_MS));
      out.set(id, keyframe(q, alpha, id, m));
    }
    return out;
  }

  function keyframe(q, alpha, id, m) {
    const last = m.last ?? {};
    return {
      x: q.x,
      y: q.y,
      alpha,
      kind: "player",
      name: last.name,
      pos: last.position,
      shirt: last.shirtNumber,
      isGK: Boolean(last.isGoalkeeper),
      teamId: last.teamId ?? (/^home/.test(String(id)) ? "home" : "away"),
      speed: q.speed,
      distance: q.distance,
    };
  }

  function orient() {
    if (state.orientation === "fixed-left") return "home-left";
    if (state.orientation === "fixed-right") return "home-right";
    const p = playheadTarget() >= 45 * 60000 ? periodFor(playheadTarget()) : "1H";
    return p === "1H" || p === "ET1" ? "home-left" : "home-right";
  }

  /* ---------------------------------------------------------------- paint */
  function draw(t) {
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
    drawPitch(ctx);
    drawTrail(ctx, t);
    drawEntities(ctx);
    drawBall(ctx);
  }

  function drawPitch(ctx) {
    const a = mapPoint(layout, 0, 0, "home-left");
    const b = mapPoint(layout, 1, 1, "home-left");
    const stripes = 10;
    for (let i = 0; i < stripes; i++) {
      ctx.fillStyle = i % 2 ? "#0b0b1a" : "#101024";
      ctx.fillRect(a.x + ((b.x - a.x) * i) / stripes, a.y, (b.x - a.x) / stripes + 0.5, b.y - a.y);
    }
    ctx.strokeStyle = "rgba(224,213,255,0.9)";
    ctx.lineWidth = 1.25;
    ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
    const mid = mapPoint(layout, 0.5, 0, "home-left");
    ctx.beginPath();
    ctx.moveTo(mid.x, a.y);
    ctx.lineTo(mid.x, b.y);
    ctx.stroke();
    const centre = mapPoint(layout, 0.5, 0.5, "home-left");
    ctx.beginPath();
    ctx.arc(centre.x, centre.y, 9.15 * layout.scale, 0, Math.PI * 2);
    ctx.stroke();
    for (const end of ["left", "right"]) {
      const s = end === "left" ? 1 : -1;
      box(ctx, 16.5, 40.32, s); // penalty area
      box(ctx, 5.5, 18.32, s); // six-yard area
      box(ctx, -2, 7.32, s); // the goal, outside the line
      const spotX = end === "left" ? a.x + 11 * layout.scale : b.x - 11 * layout.scale;
      ctx.beginPath();
      ctx.fillStyle = "rgba(224,213,255,0.95)";
      ctx.arc(spotX, centre.y, 1.4, 0, Math.PI * 2);
      ctx.fill();
      // The arc outside the penalty box, centred on the spot.
      ctx.beginPath();
      ctx.strokeStyle = "rgba(224,213,255,0.9)";
      if (end === "left") ctx.arc(spotX, centre.y, 9.15 * layout.scale, -0.93, 0.93);
      else ctx.arc(spotX, centre.y, 9.15 * layout.scale, Math.PI - 0.93, Math.PI + 0.93);
      ctx.stroke();
    }
  }

  /** Stroke a box `depthM` deep and `widthM` wide, centred on the goal line of the end signed by `s`. */
  function box(ctx, depthM, widthM, s) {
    const gx = s > 0 ? 0 : 1;
    const x0 = gx + Math.min(0, depthM) / 105;
    const x1 = gx + Math.max(0, depthM) / 105;
    const p0 = mapPoint(layout, x0, clamp01(0.5 - widthM / 2 / 68), "home-left");
    const p1 = mapPoint(layout, x1, clamp01(0.5 + widthM / 2 / 68), "home-left");
    ctx.strokeRect(Math.min(p0.x, p1.x), Math.min(p0.y, p1.y), Math.abs(p1.x - p0.x), Math.abs(p1.y - p0.y));
  }

  function drawTrail(ctx, t) {
    if (anim.trail.length < 2) return;
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    for (let i = 1; i < anim.trail.length; i++) {
      const prev = anim.trail[i - 1];
      const pt = anim.trail[i];
      const age = 1 - clamp01((t - pt.t) / 2000);
      ctx.strokeStyle = `rgba(255,217,0,${0.08 + age * 0.4})`;
      ctx.beginPath();
      ctx.moveTo(...px(prev));
      ctx.lineTo(...px(pt));
      ctx.stroke();
    }
  }

  function drawEntities(ctx) {
    const palette = PALETTES[state.palette] ?? PALETTES.okabe;
    const entries = Array.from(anim.drawn.values()).filter((e) => e.alpha > 0);
    const near = nearestTo(anim.ball.x, anim.ball.y, entries);
    const r = Math.max(9, layout.scale * 3.4);
    for (const e of entries) {
      const [cx, cy] = px(e);
      ctx.globalAlpha = e.alpha;
      const fill = e.isGK ? "#f8fafc" : palette[e.teamId] ?? palette.away;
      const textColor = e.isGK ? palette[e.teamId] ?? palette.away : e.teamId === "home" ? palette.homeText : palette.awayText;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = fill;
      ctx.fill();
      ctx.lineWidth = e.isGK ? 2 : 1;
      ctx.strokeStyle = e.isGK ? palette[e.teamId] ?? palette.away : "rgba(0,0,0,0.25)";
      ctx.stroke();
      if (near === e) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(255,255,255,0.95)";
        ctx.beginPath();
        ctx.arc(cx, cy, r + 2, 0, Math.PI * 2);
        ctx.stroke();
      }
      let inside = null;
      if (state.label === "number") inside = { text: String(e.shirt ?? ""), size: r * 0.95 };
      else if (state.label === "name") inside = { text: String(e.name ?? "").slice(0, 8), size: r * 0.6 };
      else if (state.label === "position") inside = { text: String(e.pos ?? ""), size: r * 0.6 };
      else if (state.label === "speed") inside = { text: e.speed == null ? "—" : String(Math.round(e.speed)), size: r * 0.55 };
      else if (state.label === "distance") inside = { text: e.distance == null ? "—" : e.distance.toFixed(1), size: r * 0.55 };
      if (inside && inside.text) {
        ctx.fillStyle = textColor;
        ctx.font = `700 ${Math.round(inside.size)}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(inside.text, cx, cy);
      }
      // The short name rides below the disc unless it is already the label.
      if ((state.label === "number" || state.label === "none") && e.name) {
        ctx.font = "600 10px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = "rgba(0,0,0,0.55)";
        ctx.fillStyle = "rgba(255,255,255,0.92)";
        ctx.strokeText(e.name.slice(0, 9), cx, cy + r + 2);
        ctx.fillText(e.name.slice(0, 9), cx, cy + r + 2);
      }
      if (state.selected === e.__id) {
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(cx, cy, r + 5, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.globalAlpha = 1;
    }
  }

  function drawBall(ctx) {
    if (anim.ballVisible === false) return;
    const [cx, cy] = px(anim.ball);
    const r = Math.max(4, layout.scale * 1.35) * (1 + (anim.ball.z || 0) * 0.35);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(0,0,0,0.4)";
    ctx.stroke();
  }

  /** Normalised → container pixels, honouring the current orientation. */
  function px(e) {
    const p = mapPoint(layout, e.x, e.y, orient());
    return [p.x, p.y];
  }

  /* --------------------------------------------------------- DOM widgets */
  const CHIPS = [
    ["number", "numbers"],
    ["name", "names"],
    ["position", "positions"],
    ["speed", "speed"],
    ["distance", "distance"],
    ["none", "plain"],
  ];

  function renderChips() {
    chipbar.textContent = "";
    const caps = state.source?.capabilities ?? {};
    for (const [key, label] of CHIPS) {
      // Only offer chips the feed actually supports.
      if ((key === "speed" || key === "distance") && !caps[key]) continue;
      const b = makeEl("button", "pf-chip", chipbar);
      b.textContent = label;
      b.setAttribute("aria-pressed", String(state.label === key));
      if (state.label === key) b.classList.add("is-on");
      b.addEventListener("click", () => {
        state.label = key;
        renderChips();
      });
    }
    // Orientation chip: auto → fixed (home left) → fixed (home right) → auto.
    const flip = makeEl("button", "pf-chip pf-chip--flip", chipbar);
    flip.textContent = state.orientation === "auto" ? "flip: auto" : state.orientation === "fixed-left" ? "flip: fixed L" : "flip: fixed R";
    flip.addEventListener("click", () => {
      state.orientation = state.orientation === "auto" ? "fixed-left" : state.orientation === "fixed-left" ? "fixed-right" : "auto";
      renderChips();
    });
    const pal = makeEl("button", "pf-chip pf-chip--palette", chipbar);
    pal.textContent = (PALETTES[state.palette] ?? PALETTES.midnight).label;
    pal.addEventListener("click", () => {
      const at = PALETTE_KEYS.indexOf(state.palette);
      state.palette = PALETTE_KEYS[(at + 1) % PALETTE_KEYS.length];
      renderChips();
    });
  }

  function openCard(entry) {
    const team = (state.source?.teams ?? []).find((tm) => tm.teamId === entry.teamId);
    card.textContent = "";
    const h = makeEl("div", "pf-card__head", card);
    h.textContent = entry.name ?? entry.__id ?? "";
    const rows = makeEl("dl", "pf-card__rows", card);
    const facts = [
      ["team", team?.name ?? entry.teamId],
      ["position", entry.isGK ? "goalkeeper" : entry.pos],
      ["shirt", entry.shirt],
      ["speed", entry.speed == null ? null : `${Math.round(entry.speed)} km/h`],
      ["distance", entry.distance == null ? null : `${entry.distance.toFixed(2)} km`],
    ];
    for (const [k, v] of facts) {
      if (v == null || v === "") continue;
      const dt = makeEl("dt", null, rows);
      dt.textContent = k;
      const dd = makeEl("dd", null, rows);
      dd.textContent = String(v);
    }
    card.classList.add("is-open");
  }

  function closeCard() {
    state.selected = null;
    card.classList.remove("is-open");
  }

  /* ---------------------------------------------------------- interaction */
  let downAt = 0;
  canvas.addEventListener("pointerdown", (ev) => {
    downAt = now();
    // Clicking anywhere else in the wrap (not the canvas, not the card) closes.
    void ev;
  });
  canvas.addEventListener("pointerup", (ev) => {
    if (now() - downAt > 350) return; // a drag, not a tap
    const pick = hitTest(ev);
    if (!pick) {
      closeCard();
      return;
    }
    state.selected = pick.__id;
    openCard(pick);
  });
  card.addEventListener("pointerup", (ev) => ev.stopPropagation());
  wrap.addEventListener("pointerdown", (ev) => {
    if (ev.target === canvas || card.contains(ev.target)) return;
    closeCard();
  });

  function hitTest(ev) {
    const rect = canvas.getBoundingClientRect();
    const inv = mapPointInverse(layout, ev.clientX - rect.left, ev.clientY - rect.top, orient());
    const slack = (Math.max(9, layout.scale * 3.4) + 8) / layout.scale;
    let best = null;
    let bestD = Infinity;
    for (const [id, e] of anim.drawn) {
      if (e.alpha <= 0.2) continue;
      const d = Math.hypot(e.x - inv.x, e.y - inv.y);
      if (d < bestD) {
        bestD = d;
        best = Object.assign({ __id: id }, e);
      }
    }
    if (!best || bestD > slack) return null;
    return best;
  }

  /* ------------------------------------------------------------ awareness */
  function syncBanner() {
    banner.classList.toggle(
      "is-on",
      Boolean(state.source?.isLive) && anim.tailArrivedAt > 0 && now() - anim.tailArrivedAt > STALE.holdMs,
    );
  }

  /* ------------------------------------------------------------ public API */
  const api = {
    canvas,
    setFrames,
    reload,
    setPlayback({ playing, rate } = {}) {
      if (playing !== undefined) state.playing = playing;
      if (rate !== undefined) state.rate = rate;
    },
    seek(ms) {
      anim.head = Math.max(0, ms);
      anim.trail.length = 0;
    },
    get playheadMs() {
      return playheadTarget();
    },
    get isStale() {
      return Boolean(state.source?.isLive) && anim.tailArrivedAt > 0 && now() - anim.tailArrivedAt > STALE.holdMs;
    },
    setOrientation(mode) {
      state.orientation = mode ?? "auto";
      renderChips();
    },
    setPalette(p) {
      state.palette = PALETTES[p] ? p : state.palette;
      renderChips();
    },
    get palettes() {
      return PALETTE_KEYS;
    },
    /** Optional callback: `fn(matchClockMs)` while replay playback advances. */
    emitPlayhead: null,
    destroy() {
      cancelAnimationFrame(anim.raf);
      if (ro) ro.disconnect();
      container.classList.remove("pf-root");
      container.textContent = "";
    },
    __debug: {
      get drawn() {
        return anim.drawn;
      },
      get anim() {
        return anim;
      },
      get state() {
        return state;
      },
      orient,
      /* One manual loop step, for driving the view where rAF is throttled (a
         background tab) or absent. Not used by the page itself. */
      tick(wallNow) {
        tick(wallNow ?? now());
      },
    },
  };

  renderChips();
  resize();
  reload();
  anim.lastWall = now();
  anim.raf = requestAnimationFrame(tick);
  return api;
}
