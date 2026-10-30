"""Live football, imported at the level of detail the feed actually has.

StatsBomb answers "what happened" for a match that is over. This module answers "what is
happening" for a match that is not: it reads OpenLigaDB's public matchday feed and turns a
fixture into the score, the clock, and every goal with its scorer and minute.

What this source is, and what it is not
---------------------------------------

OpenLigaDB (https://api.openligadb.de) is a free, keyless, community-maintained feed for
German football and a handful of competitions it also carries (the Premier League and the
Champions League among them). It is a *goal feed*, not an event feed: it records goals,
the half-time and full-time results, the teams and the kick-off, and it updates while a
match is in play. It does not record passes, carries, shots that missed, or lineups.

So everything below is built from goals and results alone, and nothing pretends
otherwise:

* **The clock is approximate.** The feed does not say what minute it is; it says when the
  match kicked off and when each goal was scored. :func:`clock_for` derives a minute from
  the wall clock, allowing fifteen minutes for the interval, and the page prints it with a
  leading ``~`` so a reader can tell a derived clock from a recorded one.
* **There are no lineups and no player-level statistics.** The only players named here are
  the scorers, because those are the only players the feed names. A team sheet would be an
  invention.
* **A goal is the unit of evidence.** The moments :func:`moments` reports — lead changes,
  a comeback, goals in a burst — are counts over the goal list, and each one carries the
  minutes it was counted from. They are claims this project can verify from the data in
  hand, which is the only kind it makes.

Attribution: data from OpenLigaDB (https://www.openligadb.de), used under its public API
terms. See ``docs/live-matches.md``.

Caching
-------

The feed is polled, not scraped per reader: :func:`matchday` keeps the last response for
:data:`CACHE_TTL_SECONDS` and serves it to every caller in that window. A stale copy is
better than a blank page when the network is down, so a failed refresh falls back to the
last good response and says so with ``stale`` rather than raising if one exists.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "BASE_URL",
    "CACHE_TTL_SECONDS",
    "LEAGUES",
    "Goal",
    "LiveMatch",
    "Matchday",
    "OpenLigaError",
    "cache_path",
    "clock_for",
    "leagues",
    "matchday",
    "moments",
    "parse_match",
    "parse_matchday",
]

#: Where the public feed lives.
BASE_URL = "https://api.openligadb.de"

#: How long one response is reused before the feed is asked again. Long enough that a page
#: polling every thirty seconds makes at most two upstream requests a minute per league,
#: short enough that a goal is on screen within half a minute of being recorded.
CACHE_TTL_SECONDS = 25.0

#: The competitions this project offers. A whitelist rather than "whatever the caller
#: typed": the league is part of an upstream URL, and an endpoint that proxies an arbitrary
#: path is a hole with a football painted on it. Each is checked to answer at the time of
#: writing; a league that stops answering fails visibly on its own page.
LEAGUES: dict[str, str] = {
    "bl1": "Bundesliga",
    "bl2": "2. Bundesliga",
    "bl3": "3. Liga",
    "dfb": "DFB-Pokal",
    "pl": "Premier League",
    "ucl": "Champions League",
}

#: A live match is treated as in play for this long after kick-off at most. A match that
#: starts late or whose result is never posted should stop calling itself live eventually;
#: three hours covers stoppage, the interval and a delayed second half without covering the
#: next fixture.
LIVE_WINDOW_SECONDS = 3 * 60 * 60


class OpenLigaError(RuntimeError):
    """The live feed could not be reached or understood."""


@dataclass(frozen=True, slots=True)
class Goal:
    """One goal, as the feed records it.

    ``side`` is derived from ``scoringTeamId`` and is the side the goal counts for, which
    is the side that benefited even when the scorer put it into their own net. ``own_goal``
    says that happened; ``scorer`` is still the person the feed names.
    """

    minute: int
    side: str
    scorer: str
    home_score: int
    away_score: int
    penalty: bool = False
    own_goal: bool = False
    overtime: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "minute": self.minute,
            "side": self.side,
            "scorer": self.scorer,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "penalty": self.penalty,
            "own_goal": self.own_goal,
            "overtime": self.overtime,
        }

    @property
    def label(self) -> str:
        """``23' — Name (pen)`` for a timeline row."""
        marks = []
        if self.penalty:
            marks.append("pen")
        if self.own_goal:
            marks.append("OG")
        suffix = f" ({', '.join(marks)})" if marks else ""
        return f"{self.minute}'{suffix} — {self.scorer}"


