/* Drive the running overlay in a real browser and report what the page actually does.
 *
 * This is the other half of tools/browser_check.py: that script starts the server and the
 * browser, this one speaks the DevTools protocol to the tab and prints one JSON object of
 * observations. Every judgement lives in tests/test_browser_harness.py — a question about
 * the page ("is the boundary nearest the pointer the brightest one?") is answered here by
 * measuring, and decided there, next to the rest of the suite's assertions.
 *
 * It exists because nothing else in this project can see the page. The ports in
 * pitchframe/overlay/static are decoration and canvas: no test that reads a file can tell
 * whether a panel lit up, whether one lamp moved across the page or eight panels each lit
 * their own edge, or whether the sphere drew a card. The measurements below are the ones
 * that were made by hand in a browser while these ports were written — kept, rather than
 * thrown away with the session that made them, because the next change to the glow or the
 * sea is otherwise unverifiable from a terminal.
 *
 * Three things about how it drives the browser are deliberate:
 *
 *   It writes nothing to the page. Every probe is a read of the DOM, a computed style, or
 *   a screenshot. The two exceptions are documented where they happen: the column probe
 *   scrolls the page to see which panels are reachable as the commentary moves and returns
 *   it to the top, and the layout probe takes the port's class off a panel to measure the
 *   box underneath it, then puts it back.
 *
 *   Reduced motion is emulated for the whole run. The sea animates, the sphere spins on
 *   its own and the replay appends rows — with any of those moving, "did the pointer change
 *   this pixel" has no answer. The setting the ports already respect is the cheapest way to
 *   hold the page still, and it is what a reader who has asked for no motion sees.
 *
 *   The screenshots are of bands, not of the page. A full-viewport PNG is handed to the tab
 *   as base64 to be decoded twice per comparison; four 10-pixel-wide strips are the same
 *   question about the same pixels for a fraction of the traffic.
 *
 * Usage: PF_BASE=http://127.0.0.1:8000 CDP_PORT=9333 node tools/browser_check.mjs
 */

import { setTimeout as sleep } from "node:timers/promises";

const PORT = Number(process.env.CDP_PORT || 9333);
const BASE = process.env.PF_BASE || "http://127.0.0.1:8000";
const MATCH_PATH = process.env.PF_MATCH || "/match/matchday";

/* A fixed viewport, so that every rectangle below is the same rectangle on every machine
   and a band can be described in pixels rather than in fractions of whatever the window
   happened to be. */
const VIEWPORT = { width: 1440, height: 900 };

/* ------------------------------------------------------------------ the socket -- */

async function target() {
  for (let attempt = 0; attempt < 80; attempt++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((tab) => tab.type === "page" && tab.webSocketDebuggerUrl);
      if (page) return page;
    } catch {}
    await sleep(250);
  }
  throw new Error(`nothing answered the DevTools endpoint on 127.0.0.1:${PORT}`);
}

const page = await target();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.onopen = resolve;
  ws.onerror = reject;
});

let nextId = 0;
const pending = new Map();
/* What the page said about itself while it ran: exceptions, console errors and warnings.
   Reported per navigation, because a clean catalogue page says nothing about a feed that
   threw on its way up. */
const problems = [];
let at = "start";

ws.onmessage = (message) => {
  const msg = JSON.parse(message.data);
  if (msg.method === "Runtime.exceptionThrown") {
    problems.push({ at, kind: "exception", text: msg.params.exceptionDetails.text });
  }
  if (msg.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(msg.params.type)) {
    problems.push({
      at,
      kind: msg.params.type,
      text: msg.params.args.map((arg) => arg.value ?? arg.description ?? "").join(" ")
    });
  }
  if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
    const entry = msg.params.entry;
    /* The faces are requested from a CDN and never waited on — see the <head> of either
       page. Offline, that request fails, and it is the one failure this project intends. */
    if (!`${entry.url || ""}${entry.text}`.includes("fonts.g")) {
      problems.push({ at, kind: "network", text: entry.text, url: entry.url });
    }
  }
  const resolve = pending.get(msg.id);
  if (resolve) {
    pending.delete(msg.id);
    resolve(msg);
  }
};

const send = (method, params = {}) =>
  new Promise((resolve) => {
    const id = ++nextId;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });

