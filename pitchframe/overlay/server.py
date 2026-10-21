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
from .stale import code_state

__all__ = ["create_app", "app", "MAX_WINDOWS"]

_STATIC_DIR = Path(__file__).with_name("static")

#: Upper bound on requested windows, so a crafted query cannot ask the server to
#: measure an unbounded number of them.
MAX_WINDOWS = 64


@lru_cache(maxsize=32)
def _replay_document(
    seed: int,
    match_id: str,
    fault: str,
    window_seconds: int,
    max_frames: int | None,
) -> dict[str, Any]:
    """Build once, serve many. Cached because the result cannot vary."""
    return build_replay(
        seed,
        match_id,
        window_seconds=window_seconds,
        fault=fault,
        max_frames=max_frames,
    ).to_dict()


@lru_cache(maxsize=1)
def _catalogue_document() -> dict[str, Any]:
    """The named matches, built once per process.

    Cached for the same reason a replay is: the answer is a pure function of source
    that does not change while the process lives. Measuring the whole catalogue costs
    about a second, which is a second a reader would otherwise spend staring at an
    empty sphere every time they opened the mode browser, and there is nothing to
    gain by measuring it again -- :func:`pitchframe.overlay.stale.code_state` is what
    notices when the source it came from has moved on.
    """
    entries = catalogue()
    # The address of each match is added here rather than in `pitchframe.scenarios`, which
    # knows about matches and nothing about HTTP: a catalogue entry that carried its own
    # path would make a registry module the second place a route is decided, and the first
    # time a route moved the catalogue would be serving the old one.
    for entry in entries:
        entry["path"] = f"/match/{entry['key']}"
    return {"count": len(entries), "scenarios": entries}


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


def _validate(seed: int, fault: str, window_seconds: int, max_frames: int | None) -> None:
    """Reject impossible parameters with a message that names the fix."""
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
    if seed < 0:
        raise HTTPException(status_code=400, detail="seed must not be negative")


def create_app() -> FastAPI:
    """Build the application. Separate from the module-level ``app`` so tests can
    construct one without importing a server, and so a second instance is possible."""
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

        A 503 rather than a slightly wrong document when the code on disk has moved on:
        the page already surfaces the server's ``detail`` when a request fails, so an
        out-of-date process says so in place of quietly answering with old numbers.
        """
        if scenario is not None and seed is not None:
            raise HTTPException(
                status_code=400,
                detail="give either scenario or seed, not both: they can name different matches",
            )
        if scenario is not None:
            seed = _seed_for(scenario)
        elif seed is None:
            seed = DEFAULT_SEED

        _validate(seed, fault, window_seconds, max_frames)
        _require_current_code()
        return _replay_document(seed, match_id, fault, window_seconds, max_frames)

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
        return _catalogue_document()

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        """Cheap readiness probe: which faults exist, and which code is serving them."""
        state = code_state()
        return {
            "status": "ok",
            "faults": list(FAULT_MODES),
            "default_seed": DEFAULT_SEED,
            "window_seconds": DEFAULT_WINDOW_SECONDS,
            "first_window_start": DEFAULT_FIRST_WINDOW_START,
            # Hashes rather than versions: there is no version, and the question this
            # answers is "do the files on disk match the process you are talking to".
            "code": {
                "loaded": state.loaded,
                "current": state.current,
                "stale": state.is_stale,
                "changed": list(state.changed),
            },
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
