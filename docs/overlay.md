# The live overlay

A match feed in a browser where every published line is a claim that was checked
against the event stream, and the measurement that decided it is shown beside it.

```bash
python -m pitchframe --overlay            # http://127.0.0.1:8000
python -m pitchframe --overlay --port 8770 --seed 7
```

The overlay needs `fastapi` and `uvicorn`:

```bash
python -m pip install -r requirements.txt
```

Nothing else in the project does. `--overlay` reports the missing dependency and the
command that fixes it rather than raising an import error, so every other command
still runs on a bare interpreter.

## The two views

The same data, at two densities, and the difference is not cosmetic.

**Fan** — the scoreboard, the incidents, and the published commentary. No receipts, no
rail, and no rejected lines. This is the readable product: someone who wants the match
can follow it without ever meeting the machinery.

The withholding is the important part. A rejected claim is one the system refused to
publish, so showing it here would put a wrong statement in front of exactly the reader
it was withheld from. One honest line still reports what happened underneath, with a
**See the receipts** button that switches views.

**Analyst** — everything, plus the two things the product is actually about:

- the claim being checked right now, with **claimed** and **measured** side by side
  and a stamp: `VERIFIED`, `BLOCKED`, or `CORRECTED`;
- every rejected claim kept in the feed, struck through, next to the correction that
  replaced it.

Most products delete their errors. This one files them, because a system that shows
its own error rate is the only kind whose accuracy a viewer can check.

## What "live" means here

The overlay is a **replay of a deterministic script**, not a stream. `build_replay()`
walks a match window by window, runs the real propose/verify/retry loop for each one,
and records every frame up front; the browser then reveals them against a clock.

That buys three things:

| | |
|---|---|
| **A demo cannot fail on stage** | No model call, no socket and no clock race in the request path. The page renders from a document that already exists. |
| **Frames are honest about time** | A frame is built from only the events that had happened and only the windows the analyser had closed, so the narrator never sees its own future. |
| **The numbers are checkable** | Two runs with the same arguments produce identical bytes, so `tests/test_overlay.py` asserts on the whole document rather than a sample. |

One real second is one match minute at 1×. Windows close every five minutes, so a
claim lands every five seconds, and the transport lets you pause, scrub to any minute,
or run at 4× and 16×. For a recorded demo, `?fault=none` is the honest baseline and
`?fault=narrator-invert` plants one wrong direction per window so the catch is visible.

Every planted row is labelled `injected` in the payload and **planted fault** in the
page. An injected failure presented as a natural one would make the catch rate
unfalsifiable, because the failures being caught would have been planted by the same
code reporting the catch.

## Query parameters

| Parameter | Default | Meaning |
|---|---|---|
| `seed` | `7` | Which synthetic match. Everything downstream is a function of it. |
| `fault` | `none` | `none` for the honest baseline, `narrator-invert` to plant one wrong direction per window. |
| `view` | `fan` | `fan` or `analyst`. |
| `match_id` | `PF-001` | Identifier used in generated event ids. |
| `at` | replay start | Match second to open on — `3615` is 60:15. A link that carries one opens paused there. |
| `goal` | none | A build-up to open, named by the payload's own key: `` clock\|team\|scorer ``. |

```text
http://127.0.0.1:8770/?seed=7&fault=narrator-invert&view=analyst
```

The JSON behind any frame is at `/api/replay` with the same parameters, so the page
can be checked against the payload it came from. `/api/health` is the readiness probe,
and it also reports whether the process agrees with the files on disk. Only the first
four parameters reach the server; `at` and `goal` position the page and are read there.

### A link to the frame you saw

The URL is a description of the frame on screen, and it is kept current: the view,
the match second, and which build-up panel is open. It is written on every view change,
on a seek, when a panel opens or closes, and once a second while the replay plays — not
per frame, because a history call at 60 Hz is something some browsers refuse outright
(Safari throws past 100 calls in 30 seconds), and a second is the resolution the page
displays anyway.

