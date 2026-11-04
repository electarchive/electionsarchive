import type {
  Band,
  LiveElection,
  LiveEstimate,
  LiveRace,
} from "@ea/analytics/live";
import { load, Part } from "svelte-islands";
import type { Control } from "#lib/live/nation.js";
import { ranked, sum } from "#lib/live/standing.js";

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

export type Update = {
  race: string;
  at: string;
  leader: string | null;
  margin: number;
  added: number;
  isCall: boolean;
};

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

export function recent(races: Array<LiveRace>) {
  const found: Array<Update> = [];
  for (const race of races) {
    const call = race.projected;
    if (call != null && race.options.some(({ id }) => id === call.option)) {
      found.push({
        race: race.slug,
        at: call.at,
        leader: call.option,
        margin: 0,
        added: 0,
        isCall: true,
      });
    }
    let previous = 0;
    for (const frame of race.frames) {
      const total = sum(frame.totals);
      if (total > previous) {
        const [first, second] = ranked(frame.totals);
        found.push({
          race: race.slug,
          at: frame.at,
          leader: first == null ? null : (race.options[first]?.id ?? null),
          margin:
            trim(
              ((frame.totals[first ?? 0] ?? 0) -
                (frame.totals[second ?? 1] ?? 0)) /
                total,
            ) ?? 0,
          added: total - previous,
          isCall: false,
        });
      }
      previous = total;
    }
  }
  return found
    .toSorted((a, b) => b.at.localeCompare(a.at) || b.added - a.added)
    .slice(0, 60);
}

export function split(
  { races, ...head }: LiveElection,
  estimates: Record<string, LiveEstimate>,
  control: Control | null,
  isTrimmed = false,
) {
  const stable: Stable = { races: [], control };
  const changing: Changing = [];
  for (const { frames, counts, certified, projected, feed, ...race } of races) {
    const estimate = estimates[race.id];
    stable.races.push(race);
    changing.push({
      frames: isTrimmed ? frames.slice(-1) : frames,
      counts,
      certified,
      projected: projected ?? null,
      ...(feed != null && { feed }),
      estimate: estimate == null ? null : encode(estimate),
    });
  }
  return { head, stable, changing, updates: recent(races) };
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

export async function latest(path: string) {
  const response = await fetch(`/live/parts/${path}`);
  if (!response.ok) {
    throw new Error(String(response.status));
  }
  const {
    stable,
    changing,
    ...rest
  }: {
    head: Head;
    stable: ConstructorParameters<typeof Part<Stable>>[0];
    changing: ConstructorParameters<typeof Part<Changing>>[0];
    shown: { results: boolean; forecasts: boolean };
    updates: Array<Update>;
  } = await response.json();
  return { ...rest, stable: new Part(stable), changing: new Part(changing) };
}

export async function optional<T>(found: Part<T> | null) {
  try {
    return found == null ? null : await load(found);
  } catch {
    return null;
  }
}

export async function opened({
  head,
  stable,
  changing,
}: {
  head: Head;
  stable: Part<Stable>;
  changing: Part<Changing>;
}) {
  try {
    const [races, moving] = await Promise.all([load(stable), load(changing)]);
    return join(head, races, moving);
  } catch {
    const found = await latest(head.slug);
    const [races, moving] = await Promise.all([
      load(found.stable),
      load(found.changing),
    ]);
    return join(found.head, races, moving);
  }
}
