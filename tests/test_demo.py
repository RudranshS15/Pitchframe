"""Tests for the fault-injecting demo agents and the CLI they drive.

The demo is the artefact most likely to be taken at face value, so its honesty is
tested rather than assumed. Two properties matter:

* the injected fault is *actually* injected — a demo that claims to invert a claim
  and silently does not would show a rejection rate of zero and look like a broken
  pipeline;
* the fault is *labelled* — an injected failure presented as a natural one is the
  one thing that would make every other claim in this project unfalsifiable.

The CLI is exercised in a subprocess, because it is the interface a judge will
actually run, and importing ``main`` would not catch a broken argv path.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from pathlib import Path

from pitchframe.__main__ import key_moments
from pitchframe.agents.demo import InjectingNarrator, RuleVerifier
from pitchframe.agents.pipeline import MetricReading, Snapshot, run_pipeline, snapshot
from pitchframe.claims import verify_claim
from pitchframe.generator import generate_match
from pitchframe.schema import MatchEvent, Player

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_HOME = Player(id="H1", name="Alaro", team="home", position="CM", shirt=8)
_AWAY = Player(id="A1", name="Brenvik", team="away", position="CM", shirt=8)

EVENTS = (
    MatchEvent("e1", "T", 10, "shot", "home", _HOME),
    MatchEvent("e2", "T", 110, "shot", "home", _HOME),
    MatchEvent("e3", "T", 120, "shot", "away", _AWAY),
    MatchEvent("e4", "T", 300, "pass", "home", _HOME, outcome="success"),
)
WINDOW = (100, 200)
BASELINE = (0, 100)


def run_demo(fault: str = "narrator-invert", events=EVENTS):
    narrator = InjectingNarrator(fault=fault)
    verifier = RuleVerifier()
    result = asyncio.run(
        run_pipeline(narrator, verifier, events, WINDOW, BASELINE)
    )
    return narrator, verifier, result


class TestFaultInjection(unittest.TestCase):
    def test_a_fault_is_actually_injected(self) -> None:
        narrator, _, _ = run_demo()
        self.assertEqual(len(narrator.injected), 1)

    def test_the_injected_claim_really_is_wrong(self) -> None:
        """If it were not, the demo would prove nothing about verification."""
        _, _, result = run_demo()
        original = next(c for c in result.claims if not c.claim_id.endswith("r"))
        self.assertFalse(verify_claim(original, EVENTS).is_verified)

    def test_injecting_nothing_produces_no_rejections(self) -> None:
        """The honest baseline: with the fault off, nothing is rejected."""
        narrator, _, result = run_demo(fault="none")
        self.assertEqual(narrator.injected, set())
        self.assertEqual(result.summary()["rejected"], 0)


class TestWhichClaimGetsInverted(unittest.TestCase):
    """The target, when a caller has one.

    Asserted against a controlled snapshot rather than a generated match. The planted
    metric used to be "whatever moved most in this window", so a test that wanted one
    particular metric had to hope for it — and broke the day the generator's stream
    changed shape, having asserted nothing about the narrator at all.
    """

    def _snapshot(self, *readings: MetricReading) -> Snapshot:
        return Snapshot(window=(100, 200), baseline=(0, 100), readings=tuple(readings))

    @staticmethod
    def _reading(metric: str, *, moved: float, from_: float) -> MetricReading:
        """A reading whose relative movement ranks as ``moved`` against ``from_``."""
        return MetricReading(metric, None, value=from_ + moved, baseline_value=from_)

    def _propose(self, narrator: InjectingNarrator, snap: Snapshot):
        return asyncio.run(narrator.propose(snap))

    def test_the_strongest_reading_is_the_default_target(self) -> None:
        snap = self._snapshot(
            self._reading("momentum", moved=80.0, from_=10.0),
            self._reading("chaos_index", moved=0.3, from_=0.5),
        )
        narrator = InjectingNarrator(fault="narrator-invert")
        claims = self._propose(narrator, snap)
        self.assertEqual(narrator.injected, {"momentum"})
        self.assertEqual(
            {c.metric: c.direction for c in claims},
            {"momentum": "down", "chaos_index": "up"},
        )

    def test_a_pinned_target_wins_over_strength(self) -> None:
        """Asking for a metric has to produce that metric, not whichever happened to
        move most — otherwise the caller's request is a suggestion."""
        snap = self._snapshot(
            self._reading("momentum", moved=80.0, from_=10.0),
            self._reading("chaos_index", moved=0.3, from_=0.5),
        )
        narrator = InjectingNarrator(fault="narrator-invert", target="chaos_index")
        claims = self._propose(narrator, snap)
        self.assertEqual(narrator.injected, {"chaos_index"})
        self.assertEqual(
            {c.metric: c.direction for c in claims},
            # The weaker one flipped; the strongest was left alone.
            {"momentum": "up", "chaos_index": "down"},
        )

    def test_a_pinned_target_displaces_the_weakest_claim_when_it_would_not_be_made(self) -> None:
        """Otherwise a request for a metric outside the strongest few plants nothing
        at all, while the demo reports a planted fault anyway."""
        readings = [
            self._reading(f"metric_{index}", moved=100.0 - index, from_=10.0)
            for index in range(4)
        ]
        readings.append(self._reading("chaos_index", moved=0.3, from_=0.5))
        narrator = InjectingNarrator(fault="narrator-invert", target="chaos_index")
        claims = self._propose(narrator, self._snapshot(*readings))

        self.assertEqual(len(claims), narrator.max_claims, "the line count must not grow")
        self.assertEqual(narrator.injected, {"chaos_index"})
        metrics = {c.metric for c in claims}
        self.assertIn("chaos_index", metrics)
        # The weakest of the four strongest gave up its place to it.
        self.assertEqual(metrics, {"metric_0", "metric_1", "metric_2", "chaos_index"})

    def test_a_flat_metric_is_never_inverted_even_when_pinned(self) -> None:
        """A reading that did not move has no direction to invert."""
        snap = self._snapshot(
            self._reading("momentum", moved=80.0, from_=10.0),
            self._reading("chaos_index", moved=0.0, from_=0.5),
        )
        narrator = InjectingNarrator(fault="narrator-invert", target="chaos_index")
        claims = self._propose(narrator, snap)
        self.assertEqual(narrator.injected, set())
        self.assertEqual({c.metric: c.direction for c in claims}, {"momentum": "up"})

    def test_injecting_a_fault_produces_exactly_one_rejection(self) -> None:
        _, _, result = run_demo()
        self.assertEqual(result.summary()["rejected"], 1)

    def test_the_injected_claim_is_corrected_by_measurement(self) -> None:
        _, _, result = run_demo()
        self.assertEqual(result.summary()["corrections"], 1)
        # This fixture is deliberately tiny, so momentum is the only metric that
        # actually moves: one proposal, one rejection, one correction. Asserting on
        # the corrected claim's identity says more than a bare count would.
        self.assertEqual([c.claim_id for c in result.verified], ["W100-1r"])

    def test_nothing_wrong_is_ever_published(self) -> None:
        """The invariant that makes the demo worth showing."""
        _, _, result = run_demo()
        for claim in result.verified:
            self.assertTrue(verify_claim(claim, EVENTS).is_verified)

    def test_the_target_reading_can_be_chosen(self) -> None:
        narrator, _, _ = run_demo()
        self.assertTrue(narrator.injected.issubset({"momentum"}))

    def test_flat_readings_are_not_claimed_about(self) -> None:
        """A claim that nothing changed would always verify, and mean nothing."""
        flat = (
            MatchEvent("e1", "T", 10, "shot", "home", _HOME),
            MatchEvent("e2", "T", 110, "shot", "home", _HOME),
            MatchEvent("e4", "T", 300, "pass", "home", _HOME, outcome="success"),
        )
        narrator, _, result = run_demo(events=flat)
        for claim in result.claims:
            self.assertNotEqual(claim.metric, "chaos_index")


