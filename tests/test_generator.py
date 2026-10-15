"""Tests for the synthetic match generator.

The generator is the data source for everything downstream, so these tests
guard the two properties the rest of the system relies on:

1. **Determinism** — the same seed always yields an identical match. Without
   this, the demo video is not reproducible and no evaluation number means
   anything.
2. **Plausible shape** — the output looks like football rather than noise,
   which is what makes the analytics layer worth building.

They also act as a trademark guard: squads must be invented, never real clubs
or players.
"""

from __future__ import annotations

import unittest

from pitchframe.generator import GOAL_HALF_WIDTH, build_squads, generate_match
from pitchframe.schema import PITCH_LENGTH, REGULATION_SECONDS


class DeterminismTests(unittest.TestCase):
    def test_same_seed_produces_an_identical_match(self) -> None:
        first = generate_match(7)
        second = generate_match(7)
        self.assertEqual(first.events, second.events)
        self.assertEqual(first.home, second.home)
        self.assertEqual(first.away, second.away)

    def test_different_seeds_produce_different_matches(self) -> None:
        self.assertNotEqual(generate_match(7).events, generate_match(8).events)

    def test_event_ids_are_unique_and_prefixed(self) -> None:
        events = generate_match(11, match_id="TEST").events
        ids = [e.id for e in events]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i.startswith("TEST-E") for i in ids))

    def test_roster_depends_on_seed(self) -> None:
        import random

        first = build_squads(random.Random(1))
        second = build_squads(random.Random(2))
        self.assertNotEqual([p.name for p in first[0].players], [p.name for p in second[0].players])


class ShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.match = generate_match(7)
        cls.events = cls.match.events

    def test_event_volume_is_plausible(self) -> None:
        self.assertGreater(len(self.events), 400)
        self.assertLess(len(self.events), 2500)

    def test_clock_is_monotonic_and_within_regulation(self) -> None:
        clocks = [e.clock for e in self.events]
        self.assertEqual(clocks, sorted(clocks))
        self.assertLessEqual(self.events[-1].clock, REGULATION_SECONDS + 200)

    def test_starts_with_kickoff_and_ends_with_full_time(self) -> None:
        self.assertEqual(self.events[0].type, "kickoff")
        self.assertEqual(self.events[-1].type, "full_time")

    def test_half_time_fires_exactly_once(self) -> None:
        self.assertEqual(sum(1 for e in self.events if e.type == "half_time"), 1)

    def test_passes_dominate_the_event_stream(self) -> None:
        passes = sum(1 for e in self.events if e.type == "pass")
        self.assertGreater(passes / len(self.events), 0.5)

    def test_every_event_player_belongs_to_the_acting_team(self) -> None:
        for event in self.events:
            self.assertIsNotNone(
                self.match.squad(event.team).by_id(event.player.id),
                f"{event.id}: {event.player.name} is not in the {event.team} squad",
            )

    def test_score_is_derived_from_recorded_goals(self) -> None:
        home = sum(1 for e in self.events if e.type == "goal" and e.team == "home")
        away = sum(1 for e in self.events if e.type == "goal" and e.team == "away")
        self.assertEqual(self.match.score, (home, away))

    def test_pressure_values_are_normalised(self) -> None:
        for event in self.events:
            if event.pressure is not None:
                self.assertGreaterEqual(event.pressure, 0.0)
                self.assertLessEqual(event.pressure, 1.0)

    def test_squads_are_full_and_disjoint(self) -> None:
        home_ids = {p.id for p in self.match.home.players}
        away_ids = {p.id for p in self.match.away.players}
        self.assertEqual(len(home_ids), 11)
        self.assertEqual(len(away_ids), 11)
        self.assertFalse(home_ids & away_ids)

    def test_speeds_are_positive_where_recorded(self) -> None:
        speeds = [e.speed_kmh for e in self.events if e.speed_kmh is not None]
        self.assertTrue(speeds)
        self.assertTrue(all(s > 0 for s in speeds))

    def test_speeds_are_football_plausible(self) -> None:
        """Realism is a claim the README makes, so it is asserted, not assumed.

        ``speed_kmh`` is ball speed. A pass is struck hard enough to reach its
        target; a shot is struck hard enough to beat a keeper, so shots are
        consistently the faster ball. A model that scaled pass speed linearly from
        zero satisfied every "is it positive" check while turning an ordinary 40 m
        pass into a 130 km/h ball, which is why the band is pinned here rather than
        left to visual inspection.
        """
        passes = [e.speed_kmh for e in self.events if e.type == "pass"]
        shots = [e.speed_kmh for e in self.events if e.type == "shot"]

        self.assertTrue(passes)
        self.assertTrue(shots)

        # Upper bounds catch the plausible-looking-but-wrong tail.
        self.assertLessEqual(max(passes), 95.0)
        self.assertLessEqual(max(shots), 125.0)
        self.assertGreaterEqual(min(shots), 60.0)

        mean_pass = sum(passes) / len(passes)
        mean_shot = sum(shots) / len(shots)
        self.assertLess(
            mean_pass,
            mean_shot,
            f"passes ({mean_pass:.1f} km/h) should be slower than shots ({mean_shot:.1f} km/h)",
        )


