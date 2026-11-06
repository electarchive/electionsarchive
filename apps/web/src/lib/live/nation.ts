import type { LiveOption, LiveRace } from "@ea/analytics/live";
import type { Shift } from "#lib/live/flips.js";
import type { Summary } from "#lib/live/summary.js";
import type { Control } from "#lib/server/live-board.js";
import { percent } from "#lib/format.js";
import { shift } from "#lib/live/flips.js";
import { isContested } from "#lib/live/standing.js";
import { m } from "#lib/paraglide/messages.js";
import { swatch } from "#lib/party.js";

export type { Control } from "#lib/server/live-board.js";

export type Chamber = "senate" | "house" | "governor";

export type Rating =
  | "won"
  | "projected"
  | "unopposed"
  | "solid"
  | "likely"
  | "lean"
  | "tossup"
  | "counting"
  | "waiting"
  | "later";

export type Seat = {
  summary: Summary;
  held: string | null;
  holder: string;
  side: string | null;
  left: number;
  right: number;
  signed: number | null;
  lean: number;
  band: { low: number; mid: number; high: number } | null;
  rating: Rating;
  isFlip: boolean;
  shift: Shift | null;
  isUpset: boolean;
  remaining: number | null;
};

export type Family = { id: string; name: string; short: string; color: string };

export const CHAMBERS: Record<Chamber, { seats: number; majority: number }> = {
  senate: { seats: 100, majority: 51 },
  house: { seats: 435, majority: 218 },
  governor: { seats: 50, majority: 26 },
};

export const STATES = new Set(
  "al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy".split(
    " ",
  ),
);

export const PRIOR = 0.7;

const RATINGS: Array<[Rating, number]> = [
  ["solid", 0.95],
  ["likely", 0.75],
  ["lean", 0.6],
];

export function family(control: Control | null, option: LiveOption | null) {
  const id = option?.party?.id;
  return id == null ? null : (control?.families[id] ?? id);
}

