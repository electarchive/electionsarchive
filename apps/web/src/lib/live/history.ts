import type { LiveEstimate, LiveRace } from "@ea/analytics/live";
import { estimate } from "@ea/analytics/live";
import { pair, sum, toward } from "#lib/live/standing.js";

export type Point = {
  at: string;
  p: number;
  low: number;
  mid: number;
  high: number;
  share: number | null;
  isLive: boolean;
};

const kept = new Map<string, LiveEstimate>();

function key(race: LiveRace, at: string) {
  const frame = race.frames.findLast((entry) => entry.at <= at);
  return `${race.id}|${at}|${sum(frame?.totals ?? [])}|${frame?.reporting ?? ""}|${race.certified == null ? "" : "c"}|${race.isClassic === true ? "classic" : ""}`;
}

export function estimateAt(race: LiveRace, at: string | null) {
  const stamp = at ?? race.frames.at(-1)?.at ?? "";
  const id = key(race, stamp);
  const held = kept.get(id);
  if (held != null) {
    return held;
  }
  const value = estimate({ race, at: stamp === "" ? null : stamp });
  kept.set(id, value);
  if (kept.size > 4000) {
    kept.delete(kept.keys().next().value ?? "");
  }
  return value;
}

export function history(race: LiveRace, until: string | null): Array<Point> {
  const [a] = pair(race);
  const option = race.options[a];
  const points: Array<Point> = [];
  const open = { ...race, certified: null };
  for (const frame of race.frames) {
    if (until != null && frame.at > until) {
      break;
    }
    const found = estimateAt(open, frame.at);
    const flip = toward(found, option);
    const low = found.margin.low * flip;
    const high = found.margin.high * flip;
    points.push({
      at: frame.at,
      p: option == null ? 0.5 : (found.winProbability[option.id] ?? 0.5),
      low: Math.min(low, high),
      mid: found.margin.mid * flip,
      high: Math.max(low, high),
      share:
        found.status === "estimating" || found.status === "too-early"
          ? Math.min(0.99, found.shareIn.mid)
          : null,
      isLive: found.status === "estimating",
    });
  }
  return points;
}
