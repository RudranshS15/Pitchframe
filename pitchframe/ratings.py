"""How well each player played, and the one paragraph that says why.

This module answers a different kind of question from the rest of the project. Every
other number here is a *measurement*: possession share, pass accuracy and the build-up
path are all counted from the event stream and would be what they are whoever looked.
A rating is not. Ten is a judgement, and what makes it publishable is not that it is
objective but that the formula behind it is fixed, written down, and applied to
everyone identically.

So the rules this module keeps are the ones that keep a judgement honest:

* **The weights are constants in this file.** No model, no learned parameters, no
  scale fitted to anything. Two runs of the same match produce the same ratings, and a
  reader can recompute any of them from the tallies the payload carries.
* **Every term is a counted thing.** A goal, a completed pass, an on-target strike
  that produced no goal, a tackle that won the ball. Nothing here reads a position, a
  reputation or a minute of the match as evidence about a player.
* **The conclusion composes facts rather than causes.** It is a renderer in the sense
  :mod:`pitchframe.overlay.commentary` is one: it may say a striker scored twice and
  may not say that this is why the match was won, because nothing in the stream says
  so. Phrasings are fixed per fact, so the same performance always reads the same way.
* **A rating near the base means the stream barely touched that player.** The base is
  :data:`BASE_RATING` and a player with no events sits on it. That is the honest
  reading of a player a five-minute-windowed event stream does not mention, and it is
  why the panel shows the tallies beside the number rather than the number alone.

What the caller gets is one :class:`PlayerRating` per player in both squads, a frame's
worth of ratings for the panel to reveal as the clock runs, and -- once the match is
over -- the man of the match with the paragraph above.

Standard library only, like the rest of the overlay's logic: the ratings are part of
the same deterministic replay as the claims, and can be asserted on rather than
eyeballed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .highlights import LABEL_STATSBOMB, LABEL_SYNTHETIC
from .schema import MatchEvent, Squad, Team
from .stats import PRESSURE_THRESHOLD

__all__ = [
    "BASE_RATING",
    "WEIGHTS",
    "CAPS",
    "PROGRESSIVE_CARRY",
    "Tally",
    "PlayerRating",
    "Award",
    "LABELS",
    "rate_squads",
    "rating_from",
    "components",
    "counts_of",
    "reasons_for",
    "leader_of",
    "award_for",
    "ratings_frame",
    "ratings_payload",
]

#: Where a player starts before anything they did is added to it. Six is the base a
#: rating service prints for a performance nobody would remark on, and it is
#: deliberately below the middle of the scale: a rating has to be *earned* upward.
BASE_RATING = 6.0

#: What each counted thing is worth. Public because the panel prints them and the
#: tests recompute the arithmetic from them -- a weight changed here changes every
#: rating in every replay, which is exactly the kind of edit that should be visible.
#:
#: **These numbers were calibrated against the matches this project generates, not
#: chosen for how they look.** The first set was not, and it produced a page of
#: 9s and 10s in which a full-back with an ordinary match outranked a striker who had
#: scored twice: a squad's worth of players each completed 18-48 passes and nobody
#: completes fewer than seven under pressure, so per-pass weights big enough to matter
#: saturated every cap for all twenty-two of them and the ratings became a volume
#: chart. What a match actually contains, per player, over ninety minutes:
#:
#:   passes completed 18-48 (median 28)   under pressure 7-18 (median 12)
#:   carries 4-15 (median 7)              progressive carries 1-7 (median 3)
#:   tackles won 1-7 (median 3)           goals 0-2, on-target shots 0-3, stops 0-3
#:
#: The weights below are set so that an ordinary match lands just above the base, a
#: good one lands in the eights, and the sparse events -- a goal, a strike on target, a
#: stop -- are worth more than any amount of ordinary volume. A goal plus the on-target
#: shot that produced it is +1.35, which is what a goal has to be worth to outrank a
#: tidy afternoon; three goals reach the top of the scale without being clamped there.
#:
#: The two pass terms are deliberately close to cancelling at the rate this generator
#: completes passes: passing ordinarily is neutral, and the rating moves on what a
#: player did beyond what everyone was doing.
WEIGHTS: dict[str, float] = {
    "goal": 1.10,
    "shot_on_target": 0.25,
    "shot_off_target": -0.10,
    "pass_completed": 0.01,
    "pass_failed": -0.03,
    "pass_under_pressure": 0.02,
    "carry": 0.01,
    "progressive_carry": 0.05,
    "tackle_won": 0.06,
    "tackle_lost": -0.02,
    "stop": 0.30,
    "conceded": -0.20,
    "win": 0.20,
    "draw": 0.0,
    "lose": -0.15,
}

#: Ceilings per term, so one kind of contribution cannot decide a rating on its own. A
#: player who completed 90 passes is not fifty per cent better than one who completed
#: 45; they are having the same kind of good game with more of it. Set above what the
#: median player reaches and around what the busiest one does, so the cap is a guard
#: against a freak stream rather than a number most of the squad is sitting on.
CAPS: dict[str, float] = {
    "pass_completed": 0.45,
    "pass_failed": -0.36,
    "pass_under_pressure": 0.32,
    "carry": 0.15,
    "progressive_carry": 0.35,
    "tackle_won": 0.42,
    "tackle_lost": -0.12,
    "stop": 0.90,
    "conceded": -0.60,
}

#: How far a carry has to move the ball, in the schema's normalised units, to count as
#: progressive. Twenty kilometres of running is worth nothing here and the ball moving
#: ten metres towards the goal is: this module rates what happened to the ball. 8 units
#: is about 8.4 m along the pitch's length.
PROGRESSIVE_CARRY = 8.0

#: Ratings never leave this range. A ceiling of ten is what the number means; the floor
#: is here so that a bad half cannot produce a figure the panel would have to explain.
FLOOR, CEILING = 1.0, 10.0

#: Provenance, rendered by the panel verbatim the way the build-up panel renders its own.
#: The second label is the distinction that matters: a rating is computed from the
#: stream, not taken from anything a person awarded. The first follows the source, so a
#: recorded match is not labelled synthetic.
LABELS = (LABEL_SYNTHETIC, "ratings computed, not awarded")


def labels_for(source: str) -> tuple[str, str]:
    """The provenance labels for one source: generated or recorded."""
    first = LABEL_STATSBOMB if source == "statsbomb" else LABEL_SYNTHETIC
    return (first, "ratings computed, not awarded")


@dataclass(frozen=True, slots=True)
class Tally:
    """Everything counted about one player. The evidence behind their rating.

    Every field is a count of events, never a judgement, so the whole struct can be
    shown to a reader beside the number it produced.
    """

    events: int = 0
    goals: int = 0
    shots_on_target: int = 0
    shots_off_target: int = 0
    passes_completed: int = 0
    passes_failed: int = 0
    completed_under_pressure: int = 0
    carries: int = 0
    progressive_carries: int = 0
    tackles_won: int = 0
    tackles_lost: int = 0
    #: On-target strikes by the *other* side that did not produce a goal. Credited to
    #: the defending goalkeeper, and named *stops* rather than saves because the
    #: generator does not distinguish a save from a block: it records the strike and
    #: whether it beat the keeper, and nothing else.
    stops: int = 0
    #: Goals the other side scored while this player was the goalkeeper.
    conceded: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "events": self.events,
            "goals": self.goals,
            "shots_on_target": self.shots_on_target,
            "shots_off_target": self.shots_off_target,
            "passes_completed": self.passes_completed,
            "passes_failed": self.passes_failed,
            "completed_under_pressure": self.completed_under_pressure,
            "carries": self.carries,
            "progressive_carries": self.progressive_carries,
            "tackles_won": self.tackles_won,
            "tackles_lost": self.tackles_lost,
            "stops": self.stops,
            "conceded": self.conceded,
        }


@dataclass(frozen=True, slots=True)
class PlayerRating:
    """One player's number, the tally behind it, and the phrases that name it."""

    player_id: str
    name: str
    shirt: int
    position: str
    team: Team
    rating: float
    tally: Tally
    #: The two or three largest contributions, as fixed phrases. The panel prints these
    #: beside the number so a rating is never just a figure out of ten.
    reasons: tuple[str, ...]

    @property
    def goals(self) -> int:
        return self.tally.goals

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "shirt": self.shirt,
            "position": self.position,
            "team": self.team,
            "rating": self.rating,
            "reasons": list(self.reasons),
            "tally": self.tally.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Award:
    """The man of the match, the runner-up, and the paragraph about them."""

    winner: PlayerRating
    runner_up: PlayerRating | None
    conclusion: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.winner.player_id,
            "name": self.winner.name,
            "shirt": self.winner.shirt,
            "position": self.winner.position,
            "team": self.winner.team,
            "rating": self.winner.rating,
            "reasons": list(self.winner.reasons),
            "goals": self.winner.tally.goals,
            "tally": self.winner.tally.to_dict(),
            "runner_up": (
                None
                if self.runner_up is None
                else {
                    "player_id": self.runner_up.player_id,
                    "name": self.runner_up.name,
                    "team": self.runner_up.team,
                    "rating": self.runner_up.rating,
                }
            ),
            "conclusion": self.conclusion,
        }


