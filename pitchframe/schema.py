"""Core domain types for Pitchframe.

Everything in the system speaks one vocabulary: :class:`MatchEvent`. The
generator produces them, the stats engine reads them, the agents reason over
them, and the overlay renders them. One shared type is what lets four agents
coordinate without a translation layer between them.

Pitch coordinates are normalised to ``0-100`` on both axes. ``(0, 0)`` is the
home team's own goal line and ``(100, 100)`` is the away team's, so the home
team always attacks towards increasing ``x``.

The generator invents its squads; the StatsBomb adapter imports real ones. Both
produce the same types, so the analytics, the claim layer and the overlay cannot
tell a synthetic match from a recorded one — and a test can run against either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "Team",
    "EventType",
    "Outcome",
    "Player",
    "Squad",
    "MatchEvent",
    "Match",
    "REGULATION_SECONDS",
    "PITCH_LENGTH",
    "PITCH_WIDTH",
    "PITCH_LENGTH_METRES",
    "PITCH_WIDTH_METRES",
]

Team = Literal["home", "away"]

EventType = Literal[
    "kickoff",
    "pass",
    # A player keeping the ball and advancing with it, rather than releasing it. A
    # first-class event so the build-up panel can draw a carry as a recorded route;
    # `highlights` can still infer one from a gap, for streams that lack the event.
    "carry",
    "shot",
    "tackle",
    "possession_change",
    "pressure",
    "goal",
    "half_time",
    "full_time",
]

Outcome = Literal["success", "fail"]

#: Seconds in regulation time (90 minutes).
REGULATION_SECONDS = 90 * 60

#: Normalised pitch extent; coordinates run 0-100 on both axes.
PITCH_LENGTH = 100.0
PITCH_WIDTH = 100.0

#: The pitch the normalised grid stands for, in metres.
#:
#: The two axes are normalised onto the same 0-100 range but they do not represent the
#: same distance: one unit along the length is 1.05 m and one across the width is
#: 0.68 m. A distance measured in raw grid units therefore overstates any sideways
#: component by about half again — which is what put a player level with the touchline
#: twenty "units" from goal when they were twelve metres from it, and made the drawn
#: finish on the build-up panel arrive somewhere the shot could not have gone. Anything
#: that compares distances converts first; ``generator.goal_distance`` is the one that
#: matters, because it decides where a side is close enough to shoot.
PITCH_LENGTH_METRES = 105.0
PITCH_WIDTH_METRES = 68.0


@dataclass(frozen=True, slots=True)
class Player:
    """A single player. ``id`` is stable for the lifetime of a match."""

    id: str
    name: str
    team: Team
    position: str
    shirt: int


@dataclass(frozen=True, slots=True)
class Squad:
    """One team's roster: everyone who took part, and the bench behind them.

    ``players`` is the starting eleven plus any substitute who came on — in a generated
    match that is exactly eleven, and in an imported one it is however many the manager
    used. ``bench`` is separate on purpose: a substitute who never came on has no events
    to rate, and folding them into ``players`` would invent a mark for a player the match
    never used. The bench is on the team sheet because a roster without its substitutes
    is half a team sheet.
    """

    team: Team
    name: str
    players: tuple[Player, ...]
    bench: tuple[Player, ...] = ()

    def by_id(self, player_id: str) -> Player | None:
        """Look a player up by id, or ``None`` if they are not in this squad."""
        for player in self.players:
            if player.id == player_id:
                return player
        return None

    def by_position(self, position: str) -> tuple[Player, ...]:
        """All players in a given position, useful for de-duplicating logic."""
        return tuple(p for p in self.players if p.position == position)


@dataclass(frozen=True, slots=True)
class MatchEvent:
    """One thing that happened, at one moment, to one player.

    Only the fields relevant to ``type`` are populated; the rest stay ``None``.
    ``clock`` is seconds since kick-off, which keeps ordering unambiguous and
    makes windowed statistics trivial to slice.

    ``possession_id`` groups events into a single uninterrupted spell of the ball
    for one side. It is additive, defaulted, and nothing that predates it depends on
    it: every event built without one reads ``0``, which is a single anonymous
    possession. It exists because a move is a *sequence*, and without a group
    boundary "a nine-pass move" cannot be counted at all — the count would run
    straight through a turnover. The generator assigns it; see
    :mod:`pitchframe.highlights` for what it is used for.
    """

    id: str
    match_id: str
    clock: int
    type: EventType
    team: Team
    player: Player
    from_xy: tuple[float, float] | None = None
    to_xy: tuple[float, float] | None = None
    outcome: Outcome | None = None
    speed_kmh: float | None = None
    pressure: float | None = None
    detail: str = ""
    #: Which uninterrupted spell of possession this event belongs to. Events that
    #: predate this field, or fixtures that do not care, all share ``0``.
    possession_id: int = 0

    @property
    def minute(self) -> int:
        """Whole minutes elapsed, for display."""
        return self.clock // 60

    @property
    def is_successful(self) -> bool:
        return self.outcome == "success"

    def describe(self) -> str:
        """A short human-readable label, used by the overlay and CLI."""
        return f"{self.minute:02d}' {self.player.name} — {self.type} ({self.outcome})"


@dataclass(frozen=True, slots=True)
class Match:
    """A complete synthetic match: two squads and an ordered event list."""

    id: str
    home: Squad
    away: Squad
    events: tuple[MatchEvent, ...]

    def squad(self, team: Team) -> Squad:
        return self.home if team == "home" else self.away

    @property
    def score(self) -> tuple[int, int]:
        """``(home_goals, away_goals)``, derived from the event stream."""
        goals = [e for e in self.events if e.type == "goal"]
        return (
            sum(1 for e in goals if e.team == "home"),
            sum(1 for e in goals if e.team == "away"),
        )
