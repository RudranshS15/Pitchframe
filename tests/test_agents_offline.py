"""End-to-end tests for the two agents, driven by the scripted client.

These run the *real* ``NarratorAgent`` and ``VerifierAgent`` over the real MAF
``Agent``, with the model replaced by a script. Nothing here touches a network, a
credential or a quota, which is what makes the whole verification story testable
before an Azure subscription exists.

Skipped when ``agent-framework`` is absent, because the core of this project stays
runnable on a bare interpreter.
"""

from __future__ import annotations

import asyncio
import unittest

from pitchframe.agents.pipeline import run_pipeline
from pitchframe.schema import MatchEvent, Player

try:
    import agent_framework as af

    from pitchframe.agents.narrator import NarratorAgent
    from pitchframe.agents.verifier import VerifierAgent
    from pitchframe.llm.offline import ScriptedChatClient

    HAS_AGENT_FRAMEWORK = True
except ImportError:  # pragma: no cover - depends on the optional extra
    HAS_AGENT_FRAMEWORK = False

requires_framework = unittest.skipUnless(
    HAS_AGENT_FRAMEWORK,
    "agent-framework is an optional extra; install requirements.txt to run these",
)

_HOME = Player(id="H1", name="Alaro", team="home", position="CM", shirt=8)
_AWAY = Player(id="A1", name="Brenvik", team="away", position="CM", shirt=8)

#: Momentum falls from 3.0 to 0.0 across the two windows.
EVENTS = (
    MatchEvent("e1", "T", 10, "shot", "home", _HOME),
    MatchEvent("e2", "T", 110, "shot", "home", _HOME),
    MatchEvent("e3", "T", 120, "shot", "away", _AWAY),
    # A trailing event past the window's end, so the window counts as closed.
    MatchEvent("e4", "T", 300, "pass", "home", _HOME, outcome="success"),
)
WINDOW = (100, 200)
BASELINE = (0, 100)

#: A claim the event stream will reject: momentum went down, not up.
INVERTED = """```json
[{"claim_id": "C1", "metric": "momentum", "team": null, "window": [100, 200],
  "direction": "up", "magnitude": 3.0, "text": "momentum swung their way"}]
```"""

#: The same claim after the Verifier's feedback.
CORRECTED = """Here is the corrected claim:
[{"claim_id": "C1r", "metric": "momentum", "team": null, "window": [100, 200],
  "direction": "down", "magnitude": 3.0, "text": "momentum swung away from them"}]
"""

ASSESSMENT = """{"explanation": "momentum fell from 3.0 to 0.0, so it moved down.",
 "hint": "restate the direction as down"}"""


def build_narrator(*replies: str) -> NarratorAgent:
    return NarratorAgent(af.Agent(client=ScriptedChatClient(list(replies)), name="narrator"))


def build_verifier(*replies: str) -> VerifierAgent:
    return VerifierAgent(af.Agent(client=ScriptedChatClient(list(replies)), name="verifier"))


@requires_framework
class TestNarratorReadsModelOutput(unittest.TestCase):
    def _propose(self, *replies: str):
        narrator = build_narrator(*replies)
        from pitchframe.agents.pipeline import snapshot

        snap = snapshot(EVENTS, WINDOW, BASELINE)
        return asyncio.run(narrator.propose(snap))

    def test_reads_a_fenced_json_array(self) -> None:
        claims = self._propose(INVERTED)
        self.assertEqual([c.claim_id for c in claims], ["C1"])

    def test_reads_bare_json(self) -> None:
        claims = self._propose(
            '[{"claim_id": "X", "metric": "momentum", "team": null, '
            '"window": [100, 200], "direction": "down", "magnitude": 1.0}]'
        )
        self.assertEqual([c.claim_id for c in claims], ["X"])

    def test_reads_json_surrounded_by_prose(self) -> None:
        claims = self._propose(f"Sure, here you go.\n{CORRECTED}\nHope that helps!")
        self.assertEqual([c.claim_id for c in claims], ["C1r"])

    def test_keeps_good_claims_when_a_sibling_is_malformed(self) -> None:
        """One broken claim must not discard the ones that parsed."""
        claims = self._propose(
            '[{"claim_id": "good", "metric": "momentum", "team": null, '
            '"window": [100, 200], "direction": "down", "magnitude": 1.0},'
            '{"claim_id": "bad", "metric": "momentum", "direction": "sideways"}]'
        )
        self.assertEqual([c.claim_id for c in claims], ["good"])

    def test_unreadable_reply_is_retried_once_with_the_schema(self) -> None:
        claims = self._propose("I am afraid I cannot do that.", CORRECTED)
        self.assertEqual([c.claim_id for c in claims], ["C1r"])

    def test_two_unreadable_replies_yield_nothing(self) -> None:
        """The retry is bounded; a model that never complies is given up on."""
        self.assertEqual(self._propose("no", "still no"), [])

    def test_protocol_failures_are_recorded(self) -> None:
        narrator = build_narrator("not json at all", "still not json")
        from pitchframe.agents.pipeline import snapshot

        asyncio.run(narrator.propose(snapshot(EVENTS, WINDOW, BASELINE)))
        self.assertTrue(narrator.parse_errors)


