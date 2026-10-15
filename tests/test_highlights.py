"""Tests for goal build-up extraction, move summaries and the highlight payload.

Three properties here are load-bearing, and each is the kind that quietly stops being
true:

* **The hand-built fixtures are exact.** Every number in :class:`TestMoveSummary` is
  worked out by hand from the coordinates in the fixture rather than captured from a
  run of the code. A test that asserts whatever the function currently returns proves
  only that the function is deterministic, which the generator tests already cover.
* **A derived step is never passed off as a recorded one.** Carries do not exist in
  the event stream, so they are inferred, and the whole labelling story depends on the
  inference being flagged. The tests pin both the flag and the label.
* **The move stops where it should.** The walk backwards has three ways to overshoot —
  through a possession id, through a tackle recorded against the other side, and
  through kick-off — and each is tested separately, because a boundary that only
  sometimes holds is worse than no boundary.

Standard library only, no model, no server: the same bare interpreter the rest of the
overlay's logic runs on.
"""

from __future__ import annotations

import itertools
import json
import unittest

from pitchframe.generator import generate_match
from pitchframe.highlights import (
    CARRY_THRESHOLD,
    LABEL_DERIVED_CARRIES,
    LABEL_SYNTHETIC,
    MoveSummary,
    build_highlights,
    extract_build_up,
    highlight_for_goal,
    highlight_payload,
    move_window,
    summarise_move,
    zone_for,
)
from pitchframe.schema import MatchEvent, Player

from pitchframe.generator import LONG_SHOT_RANGE

# --- fixtures ---------------------------------------------------------------

_COUNTER = itertools.count(1)

_ALARO = Player(id="H01", name="Alaro", team="home", position="CM", shirt=8)
_BRENBECK = Player(id="H02", name="Brenbeck", team="home", position="ST", shirt=9)
_CASDAHL = Player(id="H03", name="Casdahl", team="home", position="LW", shirt=11)
_TOMEK = Player(id="H04", name="Tomek", team="home", position="DM", shirt=6)

_ERIKOV = Player(id="A01", name="Erikov", team="away", position="CM", shirt=8)
_FILQUIST = Player(id="A02", name="Filquist", team="away", position="ST", shirt=9)
_GORADO = Player(id="A03", name="Gorado", team="away", position="LW", shirt=11)

_HOME_THREE = (_ALARO, _BRENBECK, _CASDAHL)
_AWAY_THREE = (_ERIKOV, _FILQUIST, _GORADO)


def event(
    kind: str,
    *,
    team: str = "home",
    clock: int = 0,
    from_xy: tuple[float, float] | None = None,
    to_xy: tuple[float, float] | None = None,
    outcome: str | None = None,
    player: Player | None = None,
    possession_id: int = 1,
) -> MatchEvent:
    """Build one fixture event, with a unique id and an explicit possession."""
    squad_member = player if player is not None else _HOME_THREE[0] if team == "home" else _AWAY_THREE[0]
    return MatchEvent(
        id=f"T-E{next(_COUNTER):05d}",
        match_id="T",
        clock=clock,
        type=kind,  # type: ignore[arg-type]
        team=team,  # type: ignore[arg-type]
        player=squad_member,
        from_xy=from_xy,
        to_xy=to_xy,
        outcome=outcome,  # type: ignore[arg-type]
        possession_id=possession_id,
    )


def three_pass_move(*, team: str = "home", possession_id: int = 1) -> list[MatchEvent]:
    """A move of exactly three passes and a finish, by three different players.

    The geometry is chosen so every summary value is a round number or a single
    square root: 30 units, then a diagonal, then another diagonal, then a 15-unit
    finish. Written out because the expected distance in the test is computed from
    these coordinates by hand, and a fixture that changed silently would make that
    arithmetic wrong without failing anything.
    """
    first, second, third = _HOME_THREE if team == "home" else _AWAY_THREE
    if team == "home":
        start, mid_a, mid_b, finish = (10.0, 50.0), (40.0, 50.0), (60.0, 30.0), (85.0, 50.0)
        mouth = (100.0, 50.0)
        goal_mouth = (100.0, 55.0)
    else:
        start, mid_a, mid_b, finish = (90.0, 50.0), (60.0, 50.0), (40.0, 70.0), (15.0, 50.0)
        mouth = (0.0, 50.0)
        goal_mouth = (0.0, 45.0)

    return [
        event("possession_change", team=team, clock=80, from_xy=start, outcome="success",
              possession_id=possession_id, player=first),
        event("pass", team=team, clock=84, from_xy=start, to_xy=mid_a, outcome="success",
              possession_id=possession_id, player=first),
        event("pass", team=team, clock=88, from_xy=mid_a, to_xy=mid_b, outcome="success",
              possession_id=possession_id, player=second),
        event("pass", team=team, clock=92, from_xy=mid_b, to_xy=finish, outcome="success",
              possession_id=possession_id, player=third),
        event("shot", team=team, clock=96, from_xy=finish, to_xy=mouth, outcome="success",
              possession_id=possession_id, player=third),
        event("goal", team=team, clock=100, from_xy=finish, to_xy=goal_mouth, outcome="success",
              possession_id=possession_id, player=third),
    ]


