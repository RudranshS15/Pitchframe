/* Pitchframe overlay — the mode browser.
 *
 * The page that makes the catalogue reachable. Before this, the named matches existed and
 * were tested, but the only way to open one was to type it: `?seed=28` into the feed's URL,
 * `--seed 28` on the command line. A catalogue nobody can browse is a list, and the mode
 * browser is the sphere it is browsed on.
 *
 * It fetches /api/scenarios and does three things with it, in this order:
 *
 *   Draws a card per match into the tile atlas, from that match's own numbers. No binary
 *   assets, no network beyond the catalogue itself, and nothing on a card that the
 *   catalogue does not carry — the score and the minutes are facts, and the goals' minutes
 *   are drawn as neutral ticks rather than split by side, because which side scored in the
 *   20th minute is not something /api/scenarios says. Colouring those ticks would be this
 *   page inventing a fact, which is the one thing every other file here refuses to do.
 *
 *   Opens the front card's match when it is clicked, by navigating to that match's own
 *   address: `/match/<key>`, the one the catalogue carries. The sphere decides nothing
 *   about the replay — it follows an address the server composed — and the feed does what
 *   it always did with it. The two pages are two paths — this one is `/` and a match is
 *   `/match/<key>` — because they do different jobs and only one of them has a frame worth
 *   sharing.
 *
 *   Says what it built. The sphere needs WebGL2, and a reader whose browser has none gets
 *   the component's own list instead — a line of status says which of the two is on screen,
 *   so the fallback list cannot be mistaken for the design.
 */

import { createBorderGlow } from "./border-glow.js";
import { createInfiniteMenu } from "./infinite-menu.js";

/* The fault a match is opened with travels with the page: a browser opened on the honest
   baseline opens honest matches, and one opened with the narrator fault planted keeps it,
   which is what makes a link to this page reproducible. */
const params = new URLSearchParams(location.search);
const fault = params.get("fault") || "none";

const stage = document.querySelector("[data-mode-browser]");
const statusLine = document.getElementById("mb-status");
const countLine = document.getElementById("mb-count");

/* Colours and faces come from the stylesheet, the way slats.js takes the sea's palette:
   the page's tokens are the single place a colour is decided, and a canvas that hard-coded
   the same hex would be a second place that could disagree. */
const palette = getComputedStyle(document.documentElement);
const token = (name, fallback) => palette.getPropertyValue(name).trim() || fallback;
const INK = token("--chalk", "#e9eff5");
const MUTED = token("--flint", "#7c8a9c");
const NIGHT = token("--night", "#070a0f");
const TURF = token("--turf", "#0e141c");
const RULE = token("--rule-hard", "rgba(233,239,245,.18)");
const DISPLAY = `${token("--display", "Impact, sans-serif")}`;
const BODY = `${token("--body", "system-ui, sans-serif")}`;
const MONO = `${token("--mono", "monospace")}`;

/* The stage is this page's one panel, and it lights its boundary the way the feed's seven
   do. Its interior is a radial gradient rather than a flat colour, so the mask the port
   fills the card with is told which colour that gradient ends at — read off the same
   token the stylesheet draws it with, so the two cannot drift. `glowRadius: 0` for the
   same reason the feed's panels use it: the stage clips the sphere, and a halo cut off at
   the panel's own border would be a glow with a haircut. */
createBorderGlow(stage, {
  glowRadius: 0,
  coneSpread: 32,
  backgroundColor: token("--night", "#070A0F")
});

const MINUTES_IN_A_MATCH = 90;

/**
 * `text`, cut to `max` pixels of width with an ellipsis.
 *
 * A card is 512 pixels square and the names in this project are not short, so a long name
 * has to be cut rather than allowed to run off the tile. Cutting it here is honest — the
 * card beside the sphere prints both names in full — and invented abbreviations would not
 * be: "Northgate Athletic" and "Northgate" are different claims about what a team is called.
 */
function fitText(ctx, text, max) {
  if (ctx.measureText(text).width <= max) return text;
  let cut = text;
  while (cut.length > 1 && ctx.measureText(`${cut}\u2026`).width > max) {
    cut = cut.slice(0, -1);
  }
  return `${cut.trimEnd()}\u2026`;
}

/**
 * One tile: a match card drawn from the catalogue's own numbers.
 *
 * Called by the port with a 2D context already translated and clipped to this item's atlas
 * cell, so (0, 0)-(size, size) is the tile and anything drawn outside it is discarded.
 */