class FinishGeometryTests(unittest.TestCase):
    """Where a shot and a goal actually finish, since the panel draws it.

    The build-up panel draws the finish as a line from the strike to the point the
    ball ends up, and marks the goal with a ring at the goal event's own target. When
    those two were independent random draws, a strike recorded as "on target" could
    finish well outside the posts and the ring could sit somewhere else again, so the
    picture showed the ball leaving one place and the goal appearing at another. The
    geometry is asserted here rather than eyeballed, because "it looks wrong" is
    exactly the report these numbers exist to make checkable.
    """

    SEEDS = (7, 11, 42)

    @classmethod
    def setUpClass(cls) -> None:
        cls.events = [event for seed in cls.SEEDS for event in generate_match(seed).events]

    def _mouth_offset(self, event) -> float:
        """How far outside the goal's centre line a ball finished, in normalised units."""
        assert event.to_xy is not None
        return abs(event.to_xy[1] - PITCH_LENGTH / 2)

    def test_every_goal_finishes_inside_the_posts(self) -> None:
        goals = [e for e in self.events if e.type == "goal"]
        self.assertGreater(len(goals), 5)
        for goal in goals:
            self.assertLessEqual(
                self._mouth_offset(goal),
                GOAL_HALF_WIDTH,
                f"{goal.id}: goal at y={goal.to_xy[1]:.1f} is wide of the posts",
            )

    def test_a_goal_shares_the_finish_of_the_shot_that_scored_it(self) -> None:
        """One kick, one account of it — the same target and the same ball speed."""
        pairs = 0
        for shot, goal in zip(self.events, self.events[1:]):
            if shot.type != "shot" or goal.type != "goal" or goal.team != shot.team:
                continue
            self.assertEqual(goal.to_xy, shot.to_xy)
            self.assertEqual(goal.speed_kmh, shot.speed_kmh)
            pairs += 1
        self.assertGreater(pairs, 5)

    def test_on_target_and_off_target_agree_with_the_posts(self) -> None:
        """``outcome`` is a claim about where the ball went, so it has to match it."""
        on_target = [e for e in self.events if e.type == "shot" and e.outcome == "success"]
        off_target = [e for e in self.events if e.type == "shot" and e.outcome == "fail"]
        self.assertGreater(len(on_target), 5)
        self.assertGreater(len(off_target), 5)

        for shot in on_target:
            self.assertLessEqual(
                self._mouth_offset(shot),
                GOAL_HALF_WIDTH,
                f"{shot.id}: on-target shot finished at y={shot.to_xy[1]:.1f}",
            )
        for shot in off_target:
            self.assertGreater(
                self._mouth_offset(shot),
                GOAL_HALF_WIDTH,
                f"{shot.id}: off-target shot finished inside the posts at y={shot.to_xy[1]:.1f}",
            )


class TrademarkGuardTests(unittest.TestCase):
    """The rules forbid third-party marks, so no real entity may appear."""

    BANNED = (
        "premier league", "manchester", "liverpool", "arsenal", "chelsea",
        "tottenham", "barcelona", "real madrid", "fifa", "uefa", "everton",
    )

    def test_no_real_world_club_or_player_names(self) -> None:
        for seed in (1, 5, 99):
            match = generate_match(seed)
            haystack = " ".join(
                [match.home.name, match.away.name]
                + [p.name for p in match.home.players]
                + [p.name for p in match.away.players]
            ).lower()
            for term in self.BANNED:
                self.assertNotIn(term, haystack, f"seed {seed} leaked {term!r}")


if __name__ == "__main__":
    unittest.main()
