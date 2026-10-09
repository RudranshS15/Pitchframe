"""Command-line entry point: ``python -m pitchframe``.

Generates one synthetic match and prints an analytics summary. This is the
quickest way to confirm the core pipeline runs end to end — it needs no
dependencies, no network, and no credentials, so it works on a bare Python
install. The ``--json`` form is what later stages (overlay, evaluation) consume.
"""

from __future__ import annotations

import argparse
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pitchframe",
        description="Generate a synthetic match and print its analytics summary.",
    )
    parser.add_argument("--seed", type=int, default=7, help="seed for the match simulation (default: 7)")
    parser.add_argument("--match-id", default="PF-001", help="match identifier used in event ids")
    parser.add_argument("--moments", type=int, default=5, help="how many key moments to list")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    # Windows consoles are not reliably UTF-8. Downgrade unencodable glyphs
    # rather than letting a stray character crash the report.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    match = generate_match(args.seed, args.match_id)
    summary = summarise(match, args.moments)

    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        _print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
