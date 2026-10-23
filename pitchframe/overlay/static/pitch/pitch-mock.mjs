/**
 * The mock tracking generator: 22 players + a ball moving plausibly, offline.
 *
 * The real generator upstream (`pitchframe/generator.py`) produces an *event*
 * stream — passes, shots, saves with a minute and a player — not tracking data,
 * so positions are synthesised here. The tracker is deterministic per seed (one
 * seeded RNG decides everything, including the surges and the substitutions),
 * so a replay re-running the same seed shows the same match again, and a test
 * can pin any of it.
 *
 * Motion model, in plain terms: every outfield player has a formation slot they
 * relax toward, pulled toward the ball by a role-dependent weight and wandering
 * with a slow seeded wobble; from time to time one player launches a short run
 * out of the slot. The ball is in a side's possession, walks toward a target in
 * that side's attacking half, and changes hands when it arrives or randomly —
 * nothing is authored event-by-event, which keeps this file one page of model.
 *
 * Distances: x spans 105 m and y spans 68 m, so movement is capped in *metres*
 * and converted back per axis — an equality the pure x/y version of the same
 * cap would quietly drop (a sideways step is only 0.65 of a forward one).
 */

/** Deterministic 32-bit RNG so `seed=7` is the same match on every machine. */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Home's shape, attacking +x. The away side mirrored it below. */
const FORMATION_433 = [
  { role: "GK", x: 0.05, y: 0.5 },
  { role: "RB", x: 0.2, y: 0.15 },
  { role: "CB", x: 0.15, y: 0.38 },
  { role: "CB", x: 0.15, y: 0.62 },
  { role: "LB", x: 0.2, y: 0.85 },
  { role: "DM", x: 0.35, y: 0.5 },
  { role: "CM", x: 0.45, y: 0.32 },
  { role: "CM", x: 0.45, y: 0.68 },
  { role: "RW", x: 0.68, y: 0.15 },
  { role: "ST", x: 0.75, y: 0.5 },
  { role: "LW", x: 0.68, y: 0.85 },
];

/** How strongly each role chases the ball, GK first. */
const BALL_PULL = { GK: 0.04, CB: 0.22, RB: 0.28, LB: 0.28, DM: 0.4, CM: 0.45, RW: 0.5, LW: 0.5, ST: 0.55 };

const SPRINT_MPS = 8.5;          // No player exceeds this; matches the drift too.
const PITCH_X_M = 105;
const PITCH_Y_M = 68;

/** Squad names match `pitchframe/generator.py` so both views of a match agree. */
const TEAM_NAMES = { home: "Northgate Athletic", away: "Riverside United" };

const NAME_ONSETS = ["Al", "Bren", "Cas", "Dan", "Eri", "Fil", "Gor", "Hal", "Ivo", "Jas", "Kat", "Lor", "Mir", "Nik", "Osk", "Pav", "Ras", "Sil", "Tom", "Ver"];
const NAME_CODAS = ["aro", "beck", "dahl", "ek", "ford", "gard", "holm", "ire", "kovac", "lund", "mire", "nov", "quist", "rado", "skar", "tova", "vik", "well"];

function playerName(rng, used) {
  for (let i = 0; i < 500; i++) {
    const n = `${NAME_ONSETS[(rng() * NAME_ONSETS.length) | 0]}${NAME_CODAS[(rng() * NAME_CODAS.length) | 0]}`;
    if (!used.has(n)) {
      used.add(n);
      return n;
    }
  }
  throw new Error("exhausted the name space; widen NAME_ONSETS/NAME_CODAS");
}

function hashId(id) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return (h >>> 0) / 4294967296 * Math.PI * 2;
}

/** Slow deterministic wobble around a slot, in normalised units. */
function wander(id, minutes) {
  const a = hashId(id);
  return {
    x: 0.035 * Math.sin(minutes * 0.9 + a) + 0.02 * Math.sin(minutes * 2.3 + a * 1.7),
    y: 0.035 * Math.cos(minutes * 0.8 + a * 1.3) + 0.02 * Math.sin(minutes * 1.9 + a * 0.7),
  };
}