const evaluate = async (expression) => {
  const msg = await send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true
  });
  if (msg.result?.exceptionDetails) {
    throw new Error(`page threw: ${JSON.stringify(msg.result.exceptionDetails)}`);
  }
  return msg.result?.result?.value;
};

const waitFor = async (expression, timeout = 30000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return true;
    await sleep(150);
  }
  return false;
};

/* A real mouse move through the browser's own input pipeline, which is what a hover is:
   `dispatchEvent` would produce an event the page's listener sees but the browser's own
   `:hover` state would not follow. */
const move = (x, y) =>
  send("Input.dispatchMouseEvent", {
    type: "mouseMoved",
    x: Math.round(x),
    y: Math.round(y),
    button: "none",
    buttons: 0
  });

const press = async (key, code, vk) => {
  const base = { key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk };
  await send("Input.dispatchKeyEvent", { type: "rawKeyDown", ...base });
  await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
};

const capture = async (clip) => {
  const shot = await send("Page.captureScreenshot", { format: "png", clip: { ...clip, scale: 1 } });
  return shot.result.data;
};

/* Two strips, compared pixel by pixel in the tab, which already has a PNG decoder. */
const compare = (coldData, warmData) =>
  evaluate(`(async () => {
    const load = async (data) =>
      await createImageBitmap(await (await fetch("data:image/png;base64," + data)).blob());
    const [cold, warm] = [await load(${JSON.stringify(coldData)}), await load(${JSON.stringify(warmData)})];
    const canvas = document.createElement("canvas");
    canvas.width = cold.width; canvas.height = cold.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const read = (image) => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0);
      return ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    };
    const a = read(cold), b = read(warm);
    let changed = 0, delta = 0, sumCold = 0, sumWarm = 0;
    for (let i = 0; i < a.length; i += 4) {
      const step = Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]);
      delta += step;
      if (step) changed += 1;
      sumCold += a[i] + a[i + 1] + a[i + 2];
      sumWarm += b[i] + b[i + 1] + b[i + 2];
    }
    const pixels = a.length / 4;
    return {
      pixels,
      changed,
      share: Number((changed / pixels).toFixed(4)),
      meanDelta: Number((delta / pixels).toFixed(3)),
      meanChannelCold: Number((sumCold / (pixels * 3)).toFixed(2)),
      meanChannelWarm: Number((sumWarm / (pixels * 3)).toFixed(2)),
      size: [cold.width, cold.height]
    };
  })()`);

/* Every glowing panel on the page, with what the one light has made of it: the rectangle,
   the distance from the pointer to it, the share of the light it is given, and the opacity
   the three layers resolve to. The opacity is the point — a falloff that never reaches the
   stylesheet would be a number nothing draws with. */
const panelReading = (pointer) =>
  evaluate(`(() => {
    const point = ${JSON.stringify(pointer)};
    const rect = (el) => { const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height }; };
    const distance = (r) => {
      const dx = Math.max(r.left - point.x, 0, point.x - r.right);
      const dy = Math.max(r.top - point.y, 0, point.y - r.bottom);
      return Math.round(Math.hypot(dx, dy));
    };
    return [...document.querySelectorAll(".border-glow")].map((el) => {
      const r = rect(el);
      const edge = el.querySelector(":scope > .edge-light");
      return {
        name: el.className.split(" ")[0] + (el.id ? "#" + el.id : ""),
        containsPointer: point.x >= r.left && point.x <= r.right && point.y >= r.top && point.y <= r.bottom,
        distance: distance(r),
        falloff: el.style.getPropertyValue("--glow-falloff"),
        proximity: el.style.getPropertyValue("--edge-proximity"),
        angle: el.style.getPropertyValue("--cursor-angle"),
        lit: el.dataset.lit || null,
        ringOpacity: Number(getComputedStyle(el, "::before").opacity),
        fillOpacity: Number(getComputedStyle(el, "::after").opacity),
        haloOpacity: Number(getComputedStyle(edge).opacity),
        rect: { left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }
      };
    });
  })()`);

/* ------------------------------------------------------------------- the run -- */

const out = { base: BASE, viewport: VIEWPORT, matchPath: MATCH_PATH };

await send("Page.enable");
await send("Runtime.enable");
await send("Log.enable");
await send("Emulation.setDeviceMetricsOverride", { ...VIEWPORT, deviceScaleFactor: 1, mobile: false });
await send("Emulation.setEmulatedMedia", {
  features: [{ name: "prefers-reduced-motion", value: "reduce" }]
});

