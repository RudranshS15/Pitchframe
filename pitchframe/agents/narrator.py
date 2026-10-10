"""The Narrator agent: turns a measured snapshot into checkable claims.

The Narrator is deliberately not asked to do arithmetic. The snapshot it receives
already carries each metric's value, its baseline, and the direction between them,
so describing what happened is a reading task rather than a computation. That
matters for honesty: if the prompt withheld the direction and then the model got it
wrong, the resulting failure would say nothing about whether verification works —
we would have engineered the failure ourselves. The failure this system is built to
catch is the one that survives the information given, and inverted claims do.

Two protocol errors are handled here rather than in the loop, because both are the
model failing to *speak*, not failing to be *right*:

* output that is not JSON at all, or is JSON of the wrong shape;
* output that is JSON but missing keys or carrying an impossible direction.

Both are retried once with the schema restated. A reply that fails twice is
abandoned, and the loop records that separately from a rejected claim — "the model
was wrong" and "the model was unintelligible" need different fixes.

This module requires ``agent-framework``. The loop it plugs into does not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import agent_framework as af

from ..claims import (
    CLAIM_SCHEMA_HINT,
    Claim,
    ClaimError,
    Verdict,
    extract_json_payloads,
    parse_claim,
)
from .pipeline import Assessment, Snapshot, render_snapshot

__all__ = ["NARRATOR_INSTRUCTIONS", "NarratorAgent"]


NARRATOR_INSTRUCTIONS = """You are the Narrator for a live football match.

You describe what the statistics say. You do not compute them, you do not invent \
metrics, and you do not embellish.

Rules, in order of importance:
1. Read the direction for each metric from the table you are given. It is already \
computed. Copy it; never invert it and never reason about it yourself.
2. Report only metrics that appear in the table. Never name a metric that is not \
there, and never invent a number.
3. Prefer three to five claims that would interest a viewer: a shift in who is in \
control, a change in tempo, a side suddenly passing better or worse under pressure.
4. Skip any metric marked "flat". A claim that nothing changed is not worth making.
5. The window you are given is the window you are about to discuss. Use it exactly.
6. Write the "text" field for a casual fan. No jargon beyond pressure, tempo and \
momentum.

Answer with a single JSON array of claim objects and nothing else:
""" + CLAIM_SCHEMA_HINT


_SCHEMA_REMINDER = """

Your previous reply could not be read. Reply with ONLY a JSON array of claim \
objects matching this shape, with no prose, no code fences and no commentary:
""" + CLAIM_SCHEMA_HINT


class NarratorAgent:
    """A claim-producing agent.

    Args:
        agent: a configured ``agent_framework.Agent``. Injected rather than built
            here so tests can drive this class with the scripted client and the
            pipeline can be run without a model at all.
        instructions: overrides :data:`NARRATOR_INSTRUCTIONS`.
    """

    def __init__(self, agent: af.Agent, *, instructions: str | None = None) -> None:
        self._agent = agent
        self.instructions = instructions or NARRATOR_INSTRUCTIONS
        #: Protocol failures seen while reading replies, for the trace.
        self.parse_errors: list[str] = []
        #: How many model calls this agent has made.
        self.calls = 0

    # --- the Narrator protocol ----------------------------------------------

    async def propose(self, snap: Snapshot) -> Sequence[Claim]:
        """Ask for claims about a window, retrying once on unreadable output."""
        prompt = self._proposal_prompt(snap)
        claims = self._parse(await self._ask(prompt))
        if claims:
            return claims

        # The reply was unreadable rather than wrong. Restate the schema once,
        # because models recover from a format reminder far more often than they
        # recover from being told their arithmetic was bad.
        self.parse_errors.append("proposal was unreadable; retried with the schema restated")
        return self._parse(await self._ask(prompt + _SCHEMA_REMINDER))

    async def revise(
        self, claim: Claim, verdict: Verdict, assessment: Assessment
    ) -> Claim | None:
        """Ask for a corrected claim, with the measured truth attached.

        Returns ``None`` when the model produces nothing usable, which the loop
        records as the claim being abandoned rather than kept.
        """
        prompt = self._revision_prompt(claim, verdict, assessment)
        revised = self._parse(await self._ask(prompt))
        return revised[0] if revised else None

    # --- internals ----------------------------------------------------------

    async def _ask(self, prompt: str) -> str:
        self.calls += 1
        response = await self._agent.run(prompt)
        return response.text or ""

    def _parse(self, text: str) -> list[Claim]:
        """Read claims out of a reply, keeping whatever is usable.

        One malformed claim does not discard its well-formed siblings: a reply with
        four good claims and one missing key should publish four.
        """
        claims: list[Claim] = []
        for payload in extract_json_payloads(text):
            items: Sequence[Any] = payload if isinstance(payload, list) else [payload]
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                try:
                    claims.append(parse_claim(item))
                except ClaimError as exc:
                    self.parse_errors.append(str(exc))
        return claims

    def _proposal_prompt(self, snap: Snapshot) -> str:
        return (
            f"{self.instructions}\n\n"
            f"Here is the measured state of play:\n\n{render_snapshot(snap)}\n\n"
            "Propose three to five claims about this window."
        )

    def _revision_prompt(
        self, claim: Claim, verdict: Verdict, assessment: Assessment
    ) -> str:
        start, end = claim.window
        return (
            f"{self.instructions}\n\n"
            f"Your claim was checked against the event stream and rejected.\n\n"
            f"Claim: {claim.describe()}\n"
            f"What you said: {claim.text or '(no text)'}\n"
            f"Measured: {verdict.discrepancy()}\n"
            f"Why: {verdict.reason}\n"
            f"Verifier's note: {assessment.explanation}\n"
            f"Guidance: {assessment.hint or 'use the direction measured above'}\n\n"
            f"Reply with exactly one corrected claim object for the window "
            f"[{start}, {end}], or the single word null if you cannot make an honest "
            f"claim about it. Use only the measured direction.\n\n{CLAIM_SCHEMA_HINT}"
        )