def _MOVE_WITH_A_GAP() -> list[MatchEvent]:
    """A move in which one player covers ground between two touches.

    One player touches the ball at (20, 50) -> (40, 50) and then again from (60, 50).
    Nothing in the stream accounts for the twenty units in between, which is exactly
    what an unrecorded carry looks like — so the extraction infers one, and this is
    the only way to produce a *derived* step now that the generator records carries
    itself and leaves no gaps.
    """
    return [
        event("pass", clock=10, from_xy=(20.0, 50.0), to_xy=(40.0, 50.0),
              outcome="success", player=_ALARO),
        event("pass", clock=20, from_xy=(60.0, 50.0), to_xy=(70.0, 50.0),
              outcome="success", player=_ALARO),
        event("goal", clock=24, from_xy=(70.0, 50.0), to_xy=(100.0, 50.0),
              outcome="success", player=_ALARO),
    ]


def goal_of(events: list[MatchEvent]) -> MatchEvent:
    """The one goal in a fixture."""
    return next(e for e in events if e.type == "goal")


class TestBuildUpExtraction(unittest.TestCase):
    """Task 1: walk back through the possession and return ordered steps."""

    def test_a_three_pass_move_returns_exactly_three_steps_in_order(self) -> None:
        """The specified case: the build-up alone, without the finish."""
        events = three_pass_move()
        steps = extract_build_up(events, goal_of(events), include_finish=False)

        self.assertEqual(len(steps), 3)
        self.assertEqual([step.kind for step in steps], ["pass", "pass", "pass"])
        self.assertEqual([step.index for step in steps], [0, 1, 2])
        self.assertEqual([step.clock for step in steps], [84, 88, 92])
        self.assertEqual([step.player for step in steps],
                         ["Alaro", "Brenbeck", "Casdahl"])
        # Each step starts exactly where the previous one ended: a move is a chain,
        # and a break in it would mean the path drawn was not the ball's path.
        for earlier, later in zip(steps, steps[1:]):
            self.assertEqual(earlier.end_xy, later.start_xy)

    def test_the_default_call_returns_three_steps_for_a_three_pass_move(self) -> None:
        """The specified case, on the default call and with no flag passed.

        There is no shot event in this fixture, so there is no finish to add and the
        move is exactly its three passes. This is the reading the requirement states
        most directly, and it is asserted here separately from the
        ``include_finish=False`` case above, because the two paths are different code.
        """
        events = [
            event("pass", clock=10, from_xy=(10.0, 50.0), to_xy=(40.0, 50.0),
                  outcome="success", player=_ALARO),
            event("pass", clock=16, from_xy=(40.0, 50.0), to_xy=(60.0, 30.0),
                  outcome="success", player=_BRENBECK),
            event("pass", clock=22, from_xy=(60.0, 30.0), to_xy=(85.0, 50.0),
                  outcome="success", player=_CASDAHL),
            event("goal", clock=26, from_xy=(85.0, 50.0), to_xy=(100.0, 52.0),
                  outcome="success", player=_CASDAHL),
        ]
        steps = extract_build_up(events, goal_of(events))
        self.assertEqual(len(steps), 3)
        self.assertEqual([step.kind for step in steps], ["pass", "pass", "pass"])
        self.assertEqual([step.clock for step in steps], [10, 16, 22])
        self.assertEqual([step.index for step in steps], [0, 1, 2])

    def test_the_shot_is_the_finish_when_it_is_asked_for(self) -> None:
        events = three_pass_move()
        steps = extract_build_up(events, goal_of(events))
        self.assertEqual([step.kind for step in steps], ["pass", "pass", "pass", "shot"])
        self.assertEqual(steps[-1].player, "Casdahl")
        self.assertFalse(steps[-1].derived)

    def test_a_goal_with_no_preceding_possession_returns_an_empty_list(self) -> None:
        """An unassisted finish. Empty is the answer, not an error."""
        lone = event("goal", clock=30, from_xy=(90.0, 50.0), to_xy=(100.0, 50.0),
                     outcome="success", possession_id=7)
        self.assertEqual(extract_build_up([lone], lone), ())
        self.assertEqual(extract_build_up([lone], lone, include_finish=False), ())

    def test_a_goal_does_not_count_its_own_strike_twice(self) -> None:
        """The goal event is the end of the move, not a touch inside it.

        The generator emits a shot and the goal it produced, describing the same
        strike from both ends. Collecting both reports two finishes and, worse,
        reads the gap between the shot's target and the shot's own starting point as
        travel. One shot step is the correct answer.
        """
        events = [
            event("pass", clock=10, from_xy=(20.0, 50.0), to_xy=(60.0, 50.0), outcome="success"),
            event("shot", clock=14, from_xy=(60.0, 50.0), to_xy=(100.0, 50.0), outcome="success"),
            event("goal", clock=14, from_xy=(60.0, 50.0), to_xy=(100.0, 55.0), outcome="success"),
        ]
        steps = extract_build_up(events, goal_of(events))
        self.assertEqual([step.kind for step in steps], ["pass", "shot"])
        self.assertFalse(any(step.derived for step in steps))

    def test_a_non_goal_is_refused_rather_than_guessed_at(self) -> None:
        """A pass walked backwards from would yield a plausible, meaningless path."""
        events = three_pass_move()
        with self.assertRaises(ValueError):
            extract_build_up(events, events[1])

    def test_a_shortened_build_up_keeps_the_carries_it_derived(self) -> None:
        """Dropping the finish must not drop the carries that led to it."""
        player = _ALARO
        events = [
            event("pass", clock=10, from_xy=(20.0, 50.0), to_xy=(40.0, 50.0),
                  outcome="success", player=player),
            event("pass", clock=20, from_xy=(60.0, 50.0), to_xy=(70.0, 50.0),
                  outcome="success", player=player),
            event("shot", clock=30, from_xy=(70.0, 50.0), to_xy=(100.0, 50.0),
                  outcome="success", player=player),
            event("goal", clock=32, from_xy=(70.0, 50.0), to_xy=(100.0, 50.0),
                  outcome="success", player=player),
        ]
        steps = extract_build_up(events, goal_of(events), include_finish=False)
        self.assertEqual([step.kind for step in steps], ["pass", "carry", "pass"])