Opening such a link always opens **paused** at the second it names. A link that resumed
playing would put the reader at a different minute on every load, which is the opposite
of what a bug report needs. The link carries the seed, the fault and the view as well as
the position, so the same match is rebuilt rather than a similar one.

The open panel is named by the payload's own key for a goal — `3615|home|Tomek`, the
clock, the side and the scorer the two lists already agree on. The page composes no key
of its own, so a link cannot name a goal that does not exist. If the seed or the fault is
edited by hand and the named goal is no longer in the replay, the panel simply stays
closed and the position is honoured.

`tests/test_overlay.py` reads this table and the parameters the page reads out of
`app.js`, and fails if the two disagree in either direction: a parameter documented but
ignored is a link that quietly does nothing, and one read but undocumented is a link
nobody can construct.

## How it is put together

```text
pitchframe/overlay/
  replay.py       The deterministic script. Standard library only.
  commentary.py   Turns a checked claim into a sentence. Standard library only.
  stale.py        Notices when the process has outlived the code. Standard library only.
  server.py       FastAPI shell. Serves what the modules above produced.
  static/         The page: index.html, styles.css, app.js
  static/slats.js The sea behind the page.
  static/rubber-segment.js  The view switch and the replay speed. A port.
  static/dock.js  The bar at the foot of the page. A port.
  static/motion.js  The springs and the one tween the ports need. Written here.
  static/vendor/  ogl, vendored for the sea — and the ports' upstream licence.
```

The split is by dependency, and it is the reason the overlay's behaviour is testable:
`replay` and `commentary` decide everything and need nothing, while `server` needs
FastAPI but decides nothing. Nothing in the request path measures, calls a model, or
holds state a refresh could lose.

### An old process says so, instead of answering anyway

The one way this server can lie without any of its code being wrong is by being old.
A running Python process keeps the modules it imported at startup for as long as it
lives, so after an edit it keeps serving the stream it loaded hours ago while the
repository, the tests and anyone reading the source describe something else. Nothing
in the request path can tell the difference: an out-of-date payload and a current one
have the same shape, the same keys and the same plausible numbers.

That is not hypothetical — it is how the goal build-up panel was once reported broken.
The generator had been fixed and the suite passed, but the server was still running the
old modules, so the panel kept drawing the ball back to the spot a shot was struck
from. The panel was right; the process was old; the fix was invisible to the person it
was made for.

`stale.py` hashes the package's Python sources at import — content, not timestamps —
and compares that with the same hash taken on disk at request time. When they differ,
`/api/replay` answers **503** naming the files that moved and the command that fixes
it, rather than a plausible document. The page already prints the server's `detail`
when a request fails, so the reason reaches the reader.

```json
{
  "loaded":  "…", "current": "…",
  "stale":   true,
  "changed": ["pitchframe/generator.py"]
}
```

Two deliberate limits. Static assets are exempt, because this process reads them from
disk on every request rather than importing them once — hashing them would turn a saved
stylesheet into a downtime. They can still go stale one layer further out, in the
browser, which is why every response carries `cache-control: no-store`: a browser
holding yesterday's `app.js` hides a fix just as completely, and unlike a stale process
it never fails anything. And only `*.py` is hashed, so touching a file without editing
it counts as unchanged. `/api/health` reports both hashes whether or not they match,
because there is no version number here and the useful question is not which build this
is but whether the process and the files agree.

### The commentary is a renderer, not a writer

The rule that the narrative layer may not assert free text still holds. `compose()`
receives a claim that arithmetic has already decided and chooses words for a fact it
did not determine:

- the direction comes from the verdict, never from the template;
- the glosses are fixed per `(metric, direction)`, so the same movement always reads
  the same way and no phrasing can drift into claiming more than the metric means;
- they are written to the definitions in `stats.py`, not to what sounds good.
  `chaos_index` measures the irregularity of the gaps between events, so "the game has
  opened up" is fair and "they are passing better" is not;
