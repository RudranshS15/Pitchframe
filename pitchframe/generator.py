"""Synthetic, football-realistic match generation.

The hackathon rules require synthetic data — no real match footage or licensed
event feeds — so this module *is* the data source. Every match is driven by a
seeded ``random.Random`` instance and is therefore reproducible from its seed.
That matters twice over: the demo video can be re-recorded identically, and the
test suite can assert on exact output.

The model is deliberately simple but coherent. Possession moves around the
pitch; passes and tackles resolve probabilistically based on distance and
pressure; shots happen near goal. It is not a physical simulation, but it
produces an event stream with the right *shape* for the analytics layer above
it — the pass volumes, scoring rates and pressure distributions all sit in
plausible ranges.

All squad and player names are invented. See the trademark note in
:mod:`pitchframe.schema`.
"""

from __future__ import annotations

import itertools
import random

from .schema import (
    PITCH_LENGTH,
    REGULATION_SECONDS,
    Match,
    MatchEvent,
    Player,
    Squad,
    Team,
)

__all__ = ["generate_match", "build_squads"]

# --- tuning constants -------------------------------------------------------
# Each is a probability or range that shapes the simulation. They are gathered
# here so the generator can be retuned without hunting through the loop below.

#: A goal is only likely from inside this distance (normalised pitch units).
SHOOTING_RANGE = 22.0

#: Per-event probability of a shot, inside vs. outside shooting range.
SHOT_CHANCE_CLOSE = 0.14
SHOT_CHANCE_FAR = 0.015

#: Per-event probability of a tackle attempt.
TACKLE_CHANCE = 0.13

#: Fraction of shots that hit the target, and of those, the fraction scored.
ON_TARGET_RATE = 0.42
CONVERSION_RATE = 0.31

#: Seconds between events. A 90-minute match yields roughly 1,000-1,400 events.
EVENT_INTERVAL = (2, 7)

#: Pass completion falls off with distance and rises under pressure.
PASS_BASE_SUCCESS = 0.94
PASS_DISTANCE_PENALTY = 170.0
PASS_PRESSURE_PENALTY = 0.22
PASS_SUCCESS_FLOOR = 0.35

#: Tackles win the ball about half the time.
TACKLE_SUCCESS_RATE = 0.51

_SQUAD_NAMES = {
    "home": "Northgate Athletic",
    "away": "Riverside United",
}

_FORMATION = ("GK", "RB", "CB", "CB", "LB", "DM", "CM", "CM", "RW", "ST", "LW")

_NAME_ONSETS = (
    "Al", "Bren", "Cas", "Dan", "Eri", "Fil", "Gor", "Hal", "Ivo", "Jas",
    "Kat", "Lor", "Mir", "Nik", "Osk", "Pav", "Ras", "Sil", "Tom", "Ver",
)
_NAME_CODAS = (
    "aro", "beck", "dahl", "ek", "ford", "gard", "holm", "ire", "kovac",
    "lund", "mire", "nov", "quist", "rado", "skar", "tova", "vik", "well",
)


def _player_name(rng: random.Random, used: set[str]) -> str:
    """Invent a unique player name, avoiding real-world names entirely."""
    for _ in range(500):
        name = f"{rng.choice(_NAME_ONSETS)}{rng.choice(_NAME_CODAS)}"
        if name not in used:
            return name
    raise RuntimeError("exhausted the name space; widen _NAME_ONSETS/_NAME_CODAS")


def _build_squad(rng: random.Random, team: Team) -> Squad:
    used: set[str] = set()
    players: list[Player] = []
    for index, position in enumerate(_FORMATION, start=1):
        name = _player_name(rng, used)
        used.add(name)
        players.append(
            Player(
                id=f"{team[0].upper()}{index:02d}",
                name=name,
                team=team,
                position=position,
                shirt=index,
            )
        )
    return Squad(team=team, name=_SQUAD_NAMES[team], players=tuple(players))


def build_squads(rng: random.Random) -> tuple[Squad, Squad]:
    """Build both rosters from a shared RNG, so squads are seed-dependent."""
    return _build_squad(rng, "home"), _build_squad(rng, "away")


def _pressure(rng: random.Random, distance_to_goal: float) -> float:
    """Sample a pressures value in ``0-1``.

    Pressure rises as a side gets closer to the goal it is attacking, which is
    what makes the pressure index a meaningful signal for the narrative layer
    rather than uniform noise.
    """
    closeness = 1.0 - min(distance_to_goal, PITCH_LENGTH) / PITCH_LENGTH
    base = rng.gauss(0.34, 0.14)
    return max(0.02, min(0.98, base + closeness * 0.28))


