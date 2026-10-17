"""Tests for the player ratings and the man of the match.

A rating is the one number on that page that is a judgement rather than a
measurement. What makes a judgement publishable here is that the formula behind it is
fixed, written down, and applied to everyone identically — so these tests are about
the judgement being *recomputable*:

1. **The credits are recounted from the events.** A goal is the scorer's, and the side
   that conceded it wears it; an on-target strike that produced a goal is not also a
   save; a carry is only progressive if it moved the ball. Each of those is a rule
   that a plausible-looking implementation could get wrong in a way no reader of the
   panel would notice.
2. **The arithmetic is asserted term by term.** The weights and the caps are public,
   so a test can rebuild a rating from them instead of asserting on a total that
   several different bugs could produce.
3. **The prose says only what the tallies count.** The conclusion is a renderer over
   facts; it may say a player scored twice and may not say that this is why the match
   was won. That line is checked rather than trusted.

The fixtures are hand-built tallies and event lists whose answers can be counted by
eye, and the last class checks the same properties against the matches the generator
actually produces, because the weights were calibrated against those and not against
anything else.
"""

from __future__ import annotations

import itertools
import unittest

from pitchframe.generator import generate_match
from pitchframe.ratings import (
    BASE_RATING,
    CAPS,
    CEILING,
    FLOOR,
    LABELS,
    WEIGHTS,
    award_for,
    components,
    counts_of,
    conclusion_for,
    leader_of,
    rate_squads,
    rating_from,
    ratings_frame,
    ratings_payload,
    reasons_for,
    Tally,
)
from pitchframe.schema import MatchEvent, Player, Squad, Team
from pitchframe.stats import PRESSURE_THRESHOLD

_HOME_GK = Player(id="H01", name="Alaro", team="home", position="GK", shirt=1)
_HOME_CB = Player(id="H03", name="Belden", team="home", position="CB", shirt=3)
_HOME_CM = Player(id="H08", name="Coren", team="home", position="CM", shirt=8)
_HOME_ST = Player(id="H09", name="Doren", team="home", position="ST", shirt=9)

_AWAY_GK = Player(id="A01", name="Eskil", team="away", position="GK", shirt=1)
_AWAY_CB = Player(id="A04", name="Friden", team="away", position="CB", shirt=4)
_AWAY_CM = Player(id="A07", name="Grond", team="away", position="CM", shirt=7)

_counter = itertools.count(1)


def make_event(
    kind: str,
    *,
    player: Player | None = None,
    team: Team = "home",
    clock: int = 0,
    outcome: str | None = None,
    pressure: float | None = None,
    from_xy: tuple[float, float] | None = None,
    to_xy: tuple[float, float] | None = None,
) -> MatchEvent:
    """Build one event for a fixture, with a unique id."""
    return MatchEvent(
        id=f"T-E{next(_counter):04d}",
        match_id="T",
        clock=clock,
        type=kind,  # type: ignore[arg-type]
        team=team,
        player=player if player is not None else (_HOME_CM if team == "home" else _AWAY_CM),
        from_xy=from_xy,
        to_xy=to_xy,
        outcome=outcome,  # type: ignore[arg-type]
        pressure=pressure,
    )


def squads(
    home: tuple[Player, ...] = (_HOME_GK, _HOME_CB, _HOME_CM, _HOME_ST),
    away: tuple[Player, ...] = (_AWAY_GK, _AWAY_CB, _AWAY_CM),
) -> dict[Team, Squad]:
    """A two-sided fixture, keeper included, which is what the credits are keyed on."""
    return {
        "home": Squad(team="home", name="Northgate Athletic", players=home),
        "away": Squad(team="away", name="Riverside United", players=away),
    }


def rating_of(player: Player, events: list[MatchEvent], score: tuple[int, int]) -> float:
    """One player's rating, by finding their row in a full run over the fixture."""
    table = squads()
    if player.team == "away":
        table = squads(home=table["home"].players, away=table["away"].players + (player,))
    rows = {row.player_id: row for row in rate_squads(events, table, score=score)}
    return rows[player.id].rating


