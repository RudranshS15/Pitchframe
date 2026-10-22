"""Tests for the overlay: the replay script, the commentary, and the server shell.

The overlay's interesting behaviour lives in standard-library code, so almost all of
this runs on a bare interpreter. The HTTP tests need ``fastapi`` and skip without it.

Two things these tests insist on, because both are the kind of claim that silently
stops being true:

* **The replay is reproducible.** The same arguments must produce the same bytes, or
  a recorded demo cannot be re-run in front of anyone.
* **The page's numbers come from the payload rather than from the page.** The
  scoreboard is rebuilt in the browser from the goal list, so if that list and the
  frames disagreed the score would be wrong in a way no Python test would otherwise
  see. Here they are checked against each other.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from pitchframe.agents.demo import FAULT_MODES
from pitchframe.claims import baseline_window_for
from pitchframe.overlay import build_replay, compose
from pitchframe.overlay.commentary import incident_clause
from pitchframe.overlay.replay import (
    DEFAULT_FIRST_WINDOW_START,
    DEFAULT_WINDOW_SECONDS,
)
from pitchframe.ratings import LABELS
from pitchframe.scenarios import ALL_SCENARIOS, DEFAULT_SCENARIO_KEY, contents
from pitchframe.stats import METRIC_SPECS

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from fastapi.testclient import TestClient

    from pitchframe.overlay.server import MAX_WINDOWS, create_app

    HAS_SERVER = True
except ImportError:  # pragma: no cover - depends on the optional extra
    HAS_SERVER = False

requires_server = unittest.skipUnless(
    HAS_SERVER,
    "the overlay server needs fastapi and uvicorn; install requirements.txt",
)


def frame_before(frames: list[dict], clock: int) -> dict:
    """The last frame that closed before ``clock``, for the tests that compare a
    player's mark across a goal."""
    earlier = [frame for frame in frames if frame["clock"] < clock]
    if not earlier:
        raise AssertionError(f"no frame closes before {clock}")
    return earlier[-1]


def replay_doc(**overrides: object) -> dict:
    """A small replay, built fresh, for the tests that only inspect structure."""
    kwargs: dict = {"fault": "narrator-invert", "max_frames": 3}
    kwargs.update(overrides)
    return build_replay(**kwargs).to_dict()


class TestReplayIsReproducible(unittest.TestCase):
    def test_the_same_arguments_produce_the_same_bytes(self) -> None:
        """A demo that cannot be re-run identically is not a demo."""
        first = json.dumps(replay_doc(), sort_keys=True)
        second = json.dumps(replay_doc(), sort_keys=True)
        self.assertEqual(first, second)

    def test_a_different_seed_is_a_different_match(self) -> None:
        one = replay_doc(seed=1)
        two = replay_doc(seed=2)
        self.assertNotEqual(
            (one["home"], one["away"], one["squads"]),
            (two["home"], two["away"], two["squads"]),
        )

    def test_the_payload_is_strict_json(self) -> None:
        """``allow_nan=False`` is the guard: a non-finite metric would serialise to
        ``Infinity``, which the browser refuses to parse, and the page would go blank
        with no error anywhere in Python."""
        json.dumps(replay_doc(), allow_nan=False)

    def test_the_payload_survives_a_round_trip(self) -> None:
        doc = replay_doc()
        self.assertEqual(json.loads(json.dumps(doc)), doc)

    def test_an_unknown_fault_is_refused_rather_than_ignored(self) -> None:
        """A typo must not silently disable the demonstration."""
        with self.assertRaises(ValueError):
            build_replay(fault="invert-everything")


class TestReplayWindowGeometry(unittest.TestCase):
    def test_windows_are_contiguous_and_tile_the_match(self) -> None:
        frames = replay_doc()["frames"]
        for previous, current in zip(frames, frames[1:]):
            self.assertEqual(previous["window"][1], current["window"][0])

    def test_each_baseline_is_the_equal_length_window_before_it(self) -> None:
        for frame in replay_doc()["frames"]:
            window = tuple(frame["window"])
            self.assertEqual(tuple(frame["baseline"]), baseline_window_for(window))

    def test_the_first_window_leaves_room_for_a_baseline(self) -> None:
        """Without room before kick-off there is nothing to compare against."""
        first = replay_doc()["frames"][0]
        self.assertGreaterEqual(first["baseline"][0], 0)
        self.assertEqual(first["window"][0], DEFAULT_FIRST_WINDOW_START)
        self.assertEqual(
            first["window"][1] - first["window"][0], DEFAULT_WINDOW_SECONDS
        )

    def test_frames_are_ordered_and_clock_marks_the_window_end(self) -> None:
        frames = replay_doc()["frames"]
        self.assertEqual([f["clock"] for f in frames], sorted(f["clock"] for f in frames))
        for frame in frames:
            self.assertEqual(frame["clock"], frame["window"][1])

    def test_a_frame_never_claims_about_anything_but_its_own_closed_window(self) -> None:
        """The live-pipeline property: the analyser cannot see its own future."""
        for frame in replay_doc()["frames"]:
            for row in frame["feed"]:
                self.assertEqual(row["window"], frame["window"])
                self.assertLessEqual(row["window"][1], frame["clock"])

    def test_max_frames_limits_the_replay(self) -> None:
        self.assertEqual(len(replay_doc(max_frames=2)["frames"]), 2)
        self.assertEqual(len(replay_doc(max_frames=5)["frames"]), 5)

    def test_the_ledger_never_decreases(self) -> None:
        keys = ("verified", "rejected", "corrections", "dropped")
        frames = build_replay(fault="narrator-invert", max_frames=6).to_dict()["frames"]
        for previous, current in zip(frames, frames[1:]):
            for key in keys:
                self.assertGreaterEqual(current["ledger"][key], previous["ledger"][key])


class TestFaultInjectionIsLabelled(unittest.TestCase):
    def test_the_honest_baseline_catches_nothing(self) -> None:
        """The calibration that makes every other number here meaningful: with no
        fault planted, the scripted narrator produces no wrong directions at all, so
        any rejection in a faulted run is one we manufactured."""
        doc = build_replay(fault="none", max_frames=6).to_dict()
        self.assertEqual(
            sum(1 for f in doc["frames"] for r in f["feed"] if r["status"] == "rejected"),
            0,
        )
        self.assertEqual(doc["injected"], [])

    def test_a_fault_is_planted_in_every_window(self) -> None:
        doc = replay_doc(max_frames=4)
        planted = [r for f in doc["frames"] for r in f["feed"] if r["injected"]]
        self.assertEqual(len(planted), 4)

    def test_planted_faults_are_always_caught(self) -> None:
        """A planted fault that slipped through would be the worst possible result."""
        for row in (r for f in replay_doc(max_frames=6)["frames"] for r in f["feed"] if r["injected"]):
            self.assertEqual(row["status"], "rejected")

    def test_the_injected_keys_are_reported_for_the_page_to_label(self) -> None:
        doc = replay_doc()
        self.assertTrue(doc["injected"])
        for key in doc["injected"]:
            metric = key.split("@")[0]
            self.assertIn(metric, METRIC_SPECS)

    def test_every_fault_mode_builds(self) -> None:
        for mode in FAULT_MODES:
            with self.subTest(mode=mode):
                self.assertTrue(build_replay(fault=mode, max_frames=2).to_dict()["frames"])


class TestTheCorrectionLoop(unittest.TestCase):
    """What the whole project claims, asserted against the artifact a viewer sees."""

    def _frames(self) -> list[dict]:
        return build_replay(fault="narrator-invert", max_frames=6).to_dict()["frames"]

    def test_every_rejection_in_a_window_is_answered(self) -> None:
        for frame in self._frames():
            rejected = [r for r in frame["feed"] if r["status"] == "rejected"]
            corrected = [r for r in frame["feed"] if r["kind"] == "correction"]
            self.assertEqual(len(rejected), len(corrected))

    def test_the_correction_names_the_claim_it_answers(self) -> None:
        for frame in self._frames():
            for row in frame["feed"]:
                if row["kind"] != "correction":
                    continue
                self.assertIn(row["answers"], [r["claim_id"] for r in frame["feed"]])

    def test_the_correction_follows_the_rejection_it_answers(self) -> None:
        """The page reveals rows in order, so the beat only lands in this order."""
        for frame in self._frames():
            ids = [r["claim_id"] for r in frame["feed"]]
            for row in frame["feed"]:
                if row["kind"] == "correction":
                    self.assertLess(ids.index(row["answers"]), ids.index(row["claim_id"]))

    def test_a_correction_states_the_direction_that_was_measured(self) -> None:
        """"Retry" must never quietly become "accept" — the fix has to be the truth."""
        checked = 0
        for frame in self._frames():
            for row in frame["feed"]:
                if row["kind"] != "correction":
                    continue
                self.assertEqual(row["direction"], row["measured_direction"])
                self.assertEqual(row["status"], "verified")
                checked += 1
        self.assertGreater(checked, 0)

    def test_a_rejected_line_is_never_left_as_the_stated_direction(self) -> None:
        for frame in self._frames():
            for row in frame["feed"]:
                if row["status"] == "rejected":
                    self.assertNotEqual(row["direction"], row["measured_direction"])

    def test_a_correction_is_never_marked_as_a_planted_fault(self) -> None:
        """Only a first attempt can be the planted error; the fix was measured."""
        for frame in self._frames():
            for row in frame["feed"]:
                if row["kind"] == "correction":
                    self.assertFalse(row["injected"])

    def test_a_grounded_rejection_carries_a_receipt_with_both_sides(self) -> None:
        """The overlay shows this line, so both numbers have to be in it."""
        rejections = [
            r
            for f in self._frames()
            for r in f["feed"]
            if r["status"] == "rejected" and r["measured_direction"] is not None
        ]
        self.assertTrue(rejections)
        for row in rejections:
            self.assertIn("claimed", row["receipt"])
            self.assertIn("measured", row["receipt"])

    def test_a_rejected_row_is_rendered_from_the_direction_it_claimed(self) -> None:
        """The analyst sees the sentence that would have gone out, not the true one.

        Momentum's sign is home-positive, so the gloss names a side and its direction
        decides which. Asserting the two agree is a real check; asserting the planted
        fault always reads "up" would not be — the narrator inverts whatever was
        measured, so a window where momentum genuinely rose plants a "down".
        """
        # The target is pinned rather than hoped for. Which reading gets inverted is
        # otherwise the strongest one in each window, so the metric planted moves
        # whenever the generator's stream changes; a test that waited for momentum to
        # rank first was asserting a coincidence of one seed's arithmetic.
        doc = build_replay(
            fault="narrator-invert", fault_target="momentum", max_frames=3
        ).to_dict()
        home, away = doc["home"], doc["away"]
        checked = 0
        for frame in doc["frames"]:
            for row in frame["feed"]:
                if row["status"] != "rejected" or row["metric"] != "momentum":
                    continue
                self.assertEqual(row["direction"], row["claimed_direction"])
                expected = home if row["direction"] == "up" else away
                self.assertIn(expected, row["text"])
                checked += 1
        self.assertEqual(checked, len(doc["frames"]), "every window should plant momentum")

    def test_a_pinned_target_is_planted_even_when_it_is_not_the_biggest_move(self) -> None:
        """Asking for a fault has to produce it, or the demo reports a failure it did
        not plant — which would make its own catch rate unfalsifiable."""
        doc = build_replay(
            fault="narrator-invert", fault_target="momentum", max_frames=2
        ).to_dict()
        self.assertEqual(doc["injected"], ["momentum"])
        for frame in doc["frames"]:
            self.assertTrue(
                any(
                    row["status"] == "rejected" and row["metric"] == "momentum"
                    for row in frame["feed"]
                ),
                f"window {frame['window']} planted no momentum fault",
            )

    def test_a_target_with_no_fault_to_apply_to_is_refused(self) -> None:
        """Otherwise the request would succeed and quietly do nothing."""
        with self.assertRaises(ValueError) as caught:
            build_replay(fault="none", fault_target="momentum", max_frames=1)
        self.assertIn("narrator-invert", str(caught.exception))

    def test_a_fault_target_is_not_required(self) -> None:
        """Pinning is opt-in: without one the demo plants faults exactly as it used
        to, and each is caught and labelled.

        Which *metric* gets planted is deliberately not asserted here. That is whatever
        moved most in the window, so asserting a particular one would be asserting a
        property of one seed's arithmetic — the assumption that broke when the
        generator's stream changed. It is pinned instead, and the default ranking is
        checked against a controlled snapshot in ``tests/test_demo.py``.
        """
        plain = build_replay(fault="narrator-invert", max_frames=4).to_dict()
        self.assertTrue(plain["injected"])
        rejected = [
            row for frame in plain["frames"] for row in frame["feed"]
            if row["status"] == "rejected"
        ]
        self.assertTrue(rejected)
        self.assertTrue(any(row["injected"] for row in rejected))

    def test_only_a_first_attempt_can_be_a_planted_fault(self) -> None:
        """A correction was measured, so labelling it planted would claim the system
        caught something it then invented itself."""
        doc = build_replay(
            fault="narrator-invert", fault_target="momentum", max_frames=4
        ).to_dict()
        rows = [row for f in doc["frames"] for row in f["feed"] if row["injected"]]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["status"], "rejected")
            self.assertNotEqual(row["kind"], "correction")