def generate_match(seed: int, match_id: str = "PF-001") -> Match:
    """Generate one complete synthetic match.

    The same ``seed`` always yields an identical :class:`Match`, including event
    ids, so callers can rely on byte-stable output for demos and tests.

    Args:
        seed: Drives every random decision in the simulation.
        match_id: Prefix for generated event ids.

    Returns:
        A :class:`Match` whose ``events`` are ordered by clock.
    """
    rng = random.Random(seed)
    home, away = build_squads(rng)
    squads: dict[Team, Squad] = {"home": home, "away": away}

    events: list[MatchEvent] = []
    counter = itertools.count(1)

    possession: Team = "home"
    ball: tuple[float, float] = (PITCH_LENGTH / 2, PITCH_LENGTH / 2)
    clock = 0
    second_half_started = False

    def emit(
        kind: str,
        team: Team,
        player: Player,
        *,
        from_xy: tuple[float, float] | None = None,
        to_xy: tuple[float, float] | None = None,
        outcome: str | None = None,
        speed_kmh: float | None = None,
        pressure: float | None = None,
        detail: str = "",
    ) -> MatchEvent:
        event = MatchEvent(
            id=f"{match_id}-E{next(counter):05d}",
            match_id=match_id,
            clock=clock,
            type=kind,  # type: ignore[arg-type]
            team=team,
            player=player,
            from_xy=from_xy,
            to_xy=to_xy,
            outcome=outcome,  # type: ignore[arg-type]
            speed_kmh=speed_kmh,
            pressure=pressure,
            detail=detail,
        )
        events.append(event)
        return event

    def pick(team: Team) -> Player:
        return rng.choice(squads[team].players)

    def other(team: Team) -> Team:
        return "away" if team == "home" else "home"

    emit("kickoff", possession, pick(possession), detail="Kick-off")

    while clock < REGULATION_SECONDS:
        clock += rng.randint(*EVENT_INTERVAL)

        # Half time fires exactly once and restarts play for the second half.
        if clock >= REGULATION_SECONDS // 2 and not second_half_started:
            second_half_started = True
            possession = other(possession)
            ball = (PITCH_LENGTH / 2, PITCH_LENGTH / 2)
            emit("half_time", possession, pick(possession), detail="Half time")
            continue

        goal_x = PITCH_LENGTH if possession == "home" else 0.0
        distance_to_goal = abs(goal_x - ball[0])
        pressure = _pressure(rng, distance_to_goal)

        roll = rng.random()
        shot_p = SHOT_CHANCE_CLOSE if distance_to_goal < SHOOTING_RANGE else SHOT_CHANCE_FAR

        if roll < shot_p:
            shooter = pick(possession)
            on_target = rng.random() < ON_TARGET_RATE
            scored = on_target and rng.random() < CONVERSION_RATE
            emit(
                "shot",
                possession,
                shooter,
                from_xy=ball,
                to_xy=(goal_x, rng.uniform(36.0, 64.0)),
                outcome="success" if on_target else "fail",
                speed_kmh=round(rng.uniform(62.0, 122.0), 1),
                pressure=round(pressure, 3),
            )
            if scored:
                emit(
                    "goal",
                    possession,
                    shooter,
                    from_xy=ball,
                    to_xy=(goal_x, rng.uniform(40.0, 60.0)),
                    outcome="success",
                    speed_kmh=round(rng.uniform(62.0, 122.0), 1),
                    pressure=round(pressure, 3),
                    detail=f"{squads[possession].name} score",
                )
                possession = other(possession)
                ball = (PITCH_LENGTH / 2, PITCH_LENGTH / 2)

        elif roll < shot_p + TACKLE_CHANCE:
            defender = pick(other(possession))
            won = rng.random() < TACKLE_SUCCESS_RATE
            emit(
                "tackle",
                other(possession),
                defender,
                from_xy=ball,
                outcome="success" if won else "fail",
                pressure=round(pressure, 3),
            )
            if won:
                possession = other(possession)
                ball = (
                    max(2.0, min(PITCH_LENGTH - 2.0, ball[0] + rng.uniform(-12, 12))),
                    max(2.0, min(PITCH_LENGTH - 2.0, ball[1] + rng.uniform(-12, 12))),
                )
                emit(
                    "possession_change",
                    possession,
                    pick(possession),
                    from_xy=ball,
                    outcome="success",
                    detail="Turnover",
                )

        else:
            passer = pick(possession)
            target_xy = (
                max(2.0, min(PITCH_LENGTH - 2.0, ball[0] + rng.uniform(-18, 18))),
                max(2.0, min(PITCH_LENGTH - 2.0, ball[1] + rng.uniform(-20, 20))),
            )
            distance = ((target_xy[0] - ball[0]) ** 2 + (target_xy[1] - ball[1]) ** 2) ** 0.5
            success_chance = max(
                PASS_SUCCESS_FLOOR,
                PASS_BASE_SUCCESS - distance / PASS_DISTANCE_PENALTY - pressure * PASS_PRESSURE_PENALTY,
            )
            completed = rng.random() < success_chance
            emit(
                "pass",
                possession,
                passer,
                from_xy=ball,
                to_xy=target_xy,
                outcome="success" if completed else "fail",
                # Ball speed, not player speed. A pass is struck firmly enough to
                # reach its target, so it starts around 20 km/h and gains roughly a
                # km/h per metre. Scaling linearly from zero — the obvious first
                # model — put an ordinary 40 m pass above 100 km/h, which is a shot.
                speed_kmh=round(
                    min(92.0, rng.uniform(18.0, 30.0) + distance * rng.uniform(0.8, 1.4)),
                    1,
                ),
                pressure=round(pressure, 3),
            )
            if completed:
                ball = target_xy
            else:
                possession = other(possession)
                emit(
                    "possession_change",
                    possession,
                    pick(possession),
                    from_xy=target_xy,
                    outcome="success",
                    detail="Intercepted",
                )

    emit("full_time", "home", squads["home"].players[0], detail="Full time")

    return Match(id=match_id, home=home, away=away, events=tuple(events))
