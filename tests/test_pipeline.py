"""Tests for the propose, verify and retry loop.

The loop is driven here by plain objects rather than model-backed agents, which is
the point of splitting the modules: the retry logic is the part most likely to be
wrong, so exercising it must not require a credential, a network or a quota.

The tests are written around the failure the system exists to catch — a claim whose
direction is inverted — and around the ways a real deployment degrades: a narrator
that dies, a verifier that dies, a correction that is still wrong. Every one of
those has to end in the claim being dropped rather than published.
"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Sequence

from pitchframe.agents.pipeline import (
    Assessment,
    Snapshot,
    render_snapshot,
    run_pipeline,
    snapshot,
)
from pitchframe.claims import Claim, Verdict
from pitchframe.schema import MatchEvent, Player

_HOME = Player(id="H1", name="Alaro", team="home", position="CM", shirt=8)
_AWAY = Player(id="A1", name="Brenvik", team="away", position="CM", shirt=8)

#: Momentum: +3 in the baseline window, 0 in the window. It falls by 3.
EVENTS = (
    MatchEvent("e1", "T", 10, "shot", "home", _HOME),
    MatchEvent("e2", "T", 110, "shot", "home", _HOME),
    MatchEvent("e3", "T", 120, "shot", "away", _AWAY),
    # A trailing event past the window's end, so the window counts as closed.
    # Without it every claim is rejected as premature and the loop under test
    # never gets as far as checking a direction.
    MatchEvent("e4", "T", 300, "pass", "home", _HOME, outcome="success"),
)
WINDOW = (100, 200)
BASELINE = (0, 100)


def momentum_claim(direction: str, claim_id: str = "C1") -> Claim:
    return Claim(
        claim_id=claim_id,
        metric="momentum",
        team=None,
        window=WINDOW,
        direction=direction,  # type: ignore[arg-type]
        magnitude=3.0,
        text="momentum shifted",
    )


class ScriptedNarrator:
    """A narrator whose proposals and revisions are fixed in advance."""

    def __init__(self, proposals: Sequence[Claim], revisions: Sequence[Claim | None]) -> None:
        self._proposals = list(proposals)
        self._revisions = list(revisions)
        self.revised: list[Verdict] = []

    async def propose(self, snap: Snapshot) -> Sequence[Claim]:
        return list(self._proposals)

    async def revise(
        self, claim: Claim, verdict: Verdict, assessment: Assessment
    ) -> Claim | None:
        self.revised.append(verdict)
        return self._revisions.pop(0) if self._revisions else None


class ScriptedVerifier:
    def __init__(self, explanation: str = "the measurement disagrees") -> None:
        self.explanation = explanation
        self.assessed: list[str] = []

    async def assess(self, claim: Claim, verdict: Verdict) -> Assessment:
        self.assessed.append(claim.claim_id)
        return Assessment(claim.claim_id, self.explanation, "use the measured direction")


def run(narrator: object, verifier: object, **kwargs: object):
    return asyncio.run(
        run_pipeline(  # type: ignore[arg-type]
            narrator, verifier, EVENTS, WINDOW, BASELINE, **kwargs  # type: ignore[arg-type]
        )
    )


class TestSnapshot(unittest.TestCase):
    def test_readings_include_both_sides_for_per_team_metrics(self) -> None:
        snap = snapshot(EVENTS, WINDOW, BASELINE)
        keys = {r.key for r in snap.readings}
        self.assertIn("possession_share@home", keys)
        self.assertIn("possession_share@away", keys)

    def test_whole_match_metrics_have_no_team_suffix(self) -> None:
        snap = snapshot(EVENTS, WINDOW, BASELINE)
        keys = {r.key for r in snap.readings}
        self.assertIn("momentum", keys)
        self.assertNotIn("momentum@home", keys)

    def test_reading_reports_the_direction_it_measured(self) -> None:
        snap = snapshot(EVENTS, WINDOW, BASELINE)
        reading = snap.by_key("momentum")
        assert reading is not None
        self.assertEqual(reading.direction, "down")
        self.assertEqual(reading.delta, -3.0)

    def test_rendered_table_shows_the_direction(self) -> None:
        """The narrator is given the answer; inverting it is then a real error."""
        text = render_snapshot(snapshot(EVENTS, WINDOW, BASELINE))
        self.assertIn("momentum", text)
        self.assertIn("down", text)

    def test_rendered_table_labels_flat_metrics(self) -> None:
        """Labelling 'flat' turns a hopeless guess into an unambiguous error."""
        flat = (
            MatchEvent("e1", "T", 10, "shot", "home", _HOME),
            MatchEvent("e2", "T", 110, "shot", "home", _HOME),
        )
        text = render_snapshot(snapshot(flat, WINDOW, BASELINE))
        self.assertIn("flat", text)


class TestLoopVerifiedFirstTime(unittest.TestCase):
    def test_correct_claim_is_published(self) -> None:
        result = run(ScriptedNarrator([momentum_claim("down")], []), ScriptedVerifier())
        self.assertEqual([c.claim_id for c in result.verified], ["C1"])

    def test_no_retry_is_attempted(self) -> None:
        narrator = ScriptedNarrator([momentum_claim("down")], [momentum_claim("down")])
        result = run(narrator, ScriptedVerifier())
        self.assertEqual(result.corrections, 0)
        self.assertEqual(narrator.revised, [])

    def test_summary_counts_one_verified(self) -> None:
        result = run(ScriptedNarrator([momentum_claim("down")], []), ScriptedVerifier())
        self.assertEqual(result.summary()["verified"], 1)
        self.assertEqual(result.summary()["dropped"], 0)


class TestLoopRejectsAndRetries(unittest.TestCase):
    def test_inverted_claim_is_rejected_then_corrected(self) -> None:
        narrator = ScriptedNarrator(
            [momentum_claim("up")], [momentum_claim("down", claim_id="C1r")]
        )
        result = run(narrator, ScriptedVerifier())
        self.assertEqual(result.corrections, 1)
        self.assertEqual([c.claim_id for c in result.verified], ["C1r"])

    def test_the_verifier_is_consulted_before_the_retry(self) -> None:
        verifier = ScriptedVerifier()
        narrator = ScriptedNarrator(
            [momentum_claim("up")], [momentum_claim("down", claim_id="C1r")]
        )
        run(narrator, verifier)
        self.assertEqual(verifier.assessed, ["C1"])

    def test_the_retry_sees_the_rejection_verdict(self) -> None:
        """The correction has to be informed, or it is just a second guess."""
        narrator = ScriptedNarrator(
            [momentum_claim("up")], [momentum_claim("down", claim_id="C1r")]
        )
        run(narrator, ScriptedVerifier())
        self.assertEqual(narrator.revised[0].measured_direction, "down")

    def test_a_correction_is_re_verified_not_trusted(self) -> None:
        """'Retry' must not quietly become 'accept'."""
        narrator = ScriptedNarrator([momentum_claim("up")], [momentum_claim("up", "C1r")])
        result = run(narrator, ScriptedVerifier())
        self.assertEqual(result.verified, [])
        self.assertEqual(result.dropped, 1)

    def test_the_number_of_corrections_is_capped(self) -> None:
        narrator = ScriptedNarrator(
            [momentum_claim("up")],
            [momentum_claim("up", "a"), momentum_claim("up", "b")],
        )
        result = run(narrator, ScriptedVerifier(), max_corrections=1)
        self.assertEqual(result.corrections, 1)


class TestLoopDegradesSafely(unittest.TestCase):
    def test_narrator_failure_yields_no_claims(self) -> None:
        class Dead:
            async def propose(self, snap: Snapshot) -> Sequence[Claim]:
                raise RuntimeError("endpoint unreachable")

            async def revise(self, *a: object) -> Claim | None:
                return None

        result = run(Dead(), ScriptedVerifier())
        self.assertEqual(result.verified, [])
        self.assertTrue(any(t.kind == "narrator.error" for t in result.trace))

    def test_verifier_failure_still_allows_a_correction(self) -> None:
        """A broken verifier degrades the explanation, not the retry."""
        class Dead:
            async def assess(self, claim: Claim, verdict: Verdict) -> Assessment:
                raise RuntimeError("verifier offline")

        narrator = ScriptedNarrator(
            [momentum_claim("up")], [momentum_claim("down", claim_id="C1r")]
        )
        result = run(narrator, Dead())
        self.assertEqual([c.claim_id for c in result.verified], ["C1r"])
        self.assertTrue(any(t.kind == "verifier.error" for t in result.trace))

    def test_narrator_declining_the_correction_drops_the_claim(self) -> None:
        """A narrator that will not revise must lose the claim, not keep it."""
        narrator = ScriptedNarrator([momentum_claim("up")], [None])
        result = run(narrator, ScriptedVerifier())
        self.assertEqual(result.verified, [])
        self.assertEqual(result.dropped, 1)
        self.assertTrue(any(t.kind == "claim.abandoned" for t in result.trace))

    def test_no_claim_ever_survives_unverified(self) -> None:
        """The invariant: published implies verified, whatever the agents did."""
        narrator = ScriptedNarrator(
            [momentum_claim("up"), momentum_claim("down", "ok")],
            [momentum_claim("up", "still-wrong")],
        )
        result = run(narrator, ScriptedVerifier())
        for claim in result.verified:
            self.assertIn(claim.direction, ("up", "down"))
            self.assertEqual(claim.metric, "momentum")
        self.assertEqual([c.claim_id for c in result.verified], ["ok"])


class TestTrace(unittest.TestCase):
    def test_every_claim_attempt_is_recorded(self) -> None:
        narrator = ScriptedNarrator(
            [momentum_claim("up")], [momentum_claim("down", claim_id="C1r")]
        )
        result = run(narrator, ScriptedVerifier())
        kinds = [t.kind for t in result.trace]
        self.assertIn("claim.rejected", kinds)
        self.assertIn("claim.corrected", kinds)

    def test_sequence_numbers_are_dense_and_ordered(self) -> None:
        narrator = ScriptedNarrator([momentum_claim("up")], [momentum_claim("down", "C1r")])
        result = run(narrator, ScriptedVerifier())
        self.assertEqual([t.seq for t in result.trace], list(range(len(result.trace))))

    def test_dropped_claims_are_traced_not_hidden(self) -> None:
        """A trace that hid failures would make the whole story unfalsifiable."""
        narrator = ScriptedNarrator([momentum_claim("up")], [None])
        result = run(narrator, ScriptedVerifier())
        self.assertTrue(any(t.kind == "claim.dropped" for t in result.trace))

    def test_trace_entries_carry_the_window(self) -> None:
        result = run(ScriptedNarrator([momentum_claim("down")], []), ScriptedVerifier())
        self.assertTrue(all(t.window == WINDOW for t in result.trace))


if __name__ == "__main__":
    unittest.main()
