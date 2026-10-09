"""Core domain types for Pitchframe.

Everything in the system speaks one vocabulary: :class:`MatchEvent`. The
generator produces them, the stats engine reads them, the agents reason over
them, and the overlay renders them. One shared type is what lets four agents
coordinate without a translation layer between them.

Pitch coordinates are normalised to ``0-100`` on both axes. ``(0, 0)`` is the
home team's own goal line and ``(100, 100)`` is the away team's, so the home
team always attacks towards increasing ``x``.

All squad and player names in this project are invented. The hackathon rules
forbid third-party trademarks and unlicensed material, so we deliberately
never reference real clubs, players, or competitions.
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
]

Team = Literal["home", "away"]

EventType = Literal[
    "kickoff",
    "pass",
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
    """One team's roster."""

    team: Team
    name: str
    players: tuple[Player, ...]

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
