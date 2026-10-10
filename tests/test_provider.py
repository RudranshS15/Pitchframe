"""Tests for provider tier selection.

Two things are being protected here, and the second matters more than the first.

The obvious one is ordering: the best available tier should win, and a configured
Foundry project should beat a local model.

The important one is **honesty**. The system can silently run on scripted text
when no model is reachable, and a viewer must never be able to mistake that for an
inference. So the tests below assert on the *reason* and the banner, not just the
chosen key — a selection that quietly degraded would be a bug even though every
key still resolved.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable

from pitchframe.llm.provider import (
    DEFAULT_PREFERENCE,
    TIERS,
    available_tiers,
    describe_tiers,
    select_tier,
)


def probe(*importable: str) -> Callable[[str], bool]:
    """A fake module probe that reports exactly ``importable`` as present."""
    present = set(importable)
    return lambda name: name in present


#: A machine with the full framework and Azure configured.
_FULL_STACK = ("agent_framework", "azure.identity", "ollama", "foundry_local_sdk")


class TestAvailableTiers(unittest.TestCase):
    def test_bare_machine_only_offers_offline(self) -> None:
        found = available_tiers(env={}, find_module=probe())
        self.assertEqual(found, ("offline",))

    def test_foundry_needs_endpoint_and_credentials(self) -> None:
        env = {"AZURE_AI_PROJECT_ENDPOINT": "https://example.invalid/api/projects/p"}
        self.assertIn("foundry", available_tiers(env=env, find_module=probe(*_FULL_STACK)))

    def test_foundry_absent_without_endpoint(self) -> None:
        found = available_tiers(env={}, find_module=probe(*_FULL_STACK))
        self.assertNotIn("foundry", found)

    def test_foundry_absent_without_azure_identity(self) -> None:
        """A configured endpoint is not enough; the credential library must exist."""
        env = {"AZURE_AI_PROJECT_ENDPOINT": "https://example.invalid/api/projects/p"}
        found = available_tiers(env=env, find_module=probe("agent_framework", "ollama"))
        self.assertNotIn("foundry", found)

    def test_ollama_requires_a_configured_host(self) -> None:
        without = available_tiers(env={}, find_module=probe(*_FULL_STACK))
        self.assertNotIn("ollama", without)

        with_host = available_tiers(
            env={"OLLAMA_HOST": "http://127.0.0.1:11434"},
            find_module=probe(*_FULL_STACK),
        )
        self.assertIn("ollama", with_host)

    def test_offline_always_last_and_always_present(self) -> None:
        for env in ({}, {"AZURE_AI_PROJECT_ENDPOINT": "x"}, {"OLLAMA_HOST": "y"}):
            found = available_tiers(env=env, find_module=probe(*_FULL_STACK))
            self.assertEqual(found[-1], "offline", f"env={env}")

    def test_offline_wins_nothing_but_never_disappears(self) -> None:
        """Offline must never be dropped, or the last-resort guarantee breaks."""
        found = available_tiers(env={}, find_module=probe())
        self.assertIn("offline", found)


class TestSelectTier(unittest.TestCase):
    def test_prefers_foundry_when_fully_configured(self) -> None:
        selection = select_tier(
            env={"AZURE_AI_PROJECT_ENDPOINT": "https://example.invalid/api/projects/p"},
            find_module=probe(*_FULL_STACK),
        )
        self.assertEqual(selection.key, "foundry")
        self.assertFalse(selection.is_fallback)
        self.assertTrue(selection.uses_a_model)

    def test_falls_back_to_offline_and_says_so(self) -> None:
        selection = select_tier(env={}, find_module=probe())
        self.assertEqual(selection.key, "offline")
        self.assertTrue(selection.is_fallback)
        self.assertFalse(selection.uses_a_model)

    def test_fallback_reason_names_what_was_missing(self) -> None:
        """The reason is what the overlay shows, so it has to be specific."""
        selection = select_tier(env={}, find_module=probe())
        self.assertIn("Azure AI Foundry", selection.reason)

    def test_offline_banner_never_implies_a_model(self) -> None:
        selection = select_tier(env={}, find_module=probe())
        banner = selection.banner().lower()
        self.assertIn("no model", banner)

    def test_fallback_banner_announces_the_fallback(self) -> None:
        selection = select_tier(
            env={},
            find_module=probe("agent_framework", "ollama"),
            preference=("foundry", "offline"),
        )
        self.assertEqual(selection.key, "offline")
        self.assertIn("unavailable", selection.banner())

    def test_primary_tier_banner_does_not_claim_a_fallback(self) -> None:
        selection = select_tier(
            env={"AZURE_AI_PROJECT_ENDPOINT": "https://example.invalid/api/projects/p"},
            find_module=probe(*_FULL_STACK),
        )
        self.assertNotIn("unavailable", selection.banner())

    def test_local_and_ollama_reachable_through_preference(self) -> None:
        local = select_tier(
            env={"OLLAMA_HOST": "http://127.0.0.1:11434"},
            find_module=probe(*_FULL_STACK),
            preference=("local", "offline"),
        )
        self.assertEqual(local.key, "local")
        self.assertTrue(local.uses_a_model)

        ollama = select_tier(
            env={"OLLAMA_HOST": "http://127.0.0.1:11434"},
            find_module=probe(*_FULL_STACK),
            preference=("ollama", "offline"),
        )
        self.assertEqual(ollama.key, "ollama")

    def test_unknown_preference_keys_are_ignored_not_fatal(self) -> None:
        """A typo in a config file should degrade, not refuse to start."""
        selection = select_tier(
            preference=("nonsense", "offline"),
            env={},
            find_module=probe(),
        )
        self.assertEqual(selection.key, "offline")

    def test_preference_of_only_unknown_keys_is_an_error(self) -> None:
        with self.assertRaises(RuntimeError):
            select_tier(preference=("nonsense",), env={}, find_module=probe())

    def test_preference_can_pin_offline_deliberately(self) -> None:
        """An operator may want the demo to be reproducible on purpose."""
        selection = select_tier(
            preference=("offline",),
            env={"AZURE_AI_PROJECT_ENDPOINT": "https://example.invalid/x"},
            find_module=probe(*_FULL_STACK),
        )
        self.assertEqual(selection.key, "offline")

    def test_default_preference_covers_every_tier(self) -> None:
        known = {tier.key for tier in TIERS}
        self.assertEqual(set(DEFAULT_PREFERENCE), known)


class TestDescribeTiers(unittest.TestCase):
    def test_lists_every_tier(self) -> None:
        text = describe_tiers(env={}, find_module=probe())
        for tier in TIERS:
            self.assertIn(tier.label, text)

    def test_marks_readiness(self) -> None:
        text = describe_tiers(env={}, find_module=probe())
        self.assertIn("[ready  ]", text)
        self.assertIn("[missing]", text)


if __name__ == "__main__":
    unittest.main()