export function sides(control: Control | null): [Family, Family] | null {
  if (control == null) {
    return null;
  }
  const weight = new Map<string, number>();
  const add = (id: string | null, amount = 1) => {
    if (id != null && id !== "" && control.parties[id] != null) {
      weight.set(id, (weight.get(id) ?? 0) + amount);
    }
  };
  for (const holder of Object.values(control.holders)) {
    add(holder.party);
  }
  for (const side of [control.holdovers.senate, control.holdovers.governor]) {
    for (const [id, amount] of Object.entries(side)) {
      add(id, amount);
    }
  }
  const top = [...weight]
    .toSorted((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([id]) => id);
  if (top.length < 2) {
    return null;
  }
  const ordered = top.toSorted(
    (a, b) =>
      Number(/republican/iu.test(control.parties[a]?.name ?? "")) -
      Number(/republican/iu.test(control.parties[b]?.name ?? "")),
  );
  const [left = "", right = ""] = ordered;
  return [describeFamily(control, left), describeFamily(control, right)];
}

function describeFamily(control: Control, id: string): Family {
  const party = control.parties[id];
  return {
    id,
    name: party?.name ?? "",
    short: party?.short ?? party?.name ?? "",
    color: swatch(party?.color, `party:${id}`),
  };
}

export function isSettled(rating: Rating) {
  return rating === "won" || rating === "projected" || rating === "unopposed";
}

export function seat(
  summary: Summary,
  control: Control | null,
  [left, right]: [Family, Family],
): Seat {
  const { race, estimate, totals, total } = summary;
  const holder = control?.holders[race.id] ?? null;
  const held = holder?.party ?? null;
  let leftVotes = 0;
  let rightVotes = 0;
  for (const [index, option] of race.options.entries()) {
    const id = family(control, option);
    if (id === left.id) {
      leftVotes += totals[index] ?? 0;
    } else if (id === right.id) {
      rightVotes += totals[index] ?? 0;
    }
  }
  const signed =
    total > 0 && isContested(race) ? (leftVotes - rightVotes) / total : null;
  const isEstimating = estimate?.status === "estimating";
  let pLeft = held === left.id ? PRIOR : held === right.id ? 1 - PRIOR : 0.5;
  let pRight = 1 - pLeft;
  let band: Seat["band"] = null;
  const called = summary.settled;
  if (called != null) {
    const id = family(control, called);
    pLeft = id === left.id ? 1 : 0;
    pRight = id === right.id ? 1 : 0;
  } else if (isEstimating) {
    pLeft = 0;
    pRight = 0;
    for (const option of race.options) {
      const id = family(control, option);
      const chance = estimate.winProbability[option.id] ?? 0;
      if (id === left.id) {
        pLeft += chance;
      } else if (id === right.id) {
        pRight += chance;
      }
    }
    const ahead = race.options.find((option) => option.id === estimate.leader);
    const sign =
      family(control, ahead ?? null) === left.id
        ? 1
        : family(control, ahead ?? null) === right.id
          ? -1
          : 0;
    if (sign !== 0 && Number.isFinite(estimate.margin.mid)) {
      band =
        sign > 0
          ? {
              low: estimate.margin.low,
              mid: estimate.margin.mid,
              high: estimate.margin.high,
            }
          : {
              low: -estimate.margin.high,
              mid: -estimate.margin.mid,
              high: -estimate.margin.low,
            };
    }
  }
  const pick = called ?? summary.favorite ?? summary.leader;
  const side = family(control, pick);
  let rating: Rating =
    total > 0 ? "counting" : summary.later == null ? "waiting" : "later";
  if (summary.winner != null) {
    rating = "won";
  } else if (summary.projected != null) {
    rating = "projected";
  } else if (summary.unopposed != null) {
    rating = "unopposed";
  } else if (isEstimating) {
    const top = Math.max(pLeft, pRight);
    rating = RATINGS.find(([, floor]) => top >= floor)?.[0] ?? "tossup";
  }
  const trailing = race.options.some(
    (option) =>
      option.isIncumbent &&
      summary.leader != null &&
      option.id !== summary.leader.id &&
      total > 0,
  );
  let lean = pLeft - pRight;
  if (isSettled(rating)) {
    const sure = rating === "won" ? 2 : 1.5;
    lean = side === left.id ? sure : side === right.id ? -sure : 0;
  } else if (rating === "counting" && signed != null) {
    lean = Math.max(-0.98, Math.min(0.98, signed / 0.2));
  }
  const changing = summary.isShort
    ? null
    : shift({
        held,
        to: called == null ? family(control, summary.leader) : side,
        isCalled: called != null,
        isCounting: total > 0,
      });
  return {
    summary,
    held,
    holder:
      holder == null
        ? ""
        : holder.isVacant
          ? m.night_holder_vacant({ name: holder.name })
          : holder.name,
    side,
    left: pLeft,
    right: pRight,
    signed,
    lean,
    band,
    rating,
    isFlip: changing?.flip === "flipped",
    shift: changing,
    isUpset: trailing,
    remaining:
      isEstimating && summary.race.certified == null
        ? Math.max(0, estimate.expectedTotal.mid - total)
        : null,
  };
}

function convolve(chances: Array<number>, offset: number, size: number) {
  let pmf = [1];
  for (const chance of chances) {
    const next = [...pmf.map((value) => value * (1 - chance)), 0];
    for (const [index, value] of pmf.entries()) {
      next[index + 1] = (next[index + 1] ?? 0) + value * chance;
    }
    pmf = next;
  }
  const full = new Map<number, number>();
  for (const [index, value] of pmf.entries()) {
    const slot = Math.min(size, index + offset);
    full.set(slot, (full.get(slot) ?? 0) + value);
  }
  return Array.from({ length: size + 1 }, (_, slot) => full.get(slot) ?? 0);
}

function quantile(pmf: Array<number>, wanted: number) {
  let running = 0;
  for (const [index, value] of pmf.entries()) {
    running += value;
    if (running >= wanted) {
      return index;
    }
  }
  return pmf.length - 1;
}

export type Tally = {
  won: number;
  projected: number;
  unopposed: number;
  ahead: number;
  holdover: number;
  gains: number;
  losses: number;
};

export function seatsOf(tally: Tally) {
  return (
    tally.won + tally.projected + tally.unopposed + tally.ahead + tally.holdover
  );
}

export function breakdown(tally: Tally) {
  return [
    tally.won > 0 ? m.night_tally_won({ count: tally.won }) : null,
    tally.projected > 0
      ? m.night_tally_projected({ count: tally.projected })
      : null,
    tally.unopposed > 0
      ? m.night_tally_unopposed({ count: tally.unopposed })
      : null,
    m.night_tally_ahead({ count: tally.ahead }),
    m.night_tally_not_up({ count: tally.holdover }),
  ]
    .filter((part) => part != null)
    .join(", ");
}

export function latestCall(seats: Array<Seat>) {
  let latest: string | null = null;
  for (const seat of seats) {
    const at = seat.summary.projectedAt;
    if (at != null && (latest == null || at > latest)) {
      latest = at;
    }
  }
  return latest;
}

export function chamber(
  seats: Array<Seat>,
  name: Chamber,
  holdovers: Record<string, number>,
  [left, right]: [Family, Family],
) {
  const { seats: size, majority } = CHAMBERS[name];
  const blank = (): Tally => ({
    won: 0,
    projected: 0,
    unopposed: 0,
    ahead: 0,
    holdover: 0,
    gains: 0,
    losses: 0,
  });
  const tallies = { left: blank(), right: blank(), other: blank() };
  const key = (id: string | null) =>
    id === left.id ? "left" : id === right.id ? "right" : "other";
  for (const [id, amount] of Object.entries(holdovers)) {
    tallies[key(id === "" ? null : id)].holdover += amount;
  }
  let waiting = 0;
  let later = 0;
  for (const entry of seats) {
    if (entry.rating === "later") {
      later += 1;
    } else if (entry.rating === "unopposed") {
      tallies[key(entry.side)].unopposed += 1;
    } else if (
      entry.side == null ||
      entry.summary.total === 0 ||
      entry.summary.isShort
    ) {
      waiting += 1;
    } else if (entry.rating === "won") {
      tallies[key(entry.side)].won += 1;
    } else if (entry.rating === "projected") {
      tallies[key(entry.side)].projected += 1;
    } else {
      tallies[key(entry.side)].ahead += 1;
    }
    if (entry.isFlip) {
      tallies[key(entry.side)].gains += 1;
      tallies[key(entry.held)].losses += 1;
    }
  }
  const leftPmf = convolve(
    seats.map((entry) => entry.left),
    tallies.left.holdover,
    size,
  );
  const rightPmf = convolve(
    seats.map((entry) => entry.right),
    tallies.right.holdover,
    size,
  );
  const above = (pmf: Array<number>) => {
    let found = 0;
    for (const [index, value] of pmf.entries()) {
      if (index >= majority) {
        found += value;
      }
    }
    return found;
  };
  let expectedLeft = tallies.left.holdover;
  let expectedRight = tallies.right.holdover;
  for (const entry of seats) {
    expectedLeft += entry.left;
    expectedRight += entry.right;
  }
  const order = seats.toSorted(
    (a, b) => b.lean - a.lean || (b.signed ?? 0) - (a.signed ?? 0),
  );
  const needed = majority - tallies.left.holdover;
  return {
    name,
    size,
    majority,
    tallies,
    waiting,
    later,
    pmf: leftPmf,
    range: [quantile(leftPmf, 0.1), quantile(leftPmf, 0.9)] as const,
    expected: { left: expectedLeft, right: expectedRight },
    control: {
      left: above(leftPmf),
      right: above(rightPmf),
      none: Math.max(0, 1 - above(leftPmf) - above(rightPmf)),
    },
    order,
    tipping:
      needed > 0 && needed <= order.length ? (order[needed - 1] ?? null) : null,
    needed,
  };
}

export type ChamberView = ReturnType<typeof chamber>;

export type Trail = Array<{
  at: number;
  left: number;
  right: number;
  other: number;
}>;

export function pulse(
  all: Array<LiveRace>,
  control: Control | null,
  [left, right]: [Family, Family],
): Trail {
  const races = all.filter((race) => race.frames.length > 0);
  const times = races.flatMap((race) =>
    race.frames.map((frame) => Date.parse(frame.at)),
  );
  if (times.length === 0) {
    return [];
  }
  const start = Math.min(...times);
  const end = Math.max(...times);
  const step = Math.max(
    60_000,
    Math.ceil((end - start) / 160 / 60_000) * 60_000,
  );
  const sides = races.map((race) =>
    race.options.map((option) => {
      const id = family(control, option);
      return id === left.id ? 0 : id === right.id ? 1 : 2;
    }),
  );
  const cursors = races.map(() => -1);
  const points: Trail = [];
  for (let at = start; at <= end + step - 1; at += step) {
    const moment = Math.min(at, end);
    const sums = [0, 0, 0];
    for (const [index, race] of races.entries()) {
      let cursor = cursors[index] ?? -1;
      while (
        cursor + 1 < race.frames.length &&
        Date.parse(race.frames[cursor + 1]?.at ?? "") <= moment
      ) {
        cursor += 1;
      }
      cursors[index] = cursor;
      const frame = race.frames[cursor];
      if (frame == null) {
        continue;
      }
      for (const [position, votes] of frame.totals.entries()) {
        const slot = sides[index]?.[position] ?? 2;
        sums[slot] = (sums[slot] ?? 0) + votes;
      }
    }
    points.push({
      at: moment,
      left: sums[0] ?? 0,
      right: sums[1] ?? 0,
      other: sums[2] ?? 0,
    });
  }
  return points;
}

export function odds(value: number) {
  if (value >= 0.995) {
    return `>${percent(0.99, 0)}`;
  }
  return value <= 0.005 ? `<${percent(0.01, 0)}` : percent(value, 0);
}