class TestTheScoreboardMatchesThePayload(unittest.TestCase):
    """The score is rebuilt in the browser, so it is checked here instead."""

    def test_the_goal_list_reaches_the_final_score(self) -> None:
        doc = build_replay(fault="none", max_frames=6).to_dict()
        final = doc["frames"][-1]
        home = sum(1 for g in doc["goals"] if g["team"] == "home")
        away = sum(1 for g in doc["goals"] if g["team"] == "away")
        self.assertEqual([home, away], final["score"])

    def test_no_goal_is_placed_after_the_replay_ends(self) -> None:
        doc = build_replay(fault="none", max_frames=6).to_dict()
        end = doc["frames"][-1]["clock"]
        self.assertTrue(all(g["clock"] <= end for g in doc["goals"]))

    def test_the_score_never_goes_down(self) -> None:
        frames = build_replay(fault="none", max_frames=8).to_dict()["frames"]
        for previous, current in zip(frames, frames[1:]):
            self.assertGreaterEqual(current["score"][0], previous["score"][0])
            self.assertGreaterEqual(current["score"][1], previous["score"][1])

    def test_reaching_the_final_score_needs_a_goal_list(self) -> None:
        """Guards the reason the list exists: incidents are capped for legibility, so
        a score derived from them would silently under-count."""
        doc = replay_doc()
        capped = sum(1 for f in doc["frames"] for i in f["incidents"] if i["type"] == "goal")
        self.assertLessEqual(capped, len(doc["goals"]) * 4)


class TestGoalHighlightsReachThePage(unittest.TestCase):
    """The build-up panel can only open if the payload can pair its two lists.

    The page keys a highlight by ``(clock, side, scorer)``, because the goal list is
    flattened to incidents and carries no id of its own. If that key were ever
    ambiguous, or if the two lists covered different goals, the panel would open the
    wrong move — or refuse to open at all — and nothing in Python would notice.
    """

    def _doc(self, **overrides: object) -> dict:
        kwargs: dict = {"seed": 7, "fault": "none", "max_frames": 6}
        kwargs.update(overrides)
        return build_replay(**kwargs).to_dict()

    def test_every_goal_has_a_highlight_keyed_the_same_way(self) -> None:
        doc = self._doc()
        goals = {(g["clock"], g["team"], g["player"]) for g in doc["goals"]}
        highlights = {(h["clock"], h["team"], h["scorer"]) for h in doc["highlights"]}
        self.assertEqual(goals, highlights)

    def test_the_key_is_unambiguous_across_a_whole_match(self) -> None:
        """One player cannot score twice on the same second for the same side."""
        for seed in (1, 7, 11, 42):
            with self.subTest(seed=seed):
                highlights = build_replay(seed=seed, fault="none").to_dict()["highlights"]
                keys = [(h["clock"], h["team"], h["scorer"]) for h in highlights]
                self.assertEqual(len(keys), len(set(keys)))

    def test_the_two_lists_cover_the_same_goals_in_the_same_order(self) -> None:
        doc = self._doc()
        self.assertEqual(
            [g["clock"] for g in doc["goals"]],
            [h["clock"] for h in doc["highlights"]],
        )

    def test_no_highlight_is_for_a_goal_the_replay_never_reaches(self) -> None:
        """A shortened replay must not offer a build-up to a goal it never scores: the
        row would never appear, and the highlight would be unreachable payload."""
        doc = self._doc()
        end = doc["frames"][-1]["clock"]
        self.assertTrue(all(h["clock"] <= end for h in doc["highlights"]))

    def test_every_highlight_states_its_provenance(self) -> None:
        """The panel renders these labels and composes none of its own wording, so a
        highlight that arrived without one would be shown with no provenance at all."""
        doc = build_replay(seed=7, fault="none").to_dict()
        self.assertTrue(doc["highlights"])
        for highlight in doc["highlights"]:
            with self.subTest(goal=highlight["goal_id"]):
                self.assertIn("synthetic match", highlight["labels"])

    def test_a_highlight_survives_the_json_round_trip(self) -> None:
        doc = self._doc()
        self.assertEqual(json.loads(json.dumps(doc))["highlights"], doc["highlights"])


class TestCommentary(unittest.TestCase):
    """The renderer must not invent. These are the tests that hold that line."""

    def test_every_registered_metric_has_a_gloss_in_both_directions(self) -> None:
        """A new metric in the registry must not silently lose its wording — and must
        never fall through to a gloss it was not given."""
        for metric in METRIC_SPECS:
            for direction in ("up", "down"):
                with self.subTest(metric=metric, direction=direction):
                    unglossed = f"{metric.replace('_', ' ')} moved {direction}."
                    sentence = compose(
                        metric, direction, team="home", home="H", away="A"
                    )
                    self.assertNotEqual(sentence, unglossed)

    def test_an_unregistered_metric_states_the_measurement_and_stops(self) -> None:
        self.assertEqual(
            compose("vibes", "up", team=None, home="H", away="A"),
            "vibes moved up.",
        )

    def test_the_two_directions_read_differently_for_every_metric(self) -> None:
        """If a gloss ignored its direction the page could show a claim and its
        opposite in identical words."""
        for metric in METRIC_SPECS:
            up = compose(metric, "up", team="home", home="H", away="A")
            down = compose(metric, "down", team="home", home="H", away="A")
            self.assertNotEqual(up, down, metric)

    def test_momentum_names_the_side_the_balance_tipped_to(self) -> None:
        """Momentum is signed home-positive, so "up" is a statement about a side."""
        up = compose("momentum", "up", team=None, home="Northgate", away="Riverside")
        down = compose("momentum", "down", team=None, home="Northgate", away="Riverside")
        self.assertIn("Northgate", up)
        self.assertNotIn("Riverside", up)
        self.assertIn("Riverside", down)

    def test_a_team_metric_names_the_team_it_was_given(self) -> None:
        sentence = compose(
            "pass_accuracy", "down", team="away", home="H", away="A"
        )
        self.assertIn("A", sentence)
        self.assertNotIn("H", sentence)

    def test_the_incident_clause_is_optional(self) -> None:
        self.assertEqual(incident_clause("pass", "Someone", 12), "")
        self.assertEqual(compose("momentum", "up", team=None, home="H", away="A"), 
                         compose("momentum", "up", team=None, home="H", away="A", incident=""))

    def test_the_incident_clause_states_an_event_rather_than_a_cause(self) -> None:
        clause = incident_clause("goal", "Brendahl", 63)
        self.assertIn("Brendahl", clause)
        self.assertIn("63", clause)
        for causal in ("because", "after", "led to", "thanks to"):
            self.assertNotIn(causal, clause)

    def test_the_incident_clause_appears_at_most_once_per_window(self) -> None:
        """It rode on every line in an earlier version and the feed read like a merge."""
        doc = build_replay(fault="narrator-invert", max_frames=6).to_dict()
        for frame in doc["frames"]:
            carriers = [r for r in frame["feed"] if "spell also brought" in r["text"]]
            self.assertLessEqual(len(carriers), 1)

    def test_the_carrier_of_the_incident_clause_is_a_published_line(self) -> None:
        """Otherwise the fan view, which withholds rejected lines, would show none."""
        doc = build_replay(fault="narrator-invert", max_frames=6).to_dict()
        for frame in doc["frames"]:
            carriers = [r for r in frame["feed"] if "spell also brought" in r["text"]]
            for row in carriers:
                self.assertEqual(row["status"], "verified")


@requires_server
class TestServerShell(unittest.TestCase):
    """The HTTP contract. Everything here was decided before the request arrived."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def test_health_reports_the_available_faults(self) -> None:
        body = self.client.get("/api/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["faults"], list(FAULT_MODES))

    def test_the_front_door_is_the_catalogue_and_the_feed_is_beside_it(self) -> None:
        """Two pages, two paths, one home each — and the check is which is *where*, because
        both are HTML and both return 200. A swap would show a reader one unpicked match
        instead of the choice, and nothing about the response would say so."""
        front = self.client.get("/")
        self.assertEqual(front.status_code, 200)
        self.assertIn("text/html", front.headers["content-type"])
        self.assertIn("data-mode-browser", front.text)

        feed = self.client.get("/match")
        self.assertEqual(feed.status_code, 200)
        self.assertIn("text/html", feed.headers["content-type"])
        self.assertIn('id="hl"', feed.text)

    def test_the_page_loads_its_own_assets(self) -> None:
        """A page whose stylesheet 404s still renders, and looks broken to a judge."""
        for asset in ("/static/styles.css", "/static/app.js", "/static/mode-browser.js"):
            with self.subTest(asset=asset):
                self.assertEqual(self.client.get(asset).status_code, 200)

    def test_nothing_served_may_be_kept_by_the_browser(self) -> None:
        """A browser holding yesterday's ``app.js`` makes every fix invisible.

        That is the same failure as a process holding yesterday's modules — the page
        keeps drawing the old build-up panel and nothing anywhere says 503 — so the
        assets are served unstored, and so is the payload they are drawn from.
        """
        for path in (
            "/",
            "/match",
            "/static/app.js",
            "/static/styles.css",
            "/api/replay?max_frames=1",
            "/api/scenarios",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_the_build_up_panel_is_in_the_page(self) -> None:
        """The panel is the deliverable, so a page that lost its controls should fail
        here rather than being discovered in a browser."""
        body = self.client.get("/match").text
        for marker in ('id="hl"', 'id="hl-svg"', 'id="hl-replay"', "data-hl-speed", 'id="hl-labels"'):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)

    def test_the_page_reads_the_parameters_the_table_documents(self) -> None:
        """The parameter table and the page are one contract written in two places.

        A parameter documented but never read is a link that quietly does nothing; one
        read but undocumented is a link nobody can construct. Both read as "sharing is
        broken", and neither is visible from inside either file, so the two are compared
        against each other here.
        """
        page = self.client.get("/static/app.js").text
        read_by_the_page = set(re.findall(r'params\.get\("([a-z_]+)"\)', page))

        docs = (PROJECT_ROOT / "docs" / "overlay.md").read_text(encoding="utf-8")
        section = docs.split("## Query parameters", 1)[1].split("\n## ", 1)[0]
        documented = set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, re.M))

        self.assertEqual(read_by_the_page, documented)
        # Neither set may be empty, or the equality above would hold for the wrong reason.
        self.assertIn("seed", documented)
        self.assertIn("goal", documented)

    def test_the_replay_endpoint_returns_the_document(self) -> None:
        body = self.client.get("/api/replay?fault=none&max_frames=2").json()
        self.assertEqual(len(body["frames"]), 2)
        self.assertIn("provider", body)

    def test_the_replay_endpoint_repeats_itself(self) -> None:
        first = self.client.get("/api/replay?seed=3&fault=none&max_frames=2").json()
        second = self.client.get("/api/replay?seed=3&fault=none&max_frames=2").json()
        self.assertEqual(first, second)

    def test_the_served_replay_is_a_fresh_generation_of_the_same_seed(self) -> None:
        """The endpoint is one seed's payload and nothing else.

        A parameter dropped while mapping the query string onto ``build_replay``, or a
        document served from the wrong key in the cache, would look exactly the same
        on screen as the right one — the scoreboard, the feed and the build-up panel
        would all still draw something plausible.
        """
        for seed in (7, 12):
            with self.subTest(seed=seed):
                served = self.client.get(
                    f"/api/replay?seed={seed}&fault=none&max_frames=3"
                ).json()
                fresh = build_replay(seed, "PF-001", fault="none", max_frames=3).to_dict()
                # Round-tripped so the comparison is about the content and not about
                # whether ``to_dict`` happened to leave a tuple behind.
                self.assertEqual(served, json.loads(json.dumps(fresh)))

    def test_a_server_whose_code_has_moved_on_refuses_to_serve(self) -> None:
        """Old numbers are worse than no numbers.

        The real failure this guards: the generator was fixed and the suite passed,
        while the running process kept answering with the stream it had imported
        earlier — so the build-up panel still drew the ball returning to where a shot
        was struck, and the fix was invisible to the person it was made for.

        The page already surfaces the server's ``detail`` when a request fails, so the
        reason reaches the reader instead of a silent old payload.
        """
        from pitchframe.overlay import server as server_module
        from pitchframe.overlay.stale import CodeState

        moved_on = CodeState(
            loaded="aaa", current="bbb", changed=("pitchframe/generator.py",)
        )
        with mock.patch.object(server_module, "code_state", return_value=moved_on):
            response = self.client.get("/api/replay?seed=7&fault=none&max_frames=1")

        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertIn("pitchframe/generator.py", detail)
        self.assertIn("Restart", detail)

    def test_health_reports_which_code_is_serving(self) -> None:
        """The probe answers the question that matters here: does the process agree
        with the files on disk?"""
        code = self.client.get("/api/health").json()["code"]
        self.assertEqual(code["loaded"], code["current"])
        self.assertFalse(code["stale"])
        self.assertEqual(code["changed"], [])

    def test_an_unknown_fault_is_a_named_400(self) -> None:
        response = self.client.get("/api/replay?fault=whatever")
        self.assertEqual(response.status_code, 400)
        self.assertIn("unknown fault", response.json()["detail"])

    def test_an_absurd_window_count_is_refused(self) -> None:
        """Otherwise a crafted query asks the server to measure without bound."""
        self.assertEqual(self.client.get("/api/replay?max_frames=9999").status_code, 400)
        self.assertEqual(self.client.get("/api/replay?max_frames=0").status_code, 400)
        self.assertEqual(
            self.client.get(f"/api/replay?max_frames={MAX_WINDOWS}").status_code, 200
        )

    def test_a_non_positive_window_is_refused(self) -> None:
        self.assertEqual(self.client.get("/api/replay?window_seconds=0").status_code, 400)
        self.assertEqual(self.client.get("/api/replay?seed=-1").status_code, 400)


class _Reply:
    """Just enough of an HTTP response for the readiness probe to read."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_Reply":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self.body