class CountingTests(unittest.TestCase):
    """What each player is credited with. One pass over the stream, many rules."""

    def test_the_shape_events_are_nobody_s_touch(self) -> None:
        """Kick-off, half time and full time are on the clock and not part of play.
        Counting them would inflate whoever the generator happened to name on them."""
        events = [
            make_event("kickoff", clock=0),
            make_event("half_time", clock=2700),
            make_event("full_time", clock=5400),
        ]
        rows = rate_squads(events, squads(), score=(0, 0))
        for row in rows:
            with self.subTest(player=row.player_id):
                self.assertEqual(row.tally.events, 0)
                self.assertEqual(row.rating, BASE_RATING)

    def test_a_goal_is_the_scorer_s_and_is_worn_by_the_other_keeper(self) -> None:
        """Nothing in the stream attributes a goal to the player it beat, so the
        keeper's goals-against is derived from the other side's goals — or not
        counted at all."""
        events = [
            make_event("goal", team="away", player=_AWAY_CM, clock=600, outcome="success")
        ]
        rows = {row.player_id: row for row in rate_squads(events, squads(), score=(0, 1))}
        self.assertEqual(rows["A07"].tally.goals, 1)
        self.assertEqual(rows["H01"].tally.conceded, 1)
        self.assertEqual(rows["A01"].tally.conceded, 0)

    def test_an_on_target_strike_that_scored_is_not_also_a_stop(self) -> None:
        """The generator emits the shot and the goal it produced next to each other on
        the same clock. Counting the shot as a save as well would credit the keeper for
        the goal that beat them and take it back off the scorer's side twice."""
        events = [
            make_event("shot", team="home", player=_HOME_ST, clock=600, outcome="success"),
            make_event("goal", team="home", player=_HOME_ST, clock=600, outcome="success"),
        ]
        rows = {row.player_id: row for row in rate_squads(events, squads(), score=(1, 0))}
        self.assertEqual(rows["H09"].tally.shots_on_target, 1)
        self.assertEqual(rows["A01"].tally.stops, 0)
        self.assertEqual(rows["A01"].tally.conceded, 1)

    def test_an_on_target_strike_with_no_goal_is_a_stop(self) -> None:
        """The other half of the same rule: a strike that beat nobody is the keeper's."""
        events = [make_event("shot", team="home", player=_HOME_ST, clock=600, outcome="success")]
        rows = {row.player_id: row for row in rate_squads(events, squads(), score=(0, 0))}
        self.assertEqual(rows["A01"].tally.stops, 1)
        self.assertEqual(rows["A01"].tally.conceded, 0)

    def test_a_strike_is_read_against_the_goals_on_its_own_clock(self) -> None:
        """A goal on a later clock is a different move, so the strike stands as a
        save. Reading only the next element would credit a stop for a goal that is
        plainly in the stream, and the keeper's number would move for no reason."""
        events = [
            make_event("shot", team="home", player=_HOME_ST, clock=600, outcome="success"),
            make_event("goal", team="home", player=_HOME_ST, clock=601, outcome="success"),
        ]
        rows = {row.player_id: row for row in rate_squads(events, squads(), score=(1, 0))}
        self.assertEqual(rows["A01"].tally.stops, 1)

    def test_an_off_target_strike_is_nobody_s_save(self) -> None:
        """A shot that missed is a miss. Crediting the keeper for it would make every
        defence look busy and every strike look like a save."""
        events = [
            make_event("shot", team="home", player=_HOME_ST, clock=600, outcome="fail"),
        ]
        rows = {row.player_id: row for row in rate_squads(events, squads(), score=(0, 0))}
        self.assertEqual(rows["H09"].tally.shots_off_target, 1)
        self.assertEqual(rows["A01"].tally.stops, 0)

    def test_a_pass_under_pressure_needs_the_threshold(self) -> None:
        """The tally feeds the panel's own claim about pressure, so it has to use the
        same threshold the metrics do rather than a second one of its own."""
        events = [
            make_event("pass", outcome="success", pressure=PRESSURE_THRESHOLD + 0.01),
            make_event("pass", outcome="success", pressure=PRESSURE_THRESHOLD - 0.01),
            make_event("pass", outcome="fail", pressure=PRESSURE_THRESHOLD + 0.01),
        ]
        rows = {row.player_id: row for row in rate_squads(events, squads(), score=(0, 0))}
        tally = rows["H08"].tally
        self.assertEqual(tally.passes_completed, 2)
        self.assertEqual(tally.completed_under_pressure, 1)
        self.assertEqual(tally.passes_failed, 1)

    def test_a_carry_is_progressive_only_when_it_moves_the_ball(self) -> None:
        """Every carry is a touch; only some of them take the ball somewhere. A test
        that called them all progressive would make a square ball worth as much as a
        run at the defence."""
        events = [
            make_event("carry", from_xy=(40.0, 50.0), to_xy=(46.0, 50.0)),
            make_event("carry", from_xy=(40.0, 50.0), to_xy=(60.0, 50.0)),
        ]
        rows = {row.player_id: row for row in rate_squads(events, squads(), score=(0, 0))}
        self.assertEqual(rows["H08"].tally.carries, 2)
        self.assertEqual(rows["H08"].tally.progressive_carries, 1)

    def test_a_carry_with_nowhere_recorded_still_counts_as_a_carry(self) -> None:
        """An imported feed may carry the carrier and not the positions. The touch is
        still a touch; only the progressiveness is unknowable."""
        events = [make_event("carry")]
        rows = {row.player_id: row for row in rate_squads(events, squads(), score=(0, 0))}
        self.assertEqual(rows["H08"].tally.carries, 1)
        self.assertEqual(rows["H08"].tally.progressive_carries, 0)

    def test_tackles_are_counted_by_outcome(self) -> None:
        """Winning the ball and being beaten are the two halves of a defender's match,
        and only one of them can be read from the event's existence."""
        events = [
            make_event("tackle", outcome="success"),
            make_event("tackle", outcome="success"),
            make_event("tackle", outcome="fail"),
        ]
        rows = {row.player_id: row for row in rate_squads(events, squads(), score=(0, 0))}
        self.assertEqual(rows["H08"].tally.tackles_won, 2)
        self.assertEqual(rows["H08"].tally.tackles_lost, 1)


