import type {
  LiveBaseline,
  LiveEstimate,
  LiveFrame,
  LiveOption,
  LiveRace,
  LiveUnit,
  LiveUnitCount,
} from "@ea/analytics/live";

export type Geography = {
  tiles: string;
  frame: { west: number; south: number; east: number; north: number };
  places: Array<{ areaId: string; k: number; at: [number, number] }>;
};

export type Mode = "lead" | "bubbles" | "shift" | "counted" | "remaining";

export type View = { at: string | null; isFinal: boolean };

export type UnitStanding = {
  unit: LiveUnit;
  votes: Array<number>;
  total: number;
  order: Array<number>;
  leader: number | null;
  lead: number;
  margin: number;
  signed: number | null;
  reporting: number | null;
  expected: number | null;
  at: string | null;
  baseline: LiveBaseline | null;
  baseSigned: number | null;
};

export type Standing = {
  frame: LiveFrame | null;
  at: string | null;
  isFinal: boolean;
  totals: Array<number>;
  total: number;
  order: Array<number>;
  pair: [number, number];
  reporting: number | null;
  expected: number | null;
  units: Map<string, UnitStanding>;
};

export function sum(values: Iterable<number>) {
  let total = 0;
  for (const value of values) {
    total += value;
  }
  return total;
}

export function toward(
  estimate: LiveEstimate | null,
  option: LiveOption | undefined,
) {
  return estimate?.leader == null || estimate.leader === option?.id ? 1 : -1;
}

export function ranked(votes: Array<number>) {
  return votes
    .map((value, index) => ({ value, index }))
    .toSorted((a, b) => b.value - a.value || a.index - b.index)
    .map((entry) => entry.index);
}

export function pair(race: LiveRace): [number, number] {
  const latest =
    race.certified?.totals ??
    race.frames.at(-1)?.totals ??
    race.options.map(() => 0);
  let source = latest;
  if (sum(latest) === 0) {
    source = race.options.map((_, index) =>
      sum(race.baseline.map((row) => (row.shares[index] ?? 0) * row.turnout)),
    );
  }
  const order = ranked(source).filter(
    (index) => race.options[index]?.isWriteIn !== true,
  );
  const first = order[0] ?? 0;
  const second = order[1] ?? 1;
  return first < second ? [first, second] : [second, first];
}

export function frameAt(race: LiveRace, at: string | null) {
  if (at == null) {
    return race.frames.at(-1) ?? null;
  }
  return race.frames.findLast((frame) => frame.at <= at) ?? null;
}

export function countsAt(race: LiveRace, at: string | null) {
  const latest = new Map<string, LiveUnitCount>();
  for (const row of race.counts) {
    if (at != null && row.at > at) {
      continue;
    }
    const held = latest.get(row.unitId);
    if (held == null || held.at <= row.at) {
      latest.set(row.unitId, row);
    }
  }
  return latest;
}

function signedOf(shares: Array<number>, [a, b]: [number, number]) {
  return (shares[a] ?? 0) - (shares[b] ?? 0);
}

export function standing(race: LiveRace, view: View): Standing {
  const two = pair(race);
  const isFinal = view.isFinal && race.certified != null;
  const frame = isFinal ? (race.frames.at(-1) ?? null) : frameAt(race, view.at);
  const totals = isFinal
    ? (race.certified?.totals ?? [])
    : (frame?.totals ?? race.options.map(() => 0));
  const total = sum(totals);
  const counts = countsAt(race, isFinal ? null : (frame?.at ?? view.at ?? ""));
  const bases = new Map(race.baseline.map((row) => [row.unitId, row]));
  const units = new Map<string, UnitStanding>();
  for (const unit of race.units) {
    const row = counts.get(unit.id) ?? null;
    const votes = row?.votes ?? race.options.map(() => 0);
    const unitTotal = sum(votes);
    const order = ranked(votes);
    const first = order[0];
    const second = order[1];
    const base = bases.get(unit.id) ?? null;
    units.set(unit.id, {
      unit,
      votes,
      total: unitTotal,
      order,
      leader: unitTotal > 0 && first != null ? first : null,
      lead:
        unitTotal > 0
          ? (votes[first ?? 0] ?? 0) - (votes[second ?? 1] ?? 0)
          : 0,
      margin:
        unitTotal > 0
          ? ((votes[first ?? 0] ?? 0) - (votes[second ?? 1] ?? 0)) / unitTotal
          : 0,
      signed:
        unitTotal > 0
          ? signedOf(
              votes.map((value) => value / unitTotal),
              two,
            )
          : null,
      reporting: row?.reporting ?? null,
      expected: row?.expected ?? null,
      at: row?.at ?? null,
      baseline: base,
      baseSigned: base == null ? null : signedOf(base.shares, two),
    });
  }
  return {
    frame,
    at: isFinal ? null : (frame?.at ?? null),
    isFinal,
    totals,
    total,
    order: ranked(totals),
    pair: two,
    reporting: frame?.reporting ?? null,
    expected: frame?.expected ?? null,
    units,
  };
}

export function remainder(
  row: UnitStanding,
  estimate: LiveEstimate | null,
  options: Array<LiveOption>,
  pair: [number, number],
) {
  const found = estimate?.units.find((unit) => unit.unitId === row.unit.id);
  const expected =
    found?.expectedTotal.mid ?? row.baseline?.turnout ?? row.total;
  const left = Math.max(0, expected - row.total);
  if (found == null || left < 1) {
    return { expected, left, lean: left < 1 ? null : row.baseSigned };
  }
  const final = found.projectedMargin.mid * toward(estimate, options[pair[0]]);
  const counted = (row.votes[pair[0]] ?? 0) - (row.votes[pair[1]] ?? 0);
  return {
    expected,
    left,
    lean: Math.max(-1, Math.min(1, (final * expected - counted) / left)),
  };
}
