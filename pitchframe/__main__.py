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
import threading
import time
import webbrowser
from collections.abc import Sequence

from .generator import generate_match
from .scenarios import (
    ALL_SCENARIOS,
    DEFAULT_SCENARIO_KEY,
    SCENARIOS,
    describe as describe_scenarios,
    scenario as scenario_for,
)
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

    A goal outranks everything else, whatever its pressure. It used to be weighted by
    type *and* scaled by pressure, which put a low-pressure tap-in below a
    high-pressure shot that was saved: a decided match could print a key-moments list
    with no goal in it at all, which reads as a fault in the report rather than as a
    quiet goal. Pressure still orders events *within* a class, so a high-pressure shot
    still outranks a routine one. Ties fall back to clock order for stability.
    """
    notable = [e for e in events if e.type in _NOTABLE_WEIGHTS]
    notable.sort(
        key=lambda e: (
            _NOTABLE_WEIGHTS[e.type],
            _NOTABLE_WEIGHTS[e.type] * (e.pressure or 0.0),
            -e.clock,
        ),
        reverse=True,
    )
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


def _run_claims(args: argparse.Namespace, seed: int) -> int:
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

    match = generate_match(seed, args.match_id)
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


def serving_here(host: str, port: int, timeout: float = 2.0) -> str | None:
    """What is answering on this address, if anything.

    ``ok`` for this project's own overlay, ``stale`` for one that has outlived the files it
    was started from, ``taken`` for something else on the port, and ``None`` for nothing.

    The launcher is run by double-click, and a double-click cannot be asked a question — so
    the three states a second start can find have to be told apart here rather than by the
    reader. Starting a second server regardless would fail with ``address already in use``,
    which says nothing about the real problem, and would leave the reader to work out why.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError:
        # Something is listening and answered, and it is not this probe's endpoint.
        return "taken"
    except (urllib.error.URLError, OSError):
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return "taken"
    if not isinstance(payload, dict) or "status" not in payload:
        return "taken"
    return "stale" if payload.get("code", {}).get("stale") else "ok"


def open_when_ready(url: str, host: str, port: int, timeout: float = 20.0) -> threading.Thread:
    """Open ``url`` in the default browser as soon as the server answers it.

    A thread rather than a call before ``uvicorn.run``: opening the browser first is a race
    the browser loses — a tab that loads loopback before anything is listening shows a
    connection error, and the reader is left reloading a page whose server was about to be
    ready. Polling the readiness probe the server already has costs one thread and removes
    the race.
    """
    def wait() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if serving_here(host, port, timeout=1.0) is not None:
                webbrowser.open(url)
                return
            time.sleep(0.25)
        print(
            f"  the browser was not opened: nothing answered on {url} within "
            f"{timeout:.0f} seconds"
        )

    thread = threading.Thread(target=wait, name="pitchframe-open", daemon=True)
    thread.start()
    return thread


def _run_overlay(args: argparse.Namespace, seed: int, scenario_key: str) -> int:
    """Serve the live overlay.

    The dependency check happens here rather than at import time so that every other
    command keeps working on an interpreter with nothing installed. A missing
    dependency is reported as a fixable instruction, not a traceback.
    """
    try:
        import uvicorn
    except ImportError:
        print(
            "error: the overlay needs fastapi and uvicorn.\n"
            "  python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    from .overlay.server import create_app
    from .overlay.stale import RESTART_COMMAND

    chosen = scenario_for(scenario_key)
    root = f"http://{args.host}:{args.port}"
    # Two addresses, because the overlay is two pages: the front door is the catalogue, and
    # a match is a path under it. The match is printed under its own name rather than as a
    # seed — the same address a card on the catalogue leads to, and the one a reader can
    # type back later without a number they would have to write down. A `--seed` that no
    # scenario owns has no name, so it is printed the way it was asked for.
    catalogue = f"{root}/"
    # Asked for by name when the seed is that scenario's own, and by number otherwise: a
    # `--seed 99` names no match, and printing `/match/matchday` for it would be a link to a
    # different match than the one this server is about to serve.
    match = (
        f"{root}/match/{chosen.key}" if seed == chosen.seed else f"{root}/match?seed={seed}"
    ) + ("" if args.inject_fault == "none" else f"?fault={args.inject_fault}")

    print("Pitchframe overlay")
    print(f"  scenario {chosen.key} — {chosen.note}")

    # Whether this call is the one that serves, or the one that finds a server already up.
    serving = serving_here(args.host, args.port)
    if serving == "stale":
        print(f"  a server is already running at {root}, and it is older than the files here.")
        print(f"  nothing it answers can be trusted until it restarts ({RESTART_COMMAND}).")
        return 2
    if serving == "taken":
        print(f"  something else is answering on {root}, and it is not Pitchframe.")
        print(f"  choose another port: python -m pitchframe --overlay --open --port 8010")
        return 2
    if serving == "ok":
        print(f"  already running: {catalogue}")
        if args.open:
            webbrowser.open(catalogue)
        return 0

    print(f"  {catalogue}   the catalogue — every named match, as a sphere")
    print(f"  {match}   this one directly")
    print(f"  fault={args.inject_fault}  (use fault=none in the URL for the honest baseline)")
    print("  Ctrl+C to stop")
    if args.open:
        open_when_ready(catalogue, args.host, args.port)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pitchframe",
        description="Generate a synthetic match and print its analytics summary.",
    )
    parser.add_argument(
        "--scenario",
        default=DEFAULT_SCENARIO_KEY,
        choices=sorted(ALL_SCENARIOS),
        # The whole catalogue, not just the three named showcase matches: ``--scenarios``
        # lists every one of them, and a banner that offered a name the parser then
        # refused would be a list of instructions that did not work.
        help="which named match to use when --seed is not given (default: "
        f"{DEFAULT_SCENARIO_KEY}; --scenarios lists them all)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed for the match simulation (default: the scenario's seed, "
        f"{SCENARIOS[DEFAULT_SCENARIO_KEY].seed})",
    )
    parser.add_argument(
        "--scenarios",
        action="store_true",
        dest="list_scenarios",
        help="list the named matches and their contents, then exit",
    )
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
    parser.add_argument(
        "--overlay",
        action="store_true",
        help="serve the live overlay in a browser (needs fastapi and uvicorn)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="with --overlay: open the catalogue in the default browser once it answers",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface for --overlay (default: 127.0.0.1, loopback only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="port for --overlay (default: 8000)",
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

    chosen = scenario_for(args.scenario)
    # An explicit --seed wins over the scenario's, so every command keeps taking any
    # match at all and "the named match" is only ever the default.
    seed = args.seed if args.seed is not None else chosen.seed

    if args.list_scenarios:
        print("Named matches:\n")
        print(describe_scenarios())
        print("\nPass --seed to open any other match, or --scenario <name> to open one of the above.")
        return 0

    if args.overlay:
        return _run_overlay(args, seed, args.scenario)

    if args.claims:
        return _run_claims(args, seed)

    match = generate_match(seed, args.match_id)
    summary = summarise(match, args.moments)

    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        _print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