class ArithmeticTests(unittest.TestCase):
    """The published weights, applied. Asserted term by term rather than as a total."""

    def test_every_counted_term_has_a_weight(self) -> None:
        """The bug this guards is a real one: the counting and the wording were two
        dicts keyed differently, the lookup missed, and the term was silently skipped.
        A count with no weight is a contribution nobody is paid for."""
        counted = set(counts_of(Tally()))
        self.assertTrue(counted <= set(WEIGHTS), counted - set(WEIGHTS))
        # The caps are the other direction of the same rule: a cap on a term nothing
        # counts is a limit on a contribution that never arrives.
        self.assertTrue(set(CAPS) <= counted, set(CAPS) - counted)

    def test_a_rating_is_the_base_plus_its_terms(self) -> None:
        """Recomputed from the published weights, so a rating that stopped being the
        sum of its parts would fail here rather than in a reader's head."""
        tally = Tally(goals=1, passes_completed=20, tackles_won=3, stops=2)
        expected = BASE_RATING + sum(components(tally, "win").values())
        self.assertEqual(rating_from(tally, "win"), round(expected, 1))

    def test_a_volume_term_is_capped(self) -> None:
        """A player who completed ninety passes is having the same kind of good game
        as one who completed forty-five. Without the cap a busy stream decides the
        rating on its own and the panel becomes a volume chart."""
        modest = Tally(passes_completed=45)
        extreme = Tally(passes_completed=400)
        self.assertEqual(components(extreme, "draw")["pass_completed"], CAPS["pass_completed"])
        self.assertEqual(rating_from(extreme, "draw"), rating_from(modest, "draw"))

    def test_the_scale_holds_at_both_ends(self) -> None:
        """One tally cannot produce a figure the panel would have to explain, in
        either direction — and both of these would without the clamps."""
        awful = Tally(passes_failed=90, shots_off_target=80, conceded=9)
        monster = Tally(goals=5, shots_on_target=6, stops=4, passes_completed=60, tackles_won=9)
        self.assertEqual(rating_from(awful, "lose"), FLOOR)
        self.assertEqual(rating_from(monster, "win"), CEILING)
        self.assertLess(BASE_RATING + sum(components(awful, "lose").values()), FLOOR)
        self.assertGreater(BASE_RATING + sum(components(monster, "win").values()), CEILING)

    def test_a_player_the_stream_never_mentioned_sits_on_the_base(self) -> None:
        """That is the honest reading of a player a windowed stream does not mention,
        and it is why the panel prints the tallies next to the number."""
        rows = {row.player_id: row for row in rate_squads([], squads(), score=(0, 0))}
        self.assertEqual(rows["H03"].rating, BASE_RATING)
        self.assertEqual(rows["H03"].tally, Tally())
        self.assertEqual(rows["H03"].reasons, ())

    def test_the_result_is_worth_less_than_anything_a_player_did(self) -> None:
        """A win is a fact about a side, not about one player. It breaks ties between
        two similar displays and cannot lift a quiet afternoon above a good one."""
        scorer = Tally(goals=1, passes_completed=26)
        quiet_winner = Tally(passes_completed=30, tackles_won=3)
        self.assertGreater(rating_from(scorer, "lose"), rating_from(quiet_winner, "win"))
        self.assertGreater(WEIGHTS["win"] - WEIGHTS["lose"], 0)
        self.assertLess(WEIGHTS["win"], WEIGHTS["goal"])

