export type LiveOffice = "senate" | "governor" | "house" | "other";

export type LiveOption = {
  id: string;
  label: string;
  party: { id: string; name: string; short: string; color: string } | null;
  isWriteIn: boolean;
  isIncumbent: boolean;
};

export type LiveUnit = {
  id: string;
  areaId: string | null;
  name: string;
  scheme: string;
  parentId: string | null;
};

export type LiveUnitCount = {
  unitId: string;
  at: string;
  votes: Array<number>;
  reporting: number | null;
  expected: number | null;
};

export type LiveFrame = {
  at: string;
  totals: Array<number>;
  reporting: number | null;
  expected: number | null;
};

export type LiveBaseline = {
  unitId: string;
  label: string;
  turnout: number;
  shares: Array<number>;
};

export type LiveRace = {
  id: string;
  slug: string;
  title: string;
  office: LiveOffice;
  state: string;
  district: string | null;
  options: Array<LiveOption>;
  units: Array<LiveUnit>;
  frames: Array<LiveFrame>;
  counts: Array<LiveUnitCount>;
  baseline: Array<LiveBaseline>;
  certified: { totals: Array<number>; winners: Array<string> } | null;
  source: { publisher: string; url: string | null };
};

export type LiveElection = {
  id: string;
  slug: string;
  title: string;
  date: string;
  pollClose: Array<{ state: string; at: string }>;
  newest: string | null;
  isCertified: boolean;
  races: Array<LiveRace>;
};

export type Band = { low: number; mid: number; high: number };

export type LiveEstimate = {
  status: "waiting" | "unavailable" | "too-early" | "estimating" | "settled";
  at: string;
  counted: number;
  expectedTotal: Band;
  shareIn: Band;
  margin: Band;
  leader: string | null;
  runnerUp: string | null;
  winProbability: Record<string, number>;
  units: Array<{ unitId: string; expectedTotal: Band; projectedMargin: Band }>;
};

export type EstimateInput = { race: LiveRace; at: string | null };

const DRAWS = 1000;

type Unit = {
  id: string;
  counted: Array<number>;
  reporting: number;
  turnout: number;
  shares: Array<number>;
  hasBaseline: boolean;
};

function seeded(text: string) {
  let state = 2_166_136_261;
  for (const char of text) {
    state = Math.imul(state ^ (char.codePointAt(0) ?? 0), 16_777_619);
  }
  return () => {
    state = (state + 1_831_565_813) >>> 0;
    let mixed = Math.imul(state ^ (state >>> 15), 1 | state);
    mixed ^= mixed + Math.imul(mixed ^ (mixed >>> 7), 61 | mixed);
    return ((mixed ^ (mixed >>> 14)) >>> 0) / 4_294_967_296;
  };
}

function gaussian(random: () => number) {
  const u = Math.max(random(), Number.EPSILON);
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * random());
}

function band(values: Array<number>): Band {
  const sorted = values.toSorted((a, b) => a - b);
  const pick = (q: number) =>
    sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] ?? 0;
  return { low: pick(0.1), mid: pick(0.5), high: pick(0.9) };
}

function sum(values: Array<number>) {
  let total = 0;
  for (const value of values) {
    total += value;
  }
  return total;
}

function share(reporting: number | null, expected: number | null) {
  return reporting != null && expected != null && expected > 0
    ? Math.min(1, reporting / expected)
    : null;
}

function snapshot(race: LiveRace, at: string | null) {
  const latest = new Map<string, LiveUnitCount>();
  for (const count of race.counts) {
    if (at != null && count.at > at) {
      continue;
    }
    const seen = latest.get(count.unitId);
    if (seen == null || seen.at < count.at) {
      latest.set(count.unitId, count);
    }
  }
  let frame: LiveFrame | null = null;
  for (const candidate of race.frames) {
    if (
      (at == null || candidate.at <= at) &&
      (frame == null || frame.at < candidate.at)
    ) {
      frame = candidate;
    }
  }
  return { latest, frame };
}