class TestMoveWindowBoundaries(unittest.TestCase):
    """Each way the backwards walk can overshoot, tested on its own."""

    def _move(self) -> list[MatchEvent]:
        return three_pass_move(possession_id=5)

    def test_a_different_possession_is_not_part_of_the_move(self) -> None:
        earlier = event("pass", clock=40, from_xy=(5.0, 50.0), to_xy=(15.0, 50.0),
                        outcome="success", possession_id=4, player=_TOMEK)
        events = [earlier, *self._move()]
        window = move_window(events, goal_of(events))
        self.assertNotIn(earlier, window)
        self.assertEqual(window[0].possession_id, 5)

    def test_the_tackle_that_won_the_ball_is_not_credited_to_the_scorer(self) -> None:
        """A possession's last event is the tackle that ended it, recorded against
        the side that won the ball while still carrying the old possession id. Walking
        past it would credit the scoring side with the previous side's passing."""
        tackle = event("tackle", team="away", clock=78, from_xy=(10.0, 50.0),
                       outcome="success", possession_id=5, player=_ERIKOV)
        events = [tackle, *self._move()]
        window = move_window(events, goal_of(events))
        self.assertNotIn(tackle, window)
        self.assertTrue(all(e.team == "home" for e in window))

    def test_a_kickoff_is_a_hard_boundary(self) -> None:
        """No move spans kick-off, whatever the possession id says."""
        kickoff = event("kickoff", clock=0, from_xy=(50.0, 50.0), outcome=None,
                        possession_id=5, player=_ALARO)
        events = [kickoff, *self._move()]
        window = move_window(events, goal_of(events))
        self.assertNotIn(kickoff, window)

    def test_the_window_ends_with_the_goal(self) -> None:
        events = self._move()
        goal = goal_of(events)
        self.assertIs(move_window(events, goal)[-1], goal)

    def test_an_absent_goal_yields_no_window(self) -> None:
        """A goal from another match must not silently anchor to a wrong position."""
        events = three_pass_move()
        stranger = event("goal", clock=999, from_xy=(50.0, 50.0), to_xy=(50.0, 50.0),
                         outcome="success", possession_id=99)
        self.assertEqual(move_window(events, stranger), ())


