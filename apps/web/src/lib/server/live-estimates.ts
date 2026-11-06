import type { LiveEstimate, LiveOption, LiveRace } from "@ea/analytics/live";
import { estimate } from "@ea/analytics/live";
import { setImmediate as breathe } from "node:timers/promises";
import type { Point } from "#lib/live/history.js";
import { pair, sum, toward } from "#lib/live/standing.js";

const SLICE_MS = 8;
const KEPT = 32_768;
const POINTS_KEPT = 262_144;
const known = new Map<string, { key: string; value: LiveEstimate }>();
const points = new Map<string, Point>();
const trends = new WeakMap<LiveRace, Promise<Array<Point>>>();

function pacer() {
  let started = performance.now();
  return async () => {
    if (performance.now() - started < SLICE_MS) {
      return;
    }
    await breathe();
    started = performance.now();
  };
}

function rounded(value: number) {
  return Math.round(value * 1e5) / 1e5;
}

function fingerprint(race: LiveRace) {
  let hash = 0;
  const mix = (value: number) => {
    hash = (Math.imul(hash, 31) + Math.round(value * 1e6)) % 2_147_483_647;
  };
  for (const frame of race.frames) {
    for (const votes of frame.totals) {
      mix(votes);
    }
  }
  let newest = "";
  for (const count of race.counts) {
    for (const votes of count.votes) {
      mix(votes);
    }
    if (count.at > newest) {
      newest = count.at;
    }
  }
  for (const row of race.baseline) {
    mix(row.turnout);
    mix(row.expected ?? -1);
    for (const share of row.shares) {
      mix(share);
    }
    for (const trait of row.traits ?? []) {
      mix(trait);
    }
  }
  for (const votes of race.certified?.totals ?? []) {
    mix(votes);
  }
  return [
    hash,
    race.frames.length,
    race.frames[0]?.at,
    race.frames.at(-1)?.at,
    race.counts.length,
    newest,
    race.units.length,
    race.options.length,
    race.certified == null,
  ].join("|");
}

export async function estimates(races: Array<LiveRace>, hasUnits: boolean) {
  const found: Record<string, LiveEstimate> = {};
  const pace = pacer();
  for (const race of races) {
    const key = fingerprint(race);
    let held = known.get(race.id);
    if (held?.key !== key) {
      held = { key, value: estimate({ race, at: null }) };
      known.delete(race.id);
      known.set(race.id, held);
      if (known.size > KEPT) {
        known.delete(known.keys().next().value ?? "");
      }
    }
    found[race.id] = hasUnits ? held.value : { ...held.value, units: [] };
    await pace();
  }
  return found;
}

function pointAt(
  race: LiveRace,
  option: LiveOption | undefined,
  at: string,
): Point {
  const found = estimate({ race, at });
  const flip = toward(found, option);
  const low = found.margin.low * flip;
  const high = found.margin.high * flip;
  return {
    at,
    p: rounded(option == null ? 0.5 : (found.winProbability[option.id] ?? 0.5)),
    low: rounded(Math.min(low, high)),
    mid: rounded(found.margin.mid * flip),
    high: rounded(Math.max(low, high)),
    share:
      found.status === "estimating" ||
      found.status === "too-early" ||
      found.status === "ranked"
        ? rounded(Math.min(0.99, found.shareIn.mid))
        : null,
    isLive: found.status === "estimating",
  };
}

async function traced(race: LiveRace) {
  const [a] = pair(race);
  const option = race.options[a];
  const open = { ...race, certified: null };
  const found: Array<Point> = [];
  const pace = pacer();
  for (const frame of race.frames) {
    const key = `${race.id}|${frame.at}|${sum(frame.totals)}|${frame.reporting ?? ""}|${option?.id ?? ""}`;
    let point = points.get(key);
    if (point == null) {
      point = pointAt(open, option, frame.at);
      points.set(key, point);
      if (points.size > POINTS_KEPT) {
        points.delete(points.keys().next().value ?? "");
      }
      await pace();
    }
    found.push(point);
  }
  return found;
}

export async function trend(race: LiveRace) {
  let found = trends.get(race);
  if (found == null) {
    found = traced(race);
    trends.set(race, found);
    found.catch(() => trends.delete(race));
  }
  return found;
}
