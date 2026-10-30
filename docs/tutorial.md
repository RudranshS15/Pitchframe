# Pitchframe in five commands

Everything below is designed to survive being copied by a stranger. No step needs an
Azure subscription, an API key, or a GPU; the first one needs nothing installed at all.
Total time is about five minutes, and every command can fail loudly rather than
silently — if one errors, that is a bug worth reporting, not an environment puzzle.

---

## Command 1 — the core

A generated match runs on a bare interpreter, with nothing installed:

```bash
python -m pitchframe --seed 7
```

This is the whole analytics pipeline: a seeded synthetic match (1,514 events), its
statistics, ratings and key moments, printed as a report. If this works, the project
needs no dependencies — that promise is enforced in CI by a job that installs nothing
at all.

A recorded match is one download and the same report:

```bash
python -m pitchframe --statsbomb 8658
```

That fetches the 2018 World Cup final from StatsBomb's open data and analyses its 1,532
events. `--fetch 8658` downloads without analysing; [real-matches.md](real-matches.md)
covers the mapping and the three quantities the adapter derives.

**What to look at:** both reports end with the event-type vocabulary you can grep for —
every number printed came from an event stream, not a template.

## Command 2 — the propose → verify → retry loop

```bash
python -m pitchframe --claims
```

The scripted narrator proposes claims, the rules verify each against the raw event
stream, and one claim is **deliberately planted and labelled** (`! FAULT INJECTED`)
so you can see the loop catch it: rejected, then corrected to the measured direction.

**What to look at:** the last line's tallies — `verified`, `rejected`, `corrections`,
`dropped`. Re-run with `--inject-fault none` for the honest baseline: zero rejections,
because the narrator reads the measured directions correctly. The fault is what the
system catches, not a failure it arranged for itself.

## Command 3 — install the overlay's two dependencies

```bash
python -m pip install -r requirements.txt
```

This brings in `fastapi`, `uvicorn` and the optional agent stack. The core stays
standard library; this step is only needed for the browser layer below. On Python
3.13 the install is clean; other versions fall back to the core promise.

## Command 4 — the live overlay

```bash
python -m pitchframe --overlay
```

This starts the local server and opens `http://127.0.0.1:8000` — the **catalogue**,
every named match on a sphere, recorded ones first when the cache has one. Click a card
(or press Enter) to open that match's feed: the fan view by default, the analyst view
one keypress away (`A`), the whole match playing in about ninety seconds. For a recorded
match directly:

```bash
python -m pitchframe --overlay --statsbomb 8658
```

`/matches` is the other way in — live scores and the whole archive. It opens on the
current Bundesliga matchday; `python -m pitchframe --live` prints the same list without a
browser. A live match expands into its goals and the moments the goal list supports; a
recorded match from the archive downloads on first open and lands on the feed above.

**What to look at:** the check panel under a feed row (analyst view) — claimed beside
measured with a stamp; the goal build-up (`G`, or click a goal on the timeline); the
URL bar, which is a description of the frame on screen and can be handed to someone
else to open paused on exactly that second.

## Command 5 — watch the catch, in the browser

Open, from command 4's page:

```
/match/matchday?fault=narrator-invert
```

This plants one wrong direction per analysed window. Every planted row is labelled in
the feed, published rejected, and shown with the measured receipt that overruled it —
which is the whole product: a narrative layer whose error rate a viewer can check
rather than trust.

**What to look at:** the struck-through rejected rows next to their corrections, and
the honest provider note (from `python -m pitchframe --providers`) saying which tier
produced what — a scripted reply can never be mistaken for model inference.

---

## Where to go next

- [real-matches.md](real-matches.md) — recorded matches: the mapping, the derived
  quantities, the known limits and the attribution.
- [microsoft-stack.md](microsoft-stack.md) — the Microsoft Agent Framework /
  Foundry / Entra ID layer in one paragraph and one table.
- [foundry-runbook.md](foundry-runbook.md) — pointing the agents at a real Foundry
  deployment, including the quota step that is the only slow part.
- [overlay.md](overlay.md) — how the overlay is built, and *measured*, including the
  browser harness that drives the real page in CI.
