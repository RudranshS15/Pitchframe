# Prior art: what to steal, what to read, what to avoid

Pitchframe is not the first football analysis project, and almost nothing here is
invented from scratch. This document records what we looked at, what we took, and
what we deliberately left alone.

It exists for one reason: the brief forbids third-party trademarks and unlicensed
material, and "I found it on GitHub" is not a licence. So the useful question is
not *is this good code* but *may we use it, and what is the idea worth copying
regardless*.

Licence claims below are the ones each project publishes about itself, and were
checked against the GitHub API rather than assumed; where a repository declares
nothing we treat it as **not reusable** even though the source is readable. Star
counts are snapshots and drift, so the more useful column is **last push** — a
maintenance signal that says whether a project is a live dependency or a paper
trail. As of this writing:

| Project | Stars | Licence | Last push | Read or depend? |
|---|---:|---|---|---|
| socceraction | 818 | MIT | 2026-01 | **Read** — author states it is not actively developed |
| soccer_xg | 261 | Apache-2.0 | 2024-06 | **Read only** — stale |
| penaltyblog | 233 | MIT | 2026-10 | **Depend** — actively developed |
| mplsoccer | 549 | MIT | 2026-09 | **Depend** — actively developed |
| kloppy | 562 | BSD-3-Clause | 2026-10 | **Depend** — very active |
| floodlight | 133 | MIT | 2026-05 | Read |
| open-matchroom | 2 | MIT | 2026-06 | Read — same product shape |
| football-match-intelligence | 29 | MIT | 2025-12 | Read |
| football-simulator | 17 | MIT | 2026-10 | Read |
| SkillCorner opendata | 433 | MIT | 2026-09 | Read — data terms unclear |
| google-research/football | 3668 | Apache-2.0 | 2025-06 | Read — **archived** |
| hudl/open-data | 3693 | NOASSERTION | 2026-09 | **Do not ship** |
| metrica-sports/sample-data | 503 | none | 2021-04 | Read — no licence |
| jyrao/MatchTime | 107 | none | 2025-01 | Read — no licence |

---

## The one finding that changes what we claim

Everything else in this document is plumbing. This is the part that affects our
honesty.

