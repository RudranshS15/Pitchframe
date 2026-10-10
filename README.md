# Pitchframe

Real-time match intelligence from synthetic football events — turning a raw event
stream into explainable, personalised narratives.

> **Status: early.** The synthetic data generator and the analytics engine are
> implemented and tested (45 tests). The agent layer, the overlay, and the
> evaluation harness are in progress. See [Roadmap](#roadmap).

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
Northgate Athletic  3 - 2  Riverside United
match PF-001 | 1544 events

  possession          home 60.3%  away 39.7%
  pass accuracy       home 76.8%  away 68.8%
  pass difficulty     home 0.357  away 0.354
  pressure index      home 0.492  away 0.462
  chaos index         0.459   (0 = controlled, 1 = frantic)
  momentum            +72.6  (positive favours home)
  peak speed          pass 60.5 km/h | shot 120.3 km/h
  longest press. streak  home 4  away 3

  key moments
    63'  goal     away Brenwell   pressure 0.76
    05'  goal     home Nikrado    pressure 0.65
    54'  goal     away Danholm    pressure 0.62
    44'  shot     home Nikrado    pressure 0.84
    79'  shot     home Mirbeck    pressure 0.80
```

Add `--json` for the machine-readable form that later stages consume:

```bash
python -m pitchframe --seed 7 --json
```

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

## Architecture

```text
pitchframe/
  schema.py      Domain types. One MatchEvent vocabulary shared by everything.
  generator.py   Seeded synthetic match generation.
  stats.py       Deterministic analytics + the published metric registry.
  __main__.py    CLI entry point.
tests/
```

Two properties are load-bearing:

- **The core has no dependencies.** `schema`, `generator` and `stats` import only
  the standard library, so the analytics pipeline runs on a bare Python install.
- **The stats engine is deterministic and side-effect free.** That is what lets a
  later agent treat it as ground truth: given a claim that a metric moved in some
  direction over some window, it can be recomputed and checked without a model
  call. Every metric the system may make claims about is registered in
  `stats.METRICS`, and anything outside that registry is rejected.

## Development

```bash
python -m unittest discover -s tests -t .
```

## Roadmap

- [x] Domain schema, synthetic generator, stats engine
- [x] Deterministic CLI and JSON output
- [ ] Agent layer — Analyst, Narrator, Verifier, Personaliser
- [ ] Live overlay with an analyst view and a fan view
- [ ] Evaluation harness reporting rejection, fallback and latency figures
- [ ] Evaluation of Microsoft Agent Framework as the orchestration layer

## Licence

MIT — see [LICENSE](LICENSE).