class OrderingTests(unittest.TestCase):
    """The order the panel prints, which readers read as a ranking."""

    def test_rows_are_best_first(self) -> None:
        events = [
            make_event("goal", player=_AWAY_CM, team="away", clock=600, outcome="success"),
            make_event("pass", player=_HOME_CM, outcome="success"),
        ]
        rows = rate_squads(events, squads(), score=(0, 1))
        ratings = [row.rating for row in rows]
        self.assertEqual(ratings, sorted(ratings, reverse=True))
        self.assertEqual(rows[0].player_id, "A07")

    def test_an_all_square_match_is_ordered_by_shirt_and_then_id(self) -> None:
        """Twenty-two players sit on the base in a replay the stream has not touched
        yet: the order still has to be total and identical on every run, because a
        panel that reshuffled equals between reloads would look like changed data."""
        first = rate_squads([], squads(), score=(0, 0))
        second = rate_squads([], squads(), score=(0, 0))
        self.assertEqual(
            [row.player_id for row in first], [row.player_id for row in second]
        )
        self.assertEqual([row.shirt for row in first], sorted(row.shirt for row in first))

    def test_the_leader_is_the_first_row_and_nothing_when_there_are_no_rows(self) -> None:
        """The panel's leader mark and the award read the same function, so they
        cannot disagree about who is top."""
        rows = rate_squads([], squads(), score=(0, 0))
        self.assertIs(leader_of(rows), rows[0])
        self.assertIsNone(leader_of(()))


