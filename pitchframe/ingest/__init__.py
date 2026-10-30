"""Importing real match data into the domain schema.

The generator is the project's fixture: deterministic matches for tests, offline runs and
the catalogue. Real matches arrive here, and everything downstream — the statistics, the
claim layer, the verifier, the replay — reads them through the same :class:`~pitchframe.schema.Match`,
so the analytics cannot tell the two apart and neither can a test.

Two adapters, for two kinds of match. StatsBomb's open data is the event feed: complete
matches with possession ids, lineups and shot locations, normalised by
:mod:`pitchframe.ingest.statsbomb` into the same schema. OpenLigaDB is the live goal feed,
read by :mod:`pitchframe.ingest.openliga`, and it deliberately does *not* produce a
:class:`~pitchframe.schema.Match`: with goals and a clock but no passes or lineups, it
supports a scoreboard and the claims goals can carry, not the full analytics. The two
capabilities stay apart instead of one pretending to be the other.
"""

from .statsbomb import (
    DEFAULT_COMPETITION,
    DEFAULT_SEASON,
    MatchMeta,
    SeasonMatch,
    StatsBombError,
    available,
    cache_dir,
    competitions,
    ensure,
    fetch,
    is_cached,
    load,
    parse,
    read_meta,
    season_matches,
)

__all__ = [
    "DEFAULT_COMPETITION",
    "DEFAULT_SEASON",
    "MatchMeta",
    "SeasonMatch",
    "StatsBombError",
    "available",
    "cache_dir",
    "competitions",
    "ensure",
    "fetch",
    "is_cached",
    "load",
    "parse",
    "read_meta",
    "season_matches",
]