class TestDerivedCarries(unittest.TestCase):
    """Carries do not exist in the stream, so the rule that infers them is the test."""

    def _carry_fixture(self, *, second_from: tuple[float, float]) -> list[MatchEvent]:
        return [
            event("pass", clock=10, from_xy=(20.0, 50.0), to_xy=(40.0, 50.0),
                  outcome="success", player=_ALARO),
            event("pass", clock=20, from_xy=second_from, to_xy=(70.0, 50.0),
                  outcome="success", player=_ALARO),
            event("goal", clock=24, from_xy=(70.0, 50.0), to_xy=(100.0, 50.0),
                  outcome="success", player=_ALARO),
        ]

    def test_a_carry_is_derived_when_one_player_covers_ground(self) -> None:
        events = self._carry_fixture(second_from=(60.0, 50.0))
        steps = extract_build_up(events, goal_of(events), include_finish=False)
        carries = [step for step in steps if step.kind == "carry"]
        self.assertEqual(len(carries), 1)
        carry = carries[0]
        # It runs from where the previous touch left the ball to where the next began…
        self.assertEqual(carry.start_xy, (40.0, 50.0))
        self.assertEqual(carry.end_xy, (60.0, 50.0))
        # …it is flagged as inferred…
        self.assertTrue(carry.derived)
        # …and it sits between the two touches rather than beside one of them.
        self.assertEqual(carry.clock, 15)
        self.assertEqual([step.kind for step in steps], ["pass", "carry", "pass"])

    def test_a_handover_between_players_is_not_a_carry(self) -> None:
        """A different player means the ball was passed, not carried."""
        events = [
            event("pass", clock=10, from_xy=(20.0, 50.0), to_xy=(40.0, 50.0),
                  outcome="success", player=_ALARO),
            event("pass", clock=20, from_xy=(60.0, 50.0), to_xy=(70.0, 50.0),
                  outcome="success", player=_BRENBECK),
            event("goal", clock=24, from_xy=(70.0, 50.0), to_xy=(100.0, 50.0),
                  outcome="success", player=_BRENBECK),
        ]
        steps = extract_build_up(events, goal_of(events), include_finish=False)
        self.assertFalse(any(step.derived for step in steps))

    def test_a_short_shuffle_is_not_dressed_up_as_a_carry(self) -> None:
        """Below the threshold is noise in a pass's target, not a carry."""
        just_under = (40.0 + CARRY_THRESHOLD - 0.5, 50.0)
        events = self._carry_fixture(second_from=just_under)
        steps = extract_build_up(events, goal_of(events), include_finish=False)
        self.assertFalse(any(step.derived for step in steps))

    def test_two_touches_at_the_same_instant_are_not_travel(self) -> None:
        """Two events on one clock are one action seen twice.

        A shot and the goal it produced share a clock. Reading that pair as a carry
        invents a path from the shot's target back to where the shot was taken, which
        is not something the ball did.

        The fixture holds exactly the two same-clock touches and nothing else, so the
        pair under test is the only pair there is. Adding an earlier touch would let a
        legitimate carry elsewhere in the move satisfy the assertion and hide the bug.
        """
        events = [
            event("shot", clock=40, from_xy=(60.0, 50.0), to_xy=(100.0, 50.0),
                  outcome="success", player=_ALARO),
            event("shot", clock=40, from_xy=(90.0, 40.0), to_xy=(100.0, 40.0),
                  outcome="success", player=_ALARO),
            event("goal", clock=44, from_xy=(90.0, 40.0), to_xy=(100.0, 45.0),
                  outcome="success", player=_ALARO),
        ]
        steps = extract_build_up(events, goal_of(events))
        self.assertEqual(len(steps), 2, "the fixture should offer exactly one pair")
        self.assertFalse(any(step.derived for step in steps),
                         "the same-clock pair was read as a carry")


class TestHighlightForOneGoal(unittest.TestCase):
    """`highlight_for_goal` is the single-goal entry point the others are built on."""

    def test_it_returns_the_whole_highlight(self) -> None:
        events = three_pass_move()
        goal = goal_of(events)
        highlight = highlight_for_goal(events, goal, seed=99)
        self.assertEqual(highlight.goal_id, goal.id)
        self.assertEqual(highlight.scorer, goal.player.name)
        self.assertEqual(highlight.team, goal.team)
        self.assertEqual(highlight.minute, goal.minute)
        self.assertEqual(highlight.clock, goal.clock)
        self.assertEqual(highlight.seed, 99)
        self.assertEqual(len(highlight.steps), 4)
        self.assertEqual(highlight.summary.steps, 4)
        self.assertEqual(highlight.finish_xy, goal.to_xy)
        self.assertIn(LABEL_SYNTHETIC, highlight.labels)
        self.assertFalse(highlight.has_derived_carries)

    def test_the_seed_is_the_callers_and_not_the_events(self) -> None:
        """The event stream carries no seed, so the highlight can only report what it
        was told. Two seeds over one match must differ in that field alone."""
        events = three_pass_move()
        goal = goal_of(events)
        self.assertNotEqual(
            highlight_for_goal(events, goal, seed=1).to_dict(),
            highlight_for_goal(events, goal, seed=2).to_dict(),
        )
        self.assertEqual(
            highlight_for_goal(events, goal, seed=1).steps,
            highlight_for_goal(events, goal, seed=2).steps,
        )

    def test_it_labels_a_move_that_inferred_a_carry(self) -> None:
        events = _MOVE_WITH_A_GAP()
        highlight = highlight_for_goal(events, goal_of(events), seed=1)
        self.assertTrue(highlight.has_derived_carries)
        self.assertIn(LABEL_SYNTHETIC, highlight.labels)
        self.assertIn(LABEL_DERIVED_CARRIES, highlight.labels)
        self.assertEqual(highlight.summary.carries, 1)

    def test_a_non_goal_is_refused(self) -> None:
        events = three_pass_move()
        with self.assertRaises(ValueError):
            highlight_for_goal(events, events[1], seed=1)