function poster(ctx, size, entry) {
  const [home, away] = entry.score;

  ctx.fillStyle = NIGHT;
  ctx.fillRect(0, 0, size, size);

  /* Mow stripes, the same device the match bar uses: raised turf read as bands, which is
     what makes a set of these look like a tournament rather than a spreadsheet. */
  ctx.fillStyle = TURF;
  for (let band = 0; band < 8; band += 2) {
    ctx.fillRect(0, (band * size) / 8, size, size / 8);
  }

  ctx.strokeStyle = RULE;
  ctx.lineWidth = Math.max(1, size * 0.006);
  ctx.strokeRect(size * 0.04, size * 0.04, size * 0.92, size * 0.92);

  ctx.textBaseline = "alphabetic";

  /* The name, and the number that opens the match, along the top. */
  ctx.fillStyle = MUTED;
  ctx.font = `500 ${size * 0.062}px ${MONO}`;
  ctx.textAlign = "left";
  ctx.fillText(fitText(ctx, entry.label.toUpperCase(), size * 0.55), size * 0.1, size * 0.15);
  ctx.textAlign = "right";
  ctx.fillText(`SEED ${entry.seed}`, size * 0.9, size * 0.15);

  /* The scoreline, which is what a reader picks a match by. */
  ctx.fillStyle = INK;
  ctx.font = `600 ${size * 0.26}px ${DISPLAY}`;
  ctx.textAlign = "center";
  ctx.fillText(`${home}\u2013${away}`, size * 0.5, size * 0.48);

  ctx.fillStyle = MUTED;
  ctx.font = `400 ${size * 0.056}px ${BODY}`;
  ctx.fillText(
    fitText(ctx, `${entry.home}  v  ${entry.away}`, size * 0.82),
    size * 0.5,
    size * 0.585
  );

  /* What the match contains, counted. The same two numbers the note in /api/scenarios
     spells out, in the face every machine number on this page is set in. */
  ctx.fillStyle = MUTED;
  ctx.font = `500 ${size * 0.05}px ${MONO}`;
  ctx.textAlign = "left";
  ctx.fillText(`${entry.goals} GOALS`, size * 0.1, size * 0.715);
  ctx.textAlign = "right";
  ctx.fillText(`${entry.long_build_ups} LONG BUILDS`, size * 0.9, size * 0.715);

  /* The goal timeline. One tick per goal, placed by its minute and crossing the rule, in
     the order the goals fell — the shape of the match in one glance, and the one thing on
     this card that a scoreline cannot say. */
  const left = size * 0.1;
  const right = size * 0.9;
  const axis = size * 0.8;
  ctx.strokeStyle = RULE;
  ctx.lineWidth = Math.max(1, size * 0.005);
  ctx.beginPath();
  ctx.moveTo(left, axis);
  ctx.lineTo(right, axis);
  ctx.stroke();

  ctx.strokeStyle = INK;
  ctx.lineWidth = Math.max(2, size * 0.011);
  for (const minute of entry.minutes) {
    const at = left + (Math.min(MINUTES_IN_A_MATCH, Math.max(0, minute)) / MINUTES_IN_A_MATCH) * (right - left);
    ctx.beginPath();
    ctx.moveTo(at, axis - size * 0.032);
    ctx.lineTo(at, axis + size * 0.032);
    ctx.stroke();
  }

  /* The moment the award is made is not on this card, and neither is the replay. The card
     is a door: it carries what a reader needs to choose and nothing they have to read
     twice once the match is open. */
  if (entry.default) {
    ctx.fillStyle = MUTED;
    ctx.font = `500 ${size * 0.044}px ${MONO}`;
    ctx.textAlign = "center";
    ctx.fillText("OPENS BY DEFAULT", size * 0.5, size * 0.89);
  }
}

/** One catalogue entry as the component's item, plus the fields the card art reads. */
function asItem(entry) {
  /* The card's address, from the catalogue rather than composed here: the server composes
     `/match/<key>` beside the entry it describes, so the site's URLs are decided in one
     place and a card cannot offer an address the server does not serve. The fault travels
     with it, because a catalogue browsed with the fault planted should open matches that
     still have it. */
  const href = `${entry.path}${fault === "none" ? "" : `?fault=${encodeURIComponent(fault)}`}`;
  return {
    ...entry,
    /* `title` and `description` are the component's own block, used when a caller reads
       the module without replacing it. `link` is the door the built-in action button
       follows; `href` is what this page's own card and its tile handler use. */
    title: `${entry.home} ${entry.score[0]}\u2013${entry.score[1]} ${entry.away}`,
    description: entry.note,
    link: href,
    href,
    draw: (ctx, size) => poster(ctx, size, entry)
  };
}

/**
 * The card beside the sphere: what the front match is, and the way into it.
 *
 * Replaces the component's own title/description/button block through `renderInfo`, which
 * is the prop that exists for exactly this. The block upstream puts a title on the ball's
 * left and a description on its right, sized for a gallery's one-line caption; a match has
 * more to say than that, so this card keeps the same two columns and splits the facts the
 * way the component splits them — who is playing and the score on the left, what happened
 * on the right — with the way in at the foot. The sphere still owns everything about how
 * any of it moves, because the port writes `data-moving` and `data-layout` to this
 * container either way.
 *
 * The left column is the identity and the right is the evidence, which is the same division
 * the feed makes between its scoreboard and its check panel.
 */
