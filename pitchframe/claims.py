"""Structured claims, and the arithmetic that decides whether they are true.

The narrative layer is not permitted to assert free text. Everything it says is a
:class:`Claim`: a named metric, a team, a closed clock window, and a direction.
Typing the output is what turns "the model said something plausible" into "the
model said something checkable", and checking is the entire point of this system.

A direction is meaningless on its own — "momentum is up" needs a "compared to
when". So a claim carries a :attr:`~Claim.baseline_window`: the equal-length period
immediately before the window it is about. The comparison is part of the claim
rather than left implicit in prose, which means it can be recomputed rather than
argued about.

**Arithmetic is never delegated to a model.** :func:`verify_claim` is pure code
that recomputes both windows from the raw event stream and compares. The language
model's role in verification is to explain a discrepancy in words and to guide the
retry — never to decide the outcome. A verifier whose verdict could be talked out
of the truth would be worse than no verifier at all, because it would launder wrong
claims as checked ones.

This module is standard library only, and deliberately usable without
``agent-framework`` installed: these are domain rules, not model orchestration. A
rule that only runs when a model is present is a rule nobody can test.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .schema import MatchEvent, Team
from .stats import METRIC_SPECS, Window, evaluate, in_window

__all__ = [
    "CLAIM_SCHEMA_HINT",
    "DIRECTION_EPSILON",
    "Claim",
    "ClaimError",
    "ClaimStatus",
    "Direction",
    "Verdict",
    "baseline_window_for",
    "extract_json_payloads",
    "parse_claim",
    "summarise",
    "validate_claim",
    "verify_claim",
]

#: A claim asserts one of these. ``flat`` is deliberately absent: a claim that
#: nothing changed is not a claim worth making, and admitting it here would let a
#: model pad its output with no-ops that always verify.
Direction = Literal["up", "down"]

#: Whether a claim survived checking.
ClaimStatus = Literal["verified", "rejected"]

#: Differences below this are floating-point noise, not movement. Shared by the
#: direction test and the materiality test so the two cannot disagree about what
#: counts as a change.
DIRECTION_EPSILON = 1e-9

#: The JSON shape the Narrator is asked to produce. Kept beside the parser so the
#: prompt and the validator cannot drift apart — a schema hint that promises more
#: than the parser accepts is how you get a model that is right and a system that
#: rejects it.
CLAIM_SCHEMA_HINT = """{
  "claim_id": "short stable identifier, unique within the match",
  "metric": "one of the registered metric names",
  "team": "home" | "away" | null,
  "window": [start_second, end_second],
  "direction": "up" | "down",
  "magnitude": <number>,
  "text": "one sentence a casual fan would understand"
}"""


class ClaimError(ValueError):
    """A payload could not be read as a claim.

    Raised for shape problems — missing keys, wrong types, an unparsable window.
    Deliberately separate from *verification*: malformed output is the model
    failing to speak the protocol, while a rejected claim is the model speaking
    the protocol and being wrong. The two need different retry responses.
    """


def baseline_window_for(window: Window) -> Window:
    """The equal-length window immediately before ``window``.

    Equal length matters: comparing a 10-second window against a 60-second one
    would make almost any metric look like it moved.

    Raises:
        ValueError: if the window is reversed or the baseline would start before
            kick-off, since there is then nothing to compare against.
    """
    start, end = window
    if end < start:
        raise ValueError(f"window {window!r} ends before it starts")
    length = end - start
    if length <= 0:
        raise ValueError(f"window {window!r} is empty")
    if start - length < 0:
        raise ValueError(
            f"window {window!r} has no baseline: it would start {length}s before kick-off"
        )
    return (start - length, start)


@dataclass(frozen=True, slots=True)
class Claim:
    """One checkable assertion about how a metric moved.

    Args:
        claim_id: stable identifier, used to pair a claim with its verdict and to
            keep retries distinguishable from first attempts.
        metric: a key of :data:`pitchframe.stats.METRICS`.
        team: the side the claim is about, or ``None`` for a whole-match metric.
        window: the clock window the claim is about, as ``(start, end)`` seconds.
        direction: which way the metric moved relative to the baseline window.
        magnitude: how far it moved, in the metric's own units.
        text: the human-facing sentence, shown in the overlay.
    """

    claim_id: str
    metric: str
    team: Team | None
    window: Window
    direction: Direction
    magnitude: float
    text: str = ""

    @property
    def baseline_window(self) -> Window:
        """The window this claim is measured against.

        Raises:
            ValueError: if there is no room before kick-off for a baseline.
        """
        return baseline_window_for(self.window)

    def describe(self) -> str:
        """A compact label for logs and the agent ticker."""
        side = f" ({self.team})" if self.team else ""
        start, end = self.window
        return (
            f"{self.claim_id} {self.metric}{side} {self.direction} "
            f"{self.magnitude:+.3f} over {start}-{end}s"
        )


@dataclass(frozen=True, slots=True)
class Verdict:
    """The outcome of checking a claim, with the numbers that produced it.

    The values are carried, not just the status, because a rejection has to be
    actionable: the Narrator's retry is given this object and told to correct
    itself, and the overlay shows ``claimed up 0.23 · measured down 0.05``. A bare
    boolean would tell the model it was wrong without telling it what was true.
    """

    claim_id: str
    status: ClaimStatus
    reason: str
    claimed_direction: Direction | None = None
    claimed_magnitude: float | None = None
    measured_direction: Direction | None = None
    measured_value: float | None = None
    baseline_value: float | None = None

    @property
    def delta(self) -> float | None:
        """Signed change from baseline to measured window, if both are known."""
        if self.measured_value is None or self.baseline_value is None:
            return None
        return self.measured_value - self.baseline_value

    @property
    def is_verified(self) -> bool:
        return self.status == "verified"

    def discrepancy(self) -> str:
        """A one-line comparison for the overlay, or a short reason if unknown.

        Reads ``claimed up +0.23 · measured down -0.05``: the two numbers sit side
        by side so a rejection is legible without opening the trace.
        """
        if self.delta is None:
            return self.reason
        measured = self.measured_direction or "flat"
        if self.claimed_direction is None or self.claimed_magnitude is None:
            return f"measured {measured} {self.delta:+.2f}"
        signed_claim = abs(self.claimed_magnitude)
        if self.claimed_direction == "down":
            signed_claim = -signed_claim
        return (
            f"claimed {self.claimed_direction} {signed_claim:+.2f} · "
            f"measured {measured} {self.delta:+.2f}"
        )


# --- reading model output ----------------------------------------------------


def _require(payload: Mapping[str, Any], key: str) -> Any:
    if key not in payload:
        raise ClaimError(f"claim is missing required key {key!r}")
    return payload[key]


def _parse_window(raw: Any) -> Window:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            start, end = int(raw[0]), int(raw[1])
        except (TypeError, ValueError) as exc:
            raise ClaimError(f"window {raw!r} is not a pair of integers") from exc
        if end <= start:
            raise ClaimError(f"window {(start, end)!r} must end after it starts")
        return (start, end)
    raise ClaimError(f"window {raw!r} must be a [start, end] pair")


def extract_json_payloads(text: str) -> tuple[Any, ...]:
    """Pull every JSON object or array out of free-form model output.

    Models wrap JSON in code fences, preface it with "Here are the claims:", or
    append a closing remark. Demanding bare JSON is a losing game, so this scans
    for balanced spans instead of trusting the shape of the reply.

    Malformed spans are skipped rather than raised: a reply carrying one good claim
    and one broken one should still yield the good one, and the broken one will be
    caught by :func:`parse_claim` where the error can be attributed to a claim.

    Returns the decoded payloads in the order they appear.
    """
    payloads: list[Any] = []
    index = 0
    length = len(text)

    while index < length:
        if text[index] not in "{[":
            index += 1
            continue

        depth = 0
        in_string = False
        escaped = False
        start = index
        cursor = index

        while cursor < length:
            char = text[cursor]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1

        if depth != 0:
            # Unbalanced from here to the end; nothing further can parse.
            break

        try:
            payloads.append(json.loads(text[start : cursor + 1]))
        except json.JSONDecodeError:
            pass
        index = cursor + 1

    return tuple(payloads)


def parse_claim(payload: Mapping[str, Any]) -> Claim:
    """Build a :class:`Claim` from decoded JSON, rejecting anything malformed.

    Shape only — this does not consult the event stream. A claim can parse cleanly
    and still be rejected by :func:`verify_claim`, and keeping those two failures
    distinct is what lets the retry prompt say whether the model misspoke or was
    simply wrong.

    Raises:
        ClaimError: if the payload is not a well-formed claim.
    """
    if not isinstance(payload, Mapping):
        raise ClaimError(f"claim must be an object, got {type(payload).__name__}")

    team = payload.get("team")
    if team is not None and team not in ("home", "away"):
        raise ClaimError(f"team must be 'home', 'away' or null, got {team!r}")

    direction = _require(payload, "direction")
    if direction not in ("up", "down"):
        raise ClaimError(f"direction must be 'up' or 'down', got {direction!r}")

    raw_magnitude = payload.get("magnitude", 0.0)
    try:
        magnitude = float(raw_magnitude)
    except (TypeError, ValueError) as exc:
        raise ClaimError(f"magnitude {raw_magnitude!r} is not a number") from exc

    claim_id = str(_require(payload, "claim_id")).strip()
    if not claim_id:
        raise ClaimError("claim_id must not be empty")

    return Claim(
        claim_id=claim_id,
        metric=str(_require(payload, "metric")).strip(),
        team=team,
        window=_parse_window(_require(payload, "window")),
        direction=direction,
        magnitude=magnitude,
        text=str(payload.get("text", "")).strip(),
    )


# --- checkable rules ---------------------------------------------------------


def _last_clock(events: Sequence[MatchEvent]) -> int:
    return max((e.clock for e in events), default=0)


def _window_is_closed(
    window: Window,
    events: Sequence[MatchEvent],
    closed_windows: Sequence[Window] | None,
) -> tuple[bool, str]:
    """Whether a window has already happened, and why not if it has not."""
    if closed_windows is not None:
        if window in closed_windows:
            return True, ""
        return False, (
            f"window {window!r} has not been closed by the analyser yet; "
            f"closed windows are {sorted(closed_windows)}"
        )
    if window[1] > _last_clock(events):
        return False, (
            f"window {window!r} extends past the last recorded event "
            f"at {_last_clock(events)}s"
        )
    return True, ""


def validate_claim(
    claim: Claim,
    events: Sequence[MatchEvent],
    closed_windows: Sequence[Window] | None = None,
) -> tuple[str, ...]:
    """Structural problems with a claim, as human-readable strings.

    An empty result means the claim is well-formed against this match. These are
    the failures that need no arithmetic — an invented metric name, a window that
    has not happened yet, a team-scoped metric with no team. They are checked
    before any recomputation so the cheap rejections stay cheap.

    ``closed_windows`` models the live pipeline honestly: in a streaming system the
    analyser closes windows on its own schedule, and a Narrator that asserts about
    a window which has not closed yet is racing it. Passing the closed set turns
    that race into an explicit rejection instead of a silently different answer.
    """
    problems: list[str] = []

    if claim.metric not in METRIC_SPECS:
        known = ", ".join(sorted(METRIC_SPECS))
        problems.append(f"unknown metric {claim.metric!r}; known metrics are: {known}")
    else:
        spec = METRIC_SPECS[claim.metric]
        if spec.requires_team and claim.team is None:
            problems.append(f"metric {claim.metric!r} requires a team, but none was given")
        if not spec.requires_team and not spec.per_team and claim.team is not None:
            problems.append(
                f"metric {claim.metric!r} is whole-match and takes no team, "
                f"but {claim.team!r} was given"
            )

    closed, why = _window_is_closed(claim.window, events, closed_windows)
    if not closed:
        problems.append(why)

    try:
        baseline_window_for(claim.window)
    except ValueError as exc:
        problems.append(str(exc))

    if not claim.claim_id:
        problems.append("claim_id must not be empty")

    return tuple(problems)


def verify_claim(
    claim: Claim,
    events: Sequence[MatchEvent],
    closed_windows: Sequence[Window] | None = None,
) -> Verdict:
    """Recompute a claim against the event stream and rule on it.

    The measured direction comes from comparing the metric over the claim's window
    against the same metric over its baseline window. Nothing here consults a
    language model, the claim's own magnitude, or the claim's text — a claim is
    correct if the arithmetic says so and for no other reason.

    A claim asserting movement smaller than :data:`DIRECTION_EPSILON` is rejected as
    having no measurable change, because a directional claim about a flat metric is
    not true in either direction.
    """
    problems = validate_claim(claim, events, closed_windows)
    if problems:
        return Verdict(
            claim_id=claim.claim_id,
            status="rejected",
            reason="; ".join(problems),
            claimed_direction=claim.direction,
            claimed_magnitude=claim.magnitude,
        )

    try:
        baseline = claim.baseline_window
    except ValueError as exc:  # pragma: no cover - validate_claim already checks
        return Verdict(
            claim_id=claim.claim_id,
            status="rejected",
            reason=str(exc),
            claimed_direction=claim.direction,
            claimed_magnitude=claim.magnitude,
        )

    measured_value = evaluate(claim.metric, in_window(events, claim.window), claim.team)
    baseline_value = evaluate(claim.metric, in_window(events, baseline), claim.team)
    delta = measured_value - baseline_value

    if abs(delta) <= DIRECTION_EPSILON:
        return Verdict(
            claim_id=claim.claim_id,
            status="rejected",
            reason=(
                f"{claim.metric} is flat across {claim.window[0]}-{claim.window[1]}s "
                f"(no measurable change from baseline)"
            ),
            claimed_direction=claim.direction,
            claimed_magnitude=claim.magnitude,
            measured_direction=None,
            measured_value=measured_value,
            baseline_value=baseline_value,
        )

    measured_direction: Direction = "up" if delta > 0 else "down"

    if measured_direction != claim.direction:
        return Verdict(
            claim_id=claim.claim_id,
            status="rejected",
            reason=(
                f"{claim.metric} actually moved {measured_direction} "
                f"({baseline_value:.4f} -> {measured_value:.4f}), "
                f"but the claim says {claim.direction}"
            ),
            claimed_direction=claim.direction,
            claimed_magnitude=claim.magnitude,
            measured_direction=measured_direction,
            measured_value=measured_value,
            baseline_value=baseline_value,
        )

    return Verdict(
        claim_id=claim.claim_id,
        status="verified",
        reason=(
            f"{claim.metric} moved {measured_direction} "
            f"({baseline_value:.4f} -> {measured_value:.4f}) as claimed"
        ),
        claimed_direction=claim.direction,
        measured_direction=measured_direction,
        measured_value=measured_value,
        baseline_value=baseline_value,
    )


def summarise(verdicts: Sequence[Verdict]) -> dict[str, int]:
    """Counts by status, for the honesty table in the README and the overlay."""
    counts = {"verified": 0, "rejected": 0}
    for verdict in verdicts:
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
    return counts
