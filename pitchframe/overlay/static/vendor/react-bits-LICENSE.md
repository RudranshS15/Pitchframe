MIT + Commons Clause License Condition v1.0

Copyright (c) 2026 David Haz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, and distribute the Software **as part of an
application, website, or product**, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

## Commons Clause Restriction

You may use this Software, including for any commercial purpose, **so long as you do not
sell, sublicense, or redistribute the components themselves — whether alone, in a bundle,
or as a ported version.**

## No Warranty

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

This is the licence of the React Bits components whose ports live beside this file, all
from https://reactbits.dev:

| port | component |
|------|-----------|
| `static/border-glow.js` | BorderGlow |
| `static/rubber-segment.js` | RubberSegment |
| `static/dock.js` | Dock |
| `static/wake-slider.js` | WakeSlider |
| `static/sling-button.js` | SlingButton |
| `static/infinite-menu.js` | InfiniteMenu |
| `static/slats.js` | MicroSlats |

It travels with each of them because the notice above has to be included in copies of the
software, and because the Commons Clause names ports explicitly: those files are part of
this application and are not redistributed as components on their own. Each ported file
carries the notice in its own header as well, so a file lifted out of this repository
without its directory still arrives with the terms attached.

`static/vendor/ogl.mjs` is not one of them: it is the vendored WebGL library MicroSlats
depends on, and it carries its own terms in its own header — Unlicense, which asks for
nothing of this kind.