class TestOpeningItAgain(unittest.TestCase):
    """What a second start finds on the port, and what it does about it.

    The launcher is a double-click, and a double-click cannot be asked a question — so every
    state it can find has to be handled where it is found rather than by the reader. Starting
    a second server regardless would fail with ``address already in use``, which says nothing
    about what is actually wrong; opening a browser onto a server that has outlived its files
    would show a page whose own error message is the fix. These are the four states and the
    three of them that are not "start one".
    """

    def test_the_probe_tells_the_four_states_apart(self) -> None:
        """The distinction the whole launcher rests on. A connection refused and a wrong
        application on the port are both 'not our server', and they need different answers:
        one means start one, the other means move to another port."""
        from pitchframe import __main__ as cli

        healthy = b'{"status": "ok", "code": {"stale": false}}'
        stale = b'{"status": "ok", "code": {"stale": true}}'

        with mock.patch("urllib.request.urlopen", return_value=_Reply(healthy)):
            self.assertEqual(cli.serving_here("127.0.0.1", 8000), "ok")
        with mock.patch("urllib.request.urlopen", return_value=_Reply(stale)):
            self.assertEqual(cli.serving_here("127.0.0.1", 8000), "stale")
        with mock.patch("urllib.request.urlopen", return_value=_Reply(b"<html>not us</html>")):
            self.assertEqual(cli.serving_here("127.0.0.1", 8000), "taken")
        with mock.patch("urllib.request.urlopen", return_value=_Reply(b'{"hello": 1}')):
            self.assertEqual(cli.serving_here("127.0.0.1", 8000), "taken")
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "no", None, None),
        ):
            self.assertEqual(cli.serving_here("127.0.0.1", 8000), "taken")
        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("refused")
        ):
            self.assertIsNone(cli.serving_here("127.0.0.1", 8000))

    @requires_server
    def test_a_second_start_opens_the_one_already_there(self) -> None:
        """The case a reader will hit most: they bookmarked the catalogue and ran the
        launcher again. It must open the browser and leave the running server alone — not
        start a second one, and not replace the first."""
        from pitchframe import __main__ as cli

        args = argparse.Namespace(host="127.0.0.1", port=8000, inject_fault="none", open=True)
        printed = io.StringIO()
        with mock.patch.object(cli, "serving_here", return_value="ok"), mock.patch.object(
            cli.webbrowser, "open"
        ) as opened, mock.patch("uvicorn.run") as served, contextlib.redirect_stdout(printed):
            code = cli._run_overlay(args, 28, "matchday")

        self.assertEqual(code, 0)
        served.assert_not_called()
        opened.assert_called_once_with("http://127.0.0.1:8000/")

    @requires_server
    def test_a_stale_server_is_neither_served_over_nor_opened(self) -> None:
        """The one state where opening a browser would be actively misleading: the page
        would load, look right, and refuse every request with a 503 whose own text is the
        instruction. Saying it here, in the terminal the reader is looking at, is the same
        message one step earlier."""
        from pitchframe import __main__ as cli

        args = argparse.Namespace(host="127.0.0.1", port=8000, inject_fault="none", open=True)
        printed = io.StringIO()
        with mock.patch.object(cli, "serving_here", return_value="stale"), mock.patch.object(
            cli.webbrowser, "open"
        ) as opened, mock.patch("uvicorn.run") as served, contextlib.redirect_stdout(printed):
            code = cli._run_overlay(args, 28, "matchday")

        self.assertEqual(code, 2)
        served.assert_not_called()
        opened.assert_not_called()
        self.assertIn("older than the files", printed.getvalue())
        self.assertIn("--overlay", printed.getvalue())

    @requires_server
    def test_a_port_taken_by_something_else_names_the_flag_that_changes_it(self) -> None:
        """A reader with another service on 8000 needs one fact: how to move. A traceback
        from ``uvicorn`` names the port and not the way out."""
        from pitchframe import __main__ as cli

        args = argparse.Namespace(host="127.0.0.1", port=8000, inject_fault="none", open=True)
        printed = io.StringIO()
        with mock.patch.object(cli, "serving_here", return_value="taken"), mock.patch(
            "uvicorn.run"
        ) as served, contextlib.redirect_stdout(printed):
            code = cli._run_overlay(args, 28, "matchday")

        self.assertEqual(code, 2)
        served.assert_not_called()
        self.assertIn("--port", printed.getvalue())

    @requires_server
    def test_an_empty_port_starts_a_server_and_waits_before_opening(self) -> None:
        """The only state that serves: nothing on the port, so this call is the one that
        becomes it — and the browser is handed to the readiness wait rather than to
        ``uvicorn``, because a tab that loads loopback before the socket is listening shows
        a connection error and leaves the reader reloading."""
        from pitchframe import __main__ as cli

        args = argparse.Namespace(host="127.0.0.1", port=8000, inject_fault="none", open=True)
        printed = io.StringIO()
        with mock.patch.object(cli, "serving_here", return_value=None), mock.patch.object(
            cli, "open_when_ready"
        ) as waiting, mock.patch("uvicorn.run") as served, contextlib.redirect_stdout(printed):
            code = cli._run_overlay(args, 28, "matchday")

        self.assertEqual(code, 0)
        served.assert_called_once()
        waiting.assert_called_once_with("http://127.0.0.1:8000/", "127.0.0.1", 8000)
        # Both addresses, because they are two pages: the catalogue and this match.
        self.assertIn("http://127.0.0.1:8000/", printed.getvalue())
        self.assertIn("/match/matchday", printed.getvalue())

    def test_the_browser_opens_only_once_something_answers(self) -> None:
        """Polls until the server is up, then opens — not before. The count matters as much
        as the call: opening on the first probe is exactly the race this exists to avoid."""
        from pitchframe import __main__ as cli

        answers = [None, None, "ok"]
        seen = []

        def probe(host: str, port: int, timeout: float = 2.0) -> str | None:
            seen.append((host, port))
            return answers.pop(0) if answers else "ok"

        with mock.patch.object(cli, "serving_here", side_effect=probe), mock.patch.object(
            cli.webbrowser, "open"
        ) as opened, mock.patch.object(cli.time, "sleep"):
            thread = cli.open_when_ready("http://127.0.0.1:8000/", "127.0.0.1", 8000, timeout=5)
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive(), "the wait never returned")
        self.assertEqual(len(seen), 3)
        opened.assert_called_once_with("http://127.0.0.1:8000/")

    def test_a_server_that_never_answers_says_so_instead_of_opening_a_dead_tab(self) -> None:
        """Giving up is a state to report, not a silence: a reader whose server failed to
        start needs the line that says the browser was not opened, or they will wait for a
        tab that is never coming."""
        from pitchframe import __main__ as cli

        printed = io.StringIO()
        with mock.patch.object(cli, "serving_here", return_value=None), mock.patch.object(
            cli.webbrowser, "open"
        ) as opened, mock.patch.object(cli.time, "sleep"), contextlib.redirect_stdout(printed):
            thread = cli.open_when_ready(
                "http://127.0.0.1:8000/", "127.0.0.1", 8000, timeout=0.3
            )
            thread.join(timeout=5)

        opened.assert_not_called()
        self.assertIn("was not opened", printed.getvalue())


