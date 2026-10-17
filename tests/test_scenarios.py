"""Tests for the named matches the overlay opens on.

``scenarios.py`` says in its own docstring that a scenario's note is *checked* rather
than trusted, and this is where that check lives. A note is prose a reader uses
instead of looking: if the generator is retuned and the note still says "five goals"
about a match that now has four, the note is worse than no note at all, because
nothing on the page contradicts it.

So every claim a note makes about its match is recounted here from the generator —
the goal count, the minutes those goals were scored in, and how many of the moves
were long — and the note has to agree, in the same words.

The second thing these tests pin is the decision the module exists for: the match the
overlay opens on has to be a match worth opening. Seed 7 is the quiet one, and one
goal in the 60th minute is a poor thing to put in front of a viewer who clicked a
control labelled *build-ups*.

The third is the mode browser, which is why these checks run over the whole registry
rather than over the three named showcase matches. The browser serves this catalogue as
its cards, and a card *is* its note — so a note that has drifted is a card that lies to
the reader choosing by it, exactly as a stale option on the command line would. When the
notes were only checked for the three named matches, the other fifteen were prose nobody
recounted, and the page they were about to be printed on did not exist yet to make that
visible.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from pitchframe.__main__ import main
from pitchframe.generator import generate_match
from pitchframe.highlights import build_highlights
from pitchframe.scenarios import (
    DEFAULT_SCENARIO,
    DEFAULT_SCENARIO_KEY,
    DEFAULT_SEED,
    SCENARIOS,
    ALL_SCENARIOS,
    catalogue,
    contents,
    describe,
    scenario,
)

#: The threshold a note means when it says "a move of five touches or more". Named
#: here rather than read from the implementation, because the note quotes it: if the
#: extraction ever counted a build-up differently, the note would be describing a
#: different number than the panel draws.
LONG_MOVE = 5

#: How a count is written when it opens a sentence, which is how the notes write it.
#: Only the range the scenarios actually use, so an unspelled count fails loudly
#: rather than silently passing.
_WORDS = {
    0: "no",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


def _word(value: int) -> str:
    if value not in _WORDS:
        raise AssertionError(f"no word for {value} in this test's table")
    return _WORDS[value]


def _ordinal(value: int) -> str:
    """``12`` -> ``12th``, ``61`` -> ``61st``, the way the notes write a minute."""
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _recount(item) -> dict[str, object]:
    """What a scenario's seed actually produces, read off the generator.

    Counted from the event stream and the extraction rather than from the scenario's
    own :func:`contents`, deliberately: a test that asked the module to describe
    itself could only prove it is self-consistent.
    """
    match = generate_match(item.seed, "PF-001")
    goals = [event for event in match.events if event.type == "goal"]
    highlights = build_highlights(match.events, seed=item.seed)
    steps = [len(highlight.steps) for highlight in highlights]
    return {
        "goals": len(goals),
        "minutes": [event.minute for event in goals],
        "build_up_steps": steps,
        "long_moves": sum(1 for count in steps if count >= LONG_MOVE),
        "score": match.score,
    }


class RegistryTests(unittest.TestCase):
    """The registry itself: keys, seeds and the name a typo produces."""

    def test_every_entry_is_keyed_by_its_own_key(self) -> None:
        """A lookup that returns a scenario under another name is a scenario nobody
        can find, and a banner that prints the wrong one.

        Over the whole registry, not just the named three: a card in the mode browser is
        reached by its key, so a key that does not match its entry is a card that opens
        the wrong match."""
        for key, item in ALL_SCENARIOS.items():
            with self.subTest(key=key):
                self.assertEqual(item.key, key)
                self.assertTrue(item.label)
                self.assertTrue(item.note)

    def test_no_two_scenarios_share_a_seed(self) -> None:
        """Two names for one match is one name too many: whichever a reader picks
        they get the same replay, and the second name is a lie about a choice.

        Across the whole registry, because the sphere draws all of them: a repeated seed
        would put the same card on the ball twice under two names, and a reader would have
        no way to know which one they had opened."""
        seeds = [item.seed for item in ALL_SCENARIOS.values()]
        self.assertEqual(len(seeds), len(set(seeds)))

    def test_an_unknown_name_names_the_ones_that_are_known(self) -> None:
        """A typo has to be answerable from the message. Falling back to the default
        would demonstrate the wrong match in front of an audience and say nothing."""
        with self.assertRaises(ValueError) as caught:
            scenario("matchdya")
        message = str(caught.exception)
        for key in ALL_SCENARIOS:
            with self.subTest(key=key):
                self.assertIn(key, message)

    def test_the_banner_marks_the_default_and_names_every_scenario(self) -> None:
        """The banner is how a reader picks. Every scenario has to be on a line of
        its own, and exactly one of them is the one they will get."""
        lines = describe().splitlines()
        self.assertEqual(len(lines), len(ALL_SCENARIOS))
        marked = [line for line in lines if line.startswith(" *")]
        self.assertEqual(len(marked), 1)
        self.assertIn(DEFAULT_SCENARIO_KEY, marked[0])
        for key in SCENARIOS:
            with self.subTest(key=key):
                self.assertTrue(any(key in line for line in lines))


class NoteTests(unittest.TestCase):
    """Every number a note quotes, recounted from the match it describes.

    The whole registry: these notes are what the mode browser prints on a card, and a
    card is chosen from. The three named showcase matches were the only ones checked
    while they were the only ones listed; fifteen unchecked notes were sitting behind
    them the whole time.
    """

    def test_a_note_states_the_goal_count_the_match_has(self) -> None:
        """The first thing a reader picks a match for."""
        for key, item in ALL_SCENARIOS.items():
            with self.subTest(key=key):
                actual = _recount(item)
                self.assertIn(
                    f"{_word(actual['goals'])} goal",
                    item.note,
                    f"{key}: the note does not state {actual['goals']} goals",
                )

    def test_a_note_states_the_minutes_its_goals_fell_in(self) -> None:
        """The minutes are what a note is *for* — "five goals" sounds the same whether
        they land in the first quarter or the last ten."""
        for key, item in ALL_SCENARIOS.items():
            with self.subTest(key=key):
                minutes = _recount(item)["minutes"]
                self.assertTrue(minutes, f"{key}: a scenario with no goals cannot be shown")
                for minute in (min(minutes), max(minutes)):
                    self.assertIn(
                        _ordinal(minute),
                        item.note,
                        f"{key}: the note does not place a goal in the {minute} minute",
                    )

    def test_a_note_about_long_moves_counts_them_at_the_threshold_it_names(self) -> None:
        """A note that says "four of them from moves of five touches or more" is
        making two claims at once, and the second one is the threshold. A note that
        stays silent about long moves is making a claim too: that there are none."""
        for key, item in ALL_SCENARIOS.items():
            with self.subTest(key=key):
                long_moves = _recount(item)["long_moves"]
                if "of them" not in item.note:
                    self.assertEqual(
                        long_moves,
                        0,
                        f"{key}: {long_moves} long moves go unmentioned by the note",
                    )
                    continue
                self.assertIn("five touches or more", item.note)
                self.assertIn(f"{_word(long_moves)} of them", item.note)

    def test_contents_agrees_with_the_match_it_describes(self) -> None:
        """`contents` is what the notes are written from and what a shell is pointed
        at when a new scenario is chosen. A count that disagreed with the stream
        would send the next author looking at the wrong match."""
        for key, item in ALL_SCENARIOS.items():
            with self.subTest(key=key):
                actual = _recount(item)
                counted = contents(item.seed)
                self.assertEqual(counted["goals"], actual["goals"])
                self.assertEqual(counted["minutes"], actual["minutes"])
                self.assertEqual(counted["build_up_steps"], actual["build_up_steps"])
                self.assertEqual(counted["long_build_ups"], actual["long_moves"])
                self.assertEqual(sum(counted["score"]), actual["goals"])


class DefaultScenarioTests(unittest.TestCase):
    """The decision the module exists for: what the overlay opens on."""

    def test_the_default_is_a_registered_scenario(self) -> None:
        """A default that is not in the registry is a default nobody can name."""
        self.assertIn(DEFAULT_SCENARIO_KEY, SCENARIOS)
        self.assertIs(scenario(DEFAULT_SCENARIO_KEY), DEFAULT_SCENARIO)
        self.assertEqual(DEFAULT_SEED, DEFAULT_SCENARIO.seed)

    def test_the_default_match_has_several_goals_to_open_on(self) -> None:
        """The regression this module was written for.

        Seed 7 was the default because it was the first seed to hand, and a viewer
        who opened the build-up panel on it met a single step two thirds of the way
        through the replay — which reads as a broken control rather than as a quiet
        match. Whatever the default becomes, it has to have more than one goal in it.
        """
        actual = _recount(DEFAULT_SCENARIO)
        self.assertGreaterEqual(actual["goals"], 3)

    def test_the_default_match_spreads_its_goals_across_the_match(self) -> None:
        """The same reasoning a step further: three goals in the last ten minutes
        leaves the panel shut for eighty minutes, and the panel is on the page from
        the first frame."""
        minutes = _recount(DEFAULT_SCENARIO)["minutes"]
        self.assertGreater(len(set(minutes)), 1)
        self.assertLess(min(minutes), 45)
        self.assertGreater(max(minutes), 70)

    def test_the_default_scenario_beats_the_quiet_one_on_what_the_panel_shows(self) -> None:
        """The choice, stated as the comparison it was: more goals, and more of them
        from moves with enough touches to draw."""
        chosen = _recount(DEFAULT_SCENARIO)
        quiet = _recount(SCENARIOS["quiet"])
        self.assertGreater(chosen["goals"], quiet["goals"])
        self.assertGreater(chosen["long_moves"], quiet["long_moves"])

    def test_both_sides_score_in_the_default_match(self) -> None:
        """A one-sided match has one build-up panel worth opening all afternoon, and
        the panel exists to be opened."""
        home, away = contents(DEFAULT_SCENARIO.seed)["score"]
        self.assertGreater(home, 0)
        self.assertGreater(away, 0)


class QuietScenarioTests(unittest.TestCase):
    """The other half of the decision: seed 7 has to keep meaning what it meant."""

    def test_the_quiet_match_is_the_one_the_readme_numbers_came_from(self) -> None:
        """`--seed 7` is quoted in the README and asserted across the suite, so the
        quiet scenario is not a spare: it is the seed most of this project's numbers
        were written on, and it stays reachable under its own name."""
        quiet = SCENARIOS["quiet"]
        self.assertEqual(quiet.seed, 7)
        actual = _recount(quiet)
        self.assertEqual(actual["goals"], 1)
        self.assertEqual(actual["score"], (1, 0))
        self.assertEqual(actual["minutes"], [60])
        self.assertIn("60th", quiet.note)

    def test_the_quiet_match_has_one_short_move_which_its_note_says(self) -> None:
        """Its one goal is an unassisted finish — a single step. That is the reason
        the panel looked empty there, and the note now says so before a reader picks
        it rather than after."""
        quiet = SCENARIOS["quiet"]
        self.assertEqual(_recount(quiet)["build_up_steps"], [1])
        self.assertIn("unassisted", quiet.note)


class ChaosScenarioTests(unittest.TestCase):
    """The nine-goal line needs its own story as well.

    It exists for the demo: a reader who spins through matches wants to see the panel
    open and close again on the same seed. The note for it is the one most likely to
    drift out of step with the generator, so it gets a little more scrutiny.
    """

    DEFAULT_KEY = "chaos"

    def setUp(self) -> None:
        self.item = SCENARIOS[self.DEFAULT_KEY]
        self.actual = _recount(self.item)

    def test_the_chaos_note_numbers_are_real_and_not_something_else(self) -> None:
        """The note is a counting tale: more goals than any normal match, and a spread
        wide enough that the panel has open and close again on the same seed.

        A future generator tuning that changed only *how* those goals were scored, but
        not which minutes they fell in, should not silently break this note — so the
        minutes are asserted, not borrowed from the note's favourite list."""
        self.assertGreaterEqual(self.actual["goals"], 9)
        self.assertGreaterEqual(len(self.actual["minutes"]), 6)
        self.assertGreaterEqual(min(self.actual["minutes"]), 6)
        self.assertLessEqual(max(self.actual["minutes"]), 90)
        for minute in (min(self.actual["minutes"]), max(self.actual["minutes"])):
            self.assertIn(_ordinal(minute), self.item.note)
        self.assertIn(_word(self.actual["long_moves"]), self.item.note)

    def test_the_chaos_note_mentions_breadth(self) -> None:
        """A nine-goal match is interesting because the goals are spread across the
        first half, not because they are nine. The note says so."""
        self.assertIn("between the", self.item.note)
        self.assertIn("and", self.item.note)

    def test_the_chaos_seed_never_lose_its_nine(self) -> None:
        """Deterministic, like every number here."""
        first = _recount(self.item)
        second = _recount(self.item)
        self.assertEqual(first, second)
        self.assertGreaterEqual(second["goals"], 9)
        self.assertEqual(second["minutes"], first["minutes"])


