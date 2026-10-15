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
import math
import random

from .schema import (
    PITCH_LENGTH,
    PITCH_LENGTH_METRES,
    PITCH_WIDTH,
    PITCH_WIDTH_METRES,
    REGULATION_SECONDS,
    Match,
    MatchEvent,
    Player,
    Squad,
    Team,
)

__all__ = ["generate_match", "build_squads", "goal_distance", "GOAL_HALF_WIDTH"]

# --- tuning constants -------------------------------------------------------
# Each is a probability or range that shapes the simulation. They are gathered
# here so the generator can be retuned without hunting through the loop below.

#: A goal is only likely from inside this distance, in metres — 21 m is the penalty
#: area plus a stride, so a shot from here is a good idea rather than a hopeful one.
SHOOTING_RANGE = 21.0

#: The outer limit of a speculative effort, in metres. Past this a side keeps the ball
#: instead of shooting. Without a ceiling the long shot fired from anywhere on the
#: pitch, producing "shots" from a team's own corner — absurd in the event stream, and
#: absurd on the build-up panel where the shot is the drawn finish.
LONG_SHOT_RANGE = 31.0

#: Per-event probability of a shot, inside shooting range and between the two limits.
#:
#: Both were retuned when the distance to goal began being measured in metres rather
#: than in grid units. Those units overstate sideways distance, which had confined
#: shooting to a narrow band in front of goal; measuring properly widened the band
#: across most of the final third, and the same probabilities then produced nearly
#: fifty shots and six goals a match. Football produces about twenty-eight and three,
#: so the probabilities came down to keep the shape.
#:
#: The close-range figure is still high relative to how rarely a side gets there: a
#: possession is short, about a fifth of events end it, so a side that reaches the box
#: gets only a couple of touches to shoot with.
SHOT_CHANCE_CLOSE = 0.17
SHOT_CHANCE_FAR = 0.003

#: The possession id for an event that belongs to no spell at all. Kick-off,
#: half-time and full time are match-level markers rather than things a side did with
#: the ball, so attributing them to whichever possession happened to be open gave the
#: last spell of the match two teams in it.
NO_POSSESSION = 0

#: How far a pass advances the ball along the attacking axis, and how far it may
#: drift sideways. The advance is signed by the attacking direction, so the ball
#: progresses towards the goal a side is attacking. It used to be an unbiased step in
#: both axes, which made every possession a random walk: a team would pass around one
#: corner for ten touches without ever getting closer to goal, and the reconstructed
#: build-up read as a scribble rather than a move. A minority of passes still go
#: backwards or square, which is what real passing looks like.
PASS_ADVANCE_RANGE = (-6.0, 24.0)
PASS_LATERAL = 15.0

#: Per-event probability that the player keeps the ball instead of releasing it. The
#: ball is carried forward and no team-mate touches it, so it appears in the stream as
#: a first-class `carry` event rather than being inferred after the fact.
CARRY_CHANCE = 0.12

#: A carry advances less than a pass and strays less sideways, because the player is
#: running with the ball rather than striking it into space.
CARRY_ADVANCE_RANGE = (1.0, 15.0)
CARRY_LATERAL = 6.0

#: Per-event probability of a tackle attempt.
TACKLE_CHANCE = 0.13

#: Half the goal mouth, in normalised units. A regulation goal is 7.32 m wide on a
#: 68 m pitch — a little under 11% of the width, so about 5.4 units either side of the
#: centre line on the 0-100 grid. This is what makes "on target" mean something on the
#: build-up panel: an on-target strike has to finish between the posts, and one that
#: misses has to finish outside them.
GOAL_HALF_WIDTH = 5.4

#: Fraction of shots that hit the target, and of those, the fraction scored. One in
#: three on target is the real figure, and with the conversion rate beneath it that is
#: about one goal in ten shots — also where football sits. Both were left alone through
#: the retune, because the shot *rate* was the thing that had drifted.
ON_TARGET_RATE = 0.33
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


def _to_metres(xy: tuple[float, float]) -> tuple[float, float]:
    """A grid coordinate, as the metres on the pitch it stands for.

    Each axis divides by its *normalised* extent and multiplies by its length in
    metres, because the two are not the same number: 1.05 m per unit along the length
    and 0.68 m across the width.
    """
    return (
        xy[0] * PITCH_LENGTH_METRES / PITCH_LENGTH,
        xy[1] * PITCH_WIDTH_METRES / PITCH_WIDTH,
    )