/* -------------------------------------------------------------------- the feed -- */

at = MATCH_PATH;
await send("Page.navigate", { url: `${BASE}${MATCH_PATH}` });
const feedUp = await waitFor(
  "document.readyState === 'complete' && !!document.querySelector('.border-glow') && !!document.querySelector('#feed-list .row, #feed-list li')"
);
/* Paused, so that the only thing that can move between two frames is what is moved. */
await evaluate("(() => { const p = document.getElementById('play'); if (p) p.click(); window.scrollTo(0, 0); return true; })()");
await sleep(600);

out.feed = {
  path: await evaluate("location.pathname"),
  ready: feedUp,
  scrollY: await evaluate("window.scrollY")
};

/* What each marked panel was given: the port's class, its one layer, and the fact that the
   layer is out of flow — the whole reason a port can adorn markup it did not build. */
out.feed.panels = await evaluate(`[...document.querySelectorAll('[data-glow]')].map((el) => {
  const edge = el.querySelector(':scope > .edge-light');
  const style = getComputedStyle(el);
  return {
    name: el.className.split(' ')[0],
    glow: el.classList.contains('border-glow'),
    layers: el.querySelectorAll(':scope > .edge-light').length,
    edge: edge ? { position: getComputedStyle(edge).position, ariaHidden: edge.getAttribute('aria-hidden') } : null,
    display: style.display,
    position: style.position,
    isolation: style.isolation,
    radius: style.getPropertyValue('--border-radius').trim(),
    /* How far outside its own box a panel asks the halo to reach. Zero is the claim that
       makes the clipping probe below mean something: nothing an ancestor clips can be
       part of the halo if the halo never leaves the panel. */
    glowPadding: parseFloat(style.getPropertyValue('--glow-padding')) || 0
  };
})`);

/* The page's own name. The feed opens on a scoreboard, so the heading is the one element
   that says what the address is — and it is the line a shared link lands on, which is why
   it is mounted as a pressure host. The heading's visible letters are aria-hidden and the
   clipped copy beside them is the one a reader hears, so the text is read from that copy:
   it has to name the match the replay is, once, and agree with the tab. */
out.feed.heading = await evaluate(`(() => {
  const headings = document.querySelectorAll('h1');
  const h1 = headings[0] || null;
  const say = h1 ? h1.querySelector('.text-pressure__say') : null;
  return {
    count: headings.length,
    tag: h1 ? h1.tagName : null,
    text: (say ? say.textContent : (h1 ? h1.textContent : '')).trim(),
    title: document.title,
    pressure: Boolean(h1 && h1.classList.contains('text-pressure-host')),
    letters: h1 ? h1.querySelectorAll('.text-pressure__letter').length : 0
  };
})()`);

/* Anything between a panel and the page that would cut its halo off. The port answers that
   by drawing the halo inside the boundary — `--glow-padding` is 0 and the layer is inset
   0 — so an ancestor can only cut it when a panel has been asked to reach outside itself.
   The report is what would actually be cut rather than every scroll container in between:
   the analyst column is one, deliberately, so that its lower panels are reachable at all
   (see the column probe below), and a panel scrolled against its edge is clipped along
   its own boundary, which is what the window does too. Opacity and filter are reported
   whatever the padding: they change how the panel is composited, not where it is. */
/* The team sheet. Both squads carry the eleven who started and the five behind them, and
   the panel has to show both: a roster that names only the starters is half a team sheet,
   and a bench merged into the starters would read as sixteen players on the pitch. The
   positions are read too — they were in the payload and the panel used not to print them,
   which is a list of names rather than a team sheet. */
out.feed.teamSheet = await evaluate(`(() => {
  const teams = [...document.querySelectorAll('.squads__team')];
  return {
    teams: teams.length,
    roles: [...document.querySelectorAll('.squads__role')].map((el) => el.textContent.trim()),
    starters: document.querySelectorAll('.squads__list:not(.squads__list--bench) .squads__player').length,
    substitutes: document.querySelectorAll('.squads__player--sub').length,
    positions: document.querySelectorAll('.squads__pos').length,
    names: [...document.querySelectorAll('.squads__player--sub')].map((el) => el.textContent.replace(/\\s+/g, ' ').trim())
  };
})()`);

