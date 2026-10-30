/**
 * The matches page.
 *
 * Two sources, one page, and the difference between them is the point:
 *
 * - **Live** is a goal feed. It polls a league's current matchday, shows the score and
 *   the clock the page derives from kick-off, and expands one match into its goals and
 *   the moments those goals support. It does not show passes, pressure or ratings,
 *   because the feed does not record them.
 * - **Archive** is the StatsBomb open data. Browsing it lists a season's fixtures without
 *   downloading anything; opening one fetches that match and lands on the replay, where
 *   every line is verified against the full event stream.
 *
 * Nothing here measures a match. The server decides what a live match is (status, minute,
 * moments) and what the archive holds; this module renders that and keeps the address in
 * step with what is on screen.
 */

const $ = (id) => document.getElementById(id);

const params = new URLSearchParams(location.search);

const state = {
  tab: "live",
  league: "bl1",
  live: null,
  liveTimer: null,
  liveRequest: 0,
  competitions: [],
  competition: null,
  season: null,
  archiveRows: [],
  archiveRequest: 0,
};

/* --- small helpers -------------------------------------------------------- */

const esc = (value) =>
  String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);

function timeOf(iso) {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(moment);
}

function dateOf(iso) {
  const moment = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(moment.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
  }).format(moment);
}

function setStatus(element, text, failed = false) {
  element.textContent = text;
  if (failed) element.dataset.state = "error";
  else delete element.dataset.state;
}

function setUrl({ live = null, competition = null, season = null } = {}) {
  const next = new URLSearchParams();
  if (live) next.set("live", live);
  if (competition !== null) {
    next.set("competition", String(competition));
    if (season !== null) next.set("season", String(season));
  }
  const query = next.toString();
  history.replaceState(null, "", location.pathname + (query ? `?${query}` : ""));
}

/* --- tabs ----------------------------------------------------------------- */

const tabs = { live: $("tab-live"), archive: $("tab-archive") };
const panels = { live: $("panel-live"), archive: $("panel-archive") };

function showTab(name, { update = true } = {}) {
  state.tab = name;
  for (const key of Object.keys(tabs)) {
    const selected = key === name;
    tabs[key].setAttribute("aria-selected", String(selected));
    tabs[key].tabIndex = selected ? 0 : -1;
    panels[key].hidden = !selected;
  }
  clearTimeout(state.liveTimer);
  if (name === "live") {
    if (update) setUrl({ live: currentLiveParam() });
    if (state.live) scheduleRefresh(state.live);
  } else {
    if (update) {
      setUrl({
        competition: state.competition,
        season: state.season,
      });
    }
    if (!state.competitions.length) loadCompetitions();
  }
}

for (const [name, button] of Object.entries(tabs)) {
  button.addEventListener("click", () => showTab(name));
  button.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const other = name === "live" ? "archive" : "live";
    showTab(other);
    tabs[other].focus();
  });
}

/* --- live ----------------------------------------------------------------- */

const liveLeague = $("live-league");
const liveStatus = $("live-status");
const liveList = $("live-list");
const liveEmpty = $("live-empty");
const liveDetail = $("live-detail");

function currentLiveParam() {
  // The address is the one description of an open detail. A refresh re-renders from it,
  // so a score that moves while a reader has a match open cannot close it.
  return new URLSearchParams(location.search).get("live") || null;
}

function liveBadge(match) {
  switch (match.status) {
    case "live":
      return `<span class="mx-badge mx-badge--live">Live</span>`;
    case "finished":
      return `<span class="mx-badge mx-badge--ft">Full time</span>`;
    case "pending":
      return `<span class="mx-badge mx-badge--pending">Result pending</span>`;
    default:
      return `<span class="mx-badge mx-badge--soon">${esc(timeOf(match.kickoff))}</span>`;
  }
}

function liveMinute(match) {
  if (match.status === "live" && match.minute !== null) {
    return `<span class="mx-state__minute">~${match.minute}'</span>`;
  }
  if (match.status === "pending") {
    return `<span class="mx-state__minute">no result posted</span>`;
  }
  return "";
}

function goalsMarkup(match) {
  if (!match.goals.length) {
    if (match.status === "scheduled") {
      return `<span class="mx-goals">Kick-off ${esc(timeOf(match.kickoff))}</span>`;
    }
    return `<span class="mx-goals">No goals recorded</span>`;
  }
  return (
    `<span class="mx-goals">` +
    match.goals
      .map(
        (goal) =>
          `<span><b>${goal.minute}'</b> ${esc(goal.scorer)}` +
          `${goal.penalty ? " (pen)" : ""}${goal.own_goal ? " (OG)" : ""}</span>`
      )
      .join("") +
    `</span>`
  );
}

function teamClass(score, other, finished) {
  if (!finished) return "";
  if (score > other) return " mx-team--winner";
  if (score < other) return " mx-team--loser";
  return "";
}