class FrameTests(unittest.TestCase):
    """What the panel is allowed to know at a given clock."""

    def test_a_frame_stops_at_its_clock(self) -> None:
        """A rating that had already counted a goal from the 70th minute would put a
        number on screen the scoreboard disagrees with."""
        events = [
            make_event("goal", player=_AWAY_CM, team="away", clock=4200, outcome="success")
        ]
        early = ratings_frame(events, squads(), score=(0, 0), clock=600)
        late = ratings_frame(events, squads(), score=(0, 1), clock=5400)
        self.assertLess(early["values"]["A07"], late["values"]["A07"])
        self.assertEqual(early["clock"], 600)

    def test_a_frame_names_the_leader_its_own_rows_have(self) -> None:
        """The mark beside a name is drawn from this field, so a leader named from a
        different moment would point at a player the numbers do not support."""
        events = [
            make_event("goal", player=_AWAY_CM, team="away", clock=4200, outcome="success")
        ]
        frame = ratings_frame(events, squads(), score=(0, 1), clock=5400)
        rows = rate_squads(events, squads(), score=(0, 1), until=5400)
        self.assertEqual(frame["leader"], rows[0].player_id)


class AwardTests(unittest.TestCase):
    """The man of the match, and the paragraph that explains it."""

    def test_the_payload_carries_the_award_and_the_clock_it_may_be_named_from(self) -> None:
        """The payload is the finished script: the award is computed over the whole
        match and it is the *page* that holds it back. Naming a man of the match
        before the match was played would spoil the replay it exists to show."""
        events = [
            make_event("goal", player=_AWAY_CM, team="away", clock=4200, outcome="success")
        ]
        payload = ratings_payload(
            events, squads(), home="Northgate Athletic", away="Riverside United",
            score=(0, 1), decided_at=5400,
        )
        self.assertEqual(payload["labels"], list(LABELS))
        self.assertEqual(payload["decided_at"], 5400)
        self.assertEqual(payload["award"]["goals"], 1)
        self.assertEqual(payload["award"]["player_id"], "A07")
        self.assertIn("tally", payload["award"])

    def test_the_award_is_none_when_there_is_nobody_to_give_it_to(self) -> None:
        """A hand-built fixture with no squads is a replay nobody is in, and the
        panel has to have something to render."""
        self.assertIsNone(award_for((), home="A", away="B", score=(0, 0)))
        payload = ratings_payload(
            [], {}, home="A", away="B", score=(0, 0), decided_at=5400
        )
        self.assertIsNone(payload["award"])

    def test_the_conclusion_names_the_award_the_score_and_the_runner_up(self) -> None:
        """The paragraph is the reason the panel is not just a table."""
        winner = rate_squads(
            [make_event("goal", player=_AWAY_CM, team="away", outcome="success"),
             make_event("goal", player=_AWAY_CM, team="away", clock=600, outcome="success")],
            squads(), score=(0, 2),
        )[0]
        text = conclusion_for(
            winner, None, home="Northgate Athletic", away="Riverside United", score=(0, 2)
        )
        self.assertIn("Grond", text)
        self.assertIn(str(winner.rating), text)
        self.assertIn("Riverside United", text)
        self.assertIn("The match finished 0-2.", text)
        self.assertNotIn("was next on", text)

    def test_a_goal_is_written_the_way_a_reader_says_it(self) -> None:
        """Fixed phrasings per fact: the same performance always reads the same way,
        and "scored 1" is not how anybody says it."""
        for goals, phrase in ((1, "scored once"), (2, "scored twice"), (3, "scored 3")):
            with self.subTest(goals=goals):
                events = [make_event("goal", player=_AWAY_CM, team="away", clock=60 * n,
                                     outcome="success") for n in range(goals)]
                winner = rate_squads(events, squads(), score=(0, goals))[0]
                text = conclusion_for(
                    winner, None, home="Northgate Athletic", away="Riverside United",
                    score=(0, goals),
                )
                self.assertIn(phrase, text)

    def test_a_winner_with_no_highlight_is_explained_by_the_work(self) -> None:
        """A midfielder or a defender can win the award, and the paragraph cannot
        fall back on a goal that is not there — or it would describe a different
        player's match."""
        events = [
            make_event("pass", player=_HOME_CB, outcome="success") for _ in range(30)
        ] + [make_event("tackle", player=_HOME_CB, outcome="success") for _ in range(4)]
        rows = rate_squads(events, squads(), score=(0, 0))
        winner = rows[0]
        self.assertEqual(winner.player_id, "H03")
        text = conclusion_for(
            winner, rows[1], home="Northgate Athletic", away="Riverside United", score=(0, 0)
        )
        self.assertIn("completed 30 passes", text)
        self.assertIn("won 4 tackles", text)
        self.assertIn("was next on", text)

    def test_a_keeper_s_goals_against_are_admitted_rather_than_hidden(self) -> None:
        """A keeper can win the award on saves and still have conceded. Leaving that
        out would make the one sentence about their match the sentence they lost."""
        events = [
            make_event("shot", player=_HOME_ST, clock=600, outcome="success"),
            make_event("shot", player=_HOME_ST, clock=900, outcome="success"),
            make_event("shot", player=_HOME_ST, clock=1200, outcome="success"),
            make_event("goal", player=_HOME_ST, clock=1500, outcome="success"),
            make_event("shot", player=_HOME_CM, clock=1800, outcome="success"),
        ]
        winner = [row for row in rate_squads(events, squads(), score=(1, 0)) if row.player_id == "A01"][0]
        text = conclusion_for(
            winner, None, home="Northgate Athletic", away="Riverside United", score=(1, 0)
        )
        self.assertIn("kept 4 on-target strikes out", text)
        self.assertIn("It was not a clean sheet: Northgate Athletic scored 1 time.", text)

    def test_the_conclusion_claims_no_causes(self) -> None:
        """The renderer composes facts. "Because", "thanks to" and "therefore" are
        causes, and nothing in the stream says a performance caused a result."""
        forbidden = ("because", "thanks to", "due to", "therefore", "caused", "proves")
        for seed in (7, 11, 28, 42):
            with self.subTest(seed=seed):
                match = generate_match(seed)
                rows = rate_squads(
                    match.events, {"home": match.home, "away": match.away}, score=match.score
                )
                award = award_for(
                    rows, home=match.home.name, away=match.away.name, score=match.score
                )
                for word in forbidden:
                    self.assertNotIn(word, award.conclusion.lower())


