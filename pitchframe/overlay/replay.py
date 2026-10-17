"""The deterministic replay behind the live overlay.

The overlay is a replay, not a feed, and this module is where that is decided. It
walks a synthetic match window by window; for each window it runs the *real*
propose/verify/retry loop and records what the loop did. The result is a script: every
frame the browser will ever show, computed up front and reproducible from a seed.

Three consequences, and each is why the design is worth the constraint:

**A demo cannot fail on stage.** There is no model call, no socket and no clock race
in the request path. The page renders from a JSON document that was produced
deterministically before it loaded.

**The frames are honest about time.** A frame is built from only the events that had
already happened and only the windows the analyser had already closed, so the Narrator
never sees its own future. Replaying it later cannot change what it would have said,
because the arithmetic only ever touches the window and its baseline.

**The numbers are checkable.** Two runs of :func:`build_replay` with the same arguments
produce byte-identical JSON, so ``tests/test_overlay.py`` can assert on the whole
document rather than on a sample of it.

Standard library only, and it drives the model-free agents from
:mod:`pitchframe.agents.demo`. The model-backed agents implement the same protocols, so
substituting them changes nothing here — that substitution is what
:mod:`pitchframe.overlay.server` reports in the provider banner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from ..agents.demo import FAULT_MODES, InjectingNarrator, RuleVerifier
from ..agents.pipeline import PipelineResult, Snapshot, run_pipeline, snapshot
from ..claims import Claim, baseline_window_for
from ..generator import generate_match
from ..highlights import build_highlights
from ..llm.provider import select_tier
from ..ratings import ratings_frame, ratings_payload
from ..scenarios import DEFAULT_SEED
from ..schema import Match, MatchEvent, Squad, Team
from ..stats import Window
from .commentary import compose, incident_clause

__all__ = [
    "ClaimRow",
    "Frame",
    "Incident",
    "Reading",
    "Replay",
    "build_replay",
    "DEFAULT_WINDOW_SECONDS",
    "DEFAULT_FIRST_WINDOW_START",
]

#: How long each analysed window is. Five minutes gives sixteen windows in a match,
#: which is enough for the feed to develop a rhythm without the demo dragging.
DEFAULT_WINDOW_SECONDS = 300

#: The first window's start. It must leave a full-length baseline before it, since a
#: claim is a comparison and there is nothing to compare against inside the first
#: minutes — ``baseline_window_for`` rejects those claims outright.
DEFAULT_FIRST_WINDOW_START = 600

#: Event types worth showing in the fan timeline, and how much each matters.
_INCIDENT_WEIGHTS = {"goal": 3.0, "shot": 2.0, "tackle": 1.0}

#: Cap per frame, so a busy window does not bury the commentary beneath incidents.
_MAX_INCIDENTS_PER_FRAME = 4


@dataclass(frozen=True, slots=True)
class Reading:
    """One metric over a window, beside the same metric over its baseline."""

    key: str
    metric: str
    team: str | None
    value: float
    baseline_value: float
    delta: float
    direction: str
    is_flat: bool


@dataclass(frozen=True, slots=True)
class Incident:
    """A notable event in the window, for the match timeline."""

    clock: int
    minute: int
    type: str
    team: str
    player: str
    pressure: float | None
    speed_kmh: float | None


@dataclass(frozen=True, slots=True)
class ClaimRow:
    """One line of the feed: a claim, what it said, and what measurement did to it.

    A rejected row is kept rather than discarded. That is the overlay's whole argument
    — most products delete their errors, and a system that shows them is the only one
    whose error rate can be checked by a viewer.
    """

    seq: int
    claim_id: str
    window: Window
    metric: str
    team: str | None
    #: ``claim`` for a first attempt, ``correction`` for the retried version.
    kind: str
    #: ``verified`` or ``rejected`` for this row on its own terms.
    status: str
    #: The direction this row asserts.
    direction: str
    magnitude: float
    #: The human sentence. For a rejected row this is what it *wanted* to say.
    text: str
    #: ``claimed up +7.00 · measured down -7.00``, from the verdict itself.
    receipt: str
    reason: str
    claimed_direction: str | None
    claimed_magnitude: float | None
    measured_direction: str | None
    measured_value: float | None
    baseline_value: float | None
    delta: float | None
    #: The rejected row this correction answers, if any.
    answers: str | None
    #: True when this row's wrong direction was planted on purpose, so the demo can
    #: label it instead of passing it off as a natural failure.
    injected: bool


@dataclass(frozen=True, slots=True)
class Frame:
    """One analysed window, as the browser should show it at that moment."""

    index: int
    clock: int
    minute: int
    window: Window
    baseline: Window
    score: tuple[int, int]
    #: Rows added by this frame, in the order they happened, corrections included.
    feed: tuple[ClaimRow, ...]
    readings: tuple[Reading, ...]
    incidents: tuple[Incident, ...]
    #: Running totals after this frame.
    ledger: dict[str, int]
    #: Every player's rating as of this frame's clock, plus who led. Computed from the
    #: events up to that clock and nothing later, so the panel is as honest about time
    #: as the claim above it: a rating on screen at 60 minutes is the rating the first
    #: 60 minutes produced, not the one the match ended with.
    ratings: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Replay:
    """A complete, reproducible script for the overlay."""

    seed: int
    match_id: str
    home: str
    away: str
    squads: dict[str, list[dict[str, Any]]]
    window_seconds: int
    fault: str
    provider: dict[str, Any]
    frames: tuple[Frame, ...]
    #: Reading keys whose direction was deliberately inverted, for the label.
    injected: tuple[str, ...]
    #: Every goal, uncapped, so the scoreboard can move at the exact minute rather
    #: than only when a five-minute window closes. The incident lists are capped for
    #: legibility, which would make a score derived from them silently wrong.
    goals: tuple[Incident, ...]
    #: The build-up to each of those goals, as the highlight panel consumes it.
    #: Computed from the whole match rather than from the revealed frames: a move that
    #: began before the first analysed window is still the move that produced the goal,
    #: and truncating it would draw a path that starts in the middle of nothing. The
    #: panel only ever opens on a goal the replay has already reached, so this reveals
    #: nothing about the future.
    highlights: tuple[dict[str, Any], ...]
    #: The ratings block: the labels, the clock at which the award is made, and the man
    #: of the match with the paragraph about them. The award covers the whole match, so
    #: the page holds it back until the replay reaches ``decided_at`` — the same rule the
    #: goal list follows, where the payload is the finished script and the page is the
    #: only thing that decides what a reader has earned the right to see yet.
    ratings: dict[str, Any]

    @property
    def duration(self) -> int:
        """The match clock this replay runs to."""
        return self.frames[-1].clock if self.frames else 0

    def to_dict(self) -> dict[str, Any]:
        """The JSON document the browser consumes, and the tests compare.

        Normalised to lists rather than left as the tuples ``asdict`` produces. The
        difference is not cosmetic: this method is what the tests assert against, so a
        document that is *nearly* what ``json`` emits would let a real difference
        between the payload and the page hide behind a tuple/list mismatch.
        """
        return _jsonable(asdict(self))


def _jsonable(value: Any) -> Any:
    """Recursively convert to the shapes ``json`` produces, tuples included."""
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _windows_for(events: Sequence[MatchEvent], window_seconds: int, first_start: int) -> list[Window]:
    """Every analysable window, stopping at the last event we actually have.

    A window is only offered once a recorded event sits at or after its end, so the
    replay never presents a window the analyser could not have closed.
    """
    last = max((e.clock for e in events), default=0)
    windows: list[Window] = []
    start = first_start
    while start + window_seconds <= last:
        windows.append((start, start + window_seconds))
        start += window_seconds
    return windows


def _readings(snap: Snapshot) -> tuple[Reading, ...]:
    return tuple(
        Reading(
            key=r.key,
            metric=r.metric,
            team=r.team,
            value=r.value,
            baseline_value=r.baseline_value,
            delta=r.delta,
            direction=r.direction,
            is_flat=r.is_flat,
        )
        for r in snap.readings
    )


def _as_incident(event: MatchEvent) -> Incident:
    return Incident(
        clock=event.clock,
        minute=event.minute,
        type=event.type,
        team=event.team,
        player=event.player.name,
        pressure=event.pressure,
        speed_kmh=event.speed_kmh,
    )


def _incidents(
    events: Sequence[MatchEvent], window: Window
) -> tuple[Incident | None, tuple[Incident, ...]]:
    """The notable events inside ``window``.

    Returns the single most consequential event alongside the list to display. The
    two differ because the list is re-sorted into clock order for reading, and quoting
    the earliest of a busy spell as *the* moment would be an accident of sorting
    rather than a judgement about what mattered.
    """
    start, end = window
    inside = [
        e for e in events if start <= e.clock < end and e.type in _INCIDENT_WEIGHTS
    ]
    if not inside:
        return None, ()

    inside.sort(
        key=lambda e: (_INCIDENT_WEIGHTS[e.type] * (e.pressure or 0.0), e.clock),
        reverse=True,
    )
    picked = sorted(inside[:_MAX_INCIDENTS_PER_FRAME], key=lambda e: e.clock)
    return _as_incident(inside[0]), tuple(_as_incident(e) for e in picked)


def _score_at(events: Sequence[MatchEvent], clock: int) -> tuple[int, int]:
    """The score as it stood at ``clock``, counting a goal on that exact second.

    Inclusive, unlike the half-open windows the metrics use. A goal at 90:00 belongs
    to the match, and the browser advances the scoreboard on the same boundary — the
    two have to agree or the page and its own payload would disagree about the score.
    """
    goals = [e for e in events if e.type == "goal" and e.clock <= clock]
    return (
        sum(1 for e in goals if e.team == "home"),
        sum(1 for e in goals if e.team == "away"),
    )


def _key_for(claim: Claim) -> str:
    return claim.metric if claim.team is None else f"{claim.metric}@{claim.team}"


def _rows_for(
    result: PipelineResult,
    narrator: InjectingNarrator,
    incident: str,
    home: str,
    away: str,
    first_seq: int,
) -> tuple[ClaimRow, ...]:
    """Turn one window's trace into feed rows, in the order the loop produced them.

    Rejected rows are rendered from the direction they *claimed*, so the analyst sees
    the sentence that would have gone out and the measurement that stopped it. The
    correction is rendered from what was measured. Both are shown; neither is
    silently rewritten.
    """
    by_id = {v.claim_id: v for v in result.verdicts}
    rejected_so_far: set[str] = set()
    staged: list[tuple[Claim, Any, bool, str | None]] = []

    for claim in result.claims:
        verdict = by_id.get(claim.claim_id)
        if verdict is None:  # pragma: no cover - every claim gets a verdict
            continue

        # A revision is identified by answering a claim that was just rejected, not
        # merely by ending in "r" — an id is the model's to choose and could end in
        # anything.
        base = claim.claim_id[:-1]
        is_correction = claim.claim_id.endswith("r") and base in rejected_so_far
        answers = base if is_correction else None

        staged.append((claim, verdict, is_correction, answers))
        if verdict.status == "rejected":
            rejected_so_far.add(claim.claim_id)

    # The incident tail rides on one published line, not on all of them. Repeating it
    # under every claim in a window made the feed read like a mail merge, which is the
    # opposite of what the tail is for. It goes on the first row a reader will see.
    carrier = next(
        (index for index, (_, verdict, _, _) in enumerate(staged) if verdict.is_verified),
        None,
    )

    rows: list[ClaimRow] = []
    for index, (claim, verdict, is_correction, answers) in enumerate(staged):
        rows.append(
            ClaimRow(
                seq=first_seq + index,
                claim_id=claim.claim_id,
                window=claim.window,
                metric=claim.metric,
                team=claim.team,
                kind="correction" if is_correction else "claim",
                status=verdict.status,
                direction=claim.direction,
                magnitude=claim.magnitude,
                text=compose(
                    claim.metric,
                    claim.direction,
                    team=claim.team,
                    home=home,
                    away=away,
                    incident=incident if index == carrier else "",
                ),
                receipt=verdict.discrepancy(),
                reason=verdict.reason,
                claimed_direction=verdict.claimed_direction,
                claimed_magnitude=verdict.claimed_magnitude,
                measured_direction=verdict.measured_direction,
                measured_value=verdict.measured_value,
                baseline_value=verdict.baseline_value,
                delta=verdict.delta,
                answers=answers,
                # Only a first attempt can carry a planted fault; a correction was
                # measured, so it is never flagged as injected.
                injected=(
                    not is_correction
                    and verdict.status == "rejected"
                    and _key_for(claim) in narrator.injected
                ),
            )
        )

    return tuple(rows)


def _squad_payload(squad: Squad) -> list[dict[str, Any]]:
    return [
        {"name": p.name, "shirt": p.shirt, "position": p.position, "id": p.id}
        for p in squad.players
    ]


def build_replay(
    seed: int = DEFAULT_SEED,
    match_id: str = "PF-001",
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    first_window_start: int = DEFAULT_FIRST_WINDOW_START,
    fault: str = "none",
    fault_target: str | None = None,
    max_frames: int | None = None,
) -> Replay:
    """Run the loop across a whole match and record every frame.

    Args:
        seed: the match seed. Everything downstream is a function of this. The default
            is the seed behind :data:`pitchframe.scenarios.DEFAULT_SCENARIO` — five
            goals, four of them from moves long enough to draw on the build-up panel —
            because an overlay that opens on a match with nothing to show reads as
            broken however correct it is. ``scenarios.SCENARIOS['quiet'].seed`` is the
            single-goal match, and ``--seed`` still takes any number at all.
        match_id: identifier used in generated event ids.
        window_seconds: length of each analysed window.
        first_window_start: where the first window begins; must leave room for a
            baseline before it.
        fault: which narrator fault to inject. ``none`` is the honest baseline;
            ``narrator-invert`` plants one wrong direction per window so the catch is
            visible. Injected rows are labelled, never passed off as natural errors.
        fault_target: which reading ``narrator-invert`` should invert, by key
            (``momentum``, or ``possession_share@away`` for a side-scoped metric).
            Defaults to the strongest reading in each window, which is honest but
            arbitrary — so a demonstration or a test that needs one particular metric
            caught would otherwise have to go hunting for a window where it happened
            to rank first.
        max_frames: stop after this many windows, for a shorter recording.

    Returns:
        A :class:`Replay` whose :meth:`~Replay.to_dict` is JSON-serialisable and
        byte-identical across calls with equal arguments.

    Raises:
        ValueError: if ``fault`` is not a known mode, so a typo in a query string
            fails loudly instead of silently disabling the demonstration. Also raised
            when a ``fault_target`` is supplied with no fault to apply it to — that
            request would otherwise do nothing at all, quietly.
    """
    if fault not in FAULT_MODES:
        raise ValueError(f"unknown fault {fault!r}; known modes are {', '.join(FAULT_MODES)}")
    if fault_target is not None and fault != "narrator-invert":
        raise ValueError(
            f"fault_target={fault_target!r} only applies to 'narrator-invert', not {fault!r}"
        )

    match: Match = generate_match(seed, match_id)
    home, away = match.home.name, match.away.name
    squads: dict[Team, Squad] = {"home": match.home, "away": match.away}
    windows = _windows_for(match.events, window_seconds, first_window_start)
    if max_frames is not None:
        windows = windows[:max_frames]

    frames: list[Frame] = []
    ledger = {"verified": 0, "rejected": 0, "corrections": 0, "dropped": 0}
    injected: set[str] = set()
    seq = 0

    for index, window in enumerate(windows):
        baseline = baseline_window_for(window)
        # Only what had already happened, and only windows already closed.
        events_so_far = tuple(e for e in match.events if e.clock < window[1])
        closed = tuple(windows[: index + 1])

        narrator = InjectingNarrator(fault=fault, target=fault_target)
        result = asyncio.run(
            run_pipeline(
                narrator,
                RuleVerifier(),
                events_so_far,
                window,
                baseline,
                closed_windows=closed,
            )
        )
        injected |= narrator.injected

        lead, incidents = _incidents(events_so_far, window)
        incident_text = (
            incident_clause(lead.type, lead.player, lead.minute) if lead is not None else ""
        )

        rows = _rows_for(result, narrator, incident_text, home, away, seq)
        seq += len(rows)

        counts = result.summary()
        for key in ledger:
            ledger[key] += counts.get(key, 0)

        frame_score = _score_at(events_so_far, window[1])
        frames.append(
            Frame(
                index=index,
                clock=window[1],
                minute=window[1] // 60,
                window=window,
                baseline=baseline,
                score=frame_score,
                feed=rows,
                readings=_readings(snapshot(events_so_far, window, baseline)),
                incidents=incidents,
                ledger=dict(ledger),
                ratings=ratings_frame(
                    events_so_far, squads, score=frame_score, clock=window[1]
                ),
            )
        )

    final_clock = frames[-1].clock if frames else 0
    selection = select_tier()
    return Replay(
        seed=seed,
        match_id=match.id,
        home=home,
        away=away,
        squads={"home": _squad_payload(match.home), "away": _squad_payload(match.away)},
        window_seconds=window_seconds,
        fault=fault,
        provider={
            "key": selection.key,
            "label": selection.tier.label,
            "reason": selection.reason,
            "banner": selection.banner(),
            "uses_a_model": selection.uses_a_model,
            "is_fallback": selection.is_fallback,
        },
        frames=tuple(frames),
        injected=tuple(sorted(injected)),
        # Every goal the replay will actually reach, so the scoreboard can advance on
        # the exact minute rather than only when a window closes.
        goals=tuple(
            _as_incident(e)
            for e in match.events
            if e.type == "goal" and e.clock <= final_clock
        ),
        # Built from the same match object the frames came from, so the panel and the
        # feed cannot disagree about who scored or when.
        highlights=tuple(
            highlight.to_dict()
            for highlight in build_highlights(match.events, seed=seed, until=final_clock)
        ),
        # Rated over the whole match, and withheld by the page until the replay reaches
        # ``decided_at``: naming a man of the match before the match has been played out
        # would spoil the replay exactly as printing the final score would.
        ratings=ratings_payload(
            match.events,
            squads,
            home=home,
            away=away,
            score=_score_at(match.events, final_clock),
            decided_at=final_clock,
        ),
    )