@requires_server
class TestTheNamedMatches(unittest.TestCase):
    """A match's address says which match it is, and the old ones still open it.

    The seed is the whole of what a replay is a function of, which is why it was the only
    thing the URL needed to carry. It is also unreadable: ``/match?seed=7`` says nothing
    about the match behind it, cannot be typed from memory, and cannot be recognised in a
    list of links — and the project has a registry of named matches precisely because a
    seed is not a story. So the name is in the path and the seed is what the name resolves
    to. The failure this class is about is quiet and expensive: a link written before the
    change that stops working, or a name that silently opens a different match than the one
    it names, which looks exactly like a match nobody wanted.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def test_every_named_match_has_an_address_that_serves_exactly_it(self) -> None:
        """One address per match, and the page it serves is the page for that match.

        Checked against the replay endpoint rather than against the page's own words: the
        catalogue promises a name, and what has to be true is that the name and the number
        build the same match — the same seed, the same goals, the same score — through the
        API the page itself calls.
        """
        entries = self.client.get("/api/scenarios").json()["scenarios"]
        for entry in entries:
            with self.subTest(key=entry["key"]):
                self.assertEqual(entry["path"], f"/match/{entry['key']}")
                page = self.client.get(entry["path"])
                self.assertEqual(page.status_code, 200)
                self.assertIn("text/html", page.headers["content-type"])
                by_name = self.client.get(f"/api/replay?scenario={entry['key']}").json()
                by_number = self.client.get(f"/api/replay?seed={entry['seed']}").json()
                self.assertEqual(by_name, by_number)
                self.assertEqual(by_name["seed"], entry["seed"])
                self.assertEqual(len(by_name["goals"]), entry["goals"])

    def test_a_numbered_link_lands_on_the_name_of_that_match(self) -> None:
        """A link handed out before matches had names is still a working link, and what it
        lands on is the readable address for the same match. Everything else in the query
        travels: those parameters describe the frame, not the match."""
        response = self.client.get("/match?seed=7", follow_redirects=False)
        self.assertIn(response.status_code, (307, 308))
        self.assertEqual(response.headers["location"], "/match/quiet")
        self.assertEqual(response.headers.get("cache-control"), "no-store")

        framed = self.client.get(
            "/match?seed=28&at=3615&view=analyst&fault=narrator-invert",
            follow_redirects=False,
        )
        self.assertEqual(
            framed.headers["location"],
            "/match/matchday?at=3615&view=analyst&fault=narrator-invert",
        )
        followed = self.client.get(framed.headers["location"])
        self.assertEqual(followed.status_code, 200)
        self.assertIn("text/html", followed.headers["content-type"])

    def test_a_seed_no_scenario_owns_is_still_a_match(self) -> None:
        """The point of `--seed` is that any seed at all works. A number with no name has no
        name to be redirected to, so it is served exactly as it always was — and the page
        then carries the number, because it is the only description of that match there
        is."""
        page = self.client.get("/match?seed=99")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        document = self.client.get("/api/replay?seed=99").json()
        self.assertEqual(document["seed"], 99)

    def test_a_bare_match_goes_to_the_default_by_name(self) -> None:
        """`/match` used to be the default scenario and nothing said so. One address per
        match means the default has an address too."""
        response = self.client.get("/match", follow_redirects=False)
        self.assertIn(response.status_code, (307, 308))
        self.assertEqual(response.headers["location"], f"/match/{DEFAULT_SCENARIO_KEY}")
        self.assertEqual(self.client.get(response.headers["location"]).status_code, 200)

    def test_the_frame_survives_the_move_to_the_default_s_name(self) -> None:
        """What a reader shares is the query, and a redirect that dropped it would answer
        with a different frame than the one asked for — the same match at minute zero, in
        the fan view, with no build-up open.

        The frame parameters are exactly the ones the page itself writes into the URL, so
        this is the address a copy of the address bar produces.
        """
        response = self.client.get(
            "/match?view=analyst&at=3615&goal=12-26-home&fault=narrator-invert",
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (307, 308))
        self.assertEqual(
            response.headers["location"],
            f"/match/{DEFAULT_SCENARIO_KEY}"
            "?view=analyst&at=3615&goal=12-26-home&fault=narrator-invert",
        )
        followed = self.client.get(response.headers["location"])
        self.assertEqual(followed.status_code, 200)
        self.assertIn("text/html", followed.headers["content-type"])

    def test_a_named_scenario_gets_the_address_that_names_it(self) -> None:
        """`/match?scenario=chaos` used to be redirected to the default's name, which
        dropped the name and quietly opened a different match — the one failure this
        project treats as indistinguishable from a match nobody wanted."""
        response = self.client.get("/match?scenario=quiet&at=600", follow_redirects=False)
        self.assertIn(response.status_code, (307, 308))
        self.assertEqual(response.headers["location"], "/match/quiet?at=600")
        self.assertEqual(self.client.get(response.headers["location"]).status_code, 200)

        # The default's own name, asked for the long way round, lands on itself and keeps
        # the frame rather than losing it to a second redirect.
        same = self.client.get(
            f"/match?scenario={DEFAULT_SCENARIO_KEY}&view=analyst",
            follow_redirects=False,
        )
        self.assertEqual(same.headers["location"], f"/match/{DEFAULT_SCENARIO_KEY}?view=analyst")

    def test_an_unknown_name_is_not_answered_with_a_different_match(self) -> None:
        """The page is served so the replay endpoint's refusal can be shown on it: 400 with
        the names there are. A redirect to the default would look like a working link."""
        page = self.client.get("/match?scenario=machday", follow_redirects=False)
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        refused = self.client.get("/api/replay?scenario=machday")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("machday", refused.json()["detail"])

    def test_a_name_and_a_number_on_one_address_are_refused(self) -> None:
        """They can name different matches, and the replay endpoint already refuses the
        pair. Redirecting on one of them would make the address bar the coin toss."""
        response = self.client.get("/match?scenario=quiet&seed=28", follow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertIn("not both", response.json()["detail"])

    def test_a_name_that_does_not_exist_is_a_named_404(self) -> None:
        """Refused rather than quietly redirected to the default: a typo that served a
        different match would look like a match nobody wanted, and the reader would have no
        way to tell which of the two happened."""
        response = self.client.get("/match/machday")
        self.assertEqual(response.status_code, 404)
        detail = response.json()["detail"]
        self.assertIn("machday", detail)
        for key in ALL_SCENARIOS:
            with self.subTest(key=key):
                self.assertIn(key, detail)
        self.assertIn("/match?seed=", detail, "the reader is not told the other way in")

    def test_asking_for_a_name_and_a_number_at_once_is_refused(self) -> None:
        """They can name different matches, and answering one of them would be a coin toss
        the caller never sees. An unknown name is refused with the names there are."""
        both = self.client.get("/api/replay?scenario=quiet&seed=28")
        self.assertEqual(both.status_code, 400)
        self.assertIn("not both", both.json()["detail"])
        unknown = self.client.get("/api/replay?scenario=machday")
        self.assertEqual(unknown.status_code, 400)
        self.assertIn("machday", unknown.json()["detail"])
        self.assertIn(DEFAULT_SCENARIO_KEY, unknown.json()["detail"])

    def test_the_page_reads_the_name_out_of_the_path(self) -> None:
        """The path is where the match is, and the query is the frame. A page that read the
        name from a query parameter would write it back into one, and the address would
        stop being shareable as it stands."""
        app = self.client.get("/static/app.js").text
        self.assertIn('location.pathname.split("/")', app)
        self.assertIn("parts[0] === \"match\"", app)
        self.assertIn("scenario=${encodeURIComponent(query.scenario)}", app)
        # The seed goes in a numbered link and not in a named one: the name is the seed.
        self.assertIn("if (!query.scenario) {", app)


@requires_server
class TestTheSeaBehindThePage(unittest.TestCase):
    """The background is scenery, so none of it can be asserted by reading a number.

    What can be asserted is the wiring that keeps it scenery, and each check here exists
    because the failure it catches looks like nothing at all: a module served with the
    wrong content type never executes, a background that is not inert eats the page's
    clicks, a colour borrowed from the verdict palette makes the one signal the product
    exists for ambiguous, and an import from a CDN turns a venue without wifi into a demo
    with no background and no explanation.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def test_the_module_and_its_dependency_are_served_as_javascript(self) -> None:
        for asset in ("/static/slats.js", "/static/vendor/ogl.mjs"):
            with self.subTest(asset=asset):
                response = self.client.get(asset)
                self.assertEqual(response.status_code, 200)
                # A module with a non-JavaScript content type is refused by the browser,
                # which shows up as a page whose background silently never appears.
                self.assertIn("javascript", response.headers["content-type"])

    def test_the_page_imports_it_as_a_module_into_a_marked_container(self) -> None:
        body = self.client.get("/match").text
        self.assertIn("data-slats", body)
        self.assertIn('type="module"', body)
        self.assertIn("/static/slats.js", body)

    def test_no_script_comes_from_another_origin(self) -> None:
        """The overlay is shown in rooms without wifi, so the one dependency ships with
        the page rather than being fetched at load."""
        body = self.client.get("/match").text
        sources = re.findall(r'<script[^>]*\bsrc="([^"]+)"', body)
        self.assertTrue(sources, "the page loads no scripts at all")
        for src in sources:
            with self.subTest(src=src):
                self.assertTrue(src.startswith("/"), f"{src} is not served from this host")

    def test_the_sea_is_inert(self) -> None:
        """A background that can take a click takes the controls with it."""
        css = self.client.get("/static/styles.css").text
        rule = re.search(r"\.slats\s*\{(.*?)\}", css, re.S)
        self.assertIsNotNone(rule, "no .slats rule in the stylesheet")
        self.assertIn("pointer-events: none", rule.group(1))

    def test_the_sea_is_not_painted_in_a_verdict_colour(self) -> None:
        """Green and vermilion mean published and caught here. Scenery borrowing either
        would make the page's one signal ambiguous."""
        css = self.client.get("/static/styles.css").text
        tokens = dict(re.findall(r"(--[a-z-]+):\s*([^;]+);", css))
        self.assertIn("--slats", tokens)
        self.assertIn("--slats-glint", tokens)
        for reserved in ("--verify", "--flag", "--amber", "--home", "--away"):
            with self.subTest(reserved=reserved):
                self.assertNotEqual(
                    tokens["--slats"].strip().lower(), tokens.get(reserved, "").strip().lower()
                )

    def test_the_sea_freezes_for_reduced_motion(self) -> None:
        """The page already stops its own animation for that setting. A background that
        sampled it once at load would be the one thing still moving after a reader turned
        it on, and the module is the only place that can honour it."""
        module = self.client.get("/static/slats.js").text
        self.assertIn("prefers-reduced-motion", module)
        self.assertIn('addEventListener("change"', module)

    def test_the_sea_asks_for_a_context_before_building_one(self) -> None:
        """A browser with WebGL turned off gets the plain night and nothing else.

        This used to be a guard *after* the renderer was built, and it was unreachable:
        `ogl` logs `unable to create webgl context` and then dereferences the missing
        context, so the constructor throws before the guard runs and the reader gets an
        exception in the console instead of a background. The port asks first, which also
        keeps the dependency's own error off a page that is working correctly, and catches
        the case where the question and the answer disagree — a page that has used up the
        browser's context budget passes the probe and still has no renderer.
        """
        module = self.client.get("/static/slats.js").text
        probe = module.index('probe.getContext("webgl2")')
        constructor = module.index("new Renderer(")
        self.assertLess(probe, constructor, "a renderer is built before anything asked for a context")
        self.assertIn("try {", module[constructor - 200:constructor], "the constructor can throw and is not guarded")
        # And the guard that follows still has to hold, for the probe that said yes to a
        # context the real canvas could not have.
        self.assertRegex(module, r"if \(!gl \|\| !renderer\.isWebgl2\) \{")
        self.assertIn('getExtension("WEBGL_lose_context")?.loseContext()', module)


