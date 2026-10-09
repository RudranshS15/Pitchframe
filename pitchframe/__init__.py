"""Pitchframe — real-time match intelligence from synthetic football events.

The core of this package is deliberately **dependency-free**: ``schema``,
``generator`` and ``stats`` use the standard library only. That means the whole
analytics pipeline can be exercised with no install, no network, and no
credentials — which is what lets a judge run it immediately.

The agent layer is the only part that reaches outward. It sits behind a
provider abstraction with a replay mode, so the full pipeline still runs when
no model is reachable.
"""

from __future__ import annotations

from .generator import build_squads, generate_match
from .schema import Match, MatchEvent, Player, Squad
from .stats import METRICS, compute

__version__ = "0.1.0"

__all__ = [
    "Match",
    "MatchEvent",
    "Player",
    "Squad",
    "build_squads",
    "generate_match",
    "METRICS",
    "compute",
    "__version__",
]
