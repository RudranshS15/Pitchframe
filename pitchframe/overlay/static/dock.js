/* Dock — a magnifying bar of tiles, with item menus, badges and indicator dots.
 *
 * A port of the React Bits component of the same name (https://reactbits.dev), with the
 * React half replaced by a module lifecycle: `createDock(host, options)` returns
 * `{ set, destroy }`. The magnification spring, the hop on click, the flick projection,
 * the long press, the menu geometry with its tail, the keyboard order and the dismissal
 * rules are the component's own; this is a transcription rather than a redesign.
 *
 * Ported from React Bits, Copyright (c) 2026 David Haz.
 * MIT + Commons Clause License Condition v1.0. The full notice ships beside this file as
 * vendor/react-bits-LICENSE.md, and the Commons Clause covers "a ported version" as well as
 * the components themselves: this file is part of this application and is not redistributed
 * on its own.
 *
 * Five deliberate differences from the component, each because this page is not a React
 * app:
 *
 *   - No React, and no portal. A menu is appended to `document.body` directly, which is
 *     what the portal was for: a fixed-position panel that no ancestor can clip.
 *   - `useReducedMotion` becomes a watched media query (./motion.js), so turning the
 *     setting on stops a swell that is already travelling.
 *   - The dark theme is restated in the page's own stylesheet, under `.dock`, rather than
 *     set inline from the component's `THEMES`. Colour belongs where the rest of this
 *     page's colour is, and the component's palette is a different page's. Passing `theme`
 *     or `tokens` still overrides it and is how both shipped themes stay reachable.
 *   - The sample in the component's documentation draws its glyphs with `@hugeicons/react`.
 *     This page has no icon dependency, so icons are whatever the caller passes; the two
 *     glyphs the menu itself needs are inline SVG below.
 *   - `set({ items })` updates in place when the items keep their keys, rather than
 *     rebuilding the bar. A rebuild would drop the swell under the pointer, and this dock
 *     is rebuilt whenever the replay crosses a goal.
 */

import { prefersReducedMotion, watchReducedMotion } from "./motion.js";

const SIDES = ["bottom", "top", "left", "right"];
const EDGE = 12;
const HOP = 0.4;
const HOP_TIME = 0.38;
const HOLD_TIME = 450;
const TAIL = 9;
const TAIL_HALF = 9;
const MENU_RADIUS = 10;
const MENU_GAP = 8;
const MARGIN = 8;
const POP = "cubic-bezier(0.2, 1.25, 0.4, 1)";
const CLOSE_MS = 150;
const PRESS_SLOP = 8;

const THEMES = {
  dark: {
    "--dock-panel": "rgba(38, 35, 46, 0.66)",
    "--dock-panel-edge": "rgba(255, 255, 255, 0.08)",
    "--dock-panel-highlight": "rgba(255, 255, 255, 0.07)",
    "--dock-panel-shadow": "0 22px 44px -20px rgba(0, 0, 0, 0.8), 0 6px 16px -8px rgba(0, 0, 0, 0.5)",
    "--dock-tile": "linear-gradient(180deg, #3a3644 0%, #27242f 100%)",
    "--dock-tile-edge": "rgba(255, 255, 255, 0.07)",
    "--dock-tile-highlight": "rgba(255, 255, 255, 0.12)",
    "--dock-tile-shadow": "0 4px 10px -4px rgba(0, 0, 0, 0.7)",
    "--dock-ink": "#f4f4f5",
    "--dock-label": "#2a2731",
    "--dock-label-ink": "#f4f4f5",
    "--dock-dot": "rgba(255, 255, 255, 0.72)",
    "--dock-separator": "rgba(255, 255, 255, 0.12)",
    "--dock-press": "brightness(0.75)",
    "--dock-menu": "rgba(40, 37, 47, 0.8)",
    "--dock-menu-edge": "rgba(255, 255, 255, 0.13)",
    "--dock-menu-ink": "#f4f4f5",
    "--dock-menu-muted": "rgba(244, 244, 245, 0.42)",
    "--dock-menu-separator": "rgba(255, 255, 255, 0.1)",
    "--dock-menu-shadow": "0 18px 40px -12px rgba(0, 0, 0, 0.65), 0 3px 10px rgba(0, 0, 0, 0.3)"
  },
  light: {
    "--dock-panel": "rgba(240, 240, 243, 0.8)",
    "--dock-panel-edge": "rgba(24, 24, 27, 0.07)",
    "--dock-panel-highlight": "rgba(255, 255, 255, 0.95)",
    "--dock-panel-shadow": "0 22px 44px -22px rgba(24, 24, 27, 0.3), 0 6px 16px -10px rgba(24, 24, 27, 0.16)",
    "--dock-tile": "linear-gradient(180deg, #ffffff 0%, #f6f6f8 100%)",
    "--dock-tile-edge": "rgba(24, 24, 27, 0.08)",
    "--dock-tile-highlight": "rgba(255, 255, 255, 1)",
    "--dock-tile-shadow": "0 4px 10px -5px rgba(24, 24, 27, 0.25)",
    "--dock-ink": "#27272a",
    "--dock-label": "#ffffff",
    "--dock-label-ink": "#18181b",
    "--dock-dot": "rgba(24, 24, 27, 0.55)",
    "--dock-separator": "rgba(24, 24, 27, 0.12)",
    "--dock-press": "brightness(0.9)",
    "--dock-menu": "rgba(248, 248, 250, 0.86)",
    "--dock-menu-edge": "rgba(24, 24, 27, 0.1)",
    "--dock-menu-ink": "#18181b",
    "--dock-menu-muted": "rgba(24, 24, 27, 0.4)",
    "--dock-menu-separator": "rgba(24, 24, 27, 0.1)",
    "--dock-menu-shadow": "0 18px 40px -14px rgba(24, 24, 27, 0.3), 0 3px 10px rgba(24, 24, 27, 0.1)"
  }
};