@requires_server
class TestThePortedControls(unittest.TestCase):
    """The view switch, the replay speed, the action dock, the match-minutes seek and the
    mode browser's sphere are ports of React Bits components, so what can be asserted from
    here is the wiring that keeps them working.

    Each check exists because the failure it catches looks like nothing at all. A module
    served with the wrong content type never executes. An import pointing at a file nobody
    serves leaves a control that is simply absent. `app.js` loaded as a classic script is a
    syntax error that takes the clock, the feed and every control down together. Two
    controls for one piece of state can disagree on screen with no way for a reader to tell
    which one the page believes. A full-width fixed layer swallows clicks meant for the
    page. A ported file shipped without its upstream notice is a licence breach rather than
    a bug. And none of it is visible from inside either file.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def page(self) -> str:
        """The feed. The controls under test are all on `/match`; the front door is the
        catalogue and none of them exist there."""
        return self.client.get("/match").text

    def asset(self, path: str) -> str:
        return self.client.get(path).text

    def ported_controls_css(self) -> str:
        """The section of the stylesheet that belongs to the controls that are ports, and
        nothing else.

        Bounded deliberately: the verdict colours are used legitimately elsewhere in the
        file, so a search of the whole stylesheet would pass for the wrong reason.
        """
        css = self.asset("/static/styles.css")
        self.assertIn("the ported controls", css, "the ports' stylesheet section is gone")
        section = css.split("the ported controls", 1)[1].split("states ----- */", 1)[0]
        for control in (
            "border-glow",
            ".dock",
            ".rubber-segment",
            ".wake-slider",
            ".sling-button",
            ".infinite-menu",
            ".gooey-nav",
            ".text-pressure",
        ):
            with self.subTest(control=control):
                self.assertIn(control, section)
        return section

    def test_every_module_the_controls_need_is_served_as_javascript(self) -> None:
        for path in (
            "/static/motion.js",
            "/static/border-glow.js",
            "/static/rubber-segment.js",
            "/static/dock.js",
            "/static/wake-slider.js",
            "/static/sling-button.js",
            "/static/infinite-menu.js",
            "/static/mode-browser.js",
            "/static/text-pressure.js",
            "/static/gooey-nav.js",
            "/static/masthead.js",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("javascript", response.headers["content-type"])

    def test_app_is_loaded_as_a_module(self) -> None:
        """It imports the three ports, which a classic script cannot do: the page would lose
        its clock, its feed and its controls in one go."""
        self.assertIn('type="module" src="/static/app.js"', self.page())

    def test_the_imports_are_relative_and_resolve(self) -> None:
        """A control imported from a CDN is one that does not exist without wifi, and a
        mistyped path is one that does not exist at all."""
        seen = 0
        for module in (
            "/static/app.js",
            "/static/border-glow.js",
            "/static/rubber-segment.js",
            "/static/dock.js",
            "/static/wake-slider.js",
            "/static/sling-button.js",
            "/static/infinite-menu.js",
            "/static/mode-browser.js",
            "/static/text-pressure.js",
            "/static/gooey-nav.js",
            "/static/masthead.js",
        ):
            for target in re.findall(r'from "(\./[^"]+)"', self.asset(module)):
                seen += 1
                with self.subTest(module=module, target=target):
                    self.assertEqual(
                        self.client.get("/static/" + target.lstrip("./")).status_code, 200
                    )
        # app.js imports its five controls and the glow it dresses the panels with; each of
        # the eight ports imports the shared runtime; the mode browser imports the sphere it
        # is built on plus the glow its stage wears; and the masthead imports the wordmark
        # and the nav it mounts.
        self.assertEqual(seen, 18)

    def test_the_pressure_type_is_asked_for_an_axis_the_face_has(self) -> None:
        """The effect is `font-variation-settings` written on a face that answers them.

        A stylesheet that asks Google Fonts for three static weights is served a face with
        no axes at all, and every setting the component writes is then accepted and
        ignored: the word is still, the module still runs, and nothing anywhere says the
        effect is not there. So the request is a range on both pages, and the port measures
        the face before it writes anything, reporting a flat word as flat rather than
        animating it against a font that cannot answer.
        """
        for path in ("/", "/match"):
            with self.subTest(page=path):
                self.assertIn("Oswald:wght@200..700", self.asset(path))
        module = self.asset("/static/text-pressure.js")
        self.assertIn("fontVariationSettings", module)
        self.assertIn("measureAxes", module)
        self.assertIn('"flat"', module, "the port cannot say a word is not varying")
        self.assertIn("weightRange", module, "the face's own range is not a caller's choice")

    def test_the_pressure_keeps_one_word_for_a_reader(self) -> None:
        """The letters are spans so each can be varied on its own, which makes them useless
        to a screen reader and to a selection. The visual copy is therefore hidden and one
        unbroken copy is left in the host's own reading order — a wordmark a reader hears
        once and copies once.
        """
        module = self.asset("/static/text-pressure.js")
        self.assertIn('root.setAttribute("aria-hidden", "true")', module)
        self.assertIn("text-pressure__say", module)
        self.assertIn("say.textContent = settings.text", module)

    def test_the_names_on_the_board_are_set_through_the_component(self) -> None:
        """The component owns the letters, so a textContent beside it would leave the
        scoreboard showing one name and the page title another."""
        app = self.asset("/static/app.js")
        self.assertIn("createTextPressure(host", app)
        self.assertIn("teamNames[0].setText(doc.home)", app)
        self.assertNotIn("el.teamHome.textContent", app)
        self.assertNotIn("el.teamAway.textContent", app)
        self.assertIn("data-wordmark", self.page())

    def test_the_nav_is_the_pages_own_links(self) -> None:
        """The way between the two documents has to work without the script, so the links
        are written into the page with their real addresses, and the page marks which of
        them it is. The port reads that markup; it does not keep a list of destinations of
        its own, which could disagree with the routes the server serves.
        """
        for path in ("/", "/match"):
            with self.subTest(page=path):
                body = self.asset(path)
                self.assertIn("data-site-nav", body)
                self.assertIn('href="/"', body)
                self.assertIn('href="/match"', body)
                self.assertIn('aria-current="page"', body)
                self.assertIn('src="/static/masthead.js"', body)
        module = self.asset("/static/gooey-nav.js")
        self.assertIn("[data-nav-list]", module)
        self.assertIn("aria-current", module)
        # A browser with no WebGL2 gets a moving pill rather than the component's static
        # bar: the pill is the navigation's state and the goo is the flourish.
        self.assertIn("gooey-nav__fallback", module)
        # A click with a modifier, a middle click and a link to another origin are the
        # browser's, not the control's.
        self.assertIn("event.metaKey", module)

    def test_the_nav_leaves_on_the_burst_rather_than_after_it(self) -> None:
        """A selection is a navigation, so how long the click is held is the difference
        between a control that feels finished and one that feels broken.

        The address has to be followed while the droplets are still between the items: a
        nav that waited out the whole animation would make every page change cost half a
        second before it even started. So the hold is a share of `animationTime` — and it
        is a share rather than a millisecond constant so that the look at the burst scales
        with the animation the caller asked for. The navigation itself is a seam, so a
        test or an embedded nav can watch a selection without leaving the document.
        """
        module = self.asset("/static/gooey-nav.js")
        self.assertIn("const REVEAL_SHARE", module)
        self.assertIn("settings.animationTime * REVEAL_SHARE", module)
        self.assertIn('typeof options.navigate === "function"', module)
        self.assertNotIn("location.assign(url.href)", module.split("onClick", 1)[1].split("addEventListener", 1)[0])
        share = float(re.search(r"const REVEAL_SHARE = ([\d.]+)", module).group(1))
        self.assertGreater(share, 0, "a reveal of zero is the burst nobody ever sees")
        self.assertLess(share, 0.5, "the click is held for half the burst or more")

    def test_the_masthead_mounts_both_shared_pieces(self) -> None:
        """One module for the two things both documents share, so a page cannot be given a
        wordmark without the nav or the nav without the wordmark."""
        masthead = self.asset("/static/masthead.js")
        self.assertIn("createGooeyNav(", masthead)
        self.assertIn("createTextPressure(", masthead)
        self.assertIn('from "./gooey-nav.js"', masthead)
        self.assertIn('from "./text-pressure.js"', masthead)

    def test_the_page_offers_a_container_and_fills_it(self) -> None:
        """A container nothing writes to is a control that is not there, and the page's own
        markup cannot show that: the div is present either way."""
        body = self.page()
        app = self.asset("/static/app.js")
        for marker, attribute in (
            ("view-seg", 'data-segment="view"'),
            ("speed-seg", 'data-segment="speed"'),
            ("dockrail", "data-dock"),
            ("seek", 'data-wake="seek"'),
        ):
            with self.subTest(marker=marker):
                self.assertIn(attribute, body)
                self.assertIn(f'id="{marker}"', body)
                self.assertIn(f'"{marker}"', app)

    def test_each_segment_reports_its_choice_back_to_the_page(self) -> None:
        """A control constructed without its `onChange` moves and changes nothing: the page
        would look alive and be inert."""
        app = self.asset("/static/app.js")
        self.assertIn("onChange: (value) => setView(value)", app)
        self.assertIn("onChange: (value) => setSpeed(Number(value))", app)

    def test_the_seek_is_a_slider_over_the_match_in_minutes(self) -> None:
        """The range is the match and the grid is a minute of it, so both come from the
        payload: a hard-coded 0–100 range would seek to a position the replay does not have,
        and a grid of anything else is not a minutes control. The old range input has to be
        gone with it — two controls for one position can disagree with nothing on screen to
        say which one the page believes."""
        app = self.asset("/static/app.js")
        self.assertIn("SEEK_STEP_SECONDS = 60", app)
        self.assertIn("step: SEEK_STEP_SECONDS", app)
        self.assertIn("min: state.minClock", app)
        self.assertIn("max: state.maxClock", app)
        body = self.page()
        self.assertNotIn('type="range"', body)
        self.assertNotIn("transport__seek input", self.asset("/static/styles.css"))

    def test_the_seek_is_given_a_width_it_can_draw_in(self) -> None:
        """The bars are laid out with a gap between them, so a host sized by its own
        contents is a track of gaps and nothing else: 63 gaps of two pixels are 126px,
        which is exactly what the clock's `auto` column handed this control, so every bar
        measured zero — the seek measured, focused, tabbed and announced itself while
        drawing nothing on screen. The row carrying it spans the match bar instead, and
        the bars are then sized from the board rather than from that row's contents."""
        css = self.asset("/static/styles.css")
        self.assertRegex(
            css,
            r"\.transport \{[^}]*grid-column: 1 / -1",
            "the transport is sized by its own contents again, and a 64-bar track collapses in it",
        )

    def test_the_slider_is_the_control_it_claims_to_be(self) -> None:
        """The wake is decoration over something a reader has to be able to operate and to
        hear: a real slider role, its range, its position, and the same formatted value in
        the readout as in the announced one."""
        source = self.asset("/static/wake-slider.js")
        for marker in (
            'setAttribute("role", "slider")',
            '"aria-valuemin"',
            '"aria-valuemax"',
            '"aria-valuenow"',
            '"aria-valuetext"',
            "event.key === \"Home\"",
            "PageUp",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_a_seek_forwards_is_an_append(self) -> None:
        """Rebuilding the feed for every pointer move is what made the old control stutter,
        and the wake is drawn a frame at a time: a seek that only moves forwards adds the
        rows it is missing, and only a seek backwards is allowed to wipe and rebuild."""
        app = self.asset("/static/app.js")
        self.assertRegex(app, r"if \(target < state\.clock\) \{\s*resetTo\(target\);")
        self.assertIn("revealTo(target, { animateScroll: false })", app)
        # One write for the batch of new rows, and one check panel for the newest claim.
        self.assertIn('insertAdjacentHTML("beforeend", batch)', app)
        self.assertNotIn('insertAdjacentHTML("beforeend", markup)', app)

    def test_a_seek_does_not_need_a_frame_to_happen(self) -> None:
        """A window that is not being composited draws no frames at all, so a reaction queued
        on one would silently never run — a drag that moves the handle and leaves the page
        where it was. The burst is still coalesced, but as a task."""
        app = self.asset("/static/app.js")
        self.assertIn("seekQueued = window.setTimeout(", app)
        self.assertNotIn("requestAnimationFrame(() => {\n          seekFrame", app)

    def test_the_analyst_column_is_one_pinned_column(self) -> None:
        """A sticky box covers whatever follows it in its own column.

        The rail used to stand after the pinned check and ledger, so the metrics and the
        line-ups slid up underneath them and could not be read at all. They live inside the
        pin now, and the pin is the column's own scrollport: the four panels are taller than
        a laptop window, so a pin without one would hide its bottom below the fold. The
        halo cannot be cut by that scrollport because the port draws it inside each panel's
        own boundary — `--glow-padding` is 0 — which the browser harness asks for directly.
        """
        body = self.page()
        pin = body.index('<div class="side__pin">')
        rail = body.index('<aside class="rail"')
        self.assertLess(pin, rail, "the rail is not inside the pinned column")
        self.assertLess(
            rail,
            body.index("</div>", body.index("</aside>", rail)),
            "the pinned column closes before the rail, so the pin covers it as the page scrolls",
        )

        css = self.asset("/static/styles.css")
        rule = re.search(r"\.side__pin\s*\{(.*?)\}", css, re.S)
        self.assertIsNotNone(rule, "no .side__pin rule in the stylesheet")
        self.assertIn("position: sticky", rule.group(1))
        self.assertIn("overflow-y: auto", rule.group(1), "the pin cannot scroll, so its bottom is unreachable")
        self.assertIn("max-height", rule.group(1), "the pin is not bounded by the window")
        self.assertIn("100dvh", rule.group(1), "the pin is bounded by a height that ignores the browser's chrome")
        # An unbounded pin is the failure this rule exists to prevent, so the older unit is
        # a better answer than none: it is declared first and `dvh` wins where it exists.
        self.assertIn("max-height: calc(100vh - 1.5rem);", rule.group(1), "no fallback for a browser without dvh")
        self.assertLess(
            rule.group(1).index("100vh"),
            rule.group(1).index("100dvh"),
            "the fallback is written after the unit it is a fallback for",
        )
        self.assertIn("scrollbar-color", rule.group(1), "the column's scroll is an operating system's grey slab")
        self.assertRegex(
            css,
            r"\.side__pin:focus-visible\s*\{[^}]*outline:",
            "a keyboard reader cannot see which column has focus",
        )

        # A bounded flex column shrinks its children to fit it; these are panels to scroll.
        self.assertRegex(
            css,
            r"\.side__pin\s*>\s*\*\s*\{\s*flex:\s*none;",
            "the panel boxes are squeezed by the pinned column instead of scrolled",
        )

    def test_the_column_can_be_scrolled_without_a_pointer(self) -> None:
        """Chrome, Edge and Firefox focus a scroll container by themselves; Safari does not.

        So the page gives the column a tab stop and a name — but only while it has
        something to scroll. A focusable box with nothing left in it is a tab stop that
        does nothing, which is worse than the missing one it replaces, and that is why the
        attributes are written from a measurement rather than into the markup.
        """
        app = self.asset("/static/app.js")
        self.assertIn("function syncColumnAccess()", app, "nothing gives the column a tab stop")
        self.assertIn("column.tabIndex = 0;", app)
        self.assertIn('column.setAttribute("role", "region")', app)
        self.assertIn('column.setAttribute("aria-label", "Analysis column")', app)
        self.assertIn('column.removeAttribute("tabindex")', app, "the tab stop is never taken away")
        self.assertIn("column.scrollHeight > column.clientHeight", app, "the tab stop is not measured")
        self.assertGreaterEqual(
            app.count("syncColumnAccess();"),
            4,
            "the column's height can change without the tab stop being reconsidered",
        )
        # One of those has to be the view switch: the column is only laid out in the
        # analyst view, so entering and leaving it is when the tab stop matters most.
        view = app.index("function setView(view)")
        self.assertLess(view, app.index("syncColumnAccess();", view), "setView does not reconsider the tab stop")
        self.assertLess(
            app.index("syncColumnAccess();", view),
            app.index("function setSpeed(", view),
            "the column's tab stop is reconsidered outside setView",
        )

    def test_the_feed_has_a_title_with_the_wordmark_s_treatment(self) -> None:
        """The page a shared link opens on has a title, and it moves.

        The catalogue has an `<h1>` and the feed had none — the first thing a reader met
        was a scoreboard, and the match's name existed only in the tab and the footer. The
        heading is the page's own; the words are the payload's, replaced when the replay
        arrives, so a failed fetch leaves a title that is true instead of a guess.
        """
        body = self.page()
        self.assertIn('<h1 class="title" id="match-title">', body)
        self.assertLess(
            body.index('<h1 class="title"'),
            body.index('<section class="board"'),
            "the title is not the first thing above the match bar",
        )

        css = self.asset("/static/styles.css")
        title = re.search(r"\.title\s*\{(.*?)\}", css, re.S)
        self.assertIsNotNone(title, "no .title rule in the stylesheet")
        self.assertIn("font-family: var(--display)", title.group(1), "the title is not in the wordmark's face")
        self.assertIn("text-transform: uppercase", title.group(1))

        app = self.asset("/static/app.js")
        self.assertIn("matchTitle: $(", app, "the title is not reached by the page's script")
        self.assertRegex(
            app,
            r"const title = pressure\(el\.matchTitle",
            "the title is not a pressure host, so the line a link opens on sits still",
        )
        self.assertIn("title.setText(`${doc.home} v ${doc.away}`)", app)

    def test_a_panel_is_written_when_it_changed_and_not_once_a_frame(self) -> None:
        """The replay redraws the board every frame, and the payload moves in windows.

        Rebuilding the metrics table — twenty rows, four cells each — sixty times a second
        was most of what the feed spent while it animated: measured on this machine, the
        page was busy for 83% of the wall clock, its longest tasks ran over 100ms and a
        100ms timer fired 250ms late. The guard is the fix, and it is the rule the dock's
        goal count and the launcher already followed: a panel is rebuilt when what it
        describes changes, never once per frame.
        """
        app = self.asset("/static/app.js")
        self.assertIn("if (frame !== shownFrame) {", app, "the frame-dependent panels are unguarded")
        self.assertIn("if (minuteLine !== shownMinute) {", app, "the clock is rewritten every frame")
        self.assertIn("if (scoreline !== shownScoreline) {", app, "the score is rewritten every frame")
        # The first write to the table after the board's own definition has to be behind
        # the guard: a call before it is a call on the per-frame path.
        board = app.index("function renderBoard(")
        guard = app.index("if (frame !== shownFrame) {", board)
        self.assertLess(guard, app.index("renderMetrics(", board), "the metrics are written outside the guard")

    def test_the_value_loop_cannot_schedule_a_second_chain(self) -> None:
        """What one frame of animation actually cost before this guard existed.

        A value may move another value from its own `onChange` — the wake slider hands its
        handle's velocity to a second spring on every step — and that lands back in
        `wake()` while the loop is mid-step. `frame` is cleared on entry, so without a flag
        the re-entry schedules a second chain on top of the one the end of the step is
        about to schedule: two become four, four become eight, and the page runs its
        controls hundreds of times a second. Measured, that was 45 long tasks in eight
        seconds of playback, a frame every 42ms and a click answered 167ms late; with the
        guard it is one long task, 21ms frames and a 1ms press.
        """
        runtime = self.asset("/static/motion.js")
        self.assertIn("if (frame || draining) return;", runtime, "wake() can start a second chain")
        self.assertIn("draining = true;", runtime)
        self.assertRegex(
            runtime,
            r"\} finally \{\s*draining = false;",
            "a value that throws leaves the loop asleep",
        )

    def test_a_slot_can_be_chosen_without_a_pointer(self) -> None:
        """Space, Enter and a screen reader all activate a slot by firing a click, and this
        port reaches its slots through pointer events instead — which a keyboard never
        sends. Both halves are required: a slot no keyboard can choose is a radio in name
        only, and a click path that also ran after a tap would choose twice."""
        source = self.asset("/static/rubber-segment.js")
        self.assertIn('addEventListener("click"', source, "nothing activates a slot by keyboard")
        self.assertIn("event.detail !== 0", source, "a pointer click could choose a second time")
        self.assertIn('event.key === "ArrowRight"', source, "the arrow keys are gone")

    def test_the_old_pressed_button_groups_are_gone(self) -> None:
        """Two controls for one piece of state can disagree on screen, and the reader has
        no way to tell which one the page believes."""
        body = self.page()
        css = self.asset("/static/styles.css")
        self.assertNotIn("switch__btn", body)
        self.assertNotIn("transport__speed", body)
        self.assertNotIn("switch__btn", css)
        self.assertNotIn(".transport__speed", css)

    def test_the_floating_bar_cannot_take_a_click_meant_for_the_page(self) -> None:
        """The bar lives in a fixed layer across the whole viewport, which is far larger
        than the bar. The sea obeys the same rule for the same reason."""
        rail = re.search(
            r"\.dockrail\s*\{(.*?)\}", self.ported_controls_css(), re.S
        )
        self.assertIsNotNone(rail, "no .dockrail rule in the stylesheet")
        self.assertIn("position: fixed", rail.group(1))
        self.assertIn("pointer-events: none", rail.group(1))
        self.assertIn("pointer-events: auto", self.ported_controls_css())

    def test_neither_control_is_painted_in_a_verdict_colour(self) -> None:
        """Green and vermilion mean published and caught here. A control borrowing either
        would make the page's one signal ambiguous."""
        section = self.ported_controls_css().lower()
        self.assertNotIn("#2fd08a", section)
        self.assertNotIn("#ff5a36", section)
        self.assertNotIn("var(--verify)", section)
        self.assertNotIn("var(--flag)", section)

    def test_the_build_up_menu_offers_only_what_the_replay_has_reached(self) -> None:
        """A menu naming a goal that has not happened yet spoils the replay the page exists
        to show, and it would disagree with the scoreboard about the score."""
        app = self.asset("/static/app.js")
        self.assertRegex(
            app, r"goals\.filter\(\(goal\) => goal\.clock <= state\.clock\)"
        )

    def test_every_port_watches_the_reduced_motion_setting(self) -> None:
        """The page stops its own animation for that setting, and every port honours it
        through the shared runtime. Sampled once, it is the one thing still moving after a
        reader turns it on."""
        runtime = self.asset("/static/motion.js")
        self.assertIn("prefers-reduced-motion", runtime)
        self.assertIn('addEventListener("change"', runtime)
        for path in (
            "/static/border-glow.js",
            "/static/rubber-segment.js",
            "/static/dock.js",
            "/static/wake-slider.js",
            "/static/sling-button.js",
            "/static/infinite-menu.js",
        ):
            with self.subTest(path=path):
                self.assertIn("watchReducedMotion", self.asset(path))
                self.assertIn("prefersReducedMotion", self.asset(path))

    def test_the_upstream_notice_travels_with_each_port(self) -> None:
        """The upstream licence asks for its notice in copies of the work and names ports
        explicitly, so a ported file stripped of it is a licence breach rather than a
        simplification."""
        for path in (
            "/static/border-glow.js",
            "/static/rubber-segment.js",
            "/static/dock.js",
            "/static/wake-slider.js",
            "/static/sling-button.js",
            "/static/infinite-menu.js",
        ):
            with self.subTest(path=path):
                text = self.asset(path)
                self.assertIn("Copyright (c) 2026 David Haz", text)
                self.assertIn("Commons Clause", text)
                self.assertIn("reactbits.dev", text)
        licence = self.client.get("/static/vendor/react-bits-LICENSE.md")
        self.assertEqual(licence.status_code, 200)
        self.assertIn("Commons Clause", licence.text)
        self.assertIn("ported version", licence.text)
        # The notice names every port that ships beside it, so the file that lists them
        # cannot be a subset of the ports in the directory: a port missing from the list is
        # the same licence breach as a port missing its own header.
        for name in (
            "border-glow.js",
            "rubber-segment.js",
            "dock.js",
            "wake-slider.js",
            "sling-button.js",
            "infinite-menu.js",
            "slats.js",
        ):
            with self.subTest(listed=name):
                self.assertIn(name, licence.text)


@requires_server
class TestThePanelGlow(unittest.TestCase):
    """BorderGlow: the light on a panel's boundary, on every panel of both pages.

    This is the one port here that is not a control. It does not render anything a reader
    operates, and every way it can go wrong is invisible until it is looked at: a port that
    wrapped its host would move the nodes app.js holds, a port that copied the component's
    own card rules would give each panel a display and a background of its own, and a glow
    that lit every panel the moment the page loaded would be a page that never settles. The
    checks below are the ones that can be made from here; the ones that cannot — that the
    boundary actually lights up in a browser when a    pointer arrives — are measured in one, through the DevTools protocol, and asserted in
    tests/test_browser_harness.py, which is gated behind ``PITCHFRAME_BROWSER=1`` because it
    launches a browser. What is left here is what a file can say: which panels are marked,
    what the port is allowed to touch, and which variable each layer's opacity is a function
    of.
    """

    #: The panels `match.html` marks, by the class each carries.
    PANELS = ("board", "ratings", "feed", "check", "ledger", "metrics", "squads")

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def glow_css(self) -> str:
        """The BorderGlow section of the stylesheet, and only it.

        The ports share one section, and the controls after this one are full of ``:hover``
        rules of their own; a test that read them as the glow's would be testing the dock.
        The section ends where the controls' own header begins.
        """
        css = self.client.get("/static/styles.css").text
        self.assertIn("the ported controls", css)
        section = css.split("the ported controls", 1)[1].split("/* ---- the controls:", 1)[0]
        self.assertIn(".border-glow", section, "the glow's stylesheet section is gone")
        return section

    def test_every_panel_is_marked_and_the_page_adorns_each_one(self) -> None:
        """A panel that lost its marker would keep its border and quietly stop lighting it,
        which looks exactly like a panel nobody has pointed at yet."""
        body = self.client.get("/match").text
        for panel in self.PANELS:
            with self.subTest(panel=panel):
                self.assertIn(f'class="{panel}"', body)
                self.assertRegex(
                    body,
                    rf'class="[^"]*\b{panel}\b[^"]*"[^>]*data-glow',
                    "a panel is missing its data-glow marker",
                )
        app = self.client.get("/static/app.js").text
        self.assertIn('querySelectorAll("[data-glow]")', app)
        self.assertIn("createBorderGlow(panel", app)

    def test_the_port_adorns_a_panel_instead_of_wrapping_it(self) -> None:
        """Upstream renders a card and renders the children inside it. These panels already
        exist, and app.js holds references to what is in them — a port that moved them would
        be rebuilding the page to draw a border on it. So: the class, the layers, and the
        element's own style left alone."""
        port = self.client.get("/static/border-glow.js").text
        self.assertIn("element.prepend(edge)", port)
        self.assertIn('classList.add("border-glow")', port)
        self.assertIn('edge.className = "edge-light"', port)
        for stolen in ("element.style.display", "element.style.background =", "element.style.padding",
                       "element.innerHTML", "element.append(", "replaceChildren"):
            with self.subTest(stolen=stolen):
                self.assertNotIn(stolen, port)

    def test_the_host_keeps_its_own_shape_and_layer_order(self) -> None:
        """The host rule may say where the layers go and nothing else. Upstream's card rule
        carries a display, a padding, a border and a background, and any of them here would
        be the port overriding the page it was added to; the layers themselves have to sit
        behind the content, which is what the negative z-index and the isolation are for."""
        host = re.search(r"\.border-glow \{(.*?)\}", self.glow_css(), re.S)
        self.assertIsNotNone(host, "no .border-glow rule in the stylesheet")
        # The custom properties are the port's own knobs (--glow-padding among them), so what
        # is left after them is the whole of what this rule may say about the host.
        declarations = "\n".join(
            line for line in host.group(1).splitlines() if not line.strip().startswith("--")
        )
        for property_name in ("display", "padding", "margin", "border", "background", "font", "color"):
            with self.subTest(property=property_name):
                self.assertNotIn(property_name, declarations)
        self.assertIn("isolation: isolate", declarations)
        self.assertIn("position: relative", declarations)
        layers = self.glow_css()
        self.assertIn("z-index: -1", layers)
        self.assertIn("border-radius: inherit", layers)
        self.assertIn("pointer-events: none", layers)

    def test_the_colours_are_the_stylesheets_and_never_a_verdict(self) -> None:
        """Green and vermilion mean published and caught on this page, so the glow takes its
        colours from tokens of its own — a port that hard-coded upstream's purple mesh would
        be a second place where a colour is decided, and a page whose decoration ignored
        that rule would be a page whose one signal is ambiguous."""
        css = self.client.get("/static/styles.css").text
        root = css.split(":root {", 1)[1].split("}", 1)[0]
        for token_name in ("--glow-hsl", "--glow-one", "--glow-two", "--glow-three"):
            with self.subTest(token=token_name):
                self.assertIn(token_name, root)
        glow_tokens = [line for line in root.splitlines() if "--glow-" in line]
        for line in glow_tokens:
            with self.subTest(line=line.strip()):
                lowered = line.lower()
                self.assertNotIn("#2fd08a", lowered)
                self.assertNotIn("#ff5a36", lowered)
                self.assertNotIn("var(--verify)", lowered)
                self.assertNotIn("var(--flag)", lowered)
                self.assertNotIn("var(--amber)", lowered)
                self.assertNotIn("var(--home)", lowered)
                self.assertNotIn("var(--away)", lowered)
        port = self.client.get("/static/border-glow.js").text
        for token_name in ("--glow-hsl", "--glow-one", "--glow-two", "--glow-three", "--card-bg"):
            with self.subTest(read=token_name):
                self.assertIn(token_name, port)

    def test_the_keyboard_lights_the_same_edge(self) -> None:
        """A pointer-only decoration is one half the readers never see, and there are
        controls inside every panel here. Focus lights the edge, aims the cone at the
        control that took it, and is the one thing that outranks the pointer: the port
        gives it the whole of the light, so a panel a reader is working in cannot be left
        dark by a pointer that is somewhere else."""
        port = self.client.get("/static/border-glow.js").text
        self.assertIn('addEventListener("focusin", onFocusIn)', port)
        self.assertIn('addEventListener("focusout", onFocusOut)', port)
        self.assertIn('element.dataset.lit = "true"', port)
        self.assertIn('delete element.dataset.lit', port)
        # The focus path is the falloff, not a rule in the stylesheet: the light the page
        # has is the light this panel is given, and focus is what claims all of it.
        measure = re.search(r"function measure\(\) \{(.*?)\n  \}", port, re.S)
        self.assertIsNotNone(measure, "the port no longer measures a panel against the light")
        self.assertIn('if (hasFocus) {', measure.group(1))
        self.assertIn('setProperty("--glow-falloff", "1")', measure.group(1))
        self.assertNotIn(":not([data-lit])", self.glow_css(), "the panel is gated on hover again")

    def test_the_light_is_one_source_for_the_whole_page(self) -> None:
        """One pointer, one listener, and a panel's share of the light is the distance
        from that pointer to its nearest edge.

        Upstream's component decides for itself that the pointer is near it. On a page of
        one card that is the whole question; on a page of seven panels it is seven lamps,
        and the seams between them are the pixels where the light changes source. Every
        check below is a piece of the replacement: the listener the whole page shares, the
        reach that decides what is lit at all, the token the stylesheet multiplies each
        layer's opacity by, and the absence of the per-panel pointer handler that would
        make any of it moot.
        """
        port = self.client.get("/static/border-glow.js").text
        css = self.glow_css()

        # One listener for the page, registered when the first panel arrives and taken off
        # when the last one goes, and panels are measured against it rather than against
        # their own pointer. A panel that read the pointer itself is the bug this prevents.
        self.assertIn("const light = {", port)
        self.assertIn("panels: new Set(),", port)
        self.assertIn('document.addEventListener("pointermove", light.onPointerMove', port)
        self.assertIn("light.add(instance)", port)
        self.assertIn("light.remove(instance)", port)
        self.assertNotIn("element.addEventListener(\"pointermove\"", port)
        self.assertNotIn("onPointerMove(event) {\n    /* One rect read per move", port)

        # The reach, and the measure that reads it off a panel's own edge: a panel the
        # pointer is inside is at full strength wherever inside it the pointer is, which is
        # what keeps two panels a pixel apart lit together at the boundary between them.
        self.assertRegex(port, r"const REACH_PX = \d+;")
        self.assertRegex(
            port,
            r"Math\.max\(rect\.left - light\.x, 0, light\.x - rect\.right\)",
            "the reach is measured from a centre rather than from the edge",
        )
        # The pointer is coalesced to a frame rather than measured per move: eight panels
        # times dozens of moves a second is a layout the page would pay for and not show.
        self.assertIn("requestAnimationFrame(() => light.measure())", port)

        # The cone, which each panel still draws for itself: what the one light decides is
        # where the pointer is, and the panel turns that into the arc facing it.
        measure = re.search(r"function measure\(\) \{(.*?)\n  \}", port, re.S)
        self.assertIn("lightEdge(edgeProximity(rect, x, y) * 100, cursorAngle(rect, x, y));", measure.group(1))

        # And the number all three layers are a function of, in the port and in each of
        # their opacities: a falloff that reached the DOM and no further would be a measure
        # of nothing.
        self.assertIn('setProperty(\n      "--glow-falloff",', port)
        self.assertIn("--glow-falloff: 0;", css)
        opacities = re.findall(r"opacity: calc\((.*?)\);", css, re.S)
        self.assertEqual(len(opacities), 3, "a layer's opacity is no longer a single calculation")
        for calculation in opacities:
            with self.subTest(calculation=calculation.split()[0]):
                self.assertIn("var(--glow-falloff)", calculation)
        # The gate it replaced: a panel was lit because the pointer was over it, which is
        # exactly the eight-lamps behaviour this light exists to replace.
        self.assertNotIn(":hover", css, "the panels are still lit by hovering them")

    def test_the_stage_on_the_catalogue_page_is_adorned_with_the_mask_told_which_colour(self) -> None:
        """The stage's interior is a radial gradient rather than a flat colour, and the mask
        that keeps the mesh off a panel's inside is filled with whatever colour the port
        read — which for a gradient element is nothing at all. A transparent mask would let
        the mesh paint a wedge across the panel instead of a line along it, so the page hands
        it the token the gradient ends at."""
        module = self.client.get("/static/mode-browser.js").text
        self.assertIn("createBorderGlow(stage", module)
        self.assertRegex(module, r'createBorderGlow\(stage, \{[^}]*backgroundColor: token\("--night"')
        port = self.client.get("/static/border-glow.js").text
        self.assertIn("computed.a === 1", port, "a panel with no flat colour would be masked with nothing")

    def test_the_sweep_is_watched_rather_than_sampled(self) -> None:
        """The intro sweep is the one thing here that moves on its own, so it has to stop
        when a reader turns reduced motion on mid-flight — and never start when the setting
        was already on at load."""
        port = self.client.get("/static/border-glow.js").text
        self.assertIn("if (reduced) return;", port)
        self.assertIn("detachMotion = watchReducedMotion", port)
        self.assertRegex(
            port,
            r"if \(matches\) \{\s*stopSweep\(\);",
        )
        self.assertIn("let reduced = prefersReducedMotion();", port)


@requires_server
class TestTheModeBrowser(unittest.TestCase):
    """The catalogue as a sphere: the endpoint it comes from, and the page that draws it.

    This is the layer that made the named matches reachable. They existed and were tested,
    but the only way into one was to type it — ``?seed=28`` into the feed's URL, ``--seed
    28`` on the command line — and a catalogue nobody can browse is a list. Each check here
    exists because its failure looks like nothing at all:

    * A card list written into the page is a second catalogue, and the first generator
      retune leaves it describing matches that are no longer there.
    * A card whose seed the replay endpoint refuses is a door onto nothing, and it looks
      exactly like a door onto a match.
    * A module served with the wrong content type never executes, so the stage stays empty
      and the page reports no error anywhere a test can see.
    * The sphere needs WebGL2. On a browser without it the page has to offer the same
      matches rather than an empty box, which is a claim about code nobody runs by default.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def catalogue(self) -> dict:
        return self.client.get("/api/scenarios").json()

    def test_the_endpoint_returns_every_registered_scenario(self) -> None:
        """The count, the order and the default, which is all the page needs to open on the
        right card. A default missing from the payload is a sphere that starts wherever the
        iteration happened to land."""
        body = self.catalogue()
        self.assertEqual(body["count"], len(ALL_SCENARIOS))
        self.assertEqual([entry["key"] for entry in body["scenarios"]], list(ALL_SCENARIOS))
        self.assertEqual(
            [entry["key"] for entry in body["scenarios"] if entry["default"]],
            [DEFAULT_SCENARIO_KEY],
        )

    def test_the_catalogue_is_the_registrys_own_facts(self) -> None:
        """Every field a card prints or draws, checked against the registry rather than
        against the module that produced the JSON: an endpoint that asked
        ``pitchframe.scenarios.catalogue`` whether it agreed with itself would pass for a
        page describing matches nobody can open."""
        for entry in self.catalogue()["scenarios"]:
            with self.subTest(key=entry["key"]):
                item = ALL_SCENARIOS[entry["key"]]
                facts = contents(item.seed)
                self.assertEqual(entry["seed"], item.seed)
                self.assertEqual(entry["label"], item.label)
                self.assertEqual(entry["note"], item.note)
                self.assertEqual(entry["home"], facts["home"])
                self.assertEqual(entry["away"], facts["away"])
                self.assertEqual(entry["score"], list(facts["score"]))
                self.assertEqual(entry["minutes"], facts["minutes"])
                self.assertEqual(entry["long_build_ups"], facts["long_build_ups"])

    def test_every_card_opens_the_match_it_describes(self) -> None:
        """The whole promise of the page: open a card and the match it named is what plays.

        Every entry, through HTTP, with no frame limit — about eight seconds, which is worth
        it because the interesting failure is a single card. Fifteen good ones and one whose
        seed the endpoint refuses is a sphere with a dead tile on it, and it is the reader
        who finds that, not this suite.

        The comparison is against the *document*, not against the catalogue's own words: the
        card says nine goals, so the replay has to hand the page nine goals, at those
        minutes, each with a build-up to open — and the count of long build-ups on the card
        is checked against the steps of those build-ups, which is the number a reader is
        choosing on.
        """
        for entry in self.catalogue()["scenarios"]:
            with self.subTest(key=entry["key"]):
                response = self.client.get(f"/api/replay?seed={entry['seed']}&fault=none")
                self.assertEqual(response.status_code, 200)
                doc = response.json()
                self.assertEqual(doc["seed"], entry["seed"])
                self.assertEqual(doc["home"], entry["home"])
                self.assertEqual(doc["away"], entry["away"])
                # Through the goal list, not the score field: this is the list the page's
                # own scoreboard is rebuilt from, so the two agreeing is the claim.
                self.assertEqual(sorted(g["minute"] for g in doc["goals"]), sorted(entry["minutes"]))
                # One build-up per goal: a goal with no move to draw is a goal on this
                # card that opens an empty panel when the match is running.
                self.assertEqual(len(doc["highlights"]), entry["goals"])
                self.assertEqual(
                    sorted(h["minute"] for h in doc["highlights"]),
                    sorted(entry["minutes"]),
                )
                self.assertEqual(
                    sum(1 for h in doc["highlights"] if len(h["steps"]) >= 5),
                    entry["long_build_ups"],
                )

    def test_the_catalogue_is_measured_once_per_process(self) -> None:
        """It costs about a second, and re-measuring it per request would be a second of
        empty sphere every time somebody opened the page. The cache is cleared around the
        check so it tests the caching rather than the order this class ran in."""
        from pitchframe.overlay import server as server_module

        server_module._catalogue_document.cache_clear()
        try:
            with mock.patch.object(server_module, "catalogue", return_value=[]) as measured:
                self.client.get("/api/scenarios")
                self.client.get("/api/scenarios")
                self.assertEqual(measured.call_count, 1)
        finally:
            server_module._catalogue_document.cache_clear()

    def test_a_server_whose_code_has_moved_on_refuses_the_catalogue(self) -> None:
        """A catalogue built from stale modules names seeds this process would build
        differently, so it is the one answer that must not be given: the cards would open
        matches that are not the ones they described."""
        from pitchframe.overlay import server as server_module
        from pitchframe.overlay.stale import CodeState

        moved_on = CodeState(loaded="aaa", current="bbb", changed=("pitchframe/scenarios.py",))
        with mock.patch.object(server_module, "code_state", return_value=moved_on):
            response = self.client.get("/api/scenarios")

        self.assertEqual(response.status_code, 503)
        self.assertIn("pitchframe/scenarios.py", response.json()["detail"])

    def test_the_page_is_served_at_the_front_door_and_the_demo_is_gone(self) -> None:
        """One page for one job. The placeholder that stood here described a component that
        had not been copied in yet; leaving it served beside the real page would be two
        documents claiming to be the mode browser, one of them a mock-up."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertEqual(self.client.get("/infinite-menu-demo.html").status_code, 404)

    def test_the_address_it_used_to_live_at_still_leads_there(self) -> None:
        """A link already handed out is a link somebody has. It redirects rather than
        serving a second copy, so there is one canonical address for the page — and the
        hop is uncached, so a later change of address cannot be missed by a browser that
        kept the old one."""
        response = self.client.get("/mode-browser", follow_redirects=False)
        self.assertIn(response.status_code, (307, 308))
        self.assertEqual(response.headers["location"], "/")
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        followed = self.client.get("/mode-browser")
        self.assertEqual(followed.status_code, 200)
        self.assertIn("data-mode-browser", followed.text)

    def test_the_page_offers_a_stage_and_the_module_fills_it(self) -> None:
        """The page's own markup cannot show that anything was built into it: the div is
        there either way. So the marker and the module that reads it are both required."""
        body = self.client.get("/").text
        self.assertIn("data-mode-browser", body)
        self.assertIn('id="mb-status"', body)
        self.assertIn('type="module" src="/static/mode-browser.js"', body)
        module = self.client.get("/static/mode-browser.js").text
        self.assertIn('document.querySelector("[data-mode-browser]")', module)
        self.assertIn("createInfiniteMenu(", module)

    def test_the_page_composes_its_cards_from_the_endpoint(self) -> None:
        """A catalogue fetched is a catalogue that cannot drift; a catalogue typed into a
        script is one that silently will. Nothing here may be the registry's own words."""
        module = self.client.get("/static/mode-browser.js").text
        self.assertIn('fetch("/api/scenarios"', module)
        for key in ALL_SCENARIOS:
            with self.subTest(key=key):
                self.assertNotIn(f'"{key}"', module, "the page carries its own copy of a scenario")

    def test_a_card_opens_the_match_it_names(self) -> None:
        """The one thing the sphere is for. The card links to the address the catalogue
        carries for that match — the browser composes no URL of its own, it follows one the
        server built — and a tile click goes through the same door as the card's link."""
        module = self.client.get("/static/mode-browser.js").text
        self.assertIn("entry.path", module)
        self.assertIn("location.assign(item.href)", module)
        self.assertIn('open.href = entry.href', module)
        self.assertNotIn("/match?seed=", module, "the page is composing URLs again")

    def test_the_sphere_says_which_of_the_two_it_built(self) -> None:
        """WebGL2 is not universal, and the fallback is a real path rather than a message:
        the same items, in a list, each one a click from the same replay. The page reports
        which of the two is on screen, so the list cannot be mistaken for the design."""
        module = self.client.get("/static/mode-browser.js").text
        self.assertIn("menu.fallback", module)
        port = self.client.get("/static/infinite-menu.js").text
        self.assertIn("infinite-menu__fallback", port)
        self.assertIn("fallback: !engine", port)

    def test_the_list_a_browser_without_webgl2_gets_is_clickable(self) -> None:
        """The canvas goes with the context that would have drawn on it.

        A canvas with no context draws nothing, but it is still a canvas: it is absolutely
        positioned over the whole stage, it carries `cursor: grab`, and it is in the tree
        *under* the list. Leaving it there is a stage that looks exactly like a list of
        matches and takes every click aimed at one — which the browser harness reproduced
        before this removal, by asking the browser what a click in the middle of the first
        row would land on. The check here is the one a file can make: that the removal is
        in the fallback and not somewhere that would also take the sphere's canvas away.
        """
        port = self.client.get("/static/infinite-menu.js").text
        fallback = port.index("function buildFallback()")
        removal = port.index("dom.canvas.remove()", fallback)
        self.assertLess(removal, port.index("const list = document.createElement", fallback))
        # The sphere's own path keeps it: exactly one append of the canvas, at mount.
        self.assertEqual(port.count("root.append(dom.canvas"), 1)
        self.assertIn('dom.canvas.className = "infinite-menu__canvas"', port)

    def test_the_arrow_keys_are_answered_wherever_a_reader_is(self) -> None:
        """The page prints "Arrow keys step" the moment it opens — before the reader has
        clicked or tabbed into anything — so the keys cannot wait for the sphere's own
        element to be focused: nothing focuses it on arrival, and the promise would be false
        until the reader guessed to press Tab. They are answered on the document instead,
        with the guard the feed's key handler already uses, so a key aimed at a control
        still belongs to that control. The listener is also required to come back off on
        destroy: a handler left on the document outlives the sphere it moves.
        """
        port = self.client.get("/static/infinite-menu.js").text
        self.assertIn('document.addEventListener("keydown", onKeyDown)', port)
        self.assertIn('document.removeEventListener("keydown", onKeyDown)', port)
        self.assertNotIn('root.addEventListener("keydown"', port)
        self.assertIn("target.isContentEditable", port)

    def test_each_page_says_where_the_other_one_is(self) -> None:
        """A reader who lands on a match has to be able to get back to the choice, and a
        reader on the sphere has to be able to leave for the match they are looking at. The
        feed's way back is a link a reader can see, not only an alternate-document hint,
        and each page's link is checked against the other page's address rather than against
        the string it happens to contain."""
        feed = self.client.get("/match").text
        self.assertIn('href="/"', feed)
        self.assertIn('rel="alternate" href="/"', feed)

        front = self.client.get("/").text
        self.assertIn('href="/match"', front)

        # And no page still points at the address the front door moved away from.
        for body in (feed, front):
            with self.subTest(page=body[:40]):
                self.assertNotIn('href="/mode-browser"', body)

    def test_nothing_served_to_the_browser_page_may_be_kept(self) -> None:
        """The same rule as the feed's: a browser holding yesterday's script draws
        yesterday's cards against today's catalogue."""
        for path in ("/mode-browser", "/static/mode-browser.js", "/api/scenarios"):
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.get(path).headers.get("cache-control"), "no-store"
                )


