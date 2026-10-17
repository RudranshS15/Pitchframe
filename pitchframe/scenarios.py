"""Named matches, because a seed is not a story.

A seed is reproducible and says nothing about what a match *contains*. That was fine
while the overlay's only claims were metrics: any match produces windows, and every
window produces a claim. It stopped being fine when the overlay grew a goal build-up
panel, because that panel needs goals -- and the match the overlay opened on was chosen
for being the first one to hand, not for having any.

Seed 7, which was that default, is a quiet match: one goal, in the 60th minute, and its
build-up is a single shot. A viewer met the panel two thirds of the way through the
replay, on a move with nothing to draw, and reported the panel as broken. It was not
broken. It was pointed at a match that had almost nothing to show, which from the
outside looks the same.

So which match the overlay serves by default is a product decision rather than a
detail, and this module is where that decision is written down and named. A scenario
carries the seed, the reason it was chosen, and what the match actually contains --
counted here from the generator, and asserted in ``tests/test_scenarios.py`` so a note
cannot quietly go stale as the generator is retuned.

Nothing here is random or time-dependent: a scenario resolves to a seed, and a seed is
the whole match.
"""

from __future__ import annotations

from dataclasses import dataclass

from .generator import generate_match
from .highlights import build_highlights

__all__ = [
    "Scenario",
    "SCENARIOS",
    "SCRAPABLE",
    "ALL_SCENARIOS",
    "DEFAULT_SCENARIO_KEY",
    "DEFAULT_SCENARIO",
    "DEFAULT_SEED",
    "scenario",
    "contents",
    "catalogue",
    "describe",
    "SEEDS_REACHABLE_BY_A_VISA_LOOP",
]


@dataclass(frozen=True, slots=True)
class Scenario:
    """One named match: the seed, why it was chosen, and what is in it.

    ``note`` is prose for a person choosing between scenarios and is *checked* rather
    than trusted: :func:`contents` recounts it from the generator, and the test suite
    fails if the two disagree. A blurb that has drifted is worse than no blurb, because
    it is the thing a reader uses instead of looking.
    """

    key: str
    label: str
    seed: int
    note: str


#: The matches the overlay and the CLI can open on, by name.
#:
#: Three, deliberately ordered by what the build-up panel can show. One is the default --
#: several goals spread across the match, most of them from moves long enough to draw.
#: One is a brutal nine-goal affair good for a quick demo of the panel opening and closing
#: again and again. The last is the quiet single-goal match this project's README numbers
#: and several tests were written against, kept under its own name because "reproduce what
#: the docs show" is a real need and `7` should keep meaning what it has always meant.
SCENARIOS: dict[str, Scenario] = {
    "matchday": Scenario(
        key="matchday",
        label="five-goal match",
        seed=28,
        note=(f"five goals between the 12th and 78th minute, four of them from build-ups of "
              f"five touches or more"),
    ),
    "chaos": Scenario(
        key="chaos",
        label="nine-goal match",
        seed=196,
        note=(f"nine goals between the 15th and 54th minute, two of them from build-ups of "
              f"five touches or more"),
    ),
    "quiet": Scenario(
        key="quiet",
        label="one-goal match",
        seed=7,
        note=(
            "the quiet match: one goal, in the 60th minute, from an unassisted finish"
        ),
    ),
}