function units(race: LiveRace, at: string | null) {
  const { latest, frame } = snapshot(race, at);
  const width = race.options.length;
  const baselines = new Map(race.baseline.map((row) => [row.unitId, row]));
  const found: Array<Unit> = [];
  if (latest.size > 0) {
    const ids = new Set([...baselines.keys(), ...latest.keys()]);
    for (const id of ids) {
      const count = latest.get(id);
      const base = baselines.get(id);
      const counted = count?.votes ?? race.options.map(() => 0);
      const total = sum(counted);
      found.push({
        id,
        counted,
        reporting:
          share(count?.reporting ?? null, count?.expected ?? null) ??
          (total > 0 && base != null ? Math.min(1, total / base.turnout) : 0),
        turnout: base?.turnout ?? total,
        shares:
          base?.shares ??
          counted.map((votes) => (total > 0 ? votes / total : 0)),
        hasBaseline: base != null,
      });
    }
  } else if (frame != null || race.baseline.length > 0) {
    const turnout = sum(race.baseline.map((row) => row.turnout));
    const shares = Array.from({ length: width }, (_, index) =>
      turnout > 0
        ? sum(
            race.baseline.map((row) => (row.shares[index] ?? 0) * row.turnout),
          ) / turnout
        : 0,
    );
    const counted = frame?.totals ?? race.options.map(() => 0);
    found.push({
      id: race.id,
      counted,
      reporting:
        share(frame?.reporting ?? null, frame?.expected ?? null) ??
        (turnout > 0 ? Math.min(1, sum(counted) / turnout) : 0),
      turnout: turnout > 0 ? turnout : sum(counted),
      shares,
      hasBaseline: race.baseline.length > 0,
    });
  }
  return found;
}

function pair(race: LiveRace, found: Array<Unit>) {
  const totals = race.options.map((_, index) =>
    sum(found.map((unit) => unit.counted[index] ?? 0)),
  );
  const prior = race.options.map((_, index) =>
    sum(found.map((unit) => (unit.shares[index] ?? 0) * unit.turnout)),
  );
  const weight = sum(totals) > 0 ? 1 : 0;
  const order = race.options
    .map((option, index) => ({
      index,
      score: weight * totals[index]! + (1 - weight) * prior[index]!,
      isWriteIn: option.isWriteIn,
    }))
    .filter((row) => !row.isWriteIn)
    .toSorted((a, b) => b.score - a.score);
  return [order[0]?.index ?? 0, order[1]?.index ?? 1] as const;
}