out.feed.clippingAncestors = await evaluate(`(() => {
  const out = [];
  for (const el of document.querySelectorAll('[data-glow]')) {
    const padding = parseFloat(getComputedStyle(el).getPropertyValue('--glow-padding')) || 0;
    for (let node = el.parentElement; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      const cuts = style.overflow !== 'visible' && padding > 0;
      if (cuts || style.opacity !== '1' || style.filter !== 'none') {
        out.push({
          panel: el.className.split(' ')[0],
          ancestor: node.className || node.tagName,
          overflow: style.overflow, opacity: style.opacity, filter: style.filter,
          glowPadding: padding
        });
      }
    }
  }
  return out;
})()`);

/* The corner the pointer is parked in when it must not be the light: the far corner from
   the feed's own boundary, which the sections below use to tell a panel lit by focus from
   one lit by a lamp that happened to be near it. */
const PARK = { x: VIEWPORT.width - 3, y: 3 };

/* ------------------------------------------------------------- the keyboard --- */

/* A reader with no pointer at all has to be able to see which panel they are in. The
   pointer is parked in the corner first, and the panel the button lives in is read: it has
   to be lit at full strength while the light is nowhere near it. */
out.feed.keyboard = await (async () => {
  await move(PARK.x, PARK.y);
  await sleep(900);
  const parked = await panelReading(PARK);
  const focused = await evaluate(`(() => {
    const button = document.getElementById('promo-btn');
    if (!button) return null;
    button.focus();
    return document.activeElement.id;
  })()`);
  await sleep(500);
  const panel = await evaluate(`(() => {
    const el = document.querySelector('.feed');
    return { lit: el.dataset.lit || null, angle: el.style.getPropertyValue('--cursor-angle'),
      falloff: el.style.getPropertyValue('--glow-falloff'),
      ringOpacity: Number(getComputedStyle(el, '::before').opacity) };
  })()`);
  const afterBlur = await evaluate(`(() => {
    document.activeElement.blur();
    const el = document.querySelector('.feed');
    return { active: document.activeElement.tagName, lit: el.dataset.lit || null,
      falloff: el.style.getPropertyValue('--glow-falloff') };
  })()`);
  return { focused, panel, afterBlur, parked };
})();

/* ------------------------------------------------- one light, or eight lamps -- */

/* The feed is the widest panel on the page and the one a reader spends the match in, and
   the light has work to do on either side of it: the engine room of this page is its
   columns, and the ledger, the metrics and the line-ups are the panels beside the feed.
   They are only laid out in the analyst view — the fan view hides the whole side column —
   so the light is measured there, where all seven are on screen at once. */
await press("a", "KeyA", 65);
const analyst = await waitFor("document.body.dataset.view === 'analyst'");
await sleep(700);
out.feed.view = await evaluate("document.body.dataset.view");
out.feed.allPanelsLaidOut = analyst;

/* The pointer is put on the feed's own left boundary, three pixels inside it, and every
   panel on the page is then read. The panel the pointer is in has to be at full strength,
   the panels around it have to be lit in proportion to how near they are, and the ones
   out of reach have to be dark — which is the whole difference between one lamp moving
   across the page and eight panels each deciding for itself. */
const feedBox = await evaluate(`(() => {
  const r = document.querySelector('.feed').getBoundingClientRect();
  return { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height) };
})()`);
out.feed.feedBox = feedBox;

const point = {
  x: Math.max(2, feedBox.left + 3),
  y: Math.round(Math.min(VIEWPORT.height - 4, Math.max(2, feedBox.top + 40)))
};
out.feed.pointer = point;

await move(point.x, point.y);
await sleep(1200);
out.feed.light = await panelReading(point);

/* The same reading with the light off the panels entirely: with nothing near the page,
   nothing on it is lit, whatever the pointer last did. */
await move(PARK.x, PARK.y);
await sleep(900);
out.feed.away = await panelReading(PARK);

/* -------------------------------------------------------- the boundary bands --- */

/* The pixel-level version of the same claim: with the pointer on the feed's left boundary,
   the strip along that boundary changes and the strip along the opposite one does not. A
   control frame with nothing moved in between says how much of that is the page animating
   on its own, which is nothing while reduced motion is emulated — and a band comparison
   against a moving page would prove nothing. */
