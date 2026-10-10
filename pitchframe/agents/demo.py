"""A deterministic, model-free pair of agents — for demos, evaluation and recording.

No model, no network, no quota, and no dependencies. Two jobs:

**The demo.** It deliberately inverts one claim, so the reject-and-retry loop is
visible in a single command instead of described in prose. The injected fault is
labelled in the output. That labelling is not cosmetic: if an injected failure were
presented as a natural one, the claim "we catch wrong claims" would be
unfalsifiable, because the failures being caught would have been planted by the same
code reporting the catch.

**The evaluation harness.** Running twenty seeded matches through this produces the
rejection and correction rates quoted in [the README](../README.md). Because nothing
here is random beyond the match seed and nothing calls a model, those numbers are
reproducible by anyone who clones the repository — which is the only way a
performance claim is worth anything.

This module is standard library only, and that is the point: `python -m pitchframe
--claims` works on a bare interpreter. The real agents in :mod:`narrator` and
:mod:`verifier` implement the same protocols, so substituting them changes nothing
about the loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..claims import Claim, Verdict
from .pipeline import Assessment, Snapshot

__all__ = ["InjectingNarrator", "RuleVerifier", "FAULT_MODES"]

#: Faults that can be injected. ``none`` measures the honest baseline.
FAULT_MODES = ("none", "narrator-invert")


@dataclass
class InjectingNarrator:
    """Proposes real claims from a snapshot, optionally inverting one of them.

    Args:
        fault: which fault to inject. ``narrator-invert`` flips the direction of one
            claim, which is the failure the Verifier exists to catch.
        max_claims: how many of the strongest readings to claim about.
        target: which reading to invert, by key. Defaults to the strongest one.
    """

    fault: str = "narrator-invert"
    max_claims: int = 4
    target: str | None = None

    #: Keys whose claims were deliberately inverted, for the output to label.
    injected: set[str] = field(default_factory=set)
    #: Every proposal, so an evaluation run can compare proposed against published.
    proposed: list[Claim] = field(default_factory=list)

    def _strongest(self, snap: Snapshot) -> list:
        """Readings ranked by relative movement.

        Relative rather than absolute, because the metrics do not share a scale: a
        momentum delta of 10 and a possession delta of 0.3 say very different things
        about which is the bigger story, and comparing the raw numbers would pick
        whichever metric happens to have the largest units.
        """
        moved = [r for r in snap.readings if not r.is_flat]
        moved.sort(key=lambda r: abs(r.delta) / (abs(r.baseline_value) + 1e-6), reverse=True)
        return moved[: self.max_claims]

    async def propose(self, snap: Snapshot) -> Sequence[Claim]:
        picks = self._strongest(snap)
        if not picks:
            return []

        invert_key = None
        if self.fault == "narrator-invert" and picks:
            invert_key = self.target or picks[0].key
            self.injected.add(invert_key)

        claims: list[Claim] = []
        for index, reading in enumerate(picks):
            direction = reading.direction
            if reading.key == invert_key:
                direction = "down" if direction == "up" else "up"  # the injected fault
            claims.append(
                Claim(
                    claim_id=f"W{snap.window[0]}-{index + 1}",
                    metric=reading.metric,
                    team=reading.team,
                    window=snap.window,
                    direction=direction,  # type: ignore[arg-type]
                    magnitude=abs(reading.delta),
                    text=f"{reading.key} moved {direction}",
                )
            )
        self.proposed.extend(claims)
        return claims

    async def revise(
        self, claim: Claim, verdict: Verdict, assessment: Assessment
    ) -> Claim | None:
        """Correct the direction to whatever was measured.

        A revision only happens when the measurement actually names a direction. If
        the rejection was structural — an invented metric, a window with no baseline
        — there is nothing honest to correct it to, and inventing one would be the
        exact behaviour the Verifier is meant to prevent.
        """
        if verdict.measured_direction is None:
            return None
        return Claim(
            claim_id=f"{claim.claim_id}r",
            metric=claim.metric,
            team=claim.team,
            window=claim.window,
            direction=verdict.measured_direction,
            magnitude=abs(verdict.delta) if verdict.delta is not None else claim.magnitude,
            text=f"{claim.metric} moved {verdict.measured_direction}",
        )


@dataclass
class RuleVerifier:
    """Turns a rejection into an explanation, with no model involved.

    Kept deliberately dumb. Its purpose is to make the pipeline runnable and
    reproducible anywhere; the model-backed Verifier exists to say the same thing in
    better prose, not to produce a better verdict.
    """

    assessments: int = 0

    async def assess(self, claim: Claim, verdict: Verdict) -> Assessment:
        self.assessments += 1
        if verdict.measured_direction is None:
            return Assessment(
                claim_id=claim.claim_id,
                explanation=f"cannot be repaired: {verdict.reason}",
                hint="",
            )
        return Assessment(
            claim_id=claim.claim_id,
            explanation=(
                f"{claim.metric} was measured moving {verdict.measured_direction}, "
                f"not {claim.direction}. Discrepancy: {verdict.discrepancy()}."
            ),
            hint=f"restate the direction as {verdict.measured_direction}",
        )