- quoting a real incident from the window is additive — *the spell also brought a
  shot from* — never causal. Nothing in the event stream says what caused a movement,
  so the commentary is not allowed to say either.

An unrecognised metric gets the bare measurement (`momentum moved up.`) rather than a
guess, and a test asserts that every metric in the registry has a gloss in both
directions so a new one cannot silently fall through.

### The goal build-up panel

Clicking a goal on the timeline opens the move that produced it, drawn from the
payload's own coordinates. Nothing about it is fetched separately: the highlights
ride along in the same JSON document, computed by `pitchframe/highlights.py`, which
is pure and standard-library only like the rest of the overlay's logic.

```text
pitchframe/highlights.py
  extract_build_up()   Walk a possession backwards from the goal to its first touch.
  summarise_move()     Passes, carries, ball travel, duration, starting third.
  build_highlights()   One GoalHighlight per goal, in clock order.
```

Four decisions are worth knowing about, because each is visible on screen:

- **Recorded carries, and inferred ones drawn differently.** The generator emits
  `carry` events, so a carry normally arrives with a player, a route and a speed, and
  is drawn solid and heavier than a pass. The inference is kept for the streams that
  have no such event — an imported feed, or a hand-built fixture — and derives a carry
  where one player takes two touches and the ball moved more than a threshold between
  them. Those steps carry `derived: true` all the way into the payload, are drawn
  dashed and amber, and add the *derived carries* label to the panel. It cannot fire on
  our own stream at all: every event begins exactly where the previous one ended, so
  there is no unexplained ball movement to find — and unexplained movement is what a
  carry is. A step that was guessed must never look like one that was recorded.
- **The goal is the end of the move, not a touch inside it.** The event stream usually
  records a shot and the goal it produced, describing one strike from both ends. Only
  the shot is a step; taking both would report two finishes and read the gap between
  the shot's target and the shot's own starting point as travel.
- **The walk stops at three boundaries:** a different `possession_id`, the tackle that
  won the ball (recorded against the *other* side while still carrying the possession
  it ended), and kick-off or half-time.
- **The pitch is 105 × 68 in plain SVG.** Coordinates arrive normalised 0–100 on both
  axes, so each axis is scaled onto a real pitch rather than squeezed into a square,
  and every stroke uses `vector-effect: non-scaling-stroke` so a line is the same
  weight on a phone as on a desktop. No canvas and no library *for the pitch*: it is SVG
  drawn from the payload, and the only library on the page is the vendored one behind it.

The panel labels its own provenance from the payload rather than from the view —
*synthetic match*, plus *derived carries* when a step was inferred — because a view
that could compose a label could also drop one. It never describes the move as
footage or as tracking data, and the ball travel is reported in the schema's own
normalised units with a note saying so, rather than in metres it was not measured in.

`prefers-reduced-motion` is honoured by not starting the animation on its own; the
Replay button still plays, because pressing it is an explicit request.

### The ported controls

Four of the surfaces on this page are ports of React Bits components: the sea
(`static/slats.js`, MicroSlats), the Fan/Analyst switch with the 1×/4×/16× group
(`static/rubber-segment.js`, RubberSegment), the bar at the foot of the viewport
(`static/dock.js`, Dock), and the match-minutes seek
(`static/wake-slider.js`, WakeSlider). They were ported for the same reason each time — this
project has no React, no package manager and no build step, so a component that arrives as
JSX cannot be dropped in. The React half of each was a lifecycle, which the ports keep:
`createRubberSegment(host, options)`, `createDock(host, options)` and
`createWakeSlider(host, options)` return `{ set, destroy }`, and everything under those is
the component's own logic. For the segmented control that is the stretch between slots, the
squash on landing, the rubber band at the ends of the track and the flick projection; for
the dock it is the magnification spring, the hop on a click, the long press, the flick, and
the menu geometry with its tail; for the slider it is the snapping grid, the wake's reach
and its skew behind the handle, and the two springs the wake is drawn between.