export function estimate({ race, at }: EstimateInput): LiveEstimate {
  const stamp = at ?? race.frames.at(-1)?.at ?? "";
  const found = units(race, at);
  const counted = sum(found.map((unit) => sum(unit.counted)));
  const empty: LiveEstimate = {
    status: "waiting",
    at: stamp,
    counted,
    expectedTotal: { low: counted, mid: counted, high: counted },
    shareIn: { low: 0, mid: 0, high: 0 },
    margin: { low: 0, mid: 0, high: 0 },
    leader: null,
    runnerUp: null,
    winProbability: {},
    units: [],
  };
  if (race.options.length < 2 || found.length === 0) {
    return empty;
  }
  if (counted === 0) {
    return empty;
  }
  const [a, b] = pair(race, found);
  const optionA = race.options[a]!;
  const optionB = race.options[b]!;
  if (race.certified != null) {
    const totals =
      race.certified?.totals ??
      race.options.map((_, index) =>
        sum(found.map((unit) => unit.counted[index] ?? 0)),
      );
    const all = sum(totals);
    const margin = all > 0 ? (totals[a]! - totals[b]!) / all : 0;
    const [leader, runnerUp] =
      margin >= 0 ? [optionA, optionB] : [optionB, optionA];
    return {
      ...empty,
      status: "settled",
      counted: all,
      expectedTotal: { low: all, mid: all, high: all },
      shareIn: { low: 1, mid: 1, high: 1 },
      margin: {
        low: Math.abs(margin),
        mid: Math.abs(margin),
        high: Math.abs(margin),
      },
      leader: leader.id,
      runnerUp: runnerUp.id,
      winProbability: { [leader.id]: 1, [runnerUp.id]: 0 },
      units: found.map((unit) => {
        const total = sum(unit.counted);
        const unitMargin =
          total > 0 ? (unit.counted[a]! - unit.counted[b]!) / total : 0;
        return {
          unitId: unit.id,
          expectedTotal: { low: total, mid: total, high: total },
          projectedMargin: {
            low: unitMargin,
            mid: unitMargin,
            high: unitMargin,
          },
        };
      }),
    };
  }
  if (found.every((unit) => !unit.hasBaseline)) {
    return {
      ...empty,
      status: "unavailable",
      leader: optionA.id,
      runnerUp: optionB.id,
    };
  }
  const baseTurnout = sum(found.map((unit) => unit.turnout));
  const complete = found.filter(
    (unit) => unit.reporting >= 0.95 && unit.hasBaseline,
  );
  const completeBase = sum(complete.map((unit) => unit.turnout));
  const completeCount = sum(complete.map((unit) => sum(unit.counted)));
  const coverage = baseTurnout > 0 ? completeBase / baseTurnout : 0;
  const ratio =
    completeBase > 0
      ? (completeCount / completeBase) * Math.min(1, coverage * 4) +
        (1 - Math.min(1, coverage * 4))
      : 1;
  const swings: Array<{ swing: number; weight: number }> = [];
  for (const unit of found) {
    const total = sum(unit.counted);
    if (!unit.hasBaseline || total <= 0) {
      continue;
    }
    const observed = (unit.counted[a]! - unit.counted[b]!) / total;
    const prior = unit.shares[a]! - unit.shares[b]!;
    swings.push({
      swing: observed - prior,
      weight: total * (0.25 + 0.75 * unit.reporting),
    });
  }
  const swingWeight = sum(swings.map((row) => row.weight));
  const swing =
    swingWeight > 0
      ? sum(swings.map((row) => row.swing * row.weight)) / swingWeight
      : 0;
  const spread =
    swingWeight > 0
      ? Math.sqrt(
          sum(swings.map((row) => row.weight * (row.swing - swing) ** 2)) /
            swingWeight,
        )
      : 0.1;
  const effective =
    swingWeight > 0
      ? swingWeight ** 2 / sum(swings.map((row) => row.weight ** 2))
      : 0;
  const swingError = Math.max(
    0.012,
    spread / Math.sqrt(Math.max(1, effective)) + 0.05 * (1 - coverage),
  );
  const others = (unit: Unit) =>
    Math.max(0, 1 - (unit.shares[a] ?? 0) - (unit.shares[b] ?? 0));
  const random = seeded(`${race.id}@${stamp}`);
  const finalMargins: Array<number> = [];
  const finalTotals: Array<number> = [];
  const shareIn: Array<number> = [];
  let winsA = 0;
  const perUnit = found.map(() => ({
    totals: [] as Array<number>,
    margins: [] as Array<number>,
  }));
  for (let draw = 0; draw < DRAWS; draw += 1) {
    const drawSwing = swing + swingError * gaussian(random);
    const drawRatio =
      ratio * Math.exp(0.06 * (1 - coverage) * gaussian(random));
    let votesA = 0;
    let votesB = 0;
    let total = 0;
    for (const [index, unit] of found.entries()) {
      const countedTotal = sum(unit.counted);
      const prior = unit.turnout * drawRatio;
      const byReporting =
        unit.reporting > 0.05 ? countedTotal / unit.reporting : prior;
      const trust = Math.sqrt(unit.reporting);
      const expected = Math.max(
        countedTotal,
        (trust * byReporting + (1 - trust) * prior) *
          Math.exp(0.1 * (1 - unit.reporting) * gaussian(random)),
      );
      const remaining = expected - countedTotal;
      const observed =
        countedTotal > 0
          ? (unit.counted[a]! - unit.counted[b]!) / countedTotal
          : 0;
      const prior2 = (unit.shares[a] ?? 0) - (unit.shares[b] ?? 0) + drawSwing;
      const lean = countedTotal > 0 ? 0.5 * unit.reporting : 0;
      const remainingMargin = Math.max(
        -1,
        Math.min(
          1,
          lean * observed +
            (1 - lean) * prior2 +
            (0.03 + 0.07 * (1 - unit.reporting)) * gaussian(random),
        ),
      );
      const twoParty = remaining * (1 - others(unit));
      const unitA = unit.counted[a]! + (twoParty * (1 + remainingMargin)) / 2;
      const unitB = unit.counted[b]! + (twoParty * (1 - remainingMargin)) / 2;
      votesA += unitA;
      votesB += unitB;
      total += expected;
      perUnit[index]!.totals.push(expected);
      perUnit[index]!.margins.push(
        expected > 0 ? (unitA - unitB) / expected : 0,
      );
    }
    finalMargins.push(total > 0 ? (votesA - votesB) / total : 0);
    finalTotals.push(total);
    shareIn.push(total > 0 ? Math.min(1, counted / total) : 0);
    if (votesA > votesB) {
      winsA += 1;
    }
  }
  const margin = band(finalMargins);
  const flip = margin.mid < 0;
  const leader = flip ? optionB : optionA;
  const runnerUp = flip ? optionA : optionB;
  const shareInBand = band(shareIn);
  const reportedUnits = found.filter((unit) => sum(unit.counted) > 0).length;
  const status =
    shareInBand.mid < 0.1 || reportedUnits < Math.min(3, found.length)
      ? "too-early"
      : "estimating";
  return {
    status,
    at: stamp,
    counted,
    expectedTotal: band(finalTotals),
    shareIn: shareInBand,
    margin: flip
      ? { low: -margin.high, mid: -margin.mid, high: -margin.low }
      : margin,
    leader: leader.id,
    runnerUp: runnerUp.id,
    winProbability: {
      [optionA.id]: winsA / DRAWS,
      [optionB.id]: 1 - winsA / DRAWS,
    },
    units: found.map((unit, index) => {
      const projected = band(perUnit[index]!.margins);
      return {
        unitId: unit.id,
        expectedTotal: band(perUnit[index]!.totals),
        projectedMargin: flip
          ? { low: -projected.high, mid: -projected.mid, high: -projected.low }
          : projected,
      };
    }),
  };
}
