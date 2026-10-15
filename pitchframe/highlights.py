"""Turning a goal into the path that produced it.

A goal conceded from a corner and a goal conceded after eight passes are different
events, and a scoreboard shows them identically. This module reconstructs the second
one: given the goal, it walks back through the possession that produced it and returns
the ordered touches that led to it, so the overlay can draw the move rather than only
announce the finish.

Every function here is pure — no I/O, no clock, no RNG of its own. The only input that
varies is the seed, and it arrives already baked into the event stream, so the same
seed always produces the same highlight bytes. That is what lets the overlay's payload
be asserted on in a test rather than eyeballed.

**What is real here and what is inferred.** Passes, carries and the finish are recorded
events, copied straight out of the stream. A carry can *additionally* be inferred:
where the same player takes two touches and the ball moved between them by more than
:data:`CARRY_THRESHOLD`, a carry step is inserted and flagged ``derived=True`` all the
way to the payload, and the overlay labels the whole highlight *derived carries*.

That inference exists for streams without ``carry`` events — an imported feed, or a
hand-built fixture. Note that it cannot fire on a coherent stream at all: on a stream
where every event starts exactly where the previous one ended there is no unexplained
ball movement, and unexplained ball movement is precisely what a carry is. Ours is
such a stream, so generated matches always reach the panel through recorded carries.
A derived step must never be indistinguishable from a recorded one.

**Coordinates** are the schema's normalised ``0-100`` grid, with the home side attacking
towards increasing ``x``. Thirds are therefore read in the *attacking* direction — a
move that starts on a team's own goal line is in its defensive third regardless of
which end of the array that happens to be.

The possession-chain idea is prior art, not ours. StatsBomb's open event model carries
a ``possession`` index on every event and Wyscout's release is organised around
possession chains; the approach of walking a chain backwards to reconstruct a move
comes from reading those schemas (see ``docs/prior-art.md``). No third-party code,
data or asset is used — this is written against our own schema, and the labels say so.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .generator import generate_match
from .schema import PITCH_LENGTH, MatchEvent, Team

__all__ = [
    "CARRY_THRESHOLD",
    "Zone",
    "StepKind",
    "BuildUpStep",
    "MoveSummary",
    "GoalHighlight",
    "zone_for",
    "move_window",
    "extract_build_up",
    "summarise_move",
    "highlight_for_goal",
    "build_highlights",
    "highlight_payload",
    "DEFAULT_MATCH_ID",
    "LABEL_SYNTHETIC",
    "LABEL_DERIVED_CARRIES",
]

#: Match id used when a caller does not supply one, matching the generator's default.
DEFAULT_MATCH_ID = "PF-001"

#: How far the ball must travel between two touches by the *same* player before the
#: interval is described as a carry. In normalised units, so ``3.0`` is about three
#: metres of a 105 m pitch — deliberately low, because the point is to catch real ball
#: movement without inventing a carry out of the noise in a pass's target.
CARRY_THRESHOLD = 3.0

#: One third of the pitch along its length.
_THIRD = PITCH_LENGTH / 3.0

#: Recorded event types that become steps. A ``goal`` is deliberately absent: the goal
#: *terminates* a move, it is not a touch within one. It is the marker the whole walk
#: is anchored to, and counting it as a step would report every goal twice — once as
#: the shot and once as itself. A finish is the shot that preceded it, and a move that
#: has no shot event simply has no finish step, which is the honest reading.
#:
#: ``carry`` is here because the generator emits it. :func:`_carry_between` remains as a
#: fallback for a stream that lacks the event — an imported feed, or a fixture — and a
#: step produced that way is flagged ``derived`` so the two are never confused.
_TOUCH_KINDS = {"pass": "pass", "carry": "carry", "shot": "shot"}

LABEL_SYNTHETIC = "synthetic match"
LABEL_DERIVED_CARRIES = "derived carries"

StepKind = Literal["pass", "carry", "shot"]
Zone = Literal["defensive", "middle", "final", "unknown"]


@dataclass(frozen=True, slots=True)
class BuildUpStep:
    """One touch in a move, in the order it happened.

    ``derived`` is the field that matters. A step taken from an event the generator
    actually emitted is a record; a carry is an inference drawn between two such
    records, and the flag travels with it so nothing downstream can present the two
    as the same kind of fact.
    """

    index: int
    kind: StepKind
    team: Team
    player: str
    #: Seconds since kick-off. For a derived carry this is the midpoint of the
    #: interval it occupies, since the carry itself was never timestamped.
    clock: int
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]
    derived: bool = False

    @property
    def minute(self) -> int:
        """Whole minute the step happened in, for display."""
        return self.clock // 60

    @property
    def distance(self) -> float:
        """How far the ball travelled on this step, in normalised units."""
        return round(math.dist(self.start_xy, self.end_xy), 2)

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape the overlay consumes."""
        return {
            "index": self.index,
            "kind": self.kind,
            "team": self.team,
            "player": self.player,
            "clock": self.clock,
            "minute": self.minute,
            "start_xy": [self.start_xy[0], self.start_xy[1]],
            "end_xy": [self.end_xy[0], self.end_xy[1]],
            "distance": self.distance,
            "derived": self.derived,
        }


