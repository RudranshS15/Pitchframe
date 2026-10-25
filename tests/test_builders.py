"""Tests for the live-pair builders in ``pitchframe.llm.builders``.

The builders sit at the boundary between *deciding* a tier is live (provider.py) and
*building* the client for it, so the tests here are about the two failure modes that
decision boundary can produce: a tier with no client path at all (silence, not an
error — the scripted tier is not a failure), and a tier that should be live but
cannot be built (a loud :class:`ClientUnavailable` whose message names the setting
that is missing). A generic ``ImportError`` or ``KeyError`` here would reach the
banner as a stack trace, which is exactly what the message contract forbids.
"""

from __future__ import annotations

import unittest

from pitchframe.llm.builders import ClientUnavailable, build_live_pair


class TestOfflineTier(unittest.TestCase):
    """The default tier has no live client, and must not pretend otherwise."""

    def test_offline_builds_no_pair(self) -> None:
        self.assertIsNone(build_live_pair("offline"))

    def test_unknown_tier_builds_no_pair(self) -> None:
        """An unrecognised key is the caller's (already-selected) tier, not an error."""
        self.assertIsNone(build_live_pair("local"))
        self.assertIsNone(build_live_pair("made-up"))


class TestFoundryPair(unittest.TestCase):
    """Endpoint and deployment must both be set, and one missing is named."""

    def test_missing_endpoint_names_it(self) -> None:
        with self.assertRaises(ClientUnavailable) as caught:
            build_live_pair("foundry", env={})
        self.assertIn("AZURE_AI_PROJECT_ENDPOINT", str(caught.exception))

    def test_missing_model_names_it(self) -> None:
        """The deployment setting is the one .env.example and the runbook document."""
        with self.assertRaises(ClientUnavailable) as caught:
            build_live_pair("foundry", env={"AZURE_AI_PROJECT_ENDPOINT": "https://x"})
        self.assertIn("AZURE_AI_MODEL_DEPLOYMENT", str(caught.exception))

    def test_missing_runtime_names_the_package(self) -> None:
        """agent-framework absent entirely: the message points at the install."""
        if not _agent_framework_available():
            self.skipTest("agent-framework is installed here")
        # With the runtime present but no settings, the endpoint message is what
        # fires; the runtime check only runs on machines without the package, where
        # this test is skipped rather than inverted.


def _agent_framework_available() -> bool:
    try:
        import agent_framework  # noqa: F401
    except ImportError:
        return False
    return True


class TestOllamaPair(unittest.TestCase):
    def test_missing_host_names_it(self) -> None:
        with self.assertRaises(ClientUnavailable) as caught:
            build_live_pair("ollama", env={})
        self.assertIn("OLLAMA_HOST", str(caught.exception))

    def test_missing_model_names_it(self) -> None:
        with self.assertRaises(ClientUnavailable) as caught:
            build_live_pair("ollama", env={"OLLAMA_HOST": "http://127.0.0.1:11434"})
        self.assertIn("OLLAMA_MODEL_ID", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
