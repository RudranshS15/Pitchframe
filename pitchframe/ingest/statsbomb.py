"""StatsBomb open data, imported into Pitchframe's own match vocabulary.

The generator answers "what would a match look like"; this module answers "what happened".
It reads a real match's event feed and lineups and produces the same
:class:`~pitchframe.schema.Match` the rest of the project already speaks, so the statistics,
the claim layer and the replay run on real football without a special case for it.

What is measured and what is derived
------------------------------------

The feed records events, not continuous play, so three quantities the generator can state
outright have to be derived here. Each is documented at the function that derives it and
none of them is presented as if it were measured:

* **Pressure.** StatsBomb marks ``under_pressure`` on some events and emits a ``Pressure``
  event when a defender closes a carrier down. :func:`_pressure_at` turns those two facts
  into the ``0..1`` scalar the metric registry expects: a floor while the flag is set, and
  a distance falloff from the nearest opposing pressure event in the same two seconds.
* **Speed.** There is no speed channel in an event feed. A carry has a duration and two
  locations, so its speed is distance over time — the one honest reading available, and the
  only event type it is attached to. A shot has no measured speed here and none is invented.
* **Pass success.** The absence of ``pass.outcome`` is a completed pass. Every outcome the
  feed does record is a failure.

Coordinates are rotated into the home team's attacking frame, so ``(0, 0)`` is the home
team's own goal line exactly as in the generator. The feed normalises every event to the
acting team's attacking direction; the away side is rotated 180 degrees, not merely
mirrored, because a mirror would put its left wing on the home side's right.

The clock is elapsed match time. StatsBomb restarts its minute counter at 45 for the second
half while first-half stoppage can run past it, so the adapter builds a period table from
the ``Half Start``/``Half End`` events and lays the periods end to end. A match with
stoppage therefore runs past 90:00 rather than folding it back into the 90th minute.

Attribution and licence
-----------------------

Data: StatsBomb Open Data (https://github.com/statsbomb/open-data). The adapter downloads
on demand and never modifies the source; the cache is ignored by version control. Any use
of the data is subject to StatsBomb's Public Data User Agreement and the terms in that
repository, including attribution where the data is shown. See ``docs/real-matches.md``.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..schema import Match, MatchEvent, Player, Squad, Team

__all__ = [
    "BASE_URL",
    "DEFAULT_COMPETITION",
    "DEFAULT_SEASON",
    "MatchMeta",
    "SeasonMatch",
    "StatsBombError",
    "available",
    "cache_dir",
    "competitions",
    "ensure",
    "fetch",
    "is_cached",
    "load",
    "parse",
    "read_meta",
    "season_matches",
]

#: Where the open-data repository serves its JSON.
BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

#: FIFA World Cup 2018: the season the flagship example comes from (the final, match 8658).
DEFAULT_COMPETITION = 43
DEFAULT_SEASON = 3

#: The pitch the feed uses, in its own units. Kept here rather than imported from the
#: schema because it describes the *source*, not the domain.
_SOURCE_LENGTH = 120.0
_SOURCE_WIDTH = 80.0

#: One unit of the feed's length/width axis in metres, for distance-over-time speeds.
_SOURCE_METRE_X = 105.0 / _SOURCE_LENGTH
_SOURCE_METRE_Y = 68.0 / _SOURCE_WIDTH

#: Where each period starts on the match clock, and how long it is nominally. Stoppage
#: time is folded into the end of the half it belongs to rather than added between the
#: halves: the alternative — laying periods end to end — shifts every second-half minute
#: by the first half's stoppage, which turned a 58th-minute goal into the 61st. A goal in
#: the 90+3rd shows as 90', which is a smaller and much more conventional distortion.
_PERIOD_CLOCK = {1: (0, 2700), 2: (2700, 2700), 3: (5400, 900), 4: (6300, 900)}

#: StatsBomb position names to the short codes Pitchframe's team sheets use.
_POSITIONS = {
    "Goalkeeper": "GK",
    "Right Back": "RB",
    "Left Back": "LB",
    "Center Back": "CB",
    "Right Center Back": "RCB",
    "Left Center Back": "LCB",
    "Right Wing Back": "RWB",
    "Left Wing Back": "LWB",
    "Defensive Midfield": "DM",
    "Center Defensive Midfield": "CDM",
    "Right Defensive Midfield": "RDM",
    "Left Defensive Midfield": "LDM",
    "Center Midfield": "CM",
    "Right Center Midfield": "RCM",
    "Left Center Midfield": "LCM",
    "Right Midfield": "RM",
    "Left Midfield": "LM",
    "Attacking Midfield": "AM",
    "Center Attacking Midfield": "CAM",
    "Right Attacking Midfield": "RAM",
    "Left Attacking Midfield": "LAM",
    "Right Wing": "RW",
    "Left Wing": "LW",
    "Striker": "ST",
    "Center Forward": "CF",
    "Right Center Forward": "RCF",
    "Left Center Forward": "LCF",
    "Secondary Striker": "SS",
}


class StatsBombError(RuntimeError):
    """A real match could not be downloaded, read or understood."""


@dataclass(frozen=True, slots=True)
class MatchMeta:
    """The facts about a real match that are not in its event stream.

    Home and away are not a property of the events: the feed describes actions from the
    acting team's point of view and never says which end is whose. The season index does,
    which is why :func:`fetch` downloads it once per competition and season.
    """

    match_id: str
    competition: str
    season: str
    date: str
    kick_off: str
    stadium: str
    home_id: int
    home_name: str
    away_id: int
    away_name: str

    @property
    def label(self) -> str:
        """``France 4–2 Croatia``-shaped is the caller's job; this is the fixture name."""
        return f"{self.home_name} v {self.away_name}"

    @property
    def description(self) -> str:
        """``FIFA World Cup 2018 · 15 July 2018`` for a catalogue card or a footer."""
        parts = [f"{self.competition} {self.season}".strip()]
        if self.date:
            parts.append(self.date)
        return " · ".join(part for part in parts if part)

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "competition": self.competition,
            "season": self.season,
            "date": self.date,
            "kick_off": self.kick_off,
            "stadium": self.stadium,
            "home_id": self.home_id,
            "home_name": self.home_name,
            "away_id": self.away_id,
            "away_name": self.away_name,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MatchMeta":
        return cls(
            match_id=str(payload["match_id"]),
            competition=str(payload.get("competition", "")),
            season=str(payload.get("season", "")),
            date=str(payload.get("date", "")),
            kick_off=str(payload.get("kick_off", "")),
            stadium=str(payload.get("stadium", "")),
            home_id=int(payload["home_id"]),
            home_name=str(payload["home_name"]),
            away_id=int(payload["away_id"]),
            away_name=str(payload["away_name"]),
        )

    @classmethod
    def from_index_entry(cls, entry: dict[str, Any]) -> "MatchMeta":
        home = entry["home_team"]
        away = entry["away_team"]
        stadium = entry.get("stadium") or {}
        return cls(
            match_id=str(entry["match_id"]),
            competition=str(entry.get("competition", {}).get("competition_name", "")),
            season=str(entry.get("season", {}).get("season_name", "")),
            date=str(entry.get("match_date", "")),
            kick_off=str(entry.get("kick_off", "")),
            stadium=str(stadium.get("name") or entry.get("competition_stadium") or ""),
            home_id=int(home["home_team_id"]),
            home_name=str(home["home_team_name"]),
            away_id=int(away["away_team_id"]),
            away_name=str(away["away_team_name"]),
        )


