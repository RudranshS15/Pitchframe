/* Pitchframe overlay — the browser half.
 *
 * This file decides nothing. The whole replay arrives as one JSON document that was
 * computed deterministically on the server, and everything here is presentation: a
 * clock, a reveal cursor, and templates. That is deliberate. A demo that computes
 * anything in the browser has a way to fail on stage that a demo reading a finished
 * document does not.
 *
 * Two pieces of behaviour are worth reading closely:
 *
 *   The reveal cursor. Items are held in one chronological list and released as the
 *   match clock passes them, so the page assembles itself in the order things
 *   happened rather than appearing complete.
 *
 *   The check panel. It shows whichever row was released last, which means a
 *   rejection is followed a beat later by its own correction — the sequence the
 *   product exists to demonstrate — without any special-casing for it.
 *
 *   The URL as the frame. The view, the minute the reader stopped at and the build-up
 *   they have open are mirrored into the query string, so the frame in front of them
 *   can be handed to somebody else — which is what a bug report is. The parameters are
 *   read back at boot and the page opens there, paused.
 *
 * It is a module rather than a classic script because it imports the three ported controls:
 * the segmented control (RubberSegment) drawing the view switch and the replay speed, the
 * dock drawing the actions at the foot of the viewport, and the wake slider (WakeSlider)
 * seeking through the match. Each is given its state from here and reports changes back
 * through setView/setSpeed/setPlaying/seekTo, so the panels, the transport and the dock are
 * views of one state rather than states of their own.
 */

import { createDock } from "./dock.js";
import { createRubberSegment } from "./rubber-segment.js";
import { createWakeSlider } from "./wake-slider.js";