@requires_server
class TestTheRatingsPanel(unittest.TestCase):
    """The marks, the man of the match, and the clock that decides when it is named.

    Three things have to hold for the panel to be honest rather than decorative: the
    payload has to carry a mark for every player in its own squads, the page has to draw
    them from that roster rather than from a list of its own, and the award has to be
    withheld until the replay has been played out — a man of the match named on the first
    frame would spoil the replay the whole page exists to show, exactly as printing the
    final score would.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def page(self) -> str:
        return self.client.get("/match").text

    def app(self) -> str:
        return self.client.get("/static/app.js").text

    def test_the_panel_is_on_the_page_and_the_page_fills_it(self) -> None:
        """A container nothing writes to is a control that is not there."""
        body = self.page()
        app = self.app()
        for marker, attribute in (
            ("ratings-list", 'id="ratings-list"'),
            ("ratings-window", 'id="ratings-window"'),
            ("award", 'id="award"'),
            ("award-name", 'id="award-name"'),
            ("award-text", 'id="award-text"'),
        ):
            with self.subTest(marker=marker):
                self.assertIn(attribute, body)
                self.assertIn(f'"{marker}"', app)

    def test_every_player_in_the_payload_squads_has_a_mark(self) -> None:
        """The panel joins the frame's marks against the roster the payload carries.
        A player the ratings forgot would be a nameless row or a missing one, and a
        rating for an id that is not in either squad would be drawn nowhere."""
        doc = replay_doc()
        roster = [
            player["id"] for side in ("home", "away") for player in doc["squads"][side]
        ]
        self.assertTrue(roster)
        for frame in doc["frames"]:
            with self.subTest(clock=frame["clock"]):
                rated = frame["ratings"]["values"]
                self.assertEqual(sorted(rated), sorted(roster))
                for player_id, value in rated.items():
                    self.assertGreaterEqual(value, 1.0)
                    self.assertLessEqual(value, 10.0)

    def test_a_frame_only_rates_what_its_clock_has_reached(self) -> None:
        """A mark that already counted a goal from a later window would put a number on
        the page the scoreboard disagrees with. The fixture is the default scenario, whose
        first goal is early enough to leave frames before it."""
        doc = replay_doc()
        frames = doc["frames"]
        # A goal in the middle of the replay, so there is a frame from before it and a
        # frame from after it to compare.
        goal = doc["goals"][-1]
        self.assertGreater(goal["clock"], frames[0]["clock"])
        # A goal names its scorer and not their id, and a name on its own is not unique
        # across two squads: the side is what makes it a player.
        scorer = next(
            player
            for player in doc["squads"][goal["team"]]
            if player["name"] == goal["player"]
        )
        before = frame_before(frames, goal["clock"])["ratings"]["values"][scorer["id"]]
        after = frames[-1]["ratings"]["values"][scorer["id"]]
        self.assertLess(before, after)

    def test_the_award_is_one_for_the_whole_match_and_not_a_frame_of_it(self) -> None:
        """The payload is the finished script and the page is what withholds it, and the
        difference is load-bearing: **no frame carries an award at all**, so the page
        cannot read a partial one however it tries. Cut the other way — an award computed
        as the replay runs — the page would have to decide when a match is over, and any
        truncated replay would name a man of the match for a match it never reached.

        That is also why the award's rating is *not* the last frame's value for the same
        player: the mark at 25 minutes is the mark the first twenty-five minutes produced,
        and the award is for the match."""
        doc = replay_doc()
        for frame in doc["frames"]:
            with self.subTest(clock=frame["clock"]):
                self.assertNotIn("award", frame["ratings"])

        block = doc["ratings"]
        self.assertEqual(block["decided_at"], doc["frames"][-1]["clock"])
        self.assertEqual(block["labels"], list(LABELS))
        award = block["award"]
        self.assertIsNotNone(award)
        self.assertTrue(award["reasons"])
        self.assertGreater(award["rating"], max(doc["frames"][-1]["ratings"]["values"].values()))
        self.assertIn("The match finished", award["conclusion"])

    def test_the_page_holds_the_award_until_the_clock_reaches_it(self) -> None:
        """The rule the panel states in its own note, as the comparison that implements
        it: the award is drawn only once, and only from ``decided_at``."""
        app = self.app()
        self.assertIn("state.clock >= block.decided_at", app)
        self.assertIn("doc.ratings", app)
        self.assertIn('el.awardText.textContent = award.conclusion', app)

    def test_the_mark_is_not_rounded_by_the_page(self) -> None:
        """One decimal is what a rating is; a page that rounded it again would print a
        different number from the one the payload carries and the receipts quote."""
        self.assertIn("player.rating.toFixed(1)", self.app())

    def test_the_panel_says_where_its_numbers_come_from(self) -> None:
        """Every figure on this page states its provenance, and a rating is the one
        number here that is a judgement rather than a measurement: the label has to say
        it was computed rather than awarded. It travels in the payload the way the
        build-up panel's labels do, so a view cannot compose a label of its own."""
        self.assertIn("ratings computed, not awarded", replay_doc()["ratings"]["labels"])
        self.assertIn("id=\"ratings-labels\"", self.page())
        self.assertIn("doc.ratings && doc.ratings.labels", self.app())