class ReasonTests(unittest.TestCase):
    """The phrases beside the number. A rating has to be explained, not printed."""

    def test_the_reasons_name_the_largest_contribution_first(self) -> None:
        """Ordered by what the term was worth, so the panel leads with what a player
        actually did rather than with the first thing the loop reached."""
        tally = Tally(goals=1, stops=3, passes_completed=4)
        reasons = reasons_for(tally, "draw")
        self.assertEqual(reasons[0], "1 goal")
        self.assertEqual(reasons[1], "3 stops")
        self.assertEqual(len(reasons), 3)

    def test_a_reason_is_written_in_the_singular_and_the_plural(self) -> None:
        """Fixed phrasings, checked in both numbers: a card that read "2 carry" or
        "1 stops" would be the panel telling the reader it was not written for them."""
        expected = {
            "goals": ("1 goal", "2 goals"),
            "shots_on_target": ("1 shot on target", "2 shots on target"),
            "passes_completed": ("1 completed pass", "2 completed passes"),
            "completed_under_pressure": (
                "1 pass completed under pressure",
                "2 passes completed under pressure",
            ),
            "carries": ("1 carry", "2 carries"),
            "progressive_carries": ("1 progressive carry", "2 progressive carries"),
            "tackles_won": ("1 tackle won", "2 tackles won"),
            "stops": ("1 stop", "2 stops"),
        }
        for field, (one, many) in expected.items():
            with self.subTest(field=field):
                self.assertEqual(reasons_for(Tally(**{field: 1}), "draw", limit=1), (one,))
                self.assertEqual(reasons_for(Tally(**{field: 2}), "draw", limit=1), (many,))

    def test_a_reason_is_never_a_penalty(self) -> None:
        """A card of what a player did. A keeper whose leading line was "2 conceded"
        would be the panel picking the wrong man out of its own table."""
        keeper = Tally(conceded=2, passes_failed=6, tackles_lost=2, passes_completed=12)
        reasons = reasons_for(keeper, "lose")
        self.assertEqual(reasons, ("12 completed passes",))
        for phrase in reasons:
            self.assertNotIn("conceded", phrase)
            self.assertNotIn("given away", phrase)

    def test_a_result_is_not_a_reason(self) -> None:
        """Winning is a fact about a side. It is in the arithmetic and it is not
        evidence about a player, so it cannot headline their card."""
        for result in ("win", "draw", "lose"):
            with self.subTest(result=result):
                self.assertEqual(reasons_for(Tally(), result), ())