# --- counting ---------------------------------------------------------------

class _Ledger:
    """A mutable accumulator. Frozen at the end into a :class:`Tally`."""

    __slots__ = (
        "events", "goals", "shots_on_target", "shots_off_target", "passes_completed",
        "passes_failed", "completed_under_pressure", "carries", "progressive_carries",
        "tackles_won", "tackles_lost", "stops", "conceded",
    )

    def __init__(self) -> None:
        for name in self.__slots__:
            setattr(self, name, 0)

    def frozen(self) -> Tally:
        return Tally(**{name: getattr(self, name) for name in self.__slots__})


@dataclass(frozen=True, slots=True)
class _Fixture:
    """Who is on each side, and who is in goal. Built once per rating run."""

    keepers: Mapping[Team, str]


def _other(team: Team) -> Team:
    return "away" if team == "home" else "home"


def _fixture(squads: Mapping[Team, Squad]) -> _Fixture:
    keepers: dict[Team, str] = {}
    for team in ("home", "away"):
        squad = squads.get(team)
        gloves = squad.by_position("GK") if squad else ()
        if gloves:
            keepers[team] = gloves[0].id
    return _Fixture(keepers=keepers)


def _goal_follows(events: Sequence[MatchEvent], index: int) -> bool:
    """Whether the strike at ``index`` is the one the goal after it records.

    The generator emits a shot and the goal it produced as an adjacent pair on the same
    clock, so this reads forward over events that share that clock rather than trusting
    the next element to be the goal. That tolerance matters: on a stream where the pair
    is separated -- an imported feed might put the rebound between them -- a goal would
    otherwise be credited to the keeper as a stop *and* counted against them.
    """
    shot = events[index]
    for event in events[index + 1:]:
        if event.clock > shot.clock:
            return False
        if event.type == "goal" and event.team == shot.team:
            return True
    return False


