"""The propose, verify and retry loop that turns events into checked claims.

The shape is: the Narrator proposes a claim, the Verifier checks it, and a rejected
claim is sent back once with the measured numbers attached. What survives is
published; what does not is dropped rather than shown.

Three decisions in here are load-bearing.

**The verdict is arithmetic.** :func:`~pitchframe.claims.verify_claim` decides, and
the Verifier agent cannot overrule it. The agent's contribution is the *explanation*
and the *retry guidance* — real work, but not the part that decides truth. A
verifier whose verdict could be talked out of the truth would be worse than none,
because it would launder wrong claims as checked ones.

**Exactly one retry.** A model that has already inverted a metric once rarely
recovers on request, and an unbounded loop against a paid endpoint is how a demo
turns into a bill. One correction attempt, then the claim is dropped.

**The snapshot is the honest input.** The Narrator is shown the real measured
values for the window and its baseline. Flipping a direction is therefore a genuine
error rather than something we engineered by withholding information — which
matters, because a failure the system sets up on purpose proves nothing about the
system.

This module is standard library only and imports no agent framework. The loop takes
any object matching :class:`Narrator` and :class:`Verifier`, so the retry logic can
be tested against plain fakes, and the model-backed implementations are one
substitution away.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..claims import (
    Claim,
    Verdict,
    verify_claim,
)
from ..schema import MatchEvent, Team
from ..stats import METRIC_SPECS, Window, evaluate, in_window

__all__ = [
    "Assessment",
    "MetricReading",
    "Narrator",
    "PipelineResult",
    "Snapshot",
    "TraceEvent",
    "Verifier",
    "render_snapshot",
    "run_pipeline",
    "snapshot",
]


@dataclass(frozen=True, slots=True)
class MetricReading:
    """One metric over a window, next to the same metric over its baseline.

    The ``direction`` is computed, not judged. It is the answer, and it is present
    in the Narrator's prompt on purpose: the task is to describe football, not to do
    arithmetic, and a system that fails at a task it was not asked to do teaches
    nothing about the pipeline.
    """

    metric: str
    team: Team | None
    value: float
    baseline_value: float

    @property
    def key(self) -> str:
        """Stable label, unique within a snapshot."""
        return self.metric if self.team is None else f"{self.metric}@{self.team}"

    @property
    def delta(self) -> float:
        return self.value - self.baseline_value

    @property
    def direction(self) -> str:
        return "up" if self.delta > 0 else "down"

    @property
    def is_flat(self) -> bool:
        return abs(self.delta) < 1e-9


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything the Narrator is allowed to talk about, for one window."""

    window: Window
    baseline: Window
    readings: tuple[MetricReading, ...]

    def by_key(self, key: str) -> MetricReading | None:
        for reading in self.readings:
            if reading.key == key:
                return reading
        return None


def snapshot(
    events: Sequence[MatchEvent],
    window: Window,
    baseline: Window,
) -> Snapshot:
    """Measure every claimable metric over a window and its baseline.

    Only metrics that can actually be evaluated are included. A reading that raised
    would become a prompt that lies by omission, so failures are skipped here where
    they are visible, rather than surfacing as a mysterious rejection later.
    """
    readings: list[MetricReading] = []
    in_claim = in_window(events, window)
    in_baseline = in_window(events, baseline)

    for metric, spec in METRIC_SPECS.items():
        sides: tuple[Team | None, ...] = ("home", "away") if spec.per_team else (None,)
        if spec.requires_team:
            sides = ("home", "away")

        for side in sides:
            try:
                value = evaluate(metric, in_claim, side)
                base = evaluate(metric, in_baseline, side)
            except (KeyError, ValueError, ZeroDivisionError):
                continue
            readings.append(
                MetricReading(metric=metric, team=side, value=value, baseline_value=base)
            )

    return Snapshot(window=window, baseline=baseline, readings=tuple(readings))


