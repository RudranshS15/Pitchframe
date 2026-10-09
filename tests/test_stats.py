"""Tests for the deterministic stats engine.

These functions are the Verifier's ground truth, so an error here becomes a
wrong verdict everywhere downstream. The fixtures below are hand-built event
lists whose answers can be checked by eye, rather than by re-running the
implementation.
"""

from __future__ import annotations

import itertools
import unittest

from pitchframe.schema import MatchEvent, Player
from pitchframe.stats import (
    METRICS,
    chaos_index,
    compute,
    in_window,
    longest_pressure_pass_streak,
    momentum,
    pass_accuracy,
    pass_difficulty,
    pass_difficulty_mean,
    peak_speed,
    possession_share,
    pressure_index,
)

_HOME_PLAYER = Player(id="H01", name="Alaro", team="home", position="CM", shirt=8)
_AWAY_PLAYER = Player(id="A01", name="Brenvik", team="away", position="CM", shirt=8)

_counter = itertools.count(1)


def make_event(
    kind: str,
    *,
    team: str = "home",
    clock: int = 0,
    outcome: str | None = None,
    speed: float | None = None,
    pressure: float | None = None,
    from_xy: tuple[float, float] | None = None,
    to_xy: tuple[float, float] | None = None,
) -> MatchEvent:
    """Build a single event for a fixture, with a unique id."""
    return MatchEvent(
        id=f"T-E{next(_counter):04d}",
        match_id="T",
        clock=clock,
        type=kind,  # type: ignore[arg-type]
        team=team,  # type: ignore[arg-type]
        player=_HOME_PLAYER if team == "home" else _AWAY_PLAYER,
        from_xy=from_xy,
        to_xy=to_xy,
        outcome=outcome,  # type: ignore[arg-type]
        speed_kmh=speed,
        pressure=pressure,
    )


class WindowingTests(unittest.TestCase):
    def test_window_is_half_open(self) -> None:
        events = [make_event("pass", clock=c) for c in (9, 10, 15, 19, 20)]
        included = [e.clock for e in in_window(events, (10, 20))]
        self.assertEqual(included, [10, 15, 19])

    def test_empty_window_returns_nothing(self) -> None:
        events = [make_event("pass", clock=c) for c in (1, 2, 3)]
        self.assertEqual(in_window(events, (100, 200)), ())


class PossessionTests(unittest.TestCase):
    def test_share_reflects_completed_passes_only(self) -> None:
        events = [
            make_event("pass", team="home", outcome="success"),
            make_event("pass", team="home", outcome="success"),
            make_event("pass", team="home", outcome="success"),
            make_event("pass", team="home", outcome="fail"),
            make_event("pass", team="away", outcome="success"),
        ]
        self.assertEqual(possession_share(events), {"home": 0.75, "away": 0.25})

    def test_share_is_empty_safe(self) -> None:
        self.assertEqual(possession_share([]), {"home": 0.0, "away": 0.0})


class PassingTests(unittest.TestCase):
    def test_accuracy_counts_only_that_team(self) -> None:
        events = [
            make_event("pass", team="home", outcome="success"),
            make_event("pass", team="home", outcome="success"),
            make_event("pass", team="home", outcome="success"),
            make_event("pass", team="home", outcome="fail"),
            make_event("pass", team="away", outcome="fail"),
        ]
        self.assertEqual(pass_accuracy(events, "home"), 0.75)
        self.assertEqual(pass_accuracy(events, "away"), 0.0)

    def test_accuracy_is_zero_without_passes(self) -> None:
        self.assertEqual(pass_accuracy([make_event("shot")], "home"), 0.0)

    def test_long_pass_is_harder_than_short_pressured_pass(self) -> None:
        long_unpressured = make_event(
            "pass", from_xy=(0.0, 0.0), to_xy=(45.0, 0.0), pressure=0.0
        )
        short_pressured = make_event(
            "pass", from_xy=(0.0, 0.0), to_xy=(0.0, 0.0), pressure=1.0
        )
        self.assertGreater(pass_difficulty(long_unpressured), pass_difficulty(short_pressured))
        self.assertAlmostEqual(pass_difficulty(long_unpressured), 0.65)
        self.assertAlmostEqual(pass_difficulty(short_pressured), 0.35)

    def test_difficulty_is_bounded(self) -> None:
        extreme = make_event("pass", from_xy=(0.0, 0.0), to_xy=(100.0, 100.0), pressure=1.0)
        self.assertLessEqual(pass_difficulty(extreme), 1.0)
        self.assertGreaterEqual(pass_difficulty(extreme), 0.0)

    def test_difficulty_rejects_non_passes(self) -> None:
        with self.assertRaises(ValueError):
            pass_difficulty(make_event("shot"))

    def test_mean_difficulty_averages_that_team(self) -> None:
        events = [
            make_event("pass", team="home", from_xy=(0.0, 0.0), to_xy=(45.0, 0.0), pressure=0.0),
            make_event("pass", team="home", from_xy=(0.0, 0.0), to_xy=(0.0, 0.0), pressure=0.0),
            make_event("pass", team="away", from_xy=(0.0, 0.0), to_xy=(45.0, 0.0), pressure=0.0),
        ]
        self.assertAlmostEqual(pass_difficulty_mean(events, "home"), 0.325)