class TestMoveSummary(unittest.TestCase):
    """Task 2: every value checked against arithmetic done by hand on the fixture."""

    def setUp(self) -> None:
        self.events = three_pass_move()
        self.goal = goal_of(self.events)
        self.steps = extract_build_up(self.events, self.goal)
        self.summary = summarise_move(self.steps, goal_clock=self.goal.clock)

    def test_counts_the_passes(self) -> None:
        self.assertEqual(self.summary.passes, 3)

    def test_counts_no_carries_in_a_move_that_had_none(self) -> None:
        self.assertEqual(self.summary.carries, 0)

    def test_counts_the_finish(self) -> None:
        self.assertEqual(self.summary.shots, 1)
        self.assertEqual(self.summary.steps, 4)

    def test_total_distance_is_the_sum_of_the_step_distances(self) -> None:
        """Worked out here, not read off the code:

        30 + sqrt(20² + 20²) + sqrt(25² + 20²) + 15
        = 30 + 28.28 + 32.02 + 15 = 105.30
        """
        self.assertAlmostEqual(self.summary.distance, 105.30, places=2)

    def test_distance_is_the_sum_of_the_rounded_steps_not_a_second_rounding(self) -> None:
        """The number shown must equal the numbers a viewer can add up themselves."""
        self.assertAlmostEqual(
            self.summary.distance, round(sum(s.distance for s in self.steps), 2), places=6
        )

    def test_duration_runs_from_the_first_step_to_the_goal(self) -> None:
        """104 − 84 = 16 seconds, measured to the goal rather than to the last pass."""
        self.assertEqual(self.summary.duration, 16)

    def test_start_zone_is_the_third_the_move_began_in(self) -> None:
        """It began at x = 10, which for the home side is its own defensive third."""
        self.assertEqual(self.summary.start_zone, "defensive")
        self.assertEqual(self.summary.start_xy, (10.0, 50.0))

    def test_a_negative_duration_is_clamped_rather_than_reported(self) -> None:
        """"Minus four seconds" is a worse answer than zero."""
        summary = summarise_move(self.steps, goal_clock=0)
        self.assertEqual(summary.duration, 0)

    def test_an_empty_move_summarises_to_zero_and_an_unknown_zone(self) -> None:
        summary = summarise_move((), goal_clock=500)
        self.assertEqual(
            (summary.passes, summary.carries, summary.shots, summary.steps,
             summary.distance, summary.duration, summary.start_zone, summary.start_xy),
            (0, 0, 0, 0, 0.0, 0, "unknown", None),
        )
        self.assertTrue(summary.is_empty)

    def test_the_summary_agrees_with_the_steps_it_was_built_from(self) -> None:
        rebuilt = summarise_move(self.steps, goal_clock=self.goal.clock)
        self.assertEqual(rebuilt, self.summary)


class TestZones(unittest.TestCase):
    """Thirds are read in the attacking direction, which is the whole subtlety."""

    def test_the_home_side_attacks_towards_increasing_x(self) -> None:
        self.assertEqual(zone_for(10.0, "home"), "defensive")
        self.assertEqual(zone_for(50.0, "home"), "middle")
        self.assertEqual(zone_for(90.0, "home"), "final")

    def test_the_away_side_attacks_the_other_way(self) -> None:
        """The same coordinate is the opposite third for the other team. Reading
        zones off the raw coordinate would call an away move that began on its own
        goal line a final-third move, which is the reverse of the truth."""
        self.assertEqual(zone_for(90.0, "away"), "defensive")
        self.assertEqual(zone_for(50.0, "away"), "middle")
        self.assertEqual(zone_for(10.0, "away"), "final")

    def test_the_boundaries_are_where_the_thirds_meet(self) -> None:
        self.assertEqual(zone_for(33.3, "home"), "defensive")
        self.assertEqual(zone_for(33.4, "home"), "middle")
        self.assertEqual(zone_for(66.6, "home"), "middle")
        self.assertEqual(zone_for(66.7, "home"), "final")

    def test_an_away_move_summarises_with_its_own_third(self) -> None:
        events = three_pass_move(team="away")
        goal = goal_of(events)
        summary = summarise_move(extract_build_up(events, goal), goal_clock=goal.clock)
        self.assertEqual(summary.start_zone, "defensive")
        self.assertEqual(summary.passes, 3)


