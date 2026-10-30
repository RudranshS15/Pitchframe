"""The StatsBomb adapter, tested against a payload shaped like the real one.

The fixture is hand-written rather than recorded: it is small enough to read, it names no
real player, and every assertion below is a decision the adapter makes about the feed's
format. The real match used by the demo is not in the repository and not fetched by the
suite — a test that needs the network is a test that fails on a plane.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from pitchframe.ingest import statsbomb
from pitchframe.ingest.statsbomb import MatchMeta, StatsBombError
from pitchframe.stats import peak_speed, pressure_index

HOME_ID = 1
AWAY_ID = 2
MATCH_ID = "9001"


def _lineup(team_id: int, name: str, *, used_sub: bool) -> dict[str, object]:
    """Eleven starters, one substitute who came on, one who did not."""
    players = []
    for index in range(1, 12):
        players.append(
            {
                "player_id": team_id * 100 + index,
                "player_name": f"{name} Player {index}",
                "player_nickname": None,
                "jersey_number": index,
                "positions": [
                    {
                        "position": "Goalkeeper" if index == 1 else "Center Midfield",
                        "from": "00:00",
                        "to": None,
                        "from_period": 1,
                        "to_period": None,
                        "start_reason": "Starting XI",
                    }
                ],
            }
        )
    players.append(
        {
            "player_id": team_id * 100 + 12,
            "player_name": f"{name} Substitute",
            "player_nickname": None,
            "jersey_number": 12,
            "positions": (
                [
                    {
                        "position": "Striker",
                        "from": "60:00",
                        "to": None,
                        "from_period": 2,
                        "to_period": None,
                        "start_reason": "Substitution - On (Tactical)",
                    }
                ]
                if used_sub
                else []
            ),
        }
    )
    players.append(
        {
            "player_id": team_id * 100 + 13,
            "player_name": f"{name} Unused",
            "player_nickname": None,
            "jersey_number": 13,
            "positions": [],
        }
    )
    return {"team_id": team_id, "team_name": name, "lineup": players}


def _event(
    index: int,
    period: int,
    minute: int,
    second: int,
    type_name: str,
    team_id: int,
    player_id: int | None = None,
    *,
    location: list[float] | None = None,
    possession: int = 1,
    **extra: object,
) -> dict[str, object]:
    event: dict[str, object] = {
        "index": index,
        "period": period,
        "minute": minute,
        "second": second,
        "type": {"name": type_name},
        "team": {"id": team_id, "name": "Alpha" if team_id == HOME_ID else "Beta"},
        "possession": possession,
        "location": location,
    }
    if player_id is not None:
        event["player"] = {"id": player_id, "name": f"Player {player_id}"}
    event.update(extra)
    return event


def _fixture() -> tuple[list[dict[str, object]], list[dict[str, object]], MatchMeta]:
    events: list[dict[str, object]] = [
        _event(1, 1, 0, 0, "Starting XI", HOME_ID),
        _event(2, 1, 0, 0, "Starting XI", AWAY_ID),
        _event(3, 1, 0, 0, "Half Start", HOME_ID),
        _event(4, 1, 0, 0, "Half Start", AWAY_ID),
        # A completed home pass. No `pass.outcome` is how the feed says "completed".
        _event(
            5,
            1,
            0,
            5,
            "Pass",
            HOME_ID,
            HOME_ID * 100 + 5,
            location=[10.0, 40.0],
            **{"pass": {"end_location": [60.0, 30.0], "height": {"name": "Ground Pass"}}},
        ),
        # A carry with a measured duration: ten units along the length is 8.75 m at 1 s.
        _event(
            6,
            1,
            0,
            10,
            "Carry",
            HOME_ID,
            HOME_ID * 100 + 5,
            location=[60.0, 30.0],
            duration=1.0,
            carry={"end_location": [70.0, 30.0]},
        ),
        # An away pass, made under pressure from a home press two units away.
        _event(
            7,
            1,
            1,
            0,
            "Pass",
            AWAY_ID,
            AWAY_ID * 100 + 4,
            location=[110.0, 40.0],
            under_pressure=True,
            **{
                "pass": {
                    "end_location": [115.0, 20.0],
                    "outcome": {"name": "Incomplete"},
                }
            },
        ),
        _event(
            8,
            1,
            1,
            0,
            "Pressure",
            HOME_ID,
            HOME_ID * 100 + 6,
            location=[112.0, 40.0],
            duration=1.0,
        ),
        # A tackle won, which is a Duel with type Tackle in the feed.
        _event(
            9,
            1,
            5,
            0,
            "Duel",
            AWAY_ID,
            AWAY_ID * 100 + 3,
            location=[50.0, 50.0],
            duel={"type": {"name": "Tackle"}, "outcome": {"name": "Success In Play"}},
        ),
        # A goal: the shot and the goal it produced, adjacent and on the same clock.
        _event(
            10,
            1,
            10,
            0,
            "Shot",
            HOME_ID,
            HOME_ID * 100 + 9,
            location=[110.0, 40.0],
            shot={
                "outcome": {"name": "Goal"},
                "type": {"name": "Open Play"},
                "end_location": [120.0, 40.0, 0.4],
            },
        ),
        # An own goal: the side that benefited is the `For`, the scorer is the `Against`.
        _event(11, 1, 20, 0, "Own Goal For", AWAY_ID, possession=7),
        _event(
            12,
            1,
            20,
            0,
            "Own Goal Against",
            HOME_ID,
            HOME_ID * 100 + 4,
            location=[10.0, 40.0],
            possession=7,
        ),
        # First-half stoppage runs past 45:00; the clock folds it into the half's end.
        _event(13, 1, 47, 0, "Half End", HOME_ID),
        _event(14, 1, 47, 0, "Half End", AWAY_ID),
        _event(15, 2, 45, 0, "Half Start", AWAY_ID),
        # A second-half pass at 58:33 must read 58 minutes, not 61.
        _event(
            16,
            2,
            58,
            33,
            "Pass",
            HOME_ID,
            HOME_ID * 100 + 8,
            location=[50.0, 50.0],
            **{"pass": {"end_location": [40.0, 45.0]}},
        ),
        # A substitution that brings the away substitute on.
        _event(17, 2, 60, 0, "Substitution", AWAY_ID, AWAY_ID * 100 + 12),
        _event(18, 2, 95, 0, "Half End", HOME_ID),
    ]
    lineups = [
        _lineup(HOME_ID, "Alpha", used_sub=False),
        _lineup(AWAY_ID, "Beta", used_sub=True),
    ]
    meta = MatchMeta(
        match_id=MATCH_ID,
        competition="Test Cup",
        season="2026",
        date="2026-01-01",
        kick_off="12:00:00.000",
        stadium="The Fixture",
        home_id=HOME_ID,
        home_name="Alpha",
        away_id=AWAY_ID,
        away_name="Beta",
    )
    return events, lineups, meta


class TestParsing(unittest.TestCase):
    def setUp(self) -> None:
        events, lineups, meta = _fixture()
        self.match = statsbomb.parse(events, lineups, meta)
        self.by_type = {}
        for event in self.match.events:
            self.by_type.setdefault(event.type, []).append(event)

    def test_score_counts_both_the_shot_and_the_own_goal(self) -> None:
        self.assertEqual(self.match.score, (1, 1))

    def test_a_goal_follows_its_shot_on_the_same_clock(self) -> None:
        shots = [e for e in self.match.events if e.type == "shot"]
        goals = [e for e in self.match.events if e.type == "goal" and e.detail != "own goal"]
        self.assertEqual(len(shots), 1)
        self.assertEqual(len(goals), 1)
        self.assertEqual(shots[0].clock, goals[0].clock)
        self.assertEqual(shots[0].player.id, goals[0].player.id)

    def test_pass_success_is_the_absence_of_an_outcome(self) -> None:
        passes = self.by_type["pass"]
        completed = [p for p in passes if p.outcome == "success"]
        failed = [p for p in passes if p.outcome == "fail"]
        self.assertEqual(len(completed), 2)
        self.assertEqual(len(failed), 1)

    def test_away_coordinates_are_rotated_into_the_home_frame(self) -> None:
        away = next(p for p in self.by_type["pass"] if p.team == "away")
        self.assertAlmostEqual(away.from_xy[0], 100.0 - 110.0 / 120.0 * 100.0, places=2)
        self.assertAlmostEqual(away.from_xy[1], 100.0 - 40.0 / 80.0 * 100.0, places=2)
        home = next(p for p in self.by_type["pass"] if p.team == "home")
        self.assertAlmostEqual(home.from_xy[0], 10.0 / 120.0 * 100.0, places=2)

    def test_pressure_is_derived_from_the_nearby_pressure_event(self) -> None:
        away = next(p for p in self.by_type["pass"] if p.team == "away")
        # Two feed units away, so 1 - 2/30; the under_pressure floor is lower.
        self.assertAlmostEqual(away.pressure, round(1.0 - 2.0 / 30.0, 3), places=3)

    def test_a_carry_speed_is_distance_over_its_recorded_duration(self) -> None:
        carry = self.by_type["carry"][0]
        self.assertAlmostEqual(carry.speed_kmh, 31.5, places=1)
        self.assertAlmostEqual(peak_speed(self.match.events, kind="carry"), 31.5, places=1)

    def test_tackles_are_duels_of_type_tackle(self) -> None:
        tackle = self.by_type["tackle"][0]
        self.assertEqual(tackle.team, "away")
        self.assertEqual(tackle.outcome, "success")

    def test_an_own_goal_is_credited_to_the_side_that_benefited(self) -> None:
        own = next(e for e in self.by_type["goal"] if e.detail == "own goal")
        self.assertEqual(own.team, "away")
        self.assertTrue(own.player.id.startswith("own-goal-"))
        # The scorer's own frame: near the home goal that was hit.
        self.assertLess(own.from_xy[0], 10.0)

    def test_possession_ids_come_from_the_feed(self) -> None:
        self.assertTrue(all(event.possession_id for event in self.match.events if event.type == "pass"))

    def test_stoppage_is_folded_into_the_half_it_belongs_to(self) -> None:
        half = next(e for e in self.match.events if e.type == "half_time")
        self.assertEqual(half.clock, 45 * 60)
        # 58:33 in the second half stays 58:33 — the first half's stoppage did not shift it.
        second = next(
            e for e in self.match.events if e.type == "pass" and e.clock > 45 * 60
        )
        self.assertEqual(second.clock, 58 * 60 + 33)
        self.assertEqual(self.match.events[-1].clock, 90 * 60)

    def test_the_team_sheet_includes_a_substitute_who_played(self) -> None:
        self.assertEqual(len(self.match.away.players), 12)
        self.assertEqual([p.name for p in self.match.away.bench], ["Beta Unused"])
        self.assertIn("Beta Substitute", [p.name for p in self.match.away.players])

    def test_pressure_index_reads_both_sides(self) -> None:
        self.assertGreater(pressure_index(self.match.events, "away"), 0.0)
        self.assertEqual(pressure_index(self.match.events, "home"), 0.0)

    def test_an_unknown_team_in_the_lineups_is_refused(self) -> None:
        events, lineups, meta = _fixture()
        with self.assertRaises(StatsBombError):
            statsbomb.parse(events, lineups[:1], meta)

    def test_the_report_carries_its_origin_and_the_carry_speed(self) -> None:
        from pitchframe.__main__ import summarise

        events, lineups, meta = _fixture()
        match = statsbomb.parse(events, lineups, meta)
        summary = summarise(match, source="statsbomb", origin=meta.to_dict())
        self.assertEqual(summary["source"], "statsbomb")
        self.assertEqual(summary["origin"]["match_id"], MATCH_ID)
        self.assertEqual(summary["peak_speed_kmh"]["carry"], 31.5)


class TestCache(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, match_id: str, meta: MatchMeta) -> None:
        directory = self.root / match_id
        directory.mkdir(parents=True)
        (directory / "match.json").write_text(
            json.dumps(meta.to_dict()), encoding="utf-8"
        )
        (directory / "events.json").write_text("[]", encoding="utf-8")
        (directory / "lineups.json").write_text("[]", encoding="utf-8")

    def test_a_missing_match_names_the_command_that_fetches_it(self) -> None:
        with self.assertRaises(StatsBombError) as caught:
            statsbomb.load("404", root=self.root)
        self.assertIn("--fetch 404", str(caught.exception))

    def test_an_id_that_is_a_path_is_refused_before_any_file_is_read(self) -> None:
        with self.assertRaises(StatsBombError):
            statsbomb.load("../../etc/passwd", root=self.root)
        with self.assertRaises(StatsBombError):
            statsbomb.read_meta("../8658", root=self.root)

    def test_availability_is_newest_first(self) -> None:
        events, lineups, meta = _fixture()
        newer = MatchMeta(**{**meta.to_dict(), "match_id": "2", "date": "2026-02-01"})
        older = MatchMeta(**{**meta.to_dict(), "match_id": "1", "date": "2026-01-01"})
        self._write("1", older)
        self._write("2", newer)
        self.assertEqual([item.match_id for item in statsbomb.available(self.root)], ["2", "1"])
        self.assertTrue(statsbomb.is_cached("1", self.root))
        self.assertFalse(statsbomb.is_cached("3", self.root))


class TestServing(unittest.TestCase):
    """A recorded match through the real HTTP surface, with a fixture on disk."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from pitchframe.overlay import server

        self.server = server
        server._replay_document.cache_clear()
        server._catalogue_document.cache_clear()

        self._tmp = tempfile.TemporaryDirectory()
        events, lineups, meta = _fixture()
        directory = Path(self._tmp.name) / meta.match_id
        directory.mkdir(parents=True)
        (directory / "match.json").write_text(json.dumps(meta.to_dict()), encoding="utf-8")
        (directory / "events.json").write_text(json.dumps(events), encoding="utf-8")
        (directory / "lineups.json").write_text(json.dumps(lineups), encoding="utf-8")

        self._previous = os.environ.get("PITCHFRAME_DATA_DIR")
        os.environ["PITCHFRAME_DATA_DIR"] = self._tmp.name
        self.client = TestClient(server.create_app(default_match=meta.match_id))

    def tearDown(self) -> None:
        self.server._replay_document.cache_clear()
        self.server._catalogue_document.cache_clear()
        if self._previous is None:
            os.environ.pop("PITCHFRAME_DATA_DIR", None)
        else:
            os.environ["PITCHFRAME_DATA_DIR"] = self._previous
        self._tmp.cleanup()

    def test_the_server_opens_on_the_recorded_match(self) -> None:
        body = self.client.get("/api/replay?max_frames=1").json()
        self.assertEqual(body["source"], "statsbomb")
        self.assertEqual(body["home"], "Alpha")
        self.assertEqual(body["origin"]["match_id"], MATCH_ID)

    def test_the_catalogue_puts_the_recorded_match_first(self) -> None:
        body = self.client.get("/api/scenarios").json()
        first = body["scenarios"][0]
        self.assertEqual(first["key"], f"statsbomb-{MATCH_ID}")
        self.assertTrue(first["default"])
        self.assertEqual(first["path"], f"/match?statsbomb={MATCH_ID}")
        self.assertEqual(first["score"], [1, 1])

    def test_an_uncached_id_is_a_404_that_names_the_fetch(self) -> None:
        response = self.client.get("/api/replay?statsbomb=404")
        self.assertEqual(response.status_code, 404)
        self.assertIn("--fetch 404", response.json()["detail"])

    def test_a_path_in_the_id_is_a_400_not_a_file_read(self) -> None:
        response = self.client.get("/api/replay?statsbomb=../../etc/passwd")
        self.assertEqual(response.status_code, 400)
        self.assertIn("numeric", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