# --- cache ------------------------------------------------------------------


def data_root() -> Path:
    """Where downloaded matches live.

    ``PITCHFRAME_DATA_DIR`` first, because a container or a wheel install may not own the
    repository; otherwise ``data/statsbomb`` beside the source tree, which is the one a
    developer finds without being told. The directory is created on demand and ignored by
    version control: the data is downloaded, not vendored.
    """
    override = os.environ.get("PITCHFRAME_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "statsbomb"


def _clean_id(match_id: str | int) -> str:
    """A StatsBomb match id, which is a positive integer and nothing else.

    The id becomes a directory name, and the id is user input on the HTTP surface, so
    ``..`` in it would read outside the cache. Validating here rather than at each caller
    makes every path — CLI, server, tests — reject the same shapes.
    """
    text = str(match_id)
    if not text.isdigit() or len(text) > 12:
        raise StatsBombError(
            f"match id {match_id!r} is not a StatsBomb id; ids are digits (for example 8658)"
        )
    return text


def cache_dir(match_id: str | int, root: Path | None = None) -> Path:
    """The directory one match's three files live in."""
    return (root or data_root()) / _clean_id(match_id)


def is_cached(match_id: str | int, root: Path | None = None) -> bool:
    """Whether a match can be loaded without the network."""
    directory = cache_dir(match_id, root)
    return all((directory / name).is_file() for name in ("match.json", "events.json", "lineups.json"))


def read_meta(match_id: str | int, root: Path | None = None) -> MatchMeta:
    """Read a cached match's metadata, or say how to fetch it."""
    path = cache_dir(match_id, root) / "match.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StatsBombError(
            f"match {match_id} is not cached. Fetch it with: "
            f"python -m pitchframe --fetch {match_id}"
        ) from None
    except (OSError, ValueError) as exc:
        raise StatsBombError(f"cannot read {path}: {exc}") from None
    return MatchMeta.from_dict(payload)