def _tallies(
    events: Sequence[MatchEvent], fixture: _Fixture, until: int | None
) -> dict[str, _Ledger]:
    """Count everything, for every player, in one pass over the stream."""
    ledgers: dict[str, _Ledger] = {}

    def ledger_for(player_id: str) -> _Ledger:
        ledger = ledgers.get(player_id)
        if ledger is None:
            ledger = _Ledger()
            ledgers[player_id] = ledger
        return ledger

    for index, event in enumerate(events):
        if until is not None and event.clock > until:
            break
        if event.type in ("kickoff", "half_time", "full_time"):
            continue

        ledger = ledger_for(event.player.id)
        ledger.events += 1

        if event.type == "goal":
            ledger.goals += 1
            # The side that conceded the goal: its keeper wears it. Nothing else in
            # the stream attributes a goal to the player it beat.
            keeper = fixture.keepers.get(_other(event.team))
            if keeper is not None:
                ledger_for(keeper).conceded += 1
        elif event.type == "shot":
            if event.outcome == "success":
                ledger.shots_on_target += 1
                if not _goal_follows(events, index):
                    keeper = fixture.keepers.get(_other(event.team))
                    if keeper is not None:
                        ledger_for(keeper).stops += 1
            else:
                ledger.shots_off_target += 1
        elif event.type == "pass":
            if event.outcome == "success":
                ledger.passes_completed += 1
                if (event.pressure or 0.0) >= PRESSURE_THRESHOLD:
                    ledger.completed_under_pressure += 1
            else:
                ledger.passes_failed += 1
        elif event.type == "carry":
            ledger.carries += 1
            if event.from_xy is not None and event.to_xy is not None:
                moved = (
                    (event.to_xy[0] - event.from_xy[0]) ** 2
                    + (event.to_xy[1] - event.from_xy[1]) ** 2
                ) ** 0.5
                if moved >= PROGRESSIVE_CARRY:
                    ledger.progressive_carries += 1
        elif event.type == "tackle":
            if event.outcome == "success":
                ledger.tackles_won += 1
            else:
                ledger.tackles_lost += 1

    return ledgers