function card(entry) {
  const wrap = document.createElement("div");
  wrap.className = "mb-card";
  if (entry.default) wrap.classList.add("mb-card--default");

  const left = document.createElement("div");
  left.className = "mb-card__left";

  const eyebrow = document.createElement("p");
  eyebrow.className = "mb-card__eyebrow";
  eyebrow.append(document.createTextNode("scenario "));
  const name = document.createElement("b");
  name.textContent = entry.key;
  eyebrow.append(name, document.createTextNode(` \u00b7 seed ${entry.seed}`));
  if (entry.default) {
    const badge = document.createElement("span");
    badge.className = "mb-card__badge";
    badge.textContent = "opens by default";
    eyebrow.append(badge);
  }

  const score = document.createElement("p");
  score.className = "mb-card__score";
  score.textContent = `${entry.score[0]}\u2013${entry.score[1]}`;

  const teams = document.createElement("p");
  teams.className = "mb-card__teams";
  teams.textContent = `${entry.home} v ${entry.away}`;

  left.append(eyebrow, score, teams);

  const right = document.createElement("div");
  right.className = "mb-card__right";

  /* The two counts, in the face every machine number on this page is set in, then the note
     the catalogue wrote — which is the sentence the tests recount from the generator, so
     nothing here is a description the page composed. */
  const counts = document.createElement("p");
  counts.className = "mb-card__counts";
  counts.textContent =
    `${entry.goals} goal${entry.goals === 1 ? "" : "s"} \u00b7 ${entry.long_build_ups} from ` +
    `long build-ups`;

  const note = document.createElement("p");
  note.className = "mb-card__note";
  note.textContent = entry.note;

  const when = document.createElement("p");
  when.className = "mb-card__when";
  when.textContent = entry.minutes.length
    ? `goals in the ${entry.minutes.map((minute) => `${minute}'`).join(", ")}`
    : "no goals, which is its own kind of match";

  right.append(counts, note, when);

  const open = document.createElement("a");
  open.className = "mb-card__open";
  open.href = entry.href;
  open.textContent = "Open this match";

  wrap.append(left, right, open);
  return wrap;
}

function say(message) {
  if (statusLine) statusLine.textContent = message;
}

async function boot() {
  if (!stage) return;

  let entries;
  try {
    const response = await fetch("/api/scenarios", { headers: { accept: "application/json" } });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `the server answered ${response.status}`);
    }
    const payload = await response.json();
    entries = payload.scenarios || [];
  } catch (error) {
    /* The same failure the feed reports, in the same words: a server that is not running
       is the only thing that can be wrong here, and saying which command fixes it is more
       use than saying that a fetch failed. */
    say(
      `Could not load the catalogue: ${error.message}. The server must be running — ` +
      `start it with "python -m pitchframe --overlay".`
    );
    stage.classList.add("mb__stage--empty");
    return;
  }

  if (!entries.length) {
    say("The catalogue is empty, so there is nothing to browse.");
    return;
  }

  /* The tiles are drawn from text, so they are drawn once: a face that arrives after the
     atlas was filled would leave every card in the fallback face for the life of the page.
     A browser with no font loading API at all has no such problem to wait on. */
  if (document.fonts && document.fonts.ready) {
    await document.fonts.ready.catch(() => {});
  }

  const items = entries.map(asItem);
  const menu = createInfiniteMenu(stage, {
    items,
    /* Three tiles per match: enough that the same card comes round again as a reader spins
       without the sphere filling up with it. Upstream's 160 is a gallery's number. */
    count: Math.max(24, items.length * 3),
    tileSize: 0.66,
    zoom: 1.75,
    autoplay: 5,
    intro: true,
    grayscale: true,
    dim: 0.55,
    theme: "dark",
    renderInfo: (item) => card(item),
    onActiveChange: (item) => {
      document.title = `${item.home} v ${item.away} — Pitchframe mode browser`;
    },
    onItemClick: (item) => {
      location.assign(item.href);
    }
  });

  if (countLine) {
    /* Summed from the cards rather than described in prose, and it is the same arithmetic
       the tiles draw: what the whole catalogue contains, which is the question a reader has
       before they have chosen anything. */
    const goals = entries.reduce((total, entry) => total + entry.goals, 0);
    const builds = entries.reduce((total, entry) => total + entry.long_build_ups, 0);
    countLine.textContent =
      `${entries.length} matches · ${goals} goals · ${builds} of them from build-ups of ` +
      `five touches or more`;
  }

  say(
    menu.fallback
      ? `This browser has no WebGL2, so the sphere cannot be drawn. The same ${entries.length} ` +
        `matches are listed below, and every one of them opens the same replay.`
      : `Drag to spin, click the front card to open it. Arrow keys step, Enter opens, and ` +
        `nothing is measured here — every card is a seed the feed already knows.`
  );

  /* The sphere keeps a frame budget only while it is on screen and idle only while nothing
     is approaching it; waking it on a resize the observer did not see (a font landing, a
     zoom) is cheap and stops a stale layout. */
  window.addEventListener("resize", () => menu.wake(), { passive: true });
}

boot();