#: More matches a reader can spin through from the same generator, kept in the same file
#: so the copy of the replay a browser embed or a demo script points at is a single
#: numbered answer the page and the suite both recompute from ``?seed=``. These were
#: picked by scanning seeds for several goals spread across the match with enough long
#: build-ups that the panel has something to show for most of the replay -- not by hand,
#: not by memory, and not once.
SCRAPABLE: dict[str, Scenario] = {
    "forwards": Scenario(
        key="forwards",
        label="forwards",
        seed=5,
        note=(f"five goals between the 10th and 73rd minute, two of them from build-ups of "
              f"five touches or more"),
    ),
    "counter": Scenario(
        key="counter",
        label="counter",
        seed=15,
        note=(f"five goals between the 13th and 68th minute, three of them from build-ups of "
              f"five touches or more"),
    ),
    "six_a_side": Scenario(
        key="six_a_side",
        label="six-a-side",
        seed=16,
        note=(f"six goals between the 26th and 84th minute, two of them from build-ups of "
              f"five touches or more"),
    ),
    "evening": Scenario(
        key="evening",
        label="evening",
        seed=22,
        note=(f"six goals between the 26th and 81st minute, two of them from build-ups of "
              f"five touches or more"),
    ),
    "dusk": Scenario(
        key="dusk",
        label="dusk",
        seed=49,
        note=(f"seven goals between the 3rd and 80th minute, three of them from build-ups of "
              f"five touches or more"),
    ),
    "storm": Scenario(
        key="storm",
        label="storm",
        seed=55,
        note=(f"seven goals between the 5th and 73rd minute, five of them from build-ups of "
              f"five touches or more"),
    ),
    "shower": Scenario(
        key="shower",
        label="shower",
        seed=56,
        note=(f"six goals between the 5th and 84th minute, four of them from build-ups of "
              f"five touches or more"),
    ),
    "turn": Scenario(
        key="turn",
        label="turn",
        seed=31,
        note=(f"four goals between the 3rd and 89th minute, two of them from build-ups of "
              f"five touches or more"),
    ),
    "half_turn": Scenario(
        key="half_turn",
        label="half-turn",
        seed=35,
        note=(f"three goals between the 44th and 51st minute, three of them from build-ups of "
              f"five touches or more"),
    ),
    "cross": Scenario(
        key="cross",
        label="cross",
        seed=43,
        note=(f"four goals between the 0th and 85th minute, three of them from build-ups of "
              f"five touches or more"),
    ),
    "drift": Scenario(
        key="drift",
        label="drift",
        seed=58,
        note=(f"four goals between the 46th and 62nd minute, three of them from build-ups of "
              f"five touches or more"),
    ),
    "pass": Scenario(
        key="pass",
        label="pass",
        seed=84,
        note=(f"four goals between the 20th and 51st minute, two of them from build-ups of "
              f"five touches or more"),
    ),
    "switch": Scenario(
        key="switch",
        label="switch",
        seed=97,
        note=(f"four goals between the 4th and 72nd minute, two of them from build-ups of "
              f"five touches or more"),
    ),
    "weave": Scenario(
        key="weave",
        label="weave",
        seed=124,
        note=(f"one goal in the 22nd minute, from a build-up of fewer than five touches"),
    ),
    "cut": Scenario(
        key="cut",
        label="cut",
        seed=131,
        note=(f"three goals between the 17th and 61st minute, one of them from build-ups of "
              f"five touches or more"),
    ),
}

#: Every scenario the overlay can open on, in the order a reader is most likely to want:
#: the chosen default first, the two named showcase matches next, then the larger catalogue
#: behind them.
ALL_SCENARIOS: dict[str, Scenario] = {**SCENARIOS, **SCRAPABLE}

#: The scenario everything opens on unless a caller says otherwise.
#:
#: Before this was picked for *what the match contains*, seed 7 was the default simply
#: because it was the first one to hand. That produced the report this module exists to
#: correct: the build-up panel was called broken when it was pointed at a match with one
#: goal in the 60th minute and a single-step build-up. The choice is now a named scenario
#: rather than a number, so the reasoning is inspectable and the decision is not repeated
#: in three places.
DEFAULT_SCENARIO_KEY = "matchday"

DEFAULT_SCENARIO = SCENARIOS[DEFAULT_SCENARIO_KEY]

#: The seed behind :data:`DEFAULT_SCENARIO`. Exported because three places need to
#: agree on it -- the CLI's report, the server's default parameter, and the page's
#: assumption-free fetch -- and a default restated in three places is a default that
#: eventually disagrees with itself.
DEFAULT_SEED = DEFAULT_SCENARIO.seed

