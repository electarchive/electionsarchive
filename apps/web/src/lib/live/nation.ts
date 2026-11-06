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
  party: string | null;
  ally: string | null;
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
  const caucus =
    option?.person == null ? undefined : control?.caucuses[option.person];
  return caucus ?? membership(control, option);
}

function membership(control: Control | null, option: LiveOption | null) {
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
  for (const entry of [
    ...control.holdovers.senate,
    ...control.holdovers.governor,
  ]) {
    add(entry.caucus, entry.count);
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
    party: membership(control, pick),
    ally:
      side != null && side !== membership(control, pick)
        ? (pick?.label ?? null)
        : null,
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
  const parts = [
    tally.won > 0 ? m.night_tally_won({ count: tally.won }) : null,
    tally.projected > 0
      ? m.night_tally_projected({ count: tally.projected })
      : null,
    tally.unopposed > 0
      ? m.night_tally_unopposed({ count: tally.unopposed })
      : null,
    tally.ahead > 0 ? m.night_tally_ahead({ count: tally.ahead }) : null,
    tally.holdover > 0 ? m.night_tally_not_up({ count: tally.holdover }) : null,
  ].filter((part) => part != null);
  return parts.length > 0
    ? parts.join(", ")
    : m.night_tally_ahead({ count: 0 });
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

export type Lane = "left" | "right" | "other" | "tossup" | "waiting" | "later";

export function lane(seat: Seat, [left, right]: [Family, Family]): Lane {
  if (seat.rating === "later") {
    return "later";
  }
  const side =
    seat.side === left.id ? "left" : seat.side === right.id ? "right" : "other";
  if (isSettled(seat.rating)) {
    return side;
  }
  if (
    seat.summary.total === 0 ||
    seat.summary.leader == null ||
    seat.summary.isShort
  ) {
    return "waiting";
  }
  return seat.rating === "tossup" ? "tossup" : side;
}

export type Bloc = {
  side: "left" | "right" | "other";
  party: string;
  label: string | null;
  isAllied: boolean;
  names: Array<string>;
  tally: Tally;
};

export function allied(blocs: Array<Bloc>, side: Bloc["side"]) {
  return blocs.filter((bloc) => bloc.side === side && bloc.isAllied);
}

export function caucusNote(blocs: Array<Bloc>, side: Bloc["side"]) {
  const found = allied(blocs, side);
  let count = 0;
  for (const bloc of found) {
    count += seatsOf(bloc.tally);
  }
  if (count === 0) {
    return null;
  }
  return found.every((bloc) => bloc.label == null)
    ? m.night_caucus_includes({ count })
    : m.night_caucus_includes_others({ count });
}

export function blocName(bloc: Bloc, caucus: Family) {
  if (!bloc.isAllied) {
    return bloc.label ?? m.night_chamber_others();
  }
  return bloc.label == null
    ? m.night_caucus_independents({
        count: Math.max(1, bloc.names.length),
        party: caucus.name,
      })
    : m.night_caucus_party({ party: bloc.label, caucus: caucus.name });
}

export function chamber(
  seats: Array<Seat>,
  name: Chamber,
  control: Control | null,
  pair: [Family, Family],
) {
  const [left, right] = pair;
  const { seats: size, majority } = CHAMBERS[name];
  const holdovers = name === "house" ? [] : (control?.holdovers[name] ?? []);
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
  const blocs: Array<Bloc> = [];
  const bloc = (side: Bloc["side"], party: string | null) => {
    const id = party ?? "";
    const found = blocs.find(
      (entry) => entry.side === side && entry.party === id,
    );
    if (found != null) {
      return found;
    }
    const sideId = side === "left" ? left.id : side === "right" ? right.id : "";
    const made: Bloc = {
      side,
      party: id,
      label:
        id === ""
          ? null
          : (control?.parties[id]?.name ??
            (id === left.id ? left.name : id === right.id ? right.name : null)),
      isAllied: side !== "other" && id !== sideId,
      names: [],
      tally: blank(),
    };
    blocs.push(made);
    return made;
  };
  for (const entry of holdovers) {
    const side = key(entry.caucus === "" ? null : entry.caucus);
    tallies[side].holdover += entry.count;
    const found = bloc(side, side === "other" ? "" : entry.party);
    found.tally.holdover += entry.count;
    found.names.push(...entry.names);
  }
  let waiting = 0;
  let later = 0;
  let tossups = 0;
  for (const entry of seats) {
    const where = lane(entry, pair);
    if (where === "later") {
      later += 1;
    } else if (where === "waiting") {
      waiting += 1;
    } else if (where === "tossup") {
      tossups += 1;
    } else {
      const field =
        entry.rating === "won" ||
        entry.rating === "projected" ||
        entry.rating === "unopposed"
          ? entry.rating
          : "ahead";
      tallies[where][field] += 1;
      const found = bloc(where, where === "other" ? "" : entry.party);
      found.tally[field] += 1;
      if (entry.ally != null) {
        found.names.push(entry.ally);
      }
    }
    if (entry.isFlip) {
      tallies[key(entry.side)].gains += 1;
      tallies[key(entry.held)].losses += 1;
    }
  }
  const tied = size % 2 === 0 ? size / 2 : null;
  const tiebreak =
    tied == null || name !== "senate"
      ? null
      : (control?.tiebreak.senate ?? null);
  const needs = {
    left: tiebreak === left.id && tied != null ? tied : majority,
    right: tiebreak === right.id && tied != null ? tied : majority,
  };
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
  const above = (pmf: Array<number>, floor: number) => {
    let found = 0;
    for (const [index, value] of pmf.entries()) {
      if (index >= floor) {
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
  const needed = needs.left - tallies.left.holdover;
  const leftControl = above(leftPmf, needs.left);
  const rightControl = above(rightPmf, needs.right);
  return {
    name,
    size,
    majority,
    needs,
    tiebreak:
      tiebreak === left.id ? "left" : tiebreak === right.id ? "right" : null,
    isTiebreakPending:
      tied != null && name === "senate" && control?.tiebreak.isPending === true,
    tallies,
    blocs,
    waiting,
    later,
    tossups,
    pmf: leftPmf,
    range: [quantile(leftPmf, 0.1), quantile(leftPmf, 0.9)] as const,
    expected: { left: expectedLeft, right: expectedRight },
    control: {
      left: leftControl,
      right: rightControl,
      none: Math.max(0, 1 - leftControl - rightControl),
    },
    order,
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