class TestHighlightPayload(unittest.TestCase):
    """Task 3: one JSON object per goal, deterministic for a given seed."""

    def test_two_runs_with_the_same_seed_produce_identical_json(self) -> None:
        first = json.dumps(highlight_payload(7), sort_keys=True)
        second = json.dumps(highlight_payload(7), sort_keys=True)
        self.assertEqual(first, second)

    def test_a_different_seed_produces_a_different_payload(self) -> None:
        self.assertNotEqual(highlight_payload(7), highlight_payload(8))

    def test_the_payload_carries_everything_the_task_asks_for(self) -> None:
        for highlight in highlight_payload(7):
            with self.subTest(goal=highlight["goal_id"]):
                self.assertIn("steps", highlight)
                self.assertIn("summary", highlight)
                self.assertTrue(highlight["scorer"])
                self.assertIsInstance(highlight["minute"], int)
                self.assertEqual(highlight["seed"], 7)

    def test_the_payload_is_strict_json(self) -> None:
        """A non-finite float would serialise to ``Infinity``, which the browser
        refuses to parse, and the panel would fail with nothing in Python to see."""
        json.dumps(highlight_payload(7), allow_nan=False)

    def test_the_payload_survives_a_round_trip(self) -> None:
        payload = highlight_payload(7)
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_the_steps_are_indexed_in_order_without_gaps(self) -> None:
        for highlight in highlight_payload(7):
            indices = [step["index"] for step in highlight["steps"]]
            self.assertEqual(indices, list(range(len(indices))))

    def test_step_coordinates_are_pairs_of_numbers(self) -> None:
        for highlight in highlight_payload(7):
            for step in highlight["steps"]:
                for key in ("start_xy", "end_xy"):
                    self.assertEqual(len(step[key]), 2)
                    self.assertTrue(all(isinstance(v, (int, float)) for v in step[key]))

    def test_every_goal_in_the_match_is_represented(self) -> None:
        match = generate_match(7)
        goals = [e for e in match.events if e.type == "goal"]
        payload = highlight_payload(7)
        self.assertEqual(len(payload), len(goals))
        self.assertEqual(
            [h["goal_id"] for h in payload], [g.id for g in goals]
        )

    def test_until_drops_goals_a_shortened_replay_never_reaches(self) -> None:
        """The overlay passes its own final clock, so it cannot advertise a goal it
        will never get to."""
        match = generate_match(7)
        goals = [e for e in match.events if e.type == "goal"]
        cutoff = goals[0].clock
        payload = highlight_payload(7, until=cutoff)
        self.assertTrue(all(h["clock"] <= cutoff for h in payload))
        self.assertEqual(len(payload), 1)

    def test_the_payload_agrees_with_the_highlights_built_from_the_events(self) -> None:
        """The standalone entry point and the one the overlay uses must not diverge."""
        match = generate_match(7)
        from_events = [h.to_dict() for h in build_highlights(match.events, seed=7)]
        self.assertEqual(highlight_payload(7), from_events)


class TestLabelling(unittest.TestCase):
    """Task 5: the provenance label is on the data, not on the renderer."""

    def test_every_highlight_says_it_came_from_a_synthetic_match(self) -> None:
        for highlight in highlight_payload(7):
            with self.subTest(goal=highlight["goal_id"]):
                self.assertIn(LABEL_SYNTHETIC, highlight["labels"])

    def test_the_derived_label_is_present_exactly_when_a_step_was_inferred(self) -> None:
        """Both directions, on fixtures that force each one.

        A generated match only ever exercises the negative case — see the test below
        for why — so the positive case has to come from a fixture with a genuine gap
        in it. Asserting the label follows the steps is the point; which of the two
        a match happens to contain is not. `_MOVE_WITH_A_GAP` is that gap.
        """
        events = _MOVE_WITH_A_GAP()
        inferred = highlight_for_goal(events, goal_of(events), seed=1)
        self.assertTrue(inferred.has_derived_carries)
        self.assertIn(LABEL_SYNTHETIC, inferred.labels)
        self.assertIn(LABEL_DERIVED_CARRIES, inferred.labels)
        self.assertTrue(any(step["derived"] for step in inferred.to_dict()["steps"]))

        clean_events = three_pass_move()
        recorded = highlight_for_goal(clean_events, goal_of(clean_events), seed=1)
        self.assertFalse(recorded.has_derived_carries)
        self.assertIn(LABEL_SYNTHETIC, recorded.labels)
        self.assertNotIn(LABEL_DERIVED_CARRIES, recorded.labels)

    def test_no_generated_match_ever_claims_an_inferred_carry(self) -> None:
        """A coherent stream leaves nothing to infer.

        A carry *is* unexplained ball movement. Our generator accounts for every
        metre a ball travels — consecutive events in a possession share a position —
        so the derivation has nothing to fire on, and a generated match claiming one
        would mean the ball had jumped somewhere with no event to explain it. This is
        the assertion that would have caught the shot-teleport: while it existed, the
        ball jumped the length of every shot and the derivation fired on the gap.
        """
        for seed in range(1, 25):
            match = generate_match(seed)
            for highlight in build_highlights(match.events, seed=seed):
                self.assertFalse(highlight.has_derived_carries, f"seed {seed}")
                self.assertNotIn(LABEL_DERIVED_CARRIES, highlight.labels)

    def test_generated_matches_reach_the_panel_through_recorded_carries(self) -> None:
        """The carry figure is not dead: the stream really carries the ball."""
        total = 0
        for seed in range(1, 25):
            match = generate_match(seed)
            for highlight in build_highlights(match.events, seed=seed):
                total += highlight.summary.carries
                for step in highlight.steps:
                    if step.kind == "carry":
                        self.assertFalse(step.derived, "a recorded carry is not derived")
        self.assertGreater(total, 20)

    def test_no_label_claims_this_is_real_footage(self) -> None:
        """The one thing the labelling exists to prevent."""
        banned = ("real", "footage", "broadcast", "tracking data", "live feed", "official")
        for seed in (1, 7, 42):
            for highlight in highlight_payload(seed):
                for label in highlight["labels"]:
                    for term in banned:
                        self.assertNotIn(term, label.lower())

    def test_no_player_or_club_in_the_payload_is_a_real_one(self) -> None:
        banned = ("premier league", "manchester", "liverpool", "arsenal", "fifa", "uefa")
        for seed in (1, 7, 42):
            haystack = json.dumps(highlight_payload(seed)).lower()
            for term in banned:
                self.assertNotIn(term, haystack)