@dataclass(frozen=True, slots=True)
class MoveSummary:
    """The move reduced to the handful of numbers worth showing beside it.

    ``steps`` counts every step including derived carries, while ``passes`` and
    ``carries`` break it down, so a viewer can see how much of the path is recorded
    and how much is inferred without doing arithmetic.
    """

    passes: int
    carries: int
    shots: int
    steps: int
    #: Total ball travel along the move, in normalised pitch units.
    distance: float
    #: Seconds from the first step to the goal.
    duration: int
    #: Which third of the pitch the move began in, read in the attacking direction.
    start_zone: Zone
    start_xy: tuple[float, float] | None

    @property
    def is_empty(self) -> bool:
        """True when nothing preceded the goal — an unassisted finish."""
        return self.steps == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passes": self.passes,
            "carries": self.carries,
            "shots": self.shots,
            "steps": self.steps,
            "distance": self.distance,
            "duration": self.duration,
            "start_zone": self.start_zone,
            "start_xy": None if self.start_xy is None else [self.start_xy[0], self.start_xy[1]],
        }


@dataclass(frozen=True, slots=True)
class GoalHighlight:
    """Everything the overlay needs to draw one goal's build-up."""

    goal_id: str
    team: Team
    scorer: str
    minute: int
    clock: int
    seed: int
    steps: tuple[BuildUpStep, ...]
    summary: MoveSummary
    #: Where the ball finished — the goal itself, for the panel to mark. Separate from
    #: the last step because an unassisted finish has no steps but still has a place
    #: the ball ended up.
    finish_xy: tuple[float, float] | None
    #: Provenance labels. The overlay renders these verbatim.
    labels: tuple[str, ...]

    @property
    def has_derived_carries(self) -> bool:
        """True when any step in this move was inferred rather than recorded."""
        return any(step.derived for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        """The JSON object for this goal, as the page receives it."""
        return {
            "goal_id": self.goal_id,
            "team": self.team,
            "scorer": self.scorer,
            "minute": self.minute,
            "clock": self.clock,
            "seed": self.seed,
            "steps": [step.to_dict() for step in self.steps],
            "summary": self.summary.to_dict(),
            "finish_xy": None if self.finish_xy is None else [self.finish_xy[0], self.finish_xy[1]],
            "labels": list(self.labels),
        }


# --- locating the move ------------------------------------------------------


def zone_for(x: float, team: Team) -> Zone:
    """Which third of the pitch ``x`` falls in, from ``team``'s point of view.

    ``x`` is the schema's absolute coordinate. A side's own defensive third is the
    end it defends, so the answer depends on which way the team is attacking — the
    home side attacks increasing ``x`` and the away side decreasing, so the away
    side's defensive third is the *high* end of the array. Reading zones off the raw
    coordinate would call an away move that began on its own goal line a "final
    third" move, which is the opposite of what it is.
    """
    progress = x if team == "home" else PITCH_LENGTH - x
    if progress < _THIRD:
        return "defensive"
    if progress < 2 * _THIRD:
        return "middle"
    return "final"


def _has_path(event: MatchEvent) -> bool:
    """Whether the event records both ends of a ball path."""
    return event.from_xy is not None and event.to_xy is not None


def move_window(events: Sequence[MatchEvent], goal: MatchEvent) -> tuple[MatchEvent, ...]:
    """The events of the possession that produced ``goal``, ending with the goal.

    The walk stops on two conditions, and both are needed. It stops when the
    possession id changes — that is the whole point of the field. It *also* stops when
    the team changes, because a possession's final event is the tackle that ended it,
    which is recorded against the side that won the ball while still carrying the old
    possession id. Without the team check the walk would step over that tackle and
    credit the scoring side with the previous side's passes.

    A ``kickoff``, ``half_time`` or ``full_time`` is a hard boundary even if the
    possession id somehow matches, since no move spans one.
    """
    goal_index = None
    for index, event in enumerate(events):
        if event is goal or (event.id == goal.id and event.clock == goal.clock):
            goal_index = index
            break
    if goal_index is None:
        return ()

    window: list[MatchEvent] = []
    for index in range(goal_index, -1, -1):
        event = events[index]
        if index != goal_index:
            if event.type in ("kickoff", "half_time", "full_time"):
                break
            if event.possession_id != goal.possession_id or event.team != goal.team:
                break
        window.append(event)
    window.reverse()
    return tuple(window)


def _touches(
    events: Sequence[MatchEvent], goal: MatchEvent, *, include_finish: bool
) -> tuple[MatchEvent, ...]:
    """The recorded touches of the move, in clock order.

    With ``include_finish`` the move ends on the finish — the shot, when one was
    recorded. Without it the finish is dropped and only the build-up is returned.

    The goal event itself is never a touch. :func:`move_window` returns it as the last
    element, so this drops that element and reads only what came before it. That is
    what makes an unassisted finish come out as an empty move rather than as a
    one-step move consisting of the goal, and it is also what stops the shot and the
    goal being counted as two separate touches of the same strike.
    """
    window = move_window(events, goal)
    # Filtered through the registry rather than a second hardcoded list. When these
    # two drifted apart, carries were dropped from the move — which did not merely
    # lose a step: it made the touches either side of the dropped carry adjacent, so
    # two touches by one player with a gap between them looked like an unrecorded
    # carry and the derivation invented one that had in fact been recorded.
    touches = [
        event
        for event in window[:-1]
        if event.type in _TOUCH_KINDS and _has_path(event)
    ]

    if not include_finish:
        return tuple(event for event in touches if event.type != "shot")
    return tuple(touches)


def _carry_between(earlier: MatchEvent, later: MatchEvent) -> bool:
    """Whether the interval between two touches was a carry by the same player.

    Four things must hold, and each rules out a specific false positive:

    * the same player is involved, so the ball did not simply change hands;
    * the clock advanced, because two touches recorded at the same instant are one
      action seen twice rather than travel between them. The generator emits a shot
      and the goal it produced on the same clock, and reading that pair as a carry
      would invent a seventeen-unit path from the shot's target back to the shot's own
      starting point. The goal is excluded from the touches entirely by
      :func:`_touches`, so that pair cannot reach here — this is the second guard, and
      it holds if that exclusion is ever changed;
    * both ends of the interval are known, so there is a path to measure;
    * and the ball actually moved further than :data:`CARRY_THRESHOLD`.
    """
    if earlier.player.id != later.player.id:
        return False
    if later.clock <= earlier.clock:
        return False
    if earlier.to_xy is None or later.from_xy is None:
        return False
    return math.dist(earlier.to_xy, later.from_xy) > CARRY_THRESHOLD


def extract_build_up(
    events: Sequence[MatchEvent],
    goal: MatchEvent,
    *,
    include_finish: bool = True,
) -> tuple[BuildUpStep, ...]:
    """The ordered steps that produced ``goal``.

    Args:
        events: the whole match, in clock order.
        goal: the goal event to walk back from.
        include_finish: keep the finishing strike as the last step. Set ``False`` for
            the build-up alone — the passes and carries that made the chance, without
            the shot that took it.

    Returns:
        Steps in the order they happened, each flagged ``derived`` when it was
        inferred rather than recorded. Empty when nothing preceded the goal, which is
        the honest answer for an unassisted finish rather than an error.

    Raises:
        ValueError: if ``goal`` is not a goal event. A pass walked backwards from
            would produce a plausible-looking but meaningless path.
    """
    if goal.type != "goal":
        raise ValueError(f"extract_build_up expects a goal, got {goal.type!r}")

    touches = _touches(events, goal, include_finish=include_finish)

    steps: list[BuildUpStep] = []
    for position, touch in enumerate(touches):
        if position > 0 and _carry_between(touches[position - 1], touch):
            previous = touches[position - 1]
            # The carry is the ball moving while the same player was on it, so it runs
            # from where the previous touch left the ball to where this one began.
            assert previous.to_xy is not None and touch.from_xy is not None
            steps.append(
                BuildUpStep(
                    index=len(steps),
                    kind="carry",
                    team=touch.team,
                    player=touch.player.name,
                    clock=(previous.clock + touch.clock) // 2,
                    start_xy=previous.to_xy,
                    end_xy=touch.from_xy,
                    derived=True,
                )
            )
        assert touch.from_xy is not None and touch.to_xy is not None
        steps.append(
            BuildUpStep(
                index=len(steps),
                kind=_TOUCH_KINDS[touch.type],  # type: ignore[index]
                team=touch.team,
                player=touch.player.name,
                clock=touch.clock,
                start_xy=touch.from_xy,
                end_xy=touch.to_xy,
                derived=False,
            )
        )
    return tuple(steps)


def summarise_move(
    steps: Sequence[BuildUpStep], *, goal_clock: int
) -> MoveSummary:
    """Reduce a move to the numbers that describe it.

    ``duration`` runs from the first step to the goal rather than to the last step, so
    a move whose finish is the goal measures the whole thing. It is clamped at zero,
    because a hand-built fixture can legitimately place its goal before its own
    build-up and a negative duration would be a worse answer than none.

    ``start_zone`` is read from the first step's starting position, not from the
    possession marker that preceded it. They agree in generated matches, and where a
    hand-built fixture makes them differ the step wins — the zone has to describe the
    path that is actually drawn, or the summary would contradict the picture.

    An empty move summarises to zeros with an ``"unknown"`` zone. That is not a
    failure state — it is an unassisted finish, and saying so is better than inventing
    a starting third for a move that had no start.
    """
    if not steps:
        return MoveSummary(
            passes=0,
            carries=0,
            shots=0,
            steps=0,
            distance=0.0,
            duration=0,
            start_zone="unknown",
            start_xy=None,
        )

    first = steps[0]
    return MoveSummary(
        passes=sum(1 for step in steps if step.kind == "pass"),
        carries=sum(1 for step in steps if step.kind == "carry"),
        shots=sum(1 for step in steps if step.kind == "shot"),
        steps=len(steps),
        distance=round(sum(step.distance for step in steps), 2),
        duration=max(0, goal_clock - first.clock),
        start_zone=zone_for(first.start_xy[0], first.team),
        start_xy=first.start_xy,
    )


def highlight_for_goal(
    events: Sequence[MatchEvent], goal: MatchEvent, *, seed: int
) -> GoalHighlight:
    """Build the full highlight for one goal, labels included.

    The labels are attached here rather than by the view, so no renderer can show a
    highlight without them. Every payload states that it came from a synthetic match;
    one carrying an inferred step additionally says so.
    """
    steps = extract_build_up(events, goal)
    summary = summarise_move(steps, goal_clock=goal.clock)
    labels = [LABEL_SYNTHETIC]
    if any(step.derived for step in steps):
        labels.append(LABEL_DERIVED_CARRIES)

    return GoalHighlight(
        goal_id=goal.id,
        team=goal.team,
        scorer=goal.player.name,
        minute=goal.minute,
        clock=goal.clock,
        seed=seed,
        steps=steps,
        summary=summary,
        # The goal event records where the ball ended. Falling back to where it was
        # struck keeps a hand-built fixture without a target usable rather than blank.
        finish_xy=goal.to_xy if goal.to_xy is not None else goal.from_xy,
        labels=tuple(labels),
    )


def build_highlights(
    events: Sequence[MatchEvent],
    *,
    seed: int,
    until: int | None = None,
) -> tuple[GoalHighlight, ...]:
    """A highlight for every goal, in clock order.

    Args:
        events: the whole match.
        seed: the seed the match was generated from. Carried into every highlight so a
            payload can be regenerated on its own, without the rest of the match.
        until: ignore goals after this match second. The overlay passes its own final
            clock, so a shortened replay does not advertise goals it never reaches.
    """
    return tuple(
        highlight_for_goal(events, event, seed=seed)
        for event in events
        if event.type == "goal" and (until is None or event.clock <= until)
    )


def highlight_payload(
    seed: int,
    match_id: str = DEFAULT_MATCH_ID,
    *,
    until: int | None = None,
) -> list[dict[str, Any]]:
    """One JSON object per goal, for a match identified only by its seed.

    This is the standalone entry point: generate the match from the seed, then return
    the highlights. :func:`build_highlights` is the same thing when the caller already
    holds the events, which the overlay does — regenerating a match it has already
    built would be wasted work, and would put two independently generated matches in a
    position to disagree.

    Deterministic: the same arguments always produce the same JSON, because the
    generator is a function of its seed and nothing here consults a clock or a set
    iteration order.
    """
    match = generate_match(seed, match_id)
    return [
        highlight.to_dict()
        for highlight in build_highlights(match.events, seed=seed, until=until)
    ]
