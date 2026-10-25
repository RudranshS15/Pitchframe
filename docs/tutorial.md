# Judge tutorial: Pitchframe in five commands

Everything below is designed to survive being copied by a stranger. No step needs an
Azure subscription, an API key, or a GPU; the first two need nothing installed at all.
Total time is about five minutes, and every command can fail loudly rather than
silently — if one errors, that is a bug worth reporting, not an environment puzzle.

---

## Command 1 — the core, with nothing installed

```bash
python -m pitchframe --seed 7
```

This is the whole analytics pipeline on a bare interpreter: a seeded synthetic match
(1,514 events), its statistics, ratings and key moments, printed as a report. If this
works, the project needs no dependencies — that promise is enforced in CI by a job
that installs nothing at all.

**What to look at:** the report ends with the event-type vocabulary any judge can
grep for — every number printed came from an event stream, not a template.

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
eighteen named matches on a sphere. Click a card (or press Enter) to open that
match's feed: the fan view by default, the analyst view one keypress away (`A`), the
whole match playing in about ninety seconds.

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

- [microsoft-stack.md](microsoft-stack.md) — the Microsoft Agent Framework /
  Foundry / Entra ID layer in one paragraph and one table.
- [foundry-runbook.md](foundry-runbook.md) — pointing the agents at a real Foundry
  deployment, including the quota step that is the only slow part.
- [overlay.md](overlay.md) — how the overlay is built, and *measured*, including the
  browser harness that drives the real page in CI.
