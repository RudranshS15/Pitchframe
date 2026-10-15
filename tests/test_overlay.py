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

import json
import re
import unittest
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

    def test_the_index_is_served(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_the_page_loads_its_own_assets(self) -> None:
        """A page whose stylesheet 404s still renders, and looks broken to a judge."""
        for asset in ("/static/styles.css", "/static/app.js"):
            with self.subTest(asset=asset):
                self.assertEqual(self.client.get(asset).status_code, 200)

    def test_nothing_served_may_be_kept_by_the_browser(self) -> None:
        """A browser holding yesterday's ``app.js`` makes every fix invisible.

        That is the same failure as a process holding yesterday's modules — the page
        keeps drawing the old build-up panel and nothing anywhere says 503 — so the
        assets are served unstored, and so is the payload they are drawn from.
        """
        for path in ("/", "/static/app.js", "/static/styles.css", "/api/replay?max_frames=1"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_the_build_up_panel_is_in_the_page(self) -> None:
        """The panel is the deliverable, so a page that lost its controls should fail
        here rather than being discovered in a browser."""
        body = self.client.get("/").text
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
        body = self.client.get("/").text
        self.assertIn("data-slats", body)
        self.assertIn('type="module"', body)
        self.assertIn("/static/slats.js", body)

    def test_no_script_comes_from_another_origin(self) -> None:
        """The overlay is shown in rooms without wifi, so the one dependency ships with
        the page rather than being fetched at load."""
        body = self.client.get("/").text
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


@requires_server
class TestThePortedControls(unittest.TestCase):
    """The view switch, the replay speed, the action dock and the match-minutes seek are
    ports of React Bits components, so what can be asserted from here is the wiring that
    keeps them working.

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
        return self.client.get("/").text

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
        for control in (".dock", ".rubber-segment", ".wake-slider"):
            with self.subTest(control=control):
                self.assertIn(control, section)
        return section

    def test_every_module_the_controls_need_is_served_as_javascript(self) -> None:
        for path in (
            "/static/motion.js",
            "/static/rubber-segment.js",
            "/static/dock.js",
            "/static/wake-slider.js",
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
            "/static/rubber-segment.js",
            "/static/dock.js",
            "/static/wake-slider.js",
        ):
            for target in re.findall(r'from "(\./[^"]+)"', self.asset(module)):
                seen += 1
                with self.subTest(module=module, target=target):
                    self.assertEqual(
                        self.client.get("/static/" + target.lstrip("./")).status_code, 200
                    )
        # app.js imports all three controls, and each of them imports the shared runtime.
        self.assertEqual(seen, 6)

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
        for path in ("/static/rubber-segment.js", "/static/dock.js", "/static/wake-slider.js"):
            with self.subTest(path=path):
                self.assertIn("watchReducedMotion", self.asset(path))

    def test_the_upstream_notice_travels_with_each_port(self) -> None:
        """The upstream licence asks for its notice in copies of the work and names ports
        explicitly, so a ported file stripped of it is a licence breach rather than a
        simplification."""
        for path in ("/static/rubber-segment.js", "/static/dock.js", "/static/wake-slider.js"):
            with self.subTest(path=path):
                text = self.asset(path)
                self.assertIn("Copyright (c) 2026 David Haz", text)
                self.assertIn("Commons Clause", text)
                self.assertIn("reactbits.dev", text)
        licence = self.client.get("/static/vendor/react-bits-LICENSE.md")
        self.assertEqual(licence.status_code, 200)
        self.assertIn("Commons Clause", licence.text)
        self.assertIn("ported version", licence.text)


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


if __name__ == "__main__":
    unittest.main()