def goal_distance(xy: tuple[float, float], team: Team) -> float:
    """How far ``xy`` is from the middle of the goal ``team`` attacks, in metres.

    Converted before it is compared, because the grid's two axes are normalised onto
    the same 0-100 range while standing for very different lengths: a step sideways is
    0.68 m and a step forward is 1.05 m, so a raw grid distance overstates anything
    with a large sideways component by about half again. That error was load-bearing:
    it is what let a player near the touchline count as close to goal when they were
    twelve metres out, and it fed :func:`_pressure` as well.

    Public because the tests and the build-up panel both need the number the shooting
    decision was actually made with. A limit asserted in one scale and enforced in
    another is not a limit — the grid-unit version of that check passed while the
    generator produced shots it would have refused.
    """
    goal_x = PITCH_LENGTH if team == "home" else 0.0
    gx, gy = _to_metres((goal_x, PITCH_LENGTH / 2))
    x, y = _to_metres(xy)
    return math.hypot(x - gx, y - gy)


def _pressure(rng: random.Random, distance_to_goal: float) -> float:
    """Sample a pressures value in ``0-1``.

    ``distance_to_goal`` is in metres, so the denominator is the pitch's length in
    metres rather than its normalised extent — the two differ by a factor of 1.05, and
    using the wrong one would shift every pressure in the match.

    Pressure rises as a side gets closer to the goal it is attacking, which is
    what makes the pressure index a meaningful signal for the narrative layer
    rather than uniform noise.
    """
    closeness = 1.0 - min(distance_to_goal, PITCH_LENGTH_METRES) / PITCH_LENGTH_METRES
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

    #: Identifies the current uninterrupted spell of possession. Every event emitted
    #: while it holds belongs to the same move, which is what lets a later reader
    #: count the passes that led to a goal. It advances on each of the four ways a
    #: spell can end: kick-off, half-time, a goal, and a turnover. Bumping *before* a
    #: turnover's ``possession_change`` is emitted — rather than after — puts that
    #: marker at the start of the new spell, so walking a possession backwards never
    #: walks through the event that took the ball away.
    possession_seq = 1

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
        possession_id: int | None = None,
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
            possession_id=(
                possession_seq if possession_id is None else possession_id
            ),
        )
        events.append(event)
        return event

    def pick(team: Team) -> Player:
        return rng.choice(squads[team].players)

    def other(team: Team) -> Team:
        return "away" if team == "home" else "home"

    emit(
        "kickoff",
        possession,
        pick(possession),
        detail="Kick-off",
        possession_id=NO_POSSESSION,
    )

    while clock < REGULATION_SECONDS:
        clock += rng.randint(*EVENT_INTERVAL)

        # Half time fires exactly once and restarts play for the second half.
        if clock >= REGULATION_SECONDS // 2 and not second_half_started:
            second_half_started = True
            possession = other(possession)
            ball = (PITCH_LENGTH / 2, PITCH_LENGTH / 2)
            possession_seq += 1
            emit(
                "half_time",
                possession,
                pick(possession),
                detail="Half time",
                possession_id=NO_POSSESSION,
            )
            continue

        goal_x = PITCH_LENGTH if possession == "home" else 0.0
        # Distance to the middle of the goal, measured in metres rather than along the
        # grid's own axes — see `goal_distance`. Measuring only the gap along x called
        # a player standing on the touchline nineteen units out a close-range shooter
        # and let them strike a fifty-six-unit ball in from the corner flag; the
        # current metric still overstates sideways distance, which is why it converts.
        distance_to_goal = goal_distance(ball, possession)
        pressure = _pressure(rng, distance_to_goal)

        roll = rng.random()
        if distance_to_goal < SHOOTING_RANGE:
            shot_p = SHOT_CHANCE_CLOSE
        elif distance_to_goal < LONG_SHOT_RANGE:
            shot_p = SHOT_CHANCE_FAR
        else:
            shot_p = 0.0

        if roll < shot_p:
            shooter = pick(possession)
            on_target = rng.random() < ON_TARGET_RATE
            scored = on_target and rng.random() < CONVERSION_RATE
            # One target, drawn once and shared by the shot and the goal it produces,
            # so the finishing strike and the goal end at the same point. They used to
            # be two independent draws from overlapping bands — the shot anywhere in
            # 36-64 and the goal anywhere in 40-60 — which let a shot recorded as "on
            # target" finish several units wide of the post while the goal's own
            # marker sat somewhere else again. The drawn finish and the goal it
            # produced disagreed, on the one panel whose whole job is to show the
            # finish, and the ball appeared to leave one place and arrive at another.
            if on_target:
                offset = rng.uniform(0.0, GOAL_HALF_WIDTH)
            else:
                # Wide of the post, but by a plausible margin rather than anywhere on
                # the pitch: between a third of a goal and a goal's width outside it.
                offset = rng.uniform(GOAL_HALF_WIDTH * 1.4, GOAL_HALF_WIDTH * 3.2)
            side = -1.0 if rng.random() < 0.5 else 1.0
            target_y = max(2.0, min(PITCH_LENGTH - 2.0, PITCH_LENGTH / 2 + side * offset))
            shot_target = (goal_x, target_y)
            # One speed for the strike: a goal reported as arriving faster or slower
            # than the shot that scored it would be two accounts of the same kick.
            shot_speed = round(rng.uniform(62.0, 122.0), 1)
            emit(
                "shot",
                possession,
                shooter,
                from_xy=ball,
                to_xy=shot_target,
                outcome="success" if on_target else "fail",
                speed_kmh=shot_speed,
                pressure=round(pressure, 3),
            )
            if scored:
                emit(
                    "goal",
                    possession,
                    shooter,
                    from_xy=ball,
                    to_xy=shot_target,
                    outcome="success",
                    speed_kmh=shot_speed,
                    pressure=round(pressure, 3),
                    detail=f"{squads[possession].name} score",
                )
                # The conceding side kicks off, so the goal ends the spell rather
                # than being part of the next one.
                possession = other(possession)
                ball = (PITCH_LENGTH / 2, PITCH_LENGTH / 2)
                possession_seq += 1
            else:
                # A shot that does not go in — saved, blocked or off target — ends the
                # spell, and the defending side restarts from just in front of the goal
                # they were defending.
                #
                # This matters more than it looks. The ball used to be left where the
                # shot was *struck from*, so the next pass began at the shot's own
                # starting point and the ball appeared to travel the length of the shot
                # backwards. A move reconstructed from that stream jumps tens of units
                # away from the goal between two consecutive touches, which is not
                # something a ball does. Giving the ball to the other side is both the
                # realistic outcome and the one that keeps consecutive events in a
                # possession sharing a position.
                keeper = squads[other(possession)].by_position("GK")
                restart_x = goal_x + (-6.0 if goal_x > PITCH_LENGTH / 2 else 6.0)
                possession = other(possession)
                possession_seq += 1
                ball = (restart_x, rng.uniform(30.0, 70.0))
                emit(
                    "possession_change",
                    possession,
                    keeper[0] if keeper else pick(possession),
                    from_xy=ball,
                    outcome="success",
                    detail="Saved" if on_target else "Goal kick",
                )

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
                possession_seq += 1
                emit(
                    "possession_change",
                    possession,
                    pick(possession),
                    from_xy=ball,
                    outcome="success",
                    detail="Turnover",
                )

        elif roll < shot_p + TACKLE_CHANCE + CARRY_CHANCE:
            # The player keeps the ball and runs with it. Emitting this rather than
            # leaving it to be inferred matters: a carry is by definition ball
            # movement with no event explaining it, so on a stream where every event
            # starts exactly where the last one ended — which this generator now
            # produces — there is nothing left to infer it from.
            carrier = pick(possession)
            carry_from = ball
            carry_advance = rng.uniform(*CARRY_ADVANCE_RANGE)
            ball = (
                max(
                    2.0,
                    min(
                        PITCH_LENGTH - 2.0,
                        ball[0] + (carry_advance if possession == "home" else -carry_advance),
                    ),
                ),
                max(2.0, min(PITCH_LENGTH - 2.0, ball[1] + rng.uniform(-CARRY_LATERAL, CARRY_LATERAL))),
            )
            emit(
                "carry",
                possession,
                carrier,
                from_xy=carry_from,
                to_xy=ball,
                outcome="success",
                # The player's speed rather than the ball's: nobody struck it, so the
                # ball is only going as fast as the person running with it.
                speed_kmh=round(rng.uniform(14.0, 26.0), 1),
                pressure=round(pressure, 3),
            )

        else:
            passer = pick(possession)
            # A pass goes mostly forward: the step is signed by the attacking
            # direction, so the ball moves towards the goal being attacked.
            advance = rng.uniform(*PASS_ADVANCE_RANGE)
            target_xy = (
                max(
                    2.0,
                    min(
                        PITCH_LENGTH - 2.0,
                        ball[0] + (advance if possession == "home" else -advance),
                    ),
                ),
                max(2.0, min(PITCH_LENGTH - 2.0, ball[1] + rng.uniform(-PASS_LATERAL, PASS_LATERAL))),
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
            # The ball is at the target either way: a completed pass arrives there,
            # and an interception happens there. Leaving it behind on a failed pass
            # made the turnover marker disagree with the very next event.
            ball = target_xy
            if not completed:
                possession = other(possession)
                possession_seq += 1
                emit(
                    "possession_change",
                    possession,
                    pick(possession),
                    from_xy=target_xy,
                    outcome="success",
                    detail="Intercepted",
                )

    emit(
        "full_time",
        "home",
        squads["home"].players[0],
        detail="Full time",
        possession_id=NO_POSSESSION,
    )

    return Match(id=match_id, home=home, away=away, events=tuple(events))
