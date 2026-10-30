"""The matches page's HTTP contract.

Three things are being protected here, and each of them is a decision the page depends on:

* the live endpoint names the state the adapter computed and nothing more — a 502 when the
  feed is unreachable, rather than a 200 with an empty matchday that reads as "no matches";
* the archive list is served from the source's own index, with the path a row opens and a
  ``cached`` flag the reader can trust before clicking;
* a match opened from the archive is fetched with the competition and season the archive
  named, not with the default pair.
"""

from __future__ import annotations

import unittest
from unittest import mock

from pitchframe.ingest import openliga
from pitchframe.ingest.openliga import Goal, LiveMatch, Matchday
from pitchframe.ingest.statsbomb import MatchMeta, SeasonMatch

try:
    from fastapi.testclient import TestClient

    from pitchframe.overlay import server

    HAS_SERVER = True
except ImportError:  # pragma: no cover - depends on the optional extra
    HAS_SERVER = False

requires_server = unittest.skipUnless(
    HAS_SERVER,
    "the overlay server needs fastapi and uvicorn; install requirements.txt",
)

META = MatchMeta(
    match_id="8658",
    competition="FIFA World Cup",
    season="2018",
    date="2018-07-15",
    kick_off="18:00:00.000",
    stadium="Luzhniki Stadium",
    home_id=771,
    home_name="France",
    away_id=785,
    away_name="Croatia",
)


def live_doc() -> Matchday:
    match = LiveMatch(
        match_id="83227",
        league="bl1",
        competition="Bundesliga",
        season="2026",
        matchday="8. Spieltag",
        kickoff="2026-10-30T19:30:00Z",
        home="Northgate Athletic",
        away="Riverside United",
        home_id=1,
        away_id=2,
        status="live",
        goals=(Goal(12, "home", "A. Scorer", 1, 0),),
        minute=63,
        phase="second_half",
        home_score=1,
        away_score=0,
        fetched_at="2026-10-30T20:35:00Z",
    )
    return Matchday(
        league="bl1",
        name="Bundesliga",
        season="2026",
        matchday="8. Spieltag",
        fetched_at="2026-10-30T20:35:00Z",
        matches=(match,),
    )


@requires_server
class TestMatchesPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(server.create_app())

    def test_the_page_carries_both_panels(self) -> None:
        page = self.client.get("/matches")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn('id="panel-live"', page.text)
        self.assertIn('id="panel-archive"', page.text)

    def test_the_page_loads_its_own_assets(self) -> None:
        for asset in ("/static/matches.js", "/static/matches.css"):
            with self.subTest(asset=asset):
                response = self.client.get(asset)
                self.assertEqual(response.status_code, 200)

    def test_the_nav_links_to_the_page_from_the_other_two(self) -> None:
        for path in ("/", "/match"):
            with self.subTest(path=path):
                self.assertIn('href="/matches"', self.client.get(path).text)


@requires_server
class TestLiveEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(server.create_app())

    def test_the_matchday_is_served_with_its_state(self) -> None:
        with mock.patch.object(openliga, "matchday", return_value=live_doc()) as feed:
            body = self.client.get("/api/live?league=bl1").json()
        feed.assert_called_once_with("bl1", force=False)
        self.assertEqual(body["source"], "openligadb")
        self.assertTrue(body["has_live"])
        match = body["matches"][0]
        self.assertEqual(match["status"], "live")
        self.assertEqual(match["score"], "1–0")
        self.assertEqual(match["path"], "/matches?live=bl1/83227")
        self.assertEqual(match["moments"][-1]["text"].split("'")[0], "The latest goal was A. Scorer")

    def test_the_league_is_passed_through(self) -> None:
        with mock.patch.object(openliga, "matchday", return_value=live_doc()) as feed:
            self.client.get("/api/live?league=pl")
        feed.assert_called_once_with("pl", force=False)

    def test_refresh_asks_the_feed_now(self) -> None:
        with mock.patch.object(openliga, "matchday", return_value=live_doc()) as feed:
            self.client.get("/api/live?league=bl1&refresh=true")
        feed.assert_called_once_with("bl1", force=True)

    def test_an_unreachable_feed_is_a_502_not_an_empty_matchday(self) -> None:
        with mock.patch.object(
            openliga, "matchday", side_effect=openliga.OpenLigaError("offline")
        ):
            response = self.client.get("/api/live")
        self.assertEqual(response.status_code, 502)
        self.assertIn("offline", response.json()["detail"])

    def test_the_leagues_offered_are_the_ones_the_server_accepts(self) -> None:
        with mock.patch.object(openliga, "matchday", return_value=live_doc()):
            body = self.client.get("/api/live").json()
        shortcuts = [entry["shortcut"] for entry in body["leagues"]]
        self.assertIn("bl1", shortcuts)
        self.assertIn("pl", shortcuts)


