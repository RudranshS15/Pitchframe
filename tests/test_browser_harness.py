"""Drive the overlay in a real browser and judge what the page does.

Everything else in this suite reads the repository. That is the right way to test a claim
pipeline — a number is either in the stream or it is not — and it is no way at all to test
the overlay, which is canvas, computed style and a light on a boundary. A file-level test
can say that a token was written and a class was added; it cannot say that anything lit up,
that one lamp moves across the page rather than eight panels each deciding for themselves,
or that the sphere drew a card.

So this file is the other half of ``tools/browser_check.py``: that script starts the server
and a browser and produces a report of measurements, and the assertions below say what the
numbers have to be. The measurements are the ones that were made by hand while the ports
were written — the boundary band that changes while the interior does not, the falloff that
follows the distance from the pointer, the panel the keyboard lights with the pointer far
away — and this is where they stopped being something a session remembered.

It is gated, and the gate is deliberately a human decision::

    PITCHFRAME_BROWSER=1 python -m unittest tests.test_browser_harness

``PITCHFRAME_BROWSER=1`` is what asks for it. Without it the class skips, because a run
that takes half a minute, opens a browser and needs a Chrome installed is not what the
default ``python -m unittest`` should do to somebody editing ``claims.py`` — and a suite
that is slow enough to be resented stops being run. The gate is not a fallback for a
missing browser: if the gate is set and the tooling is there, a failure to drive the page
is a failure, not a skip.

The report is produced once per run and shared by every test below: launching a browser per
assertion would take minutes and each assertion is about the same page.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = ROOT / "tools" / "browser_check.py"

#: The environment variable that asks for the harness. Named for the project rather than
#: for the tool, because a machine may have several browsers and only one of them is this.
GATE = "PITCHFRAME_BROWSER"

#: How long the whole browser run may take. The driver sleeps about twenty-five seconds by
#: design — a transition has to finish before an opacity means anything — and the rest is
#: a page measuring eighteen matches and drawing them.
TIMEOUT_SECONDS = 300.0

#: The port's own reach, in pixels, from ``REACH_PX`` in static/border-glow.js. Repeated
#: here rather than imported: the number is a fact about what the page looks like, which is
#: what this file is checking, and a test that read it out of the module it is checking
#: could not notice the module changing it.
REACH_PX = 260

#: The panels the feed marks, in source order.
PANELS = ["board", "ratings", "feed", "check", "ledger", "metrics", "squads"]

_REPORT: dict | None = None

#: What the driver said when something went wrong, kept so that a test can quote it. An
#: assertion about a page's console is worth far more with the console in its message.
_DIAGNOSTICS = ""


def _orchestrator():
    """``tools/browser_check.py``, loaded by path: tools/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("pitchframe_browser_check", ORCHESTRATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unavailable() -> str | None:
    """Why the harness cannot run here, or None when it can.

    Checked before the gate's variable is read, so that a machine without a browser says
    which of the two things is missing rather than reporting a gated-off test as the reason
    nothing ran.
    """
    if not ORCHESTRATOR.exists():
        return f"{ORCHESTRATOR.relative_to(ROOT)} is missing"
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        return "the overlay's dependencies are not installed (python -m pip install -r requirements.txt)"
    if shutil.which("node") is None:
        return "node is not on PATH, and the driver is a Node script"
    if _orchestrator().browser() is None:
        return "no Chrome or Edge found; set PITCHFRAME_CHROME to one"
    return None


#: Resolved once at import, like the gate itself: both are facts about the machine, and
#: resolving them per test would launch a browser as an argument to a skip decorator.
UNAVAILABLE = unavailable()

#: Whether the run asked for. Read here rather than in the decorator so that the message can
#: say what to set instead of only what was skipped.
ASKED = os.environ.get(GATE) == "1"


def report() -> dict:
    """The browser run's report, produced once and shared by every test below."""
    global _REPORT, _DIAGNOSTICS
    if _REPORT is None:
        with tempfile.TemporaryDirectory(prefix="pitchframe-report-") as directory:
            path = Path(directory) / "report.json"
            completed = subprocess.run(
                [sys.executable, str(ORCHESTRATOR), "--json", str(path), "--timeout", str(TIMEOUT_SECONDS - 30)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TIMEOUT_SECONDS,
            )
            diagnostics = f"{completed.stdout[-2000:]}\n{completed.stderr[-4000:]}"
            if not path.exists():
                raise AssertionError(
                    f"the browser harness did not produce a report (exit {completed.returncode}):\n"
                    + diagnostics
                )
            drove_the_page = json.loads(path.read_text(encoding="utf-8"))
            # A run that exits nonzero with a report in hand is a page that threw on the way:
            # the report is complete, and `test_neither_page_reported_a_problem` is the right
            # place to fail, quoting the console text the driver collected. An exit code with
            # nothing in `problems` to explain it is the harness failing to do its job — a
            # server that would not start, a driver that threw, a browser that never
            # answered — and that does raise here.
            if completed.returncode != 0 and not drove_the_page.get("problems"):
                raise AssertionError(
                    f"the browser harness exited {completed.returncode}:\n" + diagnostics
                )
            _DIAGNOSTICS = diagnostics if completed.returncode != 0 else ""
            _REPORT = drove_the_page
    return _REPORT


@unittest.skipIf(UNAVAILABLE is not None, f"cannot drive a browser: {UNAVAILABLE}")
@unittest.skipUnless(ASKED, f"set {GATE}=1 to drive the overlay in a real browser")
class BrowserHarnessTest(unittest.TestCase):
    """What the pages do in a browser, as opposed to what their files say."""

    report: dict = {}
    feed: dict = {}
    diagnostics = ""

    @classmethod
    def setUpClass(cls) -> None:
        """Once per class rather than once per test: a browser launch per assertion would
        take minutes, and every test below is about the same two pages.

        A failure to drive the page raises here, which is what a failure has to do when the
        run was asked for — but it raises once, rather than starting another browser to
        fail in the same way for each of the seven tests below.
        """
        cls.report = report()
        cls.feed = cls.report["feed"]
        cls.diagnostics = _DIAGNOSTICS

    # ------------------------------------------------------------- the helpers -- */

    def assertFalloffIsDistance(self, reading: list[dict], where: str) -> None:
        """Every panel's share of the light is a function of how far the pointer is.

        This is the whole claim of the shared light in one assertion: not "the panel under
        the pointer is lit" — eight panels each deciding for themselves got that right —
        but that a panel's brightness is set by the distance from the pointer to its
        nearest edge, everywhere on the page, by one number.
        """
        for panel in reading:
            expected = max(0.0, 1 - panel["distance"] / REACH_PX)
            self.assertAlmostEqual(
                float(panel["falloff"]),
                expected,
                delta=0.02,
                msg=f"{where}: {panel['name']} at {panel['distance']}px should be lit {expected:.3f}",
            )

    def assertConeFacesThePointer(self, reading: list[dict], pointer: dict, where: str) -> None:
        """Every lit panel aims its cone at the pointer, from its own centre.

        The conic gradient the port masks with starts at the top and turns clockwise, so
        the bearing from a panel's centre to the pointer is that angle plus a quarter turn.
        A panel whose cone pointed anywhere else would light a boundary the pointer is not
        near, which is the difference between a light on the page and a light on a panel.
        """
        for panel in reading:
            if float(panel["falloff"]) <= 0:
                continue  # unlit, so it has no angle to speak of
            rect = panel["rect"]
            dx = pointer["x"] - (rect["left"] + rect["width"] / 2)
            dy = pointer["y"] - (rect["top"] + rect["height"] / 2)
            if dx == 0 and dy == 0:
                continue
            expected = (math.degrees(math.atan2(dy, dx)) + 90) % 360
            actual = float(panel["angle"].removesuffix("deg"))
            difference = abs((actual - expected + 180) % 360 - 180)
            self.assertLess(
                difference,
                1.0,
                f"{where}: {panel['name']} masks {actual:.1f}deg, not the {expected:.1f}deg toward the pointer",
            )

    # --------------------------------------------------------------- the title -- */

    def test_the_feed_names_itself(self) -> None:
        """The page a shared link opens on says what it is, in one heading that moves.

        The feed used to open on a scoreboard, with the match named only in the tab and
        the footer; the heading is mounted as a pressure host, so the line is also the
        one thing above the fold that answers the pointer. What is asserted is the
        agreement rather than the words: one `<h1>`, its text is the tab's own name of
        the match, and the letters were built to swell.
        """
        heading = self.feed["heading"]
        self.assertEqual(heading["count"], 1, "the feed does not have exactly one heading")
        self.assertEqual(heading["tag"], "H1")
        self.assertTrue(heading["text"], "the heading has no text")
        self.assertIn(" v ", heading["text"], "the heading does not name both teams")
        self.assertTrue(
            heading["title"].startswith(heading["text"]),
            f"the tab says {heading['title']!r} and the page says {heading['text']!r}",
        )
        self.assertTrue(
            heading["pressure"], "the heading is not a pressure host, so it sits still"
        )
        self.assertGreater(heading["letters"], 0, "the pressure port built no letters")

    # -------------------------------------------------------------- the panels -- */

    def test_every_panel_is_adorned_without_moving_its_own_box(self) -> None:
        """The port draws on a panel rather than rebuilding it.

        All seven panels, in the view where all seven are laid out, with the layer it adds
        out of flow and the panel's own display, padding, border and radius unchanged when
        the class is taken off — which is the claim that makes this port applicable to
        markup it did not write.
        """
        self.assertTrue(self.feed["ready"], "the feed did not reach its first rows")
        self.assertEqual([panel["name"] for panel in self.feed["panels"]], PANELS)

        for panel in self.feed["panels"]:
            with self.subTest(panel=panel["name"]):
                self.assertTrue(panel["glow"], "the panel is not wearing the port's class")
                self.assertEqual(panel["layers"], 1, "a panel gets exactly one edge layer")
                self.assertEqual(panel["edge"]["position"], "absolute", "the layer is a flex item")
                self.assertEqual(panel["edge"]["ariaHidden"], "true", "decoration is announced")
                self.assertEqual(panel["position"], "relative")
                self.assertEqual(panel["isolation"], "isolate", "the layers would paint over the page")
                self.assertEqual(panel["radius"], "4px", "the corner is the panel's own, not a card's")
                self.assertEqual(
                    panel["glowPadding"],
                    0,
                    "the halo reaches outside the panel, where a scroll container would cut it",
                )

        self.assertEqual(
            self.feed["clippingAncestors"],
            [],
            "a panel's ancestor clips, so the halo is being cut off",
        )

        layout = self.feed["layoutUnchanged"]
        self.assertEqual([row["name"] for row in layout], PANELS)
        for row in layout:
            with self.subTest(panel=row["name"]):
                self.assertTrue(row["same"], f"{row['before']} became {row['after']}")
                self.assertEqual(row["displayWithClass"], row["displayWithoutClass"])
                self.assertEqual(row["childrenBefore"], row["childrenAfter"], "the port moved a child")

    def test_the_analyst_column_can_be_read_from_top_to_bottom(self) -> None:
        """Every panel of the analyst column is reachable while the commentary scrolls.

        A pinned box covers whatever follows it in its own column. The rail used to stand
        after the pin — the check and the ledger — so the metrics and the line-ups slid up
        underneath them as the page scrolled and could not be read at all. The rail moved
        inside the pin, and the pin became the column's own scrollport: all four panels are
        taller than a laptop window, so a pin without a scroll of its own would simply hide
        its bottom below the fold instead.

        Both halves are asked here, with the page scrolled to where the pin is stuck and
        the column's scroll walked: is every panel wholly inside the column at some
        position, and did the point inside it belong to the panel or to something drawn
        over it. A panel that is on screen but underneath another is exactly the state this
        test exists to catch, and a panel never wholly visible is one a reader with this
        window has no way of reading.
        """
        column = self.feed["column"]
        self.assertTrue(
            column["railInsidePin"],
            "the rail is not inside the pinned column, so the pin covers it as the page scrolls",
        )
        self.assertGreater(column["scrollRange"], 0, "the feed does not scroll, so nothing was measured")
        self.assertIn(
            column["overflow"],
            ("auto", "scroll"),
            "the pin is not a scrollport, so its lower panels are below the fold forever",
        )
        self.assertGreater(
            column["scroll"],
            column["client"],
            "the column has nothing to scroll, so this window measured nothing",
        )
        self.assertGreater(
            column["client"], 0, "the column has no height, so the view switch did not happen"
        )
        self.assertEqual(
            [panel["name"] for panel in column["panels"]],
            ["check", "ledger", "metrics", "squads"],
            "the analyst column is not the four panels the page says it is",
        )

        for panel in column["panels"]:
            with self.subTest(panel=panel["name"]):
                self.assertTrue(
                    panel["whole"],
                    f"{panel['name']} is never wholly inside the column at any scroll position",
                )
                self.assertGreater(panel["positions"], 0, f"{panel['name']} was never measured")
                self.assertEqual(
                    panel["covered"],
                    0,
                    f"{panel['name']} was covered at {panel['covered']} of its on-screen positions",
                )

        # A scrollport only a pointer can scroll is half a control, so the column carries
        # a tab stop while it has something to scroll — and the whole point of it is that
        # Safari, which does not focus scroll containers by itself, gets the same control.
        self.assertEqual(column["tabindex"], "0", "the column cannot be reached by keyboard")
        self.assertEqual(column["role"], "region")
        self.assertTrue(column["label"], "the focusable column has no accessible name")

        # And it is scrolled by the gesture a reader makes, not only by `scrollTop`.
        wheel = column["wheel"]
        self.assertGreater(wheel["columnMoved"], 0, "the wheel over the column does not scroll it")
        self.assertTrue(
            wheel["pageMoved"],
            "the wheel at the end of the column is swallowed instead of scrolling the page",
        )

    # ------------------------------------------------------- the planted fault -- */

    def test_the_board_names_the_planted_fault(self) -> None:
        """The fault is the product: the board must name it, and the clean page must not.

        A planted fault presented as a natural error would make the catch unfalsifiable —
        the planting would be done by the same code reporting the catch — so the row says
        "planted fault" in its own tag, and its why-line says the planting was deliberate.
        The un-faulted match, stepped to its last window the same way, shows nothing planted
        anywhere. The chip's own rule is held on both pages: a hidden chip carries no text,
        and a visible chip reads "planted fault" and nothing else.
        """
        faulted = self.report["fault"]["faulted"]
        self.assertTrue(faulted["ready"], "the faulted match page did not load")
        self.assertTrue(
            faulted["found"],
            f"the faulted match revealed no planted row (plantedCount {faulted['plantedCount']})",
        )
        row = faulted["row"] or {}
        self.assertIn(
            "planted fault",
            row.get("tags", []),
            f"the planted row is not labelled planted fault: {row.get('tags')!r}",
        )
        self.assertIn(
            "row--rejected",
            row.get("classes", ""),
            "a planted fault is a rejected row; a planted label on any other kind is ambiguous",
        )
        self.assertTrue(
            row.get("why", ""),
            "the planted row gives no reason; a rejected line without one reads as an opinion",
        )
        self.assertIn(
            "but the claim says",
            row.get("why", ""),
            f"the planted row does not show the discrepancy against the claim: {row.get('why')!r}",
        )

        clean = self.report["fault"]["clean"]
        self.assertTrue(clean["ready"], "the clean match page did not load")
        self.assertFalse(
            clean["found"],
            f"the clean match planted {clean['plantedCount']} row(s); a faulted page only",
        )

        for visit, board in self.report["fault"].items():
            with self.subTest(visit=visit):
                self.assertEqual(
                    board["chipHidden"],
                    not board["chipText"],
                    f"the check chip on the {visit} page carries an empty label",
                )
                if not board["chipHidden"]:
                    self.assertEqual(
                        board["chipText"],
                        "planted fault",
                        f"the check chip on the {visit} page says {board['chipText']!r}",
                    )

    # ---------------------------------------------------------- the team sheet -- */

    def test_the_team_sheet_shows_both_benches(self) -> None:
        """Eleven and five, per side, with the role that says which is which.

        A roster that named only the starters would be a team sheet with half its team
        missing; a bench folded into the starting list would read as sixteen players on
        the pitch. The positions are asserted with them, because they were in the payload
        and the panel used to print only the shirt and the name.
        """
        sheet = self.feed["teamSheet"]
        self.assertEqual(sheet["teams"], 2, "the panel does not show two teams")
        self.assertEqual(sheet["roles"].count("Starting XI"), 2)
        self.assertEqual(sheet["roles"].count("Substitutes"), 2)
        self.assertEqual(sheet["starters"], 22, "a side is not showing eleven starters")
        self.assertEqual(sheet["substitutes"], 10, "the benches are missing or the wrong size")
        self.assertEqual(sheet["positions"], 32, "some names are shown without a position")
        self.assertEqual(
            len(set(sheet["names"])),
            sheet["substitutes"],
            "the bench repeats a name",
        )

    # -------------------------------------------------------- the goal chooser -- */

    def test_a_reader_can_watch_any_goal_the_match_has_reached(self) -> None:
        """One chip per goal, one pressed, and a click on another changes the move.

        The panel used to open only the newest build-up. The dock had a menu of goals,
        which is the other corner of the page and not where a reader is when they are
        looking at the pitch. The chooser is asserted through a real mouse click and the
        title the panel then carries, because a chip that only looks pressable is the
        failure this is here to catch.
        """
        highlight = self.feed["highlight"]
        self.assertTrue(highlight["opened"], "the build-up panel did not open")
        self.assertGreaterEqual(
            highlight["goals"], 2, "the default match offers no choice of build-up"
        )
        self.assertEqual(highlight["pressed"], 1, "not exactly one goal is marked as open")
        self.assertEqual(
            len(set(highlight["labels"])),
            highlight["goals"],
            "two chips describe the same goal",
        )
        self.assertTrue(highlight["switched"], "the first chip could not be clicked")
        self.assertNotEqual(
            highlight["afterTitle"],
            highlight["beforeTitle"],
            "choosing a goal did not change the panel",
        )
        self.assertEqual(
            highlight["afterPressed"],
            highlight["firstChip"],
            "the goal that was chosen is not the one marked as open",
        )
        self.assertTrue(
            highlight["focusKept"],
            "the keyboard was dropped to the page when the panel switched goals",
        )
        self.assertTrue(highlight["dismissed"], "the panel did not close")

    def test_the_nav_fetches_the_other_page_before_the_click(self) -> None:
        """A real hover on the catalogue link adds a prefetch for it to the head.

        Reading the file would only say the code exists; this is the browser saying a
        pointer arriving on the link started the next document, which is the half of the
        navigation cost that used to land after the click.
        """
        self.assertTrue(
            self.feed["prefetch"],
            "hovering the catalogue link prefetched nothing, so the click pays for the page",
        )

    # ----------------------------------------------------------- the one light -- */

    def test_the_light_reaches_every_panel_by_distance(self) -> None:
        """One pointer, seven panels, and a brightness that is the distance to it.

        The pointer is three pixels inside the feed's left boundary. The feed has to be at
        full strength, the panels around it lit in proportion to how near they are — which
        is what upstream, where each panel decides for itself, cannot do: a panel that is
        not hovered is not lit at all there — and the panels out of reach dark.
        """
        reading = self.feed["light"]
        self.assertFalloffIsDistance(reading, "pointer on the feed")
        self.assertConeFacesThePointer(reading, self.feed["pointer"], "pointer on the feed")

        under = [panel for panel in reading if panel["containsPointer"]]
        self.assertEqual([panel["name"] for panel in under], ["feed#feed"], "the pointer is not on the feed")
        self.assertEqual(float(under[0]["falloff"]), 1.0, "the panel the pointer is in is not at full strength")

        others = sorted(
            (panel for panel in reading if not panel["containsPointer"]),
            key=lambda panel: panel["distance"],
        )
        falloffs = [float(panel["falloff"]) for panel in others]
        self.assertEqual(falloffs, sorted(falloffs, reverse=True), "a nearer panel is lit less")

        # The lamp has to cross the gutter: a page where only the panel under the pointer is
        # lit is the port upstream ships, with a falloff written and never used.
        neighbours = [panel for panel in others if 0 < float(panel["falloff"]) < 1]
        self.assertGreaterEqual(len(neighbours), 2, "the light does not reach past the panel it is in")
        for panel in neighbours:
            with self.subTest(neighbour=panel["name"]):
                self.assertGreater(panel["ringOpacity"], 0.05, "the falloff reached no layer")
                self.assertGreater(panel["haloOpacity"], 0.05)

        for panel in others:
            if float(panel["falloff"]) == 0:
                with self.subTest(dark=panel["name"]):
                    self.assertEqual(panel["ringOpacity"], 0, "a panel out of reach is still lit")
                    self.assertEqual(panel["haloOpacity"], 0)

    def test_the_light_leaves_a_panel_it_is_no_longer_near(self) -> None:
        """A panel that was lit goes dark when the light moves away from it.

        The same seven panels, read with the pointer parked in the far corner: the feed has
        to be unlit, and whatever is lit has to be lit by the same rule as before. A panel
        that kept the last reading it was given would be a lamp that never went out.
        """
        away = self.feed["away"]
        self.assertFalloffIsDistance(away, "pointer parked")
        self.assertConeFacesThePointer(away, {"x": self.report["viewport"]["width"] - 3, "y": 3}, "pointer parked")

        feed = next(panel for panel in away if panel["name"] == "feed#feed")
        self.assertEqual(float(feed["falloff"]), 0.0, "the feed stayed lit after the pointer left it")
        self.assertEqual(feed["ringOpacity"], 0)
        self.assertGreater(
            max(float(panel["falloff"]) for panel in away),
            0,
            "the pointer is on the page and nothing is lit at all",
        )

    # ------------------------------------------------------------ the keyboard -- */

    def test_the_keyboard_lights_the_panel_it_is_in(self) -> None:
        """A reader with no pointer can see which panel they are in.

        The pointer is parked in the far corner, where the feed's own share of the light is
        nothing, and the panel is then read with a control inside it focused: full strength,
        the cone aimed at the control, and every layer drawn. Tab away and it goes out.
        """
        keyboard = self.feed["keyboard"]
        self.assertEqual(keyboard["focused"], "promo-btn", "nothing took focus")

        parked = next(panel for panel in keyboard["parked"] if panel["name"] == "feed#feed")
        self.assertEqual(float(parked["falloff"]), 0.0, "the light was on the feed all along")

        self.assertEqual(keyboard["panel"]["lit"], "true", "focus did not mark the panel")
        self.assertEqual(float(keyboard["panel"]["falloff"]), 1.0, "focus did not light it fully")
        self.assertGreater(keyboard["panel"]["ringOpacity"], 0.5, "focus lit nothing on screen")

        self.assertIsNone(keyboard["afterBlur"]["lit"], "the panel stayed lit after the blur")
        self.assertEqual(float(keyboard["afterBlur"]["falloff"]), 0.0, "the light did not go back to the pointer")

    # --------------------------------------------------------------- the bands -- */

    def test_the_boundary_facing_the_pointer_is_the_brightest(self) -> None:
        """The pixels, rather than the properties: which boundary lit up.

        Four strips of the feed are captured with the light off the page, again with nothing
        moved (the page is emulated as reduced motion, so it holds still and any difference
        there is the page animating on its own), and finally with the pointer on the left
        boundary. The strip on that boundary is the one that changes and the strip on the
        opposite one — the same rows, the boundary the cone is not masked to — is not.
        """
        bands = self.feed["bands"]
        feed = self.feed["feedBox"]

        for name in ("leftEdge", "rightEdge", "interior"):
            with self.subTest(band=name):
                self.assertEqual(
                    bands[name]["idleBaseline"]["changed"],
                    0,
                    "the page moved between two frames with nothing moving it",
                )
                self.assertEqual(bands[name]["warm"]["size"], bands[name]["idleBaseline"]["size"])

        # The strips are where the test thinks they are: a 10px column of the feed's own
        # left boundary, its right boundary, and a column three hundred pixels in.
        self.assertLessEqual(abs(bands["clip"]["leftEdge"]["x"] - feed["left"]), 4)
        self.assertLessEqual(abs(bands["clip"]["rightEdge"]["x"] - (feed["right"] - 11)), 4)
        self.assertEqual(bands["clip"]["leftEdge"]["height"], bands["clip"]["rightEdge"]["height"])

        left = bands["leftEdge"]["warm"]
        right = bands["rightEdge"]["warm"]
        interior = bands["interior"]["warm"]

        self.assertGreaterEqual(left["share"], 0.8, "the lit boundary barely changed")
        self.assertGreater(left["meanChannelWarm"] - left["meanChannelCold"], 30, "the boundary did not brighten")
        self.assertLess(right["meanDelta"], left["meanDelta"] / 5, "the far boundary lit up as well")
        self.assertLess(interior["meanDelta"], left["meanDelta"] / 5, "the panel's interior lit up")

    # ------------------------------------------------------------ the catalogue -- */

    def test_the_catalogue_draws_a_sphere_whose_card_names_a_match(self) -> None:
        """The front door: the sphere is drawn, its card is a match, and its address opens it.

        The sphere is a WebGL buffer that nothing in the DOM describes, so "it drew" is
        measured in pixels — an empty stage is one colour. The address is the page's own,
        followed rather than composed: the card offers ``/match/<key>``, and the harness
        fetches the replay behind that key and the page at that address.

        A browser with no WebGL2 skips the drawing half and asserts the other path instead.
        That is the page working as designed — the fallback list *is* the interface on such a
        browser, and it is a real path in the port rather than a message — so failing here
        would be a red build for a machine, not a bug. What is left is the claim that still
        holds either way: the front door is up, the stage was filled by one of the two, and
        the catalogue was counted. Which of the two was built is measurable, and the run
        reports it as a skip so a CI log cannot quietly lose the sphere.
        """
        catalogue = self.report["catalogue"]
        self.assertTrue(catalogue["ready"], "the stage never filled")
        self.assertEqual(catalogue["path"], "/")
        self.assertIn("matches", catalogue["count"], "the page did not count the catalogue")

        if not catalogue["webgl2"]:
            self.assertTrue(catalogue["fallback"], "WebGL2 is absent and the list was not built either")
            self.assertNotEqual(catalogue["cards"]["rows"], [], "the fallback list is empty")
            self.assertIn("no WebGL2", catalogue["status"], "nothing on the page says which was built")
            # The list is up and the reader can actually use it. Measured rather than read off
            # the stylesheet, because the way this path breaks is invisible: an empty canvas
            # left over the list looks exactly like a list, and takes every click aimed at it.
            hit = catalogue["fallbackHitTest"]
            self.assertIsNotNone(hit, "the fallback list was built and nothing asked whether it works")
            self.assertTrue(hit["reachable"], f"{hit['topmost']} is over the first row, not the row")
            self.skipTest("this browser has no WebGL2, so there is no sphere to measure")

        self.assertTrue(catalogue["canvas"], "WebGL2 is available and the sphere was not built")
        self.assertFalse(catalogue["fallback"])
        self.assertGreater(catalogue["stagePixels"]["colours"], 100, "the stage is an empty rectangle")
        self.assertGreater(catalogue["stagePixels"]["brightestSum"], 200, "the stage is black")

        front = catalogue["cards"]["cards"]
        self.assertEqual(len(front), 1, "the sphere shows one card at a time")
        self.assertRegex(front[0]["href"], r"^/match/[a-z0-9][a-z0-9-]*$", "a card with no match in its address")

        arrows = catalogue["arrows"]
        self.assertNotEqual(arrows["before"], arrows["after"], "the sphere did not answer the arrow keys")
        self.assertRegex(arrows["after"], r"^/match/[a-z0-9][a-z0-9-]*$")

        for address in catalogue["addresses"]:
            with self.subTest(address=address["href"]):
                self.assertEqual(address["replayStatus"], 200)
                self.assertIsInstance(address["seed"], int)
                self.assertIn(" v ", address["match"])
                self.assertEqual(address["pageStatus"], 200)
                self.assertIn("text/html", address["contentType"])
                self.assertEqual(address["pagePath"], "feed", "the address a card offers is not the feed")

    # -------------------------------------------------------------- the pressure -- */

    def test_a_face_with_no_axes_is_reported_flat(self) -> None:
        """The pressure type's effect is a variable font answering an axis, and a face with
        none is the failure that looks like a page working: the settings are written, the
        face ignores them, and the word is simply still.

        So the port was mounted on its own host in the machine's own fallback faces and
        asked what it found. Where the axes are empty it has to say so — `data-pressure`
        off the element, not a flag inside the module — write no settings at all, and still
        leave the word readable once for a screen reader. Where a face does answer an axis
        the two agree the other way: `live`, and something written on the letters.

        Which of the three candidates vary is the machine's answer, so a run whose faces
        all vary skips rather than failing: that would be a red build for a font, not a bug.
        """
        readings = self.report["pressure"]
        self.assertTrue(readings, "the pressure probe produced no readings")

        flat = [reading for reading in readings if not reading["axes"]]
        if not flat:
            self.skipTest("every face this machine offers answers a variation axis")

        for reading in flat:
            with self.subTest(family=reading["family"]):
                self.assertEqual(
                    reading["pressure"],
                    "flat",
                    f"{reading['family']} has no axes and the port did not say so",
                )
                self.assertEqual(reading["written"], 0, "settings were written against a face that ignores them")
                self.assertEqual(reading["sample"], "")
                # The word survives the fallback: the same text, once, for a reader.
                self.assertEqual(reading["say"], "Flat face")
                self.assertEqual(reading["letters"], len("Flat face"))

        for reading in readings:
            with self.subTest(family=reading["family"]):
                self.assertEqual(reading["pressure"], "live" if reading["axes"] else "flat")
                if reading["axes"]:
                    self.assertIn("wght", reading["axes"], "a live reading without the weight axis")
                    self.assertGreater(reading["written"], 0, "a live reading wrote nothing on its letters")

    # ------------------------------------------------------------------ errors -- */

    def test_neither_page_reported_a_problem(self) -> None:
        """No exception, no console error and no failed request on either page.

        The glass on a canvas port is easy to miss and hard to notice: a port that throws
        inside its own frame loop leaves a page that looks right and has stopped moving. The
        driver collects these as they happen, per page, so a failure names the page it was
        on. The faces are requested from a CDN and never waited on, so a failure to reach
        them is filtered out where it is collected rather than forgiven here. When the run
        exited nonzero because of them, what the driver said is quoted with the failure
        rather than left in a log nobody opens.
        """
        self.assertEqual(self.report["problems"], [], self.diagnostics)


if __name__ == "__main__":
    unittest.main()