function buildSquad(rng, side, used) {
  return FORMATION_433.map((slot, i) => {
    const id = `${side}${String(i + 1).padStart(2, "0")}`;
    return {
      id,
      name: playerName(rng, used),
      teamId: side,
      shirtNumber: i + 1,
      position: slot.role,
      isGoalkeeper: slot.role === "GK",
      baseX: side === "home" ? slot.x : 1 - slot.x,
      baseY: side === "home" ? slot.y : 1 - slot.y,
      role: slot.role,
      x: side === "home" ? slot.x : 1 - slot.x,
      y: slot.y,
      dist: 0,
      speed: 0,
    };
  });
}

function buildBench(rng, side, used, firstShirt) {
  return Array.from({ length: 3 }, (_, i) => ({
    id: `${side}b${i + 1}`,
    name: playerName(rng, used),
    teamId: side,
    shirtNumber: firstShirt + i,
  }));
}

/**
 * A complete recorded match, deterministic per seed.
 *
 * Returns `{ teams, squads, frames, durationMs, capabilities, hz, step }`. All
 * timestamps are the **match clock** in milliseconds, continuous across the
 * half-time break (the break itself is not modelled — `periodFor` labels the
 * halves; extra time is accepted by the view but not generated here).
 * Optional `speed` (`km/h`) and `distance` (`km`, running total) are included,
 * so the chips they own light up in the UI.
 */
export function generateRecording(options = {}) {
  const {
    seed = 7,
    minutes = 90,
    hz = 1,
    // `[minute, side, slotIndex]` triples; slotIndex indexes FORMATION_433.
    substitutions = [[58, "home", 7], [74, "away", 9]],
  } = options;
  const rng = mulberry32(seed);
  const used = new Set();
  const squads = {
    home: { name: TEAM_NAMES.home, players: buildSquad(rng, "home", used), bench: buildBench(rng, "home", used, 12) },
    away: { name: TEAM_NAMES.away, players: buildSquad(rng, "away", used), bench: buildBench(rng, "away", used, 12) },
  };
  const pendingSubs = substitutions.map(([minute, side, slotIndex]) => ({ minute, side, slotIndex, done: false }));

  const stepMs = 1000 / hz;
  const dtMin = 1 / hz;
  const capStep = SPRINT_MPS * dtMin; // metres a player may cover in one sample
  const runClock = new Map(); // frames left in a committed run, per player id
  const total = Math.floor(minutes * 60);
  const last = Math.max(2, total);

  let ballTeam = "home";
  let ball = { x: 0.5, y: 0.5, z: 0 };
  let ballTarget = { x: 0.75, y: 0.5 };
  const frames = [];

  for (let i = 0; i < last; i++) {
    const t = i * stepMs;
    const minute = t / 60000;

    // --- substitutions: the slot keeps its shape and shirt, the person in it
    // changes. The two share the slot's *position on the pitch* at the change:
    // the incoming player's first frame is where the outgoing one was standing.
    for (const sub of pendingSubs) {
      if (!sub.done && minute >= sub.minute) {
        const squad = squads[sub.side];
        const bench = (sub.side === "home" ? squads.home : squads.away).bench;
        const inPlayer = bench.shift();
        const slot = squad.players[sub.slotIndex];
        squad.players[sub.slotIndex] = {
          ...slot,
          id: inPlayer.id,
          name: inPlayer.name,
          shirtNumber: inPlayer.shirtNumber,
          isGoalkeeper: false,
          x: slot.x,
          y: slot.y,
          dist: 0,
          speed: 0,
        };
        sub.done = true;
      }
    }

    // --- ball: possession, target, arc -------------------------------------
    const touchSpeed = 0.03 + rng() * 0.025; // normalised units per second
    const dx = ballTarget.x - ball.x;
    const dy = ballTarget.y - ball.y;
    const dm = Math.hypot(dx, dy);
    if (dm < touchSpeed * dtMin || rng() < 0.05) {
      if (rng() < 0.4) ballTeam = ballTeam === "home" ? "away" : "home";
      const toward = ballTeam === "home" ? 1 : -1;
      ballTarget = {
        x: ballTeam === "home" ? 0.55 + rng() * 0.4 : 0.05 + rng() * 0.4,
        y: 0.12 + rng() * 0.76,
      };
    } else {
      const move = Math.min(touchSpeed * dtMin, dm);
      ball.x += (dx / dm) * move;
      ball.y += (dy / dm) * move;
    }
    const toGo = Math.hypot(ballTarget.x - ball.x, ballTarget.y - ball.y);
    ball.z = toGo > 0.08 ? 0.22 * Math.sin((1 - Math.min(1, toGo / dm)) * Math.PI) : 0;

    // --- players ------------------------------------------------------------
    for (const side of ["home", "away"]) {
      for (const p of squads[side].players) {
        const pull = p.isGoalkeeper ? 0.04 : (BALL_PULL[p.role] ?? 0.3);
        const w = wander(p.id, minute);
        let tx = p.baseX * (1 - pull) + ball.x * pull + w.x;
        let ty = p.baseY * (1 - pull) + ball.y * pull + w.y;

        if (p.isGoalkeeper) {
          // GKs hold their line and track the ball's width inside the box.
          tx = p.baseX;
          ty = 0.5 + (ball.y - 0.5) * 0.3;
        } else {
          let running = runClock.get(p.id) ?? 0;
          if (running === 0 && rng() < 0.006) running = 3 + ((rng() * 4) | 0);
          if (running > 0) {
            const dir = p.teamId === "home" ? 1 : -1;
            tx = clamp01(ball.x + dir * 0.06);
            ty = clamp01(ball.y + (rng() - 0.5) * 0.08);
            runClock.set(p.id, running - 1);
          } else {
            runClock.delete(p.id);
          }
        }

        tx = clamp01(tx);
        ty = clamp01(ty);
        const step = moveCapped(p, tx, ty, capStep);
        p.dist += step / 1000; // km
        const instant = dtMin > 0 ? (step / dtMin) * 3.6 : 0; // km/h
        p.speed = p.speed * 0.55 + instant * 0.45;
      }
    }

    frames.push(serializeFrame(t, squads, ball));
  }

  return {
    seed,
    hz,
    step: stepMs,
    minutes,
    durationMs: (last - 1) * stepMs,
    teams: [
      { teamId: "home", name: TEAM_NAMES.home },
      { teamId: "away", name: TEAM_NAMES.away },
    ],
    squads,
    frames,
    capabilities: { speed: true, distance: true },
  };
}

