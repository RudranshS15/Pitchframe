# Pitchframe

Real-time match intelligence from synthetic football events — turning a raw event
stream into explainable, personalised narratives.

> **Status: working end to end.** The generator, the analytics engine, the claim
> layer, the Narrator/Verifier loop, the live overlay, the goal build-up panel and
> the player ratings with the man of the match are implemented and tested (525
> tests; the ten that drive a real browser are behind a gate — see
> [Development](#development)). See [Roadmap](#roadmap) for what is still open.

---

## The problem

A football match produces thousands of discrete events — passes, tackles, shots,
turnovers, pressure moments. Broadcasts compress all of that into a scoreline and
a few replays. The interesting part, *why* a moment mattered, is the part that
usually goes missing.

Pitchframe takes the raw event stream and produces the layer above it:

1. **Ingest** events as they happen
2. **Interpret** them into meaningful statistics and patterns
3. **Explain** why a moment matters, not just that it happened
4. **Render** that insight as a live, synchronised overlay
5. **Personalise** it so an analyst and a casual fan get genuinely different views

## Quickstart

No dependencies, no network, no credentials. The core is standard library only:

```bash
python -m pitchframe --seed 7
```

```text
Northgate Athletic  1 - 0  Riverside United
match PF-001 | 1514 events

  possession          home 53.3%  away 46.7%
  pass accuracy       home 75.0%  away 74.0%
  pass difficulty     home 0.338  away 0.350
  pressure index      home 0.472  away 0.476
  chaos index         0.447   (0 = controlled, 1 = frantic)
  momentum            +42.4  (positive favours home)
  peak speed          pass 64.5 km/h | shot 115.7 km/h
  longest press. streak  home 6  away 5

  key moments
    60'  goal     home Tomek      pressure 0.46
    05'  shot     away Brenwell   pressure 0.83
    75'  shot     away Tomquist   pressure 0.76
    46'  shot     home Brendahl   pressure 0.74
    00'  shot     home Brendahl   pressure 0.73
```

Add `--json` for the machine-readable form that later stages consume:

```bash
python -m pitchframe --seed 7 --json
```

### Watch the verification working

Two commands show the agent layer. Neither needs a dependency, a network connection
or an Azure subscription:

```bash
python -m pitchframe --providers   # which model tiers are usable on this machine
python -m pitchframe --claims      # run the propose -> verify -> retry loop
python -m pitchframe --scenarios   # the named matches, and what is in them
```

A match is a seed, and a seed says nothing about what the match contains. The seeds
worth opening are named in `pitchframe/scenarios.py`, and `--scenario` picks one:

```text
 * matchday   seed 28   five-goal match — five goals between the 12th and 78th minute,
                        four of them from moves of five touches or more
   quiet      seed 7    one-goal match — the quiet match: one goal, in the 60th minute,
                        from an unassisted finish
```

**matchday** is what the overlay opens on. **quiet** is the seed this README's own
numbers come from, kept reachable under its own name — see
[which match opens](docs/overlay.md#which-match-opens) for why the first goal of the
ninety minutes decided it.

`--claims` deliberately injects one wrong claim so the loop is visible rather than
described. The injected fault is **labelled as injected**, because an unlabelled one
would make the whole verification story unfalsifiable:

```text
  ! FAULT INJECTED: narrator-invert on momentum

  REJECTED     W1200-1   momentum actually moved down (7.0000 -> 0.0000), but the claim says up
  corrected    W1200-1r  momentum moved down (7.0000 -> 0.0000) as claimed
  verified     W1200-2   longest_pressure_pass_streak moved up (1.0000 -> 2.0000) as claimed

  verified 4   rejected 1   corrections 1   dropped 0
```

Re-run with `--inject-fault none` for the honest baseline: **zero** rejections,
because the scripted narrator reads the measured directions correctly. The fault is
what gets caught, not a failure the system arranged for itself.

### The live overlay

The overlay is the layer above: a match feed in which every published line carries
its own receipt.

```bash
python -m pip install -r requirements.txt   # fastapi + uvicorn, overlay only
python -m pitchframe --overlay              # then open http://127.0.0.1:8000
```

On Windows there is a launcher for that, `open-overlay.cmd` in the repository root:
double-click it and it starts the server if it is not already up, waits for it to answer,
and opens the catalogue in your browser. Nothing needs an agent or a terminal session to
stay alive.

The front door is the **catalogue** — eighteen named matches on a sphere, one card each.
Click a card and you land on the feed for that match at `/match/<name>`. The choice comes
first because the alternative is meeting one match nobody picked and having to find the
others.

Two views over the same verified data. **Fan** is the readable match feed — no
receipts, no rail, and no rejected lines, because a rejected claim is one the system
refused to publish and showing it to a fan would put a wrong statement in front of
the reader it was withheld from. **Analyst** shows the claim being checked with
`claimed` beside `measured` and a stamp, keeps every rejected claim in the feed struck
through, and pins the running tallies beside the commentary.

```
  VERIFIED   longest pressure pass streak · Riverside United
  Riverside United are stringing passes together under pressure.
  claimed up +1.00 · measured up +1.00

  REJECTED   momentum   planted fault
  The balance of threat has tipped toward Northgate Athletic.
  claimed up +15.20 · measured down -15.20

  CORRECTED  momentum
  The balance of threat has tipped toward Riverside United.
  claimed down -15.20 · measured down -15.20
```

One real second is one match minute at 1×, so a whole match plays in about ninety
seconds. `?fault=narrator-invert` plants one wrong direction per window so the catch
is visible; every planted row is labelled as planted.

The URL is a description of the frame on screen — the view, the match second, and the
build-up panel if one is open — so a glitch can be handed to somebody else as a link,
which opens paused on exactly that second.

Behind the page is a sea of slats that swells, catches light on its crests and is stirred
by the cursor. It is a port of the React Bits **MicroSlats** component to a module with no
framework — this project has no React and no bundler — with its one dependency (`ogl`)
vendored so the overlay still runs with no network. It is inert: it can never take a click
from a control, it freezes for `prefers-reduced-motion`, and if the browser has no WebGL2
the page is exactly what it was without it.

The controls on that page are ports too: the Fan/Analyst switch and the 1×/4×/16× group are
React Bits' **RubberSegment**, the bar floating at the foot of the viewport is its **Dock**
— play/pause, the speed as a checked menu, either view, every goal build-up the replay has
reached, a button that copies a link to the frame on screen, and a menu that lists every key
the page answers to — the seek across the match is its **WakeSlider**, a track that
lifts into a wake around the handle as it is dragged, and the build-up launcher is its
**SlingButton**: a pad in a well that stretches a band when it is pulled and throws a burst
of particles through the well when it is released. The wordmark and the two names on the
scoreboard are **TextPressure**, asking the face for a weight under the pointer, and the nav
between the catalogue and the feed is **GooeyNav**, drawn over the page's own links so that
the way between the two documents works with no script at all. All were ported for the same reason as
the sea, their licences travel beside them, and the dock duplicates the transport on
purpose: one state, three views of it. The keyboard is a fourth way in — `Space` plays and pauses, the arrows
seek by one analysed window, `1`/`2`/`3` are the three speeds, `F`/`A` the two views and `G`
the newest build-up — and the dock's last menu is that list.

Every panel lights the boundary nearest the pointer, in a cone aimed at it. That is
**BorderGlow**, the second of the ports that is not a control — the match bar, the
ratings, the commentary, the check, the ledger and the two rail panels all wear it, and so
does the sphere's stage. Four of its decisions are this page's: it adorns the panels rather
than wrapping them (the page holds references into every one of those panels); the halo is
drawn inside the boundary rather than outside it (two panels clip their contents, and a halo
that appeared on five and was cut off on two would read as a fault); a control with keyboard
focus lights the same edge, aimed at the control itself, because a decoration only the
pointer can reach is a decoration half the readers never see; and **the light is one light**.
Upstream's component decides for itself that the pointer is near it, which on one card is the
whole question and on a page of eight panels is eight lamps — every panel the pointer was
inside lit to full, the gutters unlit seams. Here the whole page shares one pointer listener
and what a panel is given is a falloff: full while the pointer is inside it, fading with the
distance to its nearest edge, gone past 260px, written as one multiplier in all three of its
layers. What a reader sees is one lamp crossing the page, the nearest boundary brightest and
its neighbours dimmer. Its colours are tokens like everything else here, and violet for the
same reason the sea is violet: green and vermilion mean published and caught, and no
decoration may borrow either. Details, parameters and the architecture are in
[docs/overlay.md](docs/overlay.md) — including how any of this is measured, since none of it
is visible from a file.

### Choosing which match

The catalogue is the front door. `/` draws every named match as a card on a sphere — the
scoreline, how many goals, how many came from moves of five touches or more, and the minutes
those goals fell in, all from the match's own numbers. Drag it to spin, step it with the
arrows, press `Enter` or click the front card to open that match in the feed.
Each page links to the other: the feed's masthead to the catalogue, the catalogue's head
back to the feed.

Every match has an address that says which one it is: `/match/matchday`, `/match/quiet`,
`/match/storm`. A name is what you can read off the URL bar or type back a week later, where
`?seed=28` is a number that could be anything. Links written before matches had names still
work: `/match?seed=28` is a 308 to `/match/matchday` with the frame's own parameters carried
across. A seed no scenario owns — `?seed=99`, which is what `--seed 99` produces — has no
name to go to and opens as it always did, because taking any number at all is the promise
that made `--seed` worth having.

It is a port of the React Bits **InfiniteMenu** component — the sixth port in this project,
and the first that is not a control — so the same rules apply to it as to the sea: no
framework, no build step, no network, and a licence that travels beside it. Two of its
decisions are the project's usual ones applied to a new surface. The cards come from
`/api/scenarios`, which serves the scenario registry itself, because a catalogue written
into a page is a second catalogue and the first generator retune leaves the two disagreeing
with nothing to fail. And the sphere needs WebGL2, so a browser without it is given the same
matches as a list, with a line saying which of the two was built, rather than an empty
rectangle — failing to draw the interface must not also fail to reach the thing the
interface is for.

Note what browsing does **not** do: it measures nothing. Every card is a seed, the feed
builds that seed's replay exactly as it always has, and the claims on the page arrive
verified and checked against the stream whether the reader typed the seed or clicked it.

### The goal build-up panel

Clicking a goal on the timeline opens the move that produced it: the pitch in plain
SVG, the ball carried along every touch in order, a step counter, a replay button and
a 0.5×/1× speed toggle. The same panel opens from the dock's *Goal build-ups* menu and
from the **launcher** on the match bar — the sling pad that is on the page from the
first frame, dimmed until the replay has scored, so the panel is reachable before the
first goal rather than only after it. The move summary beside it states the step count, the ball
travel in the schema's own normalised units, the duration in seconds and the third of
the pitch possession started in.

Two things about it are deliberate. First, **recorded touches and inferred ones are
drawn differently** — a carry is a recorded event and is drawn solid and heavier than
a pass, while an *inferred* step is dashed and amber, and adds the *derived carries*
label. No generated match produces an inferred step: our stream accounts for every
metre the ball travels, and unexplained movement is exactly what a carry is — so the
inference is there for imported feeds and fixtures, not as a decoration.
Second, **the panel labels its own provenance**: every highlight carries *synthetic
match*, and it never describes itself as footage or as tracking data, because it is
neither. No dependency and no build step was added for any of it.

### The ratings and the man of the match

Every player in both squads carries a mark out of ten, the tallies behind it, and a
line naming what they did — *1 goal · 3 stops · 25 completed passes*. The panel moves
with the replay: a mark on screen at 60 minutes is the mark the first hour produced,
counted from the events up to that minute and nothing later.

The man of the match is named at **full time**, with a paragraph about how they played.
That is a deliberate withholding rather than a missing feature: the award is computed
over the whole match and carried from the first frame, and the page refuses to draw it
before the replay has been played out — naming one at kick-off would spoil the replay
exactly as printing the final score would.

A rating is the one figure here that is a judgement rather than a measurement, so the
formula is the feature: the weights are constants in `pitchframe/ratings.py`, printed
with the panel and recomputable from the tallies beside each name, and the conclusion
is a renderer over counted facts — it may say a player scored twice and may not say that
this is why the match was won.

Requires Python 3.10+. Developed against **3.13**.

## Why synthetic data

The project generates its own matches rather than consuming a real feed. That is
a deliberate constraint, not a shortcut:

- **Licensing.** No match footage, event feed, or club likeness is used anywhere
  in this project. Every squad and player name is invented.
- **Reproducibility.** A match is a pure function of its seed, so a demo can be
  re-recorded byte-identically and the test suite can assert on exact output.
- **Controllability.** Edge cases — sustained pressure, chaotic end-to-end
  spells, a dominant possession side — can be generated on demand instead of
  waited for.

None of the analytics here are invented from scratch. What we read, what it costs
in licensing terms, and which gaps it exposes in our own metrics are recorded in
[docs/prior-art.md](docs/prior-art.md) — including the reason our verification rate
must not be read as a detection rate.

## Architecture

```text
pitchframe/
  schema.py        Domain types. One MatchEvent vocabulary shared by everything.
  generator.py     Seeded synthetic match generation.
  scenarios.py     Which named match the overlay opens on, and what is in it.
  stats.py         Deterministic analytics + the published metric registry.
  highlights.py    Goal build-up: the path a move took, and what is inferred. Pure.
  ratings.py       Player marks from fixed weights, and the man of the match. Pure.
  claims.py        Structured claims, and the arithmetic that rules on them.
  agents/
    pipeline.py    Snapshot, trace, and the propose -> verify -> retry loop.
    narrator.py    The Narrator agent (requires agent-framework).
    verifier.py    The Verifier agent (requires agent-framework).
    demo.py        Model-free agents, with labelled fault injection.
  llm/
    provider.py    Which model tier is live, and why. Standard library only.
    offline.py     The scripted client. No network, no credentials, no quota.
  overlay/
    replay.py      The deterministic script behind the live page. Stdlib only.
    commentary.py  Turns a checked claim into a sentence. Stdlib only.
    server.py      FastAPI shell (requires fastapi and uvicorn).
    static/        The page, and the ports it draws with: slats.js (the sea),
                   rubber-segment.js (the switch and the speeds), dock.js (the bar),
                   wake-slider.js (the seek), sling-button.js (the launcher).
    static/vendor/ ogl, vendored and tree-shaken, and the ports' upstream licence.
  __main__.py      CLI entry point.
tests/
```

### How a claim is checked

The narrative layer is not allowed to assert free text. Everything it says is a
`Claim`: a named metric, a team, a closed clock window, and a direction — measured
against the equal-length window immediately before it, because "momentum is up"
needs a "compared to when".

```text
Narrator proposes -> verify_claim() -> verified -> published
                          |
                       rejected
                          |
            Verifier explains and suggests a fix
                          |
        Narrator retries once -> re-verified -> published or dropped
```

Three properties are load-bearing:

- **The core has no dependencies.** `schema`, `generator`, `stats`, `claims` and
  `agents.pipeline` import only the standard library, so the analytics pipeline *and
  the retry loop* run on a bare Python install.
- **Arithmetic is never delegated to a model.** `verify_claim` recomputes both
  windows from the raw event stream. The Verifier agent explains the discrepancy and
  guides the retry, but it cannot overturn a verdict — the object it returns has no
  field for one. A verifier whose verdict could be talked out of the truth would be
  worse than none, because it would launder wrong claims as checked ones.
- **A correction is re-verified, never trusted.** "Retry" must not quietly become
  "accept", so a revised claim goes through exactly the same check as the original.

Every metric the system may claim about is registered in `stats.METRICS`, and
anything outside that registry is rejected before any arithmetic runs.

### Reading the model's reply

A model rarely returns the schema exactly as asked. Replies arrive inside code
fences, trailed by commentary, keyed with names the schema never mentioned, or
nested in an envelope object like `{"claims": [...]}`. None of that changes what is
being claimed, so `claims.py` looks through the packaging rather than rejecting it:

- `extract_json_payloads` scans for balanced JSON spans anywhere in the reply. A
  stray `{` in the prose — "I skipped {window 200-300 because it has not closed" —
  costs that one span and nothing else.
- `iter_claim_payloads` unwraps envelope objects and flattens nested arrays, so
  `{"claims": [[...]]}` reads the same as a bare array of claims.
- `parse_claim` resolves key spellings through a bounded allowlist — `metric_name`,
  `trend`, `period`, `claimId` and friends — and treats the case of `up`/`down` as
  spelling. A missing field is reported along with the keys that did arrive,
  because that message is what the retry prompt is built from.

The line is drawn at packaging and spelling. Value *synonyms* are still rejected — a
`direction` of `"rising"` is not read as `"up"` — because accepting one means
inferring an intent the model did not express, and inferring intent is the failure
this layer exists to prevent.

### Running against a real model

The model-backed agents need `agent-framework` and are chosen automatically by tier:
Microsoft Foundry first, then Foundry Local, then Ollama, and finally the scripted
client. Selection always reports which tier is live, so a scripted reply can never be
mistaken for an inference.

```bash
python -m pitchframe --providers
```

Setup for the Foundry tier — including the quota request that is the real critical
path — is in [docs/foundry-runbook.md](docs/foundry-runbook.md).

## Development

```bash
python -m unittest discover -s tests -t .
PITCHFRAME_BROWSER=1 python -m unittest tests.test_browser_harness   # needs a browser and node
```

The second line is the part of the overlay no file can describe. The sea, the sphere and the
panel glow are canvas and computed style, and what they look like is measured by
`tools/browser_check.py`, which starts the overlay and a browser and drives the tab over the
DevTools protocol. It is gated because it takes about half a minute and launches Chrome;
with the gate set and no browser installed it skips and says so, and a failure to drive the
page is a failure rather than a skip. CI runs it that way in a job of its own, on the
runner's own Chrome and with software rendering asked for by name — so a change that breaks
the glow, the sphere or the fallback list fails a push rather than a person. `python
tools/browser_check.py --browser-arg=--disable-webgl` is the same run with WebGL turned off,
which is how the paths a browser without it takes get looked at.

## Roadmap

- [x] Domain schema, synthetic generator, stats engine
- [x] Deterministic CLI and JSON output
- [x] Structured claims with deterministic verification
- [x] Narrator and Verifier agents with the reject-and-retry loop
- [x] Provider tiers with honest fallback reporting
- [x] Microsoft Agent Framework evaluated and integrated
- [x] Live overlay with an analyst view and a fan view
- [x] Goal build-up panel: possession chains, the path to a goal, labelled inferences
- [x] Player ratings from fixed weights, and the man of the match at full time
- [x] Mode browser: the eighteen named matches, browsable, each one a click from its replay
- [x] Readable per-match addresses, and a checked-in browser harness for the page's visuals
- [x] A nav between the two pages, and a wordmark and team names that swell under the pointer
- [ ] Analyst and Personaliser agents
- [ ] Evaluation harness reporting rejection, fallback and latency figures
- [ ] Live event feed in place of the deterministic replay

## Licence

MIT — see [LICENSE](LICENSE).
