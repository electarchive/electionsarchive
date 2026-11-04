import type {
  Band,
  LiveElection,
  LiveEstimate,
  LiveRace,
} from "@ea/analytics/live";
import type { Control } from "#lib/live/nation.js";

type Moving = "frames" | "counts" | "certified" | "projected" | "feed";

type Sent = { low: number | null; mid: number | null; high: number | null };

type Estimate = Omit<
  LiveEstimate,
  "expectedTotal" | "shareIn" | "margin" | "winProbability" | "units"
> & {
  expectedTotal: Sent;
  shareIn: Sent;
  margin: Sent;
  winProbability: Record<string, number | null>;
  units: Array<{ unitId: string; expectedTotal: Sent; projectedMargin: Sent }>;
};

export type Head = Omit<LiveElection, "races">;

export type Stable = {
  races: Array<Omit<LiveRace, Moving>>;
  control: Control | null;
};

export type Changing = Array<
  Pick<LiveRace, Moving> & { estimate: Estimate | null }
>;

function trim(value: number) {
  if (!Number.isFinite(value)) {
    return null;
  }
  return Number.isSafeInteger(value) ? value : Number(value.toPrecision(6));
}

function sent({ low, mid, high }: Band): Sent {
  return { low: trim(low), mid: trim(mid), high: trim(high) };
}

function band({ low, mid, high }: Sent): Band {
  return { low: low ?? NaN, mid: mid ?? NaN, high: high ?? NaN };
}

function encode(estimate: LiveEstimate): Estimate {
  return {
    ...estimate,
    expectedTotal: sent(estimate.expectedTotal),
    shareIn: sent(estimate.shareIn),
    margin: sent(estimate.margin),
    winProbability: Object.fromEntries(
      Object.entries(estimate.winProbability).map(([id, chance]) => [
        id,
        trim(chance),
      ]),
    ),
    units: estimate.units.map((unit) => ({
      unitId: unit.unitId,
      expectedTotal: sent(unit.expectedTotal),
      projectedMargin: sent(unit.projectedMargin),
    })),
  };
}

function decode(estimate: Estimate): LiveEstimate {
  return {
    ...estimate,
    expectedTotal: band(estimate.expectedTotal),
    shareIn: band(estimate.shareIn),
    margin: band(estimate.margin),
    winProbability: Object.fromEntries(
      Object.entries(estimate.winProbability).map(([id, chance]) => [
        id,
        chance ?? NaN,
      ]),
    ),
    units: estimate.units.map((unit) => ({
      unitId: unit.unitId,
      expectedTotal: band(unit.expectedTotal),
      projectedMargin: band(unit.projectedMargin),
    })),
  };
}

export function split(
  { races, ...head }: LiveElection,
  estimates: Record<string, LiveEstimate>,
  control: Control | null,
) {
  const stable: Stable = { races: [], control };
  const changing: Changing = [];
  for (const { frames, counts, certified, projected, feed, ...race } of races) {
    const estimate = estimates[race.id];
    stable.races.push(race);
    changing.push({
      frames,
      counts,
      certified,
      projected: projected ?? null,
      ...(feed != null && { feed }),
      estimate: estimate == null ? null : encode(estimate),
    });
  }
  return { head, stable, changing };
}

export function join(head: Head, stable: Stable, changing: Changing) {
  const estimates: Record<string, LiveEstimate> = {};
  const races: Array<LiveRace> = [];
  for (const [index, race] of stable.races.entries()) {
    const { estimate = null, ...moving } = changing[index] ?? {
      frames: [],
      counts: [],
      certified: null,
    };
    if (estimate != null) {
      estimates[race.id] = decode(estimate);
    }
    races.push({ ...race, ...moving });
  }
  return { election: { ...head, races }, estimates, control: stable.control };
}