class TestAgainstGeneratedMatches(unittest.TestCase):
    """The fixtures prove the rules; these prove the rules survive real data."""

    def test_every_goal_of_every_seed_extracts_without_error(self) -> None:
        """One highlight per goal, in order — not merely "some highlights".

        The floor on the total is a coverage guard: it was set from the goal rate the
        generator happens to produce, and when a generator change moved that rate the
        guard failed while the behaviour was fine. It is deliberately loose now, and
        the equality against the match's own goal count is the real assertion.
        """
        checked = 0
        for seed in range(1, 25):
            match = generate_match(seed)
            goals = [e for e in match.events if e.type == "goal"]
            highlights = build_highlights(match.events, seed=seed)
            self.assertEqual(len(highlights), len(goals), f"seed {seed}")
            for goal, highlight in zip(goals, highlights):
                checked += 1
                self.assertEqual(highlight.goal_id, goal.id)
                self.assertEqual(highlight.seed, seed)
                self.assertIn(highlight.team, ("home", "away"))
        self.assertGreater(checked, 40)

    def test_extracted_steps_chain_end_to_start(self) -> None:
        """Every step starts exactly where the last one ended.

        This is the assertion that catches a ball which jumps, and it holds for a
        derived step too: a derived carry runs from where the previous touch left the
        ball to where the next one picks it up, so it *fills* the gap rather than
        breaking the chain.

        Two exemptions used to sit here — one for a shot, called a rebound, and one
        for derived steps. The first was an excuse for a generator that left the ball
        behind after a shot; the second for an extraction that dropped carries out of
        the middle of a move. Both are fixed, so there is no exemption left and the
        path has to be continuous everywhere.
        """
        for seed in range(1, 15):
            match = generate_match(seed)
            for highlight in build_highlights(match.events, seed=seed):
                steps = highlight.steps
                for earlier, later in zip(steps, steps[1:]):
                    self.assertEqual(
                        earlier.end_xy, later.start_xy,
                        f"seed {seed} goal {highlight.goal_id}: break after {earlier.kind}",
                    )

    def test_a_derived_carry_is_never_a_repeat_of_the_touch_before_it(self) -> None:
        for seed in range(1, 15):
            match = generate_match(seed)
            for highlight in build_highlights(match.events, seed=seed):
                for step in highlight.steps:
                    if step.derived:
                        self.assertNotEqual(step.start_xy, step.end_xy)

    def test_the_summary_counts_the_steps_it_was_built_from(self) -> None:
        for seed in range(1, 15):
            match = generate_match(seed)
            for highlight in build_highlights(match.events, seed=seed):
                summary = highlight.summary
                self.assertEqual(summary.steps, len(highlight.steps))
                self.assertEqual(
                    summary.passes + summary.carries + summary.shots, summary.steps
                )

    def test_a_generated_move_never_has_a_negative_duration(self) -> None:
        for seed in range(1, 15):
            match = generate_match(seed)
            for highlight in build_highlights(match.events, seed=seed):
                self.assertGreaterEqual(highlight.summary.duration, 0)

    def test_the_generator_assigns_a_usable_possession_to_every_event(self) -> None:
        """The field the whole module rests on, checked on real output.

        Match-level markers are the exception. Kick-off, half-time and full time are
        not things a side did with the ball, so they carry the "no possession" id —
        attributing them to whichever spell happened to be open is what left the last
        possession of a match holding events from both teams.
        """
        match = generate_match(7)
        markers = {"kickoff", "half_time", "full_time"}
        for event in match.events:
            if event.type in markers:
                self.assertEqual(event.possession_id, 0, event.type)
            else:
                self.assertGreater(event.possession_id, 0, event.type)

        # Monotonic over the events that belong to a spell. Including the markers
        # would fail on a technicality rather than on a real ordering problem, since a
        # marker carries 0 wherever it falls.
        ids = [e.possession_id for e in match.events if e.possession_id > 0]
        self.assertEqual(ids, sorted(ids), "possession ids must never go backwards")

    def test_every_event_in_a_possession_belongs_to_one_side_unless_it_is_a_tackle(self) -> None:
        """Grouping is right when the only cross-team event is a tackle.

        A tackle is recorded against the side that attempted it, and that side is not
        always the side holding the ball: a lost tackle sits mid-possession without
        ending it, and the tackle that *won* the ball sits at the end carrying the
        possession id it ended. So the assertion is about every event that is not a
        tackle — those must all belong to one team. Anything else would mean the
        possession id had drifted from the ball.
        """
        match = generate_match(7)
        by_possession: dict[int, list] = {}
        for e in match.events:
            by_possession.setdefault(e.possession_id, []).append(e)

        for pid, events in by_possession.items():
            if pid == 0:
                # Not a possession: the match-level markers. They belong to no spell,
                # so there is no team for them to be consistent with.
                self.assertTrue(all(e.type in ("kickoff", "half_time", "full_time") for e in events))
                continue
            with self.subTest(possession=pid):
                holding = {e.team for e in events if e.type != "tackle"}
                self.assertLessEqual(
                    len(holding), 1, f"possession {pid} swapped teams outside a tackle"
                )