@dataclass(frozen=True, slots=True)
class LiveMatch:
    """A fixture from the current matchday, with the state it was in at ``fetched_at``.

    ``status`` is one of ``scheduled``, ``live``, ``finished`` or ``pending``. The last is
    the honest name for a match whose kick-off has passed, that the feed has not marked
    finished, and that is older than :data:`LIVE_WINDOW_SECONDS`: the result is missing, so
    calling it live or finished would both be guesses.
    """

    match_id: str
    league: str
    competition: str
    season: str
    matchday: str
    kickoff: str
    home: str
    away: str
    home_id: int
    away_id: int
    status: str
    goals: tuple[Goal, ...] = ()
    minute: int | None = None
    phase: str = ""
    home_score: int = 0
    away_score: int = 0
    location: str = ""
    fetched_at: str = ""
    stale: bool = False

    @property
    def is_live(self) -> bool:
        return self.status == "live"

    @property
    def finished(self) -> bool:
        return self.status == "finished"

    @property
    def path(self) -> str:
        """The address of this match on the matches page."""
        return f"/matches?live={self.league}/{self.match_id}"

    @property
    def score(self) -> str:
        return f"{self.home_score}–{self.away_score}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.match_id,
            "source": "openligadb",
            "league": self.league,
            "competition": self.competition,
            "season": self.season,
            "matchday": self.matchday,
            "kickoff": self.kickoff,
            "home": self.home,
            "away": self.away,
            "home_id": self.home_id,
            "away_id": self.away_id,
            "status": self.status,
            "minute": self.minute,
            "phase": self.phase,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "score": self.score,
            "location": self.location,
            "goals": [goal.to_dict() for goal in self.goals],
            "moments": moments(self),
            "path": self.path,
            "fetched_at": self.fetched_at,
            "stale": self.stale,
        }


@dataclass(frozen=True, slots=True)
class Matchday:
    """One league's current matchday, and the state of every fixture in it."""

    league: str
    name: str
    season: str
    matchday: str
    fetched_at: str
    matches: tuple[LiveMatch, ...] = field(default_factory=tuple)
    stale: bool = False

    @property
    def has_live(self) -> bool:
        return any(match.is_live for match in self.matches)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": "openligadb",
            "attribution": "OpenLigaDB",
            "league": self.league,
            "league_name": LEAGUES.get(self.league, self.league),
            "name": self.name,
            "season": self.season,
            "matchday": self.matchday,
            "fetched_at": self.fetched_at,
            "stale": self.stale,
            "has_live": self.has_live,
            "leagues": leagues(),
            "matches": [match.to_dict() for match in self.matches],
        }


# --- time and state ---------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(text: str) -> datetime | None:
    """A feed timestamp as an aware UTC datetime, or ``None`` if it is not one.

    OpenLigaDB writes ``2026-10-30T19:30:00Z`` for kick-off and occasionally a naive
    timestamp elsewhere. A naive one is read as UTC rather than as local time: the feed
    calls the field UTC, and guessing the machine's zone would move every kick-off by the
    difference between them.
    """
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clock_for(kickoff: datetime, now: datetime) -> tuple[int | None, str]:
    """The approximate match minute and the phase it falls in.

    The feed records kick-off and goal minutes, never the current minute, so this is a
    derivation and is labelled as one wherever it is shown. Fifteen minutes are allowed
    for the interval, and the clock stops at 90: a match in stoppage reads ``90'`` rather
    than inventing how much added time the referee signalled.

    Returns:
        ``(minute, phase)``. The minute is ``None`` before kick-off. ``phase`` is one of
        ``first_half``, ``half_time``, ``second_half``, ``stoppage``, ``full_time`` or
        ``not_started``.
    """
    elapsed = (now - kickoff).total_seconds() / 60.0
    if elapsed < 0:
        return None, "not_started"
    if elapsed <= 45:
        minute = max(1, int(elapsed))
        return minute, "first_half"
    if elapsed < 60:
        # The interval: the feed's own goal minutes resume at 46, so the clock waits at 45.
        return 45, "half_time"
    if elapsed < 105:
        minute = int(elapsed - 15)
        return min(90, max(46, minute)), "second_half"
    if elapsed < LIVE_WINDOW_SECONDS / 60:
        return 90, "stoppage"
    return 90, "full_time"


