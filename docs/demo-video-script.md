# The demo video: script and how to capture it

Target: **under two minutes**, public link, everything in English. This is a
recording of the system being *used*, not slides.

## Timed script (~110 seconds recorded)

| When | On screen | Narration (or on-screen caption) |
|---|---|---|
| 0:00–0:08 | The catalogue sphere turning, one card selected | "Broadcast turns a match into a scoreline. Pitchframe turns the event stream into a live, *checkable* narrative." |
| 0:08–0:22 | Terminal: `python -m pitchframe --seed 7` | "Everything runs from a seeded event stream — no dependencies for the core. Statistics, ratings, key moments — all deterministic." |
| 0:22–0:42 | Terminal: `python -m pitchframe --claims`; pause on the `FAULT INJECTED` block | "The agent loop: a narrator proposes claims, rules verify each against the raw stream. A wrong claim is planted deliberately — caught, rejected, and corrected to the measured direction." |
| 0:42–1:00 | `python -m pitchframe --overlay`, then the browser opening the catalogue, clicking **matchday** into the fan feed | "Install two dependencies, start the overlay, pick a match. The feed plays a full match in ~90 seconds." |
| 1:00–1:30 | In the feed: press `A` (analyst), click a goal on the timeline (build-up panel), drag the seek, toggle `/fault=narrator-invert` page and click a rejected row | "Analyst view: every line carries its receipt — claimed beside measured. Goals open the move that produced them; the timeline swallows the rest. With a fault replayed, rejected lines stay in the feed, struck through, labelled, and overruled by the measurement." |
| 1:30–1:50 | Close on the provider banner / `--providers` output | "And the honest part: with no model configured, the system says *scripted replies, no model in use*. Every error rate on this page is checkable by the judge watching it." |

Then discard all takes over 118 seconds; re-cut rather than squeeze.

## How this recording gets made

1. Start the overlay server from the repository (`python -m pitchframe --overlay`),
   reuse the browser harness environment from the CI browser job
   (`tools/browser_check.py` drives Chrome over the DevTools protocol, so the same
   browser can be pointed at the running server).
2. Split captures: `asciinema` (or a plain 60-second cast of the two terminal
   commands), and the browser via a screen capture of that Chrome window. Terminal
   and browser take up roughly 25/85 seconds respectively under this script.
3. One hard rule from the submission rules: a single continuous take is fine and
   simpler; splice only the terminal-into-browser cut for the 42-second switch.