class CatalogueTests(unittest.TestCase):
    """The catalogue the mode browser is built from.

    ``catalogue()`` is the only path from the registry to the page: the endpoint serves it,
    the page draws a card per entry, and every card carries the seed that opens its match.
    So this is where the page's data is pinned — not in a test that reads the JavaScript,
    which could only check that some list exists, but in one that checks the list is the
    registry's own facts.
    """

    def test_one_entry_per_scenario_in_registry_order(self) -> None:
        """The order is the order a reader is offered them in, and the first card on the
        sphere is the first entry: a dictionary iterated somewhere else would put an
        arbitrary card in front of a reader who has not chosen anything yet."""
        entries = catalogue()
        self.assertEqual([entry["key"] for entry in entries], list(ALL_SCENARIOS))
        self.assertEqual(len(entries), len(ALL_SCENARIOS))

    def test_exactly_one_entry_is_the_default_and_it_is_the_named_one(self) -> None:
        """The sphere opens on whatever is in front, so the flag has to name one match
        unambiguously. Two defaults is a page whose opening card depends on iteration
        order; none is a page that cannot say which match the feed opens on."""
        marked = [entry["key"] for entry in catalogue() if entry["default"]]
        self.assertEqual(marked, [DEFAULT_SCENARIO_KEY])

    def test_every_entry_carries_the_facts_its_card_draws(self) -> None:
        """A card is drawn, not loaded, so its fields are the drawing's only input. A
        missing one would not raise: the tile would come out with a hole in it."""
        for entry in catalogue():
            with self.subTest(key=entry["key"]):
                for field in (
                    "key", "label", "seed", "note", "default",
                    "home", "away", "score", "goals", "minutes", "long_build_ups",
                ):
                    self.assertIn(field, entry)
                self.assertIsInstance(entry["score"], list)
                self.assertEqual(len(entry["score"]), 2)
                self.assertEqual(len(entry["minutes"]), entry["goals"])

    def test_every_entry_agrees_with_the_generator(self) -> None:
        """The same recount the notes get, applied to the payload: if the catalogue and
        the match it names disagreed, the card would describe a match nobody can open.

        Recounted from the event stream rather than from ``contents``, so this cannot pass
        by asking the module whether it agrees with itself.
        """
        by_seed = {entry["seed"]: entry for entry in catalogue()}
        self.assertEqual(len(by_seed), len(ALL_SCENARIOS))
        for key, item in ALL_SCENARIOS.items():
            with self.subTest(key=key):
                entry = by_seed[item.seed]
                self.assertEqual(entry["key"], key)
                self.assertEqual(entry["note"], item.note)
                match = generate_match(item.seed, "PF-001")
                goals = [event for event in match.events if event.type == "goal"]
                self.assertEqual(entry["home"], match.home.name)
                self.assertEqual(entry["away"], match.away.name)
                self.assertEqual(entry["goals"], len(goals))
                self.assertEqual(entry["minutes"], [event.minute for event in goals])
                self.assertEqual(entry["score"], list(match.score))

    def test_the_catalogue_survives_json(self) -> None:
        """It crosses into a response body, so a tuple or a set left in it is a 500 on a
        page that was tested from the Python side and never through HTTP."""
        entries = catalogue()
        self.assertEqual(json.loads(json.dumps(entries)), entries)

    def test_the_catalogue_is_at_least_the_fifteen_more_it_advertises(self) -> None:
        """The catalogue is a promise about size: "every match this can replay" is only
        credible if there are enough of them for choosing between them to mean anything,
        and the mode browser prints the count. Fifteen beyond the three named matches is
        the number this was asked for, so it is asserted rather than assumed."""
        self.assertGreaterEqual(len(catalogue()), len(SCENARIOS) + 15)