const SHAPE = '<span class="dock-menu-shadow"></span><span class="dock-menu-glass"></span>' +
  '<svg class="dock-menu-shape" aria-hidden="true"><path/></svg>';
const CHECK_GLYPH =
  '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2">' +
  '<path d="M3.5 8.5l3 3 6-7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const CHEVRON_GLYPH =
  '<svg viewBox="0 0 16 16" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2">' +
  '<path d="M6 3.5L10.5 8 6 12.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

const hasBadge = (badge) => badge !== undefined && badge !== null && badge !== false;
const usable = (row) => Boolean(row) && !row.separator && !row.disabled;
const clamp = (value, min, max) => Math.min(Math.max(value, min), Math.max(min, max));

const menuPath = (width, height, side, at) => {
  const r = MENU_RADIUS;
  const p = (x, y) => `${(x + TAIL).toFixed(2)} ${(y + TAIL).toFixed(2)}`;
  const tail = (x, y, dx, dy, nx, ny) => {
    const q = (u, v) => p(x + dx * u + nx * v, y + dy * u + ny * v);
    return `L ${q(-TAIL_HALF, 0)} L ${q(-TAIL_HALF * 0.18, TAIL * 0.82)} Q ${q(0, TAIL)} ${q(TAIL_HALF * 0.18, TAIL * 0.82)} L ${q(TAIL_HALF, 0)}`;
  };
  return [
    `M ${p(r, 0)}`,
    side === "top" ? tail(at, 0, 1, 0, 0, -1) : "",
    `L ${p(width - r, 0)} A ${r} ${r} 0 0 1 ${p(width, r)}`,
    side === "right" ? tail(width, at, 0, 1, 1, 0) : "",
    `L ${p(width, height - r)} A ${r} ${r} 0 0 1 ${p(width - r, height)}`,
    side === "bottom" ? tail(at, height, -1, 0, 0, 1) : "",
    `L ${p(r, height)} A ${r} ${r} 0 0 1 ${p(0, height - r)}`,
    side === "left" ? tail(0, at, 0, -1, -1, 0) : "",
    `L ${p(0, r)} A ${r} ${r} 0 0 1 ${p(r, 0)} Z`
  ].join(" ");
};

const glyph = (markup) => {
  const template = document.createElement("template");
  template.innerHTML = markup;
  return template.content.firstElementChild;
};

