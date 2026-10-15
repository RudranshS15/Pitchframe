"""The live overlay: a match feed where every line is a checked claim.

Two halves, deliberately separated by their dependencies:

* :mod:`pitchframe.overlay.replay` and :mod:`pitchframe.overlay.commentary` are
  standard library only. They decide what the overlay shows, and they are what the
  tests pin.
* :mod:`pitchframe.overlay.server` needs FastAPI and uvicorn, and only ever *serves*
  what those two produced. Nothing in the request path decides anything.

That split is why the overlay's behaviour can be tested on a bare interpreter even
though a browser cannot.
"""

from __future__ import annotations

__all__ = ["build_replay", "compose"]

from .commentary import compose
from .replay import build_replay