function renderLiveRow(match) {
  const finished = match.status === "finished";
  const settled = finished || match.status === "pending";
  const label =
    match.status === "scheduled"
      ? `${match.home} versus ${match.away}, kick-off ${timeOf(match.kickoff)}`
      : `${match.home}, ${match.score}, ${match.away}, ${match.status}`;
  return `
    <li>
      <button class="mx-row" type="button" data-live-id="${esc(match.id)}"
              aria-label="${esc(label)}">
        <span class="mx-state">${liveBadge(match)}${liveMinute(match)}</span>
        <span class="mx-fixture">
          <span class="mx-team${teamClass(match.home_score, match.away_score, finished)}">
            <span>${esc(match.home)}</span>
            <span class="mx-team__score">${settled || match.status === "live" ? match.home_score : ""}</span>
          </span>
          <span class="mx-team${teamClass(match.away_score, match.home_score, finished)}">
            <span>${esc(match.away)}</span>
            <span class="mx-team__score">${settled || match.status === "live" ? match.away_score : ""}</span>
          </span>
        </span>
        ${goalsMarkup(match)}
        <span class="mx-arrow" aria-hidden="true">${match.status === "finished" ? "·" : "↗"}</span>
      </button>
    </li>`;
}

function renderDetail(match) {
  const goals = match.goals.length
    ? match.goals
        .map(
          (goal) => `
        <li>
          <span class="mx-timeline__minute">${goal.minute}'</span>
          <span>${esc(goal.scorer)}${goal.penalty ? " (pen)" : ""}${
            goal.own_goal ? " (own goal)" : ""
          }</span>
          <span class="mx-timeline__team">${esc(
            goal.side === "home" ? match.home : match.away
          )}</span>
        </li>`
        )
        .join("")
    : `<li><span class="mx-timeline__none">No goals recorded.</span></li>`;

  const moments = match.moments.length
    ? match.moments
        .map(
          (moment) => `
        <li>
          <p class="mx-moments__text">${esc(moment.text)}</p>
          <p class="mx-moments__evidence">${esc(moment.evidence)}</p>
        </li>`
        )
        .join("")
    : `<li><span class="mx-timeline__none">Nothing to claim yet: the feed has no goals.</span></li>`;

  const note =
    match.status === "live"
      ? "This is a goal feed in play. The clock is derived from kick-off and marked with ~; the scorer and minute of each goal are recorded."
      : match.status === "finished"
        ? "Full time. The score and the goal list are final; a full event-level replay is available only for recorded matches."
        : match.status === "pending"
          ? "Kick-off has passed and the feed has not posted a final result. The score shown is the last one it recorded."
          : "Not started. Kick-off is shown in your local time.";

  return `
    <article class="mx-detail" aria-label="${esc(match.home)} versus ${esc(match.away)}">
      <div class="mx-detail__top">
        <p class="mx-detail__where">${esc(match.competition)} · ${esc(match.matchday)}</p>
        <button class="mx-detail__close" id="detail-close" type="button">Close</button>
      </div>
      <div class="mx-score">
        <p class="mx-score__team">${esc(match.home)}</p>
        <p class="mx-score__value">${esc(match.score)}</p>
        <p class="mx-score__team">${esc(match.away)}</p>
      </div>
      <p class="mx-detail__kickoff">
        ${liveBadge(match)}
        ${match.status === "live" && match.minute !== null ? ` · ~${match.minute}'` : ""}
        ${match.status === "scheduled" ? "" : ` · kick-off ${esc(timeOf(match.kickoff))}`}
      </p>
      <div class="mx-detail__grid">
        <section>
          <h3>Goals</h3>
          <ol class="mx-timeline">${goals}</ol>
        </section>
        <section>
          <h3>What the goals support</h3>
          <ul class="mx-moments">${moments}</ul>
        </section>
      </div>
      <p class="mx-detail__note">${esc(note)}</p>
    </article>`;
}

function renderLive(doc) {
  state.live = doc;
  state.league = doc.league;

  // The league list comes from the server's whitelist, so a league the page offers and
  // the server accepts cannot drift apart.
  if (doc.leagues.length && liveLeague.dataset.filled !== "1") {
    liveLeague.dataset.filled = "1";
    liveLeague.innerHTML = doc.leagues
      .map(
        (league) =>
          `<option value="${esc(league.shortcut)}">${esc(league.name)}</option>`
      )
      .join("");
  }
  liveLeague.value = state.league;

  const updated = timeOf(doc.fetched_at);
  setStatus(
    liveStatus,
    `${doc.league_name} · ${doc.matchday} · ${doc.matches.length} matches · ` +
      `updated ${updated}${doc.stale ? " (cached; the feed is unreachable)" : ""}`
  );

  liveList.innerHTML = doc.matches.map(renderLiveRow).join("");
  liveEmpty.hidden = true;

  const wanted = new URLSearchParams(location.search).get("live");
  const match = wanted
    ? doc.matches.find((item) => `${doc.league}/${item.id}` === wanted)
    : null;
  if (match) {
    liveDetail.innerHTML = renderDetail(match);
    liveDetail.hidden = false;
    $("detail-close").addEventListener("click", () => {
      liveDetail.hidden = true;
      liveDetail.innerHTML = "";
      setUrl({ live: null });
      scheduleRefresh(doc);
    });
  } else {
    liveDetail.hidden = true;
    liveDetail.innerHTML = "";
  }
  scheduleRefresh(doc);
}

async function loadLive(force = false) {
  const request = ++state.liveRequest;
  clearTimeout(state.liveTimer);
  if (force) setStatus(liveStatus, "Refreshing the feed…");
  try {
    const query = new URLSearchParams({ league: state.league });
    if (force) query.set("refresh", "true");
    const response = await fetch(`/api/live?${query}`, {
      headers: { accept: "application/json" },
    });
    const body = await response.json().catch(() => ({}));
    if (request !== state.liveRequest) return;
    if (!response.ok) {
      throw new Error(body.detail || `the server answered ${response.status}`);
    }
    renderLive(body);
  } catch (error) {
    if (request !== state.liveRequest) return;
    setStatus(
      liveStatus,
      `Could not load the live feed: ${error.message}. The overlay must be running.`,
      true
    );
    scheduleRefresh(state.live);
  }
}

function scheduleRefresh(doc) {
  clearTimeout(state.liveTimer);
  if (state.tab !== "live" || document.hidden) return;
  const delay = doc && doc.has_live ? 30_000 : 60_000;
  state.liveTimer = setTimeout(() => loadLive(false), delay);
}

liveLeague.addEventListener("change", () => {
  state.league = liveLeague.value;
  state.liveRequest += 1;
  liveDetail.hidden = true;
  liveDetail.innerHTML = "";
  setUrl({ live: null });
  loadLive(false);
});

$("live-refresh").addEventListener("click", () => loadLive(true));

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.tab === "live") loadLive(false);
});

liveList.addEventListener("click", (event) => {
  const row = event.target.closest("[data-live-id]");
  if (!row) return;
  const id = row.dataset.liveId;
  const match = state.live?.matches.find((item) => item.id === id);
  if (!match) return;
  setUrl({ live: `${state.league}/${id}` });
  liveDetail.innerHTML = renderDetail(match);
  liveDetail.hidden = false;
  $("detail-close").addEventListener("click", () => {
    liveDetail.hidden = true;
    liveDetail.innerHTML = "";
    setUrl({ live: null });
    scheduleRefresh(state.live);
  });
  liveDetail.scrollIntoView({ block: "nearest", behavior: "smooth" });
});

/* --- archive -------------------------------------------------------------- */

const arcCompetition = $("arc-competition");
const arcSeason = $("arc-season");
const arcSearch = $("arc-search");
const arcStatus = $("arc-status");
const arcList = $("arc-list");
const arcEmpty = $("arc-empty");

function fillCompetitions() {
  // Grouped by country so the list reads like a league table rather than an id dump.
  const groups = new Map();
  for (const competition of state.competitions) {
    const country = competition.country || "Other";
    if (!groups.has(country)) groups.set(country, []);
    groups.get(country).push(competition);
  }
  arcCompetition.innerHTML = [...groups.entries()]
    .map(
      ([country, items]) =>
        `<optgroup label="${esc(country)}">` +
        items
          .map(
            (item) =>
              `<option value="${item.id}">${esc(item.name)}</option>`
          )
          .join("") +
        `</optgroup>`
    )
    .join("");
}

function seasonsFor(competitionId) {
  const competition = state.competitions.find(
    (item) => item.id === Number(competitionId)
  );
  return competition ? competition.seasons : [];
}

function fillSeasons(competitionId, preferred = null) {
  const seasons = seasonsFor(competitionId);
  arcSeason.innerHTML = seasons
    .map(
      (season) => `<option value="${season.id}">${esc(season.name)}</option>`
    )
    .join("");
  const chosen =
    preferred !== null && seasons.some((season) => season.id === Number(preferred))
      ? String(preferred)
      : seasons.length
        ? String(seasons[0].id)
        : "";
  arcSeason.value = chosen;
  return chosen === "" ? null : Number(chosen);
}

function archiveRowMarkup(row) {
  const settled = row.played;
  return `
    <li>
      <a class="mx-row" href="${esc(row.path)}">
        <span class="mx-state">
          <span class="mx-badge mx-badge--soon">${esc(dateOf(row.date))}</span>
          <span class="mx-state__minute">${esc((row.kick_off || "").slice(0, 5))}</span>
        </span>
        <span class="mx-fixture">
          <span class="mx-team${settled && row.home_score > row.away_score ? " mx-team--winner" : ""}">
            <span>${esc(row.home_name)}</span>
            <span class="mx-team__score">${settled ? row.home_score : ""}</span>
          </span>
          <span class="mx-team${settled && row.away_score > row.home_score ? " mx-team--winner" : ""}">
            <span>${esc(row.away_name)}</span>
            <span class="mx-team__score">${settled ? row.away_score : ""}</span>
          </span>
        </span>
        <span class="mx-goals">${esc(row.stadium || "")}</span>
        <span class="mx-badge">${row.cached ? "Ready" : "Downloads"}</span>
      </a>
    </li>`;
}

function renderArchiveRows() {
  const query = arcSearch.value.trim().toLowerCase();
  const rows = query
    ? state.archiveRows.filter(
        (row) =>
          row.home_name.toLowerCase().includes(query) ||
          row.away_name.toLowerCase().includes(query)
      )
    : state.archiveRows;
  arcList.innerHTML = rows.map(archiveRowMarkup).join("");
  arcEmpty.hidden = rows.length !== 0;
  arcEmpty.textContent = query
    ? `No team called “${arcSearch.value.trim()}” in this season.`
    : "This season has no matches in the archive.";
  const ready = state.archiveRows.filter((row) => row.cached).length;
  setStatus(
    arcStatus,
    `${state.archiveRows.length} matches · ${ready} ready` +
      (query ? ` · ${rows.length} shown` : "")
  );
}

async function loadSeason() {
  if (state.competition === null || state.season === null) return;
  const request = ++state.archiveRequest;
  setStatus(arcStatus, "Loading the season…");
  try {
    const query = new URLSearchParams({
      competition: String(state.competition),
      season: String(state.season),
    });
    const response = await fetch(`/api/archive/matches?${query}`, {
      headers: { accept: "application/json" },
    });
    const body = await response.json().catch(() => ({}));
    if (request !== state.archiveRequest) return;
    if (!response.ok) {
      throw new Error(body.detail || `the server answered ${response.status}`);
    }
    state.archiveRows = body.matches;
    renderArchiveRows();
  } catch (error) {
    if (request !== state.archiveRequest) return;
    state.archiveRows = [];
    arcList.innerHTML = "";
    arcEmpty.hidden = false;
    arcEmpty.textContent = "The archive could not be loaded.";
    setStatus(arcStatus, `Could not load the season: ${error.message}`, true);
  }
}

async function loadCompetitions() {
  setStatus(arcStatus, "Loading the archive…");
  try {
    const response = await fetch("/api/archive/competitions", {
      headers: { accept: "application/json" },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || `the server answered ${response.status}`);
    }
    state.competitions = body.competitions;
    fillCompetitions();

    const wantedCompetition = Number(params.get("competition"));
    const wantedSeason = Number(params.get("season"));
    // The World Cup first, then the Premier League, then whatever the archive lists
    // first: an archive page that opens on the alphabetically first competition is
    // technically complete and practically a shrug.
    const selected =
      state.competitions.find((item) => item.id === wantedCompetition) ||
      state.competitions.find((item) => item.id === 43) ||
      state.competitions.find((item) => item.name === "Premier League") ||
      state.competitions[0];
    if (!selected) throw new Error("the archive published no competitions");
    state.competition = selected.id;
    arcCompetition.value = String(selected.id);
    state.season = fillSeasons(
      selected.id,
      Number.isFinite(wantedSeason) && wantedSeason > 0 ? wantedSeason : null
    );
    setUrl({ competition: state.competition, season: state.season });
    await loadSeason();
  } catch (error) {
    setStatus(arcStatus, `Could not load the archive: ${error.message}`, true);
    arcEmpty.hidden = false;
    arcEmpty.textContent = "The archive could not be loaded.";
  }
}

arcCompetition.addEventListener("change", () => {
  state.competition = Number(arcCompetition.value);
  state.season = fillSeasons(state.competition);
  setUrl({ competition: state.competition, season: state.season });
  loadSeason();
});

arcSeason.addEventListener("change", () => {
  state.season = Number(arcSeason.value);
  setUrl({ competition: state.competition, season: state.season });
  loadSeason();
});

arcSearch.addEventListener("input", renderArchiveRows);

/* --- start ---------------------------------------------------------------- */

const address = params.get("live");
if (address) {
  const [league, id] = address.split("/");
  if (league && id) {
    state.league = league;
    const option = [...liveLeague.options].find(
      (item) => item.value === league
    );
    if (option) liveLeague.value = league;
  }
}

if (params.get("competition")) {
  // showTab loads the archive on its first visit, so this is the one call.
  showTab("archive");
} else {
  showTab("live");
  loadLive(false);
}