export function createDock(host, options = {}) {
  const side = SIDES.includes(options.position) ? options.position : "bottom";
  const vertical = side === "left" || side === "right";
  const rawBase = Math.max(16, options.baseItemSize || 50);
  const rawPeak = Math.max(rawBase, options.magnification || 70);
  const rawThickness = Math.max(rawBase, options.panelHeight || 68);
  const rawGap = Math.max(0, options.gap === undefined ? 10 : options.gap);
  const roundness = clamp(options.roundness === undefined ? 0.5 : options.roundness, 0, 1);
  const reach = Math.max(1, options.distance === undefined ? 200 : options.distance);
  const spring = options.spring || { mass: 0.1, stiffness: 150, damping: 12 };
  const mass = Math.max(0.01, spring.mass === undefined ? 0.1 : spring.mass);
  const stiffness = Math.max(1, spring.stiffness === undefined ? 150 : spring.stiffness);
  const damping = Math.max(0, spring.damping === undefined ? 12 : spring.damping);
  const bouncy = options.bounce !== false;
  const tiles = options.tiles !== false;
  const showLabels = options.showLabels !== false;
  const showIndicators = options.showIndicators !== false;
  const showBadges = options.showBadges !== false;
  let autoHide = Boolean(options.autoHide);

  /* Inline tokens are the exception, not the rule: with none given, the stylesheet's
     palette stands, which is where this page keeps its colour. */
  const inlineTokens = {
    ...(options.tokens || (options.theme ? THEMES[options.theme] || THEMES.dark : {})),
    ...(options.accentColor ? { "--dock-accent": options.accentColor } : {}),
    ...(options.badgeColor ? { "--dock-badge": options.badgeColor } : {})
  };

  let items = options.items || [];
  let nodes = [];
  let alive = true;
  let raf = 0;
  let last = 0;
  let hideTimer = 0;
  let serial = 0;
  let menu = null;
  let stopWatchingOutside = null;
  let skip = -1;
  let focused = -1;
  let held = null;
  let lastFocus = null;
  let lastRest = [];
  let press = null;
  /* The attribute is already set below when the bar starts hidden, so the flag has to
     agree with it: a `hidden` of false over an attribute that is set means show() returns
     early for ever and the bar never comes back. */
  let hidden = autoHide;
  let hiding = autoHide;
  let reduce = prefersReducedMotion();

  let fit = 1;
  let base = rawBase;
  let peak = rawPeak;
  let thickness = rawThickness;
  let gap = rawGap;
  let scaledReach = reach;
  let lastBase = rawBase;
  const pointer = { x: 0, y: 0, inside: false };
  const motion = new Map();

  /* --------------------------------------------------------------------- the DOM -- */

  const root = document.createElement("div");
  root.className = `dock dock--${side}${tiles ? "" : " dock--bare"}${options.className ? ` ${options.className}` : ""}`;
  root.setAttribute("role", "toolbar");
  root.setAttribute("aria-label", options.label || "Dock");
  root.setAttribute("aria-orientation", vertical ? "vertical" : "horizontal");
  for (const [name, value] of Object.entries(inlineTokens)) root.style.setProperty(name, value);
  if (options.style) Object.assign(root.style, options.style);
  host.append(root);
  if (autoHide) root.setAttribute("data-hidden", "");

  const itemKey = (item, index) => (item && item.key !== undefined ? String(item.key) : `#${index}`);
  const openIndex = () => (menu && !menu.closing ? menu.index : -1);

  function place(index, node) {
    const previous = nodes[index];
    if (previous) previous.replaceWith(node);
    else if (nodes[index + 1]) nodes[index + 1].before(node);
    else root.append(node);
    nodes[index] = node;
  }

  function motionState(index) {
    let state = motion.get(index);
    if (!state) {
      state = {
        size: base, velocity: 0, lift: 0, rise: 0, launch: 0, gravity: 0,
        hopping: false, drawn: "", node: null
      };
      motion.set(index, state);
    }
    return state;
  }

  function paintItem(index) {
    const item = items[index];
    if (!item) return;

    if (item.separator) {
      const existing = nodes[index];
      if (existing && existing.classList.contains("dock-separator")) return;
      const separator = document.createElement("span");
      separator.className = "dock-separator";
      separator.setAttribute("aria-hidden", "true");
      place(index, separator);
      return;
    }

    let button = nodes[index];
    if (!button || !button.classList.contains("dock-item")) {
      button = document.createElement("button");
      button.type = "button";
      attachItemListeners(button);
      place(index, button);
    }
    button.dataset.index = String(index);
    button.className = `dock-item${item.className ? ` ${item.className}` : ""}`;
    button.setAttribute("aria-label", item.label || "");
    if (item.menu && item.menu.length) button.setAttribute("aria-haspopup", "menu");
    else button.removeAttribute("aria-haspopup");

    button.textContent = "";
    const tile = document.createElement("span");
    tile.className = "dock-tile";
    const face = document.createElement("span");
    face.className = "dock-face";
    if (item.icon) face.append(item.icon);
    tile.append(face);
    if (showBadges && hasBadge(item.badge)) {
      const badge = document.createElement("span");
      badge.className = "dock-badge";
      if (item.badge !== true) badge.textContent = item.badge;
      tile.append(badge);
    }
    button.append(tile);
    if (showIndicators && item.active) {
      const dot = document.createElement("span");
      dot.className = "dock-dot";
      dot.setAttribute("aria-hidden", "true");
      button.append(dot);
    }
    if (showLabels && item.label && !menu) {
      const label = document.createElement("span");
      label.className = "dock-label";
      label.setAttribute("aria-hidden", "true");
      label.textContent = item.label;
      button.append(label);
    }

    /* The contents were replaced, so whatever the loop drew last no longer applies. */
    const state = motionState(index);
    state.drawn = "";
    state.node = button;
    syncOpenState(index);
  }

  function syncOpenState(index) {
    const node = nodes[index];
    const item = items[index];
    if (!node || !item || item.separator || !node.classList.contains("dock-item")) return;
    const open = openIndex();
    if (item.menu && item.menu.length) node.setAttribute("aria-expanded", String(open === index));
    if (open === index) node.dataset.open = "";
    else delete node.dataset.open;
  }

  /* --------------------------------------------------------- the magnification --- */

  const wake = () => {
    if (raf || !alive) return;
    last = performance.now();
    raf = requestAnimationFrame(tick);
  };

  function tick(now) {
    raf = 0;
    if (!alive) return;
    const dt = Math.min(1 / 30, Math.max(0.001, (now - last) / 1000));
    last = now;

    /* A bar that had to shrink does not restart from the base size: every tile keeps its
       place in the swell and is scaled with it. */
    if (base !== lastBase) {
      const ratio = base / lastBase;
      motion.forEach((state) => {
        state.size *= ratio;
        state.velocity *= ratio;
      });
      lastBase = base;
    }

    const rect = root.getBoundingClientRect();
    const centre = vertical ? rect.top + rect.height / 2 : rect.left + rect.width / 2;
    const spans = items.map((item) => (item && item.separator ? 1 : base));
    let cursor = -(spans.reduce((sum, span) => sum + span, 0) + gap * Math.max(0, spans.length - 1)) / 2;
    const rest = spans.map((span) => {
      const middle = cursor + span / 2;
      cursor += span + gap;
      return middle;
    });

    let focus = null;
    if (held !== null) focus = held;
    else if (pointer.inside) focus = (vertical ? pointer.y : pointer.x) - centre;
    else if (rest[focused] !== undefined) focus = rest[focused];
    lastFocus = focus;
    lastRest = rest;

    const steps = Math.max(1, Math.ceil(dt * 240));
    const h = dt / steps;
    const k = stiffness / mass;
    const c = damping / mass;
    let moving = false;

    items.forEach((item, index) => {
      const node = nodes[index];
      if (!node || (item && item.separator)) return;
      const state = motionState(index);
      const away = focus === null ? 1 : Math.abs(focus - rest[index]) / scaledReach;
      const target = base + (peak - base) * (away < 1 ? Math.cos((away * Math.PI) / 2) ** 2 : 0);
      if (reduce) {
        state.size = target;
        state.velocity = 0;
      } else {
        for (let i = 0; i < steps; i++) {
          state.velocity = (state.velocity + h * k * (target - state.size)) / (1 + h * c + h * h * k);
          state.size += state.velocity * h;
          if (!state.hopping) continue;
          state.rise -= state.gravity * h;
          state.lift += state.rise * h;
          if (state.lift > 0) continue;
          state.lift = 0;
          state.rise *= -0.36;
          if (state.rise < state.launch * 0.2) {
            state.rise = 0;
            state.hopping = false;
            node.removeAttribute("data-hop");
          }
        }
      }
      if (Math.abs(state.velocity) < 0.01 && Math.abs(target - state.size) < 0.01) {
        state.size = target;
        state.velocity = 0;
      } else {
        moving = true;
      }
      if (state.hopping) moving = true;

      const size = Math.max(1, state.size);
      const drawn = `${size.toFixed(2)} ${state.lift.toFixed(2)} ${side}`;
      if (drawn === state.drawn && node === state.node) return;
      state.drawn = drawn;
      state.node = node;
      const lift = side === "bottom" || side === "right" ? -state.lift : state.lift;
      const tile = node.firstElementChild;
      node.style.width = `${size}px`;
      node.style.height = `${size}px`;
      if (tile) {
        tile.style.transform = `translate(${vertical ? `${lift}px, 0` : `0, ${lift}px`}) scale(${size / base})`;
      }
    });

    if (moving) raf = requestAnimationFrame(tick);
  }

  function hop(index) {
    const node = nodes[index];
    if (reduce || !bouncy || !node) return;
    const state = motionState(index);
    if (state.hopping) return;
    const height = state.size * HOP;
    state.gravity = (8 * height) / (HOP_TIME * HOP_TIME);
    state.launch = (state.gravity * HOP_TIME) / 2;
    state.rise = state.launch;
    state.hopping = true;
    node.setAttribute("data-hop", "");
    wake();
  }

  /* Holding a tile keeps the pointer's swell pinned to it while its menu is open: the
     menu must not slide away because the pointer left the bar. */
  function hold(index) {
    if (index === null) {
      held = null;
      wake();
      hideLater();
      return;
    }
    held = lastFocus !== null ? lastFocus : lastRest[index] !== undefined ? lastRest[index] : 0;
    show();
    wake();
  }

  const show = () => {
    window.clearTimeout(hideTimer);
    hideTimer = 0;
    if (!hidden) return;
    hidden = false;
    root.removeAttribute("data-hidden");
  };

  const hide = () => {
    window.clearTimeout(hideTimer);
    hideTimer = 0;
    const blocked = pointer.inside || held !== null || root.contains(document.activeElement);
    if (hidden || !autoHide || blocked) return;
    hidden = true;
    root.setAttribute("data-hidden", "");
  };

  const hideLater = () => {
    if (hidden || hideTimer || !autoHide) return;
    hideTimer = window.setTimeout(hide, 600);
  };

  const onRootMove = (event) => {
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    pointer.inside = true;
    show();
    wake();
  };
  const onRootLeave = () => {
    pointer.inside = false;
    wake();
    hideLater();
  };
  const onFocusOut = (event) => {
    if (!root.contains(event.relatedTarget)) hideLater();
  };
  const onWindowMove = (event) => {
    if (!autoHide || pointer.inside) return;
    const box = root.offsetParent;
    const rect = box
      ? box.getBoundingClientRect()
      : { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
    const zone = EDGE + thickness * 0.6;
    const x = event.clientX;
    const y = event.clientY;
    const within = x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
    const near = {
      bottom: y >= rect.bottom - zone,
      top: y <= rect.top + zone,
      left: x <= rect.left + zone,
      right: x >= rect.right - zone
    }[side];
    if (within && near) show();
    else hideLater();
  };

  root.addEventListener("pointermove", onRootMove);
  root.addEventListener("pointerdown", onRootMove);
  root.addEventListener("pointerleave", onRootLeave);
  root.addEventListener("pointercancel", onRootLeave);
  root.addEventListener("focusin", show);
  root.addEventListener("focusout", onFocusOut);
  window.addEventListener("pointermove", onWindowMove);
  window.addEventListener("pointerdown", onWindowMove);

  /* ------------------------------------------------------------------- the menus -- */

  function buildMenu(config) {
    const rows = config.rows || [];
    const nested = Boolean(config.nested);
    const checks = rows.some((row) => usable(row) && row.checked !== undefined);
    const localReduce = reduce;

    const outer = document.createElement("div");
    outer.className = "dock-menu";
    outer.setAttribute("data-dock-menu", "");
    for (const [name, value] of Object.entries(inlineTokens)) outer.style.setProperty(name, value);
    outer.innerHTML = SHAPE;
    const glass = outer.querySelector(".dock-menu-glass");
    const shape = outer.querySelector(".dock-menu-shape");
    const path = shape.querySelector("path");

    const list = document.createElement("div");
    list.className = "dock-menu-list";
    list.setAttribute("role", "menu");
    if (config.label) list.setAttribute("aria-label", config.label);
    list.tabIndex = -1;

    const highlight = document.createElement("span");
    highlight.className = "dock-menu-highlight";
    list.append(highlight);

    const rowNodes = [];
    let active = -1;
    let sub = null;
    let blink = false;
    let chosen = false;
    let ownAlive = true;
    let subTimer = 0;
    let closeTimer = 0;
    let frameRect = { left: 0, top: 0, width: 0, height: 0 };

    const layers = [outer.firstElementChild, glass, shape, list];

    rows.forEach((row, index) => {
      if (!row) return;
      if (row.separator) {
        const separator = document.createElement("span");
        separator.className = "dock-menu-separator";
        separator.setAttribute("role", "separator");
        list.append(separator);
        return;
      }
      const submenu = Boolean(row.items && row.items.length);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "dock-menu-row";
      button.setAttribute("role", row.checked !== undefined ? "menuitemcheckbox" : "menuitem");
      if (row.checked !== undefined) button.setAttribute("aria-checked", String(Boolean(row.checked)));
      if (row.disabled) button.setAttribute("aria-disabled", "true");
      if (submenu) {
        button.setAttribute("aria-haspopup", "menu");
        button.setAttribute("aria-expanded", "false");
      }
      button.tabIndex = -1;
      button.dataset.dockRow = String(index);

      if (checks) {
        const box = document.createElement("span");
        box.className = "dock-menu-check";
        if (row.checked) box.append(glyph(CHECK_GLYPH));
        button.append(box);
      }
      if (row.icon) {
        const iconBox = document.createElement("span");
        iconBox.className = "dock-menu-icon";
        iconBox.append(row.icon);
        button.append(iconBox);
      }
      const text = document.createElement("span");
      text.className = "dock-menu-text";
      text.textContent = row.label;
      button.append(text);
      if (row.shortcut) {
        const shortcut = document.createElement("span");
        shortcut.className = "dock-menu-shortcut";
        shortcut.textContent = row.shortcut;
        button.append(shortcut);
      }
      if (submenu) {
        const chevron = document.createElement("span");
        chevron.className = "dock-menu-chevron";
        chevron.append(glyph(CHEVRON_GLYPH));
        button.append(chevron);
      }
      button.addEventListener("pointermove", () => enterRow(index));
      button.addEventListener("pointerup", () => {
        if (!pressing() || !usable(row) || submenu) return;
        releasePress();
        chooseRow(index, false);
      });
      button.addEventListener("click", (event) => {
        if (chosen || !usable(row)) return;
        if (submenu) openSub(index, event.detail === 0);
        else chooseRow(index, event.detail === 0);
      });
      list.append(button);
      rowNodes[index] = button;
    });

    outer.append(list);

    const moveHighlight = () => {
      const row = rowNodes[active];
      if (!row || blink) {
        if (blink) highlight.style.transition = "none";
        highlight.style.opacity = "0";
        return;
      }
      const jump = highlight.style.opacity !== "1" || localReduce;
      if (jump) highlight.style.transition = "none";
      highlight.style.transform = `translateY(${row.offsetTop}px)`;
      highlight.style.height = `${row.offsetHeight}px`;
      highlight.style.opacity = "1";
      if (jump) {
        void highlight.offsetHeight;
        highlight.style.transition = "";
      }
    };

    const activate = (index, withFocus = true) => {
      active = index;
      moveHighlight();
      if (withFocus && rowNodes[index]) rowNodes[index].focus({ preventScroll: true });
    };

    const clearSub = () => {
      window.clearTimeout(subTimer);
      if (!sub) return;
      const closing = sub;
      sub = null;
      if (rowNodes[closing.index]) rowNodes[closing.index].setAttribute("aria-expanded", "false");
      if (closing.menu) closing.menu.close();
    };

    const openSub = (index, keyboard) => {
      window.clearTimeout(subTimer);
      const row = rowNodes[index];
      if (!row) return;
      const rows_ = rows[index].items || [];
      sub = {
        index,
        menu: buildMenu({
          rows: rows_,
          label: typeof rows[index].label === "string" ? rows[index].label : undefined,
          edge: { left: frameRect.left, right: frameRect.left + frameRect.width, top: frameRect.top + row.offsetTop },
          nested: true,
          focusFirst: keyboard,
          onSelect: (picked, fromKeyboard) => config.onSelect(picked, fromKeyboard),
          onDismiss: (restore) => config.onDismiss(restore),
          onEnter: () => {
            window.clearTimeout(subTimer);
            activate(index, false);
          },
          onBack: () => {
            if (!sub) return;
            clearSub();
            activate(index);
          }
        })
      };
      row.setAttribute("aria-expanded", "true");
    };

    const enterRow = (index) => {
      if (chosen || active === index || !usable(rows[index])) return;
      activate(index);
      window.clearTimeout(subTimer);
      if (rows[index].items && rows[index].items.length) {
        if (!sub || sub.index !== index) subTimer = window.setTimeout(() => openSub(index, false), 120);
      } else if (sub) {
        subTimer = window.setTimeout(clearSub, 220);
      }
    };

    /* The blink is the moment between the choice and the panel leaving: the row lights,
       then the menu closes on the action. 70ms and 150ms, as upstream. */
    const chooseRow = (index, keyboard) => {
      if (chosen) return;
      const row = rows[index];
      chosen = true;
      active = index;
      window.clearTimeout(subTimer);
      if (localReduce) {
        config.onSelect(row, keyboard);
        return;
      }
      blink = true;
      moveHighlight();
      window.setTimeout(() => {
        if (!ownAlive) return;
        blink = false;
        moveHighlight();
      }, 70);
      /* Guarded the way upstream's aliveRef guards it: a menu dismissed inside the blink
         must not choose a row on its way out. */
      window.setTimeout(() => {
        if (ownAlive) config.onSelect(row, keyboard);
      }, 150);
    };

    list.addEventListener("pointerenter", () => {
      if (config.onEnter) config.onEnter();
    });
    list.addEventListener("pointerleave", () => {
      if (sub || chosen) return;
      active = -1;
      moveHighlight();
      list.focus({ preventScroll: true });
    });
    list.addEventListener("keydown", (event) => {
      if (chosen) return;
      const key = event.key;
      const order = rows.flatMap((row, index) => (usable(row) ? [index] : []));
      let handled = true;
      if (key === "ArrowDown" || key === "ArrowUp" || key === "Home" || key === "End") {
        if (order.length) {
          const at = order.indexOf(active);
          let next = order[0];
          if (key === "End") next = order[order.length - 1];
          else if (key === "ArrowDown") next = order[(at + 1) % order.length];
          else if (key === "ArrowUp") next = order[at <= 0 ? order.length - 1 : at - 1];
          if (sub) clearSub();
          activate(next);
        }
      } else if (key === "ArrowRight" && rows[active] && rows[active].items && rows[active].items.length) {
        openSub(active, true);
      } else if ((key === "ArrowLeft" || key === "Escape") && nested) {
        config.onBack();
      } else if (key === "Escape" || key === "Tab") {
        config.onDismiss(true);
      } else {
        handled = false;
      }
      if (handled) {
        event.preventDefault();
        event.stopPropagation();
      }
    });

    document.body.append(outer);

    /* Placement. A menu with a tail is anchored to the tile's edge; one opened from the
       keyboard belongs to a row instead of a point, and is placed beside it. */
    const at = { x: 0, y: 0, ...(config.point || {}) };
    const box = config.edge || { left: 0, right: 0, top: 0 };
    const width = list.offsetWidth;
    const height = list.offsetHeight;
    const viewWidth = document.documentElement.clientWidth;
    const viewHeight = document.documentElement.clientHeight;
    let left;
    let top;
    let tailAt = 0;
    let origin;
    if (side === "bottom" || side === "top") {
      left = clamp(at.x - width / 2, MARGIN, viewWidth - MARGIN - width);
      top = side === "bottom" ? at.y - TAIL - height : at.y + TAIL;
      tailAt = clamp(at.x - left, MENU_RADIUS + TAIL_HALF, width - MENU_RADIUS - TAIL_HALF);
      origin = `${tailAt}px ${side === "bottom" ? height + TAIL : -TAIL}px`;
    } else if (side === "left" || side === "right") {
      top = clamp(at.y - height / 2, MARGIN, viewHeight - MARGIN - height);
      left = side === "right" ? at.x - TAIL - width : at.x + TAIL;
      tailAt = clamp(at.y - top, MENU_RADIUS + TAIL_HALF, height - MENU_RADIUS - TAIL_HALF);
      origin = `${side === "right" ? width + TAIL : -TAIL}px ${tailAt}px`;
    } else {
      const flip = box.right - 4 + width > viewWidth - MARGIN && box.left + 4 - width >= MARGIN;
      left = flip ? box.left + 4 - width : box.right - 4;
      top = clamp(box.top - 5, MARGIN, viewHeight - MARGIN - height);
      origin = flip ? "right top" : "left top";
    }
    const path_ = menuPath(width, height, side, tailAt);
    frameRect = { left, top, width, height };
    outer.style.left = `${left}px`;
    outer.style.top = `${top}px`;
    outer.style.transformOrigin = origin;
    outer.style.visibility = "visible";
    glass.style.clipPath = `path('${path_}')`;
    path.setAttribute("d", path_);

    if (localReduce) {
      layers.forEach((layer) => layer.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 120, easing: "ease-out" }));
    } else {
      outer.animate(
        [{ transform: `scale(${nested ? 0.94 : 0.82})` }, { transform: "scale(1)" }],
        { duration: nested ? 220 : 340, easing: POP }
      );
      layers.forEach((layer) =>
        layer.animate([{ opacity: 0 }, { opacity: 1 }], { duration: nested ? 120 : 160, easing: "ease-out" })
      );
    }

    const first = rows.findIndex(usable);
    if (config.focusFirst && first >= 0) activate(first);
    else list.focus({ preventScroll: true });

    let exited = false;
    const exit = () => {
      if (exited) return;
      exited = true;
      if (config.onExited) config.onExited();
    };

    return {
      element: outer,
      frame: () => frameRect,
      close() {
        if (localReduce) {
          layers.forEach((layer) => layer.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 100, easing: "ease-in", fill: "forwards" }));
        } else {
          outer.animate([{ transform: "scale(1)" }, { transform: "scale(0.96)" }], { duration: CLOSE_MS, easing: "ease-in", fill: "forwards" });
          layers.forEach((layer) => layer.animate([{ opacity: 1 }, { opacity: 0 }], { duration: CLOSE_MS, easing: "ease-in", fill: "forwards" }));
        }
        if (sub) {
          const closing = sub;
          sub = null;
          if (closing.menu) closing.menu.close();
        }
        window.clearTimeout(closeTimer);
        closeTimer = window.setTimeout(exit, localReduce ? 100 : CLOSE_MS);
      },
      destroy() {
        ownAlive = false;
        window.clearTimeout(closeTimer);
        window.clearTimeout(subTimer);
        if (sub) {
          const closing = sub;
          sub = null;
          if (closing.menu) closing.menu.destroy();
        }
        outer.remove();
      },
      contains: (node) => outer.contains(node)
    };
  }

  function openMenu(index, mode) {
    const node = nodes[index];
    const item = items[index];
    if (!node || !item || !item.menu || !item.menu.length) return false;
    if (menu && menu.index === index && !menu.closing) return true;

    const rect = node.getBoundingClientRect();
    const point = {
      bottom: { x: rect.left + rect.width / 2, y: rect.top - MENU_GAP },
      top: { x: rect.left + rect.width / 2, y: rect.bottom + MENU_GAP },
      left: { x: rect.right + MENU_GAP, y: rect.top + rect.height / 2 },
      right: { x: rect.left - MENU_GAP, y: rect.top + rect.height / 2 }
    }[side];
    serial += 1;
    const current = { id: serial, index, point, keyboard: mode === "keyboard", rows: item.menu, closing: false, restore: false, node: null };
    menu = current;
    menu.node = buildMenu({
      rows: item.menu,
      label: typeof item.label === "string" ? item.label : undefined,
      point,
      focusFirst: current.keyboard,
      onSelect: (row, keyboard) => {
        closeMenu(Boolean(keyboard || (menu && menu.keyboard)));
        if (row.onClick) row.onClick();
      },
      onDismiss: (restore) => closeMenu(restore),
      onExited: finishMenu
    });
    watchOutside();
    hold(index);
    repaintItems();
    return true;
  }

  function closeMenu(restore) {
    if (!menu || menu.closing) return;
    menu.closing = true;
    menu.restore = Boolean(restore);
    if (menu.node) menu.node.close();
    repaintItems();
  }

  function finishMenu() {
    const closed = menu;
    menu = null;
    unwatchOutside();
    if (closed && closed.node) closed.node.destroy();
    hold(null);
    repaintItems();
    if (closed && closed.restore) {
      const node = nodes[closed.index];
      if (node && node.focus) node.focus({ preventScroll: true });
    }
  }

  function watchOutside() {
    unwatchOutside();
    const owner = nodes[menu.index];
    const onDown = (event) => {
      const target = event.target;
      if (target instanceof Element && (target.closest("[data-dock-menu]") || (owner && owner.contains(target)))) return;
      closeMenu(false);
    };
    const onScroll = (event) => {
      if (event.target instanceof Element && event.target.closest("[data-dock-menu]")) return;
      closeMenu(false);
    };
    const onAway = () => closeMenu(false);
    document.addEventListener("pointerdown", onDown, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onAway);
    window.addEventListener("blur", onAway);
    stopWatchingOutside = () => {
      document.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onAway);
      window.removeEventListener("blur", onAway);
    };
  }

  function unwatchOutside() {
    if (stopWatchingOutside) stopWatchingOutside();
    stopWatchingOutside = null;
  }

  const pressing = () => Boolean(press && press.active);
  const releasePress = () => {
    if (press) press.active = false;
  };

  function endPress() {
    if (!press) return;
    window.clearTimeout(press.timer);
    press.cleanup();
    press = null;
  }

  /* A press that stays put for HOLD_TIME opens the menu where the finger is; one that
     moves, or lifts early, is a click instead. */
  function startPress(event, index) {
    endPress();
    const target = event.currentTarget;
    if (target.hasPointerCapture && target.hasPointerCapture(event.pointerId)) {
      target.releasePointerCapture(event.pointerId);
    }
    const state = { id: event.pointerId, x: event.clientX, y: event.clientY, timer: 0, active: false, cleanup: () => {} };
    const onMove = (moved) => {
      if (moved.pointerId !== state.id) return;
      if (Math.hypot(moved.clientX - state.x, moved.clientY - state.y) > PRESS_SLOP) endPress();
    };
    const onUp = (released) => {
      if (released.pointerId === state.id) endPress();
    };
    state.cleanup = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
    state.timer = window.setTimeout(() => {
      state.cleanup();
      if (!openMenu(index, "press")) {
        press = null;
        return;
      }
      skip = index;
      state.active = true;
      window.addEventListener(
        "pointerup",
        () => {
          state.active = false;
        },
        { once: true }
      );
    }, HOLD_TIME);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    press = state;
  }

  function attachItemListeners(button) {
    button.addEventListener("pointerdown", (event) => {
      skip = -1;
      const index = Number(button.dataset.index);
      const current = menu;
      if (current && !current.closing) {
        if (event.button === 2 || event.ctrlKey) return;
        skip = index;
        closeMenu(false);
        return;
      }
      const item = items[index];
      if (item && item.menu && item.menu.length && event.button === 0 && !event.ctrlKey) {
        startPress(event, index);
      }
    });
    button.addEventListener("contextmenu", (event) => {
      const index = Number(button.dataset.index);
      const item = items[index];
      if (!item || !item.menu || !item.menu.length) return;
      event.preventDefault();
      endPress();
      openMenu(index, "pointer");
    });
    button.addEventListener("click", () => {
      const index = Number(button.dataset.index);
      if (skip === index) {
        skip = -1;
        return;
      }
      const item = items[index];
      hop(index);
      if (item && item.onClick) item.onClick();
    });
    button.addEventListener("focus", (event) => {
      if (event.currentTarget.matches(":focus-visible")) focused = Number(button.dataset.index);
      wake();
    });
    button.addEventListener("blur", () => {
      focused = -1;
      wake();
    });
    button.addEventListener("keydown", (event) => onItemKeyDown(event, Number(button.dataset.index)));
  }

  function onItemKeyDown(event, index) {
    const toward = { bottom: "ArrowUp", top: "ArrowDown", left: "ArrowRight", right: "ArrowLeft" }[side];
    if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10") || event.key === toward) {
      if (openMenu(index, "keyboard")) event.preventDefault();
      return;
    }
    const order = items.flatMap((item, i) => (item && item.separator ? [] : [i]));
    const at = order.indexOf(index);
    const keys = vertical ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
    let next;
    if (event.key === keys[0]) next = order[(at - 1 + order.length) % order.length];
    else if (event.key === keys[1]) next = order[(at + 1) % order.length];
    else if (event.key === "Home") next = order[0];
    else if (event.key === "End") next = order[order.length - 1];
    if (next === undefined) return;
    event.preventDefault();
    const node = nodes[next];
    if (node && node.focus) node.focus();
  }

  /* ------------------------------------------------------------------ the geometry -- */

  function applyFit() {
    const room = (vertical ? host.clientHeight : host.clientWidth) - EDGE * 2;
    const length =
      items.reduce((sum, item) => sum + (item && item.separator ? 1 : rawBase), 0) +
      rawGap * Math.max(0, items.length - 1) +
      rawThickness -
      rawBase;
    const next = length > 0 ? Math.min(1, Math.max(0.4, room / (length + rawPeak - rawBase))) : 1;
    fit = Math.round(next * 1000) / 1000;
    base = rawBase * fit;
    peak = rawPeak * fit;
    thickness = rawThickness * fit;
    gap = rawGap * fit;
    scaledReach = reach * fit;
    root.style.setProperty("--dock-base", `${base}px`);
    root.style.setProperty("--dock-thickness", `${thickness}px`);
    root.style.setProperty("--dock-gap", `${gap}px`);
    root.style.setProperty("--dock-pad", `${(thickness - base) / 2}px`);
    root.style.setProperty("--dock-reach", `${peak - base}px`);
    root.style.setProperty("--dock-round", String(roundness));
  }

  function repaintItems() {
    for (let index = 0; index < items.length; index++) paintItem(index);
    for (let index = items.length; index < nodes.length; index++) {
      if (nodes[index]) nodes[index].remove();
    }
    nodes.length = items.length;
    for (const index of Array.from(motion.keys())) if (index >= items.length) motion.delete(index);
    /* Repainting resets what each tile was drawn at, so the loop has to run once more or
       a tile rebuilt under the pointer sits at its base size while the spring says it is
       magnified. */
    wake();
  }

  const observer = new ResizeObserver(() => {
    applyFit();
    wake();
  });
  observer.observe(host);

  const unwatch = watchReducedMotion((matches) => {
    reduce = matches;
    wake();
  });

  repaintItems();
  applyFit();
  wake();

  return {
    /* Items are matched by `key`, so an update that only changes a label, a badge or a
       checked row leaves every tile exactly where it is. */
    set(changes = {}) {
      if (changes.items) {
        const next = changes.items;
        const sameShape =
          next.length === items.length && next.every((item, index) => itemKey(item, index) === itemKey(items[index], index));
        items = next;
        if (!sameShape) {
          for (const node of nodes) if (node) node.remove();
          nodes = [];
          motion.clear();
          lastBase = rawBase;
        }
      }
      if (changes.autoHide !== undefined) {
        autoHide = Boolean(changes.autoHide);
        if (autoHide !== hiding) {
          hiding = autoHide;
          if (autoHide) hide();
          else show();
        }
      }
      repaintItems();
      applyFit();
      wake();
    },
    /* A plain click on an item that has a menu does nothing on its own — upstream leaves
       that to the item's own onClick, and this is the call that makes a click open the
       menu instead. */
    open(index) {
      return openMenu(index, "pointer");
    },
    destroy() {
      alive = false;
      cancelAnimationFrame(raf);
      window.clearTimeout(hideTimer);
      endPress();
      unwatchOutside();
      if (menu && menu.node) menu.node.destroy();
      menu = null;
      unwatch();
      observer.disconnect();
      root.removeEventListener("pointermove", onRootMove);
      root.removeEventListener("pointerdown", onRootMove);
      root.removeEventListener("pointerleave", onRootLeave);
      root.removeEventListener("pointercancel", onRootLeave);
      root.removeEventListener("focusin", show);
      root.removeEventListener("focusout", onFocusOut);
      window.removeEventListener("pointermove", onWindowMove);
      window.removeEventListener("pointerdown", onWindowMove);
      root.remove();
    }
  };
}