`motion`, the one dependency these components declare, is not imported either.
`static/motion.js` is the part of it they use — one spring and one cubic-bezier tween,
stepped per frame rather than handed to the Web Animations API, because the components read
a number while it is still travelling and hand a spring its velocity when a finger is
lifted. The slider reads the handle's velocity from the integration itself, which is the
number `motion` reports as `useVelocity`, so the wake is drawn from the spring rather than
from a difference of two samples. The dock keeps its own loop rather than using it: it
integrates every tile together and rescales the row when the bar itself changes size, which
is one simulation rather than one value per tile.

What each control *means* here is the page's decision, and there is one control per
decision:

| | |
|---|---|
| **The view switch** | Fan or Analyst, and the only control for it. *See the receipts* sets the same value, as a jump rather than a slide: a value arriving from outside the control is not a gesture. |
| **The replay speed** | 1×, 4× or 16×, with the rate note beneath it. The in-panel 0.5×/1× stays a plain pressed-button group on purpose: it lives inside a closed `<dialog>`, which measures as nothing until it opens, and a thumb measured at zero parks itself in a corner. |
| **The dock** | Play/pause, with the component's indicator dot marking a running replay; the speed as a checked menu; the two views; the goals reached so far; copy-link; and the keyboard list. It floats over the page rather than living in it, and it duplicates the transport deliberately — both drive `setView`/`setSpeed`/`setPlaying`, so they cannot disagree. |
| **The seek** | The clock, as a minute of the match. The wake is the movement — it lifts where the handle is going and trails behind it — so a drag across the page reads as travel through the match rather than as a position. It replaces the range input rather than joining it: two controls for one position can disagree with nothing on screen to say which one the page believes. |

Three decisions inside the dock are worth naming, because each is a place a floating control
goes wrong:

- **It offers only what the replay has reached.** The build-up menu lists the goals the
  scoreboard has already counted and its badge is that count. A menu naming a goal that has
  not happened yet would spoil the replay the page exists to show — the rule that keeps
  rejected rows out of the fan view, applied to a menu.
- **The rail it lives in is inert.** The layer covers the whole viewport and the bar is a
  fraction of it; `pointer-events: none` on the layer, with the bar taking them back, is
  what stops a fixed strip over the page from swallowing clicks meant for the page.
- **Its colours come from this page.** Panel, tiles, tooltip and menu take `--turf`,
  `--chalk`, `--flint` and `--rule-hard`, and the menu highlight and badge use the page's
  selected treatment — chalk fill, night text — rather than the component's own accent.
  Neither control uses green or vermilion, which mean published and caught.

One thing the dock does leaves no mark on the page at all: copying a link. So it says so in
the tile's own label — *Copied*, or *Copy failed* — which is on screen whenever a pointer is
over the tile, which is the situation a copy was asked for in. The async clipboard is raced
against a short deadline and the synchronous path takes over, because `writeText` can stay
pending for ever in a window that never takes focus, and a label that never changes reads as
a control that did nothing.

The bar is described by one function from the page's state and rebuilt whenever that state
changes, rather than kept in step item by item. Both ports update in place when the items
keep their keys, so a bar under the pointer is not reset every time the replay crosses a
goal. `prefers-reduced-motion` is honoured by both, and watched rather than sampled once:
the thumb jumps instead of travelling, the swell settles where it is, and the menus fade
rather than pop.

The seek carries the most weight of the four, because it is the one a reader drags, and
four decisions are what make that possible. Its range and its grid come from the payload —
the lowest and highest clock the analysis reached, and one minute of the match — so a match
analysed differently seeks differently rather than into a position the replay does not have.
A seek forwards is an *append*: the rows already on screen are exactly the rows that clock
has, so only the new ones are built, and the whole batch goes in as one write because every
row animates itself in and fifty separate inserts are fifty rounds of layout. Only a seek
backwards wipes and rebuilds, which is the expensive direction and the only one that has to
be. And the reaction is queued as a task rather than on a frame: a window that is not being
composited draws no frames at all — this page can be left in the background — so a seek that
only ever ran on a frame would move the handle and leave the page where it was. A task still
coalesces a burst of pointer commits into one answer, which is what the cost was about.