def _status_for(finished: bool, kickoff: datetime, now: datetime) -> str:
    if finished:
        return "finished"
    if now < kickoff:
        return "scheduled"
    if (now - kickoff).total_seconds() <= LIVE_WINDOW_SECONDS:
        return "live"
    return "pending"


def _final_score(payload: dict[str, Any], goals: Iterable[Goal]) -> tuple[int, int]:
    """The score to show: the after-90 result if the feed has posted one, else the goals.

    ``matchResults`` is authoritative when it exists — it is where a corrected result
    lands — and the goal list is the fallback for a match still in play, where the running
    score is exactly the last goal's score.
    """
    for result in payload.get("matchResults") or ():
        if result.get("resultTypeID") == 2:
            return int(result.get("pointsTeam1") or 0), int(result.get("pointsTeam2") or 0)
    last = list(goals)
    if last:
        return last[-1].home_score, last[-1].away_score
    return 0, 0


# --- parsing ----------------------------------------------------------------


def parse_match(
    payload: dict[str, Any],
    *,
    league: str,
    now: datetime | None = None,
    fetched_at: str | None = None,
    stale: bool = False,
) -> LiveMatch:
    """One fixture from the feed's JSON, with the state it has at ``now``.

    The function is pure apart from the default clock, which is why the tests pass one:
    every branch below — scheduled, half-time, a goal in stoppage, a match the feed has
    forgotten to finish — is decided here and nowhere else.
    """
    moment = now or _utc_now()
    kickoff_text = str(payload.get("matchDateTimeUTC") or payload.get("matchDateTime") or "")
    kickoff = _parse_iso(kickoff_text)
    if kickoff is None:
        raise OpenLigaError(f"match {payload.get('matchID')} has no readable kick-off")

    home_payload = payload.get("team1") or {}
    away_payload = payload.get("team2") or {}
    home_id = int(home_payload.get("teamId") or 0)
    away_id = int(away_payload.get("teamId") or 0)
    home = str(home_payload.get("teamName") or home_payload.get("shortName") or "home")
    away = str(away_payload.get("teamName") or away_payload.get("shortName") or "away")

    goals: list[Goal] = []
    previous = (0, 0)
    for raw in payload.get("goals") or ():
        minute = int(raw.get("matchMinute") or 0)
        scoring_id = raw.get("scoringTeamId")
        if scoring_id is None:
            # Some older entries only carry the running score. The side is the one whose
            # tally moved; a goal that moved both is not something this feed emits.
            home_now = int(raw.get("scoreTeam1") or 0)
            away_now = int(raw.get("scoreTeam2") or 0)
            side = "home" if home_now > previous[0] else "away"
        else:
            side = "home" if int(scoring_id) == home_id else "away"
        goal = Goal(
            minute=minute,
            side=side,
            scorer=str(raw.get("goalGetterName") or "unknown scorer"),
            home_score=int(raw.get("scoreTeam1") or 0),
            away_score=int(raw.get("scoreTeam2") or 0),
            penalty=bool(raw.get("isPenalty")),
            own_goal=bool(raw.get("isOwnGoal")),
            overtime=bool(raw.get("isOvertime")),
        )
        goals.append(goal)
        previous = (goal.home_score, goal.away_score)

    finished = bool(payload.get("matchIsFinished"))
    status = _status_for(finished, kickoff, moment)
    minute: int | None = None
    phase = "not_started"
    if status == "live":
        minute, phase = clock_for(kickoff, moment)
    elif status == "finished":
        minute, phase = 90, "full_time"
    home_score, away_score = _final_score(payload, goals)

    group = payload.get("group") or {}
    return LiveMatch(
        match_id=str(payload.get("matchID") or ""),
        league=league,
        competition=str(payload.get("leagueName") or LEAGUES.get(league, league)),
        season=str(payload.get("leagueSeason") or ""),
        matchday=str(group.get("groupName") or ""),
        kickoff=kickoff.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        home=home,
        away=away,
        home_id=home_id,
        away_id=away_id,
        status=status,
        goals=tuple(goals),
        minute=minute,
        phase=phase,
        home_score=home_score,
        away_score=away_score,
        location=str(payload.get("location") or ""),
        fetched_at=fetched_at or moment.isoformat().replace("+00:00", "Z"),
        stale=stale,
    )