@requires_server
class TestArchiveEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.create_app())
        server._archive_catalogue.cache_clear()
        server._archive_season_matches.cache_clear()

    def tearDown(self) -> None:
        server._archive_catalogue.cache_clear()
        server._archive_season_matches.cache_clear()

    def test_the_catalogue_is_grouped_and_cached(self) -> None:
        entries = (
            {
                "id": 43,
                "name": "FIFA World Cup",
                "country": "International",
                "seasons": [{"id": 3, "name": "2018"}],
            },
        )
        with mock.patch.object(
            server, "archive_competitions", return_value=entries
        ) as source:
            first = self.client.get("/api/archive/competitions").json()
            second = self.client.get("/api/archive/competitions").json()
        self.assertEqual(source.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["count"], 1)
        self.assertEqual(first["competitions"][0]["seasons"][0]["name"], "2018")

    def test_a_season_lists_scores_and_the_path_it_opens(self) -> None:
        rows = (
            SeasonMatch(META, competition_id=43, season_id=3, home_score=4, away_score=2),
        )
        with mock.patch.object(
            server, "archive_season_matches", return_value=rows
        ), mock.patch.object(server, "is_cached", return_value=True), mock.patch.object(
            server,
            "archive_competitions",
            return_value=(
                {
                    "id": 43,
                    "name": "FIFA World Cup",
                    "country": "International",
                    "seasons": [{"id": 3, "name": "2018"}],
                },
            ),
        ):
            body = self.client.get("/api/archive/matches?competition=43&season=3").json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["competition"]["name"], "FIFA World Cup")
        self.assertEqual(body["season"]["name"], "2018")
        match = body["matches"][0]
        self.assertEqual(match["score"], "4–2")
        self.assertTrue(match["cached"])
        self.assertIn("statsbomb=8658", match["path"])
        self.assertIn("competition=43", match["path"])
        self.assertIn("season=3", match["path"])

    def test_a_missing_season_is_a_404(self) -> None:
        with mock.patch.object(
            server,
            "archive_season_matches",
            side_effect=server.StatsBombError("the archive has no matches"),
        ):
            response = self.client.get("/api/archive/matches?competition=1&season=2")
        self.assertEqual(response.status_code, 404)

    def test_a_failed_download_is_a_502(self) -> None:
        with mock.patch.object(
            server,
            "archive_season_matches",
            side_effect=server.StatsBombError("could not reach the archive"),
        ):
            response = self.client.get("/api/archive/matches?competition=1&season=2")
        self.assertEqual(response.status_code, 502)


@requires_server
class TestReplayFetchFromArchive(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.create_app())
        server._replay_document.cache_clear()

    def tearDown(self) -> None:
        server._replay_document.cache_clear()

    def test_the_archive_pair_travels_with_the_id(self) -> None:
        with mock.patch.object(
            server, "_replay_document", return_value={"match_id": "8658"}
        ) as build:
            response = self.client.get(
                "/api/replay?statsbomb=8658&competition=43&season=3"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            build.call_args.args,
            ("8658", 0, "", "none", server.DEFAULT_WINDOW_SECONDS, None, 43, 3),
        )

    def test_a_bare_id_keeps_the_cached_only_contract(self) -> None:
        with mock.patch.object(
            server, "_replay_document", return_value={"match_id": "8658"}
        ) as build:
            self.client.get("/api/replay?statsbomb=8658")
        self.assertEqual(
            build.call_args.args,
            ("8658", 0, "", "none", server.DEFAULT_WINDOW_SECONDS, None, None, None),
        )

    def test_half_a_pair_is_refused(self) -> None:
        response = self.client.get("/api/replay?statsbomb=8658&competition=43")
        self.assertEqual(response.status_code, 400)

    def test_a_pair_without_a_recorded_match_is_refused(self) -> None:
        response = self.client.get("/api/replay?seed=7&competition=43&season=3")
        self.assertEqual(response.status_code, 400)

    def test_the_document_fetches_an_uncached_match_with_the_named_pair(self) -> None:
        from pitchframe.generator import generate_match

        match = generate_match(28, "PF-001")
        with mock.patch.object(
            server, "ensure_real_match", return_value=META
        ) as ensure, mock.patch.object(
            server, "load_real_match", return_value=match
        ):
            document = server._replay_document(
                "8658", 0, "", "none", server.DEFAULT_WINDOW_SECONDS, None, 43, 3
            )
        ensure.assert_called_once_with("8658", competition=43, season=3)
        # The replay is built from the match the loader returned; the fetch contract is
        # what this test is about, not the id the generated stand-in carries.
        self.assertEqual(document["source"], "statsbomb")
        self.assertEqual(
            document["ratings"]["labels"][0], "recorded StatsBomb match"
        )


if __name__ == "__main__":
    unittest.main()