class CliReachabilityTests(unittest.TestCase):
    """Every catalogued match can be opened by name from the command line.

    ``--scenarios`` printed the whole catalogue while ``--scenario`` accepted only the
    three named matches: a list of instructions, two thirds of which the parser rejected.
    Nothing caught it because the banner and the choices were written in different places
    and only one of them was tested. This walks the whole catalogue through the parser,
    which is the same claim the mode browser makes on screen.
    """

    def test_every_scenario_the_banner_lists_can_be_opened_by_name(self) -> None:
        for key, item in ALL_SCENARIOS.items():
            with self.subTest(key=key):
                printed = io.StringIO()
                with contextlib.redirect_stdout(printed):
                    code = main(["--scenario", key, "--json"])
                self.assertEqual(code, 0, f"--scenario {key} was refused")
                summary = json.loads(printed.getvalue())
                facts = contents(item.seed)
                self.assertEqual(summary["home"], facts["home"])
                self.assertEqual(summary["away"], facts["away"])
                self.assertEqual(
                    summary["score"],
                    {"home": facts["score"][0], "away": facts["score"][1]},
                    f"--scenario {key} opened a match that is not its own",
                )

    def test_an_explicit_seed_still_wins_over_the_scenario(self) -> None:
        """Naming a match is a default, not a restriction: every command still takes any
        seed at all, which is what keeps the catalogue a convenience rather than a cage.

        Checked against the *other* match's score as well, so a run that ignored the seed
        and played the scenario cannot pass this by accident."""
        chosen = SCENARIOS["quiet"]
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = main(["--scenario", "matchday", "--seed", str(chosen.seed), "--json"])
        self.assertEqual(code, 0)
        summary = json.loads(printed.getvalue())

        wanted = contents(chosen.seed)
        ignored = contents(SCENARIOS["matchday"].seed)
        self.assertEqual(summary["score"], {"home": wanted["score"][0], "away": wanted["score"][1]})
        self.assertNotEqual(
            summary["score"],
            {"home": ignored["score"][0], "away": ignored["score"][1]},
            "the scenario's seed was used and the explicit one was discarded",
        )


