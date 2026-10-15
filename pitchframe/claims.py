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

**Reading model output is tolerant; deciding truth is not.** Everything above
assumes a payload that already parses. Real replies arrive wrapped in code fences,
followed by commentary, keyed with names the schema never mentioned, or nested
inside an envelope object. Those are containers around a claim, not claims that are
wrong, so :func:`extract_json_payloads` and :func:`iter_claim_payloads` look through
the packaging and :func:`parse_claim` accepts a bounded allowlist of key spellings.

The tolerance is deliberately narrow, and the line is worth stating because it is
not arbitrary: packaging, key *spelling* and the case of the two-valued direction
enum are forgiven, because none of them can change what was claimed. Value
*synonyms* ("rising" for "up") and alternative value *shapes* for the window pair
are not, because accepting those means inferring an intent the model did not
express — and inferring intent is the failure this module exists to prevent.

This module is standard library only, and deliberately usable without
``agent-framework`` installed: these are domain rules, not model orchestration. A
rule that only runs when a model is present is a rule nobody can test.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
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
    "iter_claim_payloads",
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

#: Key spellings models actually use, mapped onto the canonical field name.
#: Keys are normalised first (case, underscores and hyphens dropped), so
#: ``metric_name``, ``metricName`` and ``Metric Name`` all arrive as
#: ``metricname``. This is an allowlist rather than fuzzy matching on purpose: a
#: parser that guessed at an unfamiliar key would eventually accept a claim whose
#: meaning it inferred instead of read.
_FIELD_ALIASES: dict[str, str] = {
    "claimid": "claim_id",
    "id": "claim_id",
    "metric": "metric",
    "metricname": "metric",
    "stat": "metric",
    "team": "team",
    "side": "team",
    "window": "window",
    "period": "window",
    "span": "window",
    "interval": "window",
    "timerange": "window",
    "windowseconds": "window",
    "direction": "direction",
    "trend": "direction",
    "movement": "direction",
    "magnitude": "magnitude",
    "amount": "magnitude",
    "delta": "magnitude",
    "change": "magnitude",
    "text": "text",
    "sentence": "text",
    "narrative": "text",
    "description": "text",
    "summary": "text",
}

#: The canonical spellings, which take precedence over every alias.
_CANONICAL_FIELDS = frozenset(_FIELD_ALIASES.values())

#: Keys a model uses when it wraps its claims in an envelope object.
_ENVELOPE_KEYS = frozenset(
    {
        "claims",
        "claim",
        "claimlist",
        "results",
        "result",
        "items",
        "item",
        "entries",
        "data",
        "output",
        "payload",
        "list",
    }
)


