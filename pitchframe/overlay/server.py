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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..agents.demo import FAULT_MODES
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
        return FileResponse(_STATIC_DIR / "index.html")

    @application.get("/api/replay")
    def replay(
        seed: int = 7,
        match_id: str = "PF-001",
        fault: str = "none",
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        max_frames: int | None = None,
    ) -> dict[str, Any]:
        """The whole script for one match: every frame, claim and verdict.

        ``fault=none`` is the honest baseline. ``fault=narrator-invert`` plants one
        wrong direction per window so the catch is visible; the planted rows carry
        ``injected: true`` and the page labels them, because a manufactured failure
        presented as a natural one would make the catch rate unfalsifiable.

        A 503 rather than a slightly wrong document when the code on disk has moved on:
        the page already surfaces the server's ``detail`` when a request fails, so an
        out-of-date process says so in place of quietly answering with old numbers.
        """
        _validate(seed, fault, window_seconds, max_frames)
        state = code_state()
        if state.is_stale:
            raise HTTPException(status_code=503, detail=state.message())
        return _replay_document(seed, match_id, fault, window_seconds, max_frames)

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        """Cheap readiness probe: which faults exist, and which code is serving them."""
        state = code_state()
        return {
            "status": "ok",
            "faults": list(FAULT_MODES),
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
    return application


#: Module-level instance for ``uvicorn pitchframe.overlay.server:app``.
app = create_app()