The fourth is about width, and it is the one that made the control invisible. Bars are laid
out with a gap between them, so a track whose host is sized by its own contents is a row of
gaps: the transport lived in the clock's `auto` column, which measured 492px with the seek
inside it, and after play, the speed group and the rate note the seek was handed 151px —
where 63 two-pixel gaps are 126px and the 64 bars themselves measure zero. The control still
measured, focused, tabbed and announced itself, and drew nothing. The row now spans the whole
match bar (`grid-column: 1 / -1`), so the track is 967px at a desktop width and each bar is
about 13px: the seek reads as the timeline it is, filled to the minute the replay stands at.

One difference from the sea is worth stating plainly, because a reader comparing the two
would otherwise assume the same isolation. The sea is its own module and fails quietly: no
WebGL2 means no sea and the page is otherwise untouched. The three controls are imported by
`app.js`, so a module that cannot be served takes the replay down with it rather than
degrading to buttons nothing happens behind. That is the trade: a control that is silently absent would
be a page that looks fine and does nothing, and the failure that produces it — a module
served with the wrong content type, or an import whose path is wrong — is invisible on
screen and in the file that made it.

### The keyboard

The replay is drivable without a pointer, and the keys are written down in one place: a
table at the top of `app.js`. Two things read it — the listener at the foot of that file,
which answers a key, and the dock's menus, which print the key beside a row — so a column
cannot name a key that does nothing, and a key cannot be one nobody can find. One of the
dock's tiles, labelled *Keyboard*, is that table drawn as a menu: every row names its key,
and pressing the row does the thing, so the reference is usable before it has been learned.

| Key | What it does | The detail that matters |
|---|---|---|
| `Space` | Play / pause | At the end it starts again rather than doing nothing, which is the rule the transport, the dock's tile and this key all share. |
| `←` `→` | Back / forward one window | 300 match seconds, and the step comes from `window_seconds` in the payload rather than from a constant here. It pauses: a reader steering somewhere specific has said they have finished watching for now. |
| `1` `2` `3` | 1×, 4×, 16× | The digits name positions rather than numbers, because 16× cannot be typed as a digit. The speed menu's own rows say which key reaches each one. |
| `F` `A` | Fan view, Analyst view | The same two values the switch sets, so the menu, the tile and the URL still cannot disagree. |

Four rules the keys obey, each because the alternative breaks something:

- **A modifier makes the key the browser's.** `F` is the Fan view and Cmd/Ctrl+F is still
  find-on-page; nothing here fires with Ctrl, Cmd or Alt held.
- **A focused control keeps its own keys.** Space activates the focused button — the dock's
  tiles included — and the arrows step a slider that is being tabbed through.
- **An open surface owns them.** While a build-up panel is up the keys are silent: the panel
  is a modal, and a key that changed the speed or the view behind it would move something
  the reader cannot see. The same goes while a dock menu is open, whose rows would otherwise
  go on offering a speed that had already changed under them.
- **Nothing else is claimed.** A key outside the table is not handled at all.

**Licence.** React Bits is MIT + Commons Clause License Condition v1.0, Copyright (c) 2026
David Haz. The notice travels with the ports in `static/vendor/react-bits-LICENSE.md`, and
the clause names *a ported version* explicitly: these files ship as part of this application
and are not redistributed as components on their own. `ogl` (Unlicense) is vendored for the
sea. Both are recorded in [prior-art.md](prior-art.md), which is where this project keeps
what it took and under what terms.

### Three type roles, allocated by meaning