def available(root: Path | None = None) -> tuple[MatchMeta, ...]:
    """Every cached match, newest first.

    The catalogue and the CLI both read this, so a match appears exactly where it was
    downloaded and a cache with nothing in it honestly reports nothing.
    """
    directory = root or data_root()
    if not directory.is_dir():
        return ()
    matches: list[MatchMeta] = []
    for child in directory.iterdir():
        if child.is_dir() and (child / "match.json").is_file():
            try:
                matches.append(read_meta(child.name, directory))
            except StatsBombError:
                continue
    matches.sort(key=lambda meta: (meta.date, meta.match_id), reverse=True)
    return tuple(matches)


# --- downloading ------------------------------------------------------------


def _get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "pitchframe/0.1 (StatsBomb open-data importer)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise StatsBombError(f"the open data has nothing at {url}") from None
        raise StatsBombError(f"{url} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise StatsBombError(f"could not reach {url}: {exc}") from None


def _get_json(url: str, timeout: float) -> Any:
    raw = _get(url, timeout)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise StatsBombError(f"{url} did not return JSON: {exc}") from None


def _write_json(path: Path, payload: Any) -> None:
    """Write a file atomically, so a cancelled fetch cannot leave half a match behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def fetch(
    match_id: str | int,
    *,
    competition: int = DEFAULT_COMPETITION,
    season: int = DEFAULT_SEASON,
    root: Path | None = None,
    timeout: float = 30.0,
    force: bool = False,
) -> MatchMeta:
    """Download one match's events and lineups, and the season index that places it.

    Args:
        match_id: StatsBomb's match id.
        competition: competition id (43 is the World Cup).
        season: season id (3 is 2018, 106 is 2022).
        root: cache directory; defaults to :func:`data_root`.
        timeout: per-request timeout in seconds.
        force: re-download even when a cached copy is complete.

    Returns:
        The match's metadata.

    Raises:
        StatsBombError: when the season index does not contain the match, or a download
            fails. The message names the competition and season searched, because the most
            common mistake is asking for a match id from a different season.
    """
    match_id = _clean_id(match_id)
    directory = root or data_root()
    if not force and is_cached(match_id, directory):
        return read_meta(match_id, directory)

    index_path = directory / "_seasons" / f"{competition}-{season}.json"
    if force or not index_path.is_file():
        index = _get_json(
            f"{BASE_URL}/matches/{competition}/{season}.json", timeout
        )
        if not isinstance(index, list):
            raise StatsBombError(
                f"{BASE_URL}/matches/{competition}/{season}.json was not a season index"
            )
        _write_json(index_path, index)
    else:
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StatsBombError(f"cannot read {index_path}: {exc}") from None

    entry = next(
        (item for item in index if str(item.get("match_id")) == match_id),
        None,
    )
    if entry is None:
        available_ids = ", ".join(str(item.get("match_id")) for item in index[:8])
        raise StatsBombError(
            f"match {match_id} is not in competition {competition}, season {season}. "
            f"That season's first match ids are: {available_ids}… "
            "Pass the competition and season it belongs to: "
            f"--competition <id> --season <id>"
        )

    meta = MatchMeta.from_index_entry(entry)
    events = _get_json(f"{BASE_URL}/events/{match_id}.json", timeout)
    lineups = _get_json(f"{BASE_URL}/lineups/{match_id}.json", timeout)
    if not isinstance(events, list) or not events:
        raise StatsBombError(f"match {match_id} has no events")
    if not isinstance(lineups, list) or not lineups:
        raise StatsBombError(f"match {match_id} has no lineups")

    _write_json(directory / match_id / "match.json", meta.to_dict())
    _write_json(directory / match_id / "events.json", events)
    _write_json(directory / match_id / "lineups.json", lineups)
    return meta


def load(match_id: str | int, *, root: Path | None = None) -> Match:
    """Load a cached match and normalise it into the domain schema.

    Raises:
        StatsBombError: if the match is not cached or the payloads cannot be read.
    """
    directory = cache_dir(match_id, root)
    try:
        events = json.loads((directory / "events.json").read_text(encoding="utf-8"))
        lineups = json.loads((directory / "lineups.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StatsBombError(
            f"match {match_id} is not cached. Fetch it with: "
            f"python -m pitchframe --fetch {match_id}"
        ) from None
    except (OSError, ValueError) as exc:
        raise StatsBombError(f"cannot read the cached match {match_id}: {exc}") from None
    meta = read_meta(match_id, root)
    return parse(events, lineups, meta)


# --- normalising ------------------------------------------------------------


def _side_for(team_id: int | None, meta: MatchMeta) -> Team:
    return "home" if team_id == meta.home_id else "away"


def _norm_xy(location: Sequence[float] | None, side: Team) -> tuple[float, float] | None:
    """A feed coordinate in the home team's attacking frame.

    The feed orients every event to the acting team's attack, so the away side is rotated
    180 degrees: both axes flip. ``(0, 0)`` is then the home team's own goal line, which is
    the frame the rest of the project measures in.
    """
    if not location or len(location) < 2:
        return None
    x = float(location[0]) / _SOURCE_LENGTH * 100.0
    y = float(location[1]) / _SOURCE_WIDTH * 100.0
    if side == "away":
        x, y = 100.0 - x, 100.0 - y
    return (round(x, 3), round(y, 3))


def _distance_metres(a: Sequence[float], b: Sequence[float]) -> float:
    dx = (float(a[0]) - float(b[0])) * _SOURCE_METRE_X
    dy = (float(a[1]) - float(b[1])) * _SOURCE_METRE_Y
    return math.hypot(dx, dy)


def _position_code(name: str | None, *, started: bool) -> str:
    if name and name in _POSITIONS:
        return _POSITIONS[name]
    return "CM" if started else "SUB"


def _started(player: dict[str, Any]) -> bool:
    """Whether a lineup entry was in the XI: a position that began at 00:00 of period 1."""
    return any(
        position.get("from_period") == 1 and str(position.get("from")) == "00:00"
        for position in player.get("positions") or ()
    )


def _squad(
    entry: dict[str, Any],
    side: Team,
    used: set[int],
) -> Squad:
    """One lineup as a team sheet.

    ``players`` is everyone who took part — the starting eleven and any substitute who
    came on. A substitute who played has events and deserves a mark, so rating them would
    be impossible if they were left on the bench; ``bench`` is therefore only the players
    the match never used, exactly as distinct as the schema says.
    """
    players: list[Player] = []
    bench: list[Player] = []
    for item in entry.get("lineup") or ():
        player_id = int(item["player_id"])
        started = _started(item)
        positions = item.get("positions") or ()
        name = item.get("player_nickname") or item.get("player_name") or str(player_id)
        player = Player(
            id=str(player_id),
            name=str(name),
            team=side,
            position=_position_code(
                positions[0].get("position") if positions else None, started=started
            ),
            shirt=int(item.get("jersey_number") or 0),
        )
        if started or player_id in used:
            players.append(player)
        else:
            bench.append(player)
    players.sort(key=lambda player: (player.shirt, player.id))
    bench.sort(key=lambda player: (player.shirt, player.id))
    return Squad(
        team=side,
        name=str(entry.get("team_name") or ""),
        players=tuple(players),
        bench=tuple(bench),
    )


def _clock(event: dict[str, Any]) -> int:
    """The match clock, in seconds, for a feed event.

    Each period occupies its nominal slot — 0-45, 45-90, 90-105, 105-120 — and anything
    past the slot's end is clamped to it, so first-half stoppage sits at 45:00 and second-
    half stoppage at 90:00 rather than pushing the rest of the match later. The clock stays
    monotonic, which the replay's window arithmetic depends on.
    """
    period = int(event.get("period") or 1)
    start, span = _PERIOD_CLOCK.get(period, (0, 2700))
    raw = float(event.get("minute") or 0) * 60.0 + float(event.get("second") or 0)
    return max(0, int(round(start + min(max(raw - start, 0.0), float(span)))))


class _PressureIndex:
    """The pressure scalar for an event, derived from the feed's pressure events.

    A pressure event records where a defender closed down; ``under_pressure`` records that
    the carrier was closed down at all. Neither is a magnitude, so the magnitude is a
    distance falloff from the nearest opposing pressure inside the same two seconds, with
    the flag as a floor. That is a derivation, and the docs say so.
    """

    def __init__(self, events: Iterable[dict[str, Any]], meta: MatchMeta) -> None:
        self._points: dict[tuple[int, Team], list[tuple[int, tuple[float, float]]]] = {}
        for event in events:
            if ((event.get("type") or {}).get("name")) != "Pressure":
                continue
            location = event.get("location")
            team_id = (event.get("team") or {}).get("id")
            if not location or team_id is None:
                continue
            period = int(event.get("period") or 1)
            side = _side_for(team_id, meta)
            self._points.setdefault((period, side), []).append(
                (_clock(event), (float(location[0]), float(location[1])))
            )

    def at(
        self,
        *,
        period: int,
        side: Team,
        clock: int,
        location: Sequence[float] | None,
        under_pressure: bool,
    ) -> float:
        best = 0.6 if under_pressure else 0.0
        opposing: Team = "away" if side == "home" else "home"
        source_clock = int(clock)
        for point_clock, point in self._points.get((period, opposing), ()):
            if abs(point_clock - source_clock) > 2:
                continue
            distance = math.dist(
                (float(location[0]), float(location[1])), point
            )
            if distance < 30.0:
                best = max(best, 1.0 - distance / 30.0)
        return round(min(1.0, best), 3) if best else 0.0


def _outcome_is_goal(shot: dict[str, Any]) -> bool:
    return ((shot.get("outcome") or {}).get("name")) == "Goal"


def _shot_outcome(shot: dict[str, Any]) -> str:
    """``success`` for a strike that beat the keeper or was on target, else ``fail``.

    The schema has two outcomes rather than the feed's eight. "On target" is the line the
    ratings panel counts, so a save and a goal are successes and a block, a post or a
    miss is not. The distinction between a goal and a save is carried by the goal event
    that follows the shot, not by this field.
    """
    name = ((shot.get("outcome") or {}).get("name")) or ""
    return "success" if name in {"Goal", "Saved", "Saved to Post"} else "fail"


def _pass_outcome(payload: dict[str, Any]) -> str:
    return "fail" if payload.get("outcome") else "success"


def _duel_outcome(payload: dict[str, Any]) -> str:
    name = ((payload.get("outcome") or {}).get("name")) or ""
    return "success" if name.startswith(("Won", "Success")) else "fail"


def parse(
    events: Sequence[dict[str, Any]],
    lineups: Sequence[dict[str, Any]],
    meta: MatchMeta | dict[str, Any],
) -> Match:
    """Normalise one StatsBomb match into :class:`~pitchframe.schema.Match`.

    Args:
        events: the match's ``events.json`` payload.
        lineups: the match's ``lineups.json`` payload.
        meta: :class:`MatchMeta`, or the dict :func:`fetch` stores beside the payloads.

    Raises:
        StatsBombError: when the lineups do not name both teams the index recorded, or the
            payloads are not the shapes this adapter understands.
    """
    if isinstance(meta, dict):
        meta = MatchMeta.from_dict(meta)

    by_team = {int(entry["team_id"]): entry for entry in lineups if entry.get("team_id") is not None}
    if meta.home_id not in by_team or meta.away_id not in by_team:
        raise StatsBombError(
            f"the lineups for match {meta.match_id} do not name both teams "
            f"({meta.home_name} and {meta.away_name})"
        )

    used = {
        int(event["player"]["id"])
        for event in events
        if event.get("player") and event["player"].get("id") is not None
    }
    home = _squad(by_team[meta.home_id], "home", used)
    away = _squad(by_team[meta.away_id], "away", used)
    squads: dict[Team, Squad] = {"home": home, "away": away}
    by_id: dict[str, Player] = {
        player.id: player for squad in squads.values() for player in squad.players
    }

    pressures = _PressureIndex(events, meta)
    normalised: list[MatchEvent] = []
    kickoff_player = home.players[0] if home.players else None
    if kickoff_player is not None:
        normalised.append(
            MatchEvent(
                id=f"{meta.match_id}-kickoff",
                match_id=meta.match_id,
                clock=0,
                type="kickoff",
                team="home",
                player=kickoff_player,
                detail="kick-off",
            )
        )

    def player_for(event: dict[str, Any], side: Team) -> Player | None:
        payload = event.get("player") or {}
        if payload.get("id") is None:
            return None
        player_id = str(payload["id"])
        known = by_id.get(player_id)
        if known is not None:
            return known
        fallback = Player(
            id=player_id,
            name=str(payload.get("name") or player_id),
            team=side,
            position=_position_code(
                ((event.get("position") or {}).get("name")), started=False
            ),
            shirt=0,
        )
        by_id[player_id] = fallback
        return fallback

    half_time_added = False
    for event in events:
        name = ((event.get("type") or {}).get("name")) or ""
        period = int(event.get("period") or 1)
        clock = _clock(event)
        team_id = (event.get("team") or {}).get("id")
        if team_id is None:
            continue
        side = _side_for(int(team_id), meta)

        if name == "Half End" and not half_time_added and period == 1:
            # The first half's actual end, stoppage included. Placed before the second
            # half's first event, which is where the feed puts it too.
            marker = kickoff_player or player_for(event, "home")
            if marker is not None:
                normalised.append(
                    MatchEvent(
                        id=f"{meta.match_id}-half-time",
                        match_id=meta.match_id,
                        clock=clock,
                        type="half_time",
                        team="home",
                        player=marker,
                        detail="half-time",
                    )
                )
            half_time_added = True
            continue

        if name in {
            "Starting XI",
            "Half Start",
            "Half End",
            "Camera On",
            "Camera off",
            # The pair's other half carries the scorer and the location, both consumed by
            # the Own Goal For branch below; emitting it again would count the goal twice.
            "Own Goal Against",
        }:
            continue

        if name == "Own Goal For":
            # Read before the player lookup, because this event has no player: the feed
            # attributes an own goal to the side that benefited and files the scorer under
            # the other half of the pair. The score is the one thing an own goal must get
            # right, so it does not depend on a player being present.
            against = next(
                (
                    other
                    for other in events
                    if ((other.get("type") or {}).get("name")) == "Own Goal Against"
                    and other.get("possession") == event.get("possession")
                    and int(other.get("minute") or 0) == int(event.get("minute") or 0)
                ),
                None,
            )
            scorer = ((against or {}).get("player") or {}).get("name")
            normalised.append(
                MatchEvent(
                    id=f"{meta.match_id}-{event.get('index')}-own-goal",
                    match_id=meta.match_id,
                    clock=clock,
                    type="goal",
                    team=side,
                    # Not a player in either squad: the scorer put it into their own net,
                    # so crediting them with a goal for the side that benefited would be a
                    # second error on top of the first. The ratings panel rates squads, so
                    # this id is counted by the score and by the conceding keeper and by
                    # nobody's individual mark.
                    player=Player(
                        id=f"own-goal-{meta.match_id}-{event.get('index')}",
                        name=str(scorer or "own goal"),
                        team=side,
                        position="OG",
                        shirt=0,
                    ),
                    # The feed records the location in the scoring team's own frame — not
                    # the frame of the side that benefited. Rotating it by the scorer's
                    # side then places it at the goal that was hit.
                    from_xy=_norm_xy(
                        (against or {}).get("location"),
                        _side_for(
                            int(((against or {}).get("team") or {}).get("id", 0)), meta
                        ),
                    ),
                    outcome="success",
                    detail="own goal",
                    possession_id=int(event.get("possession") or 0),
                )
            )
            continue

        player = player_for(event, side)
        if player is None:
            continue

        location = event.get("location")
        under_pressure = bool(event.get("under_pressure"))
        pressure = (
            pressures.at(
                period=period,
                side=side,
                clock=int(event.get("minute") or 0) * 60 + int(event.get("second") or 0),
                location=location,
                under_pressure=under_pressure,
            )
            if name in {"Pass", "Carry", "Shot", "Duel"}
            else None
        )

        if name == "Pass":
            payload = event.get("pass") or {}
            normalised.append(
                MatchEvent(
                    id=f"{meta.match_id}-{event.get('index')}",
                    match_id=meta.match_id,
                    clock=clock,
                    type="pass",
                    team=side,
                    player=player,
                    from_xy=_norm_xy(location, side),
                    to_xy=_norm_xy(payload.get("end_location"), side),
                    outcome=_pass_outcome(payload),
                    pressure=pressure,
                    detail=str(((payload.get("height") or {}).get("name")) or "pass"),
                    possession_id=int(event.get("possession") or 0),
                )
            )
        elif name == "Carry":
            payload = event.get("carry") or {}
            end = payload.get("end_location")
            speed: float | None = None
            duration = event.get("duration")
            if location and end and duration:
                metres = _distance_metres(location, end)
                if metres >= 0.3 and float(duration) > 0:
                    # Carries include the ball rolling beside a player; 40 km/h is the cap
                    # a plausible sprint-and-dribble can reach, not a measured maximum.
                    speed = round(min(40.0, metres / float(duration) * 3.6), 1)
            normalised.append(
                MatchEvent(
                    id=f"{meta.match_id}-{event.get('index')}",
                    match_id=meta.match_id,
                    clock=clock,
                    type="carry",
                    team=side,
                    player=player,
                    from_xy=_norm_xy(location, side),
                    to_xy=_norm_xy(end, side),
                    outcome="success",
                    speed_kmh=speed,
                    pressure=pressure,
                    detail="carry",
                    possession_id=int(event.get("possession") or 0),
                )
            )
        elif name == "Shot":
            payload = event.get("shot") or {}
            origin = _norm_xy(location, side)
            target = _norm_xy(payload.get("end_location"), side)
            shot_type = str(((payload.get("type") or {}).get("name")) or "shot").lower()
            normalised.append(
                MatchEvent(
                    id=f"{meta.match_id}-{event.get('index')}",
                    match_id=meta.match_id,
                    clock=clock,
                    type="shot",
                    team=side,
                    player=player,
                    from_xy=origin,
                    to_xy=target,
                    outcome=_shot_outcome(payload),
                    pressure=pressure,
                    detail=shot_type,
                    possession_id=int(event.get("possession") or 0),
                )
            )
            if _outcome_is_goal(payload):
                # The pair is deliberate: the ratings panel reads a shot on target as a
                # stop unless a goal for the same side follows on the same clock, which
                # is exactly how the generator emits one.
                normalised.append(
                    MatchEvent(
                        id=f"{meta.match_id}-{event.get('index')}-goal",
                        match_id=meta.match_id,
                        clock=clock,
                        type="goal",
                        team=side,
                        player=player,
                        from_xy=origin,
                        to_xy=target,
                        outcome="success",
                        pressure=pressure,
                        detail=f"{squads[side].name} score",
                        possession_id=int(event.get("possession") or 0),
                    )
                )
        elif name == "Duel":
            payload = event.get("duel") or {}
            if ((payload.get("type") or {}).get("name")) != "Tackle":
                continue
            normalised.append(
                MatchEvent(
                    id=f"{meta.match_id}-{event.get('index')}",
                    match_id=meta.match_id,
                    clock=clock,
                    type="tackle",
                    team=side,
                    player=player,
                    from_xy=_norm_xy(location, side),
                    outcome=_duel_outcome(payload),
                    pressure=pressure,
                    detail="tackle",
                    possession_id=int(event.get("possession") or 0),
                )
            )

    # The end of the match is the last clock the feed records, including the markers that
    # are skipped as events — a match whose last pass is at 89:30 still ran to 90:00, and
    # a replay that stopped at the last touch would never reach full time.
    final_clock = max((_clock(event) for event in events), default=0)
    if kickoff_player is not None and final_clock > 0:
        normalised.append(
            MatchEvent(
                id=f"{meta.match_id}-full-time",
                match_id=meta.match_id,
                clock=final_clock,
                type="full_time",
                team="home",
                player=kickoff_player,
                detail="full-time",
            )
        )
    if not normalised:
        raise StatsBombError(f"match {meta.match_id} produced no events pitchframe understands")

    return Match(
        id=meta.match_id,
        home=home,
        away=away,
        events=tuple(normalised),
    )


# --- the archive ------------------------------------------------------------
#
# The adapter above answers for one match. These answer for the archive it belongs to:
# which competitions exist, which seasons each has, and which matches a season holds. They
# download the same index files :func:`fetch` already needs, into the same cache, so
# browsing and downloading share one copy of every season and a match opened from the
# archive is fetched with the competition and season the archive named rather than with a
# guess at the default.


def competitions(
    *, root: Path | None = None, timeout: float = 30.0, force: bool = False
) -> tuple[dict[str, Any], ...]:
    """Every competition in the archive, with the seasons it publishes.

    The open data describes itself in one file, ``competitions.json``, with a row per
    competition-season. Grouping it here rather than in the page means the page offers
    exactly the pairs the archive can answer for: a season is listed because its index
    exists, not because somebody typed it into a dropdown.

    Args:
        root: cache directory; defaults to :func:`data_root`.
        timeout: per-request timeout in seconds.
        force: re-download even when a cached copy is present.

    Raises:
        StatsBombError: when the file cannot be fetched or is not the expected list.
    """
    directory = root or data_root()
    path = directory / "_catalogue" / "competitions.json"
    if force or not path.is_file():
        payload = _get_json(f"{BASE_URL}/competitions.json", timeout)
        if not isinstance(payload, list) or not payload:
            raise StatsBombError(f"{BASE_URL}/competitions.json was not a catalogue")
        _write_json(path, payload)
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StatsBombError(f"cannot read {path}: {exc}") from None

    grouped: dict[int, dict[str, Any]] = {}
    for entry in payload:
        try:
            competition_id = int(entry["competition_id"])
            season_id = int(entry["season_id"])
        except (KeyError, TypeError, ValueError):
            continue
        competition = grouped.setdefault(
            competition_id,
            {
                "id": competition_id,
                "name": str(entry.get("competition_name") or f"competition {competition_id}"),
                "country": str(entry.get("country_name") or ""),
                "seasons": {},
            },
        )
        competition["seasons"].setdefault(
            season_id,
            {"id": season_id, "name": str(entry.get("season_name") or season_id)},
        )

    catalogue: list[dict[str, Any]] = []
    for competition in grouped.values():
        seasons = sorted(
            competition["seasons"].values(), key=lambda item: item["name"], reverse=True
        )
        catalogue.append(
            {
                "id": competition["id"],
                "name": competition["name"],
                "country": competition["country"],
                "seasons": seasons,
            }
        )
    catalogue.sort(key=lambda item: (item["country"], item["name"], item["id"]))
    return tuple(catalogue)


def season_matches(
    competition: int,
    season: int,
    *,
    root: Path | None = None,
    timeout: float = 30.0,
    force: bool = False,
) -> tuple[SeasonMatch, ...]:
    """Every match a season index lists, newest first.

    This is the same file :func:`fetch` reads to place a match; caching it here means a
    reader browsing a season and then opening one of its matches downloads the index once.

    Raises:
        StatsBombError: when the ids are not numeric, the index cannot be downloaded, or
            it is not the list of fixtures the archive publishes.
    """
    competition_id = _clean_ref(competition, "competition")
    season_id = _clean_ref(season, "season")
    directory = root or data_root()
    path = directory / "_seasons" / f"{competition_id}-{season_id}.json"
    if force or not path.is_file():
        payload = _get_json(f"{BASE_URL}/matches/{competition_id}/{season_id}.json", timeout)
        if not isinstance(payload, list) or not payload:
            raise StatsBombError(
                f"the archive has no matches for competition {competition_id}, "
                f"season {season_id}"
            )
        _write_json(path, payload)
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StatsBombError(f"cannot read {path}: {exc}") from None

    rows: list[SeasonMatch] = []
    for entry in payload:
        try:
            meta = MatchMeta.from_index_entry(entry)
            home_score = entry.get("home_score")
            away_score = entry.get("away_score")
            rows.append(
                SeasonMatch(
                    meta=meta,
                    competition_id=competition_id,
                    season_id=season_id,
                    home_score=int(home_score) if home_score is not None else None,
                    away_score=int(away_score) if away_score is not None else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda row: (row.meta.date, row.meta.match_id), reverse=True)
    return tuple(rows)


def ensure(
    match_id: str | int,
    *,
    competition: int,
    season: int,
    root: Path | None = None,
    timeout: float = 30.0,
    force: bool = False,
) -> MatchMeta:
    """Fetch a match if it is not cached, and return its metadata either way.

    This is the archive's one write: a match opened from a season page is downloaded at
    the moment it is asked for, with the competition and season the page was looking at,
    so the default pair is never applied to a match from somewhere else.
    """
    match_id = _clean_id(match_id)
    directory = root or data_root()
    if not force and is_cached(match_id, directory):
        return read_meta(match_id, directory)
    return fetch(
        match_id,
        competition=_clean_ref(competition, "competition"),
        season=_clean_ref(season, "season"),
        root=directory,
        timeout=timeout,
        force=force,
    )


# --- catalogue rows ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeasonMatch:
    """One row of a season index: the fixture, its score, and whether it was played.

    ``MatchMeta`` is the adapter's view of the facts it needs to normalise a match, and a
    score is not one of them — the events carry the goals. A catalogue needs the score
    before anything is downloaded, so it travels beside the meta rather than inside it.
    """

    meta: MatchMeta
    competition_id: int
    season_id: int
    home_score: int | None
    away_score: int | None

    @property
    def played(self) -> bool:
        return self.home_score is not None and self.away_score is not None

    def to_dict(self, *, cached: bool = False) -> dict[str, Any]:
        payload = self.meta.to_dict()
        payload.update(
            {
                "home_score": self.home_score,
                "away_score": self.away_score,
                "played": self.played,
                "score": (
                    f"{self.home_score}–{self.away_score}" if self.played else ""
                ),
                "cached": cached,
                "path": (
                    f"/match?statsbomb={self.meta.match_id}"
                    f"&competition={self.competition_id}"
                    f"&season={self.season_id}"
                ),
            }
        )
        return payload


def _clean_ref(value: str | int, what: str) -> int:
    """A competition or season id, which is a small positive integer.

    Both become path segments in a cache filename and both arrive as query parameters, so
    the same rule applies as for a match id: digits only, before either touches a path.
    """
    text = str(value)
    if not text.isdigit() or len(text) > 9 or int(text) == 0:
        raise StatsBombError(f"{what} {value!r} is not a numeric id")
    return int(text)
