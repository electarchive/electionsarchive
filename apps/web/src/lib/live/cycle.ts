import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
import { swatch } from "#lib/party.js";

export type CycleParty = {
  id: string;
  family: string;
  name: string;
  short: string;
  color: string | null;
  share: number;
};

export type CycleLine = {
  kind: "national" | "office";
  label: string;
  slug: string | null;
  isEstimated: boolean;
  isRedrawn: boolean;
  parties: Array<CycleParty>;
};

export type Cycle = { date: string; lines: Array<CycleLine> };

export type Axis = { left: CycleParty; right: CycleParty };

export type Range = { low: number; mid: number; high: number };

export function axisOf(cycle: Cycle): Axis | null {
  const reference = baseline(cycle);
  const [first, second] = reference?.parties ?? [];
  if (first == null || second == null) {
    return null;
  }
  return /republican/iu.test(first.name)
    ? { left: second, right: first }
    : { left: first, right: second };
}

export function baseline(cycle: Cycle) {
  return (
    cycle.lines.find((line) => line.kind === "office" && !line.isRedrawn) ??
    cycle.lines.find((line) => line.kind === "national") ??
    cycle.lines[0] ??
    null
  );
}

function belongs(id: string, party: CycleParty) {
  return id === party.id || id === party.family;
}

export function pastMargin(line: CycleLine, axis: Axis) {
  const left = line.parties.find((party) => party.family === axis.left.family);
  const right = line.parties.find(
    (party) => party.family === axis.right.family,
  );
  return left == null || right == null ? null : right.share - left.share;
}

export function sideOf(option: LiveOption | undefined, axis: Axis) {
  const id = option?.party?.id;
  if (id == null) {
    return 0;
  }
  if (belongs(id, axis.right)) {
    return 1;
  }
  return belongs(id, axis.left) ? -1 : 0;
}

export function nowMargin(
  options: Array<LiveOption>,
  totals: Array<number>,
  total: number,
  axis: Axis,
) {
  if (total <= 0) {
    return null;
  }
  let left: number | null = null;
  let right: number | null = null;
  for (const [index, option] of options.entries()) {
    const side = sideOf(option, axis);
    if (side === 1) {
      right = (right ?? 0) + (totals[index] ?? 0);
    } else if (side === -1) {
      left = (left ?? 0) + (totals[index] ?? 0);
    }
  }
  return left == null || right == null ? null : (right - left) / total;
}

export function projectedMargin(
  options: Array<LiveOption>,
  estimate: LiveEstimate | null,
  axis: Axis,
): Range | null {
  if (
    estimate == null ||
    (estimate.status !== "estimating" && estimate.status !== "settled")
  ) {
    return null;
  }
  const leader = sideOf(
    options.find((option) => option.id === estimate.leader),
    axis,
  );
  const runnerUp = sideOf(
    options.find((option) => option.id === estimate.runnerUp),
    axis,
  );
  if (leader === 0 || runnerUp !== -leader) {
    return null;
  }
  const { low, mid, high } = estimate.margin;
  return leader === 1
    ? { low, mid, high }
    : { low: -high, mid: -mid, high: -low };
}

export function tint(party: CycleParty) {
  return swatch(party.color, `party:${party.id}`);
}

export function tags(axis: Axis) {
  const left = axis.left.short.slice(0, 1).toUpperCase();
  const right = axis.right.short.slice(0, 1).toUpperCase();
  return left === right || left === "" || right === ""
    ? { left: axis.left.short, right: axis.right.short }
    : { left, right };
}