# --- weighting --------------------------------------------------------------

def _result_for(team: Team, score: tuple[int, int]) -> str:
    """``win``, ``draw`` or ``lose`` for one side, from the score as of this moment."""
    home, away = score
    if home == away:
        return "draw"
    winner: Team = "home" if home > away else "away"
    return "win" if winner == team else "lose"


def _capped(name: str, value: float) -> float:
    cap = CAPS.get(name)
    if cap is None:
        return value
    return min(value, cap) if cap >= 0 else max(value, cap)


def counts_of(tally: Tally) -> dict[str, int]:
    """The counted terms, keyed the way :data:`WEIGHTS` is: one event per unit.

    One function rather than two dicts, because there were two and they disagreed. The
    wording functions read the tally's own field names -- ``goals``, ``stops``,
    ``shots_on_target`` -- while the weights are keyed per event, in the singular, and
    the mismatch was silent: the lookup missed, the term was skipped, and every player's
    reasons came out as the one term whose name happened to be the same in both
    (``pass_under_pressure``). The panel's whole point is that a rating is explained,
    and it was explaining all twenty-two players with the same sentence.
    """
    return {
        "goal": tally.goals,
        "shot_on_target": tally.shots_on_target,
        "shot_off_target": tally.shots_off_target,
        "pass_completed": tally.passes_completed,
        "pass_failed": tally.passes_failed,
        "pass_under_pressure": tally.completed_under_pressure,
        "carry": tally.carries,
        "progressive_carry": tally.progressive_carries,
        "tackle_won": tally.tackles_won,
        "tackle_lost": tally.tackles_lost,
        "stop": tally.stops,
        "conceded": tally.conceded,
    }


def components(tally: Tally, result: str) -> dict[str, float]:
    """The weighted terms that make up a rating, before they are summed.

    Returned rather than folded straight into a number for two reasons: the panel names
    the largest of them beside the rating, and a test can check the arithmetic
    term by term instead of asserting on a total that several bugs could produce.
    """
    counts = counts_of(tally)
    weighted = {
        name: _capped(name, WEIGHTS[name] * count)
        for name, count in counts.items()
        if count
    }
    if result in WEIGHTS:
        # The result is part of a performance rather than a fact about one player, so it
        # is worth little: it breaks ties between two similar displays, and cannot lift
        # a quiet afternoon above a good one.
        weighted[result] = WEIGHTS[result]
    return weighted


def rating_from(tally: Tally, result: str) -> float:
    """One player's rating: the base, plus every capped term, clamped to the scale."""
    total = BASE_RATING + sum(components(tally, result).values())
    return round(min(CEILING, max(FLOOR, total)), 1)


#: How a contribution is written for a reader: the two numbers of a term's noun, and
#: nothing else, because English plurals are not a suffix this module can append. An
#: earlier version was a single template per term with a brace or two of the suffix
#: filled in, and it read *2 carryies* on the card: "carry" drops its y and "pass" takes
#: an es, so the singular and the plural are written out here where a reader can check
#: them. Fixed per term, so the same tally is always described in the same words, and
#: only terms a player actually has can appear.
_PHRASES: dict[str, tuple[str, str]] = {
    "goal": ("{n} goal", "{n} goals"),
    "shot_on_target": ("{n} shot on target", "{n} shots on target"),
    "shot_off_target": ("{n} shot off target", "{n} shots off target"),
    "pass_completed": ("{n} completed pass", "{n} completed passes"),
    "pass_failed": ("{n} pass given away", "{n} passes given away"),
    "pass_under_pressure": (
        "{n} pass completed under pressure",
        "{n} passes completed under pressure",
    ),
    "carry": ("{n} carry", "{n} carries"),
    "progressive_carry": ("{n} progressive carry", "{n} progressive carries"),
    "tackle_won": ("{n} tackle won", "{n} tackles won"),
    "tackle_lost": ("{n} tackle lost", "{n} tackles lost"),
    "stop": ("{n} stop", "{n} stops"),
    "conceded": ("{n} conceded", "{n} conceded"),
}