Oswald for the scoreboard, Archivo for the sentences a person reads, JetBrains Mono
for **every number a machine produced**. You can see at a glance which parts of the
page came out of arithmetic. Green and vermilion are reserved for published and
caught; the metric table shows direction with an arrow instead, because a metric
moving down is not a failure and painting it red would say that it was.

### The sea behind the page

The page has a background: a field of slats that swells, catches light on its crests,
and is stirred by the cursor. It is a port of the **MicroSlats** component (React Bits)
to a module with no framework — `static/slats.js` — because this project has no React,
no package manager and no build step. The React half of that component was only a
lifecycle, and a lifecycle is what the port keeps: `createSlats(container, options)`
returns `set()` and `destroy()`, and everything under those is the component's own code,
shaders included, with one deliberate difference noted in the file — `set()` merges
rather than resets, so `set({speed: 0})` pauses the sea instead of also reverting the
slat size to the preset.

Its dependency is the one vendored *package* here; the ports above are other people's code
too, carried under their own licences. `ogl` is vendored into `static/vendor/ogl.mjs` and
tree-shaken to the six classes the effect uses, because the alternative — a CDN import —
would make the overlay's demo depend on a network request, and this page is meant to survive
a venue with no wifi. The file carries
its own regeneration command and its licence, and it is the only file here that is
generated rather than written.

Four decisions are worth knowing, because each is a way the background could have gone
wrong:

- **It is inert.** `pointer-events: none` in the stylesheet, so it can never take a click
  from a control — and the fluid still follows the cursor, because the component listens on
  `window` rather than on the canvas.
- **Colour comes from the palette**, not from a literal in the canvas code: `--slats` and
  `--slats-glint` sit beside the verdict colours so that scenery cannot drift into the
  green and vermilion that mean published and caught.
- **`prefers-reduced-motion` is watched, not sampled once.** This page already stops its
  own animation for that setting, and the sea freezes with it; a reader who turns it on
  while the water is rolling does not have to reload.
- **It fails quietly.** No WebGL2 means no sea, no error and no message: the container
  stays empty and the page is exactly what it was. The module is loaded separately from
  the replay, so a WebGL failure cannot take the replay down with it.

It is also cheap. The field is simulated at slat-grid resolution — a few thousand texels —
and only one full-screen pass is shaded per frame; the loop stops entirely when nothing is
moving and starts again when the cursor stirs it. Measured on this machine at 1440×900,
frame pacing is identical with the sea running and paused (median 20.8 ms, worst 21.0 ms
against 21.3 ms), so the cost is below what the measurement can resolve. That is a
measurement on one machine, not a guarantee; `pixelBudget` is the knob if a slower GPU
ever needs one.

## Limits, stated plainly

- **No real feed.** The match is synthetic, from a seed. That is a licensing and
  reproducibility decision, not a prototype shortcut — see the README.
- **The default narrator is scripted.** `provider` in the payload reports which model
  tier is live and why, and the page shows that banner. With no Azure subscription it
  reads *scripted replies — no model in use*, which is what stops a scripted reply
  being mistaken for an inference.
- **No websockets.** A replay over one JSON document is more robust for a
  demonstration than a stream, and it makes the whole thing testable without a
  browser. A live feed would replace `replay.py` and leave the rest alone.
- **No tracking data.** A carry is generated as a recorded event with a carrier and a
  speed, not measured from a positional feed, and the inference path exists for feeds
  that lack the event. A real carry detector needs tracking data we do not have.
- **Build-ups are short.** Possessions in the generator change hands often, so a move
  of three to six touches is typical and a nine-pass move is rare. That is a property
  of the synthetic match, not a limit of the extraction.
- **Four surfaces here are ports, not ours.** The sea, the view switch with the replay
  speed, the dock, and the match-minutes seek are ports of React Bits components, and `ogl`
  is vendored for the sea. They are reviewed as dependencies rather than maintained as ours, their notices
  ship beside them, and none of them decides anything: every number on the page still
  comes from the payload.
