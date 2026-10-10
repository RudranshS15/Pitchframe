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

### Watch the verification working

Two commands show the agent layer. Neither needs a dependency, a network connection
or an Azure subscription:

```bash
python -m pitchframe --providers   # which model tiers are usable on this machine
python -m pitchframe --claims      # run the propose -> verify -> retry loop
```

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
  schema.py        Domain types. One MatchEvent vocabulary shared by everything.
  generator.py     Seeded synthetic match generation.
  stats.py         Deterministic analytics + the published metric registry.
  claims.py        Structured claims, and the arithmetic that rules on them.
  agents/
    pipeline.py    Snapshot, trace, and the propose -> verify -> retry loop.
    narrator.py    The Narrator agent (requires agent-framework).
    verifier.py    The Verifier agent (requires agent-framework).
    demo.py        Model-free agents, with labelled fault injection.
  llm/
    provider.py    Which model tier is live, and why. Standard library only.
    offline.py     The scripted client. No network, no credentials, no quota.
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
```

## Roadmap

- [x] Domain schema, synthetic generator, stats engine
- [x] Deterministic CLI and JSON output
- [x] Structured claims with deterministic verification
- [x] Narrator and Verifier agents with the reject-and-retry loop
- [x] Provider tiers with honest fallback reporting
- [x] Microsoft Agent Framework evaluated and integrated
- [ ] Analyst and Personaliser agents
- [ ] Live overlay with an analyst view and a fan view
- [ ] Evaluation harness reporting rejection, fallback and latency figures

## Licence

MIT — see [LICENSE](LICENSE).