def _phrase(name: str, count: int) -> str:
    templates = _PHRASES.get(name)
    if templates is None:
        # A term with no wording of its own still gets a phrase rather than a crash,
        # and the underscores do not reach the page.
        singular = plural = "{n} " + name.replace("_", " ")
    else:
        singular, plural = templates
    return (singular if count == 1 else plural).format(n=count)


def reasons_for(tally: Tally, result: str, limit: int = 3) -> tuple[str, ...]:
    """The largest few contributions *that added something*, as phrases.

    Only positive terms: a phrase list is a card of what a player did, and a card whose
    leading line was "1 conceded" would describe the award as if the panel had picked
    the wrong man. The negative terms are still in the arithmetic and still in the
    tally; they just do not get to be a headline.
    """
    parts = {name: value for name, value in components(tally, result).items() if value > 0}
    counts = counts_of(tally)
    # Ordered by what the term was worth, then by the term's name, so two equal
    # contributions describe a player the same way on every run.
    ranked = sorted(parts.items(), key=lambda item: (-item[1], item[0]))
    phrases: list[str] = []
    for name, _value in ranked:
        count = counts.get(name)
        if not count:
            continue
        phrases.append(_phrase(name, int(count)))
        if len(phrases) >= limit:
            break
    return tuple(phrases)


# --- assembling -------------------------------------------------------------

def rate_squads(
    events: Sequence[MatchEvent],
    squads: Mapping[Team, Squad],
    *,
    score: tuple[int, int],
    until: int | None = None,
) -> tuple[PlayerRating, ...]:
    """A rating for every player in both squads, best first.

    Everyone is rated, including players the stream never mentions: an unmentioned
    player sits on the base, which is the honest reading. Sorting is by rating, then by
    goals, then by shirt number and finally by id, so the order is total and identical
    on every run -- a panel that reshuffled equal ratings between reloads would look
    like the data had changed.
    """
    fixture = _fixture(squads)
    ledgers = _tallies(events, fixture, until)

    rows: list[PlayerRating] = []
    for team in ("home", "away"):
        squad = squads.get(team)
        if squad is None:
            continue
        result = _result_for(team, score)
        for player in squad.players:
            tally = ledgers.get(player.id, _Ledger()).frozen()
            rows.append(
                PlayerRating(
                    player_id=player.id,
                    name=player.name,
                    shirt=player.shirt,
                    position=player.position,
                    team=team,
                    rating=rating_from(tally, result),
                    tally=tally,
                    reasons=reasons_for(tally, result),
                )
            )

    rows.sort(key=lambda row: (-row.rating, -row.goals, row.shirt, row.player_id))
    return tuple(rows)


def leader_of(rows: Sequence[PlayerRating]) -> PlayerRating | None:
    """The best-rated player, or ``None`` for an empty list.

    The man of the match at full time and the leader on the panel during the match are
    the same function, so the panel cannot mark one player as leading while the award
    goes to another.
    """
    return rows[0] if rows else None


def _goal_phrase(goals: int) -> str:
    if goals == 1:
        return "scored once"
    if goals == 2:
        return "scored twice"
    return f"scored {goals}"


