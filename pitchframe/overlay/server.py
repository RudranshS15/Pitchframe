"""Serving the overlay.

A thin shell. The interesting decisions were all made before a request arrives:
:mod:`pitchframe.overlay.replay` already computed every frame, so these handlers look
one up and hand it over. Nothing in the request path measures anything, calls a model,
or holds state that a refresh could lose.

The one piece of state is a bounded cache, and it is a cache rather than a queue on
purpose — a replay is a pure function of its parameters, so the same URL must produce
the same bytes every time. That is what lets a demo be recorded and then re-run
identically in front of someone.

The other piece of state is the code itself, and that is where the honest-serving
claim can quietly fail: a process that outlives an edit keeps answering from the
modules it imported at startup while the files on disk describe something else. A
replay built from stale modules is indistinguishable from a current one, so
:mod:`pitchframe.overlay.stale` compares the source it loaded with the source on disk
and the replay endpoint refuses to answer when they diverge, naming the files that
moved and how to restart. Static assets are exempt: they are read per request, so
they cannot go stale.

Requires ``fastapi`` and ``uvicorn`` (see ``requirements.txt``). The replay and
commentary modules it serves do not, which is why the overlay's behaviour is tested
without either.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from ..agents.demo import FAULT_MODES
from ..highlights import build_highlights
from ..ingest import openliga
from ..ingest.statsbomb import StatsBombError
from ..ingest.statsbomb import available as available_real_matches
from ..ingest.statsbomb import competitions as archive_competitions
from ..ingest.statsbomb import ensure as ensure_real_match
from ..ingest.statsbomb import is_cached
from ..ingest.statsbomb import load as load_real_match
from ..ingest.statsbomb import read_meta
from ..ingest.statsbomb import season_matches as archive_season_matches
from ..scenarios import (
    ALL_SCENARIOS,
    DEFAULT_SCENARIO_KEY,
    DEFAULT_SEED,
    catalogue,
)
from .replay import (
    DEFAULT_FIRST_WINDOW_START,
    DEFAULT_WINDOW_SECONDS,
    build_replay,
)
from .stale import LOADED_AT, code_state, static_digest

__all__ = ["create_app", "app", "MAX_WINDOWS"]

_STATIC_DIR = Path(__file__).with_name("static")

#: Upper bound on requested windows, so a crafted query cannot ask the server to
#: measure an unbounded number of them.
MAX_WINDOWS = 64


@lru_cache(maxsize=32)
def _replay_document(
    statsbomb_id: str | None,
    seed: int,
    match_id: str,
    fault: str,
    window_seconds: int,
    max_frames: int | None,
    competition: int | None = None,
    season: int | None = None,
) -> dict[str, Any]:
    """Build once, serve many. Cached because the result cannot vary.

    ``statsbomb_id`` selects an imported match and makes every other match argument
    irrelevant; ``None`` is the generated path, which is what every existing address
    already means. Nothing below the replay branches on where the events came from — an
    imported match goes through the same windows, claims and ratings as a seeded one.
    """
    if statsbomb_id is not None:
        # The archive names the competition and season a match belongs to, and passes
        # them so a match that has never been downloaded can be fetched on the spot. A
        # bare id keeps the old contract: it must already be cached, and the error says
        # how to fetch it.
        if competition is not None and season is not None:
            meta = ensure_real_match(
                statsbomb_id, competition=competition, season=season
            )
        else:
            meta = read_meta(statsbomb_id)
        match = load_real_match(statsbomb_id)
        return build_replay(
            0,
            meta.match_id,
            match=match,
            source="statsbomb",
            origin=meta.to_dict(),
            window_seconds=window_seconds,
            fault=fault,
            max_frames=max_frames,
        ).to_dict()
    return build_replay(
        seed,
        match_id,
        window_seconds=window_seconds,
        fault=fault,
        max_frames=max_frames,
    ).to_dict()


def _real_entries() -> list[dict[str, Any]]:
    """Every cached real match, as a catalogue entry.

    Built by loading the match, so a card's scoreline and its goal minutes are the ones the
    feed actually records rather than metadata that could drift from it. A cache with
    nothing in it returns nothing: the catalogue is honest about what has been imported,
    and a reader who has fetched no real match sees the generated ones alone.
    """
    entries: list[dict[str, Any]] = []
    for meta in available_real_matches():
        match = load_real_match(meta.match_id)
        goals = [event for event in match.events if event.type == "goal"]
        highlights = build_highlights(match.events, seed=0)
        entries.append(
            {
                "key": f"statsbomb-{meta.match_id}",
                "label": f"{meta.competition} {meta.season}".strip(),
                "seed": 0,
                "note": (
                    f"{meta.date} · {meta.stadium} · {len(goals)} goals"
                    if meta.stadium
                    else f"{meta.date} · {len(goals)} goals"
                ),
                "source": "statsbomb",
                "statsbomb": meta.match_id,
                "default": False,
                "home": meta.home_name,
                "away": meta.away_name,
                "score": list(match.score),
                "goals": len(goals),
                "minutes": [goal.minute for goal in goals],
                "long_build_ups": sum(1 for item in highlights if len(item.steps) >= 5),
                "path": f"/match?statsbomb={meta.match_id}",
            }
        )
    return entries


@lru_cache(maxsize=4)
def _catalogue_document(default_match: str | None) -> dict[str, Any]:
    """The named matches, built once per process.

    Cached for the same reason a replay is: the answer is a pure function of source
    that does not change while the process lives. Measuring the whole catalogue costs
    about a second, which is a second a reader would otherwise spend staring at an
    empty sphere every time they opened the mode browser, and there is nothing to
    gain by measuring it again -- :func:`pitchframe.overlay.stale.code_state` is what
    notices when the source it came from has moved on.
    """
    # Imported matches first: a reader with a real match downloaded is much more likely
    # to want it than the thirty-sixth generated one, and the sphere's front card is the
    # one a reader opens without choosing.
    entries = _real_entries() + catalogue()
    # The address of each match is added here rather than in `pitchframe.scenarios`, which
    # knows about matches and nothing about HTTP: a catalogue entry that carried its own
    # path would make a registry module the second place a route is decided, and the first
    # time a route moved the catalogue would be serving the old one.
    for entry in entries:
        if entry["source"] == "synthetic":
            entry["path"] = f"/match/{entry['key']}"
    default_key = (
        f"statsbomb-{default_match}" if default_match is not None else DEFAULT_SCENARIO_KEY
    )
    for entry in entries:
        entry["default"] = entry["key"] == default_key
    return {"count": len(entries), "scenarios": entries}


@lru_cache(maxsize=1)
def _archive_catalogue() -> tuple[dict[str, Any], ...]:
    """The archive's competition list, read once per process.

    The file is small but the response is served to every reader of the matches page, and
    nothing about it changes while the process lives. The source digest is what notices
    when the module behind it moved, exactly as for the scenario catalogue.
    """
    return archive_competitions()


@lru_cache(maxsize=16)
def _archive_season_matches(competition: int, season: int) -> tuple[Any, ...]:
    """One season's fixtures, read once per process.

    Sixteen seasons is more than a reader opens in a sitting, and the bound stops an
    arbitrary pair of query parameters from turning the process into an unbounded cache.
    A download failure is not cached: lru_cache stores return values only.
    """
    return archive_season_matches(competition, season)


def _seed_for(scenario_key: str) -> int:
    """The seed behind a named match, or a 400 that lists the names there are.

    A name is a seed with a reason attached, so this is the one place the two are
    interchangeable. An unknown name is refused rather than falling back to the default:
    a typo that quietly served a different match would look exactly like a match nobody
    wanted, which is the failure :func:`pitchframe.scenarios.scenario` exists to prevent
    on the command line.
    """
    entry = ALL_SCENARIOS.get(scenario_key)
    if entry is None:
        known = ", ".join(sorted(ALL_SCENARIOS))
        raise HTTPException(
            status_code=400,
            detail=f"unknown match {scenario_key!r}; named matches are: {known}",
        )
    return entry.seed


def _named(key: str, request: Request) -> RedirectResponse:
    """Send an address that describes its match in the query to that match's own name.

    A link already handed out keeps working; what it lands on is the address that says
    which match it is, and the rest of the query string — the view, the second, the open
    build-up, the fault, the match id — travels with it, because those describe the frame
    and not the match. The two parameters that *are* the match are dropped: ``seed`` and
    ``scenario`` are what the name in the path replaces, and a URL carrying both would be
    two descriptions of one thing.
    """
    rest = [
        (name, value)
        for name, value in request.query_params.multi_items()
        if name not in {"seed", "scenario"}
    ]
    query = urlencode(rest)
    return RedirectResponse(f"/match/{key}" + (f"?{query}" if query else ""), status_code=308)


def _require_current_code() -> None:
    """Refuse to answer from modules that have outlived their source.

    Both documents below are built from imported modules, so both go wrong in the same
    way after an edit: the process answers from what it loaded while the files describe
    something else. The catalogue has one extra edge -- it names matches by seed, so a
    stale one could offer a card whose replay this process would build differently.
    """
    state = code_state()
    if state.is_stale:
        raise HTTPException(status_code=503, detail=state.message())


def _validate_fault(fault: str, window_seconds: int, max_frames: int | None) -> None:
    """Reject a fault or a frame count that cannot be honoured, naming the fix."""
    if fault not in FAULT_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown fault {fault!r}; known modes are {', '.join(FAULT_MODES)}",
        )
    if window_seconds <= 0:
        raise HTTPException(status_code=400, detail="window_seconds must be positive")
    if max_frames is not None and not (0 < max_frames <= MAX_WINDOWS):
        raise HTTPException(
            status_code=400, detail=f"max_frames must be between 1 and {MAX_WINDOWS}"
        )


def _validate(seed: int, fault: str, window_seconds: int, max_frames: int | None) -> None:
    """Reject impossible parameters with a message that names the fix."""
    _validate_fault(fault, window_seconds, max_frames)
    if seed < 0:
        raise HTTPException(status_code=400, detail="seed must not be negative")


def create_app(default_match: str | None = None) -> FastAPI:
    """Build the application. Separate from the module-level ``app`` so tests can
    construct one without importing a server, and so a second instance is possible.

    Args:
        default_match: a cached StatsBomb match id to open on. When set, the bare front
            door and a replay request with no match arguments both serve that real match,
            and its card is the one marked default; when ``None`` the generated catalogue
            opens exactly as it always has.
    """
    application = FastAPI(
        title="Pitchframe overlay",
        description="A match feed in which every published line is a checked claim.",
        docs_url=None,
        redoc_url=None,
    )

    @application.middleware("http")
    async def never_cache(request: Request, call_next):
        """Read per request, and say so.

        An asset on disk is only current if the browser fetches it. ``FileResponse``
        and ``StaticFiles`` send ``Last-Modified`` and ``ETag``, which is an invitation
        to cache: a reload may then be answered from the browser's own copy, and a fix
        to ``app.js`` reaches nobody — the same failure as a process that outlived its
        source, one layer further out, and this one leaves no 503 to notice. Nothing
        served here is worth keeping: the files are small and the payload is a pure
        function of its parameters.
        """
        response = await call_next(request)
        response.headers["cache-control"] = "no-store"
        return response

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """The mode browser: the catalogue as a sphere, and the front door.

        The choice comes before the thing chosen. This used to be the feed, with the
        catalogue one link away — which meant the first thing a reader met was one match
        they had not picked, and the eighteen others had to be found. Opening on the sphere
        puts the choice where the reader starts, and every card on it leads to a match.
        """
        return FileResponse(_STATIC_DIR / "index.html")

    @application.get("/match", include_in_schema=False)
    def match(request: Request) -> Response:
        """The feed: one match, replays measured and every published line checked.

        A path rather than a query parameter on the front door, because the two pages do
        different jobs and are addressed differently: this one is a *frame* of one replay
        — the view, the match second and the open build-up all belong in its query string,
        and they are what a reader shares. The front door has no frame to share and no
        seed until a card is clicked.

        Which match is in the path and not in the query, so that a link says what it is:
        ``/match/quiet`` rather than ``/match?seed=7``. Nothing gaps there — a bare
        ``/match`` goes to the default scenario's name, a ``scenario=`` that names one of
        the catalogue's matches goes to that match's name, and a numbered address that
        names a catalogued match is redirected to its name, so every link this server
        serves ends up at one address per match. In each of those the rest of the query
        string travels: the frame is what a reader shares, and a redirect that dropped
        ``at`` or ``view`` would answer with a different frame than the one asked for.

        A seed that is not in the catalogue has no name to go to and is served as it
        always was: ``/match?seed=99`` still opens a match, which is the promise that made
        ``--seed`` worth having. An unknown *name* is served the same way, and the replay
        endpoint is what refuses it with the names there are — better a page that says the
        name is wrong than one that quietly opened a different match.
        """
        named = request.query_params.get("scenario")
        numbered = request.query_params.get("seed")
        imported = request.query_params.get("statsbomb")
        if imported is not None:
            if named is not None or numbered is not None:
                raise HTTPException(
                    status_code=400,
                    detail="ask for a real match, a named scenario or a seed — not two at once",
                )
            # The page reads the id from the query itself, exactly as it reads a scenario
            # name; nothing about the document differs, so the same page serves both.
            return FileResponse(_STATIC_DIR / "match.html")
        if named is not None and numbered is not None:
            raise HTTPException(
                status_code=400,
                detail="ask for a match by name or by number, not both",
            )
        if named is not None:
            if named in ALL_SCENARIOS:
                return _named(named, request)
            # An unknown name is served rather than redirected to a match it does not
            # name: the replay endpoint answers 400 with the names there are, and a page
            # that said so is better than one that silently opened the default.
            return FileResponse(_STATIC_DIR / "match.html")
        if numbered is None:
            if default_match is not None:
                # A server started against a real match opens on it, with the rest of the
                # frame's parameters carried across, for the same reason a named redirect
                # carries them: they describe the frame, not which match it is.
                rest = [
                    (name, value)
                    for name, value in request.query_params.multi_items()
                    if name not in {"seed", "scenario", "statsbomb"}
                ]
                query = urlencode(rest)
                return RedirectResponse(
                    f"/match?statsbomb={default_match}" + (f"&{query}" if query else ""),
                    status_code=308,
                )
            # The frame a reader is looking at survives the move to the default's own
            # address: `at`, `view`, `goal` and the fault describe the replay, not which
            # one it is, and a shared link is exactly those parameters.
            return _named(DEFAULT_SCENARIO_KEY, request)
        try:
            seed = int(numbered)
        except ValueError:
            return FileResponse(_STATIC_DIR / "match.html")
        for key, entry in ALL_SCENARIOS.items():
            if entry.seed == seed:
                return _named(key, request)
        return FileResponse(_STATIC_DIR / "match.html")

    @application.get("/match/{key}", include_in_schema=False)
    def named_match(key: str) -> FileResponse:
        """One named match, at the address that names it.

        The key is checked here rather than in the page, because a page that was served
        for a name it cannot resolve would fetch nothing and report it as a network
        failure: the server knows the catalogue, so an unknown name is a 404 naming the
        ones there are, and the reader is one link from a match instead of one mystery.
        """
        if key not in ALL_SCENARIOS:
            known = ", ".join(sorted(ALL_SCENARIOS))
            raise HTTPException(
                status_code=404,
                detail=f"unknown match {key!r}; named matches are: {known}. "
                "Any seed still opens a match: /match?seed=<number>",
            )
        return FileResponse(_STATIC_DIR / "match.html")

    @application.get("/api/replay")
    def replay(
        scenario: str | None = None,
        seed: int | None = None,
        statsbomb: str | None = None,
        competition: int | None = None,
        season: int | None = None,
        match_id: str = "PF-001",
        fault: str = "none",
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        max_frames: int | None = None,
    ) -> dict[str, Any]:
        """The whole script for one match: every frame, claim and verdict.

        A match can be asked for by name (``scenario=matchday``) or by number
        (``seed=28``), and both reach the same replay, because a name *is* a seed with a
        reason attached. Asking for both is refused rather than resolved: they can name
        different matches, and a page that sent both would be asking for two matches at
        once, which is a request whose answer is a coin toss.

        ``seed`` defaults to the seed of the default scenario rather than to a number
        chosen here: the page does not send one until it has been given one, so a bare
        ``/`` and a bare ``/api/replay`` both open the multi-goal match the overlay is
        meant to be shown on. Any seed at all is still accepted — the scenario is a
        default, not a restriction.

        ``fault=none`` is the honest baseline. ``fault=narrator-invert`` plants one
        wrong direction per window so the catch is visible; the planted rows carry
        ``injected: true`` and the page labels them, because a manufactured failure
        presented as a natural one would make the catch rate unfalsifiable.

        ``competition`` and ``season`` belong to a recorded match, and travel with
        ``statsbomb`` when it was opened from the archive. They are what lets a match that
        has never been downloaded be fetched at the moment it is asked for, with the pair
        the archive named rather than the default. A bare ``statsbomb`` keeps the older
        contract: it must already be cached.

        A 503 rather than a slightly wrong document when the code on disk has moved on:
        the page already surfaces the server's ``detail`` when a request fails, so an
        out-of-date process says so in place of quietly answering with old numbers.
        """
        asked = [
            name
            for name, value in (
                ("scenario", scenario),
                ("seed", seed),
                ("statsbomb", statsbomb),
            )
            if value is not None
        ]
        if len(asked) > 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "give one of scenario, seed or statsbomb, not both: "
                    "they can name different matches"
                ),
            )
        if (competition is None) != (season is None):
            raise HTTPException(
                status_code=400,
                detail="competition and season name an archive match together; give both",
            )
        if competition is not None and statsbomb is None:
            raise HTTPException(
                status_code=400,
                detail="competition and season only describe a recorded match",
            )

        if statsbomb is not None:
            # The id is a cache directory and this is a public query parameter: digits
            # only, before anything touches the filesystem.
            if not statsbomb.isdigit():
                raise HTTPException(
                    status_code=400,
                    detail=f"statsbomb must be a numeric match id, got {statsbomb!r}",
                )
            _validate_fault(fault, window_seconds, max_frames)
            _require_current_code()
            # Building the replay raises StatsBombError when the id is not cached; the
            # handler below turns that into a 404 that names the fetch command rather
            # than a 500 that says nothing.
            try:
                return _replay_document(
                    statsbomb,
                    0,
                    "",
                    fault,
                    window_seconds,
                    max_frames,
                    competition,
                    season,
                )
            except StatsBombError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        if scenario is not None:
            seed = _seed_for(scenario)
        elif seed is None:
            if default_match is not None:
                try:
                    return _replay_document(
                        default_match, 0, "", fault, window_seconds, max_frames, None, None
                    )
                except StatsBombError as exc:
                    raise HTTPException(status_code=404, detail=str(exc)) from None
            seed = DEFAULT_SEED

        _validate(seed, fault, window_seconds, max_frames)
        _require_current_code()
        return _replay_document(
            None, seed, match_id, fault, window_seconds, max_frames, None, None
        )

    @application.get("/matches", include_in_schema=False)
    def matches_page() -> FileResponse:
        """Live scores and the recorded archive, on one page.

        The sphere is a curated catalogue: matches chosen for what they contain. This page
        is the other way to choose — what is on now, and everything in the archive — and
        it is a list rather than a sphere because a fixture that kicks off in twenty
        minutes does not need to be orbited to be found.
        """
        return FileResponse(_STATIC_DIR / "matches.html")

    @application.get("/api/live")
    def live(league: str = "bl1", refresh: bool = False) -> dict[str, Any]:
        """The current matchday of one competition, from the keyless OpenLigaDB feed.

        ``refresh=true`` skips the short cache and asks upstream now. The page uses it for
        the refresh control, not for every poll: the cache is what keeps a reader's
        thirty-second refresh from becoming the feed's worst customer.
        """
        try:
            return openliga.matchday(league, force=refresh).to_dict()
        except openliga.OpenLigaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    @application.get("/api/archive/competitions")
    def archive_catalogue() -> dict[str, Any]:
        """Every competition and season the StatsBomb archive publishes.

        Served from a file cache after the first request. A failure here is upstream's,
        and 502 says so rather than a 404 that would claim the archive has nothing.
        """
        try:
            entries = _archive_catalogue()
        except StatsBombError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        return {"count": len(entries), "competitions": list(entries)}

    @application.get("/api/archive/matches")
    def archive_season(competition: int, season: int) -> dict[str, Any]:
        """One season's fixtures, with scores and whether each is already downloaded.

        The ``cached`` flag is what lets the page say "opens at once" or "downloads on
        first open" before a reader clicks, instead of surprising them with a wait.
        """
        try:
            rows = _archive_season_matches(competition, season)
        except StatsBombError as exc:
            # A season the archive does not have is a 404; a download that failed is 502.
            status = 404 if "has no matches" in str(exc) else 502
            raise HTTPException(status_code=status, detail=str(exc)) from None

        name, season_name = "", ""
        for entry in _archive_catalogue():
            if entry["id"] != competition:
                continue
            name = entry["name"]
            season_name = next(
                (item["name"] for item in entry["seasons"] if item["id"] == season), ""
            )
            break
        return {
            "competition": {"id": competition, "name": name},
            "season": {"id": season, "name": season_name},
            "count": len(rows),
            "matches": [
                row.to_dict(cached=is_cached(row.meta.match_id)) for row in rows
            ],
        }

    @application.get("/api/scenarios")
    def scenarios() -> dict[str, Any]:
        """Every named match, with what each one contains, for the mode browser.

        The sphere the mode browser draws is a card per scenario, and each card carries
        both the seed that opens the match behind it and the ``path`` that names it. That
        pair is the whole interface: the browser navigates to the card's own address, the
        page asks for it by name, and the overlay replays what it always replays — so
        browsing the catalogue measures nothing new and a card cannot open a match the
        replay endpoint would not also serve. The path is composed here rather than in
        JavaScript for the same reason the rest of the entry is: an address is a fact
        about the catalogue, and a page that built one from a key would be a second place
        where the site's URLs are decided.

        Served from here rather than from a list written into the page for the reason
        :mod:`pitchframe.scenarios` gives for existing at all: a second copy of the
        catalogue in JavaScript is a copy that drifts, and the notes on these cards are
        the thing a reader chooses by.
        """
        _require_current_code()
        return _catalogue_document(default_match)

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        """Cheap readiness probe: which faults exist, and which code is serving them."""
        state = code_state()
        return {
            "status": "ok",
            "faults": list(FAULT_MODES),
            "default_seed": DEFAULT_SEED,
            "real_matches": [meta.match_id for meta in available_real_matches()],
            "window_seconds": DEFAULT_WINDOW_SECONDS,
            "first_window_start": DEFAULT_FIRST_WINDOW_START,
            # Hashes rather than versions: there is no version, and the question this
            # answers is "do the files on disk match the process you are talking to".
            "code": {
                "loaded": state.loaded,
                "current": state.current,
                "stale": state.is_stale,
                "changed": list(state.changed),
                # The static tree is served fresh, but its digest is the way a deployed
                # instance says which assets it carries — same reason as the hash.
                "static": static_digest(),
            },
            # When this process started, so a restart can be confirmed from the probe.
            "started": LOADED_AT,
        }

    application.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @application.get("/mode-browser", include_in_schema=False)
    def mode_browser() -> RedirectResponse:
        """Where the front door used to be, still working for anyone holding a link.

        A redirect rather than a second copy of the page: two addresses for one document is
        the same fault as two controls for one value — a reader who bookmarked the old one
        would keep landing on a page this server does not consider canonical. The
        middleware above sets ``no-store`` on it, so the browser cannot cache the hop and
        miss a later change of address.
        """
        return RedirectResponse("/", status_code=308)

    return application


#: Module-level instance for ``uvicorn pitchframe.overlay.server:app``.
app = create_app()