class TestKeyMoments(unittest.TestCase):
    """The report's "key moments" list, and the order it is in.

    This ranking is what decides whether a match summary looks right at a glance. It
    used to multiply the event's weight by its own pressure, so a tap-in scored at
    0.2 pressure ranked below a shot saved at 0.83 — and a decided match could print
    five shots and no goal.
    """

    @staticmethod
    def _event(kind: str, clock: int, pressure: float) -> MatchEvent:
        player = _HOME if kind == "goal" else _AWAY
        return MatchEvent(
            f"{kind}{clock}", "T", clock, kind, player.team, player,
            outcome="success", pressure=pressure,
        )

    def test_a_goal_outranks_a_shot_whatever_the_pressure(self) -> None:
        events = (self._event("goal", 100, 0.20), self._event("shot", 110, 0.83))
        self.assertEqual(key_moments(events, 2)[0].type, "goal")

    def test_pressure_still_orders_events_within_a_class(self) -> None:
        events = (self._event("shot", 110, 0.40), self._event("shot", 120, 0.80))
        self.assertEqual([e.clock for e in key_moments(events, 2)], [120, 110])

    def test_a_match_never_loses_its_goals_to_the_limit(self) -> None:
        """Checked on generated matches, where it was actually going wrong."""
        for seed in (7, 12, 42, 99):
            with self.subTest(seed=seed):
                events = generate_match(seed).events
                goals = sum(1 for e in events if e.type == "goal")
                moments = key_moments(events, 5)
                self.assertEqual(
                    sum(1 for e in moments if e.type == "goal"),
                    min(goals, 5),
                    "every goal should appear ahead of any non-goal",
                )

    def test_ties_fall_back_to_clock_order(self) -> None:
        events = (self._event("shot", 120, 0.5), self._event("shot", 110, 0.5))
        self.assertEqual([e.clock for e in key_moments(events, 2)], [110, 120])


