"""Command-line entry point: ``python -m pitchframe``.

Generates one synthetic match and prints an analytics summary. This is the
quickest way to confirm the core pipeline runs end to end — it needs no
dependencies, no network, and no credentials, so it works on a bare Python
install. The ``--json`` form is what later stages (overlay, evaluation) consume.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from .generator import generate_match
from .schema import Match, MatchEvent
from .stats import (
    chaos_index,
    longest_pressure_pass_streak,
    momentum,
    pass_accuracy,
    pass_difficulty_mean,
    peak_speed,
    possession_share,
    pressure_index,
)

__all__ = ["main", "summarise", "key_moments"]

#: Event types worth surfacing as "moments", and how much each matters.
_NOTABLE_WEIGHTS = {"goal": 3.0, "shot": 2.0, "tackle": 1.0}


def key_moments(events: Sequence[MatchEvent], limit: int) -> list[MatchEvent]:
    """Rank the most consequential events.

    Weighted by event type and scaled by pressure, so a high-pressure shot
    outranks a routine one. Ties fall back to clock order for stability.
    """
    notable = [e for e in events if e.type in _NOTABLE_WEIGHTS]
    notable.sort(key=lambda e: (_NOTABLE_WEIGHTS[e.type] * (e.pressure or 0.0), -e.clock), reverse=True)
    return notable[:limit]


def summarise(match: Match, moments: int = 5) -> dict[str, object]:
    """Build a JSON-serialisable summary of a match."""
    events = match.events
    home_goals, away_goals = match.score
    return {
        "match_id": match.id,
        "home": match.home.name,
        "away": match.away.name,
        "score": {"home": home_goals, "away": away_goals},
        "event_count": len(events),
        "possession": possession_share(events),
        "pass_accuracy": {
            "home": pass_accuracy(events, "home"),
            "away": pass_accuracy(events, "away"),
        },
        "pass_difficulty_mean": {
            "home": pass_difficulty_mean(events, "home"),
            "away": pass_difficulty_mean(events, "away"),
        },
        "pressure_index": {
            "home": pressure_index(events, "home"),
            "away": pressure_index(events, "away"),
        },
        "chaos_index": chaos_index(events),
        "momentum": momentum(events),
        "peak_speed_kmh": {
            "pass": peak_speed(events, kind="pass"),
            "shot": peak_speed(events, kind="shot"),
        },
        "milestones": {
            "longest_pressure_pass_streak": {
                "home": longest_pressure_pass_streak(events, "home"),
                "away": longest_pressure_pass_streak(events, "away"),
            }
        },
        "key_moments": [
            {
                "clock": e.clock,
                "minute": e.minute,
                "type": e.type,
                "team": e.team,
                "player": e.player.name,
                "pressure": e.pressure,
                "speed_kmh": e.speed_kmh,
            }
            for e in key_moments(events, moments)
        ],
    }


def _print_report(s: dict[str, object]) -> None:
    score = s["score"]
    possession = s["possession"]
    assert isinstance(score, dict) and isinstance(possession, dict)

    print(f"{s['home']}  {score['home']} - {score['away']}  {s['away']}")
    print(f"match {s['match_id']} | {s['event_count']} events\n")

    print("  possession          "
          f"home {possession['home']:.1%}  away {possession['away']:.1%}")

    for label, key, fmt in (
        ("pass accuracy", "pass_accuracy", "{:.1%}"),
        ("pass difficulty", "pass_difficulty_mean", "{:.3f}"),
        ("pressure index", "pressure_index", "{:.3f}"),
    ):
        values = s[key]
        assert isinstance(values, dict)
        print(f"  {label:<19} home {fmt.format(values['home'])}  away {fmt.format(values['away'])}")

    print(f"  chaos index         {s['chaos_index']:.3f}   (0 = controlled, 1 = frantic)")
    print(f"  momentum            {s['momentum']:+.1f}  (positive favours home)")

    speeds = s["peak_speed_kmh"]
    assert isinstance(speeds, dict)
    print(f"  peak speed          pass {speeds['pass']:.1f} km/h | shot {speeds['shot']:.1f} km/h")

    streaks = s["milestones"]
    assert isinstance(streaks, dict)
    streak = streaks["longest_pressure_pass_streak"]
    assert isinstance(streak, dict)
    print(f"  longest press. streak  home {streak['home']}  away {streak['away']}")

    print("\n  key moments")
    for moment in s["key_moments"]:  # type: ignore[union-attr]
        assert isinstance(moment, dict)
        pressure = moment["pressure"]
        pressure_text = f"{pressure:.2f}" if isinstance(pressure, float) else "-"
        print(
            f"    {moment['minute']:02d}'  {moment['type']:<8} "
            f"{moment['team']:<4} {moment['player']:<10} pressure {pressure_text}"
        )


def _report_providers() -> int:
    """Print every model tier and whether this machine can use it."""
    from .llm.provider import describe_tiers, select_tier

    selection = select_tier()
    print("Model tiers, strongest first:\n")
    print(describe_tiers())
    print(f"\nselected    {selection.tier.label}")
    print(f"reason      {selection.reason}")
    print(f"banner      {selection.banner()}")
    return 0


def _run_claims(args: argparse.Namespace) -> int:
    """Run one window through the propose, verify and retry loop.

    Uses the model-free narrator and verifier in :mod:`pitchframe.agents.demo`, so
    this works on a bare interpreter with nothing installed. The model-backed agents
    implement the same protocols and are substituted in the same place.
    """
    from .agents.demo import InjectingNarrator, RuleVerifier
    from .agents.pipeline import render_snapshot, run_pipeline, snapshot
    from .claims import baseline_window_for
    from .llm.provider import select_tier

    try:
        start_text, end_text = args.window.split(":", 1)
        window = (int(start_text), int(end_text))
        baseline = baseline_window_for(window)
    except ValueError as exc:
        print(f"error: --window must be start:end seconds ({exc})", file=sys.stderr)
        return 2

    match = generate_match(args.seed, args.match_id)
    narrator = InjectingNarrator(fault=args.inject_fault)
    result = asyncio.run(
        run_pipeline(narrator, RuleVerifier(), match.events, window, baseline)
    )

    selection = select_tier()
    print(f"{match.home.name}  v  {match.away.name}    match {match.id}")
    print(f"provider    {selection.banner()}")
    print(f"narrator    scripted, fault={args.inject_fault}")
    print()
    print(render_snapshot(snapshot(match.events, window, baseline)))
    print()

    if narrator.injected:
        for key in sorted(narrator.injected):
            print(f"  ! FAULT INJECTED: narrator-invert on {key}")
        print()

    labels = {
        "claim.verified": "verified",
        "claim.rejected": "REJECTED",
        "claim.corrected": "corrected",
        "claim.corrected.failed": "still-wrong",
        "claim.abandoned": "abandoned",
        "claim.dropped": "dropped",
    }
    for event in result.trace:
        if event.kind in ("snapshot", "narrator.proposed"):
            continue
        label = labels.get(event.kind, event.kind)
        print(f"  {label:<12} {event.claim_id or '-':<9} {event.detail[:74]}")

    counts = result.summary()
    print()
    print(
        f"  verified {counts['verified']}   rejected {counts['rejected']}   "
        f"corrections {counts['corrections']}   dropped {counts['dropped']}"
    )
    if narrator.injected and counts["corrections"]:
        print(
            "\n  The injected fault was caught by measurement and corrected.\n"
            "  Re-run with --inject-fault none for the natural rejection rate."
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pitchframe",
        description="Generate a synthetic match and print its analytics summary.",
    )
    parser.add_argument("--seed", type=int, default=7, help="seed for the match simulation (default: 7)")
    parser.add_argument("--match-id", default="PF-001", help="match identifier used in event ids")
    parser.add_argument("--moments", type=int, default=5, help="how many key moments to list")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    parser.add_argument(
        "--providers",
        action="store_true",
        help="show which model tiers are usable on this machine, then exit",
    )
    parser.add_argument(
        "--claims",
        action="store_true",
        help="run the Narrator/Verifier claim pipeline over one window",
    )
    parser.add_argument(
        "--window",
        default="1200:1500",
        help="clock window for --claims, as start:end seconds (default: 1200:1500)",
    )
    parser.add_argument(
        "--inject-fault",
        default="narrator-invert",
        choices=["none", "narrator-invert"],
        help="fault to inject into the narrator (default: narrator-invert)",
    )
    args = parser.parse_args(argv)

    # Windows consoles are not reliably UTF-8. Downgrade unencodable glyphs
    # rather than letting a stray character crash the report.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    if args.providers:
        return _report_providers()

    if args.claims:
        return _run_claims(args)

    match = generate_match(args.seed, args.match_id)
    summary = summarise(match, args.moments)

    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        _print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