@requires_server
class TestTheLauncher(unittest.TestCase):
    """The sling pad that opens the build-up panel.

    The panel used to be reachable only from a goal row and from the dock's menu, both of
    which exist only once the replay has scored — so on a match whose first goal is late,
    the feature looked broken for most of the replay. This is the control that is always
    on the page, and these are the ways it could stop being one.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def page(self) -> str:
        return self.client.get("/match").text

    def app(self) -> str:
        return self.client.get("/static/app.js").text

    def test_the_page_has_a_host_for_the_pad_and_fills_it(self) -> None:
        """An empty div is a control that is not there."""
        self.assertIn('id="sling"', self.page())
        self.assertIn("createSlingButton(el.sling", self.app())

    def test_the_pad_is_a_port_and_imported_as_one(self) -> None:
        """A pad drawn inline in app.js would be a second copy of a component whose
        notice has to travel with it."""
        app = self.app()
        self.assertIn('import { createSlingButton } from "./sling-button.js";', app)
        self.assertEqual(self.client.get("/static/sling-button.js").status_code, 200)

    def test_the_launcher_names_what_it_would_open(self) -> None:
        """A pad whose label does not change is a pad the reader cannot tell is waiting
        for a goal — which is the report this control answers."""
        body = self.page()
        app = self.app()
        for marker in ("launch-title", "launch-note"):
            with self.subTest(marker=marker):
                self.assertIn(f'id="{marker}"', body)
                self.assertIn(f'"{marker}"', app)

    def test_the_pad_is_disabled_while_it_has_nothing_to_open(self) -> None:
        """A control that looks live and does nothing when pulled reads as broken. The port
        dims itself for `disabled`, and the page is what decides when that is."""
        app = self.app()
        self.assertIn("disabled:", app)
        self.assertIn("sling.set({", app)
        self.assertIn("launchNote", app)


@requires_server
class TestTheKeyboard(unittest.TestCase):
    """The keys, and the menu column that names them.

    Both read one table, so each check here is about the ways that pairing is lost: a key
    typed into a menu by hand, a second list inside the listener, two rows claiming one
    key, a page that takes a key the browser was using, or a key that fires at something
    the reader cannot see.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def app(self) -> str:
        return self.client.get("/static/app.js").text

    def shortcuts(self) -> str:
        """The table alone. Read by slicing rather than by matching keys, so a test can
        also see how many rows there are and what they are grouped under."""
        app = self.app()
        self.assertIn("const SHORTCUTS = [", app, "the shortcut table is gone")
        return app.split("const SHORTCUTS = [", 1)[1].split("\n  ];", 1)[0]

    def test_every_shortcut_column_is_filled_from_the_table(self) -> None:
        """A menu naming a key nobody answers reads as a broken control; a key nothing
        advertises is a secret. The failure that breaks the pairing is a key typed into a
        menu by hand, so no menu may name one except through the table."""
        app = self.app()
        named = re.findall(r"shortcut:\s*([^\n]+)", app)
        self.assertTrue(named, "no menu names a key at all")
        for value in named:
            with self.subTest(value=value):
                self.assertRegex(value.strip().rstrip(","), r"^(shortcutOf|keyName)\(")

    def test_the_page_is_drivable_from_the_keyboard(self) -> None:
        """The four things a replay needs — play/pause, a seek step, a speed and a view —
        and the dock offering the whole list as a menu, because a reader who has not
        learned the keys has to be able to find them."""
        table = self.shortcuts()
        for group in ("Playback", "Seek", "Speed", "View"):
            with self.subTest(group=group):
                self.assertIn(f'group: "{group}"', table)
        keys = re.findall(r'key: "([^"]*)"', table)
        self.assertGreaterEqual(len(keys), 8)
        self.assertEqual(len(keys), len(set(keys)), "two rows answer the same key")
        app = self.app()
        self.assertIn('label: "Keyboard"', app, "nothing offers the list")
        self.assertIn("menu: shortcutRows()", app)

    def test_the_listener_answers_no_key_of_its_own(self) -> None:
        """The listener is the table's other reader. A key compared by hand inside it, or
        a second list, is how the column and the behaviour drift apart."""
        app = self.app()
        self.assertIn("shortcutFor(event.key.toLowerCase())", app)
        self.assertNotIn('event.key === "', app)

    def test_one_seek_is_one_analysed_window(self) -> None:
        """The step is the window the payload was analysed in rather than a number typed
        here: a match cut into ten-minute windows has to seek ten minutes."""
        self.assertIn("state.doc && state.doc.window_seconds", self.app())

    def test_a_modifier_leaves_the_key_to_the_browser(self) -> None:
        """`F` is the Fan view and Cmd/Ctrl+F is still find-on-page. A page that takes the
        second has broken the browser to save a keystroke."""
        self.assertIn("event.metaKey || event.ctrlKey || event.altKey", self.app())

    def test_an_open_surface_keeps_its_keys(self) -> None:
        """The build-up panel is a modal and a dock menu is a list of speeds: a key firing
        behind either would move something the reader cannot see, and would leave the menu
        offering a speed that had already changed under it."""
        app = self.app()
        self.assertIn("if (el.hl.open) return;", app)
        self.assertIn('if (document.querySelector("[data-dock-menu]")) return;', app)