(() => {
  "use strict";

  /* At 1x, one real second is one match minute. Slow enough to read, fast enough
     to reach a decision in a two-minute pitch. */
  const MATCH_SECONDS_PER_REAL_SECOND = 60;

  /* Auto-scroll only when the reader is already near the end of the feed. Yanking
     the page while somebody is reading an earlier line is worse than a stale view. */
  const NEAR_BOTTOM_PX = 240;

  const params = new URLSearchParams(location.search);
  const query = {
    seed: params.get("seed") || "7",
    match_id: params.get("match_id") || "PF-001",
    fault: params.get("fault") || "none",
    view: params.get("view") === "analyst" ? "analyst" : "fan",
    /* These two place the reader rather than parameterise the request: `at` is a match
       second and `goal` names a build-up to open. Neither reaches the server, and the
       fetch below sends only the three above. Kept raw here because an absent one has
       to stay distinguishable from a present zero — see restoreFrame. */
    at: params.get("at"),
    goal: params.get("goal"),
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    provider: $("provider"), providerText: $("provider-text"),
    teamHome: $("team-home"), teamAway: $("team-away"),
    score: $("score"), clock: $("clock"), clockState: $("clock-state"),
    shareBar: $("share-bar"), shareHome: $("share-home-pct"), shareAway: $("share-away-pct"),
    play: $("play"), seek: $("seek"), rateNote: $("rate-note"),
    viewSeg: $("view-seg"), speedSeg: $("speed-seg"), dockRail: $("dockrail"),
    feedList: $("feed-list"), feedNote: $("feed-note"),
    promo: $("promo"), promoText: $("promo-text"), promoBtn: $("promo-btn"),
    check: $("check"), checkWindow: $("check-window"), checkChip: $("check-chip"),
    checkSaying: $("check-saying"), checkFigures: $("check-figures"),
    figClaimed: $("fig-claimed"), figMeasured: $("fig-measured"),
    checkStamp: $("check-stamp"), checkWhy: $("check-why"),
    ledVerified: $("led-verified"), ledRejected: $("led-rejected"),
    ledCorrections: $("led-corrections"), ledDropped: $("led-dropped"),
    metricsBody: $("metrics-body"), metricsWindow: $("metrics-window"),
    metricsEmpty: $("metrics-empty"),
    squads: $("squads"), footMatch: $("foot-match"), footUrl: $("foot-url"),
    hl: $("hl"), hlTitle: $("hl-title"), hlLabels: $("hl-labels"), hlSvg: $("hl-svg"),
    hlStep: $("hl-step"), hlReplay: $("hl-replay"), hlClose: $("hl-close"),
    hlFigures: $("hl-figures"), hlFoot: $("hl-foot"),
  };

  /* Shown before any claim exists, and again whenever the clock is wound back to the
     start. Without it the panel goes blank on a seek, which reads as a fault. */
  const IDLE_COPY =
    "The most recent claim appears here with the two measurements that decide it — " +
    "what the claim said, and what the event stream says.";

  const state = {
    doc: null,
    items: [],
    cursor: 0,
    clock: 0,
    minClock: 0,
    maxClock: 0,
    playing: false,
    speed: 1,
    lastTs: 0,
    /* Every goal's build-up, keyed so a click on a timeline row can find it. Keyed
       rather than indexed because the timeline and the highlight list are built in
       different places and an index would be a silent coupling between them. */
    goals: new Map(),
  };

  /* One key for "which goal is this", used on both sides of the lookup. Clock plus
     side plus scorer is unique within a match: one player cannot score twice on the
     same second for the same team. */
  const goalKey = (clock, team, player) => `${clock}|${team}|${player}`;

  /* ------------------------------------------------------------- helpers ----- */

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const minuteOf = (seconds) => Math.floor(seconds / 60);

  const signed = (value, digits = 2) =>
    value === null || value === undefined
      ? "—"
      : (value >= 0 ? "+" : "-") + Math.abs(value).toFixed(digits);

  const metricLabel = (row) => row.metric.replace(/_/g, " ");

  /* A verdict stores the claimed magnitude as a positive number and carries the
     direction separately, and discrepancy() signs it by that direction. Reading the
     raw magnitude here would print "down +0.07" beside a measured "down -0.07" — the
     same claim rendered as two different numbers. */
  /* Direction as a glyph, so the reading is scannable without implying that one way
     is better than the other. */
  const arrow = (reading) =>
    reading.is_flat ? "·" : reading.direction === "up" ? "▲" : "▼";

  const claimedSigned = (row) => {
    if (row.claimed_magnitude === null || row.claimed_magnitude === undefined) return null;
    const size = Math.abs(row.claimed_magnitude);
    return row.claimed_direction === "down" ? -size : size;
  };

  const sideName = (team) =>
    team === "home" ? state.doc.home : team === "away" ? state.doc.away : "";

  /* --------------------------------------------------------------- render ---- */

  function renderProvider(provider) {
    el.providerText.textContent = provider.banner;
    el.provider.title = provider.reason;
    el.provider.classList.toggle("provider--live", Boolean(provider.uses_a_model));
  }

  /* Build one chronological list from the document. Incidents and the analysis of a
     window share a minute often; incidents sort first because they are the events the
     analysis is about. */
  function buildItems(doc) {
    const items = [];

    /* Goals come from the uncapped goal list, not from the incident lists, which are
       capped for legibility. A goal dropped by that cap would vanish from the timeline
       while the scoreboard still moved, and it would leave nothing to click. Taking
       them from here also means each goal appears exactly once. */
    doc.goals.forEach((goal) => {
      items.push({
        clock: goal.clock,
        order: 0,
        kind: "goal",
        data: goal,
        highlight: state.goals.get(goalKey(goal.clock, goal.team, goal.player)) || null,
      });
    });

    doc.frames.forEach((frame) => {
      frame.incidents.forEach((incident) => {
        if (incident.type === "goal") return;
        items.push({ clock: incident.clock, order: 1, kind: "incident", data: incident, frame });
      });
      frame.feed.forEach((row) => {
        items.push({ clock: frame.clock, order: 2, kind: "claim", data: row, frame });
      });
    });
    items.sort((a, b) => a.clock - b.clock || a.order - b.order);
    return items;
  }

  function incidentMarkup(incident) {
    return `<li class="row row--incident">
      <p class="row__clock">${minuteOf(incident.clock)}&prime;</p>
      <div class="row__body">
        <p class="row__mark">
          <span class="tag tag--${esc(incident.type)}">${esc(incident.type)}</span>
          <span class="row__side">
            <span class="row__dot row__dot--${esc(incident.team)}"></span>
            ${esc(incident.player)} &middot; ${esc(sideName(incident.team))}
          </span>
        </p>
      </div>
    </li>`;
  }

  /* A goal is the one row that opens something, so it is the one row rendered as a
     control. Everything about it still comes from the payload — the scorer, the side
     and the minute are the goal record's own fields, and the cue is only shown when a
     build-up exists to open. */
  function goalMarkup(goal, highlight) {
    if (!highlight) return incidentMarkup(goal);
    const key = goalKey(goal.clock, goal.team, goal.player);
    return `<li class="row row--incident row--openable">
      <p class="row__clock">${minuteOf(goal.clock)}&prime;</p>
      <div class="row__body">
        <button type="button" class="row__open" data-goal="${esc(key)}"
                aria-label="Show the build-up to ${esc(goal.player)}&rsquo;s goal at ${minuteOf(goal.clock)} minutes">
          <span class="tag tag--goal">goal</span>
          <span class="row__side">
            <span class="row__dot row__dot--${esc(goal.team)}"></span>
            ${esc(goal.player)} &middot; ${esc(sideName(goal.team))}
          </span>
          <span class="row__cue">build-up</span>
        </button>
      </div>
    </li>`;
  }

  function claimMarkup(row, frame) {
    const rejected = row.status === "rejected";
    const corrected = row.kind === "correction";
    const variant = rejected ? "row--rejected" : corrected ? "row--correction" : "row--verified";
    const tag = rejected ? "rejected" : corrected ? "corrected" : "verified";
    const planted = row.injected
      ? `<span class="tag tag--planted">planted fault</span>`
      : "";
    const side = row.team
      ? `<span class="row__side"><span class="row__dot row__dot--${esc(row.team)}"></span>${esc(sideName(row.team))}</span>`
      : "";

    return `<li class="row row--claim ${variant}">
      <p class="row__clock">${minuteOf(frame.clock)}&prime;</p>
      <div class="row__body">
        <p class="row__mark">
          <span class="tag">${tag}</span>
          <span>${esc(metricLabel(row))}</span>
          ${side}${planted}
        </p>
        <p class="row__text">${esc(row.text)}</p>
        ${row.receipt ? `<p class="row__receipt">${esc(row.receipt)}</p>` : ""}
        ${rejected && row.reason ? `<p class="row__why">${esc(row.reason)}</p>` : ""}
      </div>
    </li>`;
  }

  /* The signature panel. It always shows the newest row, so a rejected claim is
     followed by its correction one beat later with no special handling. */
  function renderCheck(row, frame) {
    const rejected = row.status === "rejected";
    const corrected = row.kind === "correction";

    el.check.dataset.state = rejected ? "rejected" : corrected ? "correction" : "verified";
    el.checkWindow.textContent =
      `Window ${minuteOf(frame.window[0])}′–${minuteOf(frame.window[1])}′ · ${metricLabel(row)}`;
    el.checkSaying.textContent = row.text;

    el.checkChip.hidden = !row.injected;
    el.checkChip.textContent = row.injected ? "planted fault" : "";

    el.checkFigures.hidden = false;
    el.figClaimed.textContent =
      `${row.claimed_direction || "—"} ${signed(claimedSigned(row))}`;
    el.figMeasured.textContent =
      row.measured_direction
        ? `${row.measured_direction} ${signed(row.delta)}`
        : "not measurable";

    el.checkStamp.hidden = false;
    el.checkStamp.textContent = rejected ? "blocked" : corrected ? "corrected" : "verified";
    /* Re-trigger the stamp animation: a new row should stamp again, and reusing the
       same element means the animation has to be explicitly restarted. */
    el.checkStamp.style.animation = "none";
    void el.checkStamp.offsetWidth;
    el.checkStamp.style.animation = "";

    el.checkWhy.hidden = !rejected;
    el.checkWhy.textContent = rejected
      ? (row.injected
          ? `This direction was planted on purpose to demonstrate the check. ${row.reason}`
          : row.reason)
      : "";
  }

  function latestFrame(clock) {
    const frames = state.doc.frames;
    let found = null;
    for (const frame of frames) {
      if (frame.clock <= clock) found = frame;
      else break;
    }
    return found;
  }

  function renderBoard(clock) {
    const doc = state.doc;
    const minute = Math.min(minuteOf(Math.max(clock, 0)), 90 + Math.floor(Math.max(0, clock - 5400) / 60));

    el.clock.innerHTML = `${minute}<i>&prime;</i>`;

    const goals = doc.goals.filter((g) => g.clock <= clock);
    const home = goals.filter((g) => g.team === "home").length;
    const away = goals.filter((g) => g.team === "away").length;
    /* The same rule the scoreboard follows, applied to the dock's build-up menu: it offers
       only the goals the replay has already reached. A menu naming a goal that has not
       happened yet would spoil the replay the page exists to show. Rebuilt only when the
       count changes, so a bar under the pointer is not repainted every frame. */
    if (goals.length !== dockGoals) {
      dockGoals = goals.length;
      renderDock();
    }
    el.score.innerHTML = `${home}<i>–</i>${away}`;
    el.score.setAttribute("aria-label", `Score ${home} to ${away}`);

    const frame = latestFrame(clock);
    const possession = frame
      ? frame.readings.find((r) => r.key === "possession_share@home")
      : null;
    const share = possession ? possession.value : 0.5;
    el.shareBar.style.width = `${(share * 100).toFixed(1)}%`;
    el.shareHome.textContent = possession ? `${(share * 100).toFixed(0)}%` : "–";
    el.shareAway.textContent = possession ? `${((1 - share) * 100).toFixed(0)}%` : "–";

    renderLedger(frame);

    if (frame) {
      const claimed = new Set(frame.feed.map((r) => r.metric));
      renderMetrics(frame, claimed);
    } else {
      renderMetrics(null, new Set());
    }
  }

  function renderLedger(frame) {
    const ledger = frame ? frame.ledger : { verified: 0, rejected: 0, corrections: 0, dropped: 0 };
    el.ledVerified.textContent = ledger.verified;
    el.ledRejected.textContent = ledger.rejected;
    el.ledCorrections.textContent = ledger.corrections;
    el.ledDropped.textContent = ledger.dropped;

    el.promoText.textContent = ledger.rejected
      ? `${ledger.verified} lines published, ${ledger.rejected} claim${ledger.rejected === 1 ? "" : "s"} caught by measurement and corrected.`
      : `${ledger.verified} line${ledger.verified === 1 ? "" : "s"} published, each one checked against the event stream.`;
  }

  function renderMetrics(frame, claimedKeys) {
    if (!frame || !frame.readings.length) {
      el.metricsBody.innerHTML = "";
      el.metricsWindow.textContent = "";
      el.metricsEmpty.hidden = false;
      return;
    }
    el.metricsEmpty.hidden = true;
    el.metricsWindow.textContent =
      `${minuteOf(frame.window[0])}′–${minuteOf(frame.window[1])}′`;

    /* Ranked the same way the narrator ranks: by relative movement, because the
       metrics do not share a scale and raw deltas would pick whichever happens to
       have the largest units. */
    const rows = frame.readings
      .slice()
      .sort((a, b) =>
        Math.abs(b.delta) / (Math.abs(b.baseline_value) + 1e-6) -
        Math.abs(a.delta) / (Math.abs(a.baseline_value) + 1e-6));

    el.metricsBody.innerHTML = rows
      .map((r) => {
        const state_ = r.is_flat ? "is-flat" : r.direction === "up" ? "is-up" : "is-down";
        const claimed = claimedKeys.has(r.metric) ? " is-claimed" : "";
        const label = r.team ? `${metricLabel(r)} · ${sideName(r.team)}` : metricLabel(r);
        return `<tr class="${state_}${claimed}">
          <td>${esc(label)}</td>
          <td>${r.baseline_value.toFixed(4)}</td>
          <td>${r.value.toFixed(4)}</td>
          <td class="metrics__delta">${arrow(r)} ${signed(r.delta, 4)}</td>
        </tr>`;
      })
      .join("");
  }

  function renderSquads(doc) {
    el.squads.innerHTML = ["home", "away"]
      .map((side) => {
        const players = (doc.squads[side] || [])
          .map((p) => `<li class="squads__player">
              <span class="squads__shirt">${esc(p.shirt)}</span>
              <span>${esc(p.name)}</span>
            </li>`)
          .join("");
        const name = side === "home" ? doc.home : doc.away;
        return `<div class="squads__team">
            <h3>${esc(name)}</h3>
            <ul class="squads__list">${players}</ul>
          </div>`;
      })
      .join("");
  }

  /* ------------------------------------------------------- the goal build-up -- */

  /* The pitch. Coordinates arrive normalised 0-100 on both axes, so each axis is
     scaled onto a real 105x68 pitch here rather than squeezed into a square: at a
     square aspect the picture would not read as a football pitch at all. Everything
     below is drawn in those pitch units.

     Strokes carry vector-effect="non-scaling-stroke" in the CSS, so a line is the
     same weight on a desktop as on a phone. Without it the same 2-unit stroke is a
     smear at 900px and a hairline at 390px. */
  const PITCH_L = 105;
  const PITCH_W = 68;

  const toPitch = (xy) => [xy[0] * PITCH_L / 100, xy[1] * PITCH_W / 100];
  const lerp2 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

  /* How long one step takes at 1x. Slow enough to watch the ball move, fast enough
     that a seven-step move is over in six seconds. */
  const STEP_MS = 850;

  /* Respect the reader's setting for the *automatic* animation. The Replay button
     still plays, because pressing it is an explicit request; nothing starts moving on
     its own for someone who asked it not to. */
  const REDUCED_MOTION =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const hl = {
    data: null,
    /* Completed steps. The routes up to `done` are drawn, and the ball is travelling
       through step `done` by the fraction `t`. */
    done: 0,
    t: 0,
    playing: false,
    speed: 1,
    raf: 0,
    last: 0,
    routes: [],
    ball: null,
  };

  /* Markings only. They say nothing about the match; they are the frame the data is
     read against, so they are drawn from constants rather than from the payload. */
  function pitchMarkings() {
    const cx = PITCH_W / 2;
    const box = (x, y, w, h) => `<rect class="hl__mark" x="${x}" y="${y}" width="${w}" height="${h}"/>`;
    const spot = (x, y) => `<circle class="hl__mark hl__mark--spot" cx="${x}" cy="${y}" r="0.55"/>`;
    return `
      <rect class="hl__mark hl__mark--turf" x="0" y="0" width="${PITCH_L}" height="${PITCH_W}"/>
      <rect class="hl__mark" x="0" y="0" width="${PITCH_L}" height="${PITCH_W}"/>
      <line class="hl__mark" x1="52.5" y1="0" x2="52.5" y2="${PITCH_W}"/>
      <circle class="hl__mark" cx="52.5" cy="${cx}" r="9.15"/>
      ${spot(52.5, cx)}
      ${box(0, cx - 20.16, 16.5, 40.32)}
      ${box(PITCH_L - 16.5, cx - 20.16, 16.5, 40.32)}
      ${box(0, cx - 9.16, 5.5, 18.32)}
      ${box(PITCH_L - 5.5, cx - 9.16, 5.5, 18.32)}
      ${spot(11, cx)}
      ${spot(PITCH_L - 11, cx)}
    `;
  }

  const stepLabel = (step) =>
    `${step.kind}${step.derived ? " (derived)" : ""} \u00b7 ${step.player} ${step.minute}\u2032`;

  /* Everything static about one move: the pitch, then a route per step, then the two
     ends of the move. Built once per opening, then revealed by hlPaint rather than
     rebuilt, so nothing restarts mid-animation. */
  function highlightMarkup(data) {
    const steps = data.steps;
    const routes = steps.map((step, i) => {
      const [x1, y1] = toPitch(step.start_xy);
      const [x2, y2] = toPitch(step.end_xy);
      /* Being derived overrides the kind entirely: an inferred step is drawn as an
         inference (dashed, amber) whatever kind it is pretending to be. A recorded
         carry keeps its own class and is drawn solid and heavier than a pass. */
      const variant = step.derived ? "derived" : step.kind;
      return `<line id="hl-route-${i}" class="hl__route hl__route--${esc(step.team)} hl__route--${esc(variant)}"
                    data-visible="false" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
    }).join("");

    const origin = steps.length ? toPitch(steps[0].start_xy) : null;
    const finish = data.finish_xy ? toPitch(data.finish_xy) : null;
    /* The ball finishes on the goal line, so the marker and the ball are pulled a
       unit inside it — a stroke centred on the line would be half clipped away. */
    const mouthX = finish ? (finish[0] > PITCH_L / 2 ? PITCH_L - 1 : 1) : 0;

    const mouth = finish
      ? `<line class="hl__mark hl__mark--mouth" x1="${mouthX}" y1="${clamp(finish[1] - 5, 1, PITCH_W - 1)}"
               x2="${mouthX}" y2="${clamp(finish[1] + 5, 1, PITCH_W - 1)}"/>`
      : "";
    const mark = finish
      ? `<circle class="hl__goalring" cx="${mouthX}" cy="${finish[1]}" r="2.6"/>
         <circle class="hl__goalmark" cx="${mouthX}" cy="${finish[1]}" r="0.9"/>`
      : "";
    const start = origin
      ? `<circle class="hl__origin" cx="${origin[0]}" cy="${origin[1]}" r="1.9"/>`
      : "";
    const ballAt = steps.length ? toPitch(steps[0].start_xy) : (finish || [0, 0]);

    return `${pitchMarkings()}${mouth}${routes}${start}${mark}
      <circle class="hl__ball" id="hl-ball" cx="${ballAt[0]}" cy="${ballAt[1]}" r="1.7"/>`;
  }

  function hlPaint() {
    const data = hl.data;
    if (!data) return;
    const steps = data.steps;

    /* The route for the step in progress is drawn the moment the ball starts along
       it, not once it arrives. Revealing it only on completion left the ball sliding
       along an invisible line for most of the animation, which read as a rendering
       fault. Everything past the current step stays hidden. */
    hl.routes.forEach((route, i) => {
      route.dataset.visible = i <= hl.done ? "true" : "false";
    });

    let at = null;
    if (hl.done < steps.length) {
      const step = steps[hl.done];
      at = lerp2(toPitch(step.start_xy), toPitch(step.end_xy), hl.t);
    } else if (steps.length) {
      at = toPitch(steps[steps.length - 1].end_xy);
    } else if (data.finish_xy) {
      at = toPitch(data.finish_xy);
    }
    if (at && hl.ball) {
      hl.ball.setAttribute("cx", at[0]);
      hl.ball.setAttribute("cy", at[1]);
    }

    if (!steps.length) {
      el.hlStep.textContent = "Unassisted finish — no preceding possession in the event stream.";
      return;
    }
    const shown = Math.min(hl.done + 1, steps.length);
    el.hlStep.innerHTML =
      `Step <b>${shown}</b> of <b>${steps.length}</b> &middot; ${esc(stepLabel(steps[shown - 1]))}`;
  }

  /* Advance the animation by a slice of real time, possibly across several step
     boundaries at once, so a slow frame cannot make the move take longer than it
     should. Returns true when the move is finished. */
  function hlAdvance(seconds) {
    const total = hl.data.steps.length;
    const perStep = (STEP_MS / 1000) / hl.speed;
    let remaining = seconds;
    while (remaining > 0 && hl.done < total) {
      const left = (1 - hl.t) * perStep;
      if (remaining < left) {
        hl.t += remaining / perStep;
        remaining = 0;
      } else {
        remaining -= left;
        hl.done += 1;
        hl.t = 0;
      }
    }
    return hl.done >= total;
  }

  function hlTick(ts) {
    if (!hl.playing) return;
    if (!hl.last) hl.last = ts;
    /* Capped so a background tab returning to the foreground does not jump the whole
       animation in one frame without ever drawing it. */
    const dt = Math.min((ts - hl.last) / 1000, 0.2);
    hl.last = ts;
    const finished = hlAdvance(dt);
    hlPaint();
    if (finished) {
      hl.playing = false;
      return;
    }
    hl.raf = requestAnimationFrame(hlTick);
  }

  function hlPlay() {
    if (!hl.data) return;
    if (hl.raf) cancelAnimationFrame(hl.raf);
    hl.done = 0;
    hl.t = 0;
    hl.last = 0;
    hl.playing = hl.data.steps.length > 0;
    hlPaint();
    if (hl.playing) hl.raf = requestAnimationFrame(hlTick);
  }

  function hlRest() {
    hl.done = hl.data ? hl.data.steps.length : 0;
    hl.t = 0;
    hl.playing = false;
    hlPaint();
  }

  function hlSetSpeed(speed) {
    hl.speed = speed;
    document.querySelectorAll("[data-hl-speed]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(Number(btn.dataset.hlSpeed) === speed));
    });
  }

  function hlFigure(label, value) {
    return `<div class="hl__fig"><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
  }

  function hlOpen(data) {
    hl.data = data;
    hl.routes = [];
    hl.ball = null;

    const summary = data.summary;
    el.hlTitle.textContent =
      `${data.minute}\u2032 \u00b7 ${data.scorer} \u00b7 ${sideName(data.team)}`;

    /* Straight from the payload's own labels. The page adds no wording of its own, so
       it cannot show a highlight whose provenance went missing in transit. */
    el.hlLabels.innerHTML = data.labels
      .map((label) => `<span class="hl__label${label.indexOf("derived") === 0 ? " hl__label--derived" : ""}">${esc(label)}</span>`)
      .join("");

    el.hlFigures.innerHTML = [
      hlFigure("steps", summary.steps),
      hlFigure("passes", summary.passes),
      hlFigure("carries", summary.carries + (summary.carries && data.labels.includes("derived carries") ? " derived" : "")),
      hlFigure("ball travel", summary.distance.toFixed(1)),
      hlFigure("duration", `${summary.duration}s`),
      hlFigure("started in", summary.start_zone),
    ].join("");

    /* The wording tracks what the stream actually contains. Saying "dashed lines are
       inferred carries" when a match has none would describe a picture that is not on
       screen, and claiming the generator emits no carries stopped being true. */
    const inferred = data.labels.indexOf("derived carries") !== -1;
    el.hlFoot.textContent =
      "Reconstructed from this match's own synthetic event stream. Thicker solid lines were " +
      "carried by the player rather than passed; thin solid lines are passes; the heavy line " +
      "into the goal is the finish." +
      (inferred
        ? " Dashed amber lines were inferred, not recorded, because the ball moved between two " +
          "touches with no event to account for it."
        : " Every step here was recorded — nothing on this path was inferred.") +
      " Distances are in the schema's normalised 0\u2013100 units, not metres, and not from " +
      "tracking data.";

    el.hlSvg.innerHTML = highlightMarkup(data);
    hl.routes = Array.from(el.hlSvg.querySelectorAll(".hl__route"));
    hl.ball = el.hlSvg.querySelector("#hl-ball");

    if (!el.hl.open) el.hl.showModal();
    /* The match clock runs behind a modal, which is a distraction at best. Pressing
       play again is one click and stays the reader's decision. */
    setPlaying(false);
    hlSetSpeed(hl.speed);

    if (REDUCED_MOTION) {
      hlRest();
    } else {
      hlPlay();
    }
    writeShare();
  }

  /* ------------------------------------------------------------------ the URL -- */

  /* The query string describes the frame on screen: which match and fault, which view,
     which match second, and which build-up panel is open. It is written with
     replaceState rather than pushState — a seek is not a navigation, and a history
     entry per scrub would leave the Back button unable to leave the page — and read
     once, at boot.

     A position in the URL always means a paused page. A link that resumed playing would
     put the reader at a different minute on every load, which is the opposite of the
     reproducibility a bug report needs. */
  const SHARE_INTERVAL_MS = 1000;

  /* When the playhead last wrote the URL. Writing per frame would be a history call at
     60 Hz, and some browsers refuse that outright — Safari throws past 100 calls in 30
     seconds — while a second is the resolution this page displays anyway. */
  let lastShareMs = 0;

  function shareUrl() {
    const share = new URLSearchParams();
    share.set("seed", query.seed);
    share.set("match_id", query.match_id);
    share.set("fault", query.fault);
    share.set("view", document.body.dataset.view || query.view);
    share.set("at", String(Math.round(state.clock)));
    /* Only while the panel is actually on screen, and keyed the same way the timeline
       keys it, so the link names a goal in the payload's own vocabulary rather than in
       one of its own. `open` is read rather than `hl.data` alone: the element is the
       only thing that knows the panel is gone, whatever dismissed it. */
    if (el.hl.open && hl.data) {
      share.set("goal", goalKey(hl.data.clock, hl.data.team, hl.data.scorer));
    }
    return `${location.pathname}?${share}`;
  }

  function writeShare({ now = 0 } = {}) {
    /* Nothing is written before the document arrives: a URL claiming seed 7 at 00:00
       describes a frame this page has not drawn yet. */
    if (!state.doc) return;
    if (now && now - lastShareMs < SHARE_INTERVAL_MS) return;
    lastShareMs = now || lastShareMs;
    history.replaceState(null, "", shareUrl());
  }

  /* Open on the frame the URL describes, and say whether it described one. The caller
     falls back to its own default — replay from the first window — when it did not. */
  function restoreFrame() {
    /* Only a positive number is a position. Absent and empty must not read as zero —
       Number(null) and Number("") are both 0 — and neither must a negative or a
       truncated value, because a link to zero seconds asks for the same thing as no
       link at all: the match from the beginning. */
    const seconds = Number(query.at);
    const position =
      query.at !== null && query.at !== "" && Number.isFinite(seconds) && seconds > 0
        ? seconds
        : NaN;
    const highlight = query.goal ? state.goals.get(query.goal) : null;
    if (!Number.isFinite(position) && !highlight) return false;

    /* A goal's own clock when the link names a build-up but no position: the panel is
       worth nothing if the row it opens from has not been revealed. */
    const at = Number.isFinite(position) ? position : highlight.clock;
    resetTo(clamp(at, state.minClock, state.maxClock));
    setPlaying(false);
    if (highlight) hlOpen(highlight);
    return true;
  }

  /* -------------------------------------------------------------- the clock -- */

  function setView(view) {
    document.body.dataset.view = view;
    /* A jump rather than a slide: the value came from somewhere other than the control. */
    viewSeg.set({ value: view });
    writeShare();
    renderDock();
  }

  function setSpeed(speed) {
    state.speed = speed;
    speedSeg.set({ value: String(speed) });
    const minutes = Math.round((MATCH_SECONDS_PER_REAL_SECOND * speed) / 60);
    el.rateNote.textContent =
      minutes === 1 ? "1 second = 1 match minute" : `1 second = ${minutes} match minutes`;
    renderDock();
  }

  /* A change of transport state, and nothing when it is not a change: a drag across the
     match asks for "paused" once per commit, and re-describing the bar and rewriting the URL
     for each of those is work that produces no difference on screen. */
  function setPlaying(playing) {
    if (state.playing === playing) return;
    state.playing = playing;
    document.body.dataset.playing = String(playing);
    el.play.dataset.playing = String(playing);
    el.play.setAttribute("aria-label", playing ? "Pause the replay" : "Play the replay");
    el.clockState.textContent = playing ? "live" : "paused";
    if (playing) {
      state.lastTs = 0;
      requestAnimationFrame(tick);
    }
    writeShare();
    renderDock();
  }  /* Play, from wherever the replay is standing: at the end it starts again rather than
     sitting there doing nothing, which is the one thing a play button has to get right.
     The transport, the dock's tile and the Space bar all end here rather than each
     spelling that rule out. */
  function togglePlay() {
    if (!state.playing && state.clock >= state.maxClock) resetTo(state.minClock);
    setPlaying(!state.playing);
  }

  function revealTo(clock, { animateScroll = true } = {}) {
    const stick =
      window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - NEAR_BOTTOM_PX;
    let added = 0;

    /* The newest claim in this call, rather than one panel render per claim. A seek crosses
       several claims at once and every one but the last would be overwritten before the
       frame ends — but each render still costs a forced layout, because the stamp restarts
       its animation for a new row. The panel shows the most recent claim, so the last one
       written is the one a reader sees either way. */
    let newestClaim = null;
    /* One write for the whole batch rather than one per row. Every row animates itself in
       with the `.settle` rule, so 50 inserts are 50 rounds of layout and style in a feed
       this size — 1.7 seconds for a single seek across half a match, measured — where one
       insert of the same 50 rows costs a fraction of it. And rows arriving together is what
       a seek is: one movement of the clock, not fifty. */
    let batch = "";

    while (state.cursor < state.items.length && state.items[state.cursor].clock <= clock) {
      const item = state.items[state.cursor++];
      batch +=
        item.kind === "incident" ? incidentMarkup(item.data)
        : item.kind === "goal" ? goalMarkup(item.data, item.highlight)
        : claimMarkup(item.data, item.frame);
      if (item.kind === "claim") newestClaim = item;
      added += 1;
    }

    if (batch) el.feedList.insertAdjacentHTML("beforeend", batch);
    if (newestClaim) renderCheck(newestClaim.data, newestClaim.frame);

    state.clock = clock;
    renderBoard(clock);

    /* A controlled change: the clock is the caller here, and the handle travels to it
       rather than being dropped on it. */
    if (seek) seek.set({ value: clock });

    if (added && animateScroll && stick) {
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
    }
  }

  function resetTo(clock) {
    el.feedList.innerHTML = "";
    state.cursor = 0;
    renderCheck(
      { status: "verified", kind: "claim", metric: "—", text: IDLE_COPY, receipt: "",
        injected: false, claimed_direction: null, claimed_magnitude: null,
        measured_direction: null, delta: null, team: null },
      { window: [0, 0], clock: 0, feed: [], readings: [], incidents: [], ledger: {} }
    );
    el.check.dataset.state = "idle";
    el.checkChip.hidden = true;
    el.checkFigures.hidden = true;
    el.checkStamp.hidden = true;
    el.checkWhy.hidden = true;
    revealTo(clock, { animateScroll: false });
    /* A seek is a deliberate change of frame, so the URL is written at once rather than
       on the playhead's schedule. */
    writeShare();
  }

  /* A seek of one analysed window, which is the step the payload itself works in: the
     length comes from the document rather than from a constant here, so a match analysed
     in ten-minute windows seeks ten minutes. Landing exactly on a boundary is the point —
     the claim on screen is then the claim for the window the reader asked for.

     It pauses, as the slider does: a reader steering somewhere specific has said they are
     done watching for the moment, and arriving while the clock runs on is arriving late. */
  function seekWindows(direction) {
    const step = (state.doc && state.doc.window_seconds) || 300;
    setPlaying(false);
    seekTo(state.clock + direction * step);
  }

  /* A seek the reader asked for, from the slider or from a key. Forwards is an append: the
     rows already on screen are exactly the rows this clock has, so only the few new ones
     have to be built. Backwards has to take rows away, which only a wipe and a rebuild can
     do — the expensive direction, and the reason this is not simply `resetTo` for both.
     Rebuilding a hundred rows for every pointer move is what made seeking stutter, and the
     wake this control draws is drawn a frame at a time: it needs those frames. */
  function seekTo(clock) {
    const target = Math.min(Math.max(clock, state.minClock), state.maxClock);
    if (target < state.clock) {
      resetTo(target);
      return;
    }
    revealTo(target, { animateScroll: false });
    writeShare();
  }

  function tick(ts) {
    if (!state.playing) return;
    if (!state.lastTs) state.lastTs = ts;
    const dt = (ts - state.lastTs) / 1000;
    state.lastTs = ts;

    const next = Math.min(
      state.clock + dt * MATCH_SECONDS_PER_REAL_SECOND * state.speed,
      state.maxClock
    );
    /* Reveal in the smallest step that matters: one match second. */
    revealTo(Math.floor(next), { animateScroll: true });
    state.clock = next;
    /* Throttled: the URL follows the playhead, but at the page's own resolution. */
    writeShare({ now: ts });

    if (next >= state.maxClock) {
      setPlaying(false);
      return;
    }
    requestAnimationFrame(tick);
  }

  /* ------------------------------------------------------------------- boot -- */

  function failure(message) {
    el.providerText.textContent = "replay unavailable";
    el.provider.classList.add("is-error");
    el.promoText.textContent = message;
    el.check.dataset.state = "idle";
    el.checkSaying.textContent = message;
    el.feedNote.textContent = message;
  }

  async function boot() {
    setView(query.view);
    setSpeed(1);
    el.footUrl.textContent = location.origin + location.pathname;

    const url = `/api/replay?seed=${encodeURIComponent(query.seed)}` +
      `&match_id=${encodeURIComponent(query.match_id)}` +
      `&fault=${encodeURIComponent(query.fault)}`;

    let doc;
    try {
      const response = await fetch(url, { headers: { accept: "application/json" } });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `the server answered ${response.status}`);
      }
      doc = await response.json();
    } catch (error) {
      failure(`Could not load the replay: ${error.message}. The server must be running — start it with "python -m pitchframe --overlay".`);
      return;
    }

    if (!doc.frames || !doc.frames.length) {
      failure("This match produced no analysable windows. Try another seed.");
      return;
    }

    state.doc = doc;
    /* Keyed before the timeline is built, because the timeline looks each goal's
       build-up up as it goes. */
    state.goals = new Map(
      doc.highlights.map((h) => [goalKey(h.clock, h.team, h.scorer), h])
    );
    state.items = buildItems(doc);
    state.minClock = doc.frames[0].window[0];
    state.maxClock = doc.frames[doc.frames.length - 1].clock;

    document.title = `${doc.home} v ${doc.away} — Pitchframe`;
    el.teamHome.textContent = doc.home;
    el.teamAway.textContent = doc.away;
    el.footMatch.textContent =
      `${doc.home} v ${doc.away} · ${doc.frames.length} windows · seed ${doc.seed}` +
      (doc.fault === "none" ? "" : ` · fault ${doc.fault}`);
    renderProvider(doc.provider);
    renderSquads(doc);
    /* The bar is described from the document, so it is re-described once there is one: a
       dock built before the fetch cannot offer a build-up menu at all. */
    renderDock();

    el.feedNote.textContent = doc.fault === "none"
      ? "Published lines only, newest last."
      : `Published lines only, newest last. One direction per window is deliberately wrong (fault: ${doc.fault}) so the check is visible.`;

    if (doc.injected.length) {
      el.promoText.textContent =
        "This replay plants one wrong direction per window to show the check working.";
    }

    /* Built here rather than at boot: what it seeks through is this match, from its first
       analysed window to its last, and before the payload arrives there is no such span. */
    mountSeek();

    /* A link that names a minute or a goal opens there, paused. Anything else gets the
       default: the whole story, from the first window, at once. */
    if (!restoreFrame()) {
      resetTo(state.minClock);
      setPlaying(true);
    }
  }

  /* -------------------------------------------------------------- controls --- */

  /* All three controls are ports of React Bits components, imported at the top of this
     file. The page decides what they mean; the components decide how they move. */

  const viewSeg = createRubberSegment(el.viewSeg, {
    items: [{ value: "fan", label: "Fan" }, { value: "analyst", label: "Analyst" }],
    value: query.view,
    size: "sm",
    radius: 4,
    inset: 2,
    label: "Choose a view",
    onChange: (value) => setView(value)
  });

  const speedSeg = createRubberSegment(el.speedSeg, {
    items: [1, 4, 16].map((value) => ({ value: String(value), label: `${value}\u00d7` })),
    value: "1",
    size: "sm",
    radius: 5,
    inset: 2,
    label: "Replay speed",
    onChange: (value) => setSpeed(Number(value))
  });

  /* The seek is built when there is a match to seek through, because its range *is* the
     match and its grid is a minute of it: `min`, `max` and `step` are facts about the
     payload rather than about the control. Until then it is null and every update to it is
     a no-op, which is what the four callers below assume. */
  const SEEK_STEP_SECONDS = 60;
  let seek = null;
  let pendingSeek = 0;
  let seekQueued = 0;

  function mountSeek() {
    seek = createWakeSlider(el.seek, {
      min: state.minClock,
      max: state.maxClock,
      step: SEEK_STEP_SECONDS,
      bars: 64,
      height: 30,
      restHeight: 7,
      gap: 2,
      reach: 6,
      /* Both above the component's defaults, and for the same reason: the defaults are
         tuned for a demo at a pointer's flick, and this control is dragged across eighty
         minutes on a 400px track — a stroll. `sensitivity` is the component's own knob for
         exactly that ("high lifts it on a stroll"), and the longer smoothing leaves the
         trail behind the handle instead of letting it die with the movement. Measured on
         this page at a drag pace of one commit per 70ms: the wake peaks near its full
         height, where the defaults left it under a tenth of that. */
      sensitivity: 5,
      smoothing: 160,
      showValue: true,
      ariaLabel: "Seek through the match",
      formatValue: (seconds) => `${minuteOf(seconds)}\u2032`,
      onChange: (seconds) => {
        /* The slider commits on every move — a tap, a drag and an arrow key all arrive
           here. The page answers at most once a frame, keeping the newest value: a pointer
           reports dozens of commits a second and each one moves the feed, so answering
           each in turn is what would leave no frames to draw the control with. The handle
           does not wait for this — it is moved by the port's own spring — and the transport
           stops at once, because that is one attribute and not a rebuild. */
        setPlaying(false);
        pendingSeek = seconds;
        if (seekQueued) return;
        /* Queued as a task rather than on a frame, because the page must not need a frame to
           be *correct*: a window that is not being composited draws no frames at all — this
           one can be left in the background — and a seek that only ever ran on a frame would
           then do nothing at all. A task still coalesces a burst of commits into one
           reaction, which is what the cost here is about. */
        seekQueued = window.setTimeout(() => {
          seekQueued = 0;
          seekTo(pendingSeek);
        }, 0);
      }
    });
  }

  const dock = createDock(el.dockRail, {
    position: "bottom",
    baseItemSize: 38,
    magnification: 52,
    distance: 150,
    panelHeight: 50,
    gap: 8,
    roundness: 0.45,
    label: "Replay controls",
    items: []
  });

  /* Every glyph the dock draws is inline SVG. The component's sample reaches for an icon
     package; this page has no such dependency, and `1em` sizing is the component's own
     convention — a glyph grows with the tile it sits in. */
  const GLYPHS = {
    play: '<path d="M9 5.6 18.4 12 9 18.4z" fill="currentColor"/>',
    pause: '<path d="M9.4 5.8v12.4M14.6 5.8v12.4"/>',
    gauge: '<path d="M4.4 17.4a8 8 0 1 1 15.2 0"/><path d="M12 13.6 16.2 9"/>',
    eye: '<path d="M3.8 12S7 6.4 12 6.4 20.2 12 20.2 12 17 17.6 12 17.6 3.8 12 3.8 12z"/><circle cx="12" cy="12" r="2.2"/>',
    bars: '<path d="M5 19v-5M10 19V7M15 19v-8M20 19v-3"/>',
    target: '<circle cx="12" cy="12" r="7.6"/><circle cx="12" cy="12" r="2.6"/>',
    link: '<path d="M9.6 14.4 14.4 9.6"/><path d="M11.2 7.6l1.5-1.5a3.55 3.55 0 0 1 5 5l-1.5 1.5"/><path d="M12.8 16.4l-1.5 1.5a3.55 3.55 0 0 1-5-5l1.5-1.5"/>',
    keys: '<rect x="3.4" y="6.4" width="17.2" height="11.2" rx="2.4"/><path d="M7 10h.01M10.6 10h.01M14.2 10h.01M17.8 10h.01M8.4 14h7.2"/>'
  };

  const glyphIcon = (name) => {
    const template = document.createElement("template");
    template.innerHTML =
      '<svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" ' +
      `stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${GLYPHS[name]}</svg>`;
    return template.content.firstElementChild;
  };

  /* ------------------------------------------------------------ the keyboard --- */

  /* Every key the replay answers to, written down once. Two things speak of them: the
     dock's menus, which print a key beside a row, and the listener at the foot of this
     file, which presses it. Both read this table, so a column cannot name a key that does
     nothing and a key cannot be one nobody can find.

     `key` is what the browser puts in `event.key`, lower-cased — `" "` for Space and
     `"arrowleft"` for the left arrow — and `KEY_NAMES` is how that key is written for a
     reader. The digits name a position rather than a number: 16× cannot be typed as a
     digit, so 1, 2 and 3 are the three speeds in the order they are offered, and the
     speed menu's own rows say which key reaches each one. */
  const KEY_NAMES = { " ": "Space", arrowleft: "\u2190", arrowright: "\u2192" };
  const keyName = (key) => KEY_NAMES[key] || key.toUpperCase();

  const SHORTCUTS = [
    { group: "Playback", id: "play", key: " ", label: "Play / pause", run: () => togglePlay() },
    { group: "Seek", id: "seek-back", key: "arrowleft", label: "Back one window", run: () => seekWindows(-1) },
    { group: "Seek", id: "seek-on", key: "arrowright", label: "Forward one window", run: () => seekWindows(1) },
    { group: "Speed", id: "speed-1", key: "1", label: "1\u00d7", run: () => setSpeed(1) },
    { group: "Speed", id: "speed-4", key: "2", label: "4\u00d7", run: () => setSpeed(4) },
    { group: "Speed", id: "speed-16", key: "3", label: "16\u00d7", run: () => setSpeed(16) },
    { group: "View", id: "view-fan", key: "f", label: "Fan view", run: () => setView("fan") },
    { group: "View", id: "view-analyst", key: "a", label: "Analyst view", run: () => setView("analyst") }
  ];

  /* The key printed beside a row, looked up by the same id the row was described from: a
     menu cannot name a key for an action this table does not have. */
  const shortcutOf = (id) => {
    const row = SHORTCUTS.find((entry) => entry.id === id);
    return row ? keyName(row.key) : "";
  };

  const shortcutFor = (key) => SHORTCUTS.find((entry) => entry.key === key);

  /* The cheat sheet one of the dock's tiles offers, built from the rows the listener reads
     rather than typed out a second time. Each row acts as well as names, so the menu is
     usable by a reader who has not learned the keys yet. */
  function shortcutRows() {
    const rows = [];
    let group = "";
    for (const row of SHORTCUTS) {
      if (row.group !== group) {
        if (group) rows.push({ separator: true });
        group = row.group;
      }
      rows.push({ label: row.label, shortcut: keyName(row.key), onClick: row.run });
    }
    return rows;
  }

  /* The dock is described in one function and rebuilt from the page's state, rather than
     kept in step tile by tile: every control that changes the state calls renderDock(), and
     the bar cannot drift out of agreement with the panels it duplicates. */
  let dockGoals = -1;
  let copyNote = "";
  let copyTimer = 0;

  function dockItems() {
    /* Only the goals the replay has reached. `state.clock` is the same cursor the
       scoreboard reads, so the menu and the score can never disagree. */
    const reached = state.doc ? state.doc.goals.filter((goal) => goal.clock <= state.clock) : [];
    const items = [
      {
        key: "play",
        icon: glyphIcon(state.playing ? "pause" : "play"),
        label: state.playing ? "Pause" : "Play",
        /* The dot the component draws for an open app, here marking a running replay. */
        active: state.playing,
        onClick: () => togglePlay()
      },
      {
        key: "speed",
        icon: glyphIcon("gauge"),
        label: `Speed ${state.speed}\u00d7`,
        badge: `${state.speed}\u00d7`,
        menu: [1, 4, 16].map((value) => ({
          label: `${value}\u00d7`,
          shortcut: shortcutOf(`speed-${value}`),
          checked: state.speed === value,
          onClick: () => setSpeed(value)
        }))
      },
      { key: "divide", separator: true },
      {
        key: "fan",
        icon: glyphIcon("eye"),
        label: "Fan view",
        active: (document.body.dataset.view || query.view) === "fan",
        onClick: () => setView("fan")
      },
      {
        key: "analyst",
        icon: glyphIcon("bars"),
        label: "Analyst view",
        active: document.body.dataset.view === "analyst",
        onClick: () => setView("analyst")
      },
      {
        key: "build-ups",
        icon: glyphIcon("target"),
        label: reached.length ? `Goal build-ups (${reached.length})` : "Goal build-ups",
        badge: reached.length || undefined,
        menu: reached.length
          ? reached.map((goal) => ({
              label: `${minuteOf(goal.clock)}\u2032 ${goal.player} \u00b7 ${sideName(goal.team)}`,
              onClick: () => {
                const highlight = state.goals.get(goalKey(goal.clock, goal.team, goal.player));
                if (highlight) hlOpen(highlight);
              }
            }))
          : [{ label: "No goal in this replay yet", disabled: true }]
      },
      {
        key: "share",
        icon: glyphIcon("link"),
        label: copyNote || "Copy a link",
        menu: [
          {
            label: "Copy a link to this frame",
            onClick: () => copyText(new URL(shareUrl(), location.origin).href)
          },
          { separator: true },
          {
            label: "Copy the match URL",
            onClick: () => copyText(location.origin + location.pathname)
          }
        ]
      },
      { key: "divide-keys", separator: true },
      {
        key: "keys",
        icon: glyphIcon("keys"),
        label: "Keyboard",
        /* The one menu here that is a reference as well as a control: every key the page
           answers to, each row pressable for anyone who would rather click it. */
        menu: shortcutRows()
      }
    ];
    /* A tile with a menu opens it when tapped: the component leaves a plain click to the
       item's own handler, and on this page the menu *is* what the tile is for. */
    items.forEach((item, index) => {
      if (item.menu && !item.onClick) item.onClick = () => dock.open(index);
    });
    return items;
  }

  function renderDock() {
    dock.set({ items: dockItems() });
  }

  /* Copying is the one thing the dock does that the page cannot show by moving something.
     It says so in the tile's own label, which is on screen whenever a pointer is over the
     tile — the situation in which a copy was just asked for. */
  function noteCopy(text) {
    copyNote = text;
    window.clearTimeout(copyTimer);
    copyTimer = window.setTimeout(() => {
      copyNote = "";
      renderDock();
    }, 2600);
    renderDock();
  }

  /* The modern path, raced against a deadline. `writeText` can stay pending for ever — a
     window that does not hold focus never settles it — and a tile whose label never
     changes reads as a control that did nothing. The synchronous path takes over instead,
     which is also what a browser without the async API would get. */
  function legacyCopy(text) {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.top = "-1000px";
    document.body.append(field);
    field.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch {
      copied = false;
    }
    field.remove();
    return copied;
  }

  async function copyText(text) {
    let copied = false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      copied = await Promise.race([
        navigator.clipboard.writeText(text).then(
          () => true,
          () => false
        ),
        new Promise((resolve) => window.setTimeout(() => resolve(false), 800))
      ]);
    }
    if (!copied) copied = legacyCopy(text);
    noteCopy(copied ? "Copied" : "Copy failed");
  }

  renderDock();

  el.promoBtn.addEventListener("click", () => setView("analyst"));

  el.play.addEventListener("click", () => togglePlay());

  /* ---------------------------------------------------------- build-up panel -- */

  /* One delegated listener rather than one per row: rows are appended as the clock
     reveals them, so listeners bound at boot would only ever cover the first. */
  el.feedList.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-goal]");
    if (!trigger) return;
    const highlight = state.goals.get(trigger.dataset.goal);
    if (highlight) hlOpen(highlight);
  });

  /* Both ways of dismissing the panel end here rather than only in the dialog's
     ``close`` event. That event is not dependable: on Electron 33 (Chrome 130)
     ``close()`` flips ``open`` to false and dispatches nothing, so a listener of that
     event alone leaves the animation running and `hl.data` set — and the URL then goes
     on claiming a build-up that is no longer on screen. */
  function hlDismiss() {
    if (el.hl.open) el.hl.close();
    if (hl.raf) cancelAnimationFrame(hl.raf);
    hl.playing = false;
    hl.data = null;
    writeShare();
  }

  el.hlClose.addEventListener("click", hlDismiss);
  el.hlReplay.addEventListener("click", () => hlPlay());

  document.querySelectorAll("[data-hl-speed]").forEach((btn) => {
    btn.addEventListener("click", () => {
      hlSetSpeed(Number(btn.dataset.hlSpeed));
      /* Mid-move this just changes the pace. Once the move has finished there is
         nothing left to change, so the new speed is shown by playing it again. */
      if (hl.data && hl.done >= hl.data.steps.length) hlPlay();
    });
  });

  /* The dialog's own ``open`` attribute is the one signal every browser produces: set
     when the panel is shown, removed when it is dismissed, by whichever route — the
     Close button, Escape, or something added later. Watching it instead of listening
     for ``close`` is deliberate: Electron 33 dispatches no such event at all, and this
     browser also swallows Escape before it reaches the page, so a ``close``-only or
     keydown-only cleanup silently never runs — leaving the move animating behind a
     dismissed panel and the URL still claiming a build-up that is gone. */
  new MutationObserver(() => {
    if (!el.hl.open) hlDismiss();
  }).observe(el.hl, { attributeFilter: ["open"] });

  /* One listener, reading the table at the top of this file: the keys the dock's menus
     print are the keys answered here, and there is no second list to keep in step. */
  document.addEventListener("keydown", (event) => {
    const target = event.target;

    /* A key the browser acts on with a modifier is the browser's, not ours: F is the Fan
       view and Cmd/Ctrl+F is still find-on-page, which a reader would rather not lose. */
    if (event.metaKey || event.ctrlKey || event.altKey) return;

    /* Space belongs to whatever control has focus, the dock's tiles included: a focused
       button is activated by it, and the page only takes these keys when nothing that can
       use them is focused. A slider being stepped with the arrows is using them too. */
    if (target instanceof HTMLButtonElement || target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement ||
        (target instanceof HTMLElement && target.isContentEditable)) return;

    /* A surface that has been opened owns the keys the way it owns the pointer. The
       build-up panel is a modal: a key that changed the view or the speed behind it
       would move something the reader cannot see. The dock's menus handle their own
       arrows and Escape, and their rows would go on offering a speed that had already
       changed under them. */
    if (el.hl.open) return;
    if (document.querySelector("[data-dock-menu]")) return;

    const shortcut = shortcutFor(event.key.toLowerCase());
    if (!shortcut) return;
    event.preventDefault();
    shortcut.run();
  });

  boot();
})();
