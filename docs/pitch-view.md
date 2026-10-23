# The pitch view

A live match pitch, the kind Sofascore shows during a broadcast: every player and
the ball on a drawn field, updating through the match. It is a self-contained page
under `pitchframe/overlay/static/pitch/` and carries **no new dependency and no
build step** — plain ES modules over a canvas, in keeping with the rest of the
overlay.

Open the overlay and visit

```
http://127.0.0.1:8000/static/pitch/pitch.html
```

You can also reach it from the match feed: the match bar carries a **live pitch** chip
that opens the view at the seed the payload was actually built from (not the one the
URL asked for, so the picture cannot disagree with the match on screen), and beside it
a small **on / off** switch — off hides the chip and remembers the choice in
localStorage; on brings it back. The switch itself never hides, so turning the chip
back on is always one click away.

(default replay of a 90-minute recording; add `?quick=1` for a 12-minute one, or
`?seed=99` for a different match).

## The data contract

A frame looks like this; timestamps are the **match clock** in milliseconds
(continuous through the half-time break — the break is not modelled, and
`periodFor` labels 1H / 2H / ET1 / ET2 / FT):

```
{
  t: 1_234_567,                                   // match-clock ms
  period: "1H" | "2H" | "ET1" | "ET2" | "FT",
  ball: { x, y, z? },                             // normalised 0–1 (x along, y across)
  players: [{ id, teamId: "home"|"away", shirtNumber, name,
              position, isGoalkeeper, x, y,
              speed?, distance? }]                // optional; km/h and running km
}
```

Missing, `null` and scarce fields must not crash the view: a player absent from a
frame holds their last position (live feeds hold for 2 s, then fade and raise the
**live data delayed** banner; recordings treat an eternal absence as a
substitution and fade over 0.6 s). Unknown fields are ignored, unknown ids are
drawn, nothing throws.

### Where the positions come from

The upstream generator (`pitchframe/generator.py`) produces an **event** stream —
passes, shots, saves with a minute and a player — not tracking data. Positions are
synthesised by `pitch-mock.mjs`: 22 players hold formation slots, are pulled toward
the ball by role-dependent weights, wobble with seeded noise and occasionally make a
committed run; the ball is possessed, walks toward a target in the possessor's
attacking half and changes hands over time. It is deterministic per seed, so a
replay shows the same match twice, and a test can pin any of it. Substitutions swap
a formation slot's player at fixed minutes — the outgoing id never appears again,
the incoming one starts where they stood.

Two layers share all of it through `pitch-sources.mjs`:

- **`createReplaySource(recording)`** — the whole match at once; play/pause,
  1×/2×/4× and a scrubber are the page's business, and it is the default mode,
  which is why the page can open offline.
- **`createLiveSource(recording, …)`** — the same recording streamed at 1 Hz,
  with an armed `injectDrop(seconds)`/auto drop. A drop does exactly what the
  spec's worst case does: markers drift, fade, the banner appears; the
  reconnection is a full snapshot that **glides in** rather than snapping,
  because every drawn step is rate-limited. No page reload is involved in any
  of it — that is the recovery the contract asks for.

## How it renders

- Everything per frame is a canvas op: markers, chips content, trail and pitch
  lines — no DOM re-render, so 23 moving entities stay inside the frame budget
  on a phone. `devicePixelRatio` is honoured, drawing scales to any container
  width (`ResizeObserver`).
- The field itself is midnight black (violet-tinged turf stripes, pale violet
  markings), the ball's two-second trail is sun yellow, and the delayed banner
  is sun yellow with midnight text — the three colours the view is given, each
  doing one job: violet asserts, yellow attends, and the field is the dark
  both read against.
- **Markers**: team-coloured circles, shirt number inside, short name below.
  Goalkeepers are a white disc with a team-coloured ring, so a colour-blind
  reader can still tell them apart. The default palette is **midnight** — electric
  violet against sun yellow on the view's midnight-black field, where every pair
  sits at opposite ends of the lightness range and keeps its distance at any
  zoom; the chip row cycles through it, the colour-blind-safe Okabe–Ito pair
  (blue / orange) and the classic red / blue pair, which does not survive the
  common colour-vision deficiencies.
- **Ball**: a small white marker that scales with `z` when the feed carries
  height, with a fading trail of the last 2 seconds of match clock.
- **Chips** above the pitch pick what each marker *is*: numbers, names,
  positions, speed, distance — or nothing. Only the chips the feed actually
  supports are offered: a source without `speed`/`distance` leaves those
  buttons out rather than disabling them.
- **Tapping or hovering a player** opens a card with name, team, position,
  shirt and the current speed/distance; the player nearest the ball always
  wears a highlight ring.
- The pitch is drawn to scale (105 × 68 m) with outline, halfway line, centre
  circle, both penalty areas, the six-yard boxes, spots, arcs and goals. Auto
  orientation flips the view at half-time (a 180° rotation — never a mirror, so
  numbers and names stay upright); the flip chip can pin it to either direction.

## The motion rules, in one line each

- **Interpolate:** the feed updates at 1 Hz, the screen at ~60 Hz; the playhead
  runs between the two surrounding frames and never overshoots them.
- **Never snap:** every drawn step is rate-limited (`glideTowards`), so a late
  snapshot or a scrubber jump is walked in (~0.6 s), not teleported.
- **Stale:** a live feed that stops publishing keeps drifting for 2 s (one
  previous interval's velocity, bounded), then markers fade over 0.8 s and the
  banner appears. Nothing is removed mid-match except through a substitution.
- **Substitution:** the outgoing marker fades out over 0.6 s at the position it
  last held; the incoming one fades in over the same window from where the
  player it replaced stood.

## The twelve-second boundary case we named

The view's stale rule runs on **wall clock**, but the "delayed" state the reader
sees is about **match clock** — the two are the same only at 1× playback, so
replay playback is exempt from staleness by construction (a paused recording has
no connection to go stale).

## Tests

Pure modules (mapping, interpolation, hold/fade, flip, substitutions,
determinism) are unit-tested with Node's built-in runner, no test framework
added:

```
node --test "tests/js/*.test.mjs"
```

The Python suite is untouched by this feature and still passes as before. In CI the
suite runs as a step of the `browser` job in [.github/workflows/tests.yml](../.github/workflows/tests.yml),
which is the one that already brings Node, pinned to 24.

## Known limitations

- The mock tracker *simulates* plausible movement; it does not derive positions
  from the real event stream. A feed that reports tracking data can drop in
  through the same `setFrames` route with no changes to the view.
- Half-time is instantaneous (the break is not modelled) and the mock match ends
  at 90 minutes; extra-time periods are label-accurate but never generated.
- `speed` is smoothed with an EMA over samples, so instantaneous bursts are
  damped — fine for a chip, wrong for coaching analysis.
- No server changes were made: the page is served from `/static` like every
  other asset, which also means there is no seed-validation or catalogue link
  for it — a mistyped seed still shows a perfectly valid empty-looking 0:0
  first frame rather than an error.
- The card is fixed to the bottom-right corner of the pitch rather than
  following the player across the field, which reads better on a phone than a
  card chasing its marker.