@requires_framework
class TestVerifierExplains(unittest.TestCase):
    def test_reads_explanation_and_hint(self) -> None:
        from pitchframe.claims import verify_claim

        claim = build_narrator(INVERTED)._parse(INVERTED)[0]
        verdict = verify_claim(claim, EVENTS)
        assessment = asyncio.run(build_verifier(ASSESSMENT).assess(claim, verdict))
        self.assertIn("momentum fell", assessment.explanation)
        self.assertIn("down", assessment.hint)

    def test_assessment_has_no_status_field(self) -> None:
        """The structural guarantee: a verifier reply cannot overturn a verdict."""
        from pitchframe.agents.pipeline import Assessment

        self.assertNotIn("status", Assessment.__dataclass_fields__)
        self.assertNotIn("verdict", Assessment.__dataclass_fields__)

    def test_unreadable_reply_falls_back_to_the_measured_reason(self) -> None:
        from pitchframe.claims import verify_claim

        claim = build_narrator(INVERTED)._parse(INVERTED)[0]
        verdict = verify_claim(claim, EVENTS)
        assessment = asyncio.run(build_verifier("uh, I guess so?").assess(claim, verdict))
        self.assertTrue(assessment.explanation)

    def test_verifier_outage_falls_back_to_the_rejection_reason(self) -> None:
        """A dead verifier degrades the explanation, never the verdict."""
        from pitchframe.claims import verify_claim

        claim = build_narrator(INVERTED)._parse(INVERTED)[0]
        verdict = verify_claim(claim, EVENTS)
        broken = VerifierAgent(
            af.Agent(client=ScriptedChatClient.failing(RuntimeError("offline")))
        )
        assessment = asyncio.run(broken.assess(claim, verdict))
        self.assertTrue(assessment.explanation)
        self.assertEqual(assessment.hint, verdict.reason)


@requires_framework
class TestFullPipelineOffline(unittest.TestCase):
    """Both real agents, the real loop, no network anywhere."""

    def test_inverted_claim_is_caught_and_corrected(self) -> None:
        narrator = build_narrator(INVERTED, CORRECTED)
        verifier = build_verifier(ASSESSMENT)
        result = asyncio.run(
            run_pipeline(narrator, verifier, EVENTS, WINDOW, BASELINE)
        )

        self.assertEqual(result.summary()["rejected"], 1)
        self.assertEqual(result.summary()["corrections"], 1)
        self.assertEqual([c.claim_id for c in result.verified], ["C1r"])

    def test_the_original_bad_claim_is_never_published(self) -> None:
        narrator = build_narrator(INVERTED, CORRECTED)
        result = asyncio.run(
            run_pipeline(narrator, build_verifier(ASSESSMENT), EVENTS, WINDOW, BASELINE)
        )
        self.assertNotIn("C1", [c.claim_id for c in result.verified])

    def test_a_narrator_that_will_not_correct_drops_the_claim(self) -> None:
        narrator = build_narrator(INVERTED, "null")
        result = asyncio.run(
            run_pipeline(narrator, build_verifier(ASSESSMENT), EVENTS, WINDOW, BASELINE)
        )
        self.assertEqual(result.verified, [])
        self.assertEqual(result.dropped, 1)

    def test_fault_injection_inverts_the_direction(self) -> None:
        """The canonical demo fault: a claim whose direction is deliberately backwards."""
        from pitchframe.claims import verify_claim

        inverted = build_narrator(INVERTED)._parse(INVERTED)[0]
        verdict = verify_claim(inverted, EVENTS)
        self.assertFalse(verdict.is_verified)
        self.assertEqual(verdict.claimed_direction, "up")
        self.assertEqual(verdict.measured_direction, "down")


if __name__ == "__main__":
    unittest.main()
