"""Pure functions that turn an event stream into football statistics.

Every function here is deterministic and free of side effects. That is
deliberate: it is what lets the Verifier agent treat this module as ground
truth. Given a claim that some metric moved in some direction over some window,
the Verifier recomputes the metric here and compares — no model call, no
opinion, just arithmetic.

Metric names are published in :data:`METRICS`, and :func:`compute` is the only
sanctioned entry point. The Verifier validates every incoming claim through it,
so a claim naming a metric that does not exist is rejected before it can reach
a human. Unknown-metric rejection is one of the real, non-manufactured failure
modes the pipeline is designed to catch.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .schema import MatchEvent, Team

__all__ = [
    "METRICS",
    "METRIC_SPECS",
    "MetricSpec",
    "evaluate",
    "compute",
    "in_window",
    "possession_share",
    "pass_accuracy",
    "pass_distance",
    "pass_difficulty",
    "pass_difficulty_mean",
    "pressure_index",
    "momentum",
    "chaos_index",
    "peak_speed",
    "longest_pressure_pass_streak",
]

#: A half-open clock window, ``[start, end)`` in seconds since kick-off.
Window = tuple[int, int]

#: How much each event type contributes to momentum.
_THREAT_WEIGHTS = {
    "shot": 3.0,
    "goal": 8.0,
    "tackle": 1.0,
    "pass": 0.2,
}

#: Default pressure above which a pass counts as "under pressure".
PRESSURE_THRESHOLD = 0.50


def in_window(events: Sequence[MatchEvent], window: Window) -> tuple[MatchEvent, ...]:
    """Slice events to a half-open clock window ``[start, end)``."""
    start, end = window
    return tuple(e for e in events if start <= e.clock < end)


# --- possession and passing -------------------------------------------------


def possession_share(events: Sequence[MatchEvent]) -> dict[str, float]:
    """Share of completed passes per team, as a rough possession proxy.

    Real possession is time-based and needs a tracking feed; with an event
    stream, completed passes are the standard stand-in.
    """
    home = sum(1 for e in events if e.type == "pass" and e.team == "home" and e.is_successful)
    away = sum(1 for e in events if e.type == "pass" and e.team == "away" and e.is_successful)
    total = home + away
    if total == 0:
        return {"home": 0.0, "away": 0.0}
    return {"home": round(home / total, 4), "away": round(away / total, 4)}


def pass_accuracy(events: Sequence[MatchEvent], team: Team) -> float:
    """Completed passes as a fraction of attempted passes, for one team."""
    passes = [e for e in events if e.type == "pass" and e.team == team]
    if not passes:
        return 0.0
    return round(sum(1 for p in passes if p.is_successful) / len(passes), 4)


def pass_distance(event: MatchEvent) -> float:
    """Distance travelled by a pass, in normalised pitch units."""
    if event.from_xy is None or event.to_xy is None:
        return 0.0
    return math.dist(event.from_xy, event.to_xy)


def pass_difficulty(event: MatchEvent) -> float:
    """Rate a pass from ``0.0`` (trivial) to ``1.0`` (very hard).

    Difficulty blends how far the ball had to travel with how much pressure the
    passer was under. Long passes into pressure are the hard ones, which is
    exactly the set the narrative layer should be calling out.

    Raises:
        ValueError: if ``event`` is not a pass.
    """
    if event.type != "pass":
        raise ValueError(f"pass_difficulty expects a pass, got {event.type!r}")
    difficulty_from_distance = min(pass_distance(event) / 45.0, 1.0)
    difficulty_from_pressure = min(max(event.pressure or 0.0, 0.0), 1.0)
    blended = 0.65 * difficulty_from_distance + 0.35 * difficulty_from_pressure
    return round(min(1.0, blended), 4)


def pass_difficulty_mean(events: Sequence[MatchEvent], team: Team) -> float:
    """Mean pass difficulty for a team, over the given events."""
    difficulties = [pass_difficulty(e) for e in events if e.type == "pass" and e.team == team]
    if not difficulties:
        return 0.0
    return round(sum(difficulties) / len(difficulties), 4)


# --- pressure, momentum, rhythm --------------------------------------------


def pressure_index(events: Sequence[MatchEvent], team: Team | None = None) -> float:
    """Mean pressure across events; ``team=None`` means both sides."""
    values = [
        e.pressure
        for e in events
        if e.pressure is not None and (team is None or e.team == team)
    ]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def momentum(events: Sequence[MatchEvent]) -> float:
    """Net attacking threat in the window; positive favours the home side.

    Weighted by event type so a shot counts for more than a pass, and only
    successful passes and tackles count at all.
    """
    score = 0.0
    for event in events:
        weight = _THREAT_WEIGHTS.get(event.type, 0.0)
        if event.type in ("pass", "tackle") and not event.is_successful:
            weight = 0.0
        score += weight if event.team == "home" else -weight
    return round(score, 4)


def chaos_index(events: Sequence[MatchEvent]) -> float:
    """Rhythm: ``0.0`` is metronomic, ``1.0`` is frantic end-to-end play.

    Measured as the coefficient of variation of the gaps between consecutive
    events. Steady possession play produces regular gaps; a chaotic spell
    produces a mixture of long lulls and bursts. This is the "control vs chaos"
    signal the challenge brief asks the narrative layer to explain.
    """
    clocks = sorted(e.clock for e in events)
    if len(clocks) < 3:
        return 0.0
    gaps = [b - a for a, b in zip(clocks, clocks[1:])]
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap <= 0:
        return 0.0
    variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
    coefficient = (variance**0.5) / mean_gap
    return round(min(1.0, coefficient / 1.5), 4)


def peak_speed(
    events: Sequence[MatchEvent],
    team: Team | None = None,
    kind: str | None = None,
) -> float:
    """Highest recorded speed in km/h, optionally filtered by team and type."""
    speeds = [
        e.speed_kmh
        for e in events
        if e.speed_kmh is not None
        and (team is None or e.team == team)
        and (kind is None or e.type == kind)
    ]
    return round(max(speeds), 1) if speeds else 0.0


# --- milestones -------------------------------------------------------------


def longest_pressure_pass_streak(
    events: Sequence[MatchEvent],
    team: Team,
    threshold: float = PRESSURE_THRESHOLD,
) -> int:
    """Longest run of consecutive completed passes under pressure.

    This is the kind of milestone the challenge brief asks for — a small,
    human-meaningful achievement that a casual fan and an analyst would both
    understand, and that is cheap to verify against the event stream.
    """
    longest = 0
    current = 0
    for event in events:
        if event.type in ("pass", "possession_change") and event.team != team:
            current = 0
            continue
        if event.type != "pass" or event.team != team:
            continue
        if event.is_successful and (event.pressure or 0.0) >= threshold:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


# --- the registry -----------------------------------------------------------

#: Every metric the system is allowed to make claims about. The Verifier
#: rejects any claim whose ``metric`` is not a key here.
METRICS: dict[str, Callable[..., float]] = {
    "possession_share": possession_share,
    "pass_accuracy": pass_accuracy,
    "pass_difficulty_mean": pass_difficulty_mean,
    "pressure_index": pressure_index,
    "momentum": momentum,
    "chaos_index": chaos_index,
    "peak_speed": peak_speed,
    "longest_pressure_pass_streak": longest_pressure_pass_streak,
}


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """What a metric needs in order to be evaluated, and what shape it returns.

    The Verifier has to evaluate a claim knowing nothing about the metric beyond
    its name, and the metrics do not share a calling convention: ``pass_accuracy``
    demands a team, ``momentum`` rejects one, and ``possession_share`` returns a
    mapping rather than a number. Capturing that as data rather than prose is what
    lets a claim carry only ``(metric, team)`` and still be checked.
    """

    fn: Callable[..., object]
    #: The metric cannot be evaluated without a team.
    requires_team: bool = False
    #: The metric returns ``{"home": float, "away": float}`` rather than a scalar.
    per_team: bool = False


#: Evaluation contract for every entry in :data:`METRICS`. Kept separate from the
#: registry so that ``METRICS`` stays a plain name-to-callable map.
METRIC_SPECS: dict[str, MetricSpec] = {
    "possession_share": MetricSpec(possession_share, per_team=True),
    "pass_accuracy": MetricSpec(pass_accuracy, requires_team=True),
    "pass_difficulty_mean": MetricSpec(pass_difficulty_mean, requires_team=True),
    "pressure_index": MetricSpec(pressure_index),
    "momentum": MetricSpec(momentum),
    "chaos_index": MetricSpec(chaos_index),
    "peak_speed": MetricSpec(peak_speed),
    "longest_pressure_pass_streak": MetricSpec(
        longest_pressure_pass_streak, requires_team=True
    ),
}


def evaluate(metric: str, events: Sequence[MatchEvent], team: Team | None = None) -> float:
    """Reduce any registered metric to a single number for one side.

    This is the bridge between the registry's varied signatures and the one thing a
    claim needs: a comparable scalar. A ``None`` team for a per-team metric reads the
    home side, which keeps the calling convention total rather than partial.

    Raises:
        KeyError: if ``metric`` is not registered.
        ValueError: if the metric needs a team and none could be resolved.
    """
    if metric not in METRIC_SPECS:
        known = ", ".join(sorted(METRIC_SPECS))
        raise KeyError(f"unknown metric {metric!r}; known metrics are: {known}")

    spec = METRIC_SPECS[metric]
    side: Team = team or "home"

    if spec.per_team:
        shares = spec.fn(events)
        if not isinstance(shares, dict) or side not in shares:
            raise ValueError(f"{metric!r} did not return a value for team {side!r}")
        return float(shares[side])

    if spec.requires_team:
        return float(spec.fn(events, side))

    return float(spec.fn(events))


def compute(metric: str, events: Sequence[MatchEvent], **kwargs: object) -> float:
    """Evaluate a named metric against a set of events.

    This is the single entry point the Verifier uses, so that anything the
    system claims can be recomputed the same way.

    ``possession_share`` returns a mapping rather than a scalar; callers that
    need one side should use :func:`possession_share` directly.

    Raises:
        KeyError: if ``metric`` is not registered.
    """
    if metric not in METRICS:
        known = ", ".join(sorted(METRICS))
        raise KeyError(f"unknown metric {metric!r}; known metrics are: {known}")
    return METRICS[metric](events, **kwargs)  # type: ignore[return-value]