def parse_matchday(
    payload: Iterable[dict[str, Any]],
    *,
    league: str,
    now: datetime | None = None,
    fetched_at: str | None = None,
    stale: bool = False,
) -> Matchday:
    """The current matchday from the feed's list of fixtures.

    The list is ordered by kick-off, then by the feed's own id, so the page does not
    reorder itself between refreshes: a match moving in the list while a reader is looking
    at it is indistinguishable from a different match.
    """
    moment = now or _utc_now()
    stamp = fetched_at or moment.isoformat().replace("+00:00", "Z")
    matches = [
        parse_match(item, league=league, now=moment, fetched_at=stamp, stale=stale)
        for item in payload
    ]
    matches.sort(key=lambda match: (match.kickoff, match.match_id))
    if not matches:
        raise OpenLigaError(f"the feed returned no fixtures for {league}")
    first = matches[0]
    return Matchday(
        league=league,
        name=LEAGUES.get(league, league),
        season=first.season,
        matchday=first.matchday,
        fetched_at=stamp,
        matches=tuple(matches),
        stale=stale,
    )


# --- moments ----------------------------------------------------------------


def _ordinal(value: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``11th`` — a minute as a person says it.

    Goal minutes are the one number on the live page a reader hears in their head, and
    "the 83th minute" reads as a machine talking. The teens are the exception: 11, 12 and
    13 take ``th`` despite ending in 1, 2 and 3.
    """
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def moments(match: LiveMatch) -> list[dict[str, str]]:
    """Claims about the match that the goal list alone can support.

    Each entry is a sentence and the evidence it was counted from. Nothing here reads
    possession, momentum or a player's overall performance, because a goal feed cannot
    support those. What it can support is the shape of the scoring: who led, when it
    changed, how quickly goals arrived, and whether a side came back.

    The result is deterministic and ordered by the minute the claim first became true.
    """
    goals = match.goals
    if not goals:
        return []

    found: list[tuple[int, dict[str, str]]] = []
    leader = 0
    changes = 0
    for goal in goals:
        sign = (goal.home_score > goal.away_score) - (goal.home_score < goal.away_score)
        if sign and leader and sign != leader:
            changes += 1
        if sign:
            leader = sign
    if changes:
        count = {1: "once", 2: "twice"}.get(changes, f"{changes} times")
        found.append(
            (
                1,
                {
                    "text": f"The lead changed hands {count}.",
                    "evidence": ", ".join(goal.label for goal in goals),
                },
            )
        )

    # The biggest deficit either side came back from. A draw after 0-2 is a comeback even
    # though nobody won, so this is about the scoreline rather than the result.
    deficits: dict[str, int] = {"home": 0, "away": 0}
    for goal in goals:
        deficits["home"] = max(deficits["home"], goal.away_score - goal.home_score)
        deficits["away"] = max(deficits["away"], goal.home_score - goal.away_score)
    for side, deficit in deficits.items():
        if deficit < 2:
            continue
        final = match.home_score if side == "home" else match.away_score
        other = match.away_score if side == "home" else match.home_score
        if final >= other:
            team = match.home if side == "home" else match.away
            found.append(
                (
                    2,
                    {
                        "text": f"{team} came back from {deficit} goals down.",
                        "evidence": f"{deficit}-goal deficit erased; final {match.score}",
                    },
                )
            )

    # Goals in a burst: the largest run of consecutive goals with no more than ten minutes
    # between the first and the last. A run of two is only reported when they are within
    # ten minutes of each other, which is what makes it a burst rather than a coincidence.
    best: list[Goal] = []
    for start in range(len(goals)):
        run = [goals[start]]
        for later in goals[start + 1 :]:
            if later.minute - run[0].minute <= 10:
                run.append(later)
            else:
                break
        if len(run) > len(best):
            best = run
    if len(best) >= 2:
        span = best[-1].minute - best[0].minute
        found.append(
            (
                3,
                {
                    "text": f"{len(best)} goals in {span} minutes.",
                    "evidence": ", ".join(f"{goal.minute}'" for goal in best),
                },
            )
        )

    last = goals[-1]
    found.append(
        (
            4,
            {
                "text": f"The latest goal was {last.scorer}'s in the "
                f"{_ordinal(last.minute)} minute.",
                "evidence": f"{last.minute}' · {last.home_score}–{last.away_score}",
            },
        )
    )
    found.sort(key=lambda item: item[0])
    return [entry for _, entry in found]


# --- fetching and caching ---------------------------------------------------


def data_root(root: Path | None = None) -> Path:
    """Where live responses are cached.

    The same root as the recorded matches (``PITCHFRAME_DATA_DIR`` or ``data/`` beside the
    source), under ``live/``. The cache exists so a page polling every thirty seconds does
    not become thirty requests a minute upstream; it is disposable and git-ignored.
    """
    if root is not None:
        return root
    override = os.environ.get("PITCHFRAME_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "data"


def cache_path(league: str, root: Path | None = None) -> Path:
    if league not in LEAGUES:
        raise OpenLigaError(
            f"unknown league {league!r}; live leagues are: {', '.join(LEAGUES)}"
        )
    return data_root(root) / "live" / f"{league}.json"


def _get_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": "pitchframe/0.1 (OpenLigaDB live feed)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise OpenLigaError(f"{url} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise OpenLigaError(f"could not reach {url}: {exc}") from None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise OpenLigaError(f"{url} did not return JSON: {exc}") from None


def _write_cache(path: Path, payload: list[dict[str, Any]], stamp: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps({"fetched_at": stamp, "matches": payload}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_cache(path: Path) -> tuple[str, list[dict[str, Any]], float] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        matches = payload["matches"]
        stamp = str(payload["fetched_at"])
        if not isinstance(matches, list):
            return None
        moment = _parse_iso(stamp)
        if moment is None:
            return None
        return stamp, matches, moment.timestamp()
    except (FileNotFoundError, KeyError, OSError, ValueError, TypeError):
        return None


def matchday(
    league: str = "bl1",
    *,
    root: Path | None = None,
    timeout: float = 12.0,
    force: bool = False,
    now: datetime | None = None,
    ttl: float = CACHE_TTL_SECONDS,
) -> Matchday:
    """The league's current matchday, from cache when it is fresh.

    Args:
        league: a shortcut from :data:`LEAGUES`.
        root: cache root; defaults to :func:`data_root`.
        timeout: per-request timeout in seconds.
        force: ignore a fresh cache and ask the feed now.
        now: the clock to interpret the response against; tests pass one.
        ttl: how long a cached response stays fresh.

    Raises:
        OpenLigaError: when the league is unknown, the feed fails and no cached copy
            exists, or the response is not a matchday. A failed refresh with a cached copy
            returns that copy with ``stale=True`` instead.
    """
    if league not in LEAGUES:
        raise OpenLigaError(
            f"unknown league {league!r}; live leagues are: {', '.join(LEAGUES)}"
        )
    moment = now or _utc_now()
    path = cache_path(league, root)
    cached = _read_cache(path)

    if cached is not None and not force:
        stamp, payload, fetched = cached
        if (moment.timestamp() - fetched) <= ttl:
            return parse_matchday(
                payload, league=league, now=moment, fetched_at=stamp, stale=False
            )

    url = f"{BASE_URL}/getmatchdata/{league}"
    try:
        payload = _get_json(url, timeout)
    except OpenLigaError:
        if cached is not None:
            stamp, payload, _ = cached
            return parse_matchday(
                payload, league=league, now=moment, fetched_at=stamp, stale=True
            )
        raise
    if not isinstance(payload, list) or not payload:
        if cached is not None:
            stamp, payload, _ = cached
            return parse_matchday(
                payload, league=league, now=moment, fetched_at=stamp, stale=True
            )
        raise OpenLigaError(f"the feed returned no fixtures for {league}")

    stamp = moment.isoformat().replace("+00:00", "Z")
    _write_cache(path, payload, stamp)
    return parse_matchday(
        payload, league=league, now=moment, fetched_at=stamp, stale=False
    )


def leagues() -> list[dict[str, str]]:
    """The live competitions, in the order the page offers them."""
    return [{"shortcut": key, "name": name} for key, name in LEAGUES.items()]
