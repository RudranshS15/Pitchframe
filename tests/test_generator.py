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

from pitchframe.generator import build_squads, generate_match
from pitchframe.schema import REGULATION_SECONDS


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