class TestTheStreamThePanelDrawsFrom(unittest.TestCase):
    """Generator invariants the build-up panel depends on.

    These test the event stream rather than the extraction, because the panel can only
    be as sensible as the stream underneath it. They live here rather than in
    ``test_generator`` because ``highlights`` is the module that relies on them, and
    these are the exact properties whose absence made the drawn move nonsense.
    """

    def test_the_ball_never_jumps_between_consecutive_events_in_a_possession(self) -> None:
        """A shot used to leave the ball where it was *struck*.

        The next event therefore began at the shot's own starting point, so a move
        jumped the length of the shot backwards between two touches and the panel drew
        the ball returning to where it came from. The goal and the shot that produced
        it are the one legitimate exception: they are a single strike recorded from
        both ends, so the shot's *target* and the goal's *origin* are expected to
        differ.
        """
        for seed in range(1, 25):
            events = generate_match(seed).events
            for earlier, later in zip(events, events[1:]):
                if earlier.possession_id != later.possession_id:
                    continue
                if earlier.to_xy is None or later.from_xy is None:
                    continue
                if earlier.type == "shot" and later.type == "goal":
                    continue
                self.assertEqual(
                    earlier.to_xy, later.from_xy,
                    f"seed {seed}: {earlier.type} -> {later.type} jumped",
                )

    def test_a_shot_that_does_not_score_ends_the_spell(self) -> None:
        """Otherwise the ball has to reappear wherever the shot was taken from."""
        for seed in range(1, 25):
            events = generate_match(seed).events
            for earlier, later in zip(events, events[1:]):
                if earlier.type != "shot" or later.type == "goal":
                    continue
                self.assertNotEqual(
                    later.possession_id, earlier.possession_id,
                    f"seed {seed}: play continued after a shot that did not score",
                )

    def test_a_shot_is_never_taken_from_outside_the_long_shot_limit(self) -> None:
        """An unbounded long shot produced efforts from a side's own corner.

        Measured in metres, with the generator's own function. This used to be measured
        in grid units, and the two scales differ by a factor that varies with the
        *direction* of the shot — so the check and the rule it was checking could
        disagree without either failing.
        """
        from pitchframe.generator import LONG_SHOT_RANGE, goal_distance

        for seed in range(1, 25):
            for event in generate_match(seed).events:
                if event.type != "shot":
                    continue
                distance = goal_distance(event.from_xy, event.team)
                self.assertLessEqual(distance, LONG_SHOT_RANGE + 1e-6, f"seed {seed}")

    def test_a_build_up_nets_forward_rather_than_wandering(self) -> None:
        """The move that produced a goal got closer to the goal.

        Passes used to be an unbiased step on both axes, so a possession was a random
        walk: it could spend ten touches in one corner and still end with a shot there.
        A move that nets backwards is not a build-up, and drawing it as one is what
        made the panel read as a scribble.
        """
        for seed in range(1, 25):
            match = generate_match(seed)
            for highlight in build_highlights(match.events, seed=seed):
                if not highlight.steps:
                    continue
                first = highlight.steps[0].start_xy
                last = highlight.steps[-1].end_xy
                net = last[0] - first[0] if highlight.team == "home" else first[0] - last[0]
                self.assertGreater(net, 0.0, f"seed {seed} goal {highlight.goal_id}")


class TestMoveSummaryType(unittest.TestCase):
    """A small guard that the public shape stayed put."""

    def test_summarise_move_returns_a_move_summary(self) -> None:
        self.assertIsInstance(summarise_move((), goal_clock=0), MoveSummary)


if __name__ == "__main__":
    unittest.main()