out.feed.bands = await (async () => {
  const top = Math.round(Math.max(4, feedBox.top + 60));
  const height = Math.round(Math.min(feedBox.height - 120, VIEWPORT.height - top - 20));
  const bands = {
    leftEdge: { x: Math.max(2, feedBox.left + 1), y: top, width: 10, height },
    rightEdge: { x: Math.max(2, feedBox.right - 11), y: top, width: 10, height },
    interior: { x: Math.max(2, feedBox.left + 300), y: top, width: 10, height }
  };

  await move(PARK.x, PARK.y);
  await sleep(1200);
  const cold = {};
  for (const [name, clip] of Object.entries(bands)) cold[name] = await capture(clip);
  await sleep(1200);
  const idle = {};
  for (const [name, clip] of Object.entries(bands)) idle[name] = await capture(clip);
  await move(point.x, point.y);
  await sleep(1200);
  const warm = {};
  for (const [name, clip] of Object.entries(bands)) warm[name] = await capture(clip);

  const report = { clip: bands, scrollY: await evaluate("window.scrollY") };
  for (const name of Object.keys(bands)) {
    report[name] = {
      idleBaseline: await compare(cold[name], idle[name]),
      warm: await compare(cold[name], warm[name])
    };
  }
  return report;
})();

/* ------------------------------------------------------- the analyst column --- */

/* The column beside the commentary is pinned, and a pinned box covers whatever follows it
   in its own column — which is exactly how the metrics and the line-ups used to slide up
   under the check panel and the ledger and could not be read at all. Two things are asked
   of it here, because fixing one without the other is what the reader reported: the rail
   is inside the pin (so nothing is covered), and the pin is the column's own scrollport
   (so the panels below the fold of a laptop window are reachable at all).

   The page is scrolled to where the pin is stuck — where a reader following the commentary
   is — and then the column's own scroll is walked: at each step, was a panel wholly
   inside the column's client box, and did the point inside it belong to the panel or to
   something drawn over it. Both scrolls are put back afterwards; this is the one probe
   besides the layout one that moves the page rather than reading it, and it runs after
   the pixel bands so none of them measures a scrolled page.

   The replay is walked to full time first, one window at a time from the keyboard. At the
   opening minute the feed is a few rows long, there is nothing for a pinned column to be
   pinned against, and the document is too short for the pin to reach the top of the
   window — which is a page nobody needs this guarantee about. */
for (let i = 0; i < 18; i += 1) {
  await press("ArrowRight", "ArrowRight", 39);
  await sleep(60);
}
await sleep(700);
out.feed.column = await evaluate(`(async () => {
  const names = ["check", "ledger", "metrics", "squads"];
  const panels = names.map((name) => document.querySelector("." + name));
  const pin = document.querySelector(".side__pin");
  const rail = document.querySelector(".rail");
  const drawn = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));
  if (!pin) return { railInsidePin: false, panels: [] };

  const range = document.documentElement.scrollHeight - window.innerHeight;
  const pinTop = pin.getBoundingClientRect().top + window.scrollY;
  /* Its sticky offset is 12 pixels; scrolling there is where the pin takes over. */
  window.scrollTo(0, Math.max(0, Math.min(range, Math.round(pinTop - 12))));
  await drawn();

  const box = () => pin.getBoundingClientRect();
  const seen = names.map((name) => ({ name, whole: false, covered: 0, positions: 0 }));
  const steps = 24;

  for (let step = 0; step <= steps; step += 1) {
    pin.scrollTop = ((pin.scrollHeight - pin.clientHeight) * step) / steps;
    await drawn();
    panels.forEach((el, index) => {
      if (!el) return;
      const column = box();
      const at = el.getBoundingClientRect();
      const whole = at.top >= column.top - 1 && at.bottom <= column.bottom + 1
        && at.top >= -1 && at.bottom <= window.innerHeight + 1;
      if (whole) seen[index].whole = true;
      if (at.top < column.top - 1 || at.bottom > column.bottom + 1) return;
      const hit = document.elementFromPoint(
        at.left + Math.min(24, at.width / 2),
        Math.min(column.bottom - 4, Math.max(column.top + 4, at.top + 18))
      );
      seen[index].positions += 1;
      if (!el.contains(hit)) seen[index].covered += 1;
    });
  }

  const column = box();
  const report = {
    railInsidePin: Boolean(rail && pin.contains(rail)),
    scrollRange: Math.round(range),
    overflow: getComputedStyle(pin).overflowY,
    client: pin.clientHeight,
    scroll: pin.scrollHeight,
    top: Math.round(column.top),
    bottom: Math.round(column.bottom),
    /* A scrollport only a pointer can scroll is half a control: the page gives it a tab
       stop exactly while it has something to scroll, so Safari can reach it too. */
    tabindex: pin.getAttribute('tabindex'),
    role: pin.getAttribute('role'),
    label: pin.getAttribute('aria-label'),
    panels: seen
  };
  pin.scrollTop = 0;
  window.scrollTo(0, 0);
  await drawn();
  return report;
})()`);