class PressureAndMomentumTests(unittest.TestCase):
    def test_pressure_index_averages_recorded_values(self) -> None:
        events = [make_event("pass", pressure=0.2), make_event("shot", pressure=0.8)]
        self.assertAlmostEqual(pressure_index(events), 0.5)

    def test_pressure_index_filters_by_team(self) -> None:
        events = [
            make_event("pass", team="home", pressure=0.2),
            make_event("pass", team="away", pressure=0.8),
        ]
        self.assertAlmostEqual(pressure_index(events, "home"), 0.2)
        self.assertAlmostEqual(pressure_index(events, "away"), 0.8)

    def test_pressure_index_ignores_events_without_pressure(self) -> None:
        events = [make_event("pass", pressure=0.4), make_event("kickoff")]
        self.assertAlmostEqual(pressure_index(events), 0.4)

    def test_momentum_signs_follow_the_attacking_team(self) -> None:
        self.assertGreater(momentum([make_event("shot", team="home")]), 0)
        self.assertLess(momentum([make_event("shot", team="away")]), 0)

    def test_failed_passes_do_not_contribute_momentum(self) -> None:
        self.assertEqual(momentum([make_event("pass", team="home", outcome="fail")]), 0.0)
        self.assertGreater(momentum([make_event("pass", team="home", outcome="success")]), 0)

    def test_goal_outweighs_a_shot(self) -> None:
        self.assertGreater(
            momentum([make_event("goal", team="home")]),
            momentum([make_event("shot", team="home")]),
        )


class RhythmTests(unittest.TestCase):
    def test_metronomic_events_have_zero_chaos(self) -> None:
        events = [make_event("pass", clock=c) for c in (0, 10, 20, 30, 40)]
        self.assertEqual(chaos_index(events), 0.0)

    def test_irregular_events_register_chaos(self) -> None:
        regular = [make_event("pass", clock=c) for c in (0, 10, 20, 30, 40)]
        irregular = [make_event("pass", clock=c) for c in (0, 1, 2, 41, 42)]
        self.assertGreater(chaos_index(irregular), chaos_index(regular))

    def test_chaos_is_bounded_and_short_windows_are_safe(self) -> None:
        variable = [make_event("pass", clock=c) for c in (0, 1, 90, 91, 92, 300)]
        self.assertLessEqual(chaos_index(variable), 1.0)
        self.assertEqual(chaos_index([make_event("pass", clock=0)]), 0.0)
        self.assertEqual(chaos_index([]), 0.0)


class SpeedTests(unittest.TestCase):
    def test_peak_speed_takes_the_maximum(self) -> None:
        events = [make_event("pass", speed=20.0), make_event("pass", speed=90.0), make_event("pass", speed=60.0)]
        self.assertEqual(peak_speed(events), 90.0)

    def test_peak_speed_filters_by_kind_and_team(self) -> None:
        events = [
            make_event("pass", team="home", speed=20.0),
            make_event("shot", team="home", speed=110.0),
            make_event("shot", team="away", speed=130.0),
        ]
        self.assertEqual(peak_speed(events, kind="shot", team="home"), 110.0)
        self.assertEqual(peak_speed(events, kind="shot"), 130.0)

    def test_peak_speed_is_zero_when_nothing_recorded(self) -> None:
        self.assertEqual(peak_speed([make_event("kickoff")]), 0.0)


class MilestoneTests(unittest.TestCase):
    def test_streak_counts_consecutive_pressured_completions(self) -> None:
        events = [
            make_event("pass", team="home", outcome="success", pressure=0.6),
            make_event("pass", team="home", outcome="success", pressure=0.7),
            make_event("pass", team="home", outcome="success", pressure=0.8),
            make_event("pass", team="home", outcome="success", pressure=0.1),
            make_event("pass", team="home", outcome="success", pressure=0.9),
        ]
        self.assertEqual(longest_pressure_pass_streak(events, "home"), 3)

    def test_failed_pass_breaks_the_streak(self) -> None:
        events = [
            make_event("pass", team="home", outcome="success", pressure=0.9),
            make_event("pass", team="home", outcome="fail", pressure=0.9),
            make_event("pass", team="home", outcome="success", pressure=0.9),
        ]
        self.assertEqual(longest_pressure_pass_streak(events, "home"), 1)

    def test_turnover_breaks_the_streak(self) -> None:
        events = [
            make_event("pass", team="home", outcome="success", pressure=0.9),
            make_event("possession_change", team="away", outcome="success"),
            make_event("pass", team="home", outcome="success", pressure=0.9),
        ]
        self.assertEqual(longest_pressure_pass_streak(events, "home"), 1)

    def test_threshold_is_tunable(self) -> None:
        events = [make_event("pass", team="home", outcome="success", pressure=0.4)]
        self.assertEqual(longest_pressure_pass_streak(events, "home", threshold=0.9), 0)


class RegistryTests(unittest.TestCase):
    def test_registry_exposes_the_documented_metrics(self) -> None:
        expected = {
            "possession_share", "pass_accuracy", "pass_difficulty_mean",
            "pressure_index", "momentum", "chaos_index", "peak_speed",
            "longest_pressure_pass_streak",
        }
        self.assertEqual(set(METRICS), expected)

    def test_compute_dispatches_to_the_named_metric(self) -> None:
        events = [make_event("pass", pressure=0.3), make_event("pass", pressure=0.7)]
        self.assertAlmostEqual(compute("pressure_index", events), 0.5)

    def test_compute_passes_arguments_through(self) -> None:
        events = [make_event("pass", team="home", pressure=0.3), make_event("pass", team="away", pressure=0.9)]
        self.assertAlmostEqual(compute("pressure_index", events, team="home"), 0.3)

    def test_unknown_metric_raises_with_a_helpful_message(self) -> None:
        """The Verifier depends on this to reject invented metric names."""
        with self.assertRaises(KeyError) as ctx:
            compute("momentum_shift", [])
        message = str(ctx.exception)
        self.assertIn("momentum_shift", message)
        self.assertIn("pressure_index", message)


if __name__ == "__main__":
    unittest.main()