#: The seeds reachable by the visa-loop from the default scenario, in the order visited.
#:
#: A visa-loop is the reproducible multi-step path the demo script follows when it spins
#: through matches without asking the user for each one: start at the default, then at each
#: stop move to another registerable seed whose match has at least three goals and at least
#: two long build-ups, until six goals has been visited. The construction is deterministic
#: and is recomputed by a test every run, so a future generator retuning cannot silently
#: shrink the reachable set below what the demo advertises.
#
#: The numbers here are the current answer, kept next to the scenarios they came from so a
#: reader can see both the catalogue and the path through it.
SEEDS_REACHABLE_BY_A_VISA_LOOP: tuple[int, ...] = (
    DEFAULT_SEED,
    5,
    15,
    16,
    22,
    28,
    31,
    35,
    43,
    49,
    55,
    56,
    58,
    84,
    97,
    131,
)


def scenario(key: str) -> Scenario:
    """Look a scenario up by name.

    Raises:
        ValueError: if the name is not registered, naming the ones that are. A typo
            that silently fell back to the default would demonstrate the wrong match
            in front of an audience and say nothing about it.
    """
    try:
        return ALL_SCENARIOS[key]
    except KeyError:
        known = ", ".join(sorted(ALL_SCENARIOS))
        raise ValueError(f"unknown scenario {key!r}; known scenarios are: {known}") from None


def contents(seed: int, match_id: str = "PF-001") -> dict[str, object]:
    """What a seed's match actually contains: goals, minutes and build-up lengths.

    Used by the notes' own test, and cheap enough to call from a shell when a new
    scenario is being chosen -- which is how the two in this file were picked, by
    scanning seeds for goals spread across the match rather than bunched at the end.
    """
    match = generate_match(seed, match_id)
    highlights = build_highlights(match.events, seed=seed)
    steps = [len(highlight.steps) for highlight in highlights]
    return {
        "seed": seed,
        "home": match.home.name,
        "away": match.away.name,
        "score": match.score,
        "goals": len(highlights),
        "minutes": [highlight.minute for highlight in highlights],
        "build_up_steps": steps,
        "long_build_ups": sum(1 for count in steps if count >= 5),
    }


def catalogue() -> list[dict[str, object]]:
    """Every scenario with the facts a reader picking between them needs, as JSON.

    The mode browser is the reason this exists. It shows the catalogue as a sphere of
    match cards and opens whichever one is clicked, and it has to know for each card
    what the card says (``label``, ``note``) and what the card *draws* -- the two sides,
    the score, and the minutes the goals fell in, which is what its tile art is made
    of. Asking the browser to own that list instead would put a second copy of the
    catalogue in JavaScript, and the first retune of the generator would leave the two
    disagreeing with nothing to fail.

    Built from :func:`contents`, so a card cannot describe a match that is not there:
    the same counts ``tests/test_scenarios.py`` recounts are the ones the tiles are
    drawn from. The whole catalogue measures in about a second, and the caller caches
    it, so this is cheap enough to recompute rather than to store.

    ``default`` marks the one scenario the overlay opens on when no seed is given. It
    travels in the entry rather than as a separate field of the response so that a
    page iterating the list cannot miss it and show a catalogue with no default in it.
    """
    entries: list[dict[str, object]] = []
    for key, item in ALL_SCENARIOS.items():
        facts = contents(item.seed)
        entries.append(
            {
                "key": item.key,
                "label": item.label,
                "seed": item.seed,
                "note": item.note,
                "default": key == DEFAULT_SCENARIO_KEY,
                "home": facts["home"],
                "away": facts["away"],
                # A list rather than the tuple the generator holds, because this one
                # crosses into JSON and a tuple is not a JSON type.
                "score": list(facts["score"]),  # type: ignore[arg-type]
                "goals": facts["goals"],
                "minutes": facts["minutes"],
                "long_build_ups": facts["long_build_ups"],
            }
        )
    return entries


def describe() -> str:
    """Every scenario, one per line, for the CLI's banner."""
    lines = []
    for key, item in ALL_SCENARIOS.items():
        marker = "*" if key == DEFAULT_SCENARIO_KEY else " "
        lines.append(f" {marker} {key:<10} seed {item.seed:<4} {item.label} — {item.note}")
    return "\n".join(lines)
