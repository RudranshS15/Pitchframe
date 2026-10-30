"""The archive, tested against index files shaped like the open data.

The two files behind this — ``competitions.json`` and a season's ``matches/`` index — are
small, stable, and the only thing that stands between a reader and a download. So the
tests are about the decisions made over them: grouping the competition file into one entry
per competition with its seasons, reading a season into rows with scores, ordering both,
and refusing ids that would become cache paths.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pitchframe.ingest import statsbomb
from pitchframe.ingest.statsbomb import StatsBombError


def competition_row(
    competition_id: int,
    season_id: int,
    *,
    name: str = "FIFA World Cup",
    season: str = "2018",
    country: str = "International",
) -> dict[str, object]:
    return {
        "competition_id": competition_id,
        "season_id": season_id,
        "country_name": country,
        "competition_name": name,
        "competition_gender": "male",
        "competition_youth": False,
        "competition_international": True,
        "season_name": season,
    }


def fixture(
    match_id: int,
    *,
    date: str,
    home: str = "France",
    away: str = "Croatia",
    home_score: int | None = 4,
    away_score: int | None = 2,
) -> dict[str, object]:
    return {
        "match_id": match_id,
        "match_date": date,
        "kick_off": "18:00:00.000",
        "competition": {"competition_id": 43, "competition_name": "FIFA World Cup"},
        "season": {"season_id": 3, "season_name": "2018"},
        "home_team": {"home_team_id": 771, "home_team_name": home},
        "away_team": {"away_team_id": 785, "away_team_name": away},
        "home_score": home_score,
        "away_score": away_score,
        "competition_stadium": "Luzhniki Stadium",
    }


class TestCompetitions(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_seasons_are_grouped_under_one_competition(self) -> None:
        rows = [
            competition_row(43, 3, season="2018"),
            competition_row(43, 106, season="2022"),
            competition_row(9, 281, name="1. Bundesliga", country="Germany"),
        ]
        with mock.patch.object(statsbomb, "_get_json", return_value=rows) as get:
            catalogue = statsbomb.competitions(root=self.root)
            again = statsbomb.competitions(root=self.root)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(len(catalogue), 2)
        world_cup = next(item for item in catalogue if item["id"] == 43)
        self.assertEqual([season["id"] for season in world_cup["seasons"]], [106, 3])
        self.assertEqual(catalogue, again)

    def test_the_file_is_cached_before_it_is_parsed(self) -> None:
        with mock.patch.object(
            statsbomb, "_get_json", return_value=[competition_row(43, 3)]
        ):
            statsbomb.competitions(root=self.root)
        self.assertTrue((self.root / "_catalogue" / "competitions.json").is_file())

    def test_a_duplicate_competition_season_appears_once(self) -> None:
        rows = [competition_row(43, 3), competition_row(43, 3)]
        with mock.patch.object(statsbomb, "_get_json", return_value=rows):
            catalogue = statsbomb.competitions(root=self.root)
        self.assertEqual(len(catalogue), 1)
        self.assertEqual(len(catalogue[0]["seasons"]), 1)

    def test_a_malformed_row_is_skipped_rather_than_failing_the_catalogue(self) -> None:
        rows = [competition_row(43, 3), {"competition_id": "nope"}]
        with mock.patch.object(statsbomb, "_get_json", return_value=rows):
            catalogue = statsbomb.competitions(root=self.root)
        self.assertEqual([item["id"] for item in catalogue], [43])

    def test_a_non_list_is_refused(self) -> None:
        with mock.patch.object(statsbomb, "_get_json", return_value={"nope": True}):
            with self.assertRaises(StatsBombError):
                statsbomb.competitions(root=self.root, force=True)


class TestSeasonMatches(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_rows_are_newest_first_with_scores_and_a_download_path(self) -> None:
        rows = [
            fixture(8657, date="2018-07-14", home="Belgium", away="England", home_score=2, away_score=0),
            fixture(8658, date="2018-07-15"),
        ]
        with mock.patch.object(statsbomb, "_get_json", return_value=rows):
            season = statsbomb.season_matches(43, 3, root=self.root)
        self.assertEqual([row.meta.match_id for row in season], ["8658", "8657"])
        self.assertTrue(season[0].played)
        payload = season[0].to_dict(cached=False)
        self.assertEqual(payload["score"], "4–2")
        self.assertFalse(payload["cached"])
        self.assertIn("statsbomb=8658", payload["path"])
        self.assertIn("competition=43", payload["path"])
        self.assertIn("season=3", payload["path"])

    def test_an_unplayed_fixture_has_no_score(self) -> None:
        rows = [fixture(9000, date="2026-07-15", home_score=None, away_score=None)]
        with mock.patch.object(statsbomb, "_get_json", return_value=rows):
            season = statsbomb.season_matches(43, 3, root=self.root)
        self.assertFalse(season[0].played)
        self.assertEqual(season[0].to_dict()["score"], "")

    def test_the_index_is_downloaded_once(self) -> None:
        with mock.patch.object(
            statsbomb, "_get_json", return_value=[fixture(8658, date="2018-07-15")]
        ) as get:
            statsbomb.season_matches(43, 3, root=self.root)
            statsbomb.season_matches(43, 3, root=self.root)
        self.assertEqual(get.call_count, 1)

    def test_a_malformed_fixture_is_skipped(self) -> None:
        rows = [fixture(8658, date="2018-07-15"), {"match_id": 9999}]
        with mock.patch.object(statsbomb, "_get_json", return_value=rows):
            season = statsbomb.season_matches(43, 3, root=self.root)
        self.assertEqual([row.meta.match_id for row in season], ["8658"])

    def test_a_zero_or_non_numeric_id_never_becomes_a_path(self) -> None:
        with mock.patch.object(statsbomb, "_get_json") as get:
            for competition, season in ((0, 3), (43, 0), ("../etc", 3), (43, "passwd")):
                with self.subTest(competition=competition, season=season):
                    with self.assertRaises(StatsBombError):
                        statsbomb.season_matches(competition, season, root=self.root)
        get.assert_not_called()

    def test_an_empty_season_is_refused(self) -> None:
        with mock.patch.object(statsbomb, "_get_json", return_value=[]):
            with self.assertRaises(StatsBombError):
                statsbomb.season_matches(43, 3, root=self.root, force=True)


class TestEnsure(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_a_cached_match_is_not_downloaded_again(self) -> None:
        with mock.patch.object(statsbomb, "is_cached", return_value=True), mock.patch.object(
            statsbomb, "read_meta", return_value="meta"
        ), mock.patch.object(statsbomb, "fetch") as fetch:
            result = statsbomb.ensure(8658, competition=43, season=3, root=self.root)
        self.assertEqual(result, "meta")
        fetch.assert_not_called()

    def test_an_uncached_match_is_fetched_with_the_archive_pair(self) -> None:
        with mock.patch.object(statsbomb, "is_cached", return_value=False), mock.patch.object(
            statsbomb, "fetch", return_value="meta"
        ) as fetch:
            statsbomb.ensure(8658, competition=44, season=106, root=self.root)
        fetch.assert_called_once_with(
            "8658", competition=44, season=106, root=self.root, timeout=30.0, force=False
        )

    def test_a_non_numeric_match_id_is_refused_before_the_cache(self) -> None:
        with mock.patch.object(statsbomb, "is_cached") as cached:
            with self.assertRaises(StatsBombError):
                statsbomb.ensure("../../etc", competition=43, season=3, root=self.root)
        cached.assert_not_called()


if __name__ == "__main__":
    unittest.main()
