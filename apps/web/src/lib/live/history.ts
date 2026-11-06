import type { LiveEstimate, LiveRace } from "@ea/analytics/live";
import { estimate } from "@ea/analytics/live";
import { sum } from "#lib/live/standing.js";

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
  return `${race.id}|${at}|${sum(frame?.totals ?? [])}|${frame?.reporting ?? ""}|${race.certified == null ? "" : "c"}`;
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
