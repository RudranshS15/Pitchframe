# The live overlay

A match feed in a browser where every published line is a claim that was checked
against the event stream, and the measurement that decided it is shown beside it.

```bash
python -m pitchframe --overlay            # http://127.0.0.1:8000
python -m pitchframe --overlay --port 8770 --seed 7
```

Two pages, and the front door is the choice rather than a choice:

| Path | What it is |
|---|---|
| `/` | The mode browser: every named match as a card on a sphere, each one a way in. |
| `/match` | The feed: one replay, every published line checked, with its own query string. |
| `/mode-browser` | Where the front door used to be. Redirects to `/`. |

The rest of this file is about `/match`, because it is the page with the controls, the
parameters and the panels. The catalogue's own page is in
[The mode browser](#the-mode-browser).

The overlay needs `fastapi` and `uvicorn`:

```bash
python -m pip install -r requirements.txt
```

Nothing else in the project does. `--overlay` reports the missing dependency and the
command that fixes it rather than raising an import error, so every other command
still runs on a bare interpreter.

### Opening it without a terminal

`open-overlay.cmd` in the repository root is for double-clicking. It finds a Python (this
checkout's `.venv` first), then asks the port what is already there, because a double-click
cannot be asked a question:

| What is on the port | What the launcher does |
|---|---|
| nothing | serves the overlay in this window and opens the catalogue when it answers |
| this overlay, current | opens the catalogue and exits at once, leaving that server alone |
| this overlay, out of date | stops and says so — the page would refuse every request |
| something else | stops and names `--port`, so a busy 8000 is a flag rather than a mystery |

`--open` is the flag underneath it: with `--overlay` it opens the catalogue in the default
browser once the readiness probe answers, on a thread, rather than before `uvicorn` starts.
Opening first is a race the browser loses — a tab that loads loopback before the socket is
listening shows a connection error, and the reader is left reloading a page whose server was
about to be ready.

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

*Which* match is not one of these: it is in the path, `/match/<key>`, because the match is
what the page **is** rather than a frame of it — see [Which match
opens](#which-match-opens). Everything below belongs to `/match/<key>` and describes the
frame on screen. The front door takes one — `fault`, which it passes on when it opens a
card — because a page with no replay of its own has no frame to describe.

| Parameter | Default | Meaning |
|---|---|---|
| `seed` | the scenario's seed (`28`) | Which synthetic match, by number rather than by name — for a seed no scenario owns, and for the links written before matches had names. A number that names a match is redirected to its name and its seed dropped. |
| `fault` | `none` | `none` for the honest baseline, `narrator-invert` to plant one wrong direction per window. |
| `view` | `fan` | `fan` or `analyst`. |
| `match_id` | `PF-001` | Identifier used in generated event ids. |
| `at` | replay start | Match second to open on — `3615` is 60:15. A link that carries one opens paused there. |
| `goal` | none | A build-up to open, named by the payload's own key: `` clock\|team\|scorer ``. |

```text
http://127.0.0.1:8770/match/matchday?fault=narrator-invert&view=analyst
http://127.0.0.1:8770/match?seed=99            # a match no scenario owns, by number
```

The JSON behind any frame is at `/api/replay` with the same parameters, so the page
can be checked against the payload it came from. `/api/health` is the readiness probe,
and it also reports whether the process agrees with the files on disk. The match reaches the
server as `scenario=<key>` or as `seed=<number>` — the page sends the one its address gave
it, and the endpoint refuses both at once rather than choosing. `fault` and `match_id`
travel with it; `view`, `at` and `goal` are the page's own reading of what is on screen.

### A link to the frame you saw

The URL is a description of the frame on screen, and it is kept current: the view,
the match second, and which build-up panel is open. It is written on every view change,
on a seek, when a panel opens or closes, and once a second while the replay plays — not
per frame, because a history call at 60 Hz is something some browsers refuse outright
(Safari throws past 100 calls in 30 seconds), and a second is the resolution the page
displays anyway.

Opening such a link always opens **paused** at the second it names. A link that resumed
playing would put the reader at a different minute on every load, which is the opposite
of what a bug report needs. The match travels in the path and the fault and the view in
the query, so the same match is rebuilt rather than a similar one — and a match with no
name carries its seed the page was actually built from, rather than the one it asked for,
because a link that inherited the server's default would open a different match from the
one it describes.

The open panel is named by the payload's own key for a goal — `3615|home|Tomek`, the
clock, the side and the scorer the two lists already agree on. The page composes no key
of its own, so a link cannot name a goal that does not exist. If the seed or the fault is
edited by hand and the named goal is no longer in the replay, the panel simply stays
closed and the position is honoured.

`tests/test_overlay.py` reads this table and the parameters the page reads out of
`app.js`, and fails if the two disagree in either direction: a parameter documented but
ignored is a link that quietly does nothing, and one read but undocumented is a link
nobody can construct.

### Which match opens

The default is not a seed picked off the top of the list. Seeds are named in
`pitchframe/scenarios.py`, and the one the overlay opens on is chosen for having
something to show: **matchday**, seed 28, five goals between the 12th and 78th minute,
four of them from moves of five touches or more. **quiet**, seed 7, is the single-goal
match this project's README numbers and several tests were written against — kept under
its own name, and no longer the thing a viewer meets first.

Every match has an address that says which one it is: `/match/matchday`, `/match/quiet`,
`/match/storm`. A name is what a reader can read off the URL bar, hand to somebody else, or
type back a week later; `?seed=28` is a number that could be anything, and the address is
the only part of a link most people ever look at. The name is the path because the match is
what the page is — the query string is already the frame, and a match is not a property of a
frame.

The name is also the only way in for a link that was written before this existed, and that
is handled rather than promised. `/match?seed=28` is a **308** to `/match/matchday` with the
rest of the query string carried across — the view, the second, the open build-up and the
fault describe the frame, so they travel; `seed` itself is dropped, because it is the thing
the name replaces and a URL holding both would be two descriptions of one match.
`?scenario=quiet` is redirected the same way and for the same reason, to `/match/quiet`:
the long form was answering with the *default's* address and dropping the name, which
opened a different match from the one asked for — the failure this whole section exists to
prevent. A bare `/match` goes to the default scenario's name and keeps the frame too, so
`/match?at=3615&view=analyst` lands on the same minute of the same match rather than on
minute zero in the fan view. A name and a number on one address are refused with the same
400 the replay endpoint gives them, because they can name different matches.

A seed no scenario owns — `?seed=99`, which is what `--seed 99` produces — has no name to
go to, so it is served as it always was: `--seed` and `?seed=` still take any number at
all, which is the promise that made the flag worth having. An unknown *name* in the path is
a 404 listing the names there are; an unknown name in `?scenario=` is served to the page,
which fetches the replay and shows the endpoint's 400 with that same list. Either way the
reader is told the name is wrong, rather than being handed a match they did not ask for.

Behind those two is the rest of the catalogue: eighteen named matches in total, fifteen
of them picked by scanning seeds for goals spread across the match with enough long
build-ups that the build-up panel has something to draw. Every one of them is recounted
from the generator by `tests/test_scenarios.py` — the goal count, the minutes those goals
fell in, and how many of the moves were long — so a note cannot go on describing a match
the generator no longer produces. The catalogue is the same list the mode browser draws
its cards from, which is the only reason it has to be right.

```bash
python -m pitchframe --scenarios          # every named match, with its contents
python -m pitchframe --scenario quiet     # report on the quiet one
python -m pitchframe --scenario storm     # or any of the other fifteen
python -m pitchframe --overlay --scenario quiet
```

A seed is still a seed: `--seed` and `?seed=` take any number and the page rebuilds
whatever it names, and the launcher prints the address of the match it is serving under
that match's own name rather than as a number. The scenario is only the *default*, and it
exists because of a report that the build-up panel was broken. It was not broken. It was pointed at seed 7,
whose only goal is in the 60th minute and whose build-up is a single shot — so a viewer
who opened the panel two thirds of the way through met a move with nothing to draw,
which from the outside looks the same as a fault. The default is a match chosen for its
contents, and the reason is written down rather than remembered.

### The mode browser

`/` is how a reader chooses between those matches instead of typing a seed — the front
door, with the feed under it at `/match/<key>`. One card per entry in the catalogue, on a
sphere: drag it to spin, step it with the arrow keys, press Enter or click the front card to
open the match. Each card navigates to its own `path`, which the catalogue endpoint composes
beside the entry it describes — so the addresses of this site are decided in one place, and
a card cannot offer one the server does not serve. The feed then loads exactly what it
always loads, one seed's replay, so browsing measures nothing new — it chooses what to
measure. Each page links to the other: the feed's masthead to the catalogue, the
catalogue's head to the feed.

The order matters and was once the other way round. Opening on a single unpicked match put
the choice one link away and made the other seventeen something to go looking for; opening
on the sphere puts the choice where a reader starts, and the feed becomes where a card
lands. A page whose whole subject is one match makes a poor front door for a catalogue of
eighteen.

The cards come from `/api/scenarios`, which serves the registry itself: the key, the
label, the note, and the facts a card draws. That endpoint exists so the page does not
carry a second copy of the catalogue — a list written into a script drifts from the
registry the first time the generator is retuned, and nothing on screen would say so. It
is cached for the process's life for the same reason a replay is: it costs about a second
to measure and cannot change while the process lives. A server whose code has moved on
refuses it with the same 503 the replay endpoint gives, because a stale catalogue names
seeds this process would build differently.

Three decisions in the page are worth naming:

- **Every card is drawn, not loaded.** This project ships no binary assets and works with
  no network, so a tile is drawn from its match's own numbers: the scoreline, the two
  counts, and the minutes the goals fell in as ticks along a rule. Those ticks are *not*
  split by side. Which side scored in the 20th minute is not something the catalogue says,
  and colouring them would be the page inventing a fact — the one thing every other file
  here refuses to do. The card beside the sphere names both teams in full, so a name cut to
  fit a tile is an abbreviation of a fact rather than a different one.
- **A browser without WebGL2 gets the catalogue, not an apology.** The sphere is the
  interface, so failing to draw it must not also fail to reach the matches: the component's
  fallback is a list of the same items, in the same order, each one a click from the same
  replay, and a status line says which of the two was built. The fallback is a real path in
  the port rather than a message, which is why it has tests of its own — and not just tests
  that it was built. The canvas goes with the context that would have drawn on it, because a
  context-less canvas still covers the stage and still takes clicks: the harness reproduced
  that, by asking a real browser what a click in the middle of the first row would land on
  and being told `infinite-menu__canvas`. That is the shape of every failure on this path —
  a page that looks like a list of matches and cannot be used as one.
- **The arrow keys are the page's, not the sphere's.** The port binds them on the document,
  guarded so that a key aimed at a control — a field, a button, the feed's own seek — stays
  that control's, which is how the feed's key handler has always been written. Binding them
  on the sphere's own element instead would leave the line this page prints, *Arrow keys
  step*, false until the reader happened to press Tab: nothing focuses the sphere on
  arrival, and the front door is the one page where the reader has not been told to do
  anything yet. The cost is that the arrows no longer scroll this page; Space and
  PageUp/PageDown still do.

`fault` is carried through from the browser's own query string, so a mode browser opened
on the honest baseline opens honest matches and one opened with the fault planted keeps it.

### Choosing a page

The way between the catalogue and the feed used to spend its time in three places, and
only one of them was the page being loaded. The nav held the click for a third of its
520ms burst — about 177ms — before following the address; the next document then had to be
fetched from cold; and the catalogue drew its sphere only after `document.fonts.ready`,
which waits for every face a CDN has been asked for. A slow font request therefore left
the front door as a headline above an empty stage, which reads as a broken page rather
than as a page with a different typeface.

Each of those is now bounded. The burst is 420ms and the address is followed after 18% of
it — about 76ms, one painted frame of the pill breaking — because the click has landed and
watching droplets land is not worth a fifth of a second. The destination is prefetched by
a `<link rel="prefetch">` added while the pointer is on the link or the keyboard is on it,
so the click is not the first request for the page. The catalogue waits at most 250ms for
faces and then draws in the fallback face rather than waiting on a network it does not
control; the words on the cards are DOM text and pick up the real face when it arrives.
And where the browser supports it, a 150ms cross-document view transition keeps the old
document on screen while the new one loads and crosses the two over, so the load reads as
a change of page rather than as a blank frame. Reduced motion turns the crossfade off and
navigates at once, which is what the nav already did for a keyboard activation.

## How it is put together

```text
pitchframe/scenarios.py  Which match opens by default, and why. Stdlib only.
pitchframe/ratings.py    Marks, the tallies behind them, and the award. Stdlib only.
pitchframe/overlay/
  replay.py       The deterministic script. Standard library only.
  commentary.py   Turns a checked claim into a sentence. Standard library only.
  stale.py        Notices when the process has outlived the code. Standard library only.
  server.py       FastAPI shell. Serves what the modules above produced.
  static/         The feed: match.html, styles.css, app.js
  static/index.html  The catalogue as a sphere. The front door, served at `/`.
  static/mode-browser.js  Its cards, drawn from /api/scenarios, and the way in.
  static/slats.js The sea behind the page.
  static/border-glow.js  The light on a panel's boundary. A port.
  static/rubber-segment.js  The view switch and the replay speed. A port.
  static/dock.js  The bar at the foot of the page. A port.
  static/wake-slider.js  The match-minutes seek. A port.
  static/sling-button.js  The build-up launcher's pad. A port.
  static/infinite-menu.js  The mode browser's sphere. A port.
  static/text-pressure.js  The wordmark and the board's two names. A port.
  static/gooey-nav.js  The nav between the two documents. A port.
  static/masthead.js  The wordmark and the nav, mounted for both pages.
  static/motion.js  The springs and the one tween the ports need. Written here.
  static/vendor/  ogl, vendored for the sea — and the ports' upstream licence.
tools/browser_check.py  Starts the overlay and a browser for the harness below.
tools/browser_check.mjs  Drives the tab over the DevTools protocol, and measures.
tests/test_browser_harness.py  What those measurements have to be. Gated.
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

The panel is opened from whichever surface is nearest, and there are three: the goal's
own row on the timeline, the dock's *Goal build-ups* menu, and the **launcher** — the
sling pad on the match bar, which opens the newest build-up the replay has reached and
is on the page from the first frame. The first two exist only once a goal has happened,
which is what made the feature look like a broken control on a quiet match: for sixty
minutes there was nothing on the page that said the panel existed at all. The launcher
says what it is waiting for, dims itself while there is nothing to open, and lights up
the moment a goal lands — and `G` reaches it from the keyboard.

A fourth way in is inside the panel itself. When the match has scored more than once, the
panel carries a chip per goal the replay has reached — the minute, the scorer and the side
— with the move on screen pressed. Choosing one opens that move without leaving the
panel, and focus is put back on the chip that was chosen rather than dropped to the top of
the dialog, so a keyboard reader can walk the row. With a single goal the row is hidden:
a control whose only action is to reopen the panel that is already open is not a control.
The row is built from the same reached-goal list as the launcher and the dock's menu, so it
cannot offer a goal the scoreboard has not scored yet.

### The team sheet

The squads panel is a team sheet: the eleven who started, then the five substitutes behind
them, each with a shirt number, a name and a position. The bench is carried in its own list
in the payload (`Squad.bench`, `bench` beside `squads` in the replay document) rather than
appended to the starting eleven. That is not tidiness: a substitute did not play, every
frame's ratings cover exactly the eleven, and a merged roster would invite the page to put
a mark against a player who never touched the ball.

Adding substitutes did not change a single match. The bench names are drawn from a *copy*
of the generator's random state, so the simulation that follows draws exactly the numbers
it drew before there was a bench — which is why the named scenarios still produce the same
goals at the same minutes. A bench that consumed five names from the shared stream would
have shifted every decision after the squads were built, and invalidated every recorded
figure in this document.

### The ratings and the award

`pitchframe/ratings.py` gives every player in both squads a mark out of ten, the
tallies behind it, and — once the match is over — the man of the match with a paragraph
about how they played. It is standard library only, like the rest of the overlay's
logic, and deterministic: the same match always produces the same numbers.

A rating is the one figure on this page that is a *judgement* rather than a
measurement, and what makes it publishable is that the judgement is written down:

- **The weights are constants in that file**, printed by the panel and recomputable by
  a reader from the tallies beside each name. No model, no learned parameter, no scale
  fitted to anything.
- **Every term is a counted thing** — a goal, a completed pass, a strike on target that
  produced no goal, a tackle that won the ball. Nothing reads a position, a reputation
  or a minute of the match as evidence about a player.
- **The marks move with the replay.** Each frame carries every player's mark *as of that
  frame's clock*, counted from the events up to that minute and nothing later, so a mark
  at 60 minutes is the mark the first hour produced rather than the one the match ended
  with. The panel's own window label is the frame's clock.
- **The award is withheld until full time.** The payload is the finished script, and the
  award is computed over the whole match and carried from the first frame — deliberately:
  no frame contains an award at all, so the page cannot read a partial one however it
  tries. The page is what waits, and it waits on `decided_at`, the clock the replay
  actually reaches. Naming a man of the match on the opening frame would spoil the replay
  exactly as printing the final score would.
- **The conclusion composes facts rather than causes.** It is a renderer in the same sense
  the commentary is: it may say a player scored twice and may not say that this is why the
  match was won. Phrasings are fixed per fact, so the same performance always reads the
  same way, and a test forbids the words that would imply otherwise.

The base is a **6.0**, and a player the stream never mentions sits on it. That is the
honest reading of a player a windowed event stream does not touch, and it is why the
panel prints the reason beside the number rather than the number alone: *1 goal ·
3 stops · 25 completed passes* is the evidence, and the mark is what the fixed weights
make of it.

`prefers-reduced-motion` is honoured by not starting the animation on its own; the
Replay button still plays, because pressing it is an explicit request.

### The panel edges

Every panel on both pages lights the boundary nearest the pointer, in a cone aimed at it —
`static/border-glow.js`, a port of BorderGlow. Seven panels carry it on the feed (`data-glow`
on the match bar, the ratings, the feed, the check, the ledger and the two rail panels) and
one on the catalogue: the sphere's stage.

Four things about it are this page's decisions rather than the component's, and each is in
the file's own header as well:

- **It adorns the panels; it does not wrap them.** Upstream renders a card and renders the
  caller's children inside it. These panels are markup app.js holds references into — the
  match bar's clock, the feed's list, the check's figures — so a port that re-parented them
  would be rebuilding the page to draw a border on it. This one adds the layers to the
  element it is handed and touches nothing else: no display, no padding, no border, no
  background. That is asserted, not just intended: `TestThePanelGlow` fails if the host rule
  in the stylesheet says anything but where the layers go, and the browser harness measures
  each panel's own box with the port's class taken off and put back.
- **The light is one light.** Upstream's component decides for itself that the pointer is
  near it, which on a page of one card is the whole question and on a page of eight panels
  is eight lamps: every panel the pointer happened to be inside would light to full
  strength, each would aim at a bearing measured from its own centre, and the gutters
  between them would be unlit seams. Here the page has **one** `pointermove` listener, every
  mounted panel registers with it, and a frame's worth of moves is coalesced into one batch
  of measurements. What a panel is given is a *falloff*: full while the pointer is inside
  it, fading with the distance from the pointer to its nearest edge, and out of the picture
  at 260px. That number is the whole of the reach, and it is written into the three layers'
  opacities as a single multiplier — so what a reader sees is one lamp travelling across the
  page, the boundary nearest the cursor brightest and its neighbours dimmer, rather than
  eight effects that happen to share a colour. The cone each panel draws is still its own,
  because only the panel knows where its own boundary is; what the light decides for all of
  them is where the pointer is.
- **The halo is drawn inside the boundary.** `--glow-padding` is passed as 0, so what lights
  is the 1px mesh ring and the wash behind it rather than upstream's 40px halo outside the
  card. The match bar and the check clip their contents (`overflow: hidden`), and a halo
  that appeared on five panels and was cut off on two would read as a fault. The grid's gap
  is a centimetre, which is no room for eight overlapping glows either.
- **Keyboard focus lights the same edge.** A pointer-only decoration is one half the readers
  never see, and every panel here has controls in it. On `focusin` the cone is aimed at the
  control's own box — measured, the same way the pointer's bearing is measured — and the
  proximity is taken to the component's full; on `focusout` it goes back to the pointer.

The colours are tokens in the stylesheet (`--glow-hsl` for the halo, `--glow-one` to
`--glow-three` for the mesh), not props, so that a colour is still decided in one place on
this page. They are violet, and deliberately so: green and vermilion mean published and
caught here, the two team colours are spoken for, and amber means live — violet is the one
family left that no number, no verdict and no team has claimed. The stylesheet section is
also inside the block the verdict-colour guard test reads.

The stage on the catalogue page is the one panel whose interior is not a flat colour (it is
a radial gradient), so the mask that keeps the mesh off a panel's inside is handed the token
that gradient ends at rather than being read off the element: an empty mask would let the
mesh paint a wedge across the sphere instead of a line along its edge. It is also the one
place the effect reads more quietly, because the sphere's canvas covers the stage's inside
and the halo behind it only shows through the gaps between tiles.

How any of that can be *checked* is the other half of this section, because none of it is
visible from a file. `tools/browser_check.py` starts the overlay on a port nothing else is
using and a browser with a profile of its own, `tools/browser_check.mjs` drives that tab
over the DevTools protocol, and `tests/test_browser_harness.py` says what the numbers have
to be. The driver writes almost nothing to the page: one probe takes the port's class off a
panel to measure the box underneath it and puts it back, and one mounts the pressure type on
an element of its own, off the layout, in a face the machine may or may not vary, and removes
it again — both are documented where they happen. It emulates
`prefers-reduced-motion` for the whole run: with the sea moving and the replay appending
rows, "did the pointer change this pixel" has no answer.

What it measures, on the analyst view where all seven panels are laid out at once — the
pointer three pixels inside the feed's left boundary, the replay paused:

- every panel's share of the light is the distance from the pointer to its nearest edge,
  within 0.02 of `1 - d/260`, and every lit panel's cone points at the pointer within a
  degree — one source, seven arcs of it;
- the panel the pointer is in is at 1.000, the ratings at 0.786 (56px away), the match bar at
  0.136 (225px), and the four panels past the reach at 0.000 with all three of their layers
  at opacity 0. Under the eight-lamps version the ratings and the match bar would both have
  been dark, because neither is hovered;
- in a 10px band inside that boundary about 95% of the pixels change and the mean channel
  value rises from 21 to 76, while the same rows at the *opposite* boundary move by about 10
  and the panel's interior by under 13. Those four figures move a percent or so between runs,
  because the page's scroll offset and the tile the sphere is showing both do — the feed
  scrolls as rows arrive and the sphere keeps turning — so what the harness asserts is that
  the boundary changes far more than the opposite boundary and the interior, not a digit. Two
  frames with nothing moved between them differ in **zero** pixels, in all three bands, and
  that one is asserted exactly: it is what makes every difference above the light and not the
  sea;
- with the pointer parked in the far corner, the feed's share is 0.000 and every layer of it
  is at 0; focusing a control inside it takes it back to 1.000 with the ring at full and the
  cone aimed at the button, and blurring it gives it back to the pointer.

The same run draws the front door: WebGL2 present, a canvas built, some five thousand
distinct colours in a band across the stage (an empty rectangle is one), six arrow presses
changing the front card from `/match/matchday` to `/match/switch`, and both of those
addresses followed inside
the page — the replay behind the key answers 200 with a seed, and the page at the address is
the feed rather than a redirect. Neither page logged an exception, a console error or a
failed request, the Google Fonts request being the one failure this project intends.

The run also asks the pressure type what it is on a face with no axes, because that is the
failure with no symptom: `font-variation-settings` written at a static face is accepted and
ignored, so the word is simply still and the module looks like it is working. Mounted on the
machine's own fallback faces it reported `flat` with nothing written and the word intact in
all three, and the assertion is the contract rather than the font — the axes it found, and
the state it reported, have to agree — so a machine whose faces all vary skips instead of
failing.

It is gated, because half a minute and a browser is not what `python -m unittest` should do
to somebody editing `claims.py`:

```bash
PITCHFRAME_BROWSER=1 python -m unittest tests.test_browser_harness
python tools/browser_check.py --headed --json report.json    # watch it, keep the numbers
```

Without the variable the class skips; with it and no browser installed, the skip says which
of the two is missing. A failure to drive the page is a failure rather than a skip, since the
run is the thing that was asked for.

CI runs it, in a job of its own: a third job in `.github/workflows/tests.yml` beside the bare
interpreter and the full suite, because this is the only one that can see the page and the
only one that needs a second runtime. The runner has no GPU, so it asks for software
rendering by name — `PITCHFRAME_BROWSER_ARGS="--disable-gpu --enable-unsafe-swiftshader
--use-angle=swiftshader"` — which is also why that knob exists rather than a flag hard-coded
here: Chrome 126 onward refuses to fall back to SwiftShader unless it is told to, and without
it the stage builds no canvas and the sphere half of the run would skip on every push.

The same knob is how the no-WebGL2 path is looked at by a browser rather than by a test that
pretends: `--browser-arg=--disable-webgl` turns the whole of WebGL off, and the run then
exercises the sea's empty container, the catalogue's list, the hit test above it and the skip
in place of the sphere's pixels. It is worth running by hand after touching either renderer,
because it is the path nothing else reaches.

### The analyst column

The analyst view's column — the check, the ledger, the metrics and the line-ups — is one
pinned column, and it is pinned as a whole for a reason that is easy to get wrong: a
`position: sticky` box covers whatever follows it *in its own column* as the page scrolls.
The rail used to stand after the pinned pair of check and ledger, so the metrics and the
line-ups slid up underneath them and could not be read at all — the pixels were there and
the pointer landed on the check panel above them. The rail lives inside the pin now, and
there is nothing after the pin left to cover.

The pin is also the column's own scrollport, which is the second half of the same report.
All four panels together are about 1,400 pixels — taller than a laptop window — so a pin
that merely took the column's height would strand its own bottom: the check and the ledger
would hold the top of the window while the metrics and the line-ups sat below the fold,
reachable only when the document ended. Bounded by the window — `100dvh`, with `100vh`
written first as the fallback for a browser that has not learned the newer unit — and given
`overflow-y: auto`, the pin covers nothing and hides nothing: the page scrolls the
commentary, the column scrolls itself, and every panel can be brought wholly into view. A
bounded flex column also
shrinks its children to fit it — the check panel went from 253 pixels to 39 the first time
the pin was given a height — so the panels are `flex: none`, there to be scrolled rather
than squeezed.

Nothing is cut by that scrollport. The glow port draws its three layers *inside* a panel's
boundary (`--glow-padding` is 0 and the layer is inset 0), so a panel leaving the top of the
column is clipped along its own edge exactly as the window clips it; the four pixels of
padding, taken back by the negative margin, are for the focus rings of the controls in
these panels, which are drawn outside their boxes. The harness reports a clipping ancestor
only where it could actually cut something — when `--glow-padding` is not 0 — and asserts
that it is 0 on every panel it measures.

A scrollport that only a pointer can scroll is half a control, so the column carries a tab
stop — written by `app.js` rather than into the markup, and only while it has something to
scroll. Chrome, Edge and Firefox make scroll containers focusable by themselves; Safari
does not, and a focusable box with nothing left to reach would be a tab stop that does
nothing. It is labelled a region and outlined on its own edge (inside the box, because the
box is the scrollport), and its scrollbar is drawn in the page's rule colour rather than the
operating system's grey. The wheel chains as usual: it scrolls the column until the column
ends and then scrolls the page, so a pointer over the column can never be trapped at the
bottom of the line-ups.

Both of those are checked in a browser, and the scroll one needs a page that has something
to pin: at the opening minute the feed is a few rows long and the document is too short for
the pin to reach the top of the window at all. So the harness walks the replay to full time
from the keyboard first, scrolls the page to where the pin takes over, walks the column's
own scroll at twenty-five positions, and asks of each panel whether it was wholly inside the
column at some point and whether the point inside it belonged to the panel or to something
drawn over it. Then it turns a real wheel over the column — `scrollTop` alone would keep
passing if something had started swallowing the gesture — and checks that the column moves,
and that a turn at its end moves the page. A panel on screen but underneath another is the
failure this exists for.

### The title

The catalogue always opened on an `<h1>`; the feed opened on a scoreboard, and the match's
name existed only in the tab and the footer — the right title for a file, the wrong one for
a page. The feed has a heading above the match bar now, in the same display face and at the
same scale as the catalogue's, and it carries the wordmark's treatment: the letters are
spans whose weight the pointer asks for (`static/text-pressure.js`), so the first line a
shared link opens on answers the pointer instead of sitting still.

The words are the replay's own — the same `home` and `away` the scoreboard is set from,
joined by the `v` the tab title already used — and they are written when the document
arrives. Until then, and if the replay never arrives, the heading says what the page is
("Verified match feed") rather than naming a match it has not been told about, which is the
same rule the board's own two names follow.

### The ported controls

Nine of the pieces on the two pages are ports of React Bits components: the sea
(`static/slats.js`, MicroSlats), the Fan/Analyst switch with the 1×/4×/16× group
(`static/rubber-segment.js`, RubberSegment), the bar at the foot of the viewport
(`static/dock.js`, Dock), the match-minutes seek (`static/wake-slider.js`, WakeSlider), the
build-up launcher's pad (`static/sling-button.js`, SlingButton), the catalogue's sphere
(`static/infinite-menu.js`, InfiniteMenu), the wordmark and the board's two team names
(`static/text-pressure.js`, TextPressure), the nav between the two documents
(`static/gooey-nav.js`, GooeyNav), and the panel glow (`static/border-glow.js`,
BorderGlow, which is not a control at all — see [the panel edges](#the-panel-edges)). They were ported for the same reason each time —
this project has no React, no package manager and no build step, so a component that
arrives as JSX cannot be dropped in. The React half of each was a lifecycle, which the
ports keep: `createRubberSegment(host, options)`, `createDock(host, options)`,
`createWakeSlider(host, options)`, `createSlingButton(host, options)`,
`createInfiniteMenu(host, options)`, `createTextPressure(host, options)` and
`createGooeyNav(host, options)` return `{ set, destroy }` — with `setItems` and
`active` beside them on the sphere and `setText` beside them on the pressure type, because
a catalogue and a match both arrive after the mount — and everything under those is the
component's own logic. For the segmented control that is the
stretch between slots, the squash on landing, the rubber band at the ends of the track and
the flick projection; for the dock it is the magnification spring, the hop on a click, the
long press, the flick, and the menu geometry with its tail; for the slider it is the
snapping grid, the wake's reach and its skew behind the handle, and the two springs the
wake is drawn between; for the sling it is the rubber-banding of the pull, the band drawn
as the two tangents from the well to the pad, the power arc, the aim of the pad's own
glyph, the arm-and-release rule, and the burst of particles thrown through the well along
the direction the pad was pulled; for the sphere it is the Fibonacci layout, the
quaternion drag with the tiles stretching behind it, the neighbour walk the arrows and the
autoplay both step along, the settle that decides when a tile has arrived, and the whole
sphere drawn in one instanced call from a texture atlas; for the glow it is the
proximity measure, the conic mask the pointer's bearing is fed into, the seven-stop mesh
drawn from three colours, the ladder of shadows the halo is made of, and the intro sweep;
for the pressure type it is the pressure field around the pointer, the soft follow, the
idle wave and pulse, the ripple a click sends through the letters, and the per-letter
settings written on every frame; and for the nav it is the pill as a signed distance field,
the droplets a selection breaks it into, the springs that carry them, the field traced into
a clip path so the label can be painted in the pill's colour, and the one draw call the goo
is made in. A selection follows its address at a share of that animation rather than at its
end — about 180ms at the default 520 — so the page changes while the droplets are still in
the air instead of after they have landed; a keyboard activation and a reader who has asked
for reduced motion leave at once. The sphere's three additions — drawn tiles,
a watched reduced-motion setting, and the fallback list — are in
[the mode browser](#the-mode-browser).

TextPressure and GooeyNav each add one thing this page needs rather than upstream's
default. The pressure type measures which variation axes the loaded face answers before it
writes any — two probe renders at opposite ends of each axis, compared — and a word in a
face with none is left exactly as written with `data-pressure="flat"` saying so, because
`font-variation-settings` on a face without the axis is accepted and ignored and there is
no error to notice. That is why the pages ask Google Fonts for Oswald as a weight *range*:
three static weights are a face with no axes, and every setting would be written into
nothing. The nav reads its items from the page's own links and leaves them as links, so the
way between the documents survives no script, no WebGL2 and no pointer; a browser without
WebGL2 still gets the moving pill, because the pill is the navigation's state and the goo
is the flourish. Its canvas is a little larger than the row so that droplets have somewhere
to fly, which is why the nav clips with `overflow-clip-margin` rather than letting an
oversized layer widen the document.

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
| **The launcher** | The build-up. Pulled or tapped, it opens the newest goal the replay has reached; disabled, and dimmed, until there is one. It is the surface that makes the panel reachable at all in the first half of a quiet match, and the pad is where the gesture belongs because a pull delivered *something* rather than toggling a state. |
| **The sphere** | *Which* match. It is the only control that leaves the page, and the only one whose value is not a setting but a choice — so it does not write a frame of the replay, it hands over a seed and lets the feed do what it always did with one. |
| **The nav** | *Which document.* Two pages, two jobs, and the one you are in is filled. It replaces the two one-way links each page used to carry: one control for the decision, marked by the page's own `aria-current` rather than by a script, and the only place a destination is written is the markup. |

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
WebGL2 means no sea and the page is otherwise untouched. The four controls are imported by
`app.js`, so a module that cannot be served takes the replay down with it rather than
degrading to buttons nothing happens behind. That is the trade: a control that is silently absent would
be a page that looks fine and does nothing, and the failure that produces it — a module
served with the wrong content type, or an import whose path is wrong — is invisible on
screen and in the file that made it.

### What a frame costs

The feed animates: the replay redraws the board every frame, the sea draws behind it, and
the seek's handle travels on a spring. What that costs was measured rather than assumed, by
driving the page in a browser of its own with background throttling off and recording long
tasks, frame intervals, a 100ms timer's lateness, a real press's delivery and a CPU profile
— eight seconds of playback, three runs, medians. Two things were wrong, and both were the
kind that look like a slow machine rather than a bug.

**The value loop could schedule a second chain.** `static/motion.js` clears its frame
handle on entry to a step and schedules the next frame at the end of it. A value is allowed
to move another value from its own `onChange` — the wake slider hands its handle's velocity
to a second spring on *every* step, which is how the wake is drawn from the speed the handle
is travelling at — and that lands back in `wake()` while the loop is mid-step. With the
handle already cleared, the re-entry scheduled a second chain on top of the one the step was
about to schedule: two became four, four became eight, and the page ran its controls
hundreds of times a second. A footer flag — the step in progress owns the next frame — is
the whole fix, cleared in a `finally` so a value that throws cannot leave the loop asleep.

**The board was rebuilt sixty times a second.** The payload moves in five-minute windows;
the clock ticks once a second; and `renderBoard` wrote the minute, the score, the share, the
ledger and the whole metrics table — twenty rows, four cells each — on every frame anyway.
Each part of the board is now written only when the thing it shows has changed, with the
minute and the scoreline compared as the strings they are written as and everything that
is a function of the analysed frame keyed on the frame itself. It is the rule the dock's
goal count and the launcher already followed, applied to the panel that needed it most. The
seek's track keeps its own version of the same rule: a bar is written when its height
changes, not once per frame for all sixty-four.

What those two cost, on this machine, over eight seconds of playback at 1×:

| | before | after |
|---|---|---|
| long tasks | 45 (2874ms) | 1 (81ms) |
| frame interval, median | 41.6ms | 20.8ms |
| a 100ms timer, median lateness | 40.5ms | 1.5ms |
| the same timer, 95th percentile | 189ms | 29ms |
| a real press, delivery, median | 14.3ms | 1.0ms |
| main thread busy | 83% | 20% |
| script time | 4630ms | 288ms |
| profile self time, the seek's paint | 3321ms | 19ms |

`python -m unittest` cannot see any of that: it is a property of two files and one browser.
The parts that can be checked without one are checked — a test reads `motion.js` for the
guard and the `finally`, and reads `app.js` for the frame-keyed render — and the numbers
above are kept here, because the next person to write a per-frame render should know what
the last one cost.

They are medians of three runs on a machine doing nothing else, and that last clause is
load-bearing: re-run while a browser and a dev server are busy and the task totals roughly
double, while a press still lands in one or two milliseconds and the timers stay in the
tens — none of them near the hundreds the loop bug produced. The comparison that means
something is the two columns against each other, taken minutes apart by the same script on
the same machine, not any one number against another afternoon's.

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
  the replay, so a WebGL failure cannot take the replay down with it. *Asking* is what makes
  that true rather than intended — `ogl` does not hand back a renderer with no context in it,
  it logs `unable to create webgl context` and then dereferences the missing context, so the
  constructor throws past any guard placed after it. The probe asks the same question with a
  canvas of its own and lets go of the answer, and the construction is guarded anyway for the
  browser that says yes to the probe and no to the real canvas.

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
- **Seven surfaces here are ports, not ours.** The sea, the view switch with the replay
  speed, the dock, the match-minutes seek, the build-up launcher's pad, the catalogue's
  sphere, and the light on a panel's boundary are ports of React Bits components, and `ogl`
  is vendored for the sea. They are reviewed as dependencies rather than maintained as ours,
  their notices ship beside them, and none of them decides anything: every number on the
  page still comes from the payload.
- **A rating is a formula, not a scouting report.** It is computed from this match's own
  event stream with fixed weights that are printed with it, so it is checkable, identical
  on every run, and says nothing about a player that the stream did not record. It is not
  a measure of ability and is not meant to be compared with ratings from anywhere else.
- **The browser harness needs a browser and Node.** The parts of this page that no file can
  describe are measured by `tools/browser_check.py`, which launches Chrome or Edge and drives
  it; on a machine with neither, `tests/test_browser_harness.py` skips and says which of the
  two is missing. Everything else in the suite still runs with nothing but Python, and the
  glow and the sphere are the only things that lose their check when the browser is gone.