Our whole verification story measures the system's claims against arithmetic
computed from **our own generator's event stream**. The events and the ground
truth are the same artifact. That is exactly the condition a 2024 study of
tracking-derived events identifies as inflating results. *Event detection in
football: Improving the reliability of match analysis*, Bischofberger, Baca and
Schikuta, **PLoS ONE 19(4)**, April 2024
([abstract](https://eprints.cs.univie.ac.at/8072)) validated a pass and shot
detector across four datasets and concluded that published accuracy is
overestimated, because of this:

> In accordance with previous studies, we achieve F-scores of up to 0.92 for
> passes, but only when there is an inherent dependency between event and
> positional data. We find a significantly lower accuracy with F-scores of 0.71
> for passes and 0.65 for shots if event and positional data are independent.

The setting is not ours — they detect events from tracking data; we check claims
against an event stream — but the failure mode is the same shape: **measuring a
system against a reference derived from the system.** Their number moved by more
than twenty points when the dependency was removed.

So we should not quietly present 100% verification as a performance figure. It is
not a detection rate and it is not accuracy against real football. It is a
statement about **internal consistency** — the pipeline cannot publish something
its own arithmetic contradicts — and that is a real and useful property, but it
is a different property. Our README already concedes the `fault=none` baseline
produces zero rejections; this is the theoretical reason *why* that concession
matters, and it should be stated in those terms.

The corollary is a design constraint, not a to-do: **the only way to earn a
detection number is to run against events we did not generate.** That means a
real dataset (below), and it is a bigger job than it sounds, because our event
schema does not currently carry what real feeds carry.

---

## Tier 1 — genuine capability gaps

These close holes where we currently cannot make a claim at all, or make one with
a number we invented.

### 1. Momentum is hand-weighted; xT is the principled version

[stats.py](../pitchframe/stats.py) defines momentum from a literal dict:

```python
_THREAT_WEIGHTS = {"shot": 3.0, "goal": 8.0, "tackle": 1.0, "pass": 0.2}
```

A shot is worth 3 and a pass is worth 0.2 because we wrote those numbers down.
That is not analysis, it is a guess with a scale factor, and any football person
who looks will see it immediately.

The established replacement is **expected threat (xT)**, introduced by Karun Singh
in 2019 ([original write-up](https://karun.in/blog/expected-threat.html)) and
implemented in
[ML-KULeuven/socceraction](https://github.com/ML-KULeuven/socceraction) (MIT,
818★), which ships both xT and VAEP and has become the reference implementation
in the open analytics community. The idea: divide the pitch into a grid, give
each cell a value equal to the probability that possession there eventually
produces a goal, and value a move as `xT(end) − xT(start)`.

The important decision for us is **where the grid values come from**. Two options:

- Import published coefficients. Fast, but the numbers describe real football
  and we would be describing our synthetic match with someone else's league.
- **Derive the grid from our own generator's event-to-goal frequencies.** More
  work, and it means the grid is only meaningful for the world it was computed
  in — which is precisely the honest position. We would state the grid is fitted
  to our synthetic distribution and is not a claim about real football.

The second is the one that fits this project. It also produces a genuinely
interesting artifact: our own pitch-value surface, which we can render.

Note that xT and VAEP are not the same thing and should not be conflated;
socceraction ships both, and the distinction is the subject of a paper of its own
(Van Roy et al., *Valuing on-the-ball actions in soccer: a critical comparison of
XT and VAEP*, AAAI-20 workshop). VAEP's original citation is Decroos et al.,
*Actions speak louder than goals*, **KDD 2019**. If we build xT, we should also be
able to say what we did **not** build.

One caveat on socceraction as a dependency rather than a text: its own README
says *"be aware that socceraction is not actively developed. It's primary use is
to enable reproducibility of our research"* and warns feature requests will
likely be declined. Read it for the method; do not build on it.

### 2. We did not model possession, so we could not describe moves

**Now addressed** — see *The goal build-up panel* below. This entry is kept as it
was written, because the reasoning that made the change worth making is the useful
part and it would be dishonest to present it as obvious in hindsight.

[schema.py](../pitchframe/schema.py) had no possession identifier. An event knows
its clock, team, player and location, but not **which possession it belonged
to**. StatsBomb's event model carries `possession` and `play_pattern` on every
event for this reason, and Wyscout's release is built around possession chains.

The cost is concrete: "a nine-pass move" is the single most legible claim in
football commentary, and we cannot make it, because counting consecutive passes
requires knowing where one sequence ended and the next began. Our
`longest_pressure_pass_streak` is the closest thing and it is a proxy built from
per-event pressure readings rather than genuine possession.

Adding `possession: int` and `play_pattern: str` to `MatchEvent`, and having the
generator assign them, unlocks a class of claims that are both more interesting
and easier for a non-analyst to check. It is the highest-value schema change
available and it is a generator change first.

### 3. Shots carry no probability, so shot quality is unclaimable

We have shots and goals but no xG. So "that was a good chance" is not something
the system can say or verify, even though it is the most common analysis claim in
the sport.

[soccer_xg](https://github.com/ML-KULeuven/soccer_xg) (Apache-2.0, 261★) is the
reference implementation of a *trainable* xG pipeline — `DataApi`, `XGModel`,
`train()`, `validate()`, `estimate()` — over SPADL event data. It has not been
pushed since mid-2024, so treat it as a paper trail to read rather than a
dependency to adopt; the feature-set and train/validate split are the useful part,
not the coefficients.

More useful than the model itself is how it is judged. Football xG work
([evaluation paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC10075453/)) is
scored on probability quality, not ranking: **ROC AUC, Brier score and expected
calibration error (ECE)**, with isotonic, Platt and Beta calibration compared.
That gives us the vocabulary for a number that actually means something, and
replaces a metric we invented.

### 4. Our "chaos index" is a measure of irregularity, chosen by us

[stats.py](../pitchframe/stats.py) computes rhythm as the coefficient of variation of
gaps between consecutive events. It is defensible and easy to explain.

[floodlight-sports/floodlight](https://github.com/floodlight-sports/floodlight)
(MIT, 133★) — a general sports-data framework with Voronoi space control,
metabolic power and **approximate entropy** — is the place to check that choice.
ApEn is the standard irregularity measure for physiological and match time
series, and if our CV-based index and ApEn disagree on the same window, that is a
finding about our metric, not a coincidence. Also worth reading: Voronoi space
control is a far better foundation for a "who controlled the pitch" claim than
anything we currently compute.

---

## Tier 2 — visible parity with a real score app

Our overlay is deliberately close to the shape people already know. Here is the
prior art for that shape, and the features we are missing.

**The closest UI match** is
[profilsoftware/open-matchroom](https://github.com/profilsoftware/open-matchroom)
(**MIT**): a white-label match centre with a scoreboard, line-ups, a Live tab
carrying the timeline and commentary, and a Statistics tab, built on Next.js and
Django REST with an admin live console. It has very few stars, so it is a
structure to learn from rather than a thing to depend on — but it is the same
product shape, permissively licensed, and worth reading for how it splits the
scoreboard from the tabs.

[DataKnight1/football-match-intelligence](https://github.com/DataKnight1/football-match-intelligence)
(**MIT**) is the closest reference for the *analytical* half: shot maps, pass
networks, momentum, cumulative xG and possession timelines in one dashboard, with
frame-by-frame playback. That cumulative-xG chart is the single most copyable
idea in this document.

Features a SofaScore-shaped product has that we do not:

- **Pre-match win probability.** Football forecasting has a standard tool for
  this — Dixon-Coles and bivariate Poisson models, implemented in
  [martineastwood/penaltyblog](https://github.com/martineastwood/penaltyblog)
  (**MIT**, 233★, pushed within the last week) implements and also exposes a
  pretrained xT loader. This is a small, well-understood model and it is *verifiable*, because
  win probabilities can be scored for calibration exactly like xG.
- **A shot map.** [andrewRowlinson/mplsoccer](https://github.com/andrewRowlinson/mplsoccer)
  (**MIT**, 549★) is the standard Python pitch-plotting library — `Pitch`,
  `Radar`, heatmaps, arrows, coordinate standardisation. It would let us export a
  static PNG shot map or pass network straight from the pipeline, which is a
  shareable artifact for a submission page. Note
  [slothfulwave/soccerplots](https://github.com/slothfulwave/soccerplots) (MIT)
  has merged into mplsoccer and should not be started with.
- **A momentum chart rather than a momentum number.** We compute momentum; a
  broadcast-style area chart is the form people actually read.

---

## Data: what we may and may not touch

The synthetic-data stance in the README is a choice we made for legal reasons and
stuck to for coherence reasons. If we ever validate against real data, this table
is the constraint set. The distinction that matters is that **open to download is
not the same as licensed to use**, and a code licence never covers the data.

| Dataset | Content | Coordinates | Terms |
|---|---|---|---|
| [Wyscout public dataset](https://figshare.com/collections/Soccer_match_event_dataset/4415000) | 1,941 matches, ~3.25m events, 7 competitions | **0–100**, from the attacking team's perspective | **CC BY 4.0** — attribution required, commercial use permitted. Confirmed in the [processed mirror](https://github.com/koenvo/wyscout-soccer-match-event-dataset): "The data sets are released under the CC BY 4.0 License." |
| [StatsBomb Open Data](https://github.com/hudl/open-data) | Curated matches, events and line-ups as JSON | **120 × 80**, origin top-left | Research use, attribution expected; GitHub records the licence as NOASSERTION. **Do not ship.** |
| [SkillCorner Open Data](https://github.com/SkillCorner/opendata) | 10 matches, broadcast tracking + derived events, ~97% player-ID accuracy | **metres**, origin at pitch centre | Repo carries **MIT**, though the README does not state whether the licence covers the data as well as the code. Check before redistributing. |
| [Metrica Sports sample data](https://github.com/metrica-sports/sample-data) | 3 matches, synchronous event + tracking | **0–1**, origin top-left, pitch 105 × 68 m | No declared licence. Asks for acknowledgement. |
| [Football-Data.co.uk](https://football-data.co.uk/data.php) | Match results and odds, **not** event streams | n/a | Private individuals only; explicitly excludes automated scraping and AI training. **Do not use.** |
| [SoccerNet](https://www.soccer-net.org/faq) | 500 broadcast games, action-spotting labels; tracking and game-state devkits | video pixels | Copyrighted video, NDA-gated, research only. |

Two practical notes. First, **Wyscout is the target**: CC BY 4.0 is a real licence
grant, 0–100 is our normalisation, and it means an adapter is a coordinate flip
rather than a rewrite. It is also genuinely reachable — the processed mirror is
loadable through kloppy in two lines, no scraping and no credentials:

```python
from kloppy import datasets
dataset = datasets.load("wyscout", match_id=2499841)
```

Cite it as Pappalardo, Cintia, Rossi et al., *A public data set of spatio-temporal
match events in soccer competitions*, **Scientific Data 6**, 236 (2019). Second,
our invented club names are not incidental — they are what keeps us clear of the
trademark rules, and they should stay invented even if we later validate against
real data.

### Coordinate discipline

We normalise to 0–100 with the home team attacking towards increasing `x`, and we
say so in [schema.py](../pitchframe/schema.py). Every provider above disagrees with
us and with each other: StatsBomb is 120 × 80 top-left, Wyscout is 0–100
attacking-perspective, Metrica is 0–1 top-left, SkillCorner is centre-origin
metres.

[PySport/kloppy](https://github.com/PySport/kloppy) (**BSD-3-Clause**, 562★,
pushed this week) exists largely because of this, and its
[coordinate-system guide](https://kloppy.pysport.org/user-guide/concepts/coordinates/)
is worth reading even though we are not adopting the library. The lesson we
should carry: **origin, y-orientation, units and play direction all need to be
recorded explicitly**, because a silent mismatch here produces plausible-looking
wrong numbers rather than a crash. If we add a Wyscout adapter, the adapter must
flip orientation *and* the schema should carry the convention it is in.

---

## Methodology worth borrowing

These are not features. They are ways of measuring that we currently lack.

**Commentary text has standard metrics we do not compute.** The
[SoocerNet dense-captioning task](https://www.soccer-net.org/tasks/dense-video-captioning)
scores generated text against references using **METEOR, BLEU, ROUGE-L and
CIDEr**, ranked on METEOR. Our narrator produces commentary and we score it with
nothing at all. METEOR is the right first addition because it handles paraphrase
better than BLEU, which matters when the same fact can be stated several ways.

**Attributing a claim to the right minute is a separate, measurable property.**
[MatchTime / MatchVoice](https://github.com/jyrao/MatchTime) found that commentary
timestamps are routinely misaligned, hand-aligned 49 matches to build a reference,
and report **mean absolute offset** (13.89s → 6.89s) plus the share of clips
correct within 10/30/45/60 seconds. That framing applies directly to our
`closed_windows` design: we can state, and test, the lag between an event and the
earliest frame that could legitimately mention it. Worth copying the *metric*;
the repository declares no code licence and some releases are still listed as
TODO, so it is reference material only.

**If events ever come from video, the scoring vocabulary is average-mAP with
temporal tolerances** — 1–5 seconds for tight timing, 5–60 seconds for loose —
from the SoccerNet action-spotting benchmark. Useful for stating how good an
event feed is, and the tolerance split is the insight: being right about *what*
happened is a different question from being right about *when*.

---

## The goal build-up panel

This is the one feature in this document that we actually built, so it gets its
own accounting: what the idea was, where it came from, and what licence it
arrived under.

**What it does.** Clicking a goal on the timeline opens a panel that draws the
move that produced it — where possession started, every touch in order, how the
ball travelled, how long it took, and the finish — with the ball animated along
the path and a step counter.

**Where the idea came from.** Two places, neither of them code:

- **Possession chains as a schema concept.** StatsBomb's open event model puts a
  `possession` index on every event and Wyscout's release is organised around
  possession chains, which is what makes "a move" a thing you can point at in
  those schemas and not in ours. We read the *shape* of that idea and added an
  additive `possession_id` to `MatchEvent`, defaulted so nothing that predates it
  breaks. No code, no data and no documented field list was copied; the generator
  assigns the field from its own state machine.
- **Per-goal replay inside a match-centre UI.**
  [DataKnight1/football-match-intelligence](https://github.com/DataKnight1/football-match-intelligence)
  (**MIT**) ships frame-by-frame playback over a match dashboard alongside its
  shot maps and cumulative xG, which is the same product shape as this panel at a
  larger scale. It is a structure to learn from rather than a thing to depend on.
  [profilsoftware/open-matchroom](https://github.com/profilsoftware/open-matchroom)
  (**MIT**) is the same story for putting a detail view beside a match feed.

**What is ours.** The backward walk — the boundary rules (stop at a possession
change, stop at the other team's tackle, stop at kick-off), the carrier
inference, the third-splitting in the attacking direction, and the SVG rendering —
is written against our own schema. Two decisions are worth recording because they
are the ones a reviewer would question:

- **A carry is a recorded event, and a guess is labelled as a guess.** The generator
  emits `carry` events, so a carry reaches the panel with a carrier, a route and a
  speed and is drawn solid and heavier than a pass. The inference is kept for feeds
  that have no such event: there a carry is derived from one player covering ground
  between two touches, flagged `derived` all the way to the payload, drawn dashed and
  thinner, and announced in the panel's own labels. It cannot fire on our own stream —
  every event begins where the previous one ended, so there is no unexplained movement
  for it to find. The alternative, drawing a guess as though it were a recorded pass,
  would undermine the one thing this project is about. Real carry detection is a
  tracking-data problem (the SkillCorner and Metrica datasets above carry the
  positional feeds that make it answerable); we have events only, and say so in the
  UI.
- **The pitch is drawn in plain SVG, 105 × 68, with no dependency.**
  [andrewRowlinson/mplsoccer](https://github.com/andrewRowlinson/mplsoccer) (**MIT**)
  is the standard Python pitch-plotting library and would do the markings in a few
  lines; we did not adopt it because it produces raster output server-side, which
  cannot be animated step by step in a browser, and because the overlay's whole
  discipline is no new dependency and no build step.

**Licence status: nothing was taken for the panel.** No third-party code, data,
asset, icon or copy is used in it, and **no dependency was added** for it — the panel
is standard-library Python on the server and plain DOM/SVG in the browser. The projects above are all
permissive (MIT) or are schemas read for their shape, and this file is where we
say so rather than a comment in the source.

**What we deliberately did not build.** Expected threat would value the move
rather than describe it, and the grid coefficients are somebody else's league — see
the xT discussion above. A shot map (mplsoccer) and a cumulative-xG chart
(football-match-intelligence) are the other two obvious adjacencies; both are
still unbuilt.

---

## What we appear to have that others do not

An honest audit, since everything above is borrowed.

The **event-valuation** layer is a crowded, mature field — xT, VAEP, xG, EPV all
predate us and are better than what we have. The **match-centre UI** is a solved
shape and we are following it, not leading it. The **synthetic generator** is a
minor variant of a thing several projects do ([football-simulator](https://github.com/tbleckert/football-simulator),
MIT, emits timestamped match state and events at 0.25-second resolution; [Google
Research Football](https://github.com/google-research/football), Apache-2.0,
archived, is the physics-based research environment).

What we could not find an equivalent for is the **refusal**. The established
tools *rank* actions and *describe* matches; none of them ship a mechanism that
takes a claim about a match, recomputes it from the event stream, and declines to
publish it when the arithmetic disagrees — then tells the reader that it did.
That inversion, plus the fan/analyst split that withholds rejected claims from
the reader they were withheld for, is the part of this project that is a product
idea rather than an implementation of someone else's.

Which is worth keeping in proportion. It is one good idea sitting on top of a lot
of other people's work, and the work above is how the parts underneath get to the
level of the part on top.

---

*If we adopt any of the above, this file should record what we took and what
licence we took it under. Permissive and worth reading: socceraction (MIT),
soccer_xg (Apache-2.0), mplsoccer (MIT), kloppy (BSD-3-Clause), floodlight (MIT),
penaltyblog (MIT), open-matchroom (MIT), football-match-intelligence (MIT). Not
licensed, read for ideas only: soccermatics, MatchTime, SoccerNet devkits.*

*Tally so far: the goal build-up panel took an idea (possession chains, per-goal
replay) from MIT-licensed and schema-only sources and took no code. Four surfaces of
the overlay did take code: the sea, the view switch with the replay speed, the bar
at the foot of the page, and the match-minutes seek are ports of React Bits components
(MIT + Commons Clause License Condition v1.0, Copyright (c) 2026 David Haz), and the
sea's renderer is
`ogl` (Unlicense), vendored. Those licences ship with them —
`overlay/static/vendor/react-bits-LICENSE.md` and `overlay/static/vendor/ogl.mjs` — and
`docs/overlay.md` records what each port changed. Nothing else in this repository is
third-party material, and no dependency was added to anything the project needs to
run: still no React, no package manager and no build step.*
