# Real matches

Pitchframe began on generated matches. It now runs on recorded ones too, and the recorded
path is the one to show someone who wants to know whether the analytics mean anything.

The adapter reads [StatsBomb's open data](https://github.com/statsbomb/open-data) — real
matches, complete event feeds, lineups and shot locations — and normalises them into the
same `Match` the generator produces. Nothing downstream branches on the source: the
statistics, the claim layer, the verifier, the ratings and the replay are the same code.

## Five commands

```bash
python -m pitchframe --fetch 8658                    # download the 2018 World Cup final
python -m pitchframe --statsbomb 8658                # its analytics report
python -m pitchframe --statsbomb 8658 --claims       # propose → verify → retry, on real events
python -m pitchframe --statsbomb 8658 --json         # the machine-readable summary
python -m pitchframe --overlay --statsbomb 8658      # the live replay, opening on it
```

`--statsbomb` downloads on first use, so `--fetch` is optional; it exists so the download can
happen before a demonstration rather than during one. `--overlay --statsbomb 8658` marks the
real match as the catalogue's default card and serves its replay at
`/match?statsbomb=8658`.

The default competition and season are the 2018 World Cup (`--competition 43 --season 3`).
A match from another season needs its own pair:

```bash
python -m pitchframe --fetch 3869685 --competition 43 --season 106   # the 2022 final
```

## Where the data goes

`data/statsbomb/<match_id>/` beside the repository, holding `match.json`, `events.json` and
`lineups.json`. `PITCHFRAME_DATA_DIR` overrides the location, which is what a container
should do. The whole of `data/` is ignored by git: the feeds are downloaded, not vendored.

`python -m pitchframe --scenarios` lists the generated matches and every recorded match in
the cache. The catalogue at `/` does the same, with recorded matches first.

## What the adapter maps

| Feed event | Becomes | Notes |
|---|---|---|
| `Pass` | `pass` | No `pass.outcome` is a completed pass. |
| `Carry` | `carry` | Speed is distance over the recorded `duration`. |
| `Shot` (outcome `Goal`) | `shot` + `goal` | Adjacent, on one clock — the pair the ratings panel already reads. |
| `Shot` (other outcomes) | `shot` | `Saved` counts as on target; a block, post or miss does not. |
| `Own Goal For` / `Own Goal Against` | `goal` | Credited to the side that benefited, with the scorer kept out of both squads' marks. |
| `Duel` of type `Tackle` | `tackle` | `Won`/`Success*` is a win; anything else is a loss. |
| `Starting XI`, `Half Start`, `Half End` | `kickoff`, `half_time`, `full_time` | Markers only; the teams' rosters come from the lineups file. |

Everything else — ball receipts, pressures (used as a signal, not emitted), dribbles,
clearances, fouls, substitutions — is dropped. The stream the analytics see is the one the
generator's own vocabulary describes.

## What is measured and what is derived

The feed records events, not continuous play, so three quantities are derived. None is
presented as measured:

- **Pressure.** `under_pressure` is a flag, not a magnitude, and a `Pressure` event records
  where a defender closed down. The adapter takes the larger of a `0.6` floor while the flag
  is set and a distance falloff from the nearest opposing pressure event in the same two
  seconds. That is the `0..1` scalar the metric registry expects, and it is a derivation.
- **Speed.** Only a carry has both ends of a movement and a duration, so only a carry gets a
  speed, capped at 40 km/h. Passes and shots have no measured speed and none is invented;
  the report's `peak_speed_kmh` therefore reads `0.0` for those two on a real match and is
  populated for carries.
- **Pass success.** The absence of `pass.outcome` means the pass was completed; every
  outcome the feed records is a failure.

Coordinates are rotated into the home team's attacking frame, so `(0, 0)` is the home
team's own goal line exactly as in the generator. The away side is rotated 180 degrees, not
mirrored.

The clock is match minutes with each half's stoppage folded into that half's end: first-half
stoppage sits at 45:00 and second-half stoppage at 90:00. The alternative — laying periods
end to end — shifts every second-half minute by the first half's stoppage, which turned a
58th-minute goal into the 61st. A goal in the 90+3rd therefore reads as 90'. Extra time is
mapped onto 90–105 and 105–120.

A goal recorded at 27:53 is placed at its recorded second and displays as 27' (the clock's
floor), where a broadcast would say 28'. The clock stays exact; only the display rounds
down.

## Known limits

- **`chaos_index` saturates on recorded feeds.** It is measured as the coefficient of
  variation of the gaps between events, and the generator spaces events far more evenly than
  real play bursts and pauses. A real match reads `1.0` — the top of the scale — whatever
  its actual rhythm. It is reported, but it is not yet a trustworthy reading on imported
  data. Every other metric transfers.
- **No tracking data.** Positions are event locations: where a pass started, where a carry
  ended, where a shot was struck. There is no off-ball movement, and the live pitch view is
  explicitly a mock tracker, so its chip is hidden on a recorded match rather than pointed
  at positions the feed does not contain.
- **Substitutes who played are in `players`.** A recorded team sheet is the eleven who
  started plus anyone who came on; the bench is only the unused. The ratings panel rates
  everyone who took part, which is what a real match needs and what a generated one never
  exercises.
- **Event ids are the feed's.** A goal shares its shot's index with a `-goal` suffix; a
  correction or a re-import produces the same ids, which keeps the replay reproducible.

## Attribution

The data is StatsBomb Open Data. Their terms ask that any published or distributed
research, analysis or insight based on it states the data source as StatsBomb and uses their
logo from the [media pack](https://statsbomb.com/media-pack/). The catalogue labels recorded
matches `STATSBOMB` and the match footer names the feed, so the attribution travels with the
thing that displays it.

Nothing here redistributes the data: the cache is downloaded on demand and git-ignored.