def _normalise_key(key: object) -> str:
    """Fold a key to its comparable form: lowercase, no separators."""
    return str(key).strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _resolve_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map a payload's own key spellings onto the canonical field names.

    Canonical spellings win over aliases regardless of the order they appear in, so
    a payload carrying both ``metric`` and ``metric_name`` is read the way the
    schema says to read it.
    """
    resolved: dict[str, Any] = {
        key: value for key, value in payload.items() if key in _CANONICAL_FIELDS
    }
    for key, value in payload.items():
        canonical = _FIELD_ALIASES.get(_normalise_key(key))
        if canonical is not None:
            resolved.setdefault(canonical, value)
    return resolved


def _looks_like_claim(payload: Mapping[str, Any]) -> bool:
    """Whether a mapping already carries the two fields no envelope would have."""
    fields = {_FIELD_ALIASES.get(_normalise_key(key)) for key in payload}
    return "metric" in fields and "direction" in fields


def _parse_window(raw: Any) -> Window:
    if isinstance(raw, Mapping):
        # {"start": 60, "end": 120} — the same pair, spelled out.
        normalised = {_normalise_key(key): value for key, value in raw.items()}
        for start_key, end_key in (("start", "end"), ("from", "to"), ("begin", "finish")):
            if start_key in normalised and end_key in normalised:
                raw = [normalised[start_key], normalised[end_key]]
                break
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

    An opener that never closes — a stray ``{`` in "I could not use {window 60s" —
    costs only that opener. The scan resumes one character later instead of
    abandoning the reply, because the well-formed JSON frequently comes *after* the
    prose that broke.

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
            # This opener never closes. Step past it and keep looking: giving up
            # here would discard valid JSON that merely follows some prose.
            index = start + 1
            continue

        try:
            payloads.append(json.loads(text[start : cursor + 1]))
        except json.JSONDecodeError:
            pass
        index = cursor + 1

    return tuple(payloads)


def iter_claim_payloads(payload: Any) -> Iterator[Mapping[str, Any]]:
    """Walk a decoded payload and yield every claim-shaped mapping inside it.

    Models emit claims in whatever envelope their training data suggested: a bare
    array, an array nested one level deeper, or an object like ``{"claims": [...]}``.
    All three mean the same thing, so the shape of the container is treated as
    packaging to look through rather than a protocol error to report.

    A mapping that is neither claim-shaped nor a recognisable envelope is still
    yielded, so :func:`parse_claim` can raise its precise "missing required key"
    message naming the keys it did receive. Swallowing it here would turn a
    diagnosable protocol failure into silence, which is the opposite of useful when
    the retry prompt is built from that message.
    """
    if isinstance(payload, (list, tuple)):
        for item in payload:
            yield from iter_claim_payloads(item)
        return

    if not isinstance(payload, Mapping):
        return

    if not _looks_like_claim(payload):
        envelopes = [
            value
            for key, value in payload.items()
            if _normalise_key(key) in _ENVELOPE_KEYS
        ]
        if envelopes:
            for value in envelopes:
                yield from iter_claim_payloads(value)
            return

    yield payload


def parse_claim(payload: Mapping[str, Any]) -> Claim:
    """Build a :class:`Claim` from decoded JSON, rejecting anything malformed.

    Shape only — this does not consult the event stream. A claim can parse cleanly
    and still be rejected by :func:`verify_claim`, and keeping those two failures
    distinct is what lets the retry prompt say whether the model misspoke or was
    simply wrong.

    Field names are resolved through :data:`_FIELD_ALIASES` before anything is
    required, so a claim that says ``metric_name`` and ``trend`` is read as the
    claim it is. Missing fields are reported with the keys that were actually
    present, since that message is what the model is retried with.

    Raises:
        ClaimError: if the payload is not a well-formed claim.
    """
    if not isinstance(payload, Mapping):
        raise ClaimError(f"claim must be an object, got {type(payload).__name__}")

    fields = _resolve_fields(payload)
    received = ", ".join(sorted(str(key) for key in payload)) or "nothing"

    def required(name: str) -> Any:
        if name not in fields:
            raise ClaimError(
                f"claim is missing required key {name!r}; "
                f"the keys it did have were: {received}"
            )
        return fields[name]

    team: Team | None = None
    raw_team = fields.get("team")
    if raw_team is not None:
        if not isinstance(raw_team, str) or raw_team.strip().lower() not in ("home", "away"):
            raise ClaimError(f"team must be 'home', 'away' or null, got {raw_team!r}")
        team = "home" if raw_team.strip().lower() == "home" else "away"

    raw_direction = required("direction")
    # Case is spelling, not meaning: "Up" and "up" assert the same thing.
    if not isinstance(raw_direction, str) or raw_direction.strip().lower() not in ("up", "down"):
        raise ClaimError(f"direction must be 'up' or 'down', got {raw_direction!r}")
    direction: Direction = "up" if raw_direction.strip().lower() == "up" else "down"

    raw_magnitude = fields.get("magnitude", 0.0)
    try:
        magnitude = float(raw_magnitude)
    except (TypeError, ValueError) as exc:
        raise ClaimError(f"magnitude {raw_magnitude!r} is not a number") from exc

    claim_id = str(required("claim_id")).strip()
    if not claim_id:
        raise ClaimError("claim_id must not be empty")

    return Claim(
        claim_id=claim_id,
        metric=str(required("metric")).strip(),
        team=team,
        window=_parse_window(required("window")),
        direction=direction,
        magnitude=magnitude,
        text=str(fields.get("text", "")).strip(),
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
        # Carried on a verified verdict too, not only on a rejection: the overlay's
        # receipt reads "claimed ... · measured ..." and a passing claim is exactly
        # the case where the two sides agreeing is worth showing.
        claimed_magnitude=claim.magnitude,
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
