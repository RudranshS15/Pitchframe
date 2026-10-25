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
 * It is a module rather than a classic script because it imports the ported controls: the
 * segmented control (RubberSegment) drawing the view switch and the replay speed, the dock
 * drawing the actions at the foot of the viewport, the wake slider (WakeSlider) seeking
 * through the match, the sling (SlingButton) the build-up launcher is drawn on, and the
 * pressure type (TextPressure) the two team names are set in. Each is given its state from
 * here and reports changes back through setView/setSpeed/setPlaying/seekTo or an onSend, so
 * the panels, the transport, the dock and the launcher are views of one state rather than
 * states of their own.
 *
 * The team names are set through the pressure port rather than written into the span, for
 * the reason every other field here has one writer: the component owns the letters (it
 * splits them to vary each one), so writing textContent beside it would leave the scoreboard
 * showing one name and the document title another.
 */

import {
  goalKey,
  buildItems,
  reachedGoals,
  newestBuildUp,
  frameAt,
} from "./goal-clock.mjs";
import { buildFrameQuery, parseFrameQuery } from "./frame-link.mjs";
import { rankByRelativeMovement } from "./relative-rank.mjs";
import { plural, rateNote } from "./wording.mjs";
import { createBorderGlow } from "./border-glow.js";
import { createDock } from "./dock.js";
import { createRubberSegment } from "./rubber-segment.js";
import { createSlingButton } from "./sling-button.js";
import { createTextPressure } from "./text-pressure.js";
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

  /*
   * The match is named in the path: `/match/quiet`, not `/match?seed=7`.
   *
   * A number says nothing about what is behind it, so an address built from one cannot be
   * read, linked to by hand, or typed from memory — three things a reader does with a URL
   * and none of which a number supports. The name comes from the path rather than from a
   * query parameter because the match is *which page this is*, and the query string is
   * already spoken for: it is the frame — the view, the second, the open build-up, the
   * fault — which is what a reader shares and what changes while they read.
   *
   * The server never serves this page at a name it does not have (an unknown one is a 404
   * listing the ones there are), and it redirects `/match?seed=7` to `/match/quiet`, so a
   * link written before matches had names still lands on the match it named. A seed no
   * scenario owns has no name to go to and stays a seed, which is why both forms are read
   * here: this page can be reached either way, and neither is a fallback for the other.
   */
  const namedMatch = (() => {
    const parts = location.pathname.split("/").filter(Boolean);
    return parts.length === 2 && parts[0] === "match" ? decodeURIComponent(parts[1]) : null;
  })();

  const query = {
    /* A name if the address has one, and otherwise the seed it carries — or neither, in
       the one case left over: an addressed seed that no scenario owns, where the number is
       the only description of the match there is. The payload reports the seed it actually
       built, which is what a numbered link has to carry to be reproducible. */
    scenario: namedMatch,
    seed: params.get("seed"),
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
    matchTitle: $("match-title"),
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
    ratingsList: $("ratings-list"), ratingsWindow: $("ratings-window"),
    ratingsEmpty: $("ratings-empty"), ratingsLabels: $("ratings-labels"),
    award: $("award"), awardName: $("award-name"), awardRating: $("award-rating"),
    awardReasons: $("award-reasons"), awardText: $("award-text"),
    launchTitle: $("launch-title"), launchNote: $("launch-note"), sling: $("sling"),
    pitchChip: $("pitch-chip"), pitchOpen: $("pitch-open"), pitchSwitch: $("pitch-switch"),
    squads: $("squads"), footMatch: $("foot-match"), footUrl: $("foot-url"),
    hl: $("hl"), hlTitle: $("hl-title"), hlGoals: $("hl-goals"), hlLabels: $("hl-labels"), hlSvg: $("hl-svg"),
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
    /* The roster the ratings panel is drawn against: both squads, flattened once. The
       payload sends a player's mark per frame and nothing else about them, because the
       names and shirts are already here and repeating them sixteen times is most of a
       megabyte saying what this list already says. */
    roster: [],
  };

  /* --------------------------------------------------- the live pitch chip -- */

  /* The chip on the match bar that opens the standalone pitch view, and the small
     switch beside it that turns the entry point off and on. The choice is remembered
     in localStorage — and even where storage is fenced off, the page's own answer
     stands, so the switch never stops working. */
  const PITCH_CHIP_KEY = "pitchframe:pitch-chip";
  let pitchChipOn = (() => {
    try { return localStorage.getItem(PITCH_CHIP_KEY) !== "off"; }
    catch { return true; /* storage unavailable is a setting, not a fault */ }
  })();

  function renderPitchChip() {
    el.pitchOpen.hidden = !pitchChipOn;
    el.pitchChip.classList.toggle("pitchchip--off", !pitchChipOn);
    el.pitchSwitch.setAttribute("aria-pressed", String(pitchChipOn));
    el.pitchSwitch.setAttribute(
      "aria-label",
      pitchChipOn ? "Hide the live pitch button on the match bar"
                  : "Show the live pitch button on the match bar",
    );
    el.pitchSwitch.textContent = pitchChipOn ? "on" : "off";
  }

  el.pitchSwitch.addEventListener("click", () => {
    pitchChipOn = !pitchChipOn;
    try { localStorage.setItem(PITCH_CHIP_KEY, pitchChipOn ? "on" : "off"); }
    catch { /* the toggle still flips, it just is not remembered */ }
    renderPitchChip();
  });
  renderPitchChip();

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

  /* The chronological list is built by the shared goal-sync module (goal-clock.mjs),
     where the same decisions are unit-tested against the playhead's own stepping. */

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

  /* The board is redrawn on every frame of the replay, and almost every one of those
     frames has nothing to say: the clock ticks sixty times a second while the payload
     moves in five-minute windows, so the minute label changes once a second and the
     metrics, the ledger and the share of possession change once every five.

     Writing all of it anyway is what made the page slow to answer under its own
     animation — sixty rebuilds a second of a twenty-row table, each one a parse and a
     forced layout — so each part of the board is written only when the thing it shows
     has changed. The minute and the scoreline are compared as the strings they are
     written as, and everything that is a function of the analysed frame is keyed on the
     frame itself: `frameAt` hands back the same object until a new window is
     reached, so that comparison is the question "has the payload moved".

     The rule is the one the dock's goal count and the launcher already follow — a panel
     is rebuilt when what it describes changes, never once per frame. */
  let shownMinute = null;
  let shownScoreline = null;
  let shownFrame = null;

  function renderBoard(clock) {
    const doc = state.doc;
    /* Before the payload lands there is no board to render: no goals, no frames, no
       scoreboard. Leaving with everything as it was is correct — applyPayload runs its
       own first render once the document arrives. The play button does not wait for the
       page's own fetch, so this is a reachable state, not a hypothetical one. */
    if (!doc) return;
    const minute = Math.min(minuteOf(Math.max(clock, 0)), 90 + Math.floor(Math.max(0, clock - 5400) / 60));

    const minuteLine = `${minute}<i>&prime;</i>`;
    if (minuteLine !== shownMinute) {
      shownMinute = minuteLine;
      el.clock.innerHTML = minuteLine;
    }

    const goals = reachedGoals(doc.goals, clock);
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
    const scoreline = `${home}<i>–</i>${away}`;
    if (scoreline !== shownScoreline) {
      shownScoreline = scoreline;
      el.score.innerHTML = scoreline;
      el.score.setAttribute("aria-label", `Score ${home} to ${away}`);
    }

    const frame = frameAt(doc.frames, clock);
    if (frame !== shownFrame) {
      shownFrame = frame;

      const possession = frame
        ? frame.readings.find((r) => r.key === "possession_share@home")
        : null;
      const share = possession ? possession.value : 0.5;
      el.shareBar.style.width = `${(share * 100).toFixed(1)}%`;
      el.shareHome.textContent = possession ? `${(share * 100).toFixed(0)}%` : "–";
      el.shareAway.textContent = possession ? `${((1 - share) * 100).toFixed(0)}%` : "–";

      renderLedger(frame);
      renderRatings(frame);

      if (frame) {
        const claimed = new Set(frame.feed.map((r) => r.metric));
        renderMetrics(frame, claimed);
      } else {
        renderMetrics(null, new Set());
      }
    }

    renderAward();
    renderLauncher();
  }

  function renderLedger(frame) {
    const ledger = frame ? frame.ledger : { verified: 0, rejected: 0, corrections: 0, dropped: 0 };
    el.ledVerified.textContent = ledger.verified;
    el.ledRejected.textContent = ledger.rejected;
    el.ledCorrections.textContent = ledger.corrections;
    el.ledDropped.textContent = ledger.dropped;

    /* Both counts go through the shared plural, in both branches.
       "1 lines published" was the wart the extraction preserved on
       purpose, waiting for this edit: the rejected branch now reads the
       verified count through plural() too, so the boundary is "1 line
       published". */
    el.promoText.textContent = ledger.rejected
      ? `${plural(ledger.verified, "line")} published, ${plural(ledger.rejected, "claim")} caught by measurement and corrected.`
      : `${plural(ledger.verified, "line")} published, each one checked against the event stream.`;
  }

  /* ------------------------------------------------------- the analyst column ---- */

  /* The analyst column is a scrollport, and a scrollport only a pointer can scroll is half
     a control. Chrome, Edge and Firefox make scroll containers focusable by themselves;
     Safari does not, so the column is given a tab stop here — but only while it has
     something to scroll. A focusable box with nothing left to reach is a tab stop that
     does nothing, which is worse than the one it replaces.

     Only the attribute writes are conditional, not the measurement: `scrollHeight` and
     `clientHeight` on an element outside the layout are two zeroes, and two zeroes are
     cheap. It is called where the column's content changes and when the window does. */
  const column = document.querySelector(".side__pin");
  let columnScrolls = null;

  function syncColumnAccess() {
    if (!column) return;
    const scrolls = column.scrollHeight > column.clientHeight + 1;
    if (scrolls === columnScrolls) return;
    columnScrolls = scrolls;
    if (scrolls) {
      column.tabIndex = 0;
      column.setAttribute("role", "region");
      column.setAttribute("aria-label", "Analysis column");
    } else {
      column.removeAttribute("tabindex");
      column.removeAttribute("role");
      column.removeAttribute("aria-label");
    }
  }

  window.addEventListener("resize", syncColumnAccess);

  function renderMetrics(frame, claimedKeys) {
    if (!frame || !frame.readings.length) {
      el.metricsBody.innerHTML = "";
      el.metricsWindow.textContent = "";
      el.metricsEmpty.hidden = false;
      syncColumnAccess();
      return;
    }
    el.metricsEmpty.hidden = true;
    el.metricsWindow.textContent =
      `${minuteOf(frame.window[0])}′–${minuteOf(frame.window[1])}′`;

    /* Ranked the same way the narrator ranks: by relative movement, because the
       metrics do not share a scale and raw deltas would pick whichever happens to
       have the largest units. The rule lives in relative-rank.mjs — the same
       formula, down to its epsilon, as demo.py's rank_by_relative_movement, and
       the cross-language test holds both to one order on identical fixtures. */
    const rows = rankByRelativeMovement(frame.readings);

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
    syncColumnAccess();
  }

  /* The team sheet: the eleven who started, then the substitutes. The bench is drawn
     from its own list in the payload rather than from the end of one merged roster,
     because a substitute did not play: the ratings do not score them, the scoreboard
     does not name them, and a merged list would invite a page to pretend otherwise.

     The position is shown beside each name — it was in the payload from the start and
     the panel only printed the shirt and the name, which is a list of people rather
     than a team sheet. */
  function renderSquads(doc) {
    const rows = (players, modifier = "") => (players || [])
      .map((p) => `<li class="squads__player${modifier}">
          <span class="squads__shirt">${esc(p.shirt)}</span>
          <span>${esc(p.name)}</span>
          <span class="squads__pos">${esc(p.position)}</span>
        </li>`)
      .join("");

    el.squads.innerHTML = ["home", "away"]
      .map((side) => {
        const bench = doc.bench ? doc.bench[side] : [];
        const name = side === "home" ? doc.home : doc.away;
        /* A payload from a server that predates the bench has no substitutes to draw,
           and the heading is omitted rather than printed over an empty list. */
        const substitutes = bench && bench.length
          ? `<p class="squads__role">Substitutes</p>
             <ul class="squads__list squads__list--bench">${rows(bench, " squads__player--sub")}</ul>`
          : "";
        return `<div class="squads__team">
            <h3>${esc(name)}</h3>
            <p class="squads__role">Starting XI</p>
            <ul class="squads__list">${rows(doc.squads[side])}</ul>
            ${substitutes}
          </div>`;
      })
      .join("");
    syncColumnAccess();
  }

  /* ------------------------------------------------------------ the ratings ---- */

  /* Live, like everything else on this page: each frame carries every player's mark as of
     that frame's clock, computed on the server from the events up to that minute and
     nothing later. A mark on screen at 60 minutes is the mark the first hour produced, not
     the one the match ended with.

     The order is this page's — best first — the way the metric table is ranked here too.
     The marks, the tallies behind them and the award are the payload's; the page sorts and
     prints. */
  let shownRatings = -1;

  function renderRatings(frame) {
    const block = state.doc && state.doc.ratings;
    if (!block || !state.roster.length) return;
    const clock = frame ? frame.clock : -1;
    /* Re-rendered only when the frame changes. The ratings move at window boundaries and
       nothing in between, and this panel is 22 rows. */
    if (clock === shownRatings) return;
    shownRatings = clock;

    el.ratingsEmpty.hidden = Boolean(frame);
    el.ratingsWindow.textContent = frame ? `${minuteOf(frame.clock)}\u2032` : "";
    if (!frame) {
      el.ratingsList.innerHTML = "";
      return;
    }

    const values = frame.ratings.values || {};
    const rows = state.roster
      .map((player) => ({ ...player, rating: values[player.id] }))
      .filter((player) => typeof player.rating === "number")
      .sort((a, b) => b.rating - a.rating || a.shirt - b.shirt);

    el.ratingsList.innerHTML = rows
      .map((player) => {
        const leading = player.id === frame.ratings.leader;
        return `<li class="ratings__row${leading ? " ratings__row--leading" : ""}">
          <span class="ratings__shirt">${esc(player.shirt)}</span>
          <span class="ratings__who">${esc(player.name)}<span class="ratings__pos">${esc(player.position)}</span></span>
          <span class="ratings__mark">${player.rating.toFixed(1)}</span>
        </li>`;
      })
      .join("");
  }

  /* The award, once the replay has played the match out. Held back rather than shown greyed
     out: naming a man of the match before the match ended would spoil the replay exactly as
     printing the final score would, and this page shows the score as it stands. */
  let shownAward = false;

  function renderAward() {
    const block = state.doc && state.doc.ratings;
    const award = block ? block.award : null;
    const show = Boolean(award && state.clock >= block.decided_at);
    if (show === shownAward) return;
    shownAward = show;
    el.award.hidden = !show;
    if (!show) return;

    el.awardName.textContent = `${award.name} \u00b7 ${sideName(award.team)}`;
    el.awardRating.textContent = award.rating.toFixed(1);
    el.awardReasons.innerHTML = (award.reasons || [])
      .map((reason) => `<span class="award__reason">${esc(reason)}</span>`)
      .join("");
    el.awardText.textContent = award.conclusion;
  }

  /* ----------------------------------------------------------- the launcher ---- */

  /* The one control that opens the build-up panel and is always on the page.

     The panel used to open from a goal row on the timeline and from the dock's build-up
     menu, and both of those exist only once the replay has scored: on the quiet match the
     feature was unreachable until the 60th minute, which reads as a control that does not
     work rather than as a match with nothing in it yet. This says what it is waiting for,
     and lights up the moment a goal lands. */
  function newestBuildUpOnPage() {
    return state.doc
      ? newestBuildUp(state.doc.goals, state.goals, state.clock)
      : null;
  }

  function openNewestBuildUp() {
    const newest = newestBuildUpOnPage();
    if (newest) hlOpen(newest.highlight);
  }

  /* Described from the reached goals, so the launcher and the scoreboard cannot disagree:
     both read the same clock. Rebuilt only when the goal it would open changes, so a label
     under the pointer is not rewritten every frame. */
  let launchKey = null;

  function renderLauncher() {
    const newest = newestBuildUpOnPage();
    const key = newest ? goalKey(newest.goal.clock, newest.goal.team, newest.goal.player) : "";
    if (key === launchKey) return;
    launchKey = key;
    sling.set({
      disabled: !newest,
      ariaLabel: newest ? `Open the build-up to ${newest.goal.player}'s goal` : "No goal yet"
    });
    if (newest) {
      el.launchTitle.textContent = "Open the build-up";
      el.launchNote.textContent =
        `${minuteOf(newest.goal.clock)}\u2032 \u00b7 ${newest.goal.player} \u00b7 ${sideName(newest.goal.team)}`;
    } else {
      el.launchTitle.textContent = "Goal build-up";
      el.launchNote.textContent = "No goal yet — pull it when one lands.";
    }
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

  /* Every goal the replay has reached, with its build-up, in match order. The same rule
     the launcher and the dock follow: a goal the clock has not reached is not on offer,
     because opening its panel would reveal a move before the scoreboard revealed the
     goal it produced. */
  function reachedBuildUps() {
    if (!state.doc) return [];
    return reachedGoals(state.doc.goals, state.clock)
      .map((goal) => ({ goal, highlight: state.goals.get(goalKey(goal.clock, goal.team, goal.player)) }))
      .filter((entry) => entry.highlight);
  }

  /* The chooser inside the panel: one chip per goal, shown only when there is more than
     one build-up to choose between. With a single goal the panel already names it, and a
     button whose only action is to reopen the panel that is open is not a control. */
  function renderHlGoals() {
    if (!el.hlGoals) return;
    const entries = reachedBuildUps();
    if (entries.length < 2) {
      el.hlGoals.hidden = true;
      el.hlGoals.replaceChildren();
      return;
    }
    const current = hl.data ? goalKey(hl.data.clock, hl.data.team, hl.data.scorer) : null;
    el.hlGoals.hidden = false;
    el.hlGoals.innerHTML = entries
      .map(({ goal }) => {
        const key = goalKey(goal.clock, goal.team, goal.player);
        return `<button type="button" class="hl__goal" data-hl-goal="${esc(key)}" aria-pressed="${key === current}">
          <span class="hl__goal-clock">${minuteOf(goal.clock)}&prime;</span>
          <span>${esc(goal.player)} &middot; ${esc(sideName(goal.team))}</span>
        </button>`;
      })
      .join("");
  }

  function hlOpen(data) {
    /* A chip is inside the row this call rebuilds. Reading focus before the rebuild is
       what lets the keyboard stay on the goal it chose instead of being dropped to the
       top of the dialog after every switch. */
    const restoreFocus = Boolean(el.hlGoals && el.hlGoals.contains(document.activeElement));
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
    renderHlGoals();
    if (restoreFocus && !el.hlGoals.hidden) {
      const chosen = el.hlGoals.querySelector('[aria-pressed="true"]');
      if (chosen) chosen.focus({ preventScroll: true });
    }

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
    /* A named match is named in the path, and nothing about the match goes in the query:
       the name is the seed. A numbered one carries the seed the payload was actually built
       from, rather than the one this page asked for — a link that omitted the seed and
       inherited the server's default has to carry the match it opened, or opening it later
       would build a different one. The build rule lives in frame-link.mjs, tested as a
       round trip against the parse below. */
    /* Only while the panel is actually on screen, and keyed the same way the timeline
       keys it, so the link names a goal in the payload's own vocabulary rather than in
       one of its own. `open` is read rather than `hl.data` alone: the element is the
       only thing that knows the panel is gone, whatever dismissed it. */
    return `${location.pathname}?${buildFrameQuery({
      named: !!query.scenario,
      seed: state.doc ? state.doc.seed : query.seed,
      matchId: query.match_id,
      fault: query.fault,
      view: document.body.dataset.view || query.view,
      at: state.clock,
      goal: el.hl.open && hl.data
        ? goalKey(hl.data.clock, hl.data.team, hl.data.scorer)
        : null,
    })}`;
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
    /* The position rule — only a positive number is a position, absent and empty must
       not read as zero, nor a negative or a truncated value, because a link to zero
       seconds asks for the same thing as no link at all — is decided in
       frame-link.mjs, round-tripped against the build above. */
    const { position } = parseFrameQuery(query);
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
    /* The column is only laid out in the analyst view, so switching into it is the moment
       its tab stop becomes meaningful (and switching out is when it must go). */
    syncColumnAccess();
  }

  function setSpeed(speed) {
    state.speed = speed;
    speedSeg.set({ value: String(speed) });
    el.rateNote.textContent = rateNote(MATCH_SECONDS_PER_REAL_SECOND * speed);
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
    /* The same pre-payload guard the board has: state.items is built from the payload,
       and revealing into a document that has not arrived would walk `undefined`. */
    if (!state.doc) return;
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
    /* The launcher says why it cannot be pulled, rather than sitting there dim with a
       caption about goals. It is the one control on the page whose reason for being off
       is a failed request. */
    el.launchTitle.textContent = "Goal build-up";
    el.launchNote.textContent = "The replay did not load, so there is nothing to open.";
  }

  async function boot() {
    setView(query.view);
    setSpeed(1);
    el.footUrl.textContent = location.origin + location.pathname;

    /* The page's title, and the two names on the scoreboard. Mounted before the fetch
       because the spans already carry their words in the markup, and a reader whose server
       is not running should still see a title and a scoreboard rather than empty slots: the
       component is mounted on what is there, and the payload replaces it when it arrives.

       The title takes the wordmark's own treatment, because it is the page's wordmark — the
       first thing a shared link opens on. `fit` is off everywhere here: these are a page
       title and a scoreboard's labels, sized by the layout like everything beside them.
       Oswald's weight axis is the range this face has; the stylesheet rests them at 500, so
       the pressure runs either side of it. */
    const pressure = (host, radius, smoothing) => createTextPressure(host, {
      fit: false,
      radius,
      smoothing,
      clickRipple: false,
      weightRange: [400, 700]
    });

    const title = pressure(el.matchTitle, 0.55, 0.35);
    const teamNames = [el.teamHome, el.teamAway].map((host) => pressure(host, 0.5, 0.4));

    /* Asked for by name when the address has one, and by number otherwise, which are the
       two ways a match can be described. Neither is invented here: a page that guessed a
       default would open a different match from the one its own address names the moment
       those two disagreed. */
    const url = `/api/replay?match_id=${encodeURIComponent(query.match_id)}` +
      `&fault=${encodeURIComponent(query.fault)}` +
      (query.scenario !== null ? `&scenario=${encodeURIComponent(query.scenario)}` : "") +
      (query.scenario === null && query.seed !== null
        ? `&seed=${encodeURIComponent(query.seed)}`
        : "");

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
    state.items = buildItems(doc, state.goals);
    state.minClock = doc.frames[0].window[0];
    state.maxClock = doc.frames[doc.frames.length - 1].clock;

    document.title = `${doc.home} v ${doc.away} — Pitchframe`;
    /* The title and the tab say the same thing, and the board keeps the two names apart
       because the score sits between them. */
    title.setText(`${doc.home} v ${doc.away}`);
    teamNames[0].setText(doc.home);
    teamNames[1].setText(doc.away);
    el.footMatch.textContent =
      `${doc.home} v ${doc.away} · ${doc.frames.length} windows · seed ${doc.seed}` +
      (doc.fault === "none" ? "" : ` · fault ${doc.fault}`);
    /* The chip opens the pitch view at the seed the payload built (not the one the URL
       asked for), so a numbered link that the server rounded to a scenario cannot send
       the reader looking at a different match from the one on screen. */
    el.pitchOpen.href = `/static/pitch/pitch.html?seed=${encodeURIComponent(String(doc.seed))}`;
    renderProvider(doc.provider);
    renderSquads(doc);

    /* The roster the ratings panel joins its marks against. Built from the payload's own
       squads, so a player who is not in the replay cannot appear in the panel. */
    state.roster = ["home", "away"].flatMap((side) =>
      (doc.squads[side] || []).map((player) => ({
        id: player.id,
        name: player.name,
        shirt: player.shirt,
        position: player.position,
      }))
    );
    /* Straight from the payload, like the build-up panel's labels: a view that could
       compose a provenance label could also drop one. */
    el.ratingsLabels.innerHTML = ((doc.ratings && doc.ratings.labels) || [])
      .map((label) => `<span class="ratings__label">${esc(label)}</span>`)
      .join("");
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

  /* ----------------------------------------------------------- panel edges --- */

  /* Every panel on the page lights the boundary nearest the pointer, through BorderGlow —
     a fifth port, and the only one that is not a control. One instance per panel marked
     `data-glow` in the markup, which is all the page has to say about it: the port reads
     each panel's own background, radius and tokens off the element, so no option is passed
     here and there is no second place where a panel's colours are written down.

     No reach is passed either, and that is deliberate: the distance at which a panel stops
     being lit is a property of *the page* — how far apart these panels are — rather than of
     any one of them, so it is one constant in the port rather than eight numbers here. The
     light itself is shared: every instance below registers with the port's one listener and
     is measured against the same pointer, so what these eight panels draw is eight arcs of
     one lamp rather than eight lamps that happen to agree on a colour.

     The halo is drawn inside the boundary (`glowRadius: 0`) rather than outside it. The
     match bar and the check clip their contents, so an outer halo would show on five panels
     and be cut off on two — and the grid's gap is a centimetre, which is not room for eight
     concentric glows to overlap in.

     Nothing keeps the handles: the listeners are on the panels themselves and the replay
     rewrites their contents rather than replacing the panels, so these live as long as the
     page does and nothing has to be torn down. */
  for (const panel of document.querySelectorAll("[data-glow]")) {
    createBorderGlow(panel, {
      glowRadius: 0,
      /* A wider cone than the component's 25%, because these are large rectangles: a quarter
         of a boundary lit reads as a smudge on a panel 40rem wide, where on a card 6rem
         across it is the whole edge. */
      coneSpread: 32,
    });
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
    items: [0.25, 1, 4, 16].map((value) => ({ value: String(value), label: `${value}\u00d7` })),
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

  /* The launcher's pad. Pulled or tapped, it opens the newest build-up the replay has
     reached; `set({disabled})` is how the page says whether there is one yet, and the
     control is never re-mounted, so a pad under a finger is not replaced. */
  const sling = createSlingButton(el.sling, {
    size: 56,
    strokeWidth: 3,
    armAt: 44,
    maxPull: 160,
    launchSpeed: 2600,
    recoil: 0.2,
    flight: 120,
    particles: 14,
    spread: 60,
    axis: "any",
    tapSends: true,
    disabled: true,
    ariaLabel: "No goal yet",
    onSend: () => openNewestBuildUp()
  });

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
     digit, so 1, 2 and 3 are 1×, 4× and 16× in the order they are offered, with 0 the
     slowest — and the speed menu's own rows say which key reaches each one. */
  const KEY_NAMES = { " ": "Space", arrowleft: "\u2190", arrowright: "\u2192" };
  const keyName = (key) => KEY_NAMES[key] || key.toUpperCase();

  const SHORTCUTS = [
    { group: "Playback", id: "play", key: " ", label: "Play / pause", run: () => togglePlay() },
    /* These two rows name both granularities the arrows have. Away from the seek control
       one press is one analysed window (`seekWindows`, from `window_seconds`); with the
       seek handle itself focused the browser hands the keys to that slider instead, and
       one press is its own step — 60 match seconds (see SEEK_STEP_SECONDS at mountSeek).
       The menu says both, so a reader who focused the slider and found the step shorter is
       reading the behaviour, not a bug. */
    { group: "Seek", id: "seek-back", key: "arrowleft",
      label: "Back one window (60s when the seek slider has focus)", run: () => seekWindows(-1) },
    { group: "Seek", id: "seek-on", key: "arrowright",
      label: "Forward one window (60s when the seek slider has focus)", run: () => seekWindows(1) },
    { group: "Speed", id: "speed-0.25", key: "0", label: "0.25\u00d7", run: () => setSpeed(0.25) },
    { group: "Speed", id: "speed-1", key: "1", label: "1\u00d7", run: () => setSpeed(1) },
    { group: "Speed", id: "speed-4", key: "2", label: "4\u00d7", run: () => setSpeed(4) },
    { group: "Speed", id: "speed-16", key: "3", label: "16\u00d7", run: () => setSpeed(16) },
    { group: "View", id: "view-fan", key: "f", label: "Fan view", run: () => setView("fan") },
    { group: "View", id: "view-analyst", key: "a", label: "Analyst view", run: () => setView("analyst") },
    /* The launcher's own key. It does nothing until a goal has landed, which is the same
       rule as the pad: the menu row it prints is offered to a reader who has not found the
       control yet, not to one who should be told about the future. */
    { group: "Highlights", id: "buildup", key: "g", label: "Goal build-up", run: () => openNewestBuildUp() }
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
    const reached = reachedGoals(state.doc ? state.doc.goals : [], state.clock);
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
        menu: [0.25, 1, 4, 16].map((value) => ({
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

  /* One listener for the row rather than one per chip: the row is rebuilt every time the
     panel opens on another goal, and listeners attached to the buttons would be thrown
     away with them. */
  el.hlGoals.addEventListener("click", (event) => {
    const button = event.target.closest("[data-hl-goal]");
    if (!button || !state.goals) return;
    const highlight = state.goals.get(button.dataset.hlGoal);
    if (!highlight) return;
    if (hl.data && goalKey(hl.data.clock, hl.data.team, hl.data.scorer) === button.dataset.hlGoal) return;
    hlOpen(highlight);
  });

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
