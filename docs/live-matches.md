# Live matches and the archive

The sphere on the front door is a curated catalogue: eighteen generated matches and any
recorded match already in the cache, chosen for what they contain. `/matches` is the other
way in — what is happening now, and everything in the archive — and it keeps the two
sources' capabilities apart instead of flattening them into one list.

```bash
python -m pitchframe --overlay
# then open http://127.0.0.1:8000/matches
```

## Live and recent

**Source.** [OpenLigaDB](https://www.openligadb.de), a free, keyless, community-maintained
feed. The project reads six competitions, by shortcut:

| Shortcut | Competition |
|---|---|
| `bl1` | Bundesliga |
| `bl2` | 2. Bundesliga |
| `bl3` | 3. Liga |
| `dfb` | DFB-Pokal |
| `pl` | Premier League |
| `ucl` | Champions League |

The whitelist is in `pitchframe/ingest/openliga.py`. It exists because the league is part
of an upstream URL: an endpoint that proxies whatever path a caller typed is a hole with a
football painted on it.

**What the feed records.** Goals, with a scorer, a minute and a running score; the
half-time and full-time result; the teams, the kick-off and the matchday. It updates while
a match is in play.

**What it does not record.** Passes, carries, shots that missed, pressure, lineups, or the
current match minute. Everything the page shows is built from the goal list, and the page
says so rather than filling the gaps:

- The **clock is derived**: elapsed wall time from kick-off, fifteen minutes allowed for
  the interval, stopped at 90. It is printed with a leading `~`.
- A **match with no event-level data** gets no pass count, no pressure index and no
  player ratings. Those panels belong to a full event stream.
- The only **players named** are the scorers, because they are the only players the feed
  names.

**Statuses.** A fixture is `scheduled` before kick-off, `live` while the feed has not
finished it and kick-off is within three hours, `finished` when the feed posts a result,
and `pending` when kick-off has passed, the result is missing, and the three-hour window
has closed. `pending` is the honest name for a match nobody should call live or final.

**Moments.** The page reports only claims the goal list supports:

- how often the lead changed hands, with every goal as evidence;
- a two-goal deficit that was erased;
- a burst of goals within ten minutes of each other;
- the latest goal, as an ordinal minute.

Each carries the minutes it was counted from. None of them reads possession, momentum or a
player's overall performance, because a goal feed cannot support those.

**Polling and cache.** One upstream request is shared by every reader for
`CACHE_TTL_SECONDS` (25 seconds) per competition, under `data/live/`. With a match in
play the page refreshes every thirty seconds; otherwise every sixty. A failed refresh
falls back to the last good response and marks it `stale`; a failed first fetch is a 502,
not an empty matchday that reads as "no matches today". The refresh control forces a
request.

**Attribution.** Data from OpenLigaDB, used under its public API terms.

## Recorded archive

**Source.** [StatsBomb open data](https://github.com/statsbomb/open-data), the same feed the
recorded-match adapter already reads.

- `GET /api/archive/competitions` groups `competitions.json` into one entry per
  competition, with its seasons. Twenty-four competitions at the time of writing, from the
  World Cup to the NWSL.
- `GET /api/archive/matches?competition=<id>&season=<id>` reads that season's index and
  returns every fixture with its date, teams, score (when played) and whether the match is
  already cached.
- Opening a row goes to `/match?statsbomb=<id>&competition=<c>&season=<s>`. The pair is
  what lets the replay endpoint download a match that is not cached yet, with the
  competition and season the archive named rather than the default pair. The first open
  takes a second or two; every later open reads the cache.

Both endpoints cache their index files under `data/statsbomb/`, which is git-ignored and
disposable. A season the archive does not have is a 404; a failed download is a 502.

## Endpoints

| Endpoint | What it returns |
|---|---|
| `GET /matches` | The page: live list, archive browser, detail panel. |
| `GET /api/live?league=bl1` | One competition's current matchday, with state, goals, moments and the league list. `refresh=true` bypasses the cache. |
| `GET /api/archive/competitions` | Every competition and season the archive publishes. |
| `GET /api/archive/matches?competition=43&season=3` | One season's fixtures, scores and cache state. |
| `GET /api/replay?statsbomb=8658&competition=43&season=3` | The verified replay, downloading the match on demand when the pair is present. |

## Command line

```bash
python -m pitchframe --live           # Bundesliga
python -m pitchframe --live pl        # Premier League
python -m pitchframe --live --json    # the same matchday, as JSON
```

`--live` prints the score, the status and the goals, and closes with the one honest
caveat: full events, claims and ratings are available for recorded matches.

## Tests

`tests/test_openliga.py` covers the adapter offline — every status, the derived clock at
its boundaries, own goals and penalties, the moments, and the cache (fresh, expired,
failed refresh with a fallback, unknown league). `tests/test_archive.py` covers the
catalogue grouping, ordering, paths and id validation. `tests/test_matches_api.py` covers
the HTTP surface, including the on-demand fetch from the archive.

No test in these files touches the network: the feed's clock is passed in, and the two
downloads are patched.