class VisaLoopTests(unittest.TestCase):
    """The demo script's spiral is reproducible, and it reaches six goals.

    The page advertises being able to spin through matches without asking the reader
    for each one. That is not a promise the demo makes from memory: it is a promise
    the demo makes from a path through the same ``?seed=`` catalogue the suite checks,
    so the path is the kind of thing that can go stale.
    """

    def test_the_visa_loop_reaches_six_goals(self) -> None:
        """A loop that flips through matches should be able to land on a six-goal match
        without asking the reader for help along the way, and it should do so before the
        loop empties."""
        from pitchframe.scenarios import (
            SEEDS_REACHABLE_BY_A_VISA_LOOP,
            contents,
        )

        reached_six_at = None
        for seed in SEEDS_REACHABLE_BY_A_VISA_LOOP:
            actual = contents(seed)
            if actual["goals"] >= 6:
                reached_six_at = seed
                break
        self.assertIsNotNone(
            reached_six_at,
            "the visa-loop must be able to reach a six-goal match from the default",
        )
        remaining = SEEDS_REACHABLE_BY_A_VISA_LOOP.index(reached_six_at) + 1
        self.assertLess(
            remaining,
            len(SEEDS_REACHABLE_BY_A_VISA_LOOP),
            "the loop should reach six goals before it runs out of seeds",
        )


if __name__ == "__main__":
    unittest.main()