class TestRevisionPolicy(unittest.TestCase):
    def test_revision_uses_the_measured_direction(self) -> None:
        narrator = InjectingNarrator(fault="narrator-invert")
        snap = snapshot(EVENTS, WINDOW, BASELINE)
        claim = asyncio.run(narrator.propose(snap))[0]
        verdict = verify_claim(claim, EVENTS)
        revised = asyncio.run(
            narrator.revise(claim, verdict, asyncio.run(RuleVerifier().assess(claim, verdict)))
        )
        assert revised is not None
        self.assertEqual(revised.direction, verdict.measured_direction)

    def test_no_revision_when_nothing_was_measured(self) -> None:
        """An unrepairable claim must be dropped, not given a plausible direction."""
        from pitchframe.claims import Claim, Verdict

        narrator = InjectingNarrator()
        claim = Claim("X", "momentum", None, WINDOW, "up", 1.0)
        structural = Verdict(
            claim_id="X",
            status="rejected",
            reason="unknown metric",
            claimed_direction="up",
            measured_direction=None,
        )
        assessment = asyncio.run(RuleVerifier().assess(claim, structural))
        self.assertIsNone(asyncio.run(narrator.revise(claim, structural, assessment)))


class TestRuleVerifier(unittest.TestCase):
    def test_explanation_names_the_measured_direction(self) -> None:
        narrator = InjectingNarrator()
        snap = snapshot(EVENTS, WINDOW, BASELINE)
        claim = asyncio.run(narrator.propose(snap))[0]
        verdict = verify_claim(claim, EVENTS)
        assessment = asyncio.run(RuleVerifier().assess(claim, verdict))
        self.assertIn(verdict.measured_direction or "", assessment.explanation)

    def test_unrepairable_claim_gets_no_hint(self) -> None:
        from pitchframe.claims import Claim, Verdict

        claim = Claim("X", "vibes", None, WINDOW, "up", 1.0)
        verdict = Verdict("X", "rejected", "unknown metric", "up", None, None, None, None)
        assessment = asyncio.run(RuleVerifier().assess(claim, verdict))
        self.assertEqual(assessment.hint, "")


class TestCli(unittest.TestCase):
    """The interface a judge runs, exercised as a real subprocess."""

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pitchframe", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=PROJECT_ROOT,
        )

    def test_providers_exits_cleanly(self) -> None:
        done = self._run("--providers")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Scripted (no model)", done.stdout)

    def test_providers_reports_the_honesty_banner(self) -> None:
        done = self._run("--providers")
        self.assertIn("banner", done.stdout)

    def test_claims_exits_cleanly(self) -> None:
        done = self._run("--claims", "--seed", "7")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_claims_labels_the_injected_fault(self) -> None:
        """Unlabelled, an injected fault would make the whole story unfalsifiable."""
        done = self._run("--claims", "--seed", "7")
        self.assertIn("FAULT INJECTED", done.stdout)

    def test_claims_shows_a_rejection_and_a_correction(self) -> None:
        done = self._run("--claims", "--seed", "7")
        self.assertIn("REJECTED", done.stdout)
        self.assertIn("corrected", done.stdout)

    def test_claims_with_no_fault_reports_no_rejection(self) -> None:
        done = self._run("--claims", "--seed", "7", "--inject-fault", "none")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("FAULT INJECTED", done.stdout)
        self.assertIn("rejected 0", done.stdout)

    def test_bad_window_is_a_clean_error_not_a_traceback(self) -> None:
        done = self._run("--claims", "--window", "0:60")
        self.assertEqual(done.returncode, 2)
        self.assertIn("no baseline", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_plain_summary_still_works(self) -> None:
        done = self._run("--seed", "7", "--moments", "2")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Northgate", done.stdout)


class TestCliOnGeneratedMatches(unittest.TestCase):
    """The demo must work on every match, not just the seed it was built against."""

    def test_fault_is_caught_across_many_seeds(self) -> None:
        for seed in (1, 7, 42, 99, 2026):
            with self.subTest(seed=seed):
                match = generate_match(seed)
                narrator = InjectingNarrator(fault="narrator-invert")
                result = asyncio.run(
                    run_pipeline(
                        narrator,
                        RuleVerifier(),
                        match.events,
                        (1200, 1500),
                        (900, 1200),
                    )
                )
                self.assertEqual(result.summary()["rejected"], 1, f"seed {seed}")
                self.assertEqual(result.summary()["corrections"], 1, f"seed {seed}")
                for claim in result.verified:
                    self.assertTrue(verify_claim(claim, match.events).is_verified)


if __name__ == "__main__":
    unittest.main()