/* The gesture itself, because `scrollTop = x` would keep passing if something on the page
   had started swallowing the wheel: a turn over the column has to move the column, and a
   turn at its end has to move the page — otherwise the pointer over the column is a trap
   at the bottom of the line-ups. */
out.feed.column.wheel = await (async () => {
  const spot = await evaluate(`(() => {
    const pin = document.querySelector('.side__pin');
    const range = document.documentElement.scrollHeight - window.innerHeight;
    window.scrollTo(0, Math.min(range, Math.round(pin.getBoundingClientRect().top + window.scrollY - 12)));
    pin.scrollTop = 0;
    const b = pin.getBoundingClientRect();
    return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) };
  })()`);
  await sleep(300);
  const turn = async () => {
    await send("Input.dispatchMouseEvent", {
      type: "mouseWheel", x: spot.x, y: spot.y, deltaX: 0, deltaY: 320
    });
    await sleep(350);
  };
  await turn();
  const columnMoved = await evaluate("document.querySelector('.side__pin').scrollTop");
  await evaluate(`(() => {
    const pin = document.querySelector('.side__pin');
    pin.scrollTop = pin.scrollHeight - pin.clientHeight;
    return true;
  })()`);
  const pageBefore = await evaluate("window.scrollY");
  await turn();
  const pageAfter = await evaluate("window.scrollY");
  await evaluate(`(() => {
    document.querySelector('.side__pin').scrollTop = 0;
    window.scrollTo(0, 0);
    return true;
  })()`);
  await sleep(200);
  return { columnMoved: Math.round(columnMoved), pageBefore, pageAfter, pageMoved: pageAfter > pageBefore + 1 };
})();

/* ------------------------------------------------------------- the layout --- */

/* The one probe that writes to the markup (the column above moved the scroll position and
   put it back). A port that adorned a panel by wrapping it, or by
   making the layer a flex item, would move the panel's own box; this takes the class and
   the layer off, measures the box underneath, and puts both back. It is last among the
   feed's probes so that nothing below measures a page in a state this created. */
out.feed.layoutUnchanged = await evaluate(`(() => {
  const out = [];
  const box = (el, style) => [
    Math.round(el.getBoundingClientRect().width),
    Math.round(el.getBoundingClientRect().height),
    style.paddingTop, style.paddingLeft, style.borderTopWidth, style.borderRadius
  ].join('|');
  for (const panel of document.querySelectorAll('[data-glow]')) {
    const edge = panel.querySelector(':scope > .edge-light');
    const before = panel.getBoundingClientRect();
    const beforeBox = box(panel, getComputedStyle(panel));
    const beforeDisplay = getComputedStyle(panel).display;
    const beforeChildren = panel.children.length;
    panel.classList.remove('border-glow');
    edge.remove();
    const afterBox = box(panel, getComputedStyle(panel));
    const afterDisplay = getComputedStyle(panel).display;
    panel.classList.add('border-glow');
    panel.prepend(edge);
    const after = panel.getBoundingClientRect();
    out.push({
      name: panel.className.split(' ')[0],
      same: beforeBox === afterBox && before.width === after.width && before.height === after.height,
      displayWithClass: beforeDisplay,
      displayWithoutClass: afterDisplay,
      before: beforeBox,
      after: afterBox,
      childrenBefore: beforeChildren,
      childrenAfter: panel.children.length
    });
  }
  return out;
})()`);

/* ------------------------------------------------------------- the catalogue -- */

at = "/";
await send("Page.navigate", { url: `${BASE}/` });
const sphereUp = await waitFor(
  "document.readyState === 'complete' && (!!document.querySelector('.infinite-menu canvas') || !!document.querySelector('.infinite-menu__fallback'))"
);
await sleep(1500);

