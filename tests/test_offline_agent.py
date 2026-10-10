"""Tests that the agent pipeline runs with no network, no Azure and no quota.

These are the tests that keep the project unblocked. A new Foundry subscription
starts at zero TPM and a quota increase takes days, so if the pipeline could only
be exercised against a live endpoint, the schedule would sit idle waiting on a
support ticket. Against the scripted client it can be developed and tested today.

They are also the tests that keep the *no-Azure path* honest. The rules require the
project to be runnable free of charge and without restriction, and a judge cannot
authenticate to somebody else's subscription. If these pass on a bare machine, the
repository is genuinely testable by anyone who clones it.

The whole module is skipped when ``agent-framework`` is absent, because the core of
this project is standard library only and must stay runnable without it.
"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Sequence

from pitchframe.llm.provider import TIERS, available_tiers, select_tier

try:
    import agent_framework as af

    from pitchframe.llm.offline import (
        ScriptExhausted,
        ScriptedChatClient,
    )

    HAS_AGENT_FRAMEWORK = True
except ImportError:  # pragma: no cover - depends on the optional extra
    HAS_AGENT_FRAMEWORK = False

requires_framework = unittest.skipUnless(
    HAS_AGENT_FRAMEWORK,
    "agent-framework is an optional extra; install requirements.txt to run these",
)


@requires_framework
class TestScriptedClient(unittest.TestCase):
    """The client on its own, without an agent around it."""

    def _ask(self, client: ScriptedChatClient, text: str) -> str:
        """Drive one turn through the real MAF entry point."""
        response = asyncio.run(client.get_response([af.Message("user", [text])]))
        assert isinstance(response, af.ChatResponse)
        return response.text

    def test_replies_in_script_order(self) -> None:
        client = ScriptedChatClient(["first", "second"])
        self.assertEqual(self._ask(client, "a"), "first")
        self.assertEqual(self._ask(client, "b"), "second")

    def test_records_the_prompts_it_was_given(self) -> None:
        """Tests assert on what the agent *asked*, not only what it answered."""
        client = ScriptedChatClient(["ok"])
        self._ask(client, "what is the pressure index")
        self.assertEqual(client.call_count, 1)
        self.assertIn("pressure index", client.prompts[0])

    def test_always_repeats_forever(self) -> None:
        client = ScriptedChatClient.always("steady")
        for _ in range(5):
            self.assertEqual(self._ask(client, "x"), "steady")
        self.assertEqual(client.remaining, 0)

    def test_exhaustion_is_loud_rather_than_plausible(self) -> None:
        """A silent fallback reply would let a test pass while testing nothing."""
        client = ScriptedChatClient(["only-one"])
        self._ask(client, "a")
        with self.assertRaises(ScriptExhausted):
            self._ask(client, "b")

    def test_exhaustion_message_is_actionable(self) -> None:
        client = ScriptedChatClient(["only-one"], script_name="narrator")
        self._ask(client, "a")
        with self.assertRaises(ScriptExhausted) as caught:
            self._ask(client, "b")
        self.assertIn("narrator", str(caught.exception))

    def test_raised_exception_is_a_simulated_provider_failure(self) -> None:
        """Fault injection: reach the fallback path without breaking anything real."""
        client = ScriptedChatClient.failing(ConnectionError("foundry unreachable"))
        with self.assertRaises(ConnectionError):
            self._ask(client, "a")

    def test_failures_interleave_with_successes(self) -> None:
        """The retry path is a sequence: fail, fail, succeed."""
        client = ScriptedChatClient(
            [ConnectionError("boom"), ConnectionError("boom again"), "recovered"]
        )
        with self.assertRaises(ConnectionError):
            self._ask(client, "a")
        with self.assertRaises(ConnectionError):
            self._ask(client, "b")
        self.assertEqual(self._ask(client, "c"), "recovered")

    def test_callable_step_sees_the_prompt(self) -> None:
        """Lets a reply depend on what was asked, which is how claims get echoed."""
        seen: list[str] = []

        def echo(messages: Sequence[af.Message]) -> str:
            text = "\n".join(getattr(m, "text", "") or "" for m in messages)
            seen.append(text)
            return f"ack: {text.splitlines()[-1]}"

        client = ScriptedChatClient([echo])
        self.assertEqual(self._ask(client, "claim-42"), "ack: claim-42")
        self.assertEqual(seen, ["claim-42"])

    def test_describe_reports_progress(self) -> None:
        client = ScriptedChatClient(["a", "b"], script_name="verifier")
        self._ask(client, "x")
        described = client.describe()
        self.assertIn("verifier", described)
        self.assertIn("1 call", described)

    def test_streaming_is_refused_rather_than_faked(self) -> None:
        """Claiming to stream while replaying a fixed string would be a lie.

        ``get_response(stream=True)`` is lazy: it hands back a ``ResponseStream``
        straight away and only reaches the client when that stream is consumed. The
        refusal therefore has to be asserted on iteration, not on the call, or the
        test would pass while the check never ran.
        """
        client = ScriptedChatClient(["a"])
        stream = client.get_response([af.Message("user", ["x"])], stream=True)

        async def consume() -> None:
            async for _ in stream:
                pass

        with self.assertRaises(NotImplementedError):
            asyncio.run(consume())


@requires_framework
class TestAgentRunsOffline(unittest.IsolatedAsyncioTestCase):
    """The full MAF agent, assembled and run against the scripted client."""

    async def test_agent_returns_the_scripted_reply(self) -> None:
        agent = af.Agent(
            client=ScriptedChatClient(["pressure index up 0.23"]),
            instructions="You are the Narrator.",
            name="narrator",
        )
        response = await agent.run("describe the last window")
        self.assertEqual(response.text, "pressure index up 0.23")

    async def test_agent_with_tools_does_not_degrade(self) -> None:
        """Without FunctionInvocationLayer MAF warns and runs the agent crippled.

        A crippled client would make every offline test unrepresentative of the
        real pipeline, which is worse than having no offline tests at all.
        """

        def get_pressure(team: str) -> str:
            """Return the pressure index for a team."""
            return "0.41"

        tool = af.FunctionTool(
            name="get_pressure", description="Get pressure.", func=get_pressure
        )
        client = ScriptedChatClient(["0.41"])
        self.assertIsInstance(client, af.FunctionInvocationLayer)

        with self.assertNoLogs("agent_framework._agents", level="WARNING"):
            agent = af.Agent(
                client=client,
                instructions="You are the Narrator.",
                name="narrator",
                tools=[tool],
            )
            response = await agent.run("what is the pressure")

        self.assertEqual(response.text, "0.41")

    async def test_two_agents_keep_separate_call_records(self) -> None:
        """Narrator and Verifier must not share state through the client."""
        narrator_client = ScriptedChatClient(["claim"], script_name="narrator")
        verifier_client = ScriptedChatClient(["verdict"], script_name="verifier")

        narrator = af.Agent(client=narrator_client, instructions="n", name="narrator")
        verifier = af.Agent(client=verifier_client, instructions="v", name="verifier")

        self.assertEqual((await narrator.run("a")).text, "claim")
        self.assertEqual((await verifier.run("b")).text, "verdict")

        self.assertEqual(narrator_client.call_count, 1)
        self.assertEqual(verifier_client.call_count, 1)

    async def test_prompt_survives_the_round_trip(self) -> None:
        """What the agent sends is observable, so prompts can be asserted on."""
        client = ScriptedChatClient(["ok"])
        agent = af.Agent(client=client, instructions="You are the Narrator.", name="n")
        await agent.run("window 10-20 momentum")
        self.assertTrue(any("window 10-20 momentum" in p for p in client.prompts))


class TestTierSelectionOnThisMachine(unittest.TestCase):
    """Tier selection itself is stdlib-only, so this runs anywhere."""

    def test_selection_always_succeeds(self) -> None:
        """Whatever this machine has, a tier must resolve."""
        self.assertIn(select_tier().key, {tier.key for tier in TIERS})

    def test_offline_is_reachable_even_unconfigured(self) -> None:
        self.assertIn("offline", available_tiers(env={}, find_module=lambda _: False))


if __name__ == "__main__":
    unittest.main()
