"""The live adapter, tested against payloads shaped like the feed's.

The fixture is hand-written: small enough to read, naming no real player, and shaped the
way OpenLigaDB shapes a match. Nothing here touches the network — a test that needs the
feed is a test that fails on a plane, and the clock is passed in for the same reason:
every state the page can show (scheduled, the interval, a goal in stoppage, a result the
feed never posted) has to be reachable without waiting for a real match to get there.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from pitchframe.ingest import openliga
from pitchframe.ingest.openliga import (
    OpenLigaError,
    clock_for,
    moments,
    parse_match,
    parse_matchday,
)

NOW = datetime(2026, 10, 30, 18, 30, tzinfo=timezone.utc)


def goal(
    minute: int,
    *,
    home: int,
    away: int,
    scoring: str = "home",
    name: str = "A. Scorer",
    penalty: bool = False,
    own_goal: bool = False,
    overtime: bool = False,
) -> dict[str, object]:
    return {
        "goalID": 1,
        "scoreTeam1": home,
        "scoreTeam2": away,
        "matchMinute": minute,
        "goalGetterID": 100,
        "goalGetterName": name,
        "scoringTeamId": 1 if scoring == "home" else 2,
        "isPenalty": penalty,
        "isOwnGoal": own_goal,
        "isOvertime": overtime,
        "comment": None,
    }


def payload(
    *,
    finished: bool = True,
    kickoff: str = "2026-10-30T18:00:00Z",
    goals: list[dict[str, object]] | None = None,
    result: tuple[int, int] | None = None,
) -> dict[str, object]:
    return {
        "matchID": 83227,
        "matchDateTime": "2026-10-30T20:00:00",
        "timeZoneID": "W. Europe Standard Time",
        "leagueName": "Testliga",
        "leagueSeason": 2026,
        "leagueShortcut": "bl1",
        "matchDateTimeUTC": kickoff,
        "group": {"groupName": "8. Spieltag", "groupOrderID": 8},
        "team1": {"teamId": 1, "teamName": "Northgate Athletic", "shortName": "Northgate"},
        "team2": {"teamId": 2, "teamName": "Riverside United", "shortName": "Riverside"},
        "lastUpdateDateTime": "2026-10-30T18:00:00",
        "matchIsFinished": finished,
        "matchResults": (
            [
                {
                    "resultTypeID": 2,
                    "pointsTeam1": result[0],
                    "pointsTeam2": result[1],
                }
            ]
            if result
            else []
        ),
        "goals": goals or [],
        "location": "Teststadion",
        "numberOfViewers": None,
    }


class TestClock(unittest.TestCase):
    def test_before_kick_off_there_is_no_minute(self) -> None:
        minute, phase = clock_for(NOW + timedelta(minutes=5), NOW)
        self.assertIsNone(minute)
        self.assertEqual(phase, "not_started")

    def test_the_first_half_counts_from_kick_off(self) -> None:
        minute, phase = clock_for(NOW - timedelta(minutes=30), NOW)
        self.assertEqual((minute, phase), (30, "first_half"))

    def test_the_interval_stops_at_forty_five(self) -> None:
        minute, phase = clock_for(NOW - timedelta(minutes=50), NOW)
        self.assertEqual((minute, phase), (45, "half_time"))

    def test_the_second_half_allows_the_fifteen_minute_interval(self) -> None:
        # 70 wall-clock minutes after kick-off is 55 played minutes.
        minute, phase = clock_for(NOW - timedelta(minutes=70), NOW)
        self.assertEqual((minute, phase), (55, "second_half"))

    def test_stoppage_reads_ninety_rather_than_inventing_added_time(self) -> None:
        minute, phase = clock_for(NOW - timedelta(minutes=120), NOW)
        self.assertEqual((minute, phase), (90, "stoppage"))


class TestParseMatch(unittest.TestCase):
    def test_a_finished_match_shows_the_posted_result(self) -> None:
        match = parse_match(
            payload(goals=[goal(5, home=1, away=0), goal(83, home=1, away=1, scoring="away")]),
            league="bl1",
            now=NOW,
        )
        self.assertEqual(match.status, "finished")
        self.assertEqual(match.score, "1–1")
        self.assertEqual(match.minute, 90)
        self.assertEqual([item.minute for item in match.goals], [5, 83])

    def test_the_result_field_wins_over_the_last_goal(self) -> None:
        """A corrected result lands in ``matchResults``, not in the goal list."""
        match = parse_match(
            payload(goals=[goal(5, home=1, away=0)], result=(3, 0)),
            league="bl1",
            now=NOW,
        )
        self.assertEqual(match.score, "3–0")

    def test_a_goal_is_credited_to_the_side_the_feed_says_benefited(self) -> None:
        match = parse_match(
            payload(
                finished=False,
                goals=[goal(12, home=0, away=1, scoring="away", name="Own Net")],
            ),
            league="bl1",
            now=NOW - timedelta(minutes=20),
        )
        self.assertEqual(match.goals[0].side, "away")
        self.assertEqual(match.goals[0].scorer, "Own Net")

    def test_a_penalty_and_an_own_goal_are_kept_apart(self) -> None:
        match = parse_match(
            payload(
                goals=[
                    goal(20, home=1, away=0, penalty=True),
                    goal(40, home=2, away=0, own_goal=True),
                ]
            ),
            league="bl1",
            now=NOW,
        )
        self.assertTrue(match.goals[0].penalty)
        self.assertFalse(match.goals[0].own_goal)
        self.assertTrue(match.goals[1].own_goal)
        self.assertIn("(pen)", match.goals[0].label)
        self.assertIn("OG", match.goals[1].label)

    def test_a_match_in_play_is_live_with_a_derived_minute(self) -> None:
        match = parse_match(
            payload(finished=False, kickoff="2026-10-30T18:00:00Z"),
            league="bl1",
            now=NOW,
        )
        self.assertEqual(match.status, "live")
        self.assertTrue(match.is_live)
        self.assertEqual(match.minute, 30)

    def test_a_match_whose_result_never_arrived_is_pending_not_live(self) -> None:
        match = parse_match(
            payload(finished=False, kickoff="2026-10-30T12:00:00Z"),
            league="bl1",
            now=NOW,
        )
        self.assertEqual(match.status, "pending")

    def test_a_future_match_is_scheduled(self) -> None:
        match = parse_match(
            payload(finished=False, kickoff="2026-10-30T21:00:00Z"),
            league="bl1",
            now=NOW,
        )
        self.assertEqual(match.status, "scheduled")
        self.assertIsNone(match.minute)
        self.assertEqual(match.score, "0–0")

    def test_an_unreadable_kick_off_is_refused(self) -> None:
        broken = payload()
        broken["matchDateTimeUTC"] = "not a date"
        broken["matchDateTime"] = "not a date"
        with self.assertRaises(OpenLigaError):
            parse_match(broken, league="bl1", now=NOW)

    def test_the_league_label_is_the_one_the_page_offers(self) -> None:
        match = parse_match(payload(), league="pl", now=NOW)
        self.assertEqual(match.league, "pl")
        self.assertIn("/matches?live=pl/", match.path)


class TestMatchday(unittest.TestCase):
    def test_fixtures_are_ordered_by_kick_off(self) -> None:
        early = payload(kickoff="2026-10-30T14:30:00Z")
        late = payload(kickoff="2026-10-30T18:30:00Z")
        late["matchID"] = 9002
        doc = parse_matchday([late, early], league="bl1", now=NOW)
        self.assertEqual([match.match_id for match in doc.matches], ["83227", "9002"])
        self.assertEqual(doc.matchday, "8. Spieltag")

    def test_has_live_is_true_only_while_one_is_in_play(self) -> None:
        scheduled = parse_matchday(
            [payload(finished=False, kickoff="2026-10-30T21:00:00Z")],
            league="bl1",
            now=NOW,
        )
        self.assertFalse(scheduled.has_live)
        live = parse_matchday(
            [payload(finished=False, kickoff="2026-10-30T18:00:00Z")],
            league="bl1",
            now=NOW,
        )
        self.assertTrue(live.has_live)

    def test_an_empty_matchday_is_an_error(self) -> None:
        with self.assertRaises(OpenLigaError):
            parse_matchday([], league="bl1", now=NOW)


class TestMoments(unittest.TestCase):
    def test_no_goals_supports_no_claims(self) -> None:
        match = parse_match(payload(goals=[]), league="bl1", now=NOW)
        self.assertEqual(moments(match), [])

    def test_a_lead_change_is_counted_and_evidenced(self) -> None:
        match = parse_match(
            payload(
                goals=[
                    goal(10, home=1, away=0),
                    goal(40, home=1, away=1, scoring="away"),
                    goal(70, home=1, away=2, scoring="away"),
                ]
            ),
            league="bl1",
            now=NOW,
        )
        texts = [entry["text"] for entry in moments(match)]
        self.assertIn("The lead changed hands once.", texts)
        change = next(entry for entry in moments(match) if "lead changed" in entry["text"])
        self.assertIn("10'", change["evidence"])

    def test_a_two_goal_deficit_erased_is_a_comeback(self) -> None:
        match = parse_match(
            payload(
                goals=[
                    goal(5, home=1, away=0),
                    goal(15, home=2, away=0),
                    goal(50, home=2, away=1, scoring="away"),
                    goal(80, home=2, away=2, scoring="away"),
                ]
            ),
            league="bl1",
            now=NOW,
        )
        texts = [entry["text"] for entry in moments(match)]
        self.assertIn("Riverside United came back from 2 goals down.", texts)

    def test_a_burst_is_reported_with_its_minutes(self) -> None:
        match = parse_match(
            payload(
                goals=[
                    goal(20, home=1, away=0),
                    goal(23, home=1, away=1, scoring="away"),
                    goal(29, home=2, away=1),
                ]
            ),
            league="bl1",
            now=NOW,
        )
        found = next(entry for entry in moments(match) if "goals in" in entry["text"])
        self.assertEqual(found["text"], "3 goals in 9 minutes.")
        self.assertEqual(found["evidence"], "20', 23', 29'")

    def test_the_latest_goal_is_an_ordinal(self) -> None:
        match = parse_match(
            payload(goals=[goal(3, home=1, away=0)]), league="bl1", now=NOW
        )
        found = next(entry for entry in moments(match) if "latest goal" in entry["text"])
        self.assertIn("3rd minute", found["text"])


class TestCache(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_a_fresh_copy_is_reused_without_asking_again(self) -> None:
        feed = [payload(goals=[goal(10, home=1, away=0)])]
        with mock.patch.object(openliga, "_get_json", return_value=feed) as get:
            first = openliga.matchday("bl1", root=self.root, now=NOW)
            second = openliga.matchday("bl1", root=self.root, now=NOW)
        self.assertEqual(get.call_count, 1)
        self.assertFalse(first.stale)
        self.assertFalse(second.stale)
        self.assertTrue(openliga.cache_path("bl1", self.root).is_file())

    def test_an_expired_copy_is_refreshed(self) -> None:
        feed = [payload()]
        with mock.patch.object(openliga, "_get_json", return_value=feed) as get:
            openliga.matchday("bl1", root=self.root, now=NOW)
            openliga.matchday(
                "bl1", root=self.root, now=NOW + timedelta(seconds=60), ttl=25
            )
        self.assertEqual(get.call_count, 2)

    def test_a_failed_refresh_falls_back_to_the_last_good_copy(self) -> None:
        feed = [payload(goals=[goal(10, home=1, away=0)])]
        with mock.patch.object(openliga, "_get_json", return_value=feed):
            openliga.matchday("bl1", root=self.root, now=NOW)
        with mock.patch.object(
            openliga, "_get_json", side_effect=OpenLigaError("offline")
        ):
            stale = openliga.matchday(
                "bl1", root=self.root, now=NOW + timedelta(seconds=120), ttl=25
            )
        self.assertTrue(stale.stale)
        self.assertEqual(len(stale.matches), 1)

    def test_a_failed_first_fetch_raises(self) -> None:
        with mock.patch.object(
            openliga, "_get_json", side_effect=OpenLigaError("offline")
        ):
            with self.assertRaises(OpenLigaError):
                openliga.matchday("bl1", root=self.root, now=NOW)

    def test_force_asks_even_when_the_copy_is_fresh(self) -> None:
        feed = [payload()]
        with mock.patch.object(openliga, "_get_json", return_value=feed) as get:
            openliga.matchday("bl1", root=self.root, now=NOW)
            openliga.matchday("bl1", root=self.root, now=NOW, force=True)
        self.assertEqual(get.call_count, 2)

    def test_an_unknown_league_never_reaches_the_network(self) -> None:
        with mock.patch.object(openliga, "_get_json") as get:
            with self.assertRaises(OpenLigaError):
                openliga.matchday("not-a-league", root=self.root, now=NOW)
            with self.assertRaises(OpenLigaError):
                openliga.cache_path("not-a-league", self.root)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