out.catalogue = {
  ready: sphereUp,
  path: await evaluate("location.pathname"),
  canvas: await evaluate("!!document.querySelector('.infinite-menu canvas')"),
  fallback: await evaluate("!!document.querySelector('.infinite-menu__fallback')"),
  /* Sampled rather than inferred from what was built: a browser with no WebGL2 gets the
     list, which is this page working, and the harness has to be able to tell that state
     from a sphere that failed to draw. */
  webgl2: await evaluate("!!document.createElement('canvas').getContext('webgl2')"),
  status: await evaluate("(document.getElementById('mb-status') || {}).textContent || ''"),
  count: await evaluate("(document.getElementById('mb-count') || {}).textContent || ''"),
  title: await evaluate("document.title")
};

/* The card the sphere is showing, and the address it offers: the harness does not invent a
   URL to test, it follows the one the page published. The sphere keeps one card element
   and rewrites it for whichever match is in front, so this is the front match rather than
   a list — the arrow-key section below is what shows it is not frozen. */
out.catalogue.cards = await evaluate(`(() => ({
  cards: [...document.querySelectorAll('.mb-card__open')].map((a) => ({
    kind: 'card', href: a.getAttribute('href'), text: a.textContent.trim()
  })),
  rows: [...document.querySelectorAll('.infinite-menu__fallback-row')].map((row) => ({
    kind: 'row', href: null, text: row.textContent.trim()
  }))
}))()`);

/* The stage lit by the same port as the feed's panels, told which colour its interior ends
   at because it is a radial gradient rather than a flat background. */
out.catalogue.stageGlow = await evaluate(`(() => {
  const stage = document.querySelector('[data-mode-browser]');
  const style = getComputedStyle(stage);
  return {
    glow: stage.classList.contains('border-glow'),
    layers: stage.querySelectorAll(':scope > .edge-light').length,
    cardBg: style.getPropertyValue('--card-bg').trim(),
    radius: style.getPropertyValue('--border-radius').trim(),
    display: style.display
  };
})()`);

/* The list, reachable. A canvas that will never be drawn is invisible but not transparent
   to the pointer, so a fallback that left one over the list would look right and take every
   click aimed at a match — which is the one thing this path exists to keep working. Asked of
   the browser rather than read off the stylesheet: elementFromPoint is the browser's own
   answer to "what would this click land on". */
out.catalogue.fallbackHitTest = !out.catalogue.fallback ? null : await evaluate(`(() => {
  const button = document.querySelector('.infinite-menu__fallback-btn');
  if (!button) return { button: null, topmost: null, reachable: false };
  const box = button.getBoundingClientRect();
  const topmost = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return {
    button: button.textContent.trim().slice(0, 48),
    topmost: topmost ? topmost.className || topmost.tagName : null,
    reachable: Boolean(topmost) && (topmost === button || button.contains(topmost) || topmost.contains(button))
  };
})()`);

/* Whether the sphere drew anything: a band across the middle of the stage, counted for
   how many distinct colours it holds. An empty rectangle is one colour, a card of text and
   scorelines is many — and this is the claim "the sphere is up" that no DOM read can make,
   because the drawing is in a WebGL buffer nothing in the DOM describes. */
out.catalogue.stagePixels = await (async () => {
  const stage = await evaluate(`(() => {
    const r = document.querySelector('[data-mode-browser]').getBoundingClientRect();
    return { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
  })()`);
  const clip = {
    x: Math.max(0, stage.x),
    y: Math.max(0, stage.y),
    width: Math.max(1, Math.min(stage.width, VIEWPORT.width)),
    height: Math.max(1, Math.min(stage.height, VIEWPORT.height))
  };
  const data = await capture(clip);
  return {
    clip,
    ...(await evaluate(`(async () => {
      const image = await createImageBitmap(await (await fetch("data:image/png;base64," + ${JSON.stringify(data)})).blob());
      const canvas = document.createElement("canvas");
      canvas.width = image.width; canvas.height = image.height;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(image, 0, 0);
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      const colours = new Set();
      let sum = 0, count = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        colours.add((pixels[i] << 16) | (pixels[i + 1] << 8) | pixels[i + 2]);
        sum += pixels[i] + pixels[i + 1] + pixels[i + 2];
        count += 3;
      }
      let brightest = 0;
      for (const value of colours) brightest = Math.max(brightest, ((value >> 16) & 255) + ((value >> 8) & 255) + (value & 255));
      return { colours: colours.size, meanChannel: Number((sum / count).toFixed(2)), brightestSum: brightest };
    })()`))
  };
})();