class GeneratedMatchTests(unittest.TestCase):
    """The same rules against the matches the weights were calibrated on."""

    SEEDS = (7, 11, 28, 42)

    @classmethod
    def setUpClass(cls) -> None:
        cls.matches = {seed: generate_match(seed) for seed in cls.SEEDS}

    def _rows(self, seed: int):
        match = self.matches[seed]
        return rate_squads(
            match.events, {"home": match.home, "away": match.away}, score=match.score
        )

    def test_every_player_is_rated_and_inside_the_scale(self) -> None:
        """Twenty-two rows, one per player, and a number a reader can read: anything
        at or above ten would be the page printing a clamped figure as if it were a
        measurement."""
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                match = self.matches[seed]
                rows = self._rows(seed)
                self.assertEqual(len(rows), len(match.home.players) + len(match.away.players))
                self.assertEqual(len({row.player_id for row in rows}), len(rows))
                for row in rows:
                    self.assertGreaterEqual(row.rating, FLOOR)
                    self.assertLess(row.rating, CEILING)

    def test_the_weights_do_not_saturate_a_real_squad(self) -> None:
        """The calibration, pinned. The first set of weights produced a page of 9s and
        10s in which a full-back with an ordinary match outranked a striker who had
        scored twice, because a squad's worth of players all complete between eighteen
        and forty-eight passes. These bounds are what "a page of ordinary numbers, with
        the good matches in the eights" means in figures."""
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                ratings = [row.rating for row in self._rows(seed)]
                self.assertLess(sum(ratings) / len(ratings), 8.0)
                self.assertLessEqual(sum(1 for value in ratings if value >= 8.5), 2)

    def test_a_sparse_contribution_outranks_ordinary_volume(self) -> None:
        """What the weights are for: a goal, a save or a piece of defending has to be
        worth more than any amount of passing everyone was doing anyway."""
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                winner = self._rows(seed)[0]
                tally = winner.tally
                self.assertTrue(tally.goals or tally.stops, winner.player_id)

    def test_every_row_carries_the_tally_behind_its_number(self) -> None:
        """The panel shows the evidence beside the figure, so a row that travelled
        without it would be a number nobody can check."""
        row = self._rows(28)[0]
        carried = row.to_dict()
        self.assertIn("tally", carried)
        self.assertEqual(carried["tally"], row.tally.to_dict())
        self.assertEqual(carried["rating"], row.rating)

    def test_the_same_match_rates_the_same_way_twice(self) -> None:
        """Determinism is the property every other number here has, and a rating that
        drifted between runs would make the panel's own receipts unusable."""
        first = self._rows(11)
        second = self._rows(11)
        self.assertEqual(
            [(row.player_id, row.rating, row.reasons) for row in first],
            [(row.player_id, row.rating, row.reasons) for row in second],
        )

    def test_the_award_goes_to_the_top_row(self) -> None:
        """One function decides who leads the panel and who takes the award at full
        time, so the page cannot mark one player and name another."""
        match = self.matches[28]
        rows = self._rows(28)
        award = award_for(
            rows, home=match.home.name, away=match.away.name, score=match.score
        )
        self.assertIs(award.winner, rows[0])
        self.assertIs(award.runner_up, rows[1])
        self.assertIn(rows[1].name, award.conclusion)


if __name__ == "__main__":
    unittest.main()
