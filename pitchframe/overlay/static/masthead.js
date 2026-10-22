/* Pitchframe overlay — the masthead's two shared pieces.
 *
 * Both documents carry the same two things that are not about the document they are in:
 * the wordmark, and the way to the other one. So they are built once, here, and each page
 * only writes the markup they are built on — the brand's own word and the nav's own links.
 * Neither is a control this page invents: the nav is the page's own `<a href>`s with a
 * component drawn over them, and the wordmark is the page's own text with its letters
 * asked for a weight the face can answer. A browser without the script, without WebGL2,
 * or without a variable face is left with a wordmark, two links and no apology.
 *
 * The nav is mounted on `[data-site-nav]` and reads its items from the list inside it, so
 * a page that adds a destination adds it to the markup in one place. The wordmark is
 * mounted on `[data-wordmark]` and reads its text from the element, so the name is
 * written once per page and spelled by the page, not by this file.
 */

import { createGooeyNav } from "./gooey-nav.js";
import { createTextPressure } from "./text-pressure.js";

const nav = document.querySelector("[data-site-nav]");
if (nav) {
  createGooeyNav(nav, {
    size: "sm",
    /* Two items, so a short throw is enough: the droplets travel between neighbouring
       links rather than across a gallery's row. */
    spread: 42,
    particleCount: 12,
    /* Short enough to read as a reaction rather than a wait: the address is followed
       after a sixth of this (gooey-nav.js), about 76ms. */
    animationTime: 420
  });
}

const wordmark = document.querySelector("[data-wordmark]");
if (wordmark) {
  createTextPressure(wordmark, {
    /* The masthead's type is the page's own size. `fit` would size the word to fill its
       container, and a wordmark inside a masthead is exactly the case where the container
       is the word. */
    fit: false,
    radius: 0.55,
    smoothing: 0.35,
    clickRipple: false,
    /* Oswald's weight axis runs 200–700 and the wordmark sits at 500 in the stylesheet.
       Resting at 400 and swelling to the bold end is the same pressure upstream applies,
       mapped into the range the face actually has instead of pinning it at both ends. */
    weightRange: [400, 700]
  });
}