/* The sphere answers the arrow keys: with reduced motion emulated it holds still until it
   is told not to, so a change of the front card is the key and nothing else. */
out.catalogue.arrows = !out.catalogue.canvas
  ? { before: null, after: null, skipped: "no WebGL2, so there is no sphere to step" }
  : await (async () => {
      const before = await evaluate("document.querySelector('.mb-card__open').getAttribute('href')");
      for (let i = 0; i < 6; i++) {
        await press("ArrowRight", "ArrowRight", 39);
        await sleep(120);
      }
      await sleep(900);
      return {
        before,
        after: await evaluate("document.querySelector('.mb-card__open').getAttribute('href')")
      };
    })();

/* The two addresses the page published, followed from inside the page: the card offers
   `/match/<key>`, so the match behind that key is what the card opens — and the page at
   that address is the feed, served rather than redirected away from. This is the harness
   reading the page's own output rather than a URL the test made up. */
const addresses = [...new Set([out.catalogue.arrows.before, out.catalogue.arrows.after].filter(Boolean))];
/* Every string in the injected expression is single-quoted: this file is a template
   literal, and a double quote inside one is a double quote in the page's own source. */
out.catalogue.addresses = addresses.length === 0 ? [] : await evaluate(`(async () => {
  const out = [];
  for (const href of ${JSON.stringify(addresses)}) {
    const key = href.split('?')[0].split('/')[2];
    const replay = await fetch('/api/replay?scenario=' + encodeURIComponent(key));
    const doc = await replay.json();
    const page = await fetch(href);
    const body = await page.text();
    out.push({
      href, key,
      replayStatus: replay.status,
      seed: doc.seed,
      match: doc.home + ' v ' + doc.away,
      pageStatus: page.status,
      contentType: page.headers.get('content-type'),
      /* The feed's own list, and not just an HTML 200: the address has to serve the page
         a reader is promised, which is the one with a commentary feed in it. */
      pagePath: body.includes('id="feed-list"') ? 'feed' : 'not the feed'
    });
  }
  return out;
})()`);

/* ---------------------------------------------------- a face with no axes --- */

/* The pressure type's whole effect is a variable font being asked for a weight it can
   answer. On a face with no axes every `font-variation-settings` is accepted and ignored,
   which is the failure that looks like a page working: the word is written, the module
   loaded, nothing anywhere says the swell is not there.

   So the port is mounted on its own hidden host — off the layout, in a face this machine
   has and that does not vary — and asked what it is. A live face has to say `live` and
   have written its settings; a flat one has to say `flat`, write nothing, and still leave
   the word readable once. The readings are a list because which faces vary is the
   machine's answer, not this file's: the assertion that matters is the contract between
   the axes found and the state reported. */
out.pressure = await evaluate(`(async () => {
  const { createTextPressure } = await import('/static/text-pressure.js');
  const candidates = ['monospace', '"Courier New", Courier, monospace', 'serif'];
  const readings = [];
  for (const family of candidates) {
    const host = document.createElement('span');
    host.textContent = 'Flat face';
    host.style.cssText =
      'position:fixed;left:8px;top:8px;opacity:0;pointer-events:none;font-family:' + family + ';font-size:20px';
    document.body.appendChild(host);
    const instance = createTextPressure(host, { fit: false });
    const axes = instance.axes();
    const box = host.getBoundingClientRect();
    window.dispatchEvent(new PointerEvent('pointermove', {
      clientX: box.left + box.width / 2,
      clientY: box.top + box.height / 2,
      bubbles: true
    }));
    await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    const letters = [...host.querySelectorAll('.text-pressure__letter')];
    readings.push({
      family: family,
      axes: axes,
      pressure: host.dataset.pressure || null,
      letters: letters.length,
      written: letters.filter((span) => span.style.fontVariationSettings).length,
      sample: (letters[0] && letters[0].style.fontVariationSettings) || '',
      say: (host.querySelector('.text-pressure__say') || {}).textContent || ''
    });
    instance.destroy();
    host.remove();
  }
  return readings;
})()`);

out.problems = problems;
console.log(JSON.stringify(out, null, 2));
ws.close();