function moveCapped(p, tx, ty, capStepM) {
  const dxm = (tx - p.x) * PITCH_X_M;
  const dym = (ty - p.y) * PITCH_Y_M;
  const dm = Math.hypot(dxm, dym);
  if (dm === 0) return 0;
  const step = Math.min(dm, capStepM);
  p.x += (dxm / dm) * (step / PITCH_X_M);
  p.y += (dym / dm) * (step / PITCH_Y_M);
  return step;
}

function serializeFrame(t, squads, ball) {
  const players = [];
  for (const side of ["home", "away"]) {
    for (const p of squads[side].players) {
      players.push({
        id: p.id,
        teamId: p.teamId,
        shirtNumber: p.shirtNumber,
        name: p.name,
        position: p.position,
        isGoalkeeper: p.isGoalkeeper,
        x: r3(p.x),
        y: r3(p.y),
        speed: Math.round(p.speed * 10) / 10,
        distance: Math.round(p.dist * 10) / 10,
      });
    }
  }
  return {
    t,
    period: periodOf(t),
    ball: { x: r3(ball.x), y: r3(ball.y), z: r3(ball.z) },
    players,
  };
}

function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

function r3(v) {
  return Math.round(v * 1000) / 1000;
}

function periodOf(ms) {
  if (ms < 45 * 60_000) return "1H";
  if (ms < 90 * 60_000) return "2H";
  if (ms < 105 * 60_000) return "ET1";
  if (ms < 120 * 60_000) return "ET2";
  return "FT";
}