def conclusion_for(
    winner: PlayerRating,
    runner_up: PlayerRating | None,
    *,
    home: str,
    away: str,
    score: tuple[int, int],
) -> str:
    """The paragraph that explains the award, from counted facts only.

    Written the way :mod:`pitchframe.overlay.commentary` writes: the sentences are
    fixed per fact and the facts come from the tally, so the paragraph cannot claim
    more than was counted. It never says a performance *caused* a result, never
    compares a player to anyone outside this match, and never describes a shot, a pass
    or a save that is not in the stream.
    """
    side = home if winner.team == "home" else away
    other = away if winner.team == "home" else home
    tally = winner.tally
    sentences = [f"{winner.name} takes the award on {winner.rating}, for {side}."]

    detail: list[str] = []
    if tally.goals:
        detail.append(_goal_phrase(tally.goals))
    if tally.shots_on_target:
        remaining = tally.shots_on_target - tally.goals
        if remaining > 0:
            detail.append(
                f"put {remaining} other shot{'s' if remaining != 1 else ''} on target"
            )
    if tally.stops:
        stops = tally.stops
        detail.append(f"kept {stops} on-target strike{'s' if stops != 1 else ''} out")
    if not detail:
        # No goals and no saves: a midfielder's or a defender's match, and the rating
        # has to be explained by the work rather than by a highlight.
        if tally.passes_completed:
            detail.append(f"completed {tally.passes_completed} passes")
        if tally.tackles_won:
            detail.append(f"won {tally.tackles_won} tackle{'s' if tally.tackles_won != 1 else ''}")
        if tally.progressive_carries:
            detail.append(
                f"carried the ball forward {tally.progressive_carries} time"
                + ("" if tally.progressive_carries == 1 else "s")
            )
    if detail:
        sentences.append(f" {winner.name} " + " and ".join(detail) + ".")

    if tally.conceded:
        conceded = tally.conceded
        sentences.append(
            f" It was not a clean sheet: {other} scored {conceded} "
            f"time{'s' if conceded != 1 else ''}."
        )

    if runner_up is not None:
        gap = round(winner.rating - runner_up.rating, 1)
        if gap <= 0:
            sentences.append(
                f" {runner_up.name} drew level on {runner_up.rating}, and the tie went "
                "to the larger tally."
            )
        else:
            sentences.append(
                f" {runner_up.name} was next on {runner_up.rating}, {gap} behind."
            )

    sentences.append(
        f" The match finished {score[0]}-{score[1]}."
    )
    return "".join(sentences)


def award_for(
    rows: Sequence[PlayerRating], *, home: str, away: str, score: tuple[int, int]
) -> Award | None:
    """The man of the match and the paragraph, or ``None`` when there is nobody to give
    it to — a replay with no squads at all, which a hand-built fixture can be."""
    winner = leader_of(rows)
    if winner is None:
        return None
    runner_up = rows[1] if len(rows) > 1 else None
    return Award(
        winner=winner,
        runner_up=runner_up,
        conclusion=conclusion_for(winner, runner_up, home=home, away=away, score=score),
    )


# --- what the payload carries -----------------------------------------------

def ratings_frame(
    events: Sequence[MatchEvent],
    squads: Mapping[Team, Squad],
    *,
    score: tuple[int, int],
    clock: int,
) -> dict[str, Any]:
    """One moment's ratings, as the panel reveals them.

    A map of player id to rating rather than a list of records: the names, shirts and
    positions are already in the payload's squads, and repeating them once per player
    per frame would be most of a megabyte of names saying what the roster already says.
    Whoever is top is named here as well, so the panel does not have to work out who is
    leading from a map whose order is its own business.
    """
    rows = rate_squads(events, squads, score=score, until=clock)
    leader = leader_of(rows)
    return {
        "clock": clock,
        "leader": leader.player_id if leader else None,
        "values": {row.player_id: row.rating for row in rows},
    }


def ratings_payload(
    events: Sequence[MatchEvent],
    squads: Mapping[Team, Squad],
    *,
    home: str,
    away: str,
    score: tuple[int, int],
    decided_at: int,
    source: str = "synthetic",
) -> dict[str, Any]:
    """The whole ratings block: the labels, the moment the award is made, and the award.

    The award is computed over the full match and carried from the first frame, exactly
    as the goal list is: the payload is the finished script, and it is the *page* that
    withholds it until the clock reaches :data:`decided_at`. A build-up panel that
    could name a goal in the 78th minute while the scoreboard read 0-0 would spoil the
    replay it exists to show, and an award made before the match ended would do the
    same thing with a name.
    """
    rows = rate_squads(events, squads, score=score)
    award = award_for(rows, home=home, away=away, score=score)
    return {
        "labels": list(labels_for(source)),
        "decided_at": decided_at,
        "award": None if award is None else award.to_dict(),
    }