@requires_server
class TestTheTeamSheetAndTheGoalChoice(unittest.TestCase):
    """The two things a reader picks from inside a match.

    A team sheet without its substitutes is half a team sheet, and a match with five
    goals whose build-up panel only opens the newest one hides four of them behind a
    menu in the opposite corner. Both are payload-and-page claims: the payload has to
    carry the bench and the goals, and the page has to draw them from there rather than
    from a list of its own.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())
        cls.doc = cls.client.get("/api/replay?scenario=matchday").json()
        cls.page = cls.client.get("/match/matchday").text
        cls.app = cls.client.get("/static/app.js").text

    def test_every_side_carries_its_own_bench(self) -> None:
        """Eleven who started and five behind them, by name and by shirt, and no id in
        both lists: a substitute who shared an id with a starter would draw a mark for a
        player who never came on."""
        for side in ("home", "away"):
            with self.subTest(side=side):
                starters = self.doc["squads"][side]
                bench = self.doc["bench"][side]
                self.assertEqual(len(starters), 11)
                self.assertEqual(len(bench), 5)
                self.assertFalse(
                    {p["id"] for p in starters} & {p["id"] for p in bench},
                    "a substitute shares an id with a starter",
                )
                self.assertFalse(
                    {p["name"] for p in starters} & {p["name"] for p in bench},
                    "a substitute shares a name with a starter",
                )
                for player in bench:
                    self.assertTrue(player["name"] and player["position"])
                    self.assertGreaterEqual(player["shirt"], 12)

    def test_the_bench_is_not_rated(self) -> None:
        """A mark is what a player did with the minutes they played; a substitute who
        never came on has none, and folding the bench into the roster the ratings join
        against would invent one."""
        starters = {
            p["id"] for side in ("home", "away") for p in self.doc["squads"][side]
        }
        bench = {
            p["id"] for side in ("home", "away") for p in self.doc["bench"][side]
        }
        self.assertFalse(starters & bench)
        last = self.doc["frames"][-1]["ratings"]["values"]
        self.assertEqual(set(last), starters, "the ratings and the starting elevens disagree")

    def test_the_panel_can_name_every_goal_it_could_show(self) -> None:
        """The chooser is built from the reached goals, not from the newest highlight: a
        button whose only job is to reopen the panel that is already open is not a
        control, so it is hidden below two, and the payload has enough goals for the
        default match to exercise it."""
        self.assertGreaterEqual(len(self.doc["goals"]), 2)
        self.assertIn('id="hl-goals"', self.page)
        self.assertIn('role="group" aria-label="Goals in this match"', self.page)
        self.assertIn("function renderHlGoals()", self.app)
        self.assertIn("entries.length < 2", self.app)
        self.assertIn("hlGoals.hidden = true", self.app)
        self.assertIn("data-hl-goal=", self.app)
        self.assertIn('el.hlGoals.addEventListener("click"', self.app)
        self.assertIn("reachedBuildUps()", self.app)

if __name__ == "__main__":
    unittest.main()
