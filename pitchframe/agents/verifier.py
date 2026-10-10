"""The Verifier agent: explains a rejection, and says what to do instead.

The single most important thing about this class is what it *cannot* do. It cannot
verify a claim, and it cannot un-reject one.

That is enforced structurally rather than by instruction. The ground truth comes
from :func:`~pitchframe.claims.verify_claim`, which is pure arithmetic over the
event stream. The object this agent returns, :class:`~pitchframe.agents.pipeline.Assessment`,
has no status field at all — there is no shape of reply the model could produce
that would overturn the verdict, because the verdict is not among the things it is
asked to supply. A prompt instruction saying "do not overrule the measurement" can
be talked around; a return type that omits the field cannot.

So what is the model doing here, if not deciding? Two things a comparison operator
is genuinely bad at:

* explaining *why* a claim was wrong, in language the retry prompt can act on;
* recognising when a claim is wrong in a way that cannot be repaired — a metric that
  does not exist, a window with no baseline — and saying so instead of suggesting a
  correction that will fail again.

Both feed the Narrator's single retry, which is where the value shows up: the
correction rate is a real number, and nothing in it was decided by a model.
"""

from __future__ import annotations

import agent_framework as af

from ..claims import Claim, Verdict, extract_json_payloads
from .pipeline import Assessment

__all__ = ["VERIFIER_INSTRUCTIONS", "VerifierAgent"]


VERIFIER_INSTRUCTIONS = """You are the Verifier for a live football match.

A claim has already been checked against the raw event stream by exact arithmetic \
and has been REJECTED. That verdict is final. It is not yours to make, review, or \
appeal, and you must not restate the numbers as though you had computed them.

Your job is to explain the rejection and guide a correction.

Reply with a single JSON object and nothing else:
{
  "explanation": "one or two sentences, addressed to the narrator, saying why this \
claim contradicts the measurement",
  "hint": "a short, concrete instruction for the corrected claim, or an empty \
string if the claim cannot be repaired"
}

Rules:
- Be specific about the direction that was measured. Vague reassurance is useless.
- If the metric does not exist, or the window has no baseline, or the window has \
not closed yet, say plainly that the claim cannot be repaired and leave "hint" empty. \
Do not invent a nearby metric that the narrator could claim instead.
- Never propose a new number. You are describing a discrepancy, not producing one.
- Do not be apologetic or verbose. The narrator is an agent, not a person."""


class VerifierAgent:
    """Explains rejections. Cannot produce or overturn a verdict.

    Args:
        agent: a configured ``agent_framework.Agent``, injected so tests can drive
            this with the scripted client.
    """

    def __init__(self, agent: af.Agent, *, instructions: str | None = None) -> None:
        self._agent = agent
        self.instructions = instructions or VERIFIER_INSTRUCTIONS
        #: Protocol failures seen while reading replies, for the trace.
        self.parse_errors: list[str] = []
        self.calls = 0

    async def assess(self, claim: Claim, verdict: Verdict) -> Assessment:
        """Explain a rejection, falling back to the measured reason.

        The fallback matters more than it looks. If the Verifier is unavailable, the
        loop must still be able to retry the claim with something useful, and the
        rejection reason is already a precise statement of the discrepancy. Failing
        open to a worse explanation is fine here; failing *closed* — dropping a
        recoverable claim because a second model call broke — would not be.
        """
        measure = verdict.discrepancy()
        prompt = (
            f"{self.instructions}\n\n"
            f"Claim: {claim.describe()}\n"
            f"The narrator's wording: {claim.text or '(none)'}\n"
            f"Claimed direction: {claim.direction}\n"
            f"Measured: {measure}\n"
            f"Recorded reason for rejection: {verdict.reason}\n"
        )

        self.calls += 1
        try:
            response = await self._agent.run(prompt)
        except Exception as exc:  # noqa: BLE001 - the loop retries with the raw reason
            self.parse_errors.append(f"verifier call failed: {type(exc).__name__}: {exc}")
            return Assessment(
                claim_id=claim.claim_id,
                explanation=f"verifier unavailable ({type(exc).__name__})",
                hint=verdict.reason,
            )

        explanation, hint = self._read(response.text or "")
        return Assessment(
            claim_id=claim.claim_id,
            explanation=explanation or f"rejected: {verdict.reason}",
            hint=hint or measure,
        )

    def _read(self, text: str) -> tuple[str, str]:
        """Pull ``explanation`` and ``hint`` out of the reply, tolerantly."""
        for payload in extract_json_payloads(text):
            if isinstance(payload, dict):
                explanation = str(payload.get("explanation", "")).strip()
                hint = str(payload.get("hint", "")).strip()
                if explanation or hint:
                    return explanation, hint
        # Unreadable, but not useless: the prose still beats an empty explanation.
        stripped = text.strip()
        if stripped:
            self.parse_errors.append("verifier reply was not JSON; used raw text")
            return stripped[:400], ""
        return "", ""