def render_snapshot(snap: Snapshot) -> str:
    """Render a snapshot as the table the Narrator reads.

    Flat readings are labelled rather than hidden. A model that cannot see that a
    metric is unchanged may claim it moved; showing ``flat`` makes any such claim an
    unambiguous error instead of a defensible guess.
    """
    start, end = snap.window
    b_start, b_end = snap.baseline
    lines = [
        f"Window {start}-{end}s, compared against baseline {b_start}-{b_end}s.",
        "",
        f"{'metric':<42} {'baseline':>10} {'window':>10}  {'direction':<6} delta",
        "-" * 84,
    ]
    for reading in snap.readings:
        shown = "flat" if reading.is_flat else reading.direction
        lines.append(
            f"{reading.key:<42} {reading.baseline_value:>10.4f} "
            f"{reading.value:>10.4f}  {shown:<6} {reading.delta:+.4f}"
        )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Assessment:
    """The Verifier's explanation of a rejection, and how to fix it.

    Carries no verdict. The verdict was already decided by arithmetic; this is the
    part a language model is genuinely better at than a comparison, which is
    explaining *why* it happened and what to try instead.
    """

    claim_id: str
    explanation: str
    hint: str = ""


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One thing that happened during the run, for the overlay and the export.

    Every event is written, including the drops. A trace that only recorded
    successes would make the verification story unfalsifiable, which is the failure
    mode the whole project exists to avoid.
    """

    seq: int
    kind: str
    claim_id: str | None
    detail: str
    window: Window | None = None


@dataclass
class PipelineResult:
    """What came out, and what it cost to get there."""

    claims: list[Claim] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)
    trace: list[TraceEvent] = field(default_factory=list)
    dropped: int = 0
    corrections: int = 0

    @property
    def verified(self) -> list[Claim]:
        """Claims that survived checking and may be published.

        Derived by joining claims to verdicts rather than tracked separately, so
        the two cannot drift apart: there is no code path that could add a claim to
        this list without a passing verdict behind it.
        """
        by_id = {v.claim_id: v for v in self.verdicts}
        published: list[Claim] = []
        for claim in self.claims:
            verdict = by_id.get(claim.claim_id)
            if verdict is not None and verdict.is_verified:
                published.append(claim)
        return published

    def summary(self) -> dict[str, int]:
        return {
            "verified": sum(1 for v in self.verdicts if v.is_verified),
            "rejected": sum(1 for v in self.verdicts if not v.is_verified),
            "corrections": self.corrections,
            "dropped": self.dropped,
        }


@runtime_checkable
class Narrator(Protocol):
    """Produces and revises claims. Implemented by the model-backed Narrator agent."""

    async def propose(self, snap: Snapshot) -> Sequence[Claim]: ...

    async def revise(
        self, claim: Claim, verdict: Verdict, assessment: Assessment
    ) -> Claim | None: ...


@runtime_checkable
class Verifier(Protocol):
    """Explains a rejection and suggests a correction."""

    async def assess(self, claim: Claim, verdict: Verdict) -> Assessment: ...


async def run_pipeline(
    narrator: Narrator,
    verifier: Verifier,
    events: Sequence[MatchEvent],
    window: Window,
    baseline: Window,
    *,
    max_corrections: int = 1,
) -> PipelineResult:
    """Propose claims for one window, check them, and retry the failures once.

    Args:
        narrator: proposes claims from a snapshot and revises rejected ones.
        verifier: explains why a claim was rejected.
        events: the whole match, since verification needs the baseline too.
        window: the clock window under discussion.
        baseline: the window the claims are measured against.
        max_corrections: how many times a single claim may be sent back. One by
            default; raising it multiplies cost against a paid endpoint.

    Every attempt is verified with :func:`~pitchframe.claims.verify_claim`, including
    the corrected one. A revision is not trusted because it was a revision — it is
    checked the same way the original was, which is what stops "retry" from becoming
    "accept".
    """
    result = PipelineResult()
    seq = 0

    def record(kind: str, detail: str, claim_id: str | None = None) -> None:
        nonlocal seq
        result.trace.append(
            TraceEvent(seq=seq, kind=kind, claim_id=claim_id, detail=detail, window=window)
        )
        seq += 1

    snap = snapshot(events, window, baseline)
    record("snapshot", f"{len(snap.readings)} readings for window {window[0]}-{window[1]}s")

    try:
        proposed = await narrator.propose(snap)
    except Exception as exc:  # noqa: BLE001 - a dead narrator must not kill the run
        record("narrator.error", f"{type(exc).__name__}: {exc}")
        return result

    record("narrator.proposed", f"{len(proposed)} claim(s)")

    for claim in proposed:
        verdict = verify_claim(claim, events)
        result.claims.append(claim)
        result.verdicts.append(verdict)
        record("claim.verified" if verdict.is_verified else "claim.rejected",
               verdict.reason, claim.claim_id)

        if verdict.is_verified:
            continue

        current = claim
        for _ in range(max_corrections):
            try:
                assessment = await verifier.assess(current, verdict)
            except Exception as exc:  # noqa: BLE001 - fall back to the raw reason
                assessment = Assessment(
                    claim_id=current.claim_id,
                    explanation=f"verifier unavailable ({type(exc).__name__}); "
                    f"using the measured reason",
                    hint=verdict.reason,
                )
                record("verifier.error", str(exc), current.claim_id)
            record("verifier.assessed", assessment.explanation, current.claim_id)

            try:
                revised = await narrator.revise(current, verdict, assessment)
            except Exception as exc:  # noqa: BLE001
                record("narrator.error", f"{type(exc).__name__}: {exc}", current.claim_id)
                break

            if revised is None:
                record("claim.abandoned", "narrator declined to correct", current.claim_id)
                break

            result.corrections += 1
            revised_verdict = verify_claim(revised, events)
            result.claims.append(revised)
            result.verdicts.append(revised_verdict)
            record(
                "claim.corrected" if revised_verdict.is_verified else "claim.corrected.failed",
                revised_verdict.reason,
                revised.claim_id,
            )
            current, verdict = revised, revised_verdict
            if revised_verdict.is_verified:
                break

        if not verdict.is_verified:
            result.dropped += 1
            record("claim.dropped", verdict.reason, current.claim_id)

    return result
