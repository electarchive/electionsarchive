export type LiveOffice = "senate" | "governor" | "house" | "other";

export type LiveOption = {
  id: string;
  label: string;
  person: string | null;
  photo: string | null;
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
  expected?: number;
  traits?: Array<number>;
};

export type LiveRace = {
  id: string;
  slug: string;
  title: string;
  office: LiveOffice;
  state: string;
  district: string | null;
  area?: string | null;
  options: Array<LiveOption>;
  units: Array<LiveUnit>;
  frames: Array<LiveFrame>;
  counts: Array<LiveUnitCount>;
  baseline: Array<LiveBaseline>;
  certified: { totals: Array<number>; winners: Array<string> } | null;
  projected?: { option: string; at: string } | null;
  source: { publisher: string; url: string | null };
  jurisdiction?: {
    name: string;
    kind: "county" | "municipal" | "school" | "special" | "judicial" | "state";
  } | null;
  seats?: number;
  isMeasure?: boolean;
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

const MODEL = {
  draws: 500,
  spread: 0.12,
  sizeRef: 20_000,
  sizeExp: 0.25,
  sizeLow: 0.3,
  partial: 0.05,
  residual: 0.04,
  spike: 0.003,
  priors: { swing: 0.1, slope: 0.15, size: 0.015, order: 0.15, trait: 0.015 },
  turnout: 0.12,
  same: { center: 0.06, spread: 0.08 },
  drift: 0.06,
  quiet: 15,
  pause: 2,
  moving: 0.1,
  shift: 0.028,
  surge: 0.14,
  tail: {
    share: 0.035,
    spread: 0.5,
    lean: 0.06,
    local: 0.05,
    grace: 1,
    pace: 13,
  },
};

const TAILS: Record<string, number> = {
  ak: 0.4,
  az: 0.3,
  ca: 0.6,
  co: 0.1,
  dc: 0.1,
  fl: 0.01,
  hi: 0.05,
  il: 0.08,
  md: 0.2,
  nj: 0.12,
  nv: 0.25,
  ny: 0.08,
  or: 0.4,
  ut: 0.3,
  wa: 0.5,
};

const TAUS = 41;
const PIS = [0.05, 0.2, 0.4, 0.6, 0.8, 0.95];
const SCALES = [0.4, 0.55, 0.75, 1, 1.35];

type Unit = {
  id: string;
  counted: Array<number>;
  total: number;
  margin: number;
  others: number;
  early: { total: number; margin: number } | null;
  expected: number;
  prior: number;
  features: Array<number>;
  traits: Array<number> | null;
  settled: number;
  sigma: number;
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

function clamp(value: number, low: number, high: number) {
  return Math.min(high, Math.max(low, value));
}

function series(race: LiveRace, at: string | null) {
  const byUnit = new Map<string, Array<LiveUnitCount>>();
  for (const count of race.counts) {
    if (at != null && count.at > at) {
      continue;
    }
    const list = byUnit.get(count.unitId);
    if (list == null) {
      byUnit.set(count.unitId, [count]);
    } else {
      list.push(count);
    }
  }
  if (byUnit.size === 0) {
    const frames = race.frames
      .filter((frame) => at == null || frame.at <= at)
      .map((frame) => ({
        unitId: race.id,
        at: frame.at,
        votes: frame.totals,
        reporting: frame.reporting,
        expected: frame.expected,
      }));
    if (frames.length > 0) {
      byUnit.set(race.id, frames);
    }
  }
  return byUnit;
}

function early(list: Array<LiveUnitCount>, a: number, b: number) {
  const ordered = list.toSorted((x, y) => x.at.localeCompare(y.at));
  const last = ordered.at(-1);
  const total = last == null ? 0 : sum(last.votes);
  let best: { total: number; margin: number } | null = null;
  let score = 0;
  for (const count of ordered) {
    const seen = sum(count.votes);
    if (seen <= 0 || seen >= total * 0.9) {
      continue;
    }
    const value = seen * (total - seen);
    if (value > score) {
      score = value;
      best = {
        total: seen,
        margin: ((count.votes[a] ?? 0) - (count.votes[b] ?? 0)) / seen,
      };
    }
  }
  return best;
}

function pair(race: LiveRace, latest: Map<string, Array<number>>) {
  const totals = race.options.map(() => 0);
  for (const votes of latest.values()) {
    for (const [index, value] of votes.entries()) {
      totals[index] = (totals[index] ?? 0) + value;
    }
  }
  const prior = race.options.map((_, index) =>
    sum(race.baseline.map((row) => (row.shares[index] ?? 0) * row.turnout)),
  );
  const counted = sum(totals) > 0;
  const order = race.options
    .map((option, index) => ({
      index,
      score: counted ? totals[index]! : prior[index]!,
      isWriteIn: option.isWriteIn,
    }))
    .filter((row) => !row.isWriteIn)
    .toSorted((x, y) => y.score - x.score);
  return [order[0]?.index ?? 0, order[1]?.index ?? 1] as const;
}

function units(race: LiveRace, at: string | null) {
  const tail = TAILS[race.state] ?? MODEL.tail.share;
  const byUnit = series(race, at);
  const latest = new Map<string, Array<number>>();
  const changed = new Map<string, string>();
  const waits = new Map<string, number>();
  let now = at ?? "";
  for (const [id, list] of byUnit) {
    let pick = list[0]!;
    for (const count of list) {
      if (count.at > pick.at) {
        pick = count;
      }
    }
    const total = sum(pick.votes);
    let since = pick.at;
    for (const count of list) {
      if (count.at < since && sum(count.votes) === total) {
        since = count.at;
      }
    }
    latest.set(id, pick.votes);
    changed.set(id, since);
    const steps: Array<number> = [];
    let last = -1;
    for (const count of list.toSorted((x, y) => x.at.localeCompare(y.at))) {
      const value = sum(count.votes);
      if (value !== last) {
        steps.push(Date.parse(count.at));
        last = value;
      }
    }
    const gaps = steps
      .slice(1)
      .map((time, index) => (time - steps[index]!) / 60_000)
      .toSorted((x, y) => x - y);
    waits.set(
      id,
      Math.max(
        MODEL.quiet,
        MODEL.pause * (gaps[Math.floor(gaps.length / 2)] ?? 0),
      ),
    );
    if (pick.at > now) {
      now = pick.at;
    }
  }
  const clock = Date.parse(now);
  const [a, b] = pair(race, latest);
  const pooled =
    byUnit.has(race.id) && race.baseline.every((row) => row.unitId !== race.id);
  const rows: Array<LiveBaseline> = pooled
    ? [
        {
          unitId: race.id,
          label: race.title,
          turnout: sum(race.baseline.map((row) => row.turnout)),
          expected: sum(
            race.baseline.map((row) => row.expected ?? row.turnout),
          ),
          shares: race.options.map((_, index) => {
            const all = sum(race.baseline.map((row) => row.turnout));
            return all > 0
              ? sum(
                  race.baseline.map(
                    (row) => (row.shares[index] ?? 0) * row.turnout,
                  ),
                ) / all
              : 0;
          }),
        },
      ].filter((row) => row.turnout > 0)
    : race.baseline;
  const baselines = new Map(rows.map((row) => [row.unitId, row]));
  const found: Array<Unit> = [];
  for (const id of new Set([...baselines.keys(), ...latest.keys()])) {
    const counted = latest.get(id) ?? race.options.map(() => 0);
    const total = sum(counted);
    const base = baselines.get(id);
    const list = byUnit.get(id);
    found.push({
      id,
      counted,
      total,
      margin: total > 0 ? ((counted[a] ?? 0) - (counted[b] ?? 0)) / total : 0,
      others: clamp(
        1 -
          (total > 0
            ? ((counted[a] ?? 0) + (counted[b] ?? 0)) / total
            : (base?.shares[a] ?? 0) + (base?.shares[b] ?? 0)),
        0,
        1,
      ),
      early: list == null ? null : early(list, a, b),
      expected: base == null ? 0 : (base.expected ?? base.turnout) / (1 + tail),
      prior: base == null ? 0 : (base.shares[a] ?? 0) - (base.shares[b] ?? 0),
      features: [],
      traits: base?.traits ?? null,
      settled: changed.has(id)
        ? (clock - Date.parse(changed.get(id)!)) / 60_000 >=
          (waits.get(id) ?? MODEL.quiet)
          ? 1
          : MODEL.moving
        : 1,
      sigma: MODEL.spread,
      hasBaseline: base != null && base.turnout > 0,
    });
  }
  const sized = found.filter((unit) => unit.hasBaseline);
  const weight = sum(sized.map((unit) => unit.expected));
  if (weight > 0) {
    const mean =
      sum(sized.map((unit) => unit.expected * Math.log(unit.expected))) /
      weight;
    const spread = Math.sqrt(
      sum(
        sized.map(
          (unit) => unit.expected * (Math.log(unit.expected) - mean) ** 2,
        ),
      ) / weight,
    );
    const width = Math.max(0, ...sized.map((unit) => unit.traits?.length ?? 0));
    const scales = Array.from({ length: width }, (_, index) => {
      const known = sized.filter((unit) => unit.traits?.[index] != null);
      const total = sum(known.map((unit) => unit.expected));
      const middle =
        total > 0
          ? sum(known.map((unit) => unit.expected * unit.traits![index]!)) /
            total
          : 0;
      const deviation =
        total > 0
          ? Math.sqrt(
              sum(
                known.map(
                  (unit) =>
                    unit.expected * (unit.traits![index]! - middle) ** 2,
                ),
              ) / total,
            )
          : 0;
      return { middle, deviation };
    });
    for (const unit of sized) {
      unit.features = [
        spread > 0 ? (Math.log(unit.expected) - mean) / spread : 0,
        ...scales.map(({ middle, deviation }, index) => {
          const value = unit.traits?.[index];
          return value == null || deviation <= 0
            ? 0
            : (value - middle) / deviation;
        }),
      ];
      unit.sigma =
        MODEL.spread *
        clamp(
          (MODEL.sizeRef / unit.expected) ** MODEL.sizeExp,
          MODEL.sizeLow,
          1,
        );
    }
  }
  return { found, a, b };
}

function logcdf(x: number) {
  if (x < -6) {
    return (-x * x) / 2 - Math.log(-x) - 0.5 * Math.log(2 * Math.PI);
  }
  const t = 1 / (1 + 0.3275911 * Math.abs(x / Math.SQRT2));
  const tail =
    t *
    (0.254829592 +
      t *
        (-0.284496736 +
          t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))) *
    Math.exp((-x * x) / 2);
  return Math.log(x < 0 ? tail / 2 : 1 - tail / 2);
}

function components(unit: Unit, tau: number, pi: number, scale: number) {
  const sigma = unit.sigma * scale;
  const ell = Math.log(unit.total / unit.expected) - tau;
  const p = clamp(pi * unit.settled, 1e-6, 1 - 1e-6);
  const mu = -ell - sigma * sigma;
  const done =
    Math.log(p) -
    (ell * ell) / (2 * sigma * sigma) -
    Math.log(sigma * Math.sqrt(2 * Math.PI));
  const open = Math.log(1 - p) + ell + (sigma * sigma) / 2 + logcdf(mu / sigma);
  return { done, open, mu };
}

function truncated(mu: number, sigma: number, random: () => number) {
  if (mu / sigma > -1) {
    for (;;) {
      const value = mu + sigma * gaussian(random);
      if (value >= 0) {
        return value;
      }
    }
  }
  const bound = -mu / sigma;
  const rate = (bound + Math.sqrt(bound * bound + 4)) / 2;
  for (;;) {
    const z = bound - Math.log(Math.max(random(), Number.EPSILON)) / rate;
    if (random() <= Math.exp(-((z - rate) ** 2) / 2)) {
      return mu + sigma * z;
    }
  }
}

function turnout(found: Array<Unit>, center: number, spread: number) {
  const step = (8 * spread) / (TAUS - 1);
  const taus = Array.from(
    { length: TAUS },
    (_, index) => center - 4 * spread + index * step,
  );
  const reported = found.filter((unit) => unit.hasBaseline && unit.total > 0);
  const logs: Array<number> = [];
  for (const tau of taus) {
    for (const scale of SCALES) {
      for (const pi of PIS) {
        let log = (-0.5 * (tau - center) ** 2) / (spread * spread);
        for (const unit of reported) {
          const { done, open } = components(unit, tau, pi, scale);
          const top = Math.max(done, open);
          log += top + Math.log(Math.exp(done - top) + Math.exp(open - top));
        }
        logs.push(log);
      }
    }
  }
  const top = Math.max(...logs);
  const weights = logs.map((log) => Math.exp(log - top));
  const all = sum(weights);
  const cumulative: Array<number> = [];
  let running = 0;
  for (const weight of weights) {
    running += weight / all;
    cumulative.push(running);
  }
  return {
    draw(random: () => number) {
      const u = random();
      let cell = cumulative.findIndex((value) => value >= u);
      if (cell < 0) {
        cell = cumulative.length - 1;
      }
      const tau =
        taus[Math.floor(cell / (PIS.length * SCALES.length))]! +
        (random() - 0.5) * step;
      return { cell, tau };
    },
    unit(
      unit: Unit,
      cell: number,
      tau: number,
      surge: number,
      random: () => number,
    ) {
      const scale = SCALES[Math.floor(cell / PIS.length) % SCALES.length]!;
      if (unit.total <= 0) {
        return (
          unit.expected *
          Math.exp(tau + surge + unit.sigma * scale * gaussian(random))
        );
      }
      const { done, open, mu } = components(
        unit,
        tau,
        PIS[cell % PIS.length]!,
        scale,
      );
      if (random() < 1 / (1 + Math.exp(open - done))) {
        return (
          unit.total *
          (1 + MODEL.spike * -Math.log(Math.max(random(), Number.EPSILON)))
        );
      }
      return unit.total * Math.exp(truncated(mu, unit.sigma * scale, random));
    },
  };
}

function solve(
  matrix: Array<number>,
  vector: Array<number>,
  random: (() => number) | null,
) {
  const n = vector.length;
  const lower = new Float64Array(n * n);
  for (let i = 0; i < n; i += 1) {
    for (let j = 0; j <= i; j += 1) {
      let value = matrix[i * n + j]!;
      for (let k = 0; k < j; k += 1) {
        value -= lower[i * n + k]! * lower[j * n + k]!;
      }
      lower[i * n + j] =
        i === j ? Math.sqrt(Math.max(value, 1e-12)) : value / lower[j * n + j]!;
    }
  }
  const y = new Float64Array(n);
  for (let i = 0; i < n; i += 1) {
    let value = vector[i]!;
    for (let k = 0; k < i; k += 1) {
      value -= lower[i * n + k]! * y[k]!;
    }
    y[i] = value / lower[i * n + i]!;
  }
  const noise = Array.from({ length: n }, () =>
    random == null ? 0 : gaussian(random),
  );
  const x = new Float64Array(n);
  for (let i = n - 1; i >= 0; i -= 1) {
    let value = y[i]! + noise[i]!;
    for (let k = i + 1; k < n; k += 1) {
      value -= lower[k * n + i]! * x[k]!;
    }
    x[i] = value / lower[i * n + i]!;
  }
  return x;
}

function partial(fraction: number) {
  return (
    (MODEL.partial ** 2 * (1 - fraction)) / Math.max(fraction, 0.05) +
    (MODEL.drift * (1 - fraction)) ** 2
  );
}

function predict(coefficients: Float64Array, unit: Unit, center: number) {
  let value =
    unit.prior + coefficients[0]! + coefficients[1]! * (unit.prior - center);
  for (let index = 2; index < coefficients.length - 1; index += 1) {
    value += coefficients[index]! * (unit.features[index - 2] ?? 0);
  }
  return value;
}

function fit(
  found: Array<Unit>,
  totals: Array<number>,
  center: number,
  residual: Array<number>,
  random: (() => number) | null,
) {
  const { swing, slope, size, order, trait } = MODEL.priors;
  const traits = Math.max(1, ...found.map((unit) => unit.features.length)) - 1;
  const priors = [swing, slope, size];
  for (let index = 0; index < traits; index += 1) {
    priors.push(trait);
  }
  priors.push(order);
  const width = priors.length;
  const matrix = Array.from({ length: width * width }, (_, index) =>
    index % (width + 1) === 0 ? 1 / priors[index / (width + 1)]! ** 2 : 0,
  );
  const vector = priors.map(() => 0);
  const add = (row: Array<number>, y: number, variance: number) => {
    const w = 1 / variance;
    for (let i = 0; i < width; i += 1) {
      vector[i] = vector[i]! + w * row[i]! * y;
      for (let j = 0; j < width; j += 1) {
        matrix[i * width + j] = matrix[i * width + j]! + w * row[i]! * row[j]!;
      }
    }
  };
  const features = (unit: Unit) =>
    Array.from({ length: traits + 1 }, (_, index) => unit.features[index] ?? 0);
  for (const [index, unit] of found.entries()) {
    if (!unit.hasBaseline || unit.total <= 0) {
      continue;
    }
    const fraction = Math.min(1, unit.total / totals[index]!);
    add(
      [1, unit.prior - center, ...features(unit), 1 - fraction],
      unit.margin - unit.prior,
      residual[index]! ** 2 + partial(fraction),
    );
    if (unit.early != null) {
      const first = Math.min(1, unit.early.total / totals[index]!);
      const step = Math.max(0.05, fraction - first);
      const increment =
        (unit.margin * unit.total - unit.early.margin * unit.early.total) /
        (unit.total - unit.early.total);
      add(
        [...priors.slice(1).map(() => 0), fraction],
        unit.early.margin - increment,
        MODEL.partial ** 2 * (1 / Math.max(first, 0.05) + 1 / step),
      );
    }
  }
  return solve(matrix, vector, random);
}

export function estimate({ race, at }: EstimateInput): LiveEstimate {
  const stamp = at ?? race.frames.at(-1)?.at ?? "";
  const { found, a, b } = units(race, at);
  const tail = TAILS[race.state] ?? MODEL.tail.share;
  const counted = sum(found.map((unit) => unit.total));
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
  if (race.options.length < 2 || found.length === 0 || counted === 0) {
    return empty;
  }
  const optionA = race.options[a]!;
  const optionB = race.options[b]!;
  const opened = race.frames.find((frame) => sum(frame.totals) > 0)?.at;
  const days =
    opened == null
      ? 0
      : Math.max(0, (Date.parse(stamp) - Date.parse(opened)) / 86_400_000);
  const left =
    tail *
    Math.exp(-Math.max(0, days - MODEL.tail.grace) / (MODEL.tail.pace * tail));
  if (race.certified != null) {
    const totals = race.certified.totals;
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
      units: found.map((unit) => ({
        unitId: unit.id,
        expectedTotal: { low: unit.total, mid: unit.total, high: unit.total },
        projectedMargin: {
          low: unit.margin,
          mid: unit.margin,
          high: unit.margin,
        },
      })),
    };
  }
  const based = found.filter((unit) => unit.hasBaseline);
  if (based.length === 0) {
    return {
      ...empty,
      status: "unavailable",
      leader: optionA.id,
      runnerUp: optionB.id,
    };
  }
  const weight = sum(based.map((unit) => unit.expected));
  const center = sum(based.map((unit) => unit.expected * unit.prior)) / weight;
  const median = based.map((unit) => unit.expected).toSorted((x, y) => x - y)[
    Math.floor(based.length / 2)
  ]!;
  const scale = found.map((unit) =>
    unit.hasBaseline ? clamp((median / unit.expected) ** 0.15, 0.6, 1.8) : 1,
  );
  const model = race.baseline.some((row) => row.expected != null)
    ? turnout(found, 0, MODEL.turnout)
    : turnout(found, MODEL.same.center, MODEL.same.spread);
  const random = seeded(`${race.id}@${stamp}`);
  const typical = found.map((unit) => {
    if (!unit.hasBaseline) {
      return unit.total;
    }
    return unit.total > 0 ? Math.max(unit.total, unit.expected) : unit.expected;
  });
  const first = fit(
    found,
    typical,
    center,
    scale.map((value) => MODEL.residual * value),
    null,
  );
  let squares = 5 * MODEL.residual ** 2;
  let weights = 5;
  for (const [index, unit] of found.entries()) {
    const fraction = unit.hasBaseline ? unit.total / typical[index]! : 0;
    if (!unit.hasBaseline || fraction < 0.6) {
      continue;
    }
    const predicted =
      predict(first, unit, center) +
      first.at(-1)! * (1 - Math.min(1, fraction));
    squares +=
      Math.max(
        0,
        (unit.margin - predicted) ** 2 - partial(Math.min(1, fraction)),
      ) /
      scale[index]! ** 2;
    weights += 1;
  }
  const residual = Math.max(0.015, Math.sqrt(squares / weights));
  const residuals = scale.map((value) => residual * value);
  const finalMargins: Array<number> = [];
  const finalTotals: Array<number> = [];
  const shareIn: Array<number> = [];
  let winsA = 0;
  const perUnit = found.map(() => ({
    totals: [] as Array<number>,
    margins: [] as Array<number>,
  }));
  const totals = found.map(() => 0);
  for (let draw = 0; draw < MODEL.draws; draw += 1) {
    const { cell, tau } = model.draw(random);
    const surge = MODEL.surge * gaussian(random);
    const shift = MODEL.shift * gaussian(random);
    const late =
      left *
      Math.exp(
        MODEL.tail.spread * gaussian(random) - MODEL.tail.spread ** 2 / 2,
      );
    const lean = MODEL.tail.lean * gaussian(random);
    const fractions: Array<number> = [];
    for (const [index, unit] of found.entries()) {
      if (unit.hasBaseline) {
        totals[index] = model.unit(unit, cell, tau, surge, random);
        if (unit.total > 0) {
          fractions.push(unit.total / totals[index]);
        }
      }
    }
    const typicalFraction =
      fractions.length > 0
        ? fractions.toSorted((x, y) => x - y)[Math.floor(fractions.length / 2)]!
        : 0.7;
    for (const [index, unit] of found.entries()) {
      if (!unit.hasBaseline) {
        totals[index] = Math.max(
          unit.total,
          (unit.total / Math.max(typicalFraction, 0.2)) *
            Math.exp(MODEL.spread * gaussian(random)),
        );
      }
    }
    const coefficients = fit(found, totals, center, residuals, random);
    const order = coefficients.at(-1)!;
    let votes = 0;
    let all = 0;
    for (const [index, unit] of found.entries()) {
      const final = totals[index]!;
      const fraction = Math.min(1, unit.total / final);
      const prior = unit.hasBaseline
        ? predict(coefficients, unit, center)
        : unit.margin;
      const priorVariance = residuals[index]! ** 2;
      let margin: number;
      if (unit.total > 0) {
        const noise = partial(fraction);
        const observed = unit.margin - order * (1 - fraction);
        const precision = 1 / priorVariance + (noise > 0 ? 1 / noise : 1e12);
        const mean =
          (prior / priorVariance +
            (noise > 0 ? observed / noise : observed * 1e12)) /
          precision;
        margin = mean + gaussian(random) / Math.sqrt(precision);
      } else {
        margin = prior + Math.sqrt(priorVariance) * gaussian(random);
      }
      margin += shift * Math.sqrt(1 - fraction);
      const remaining = final - unit.total;
      const room = remaining * (1 - unit.others);
      const countedLead = unit.margin * unit.total;
      const lead = clamp(margin * final - countedLead, -room, room);
      const extra = final * late;
      const ceiling = 1 - unit.others;
      const unitVotes =
        countedLead +
        lead +
        (final > 0
          ? extra *
            clamp(
              (countedLead + lead) / final +
                lean +
                MODEL.tail.local * gaussian(random),
              -ceiling,
              ceiling,
            )
          : 0);
      const unitTotal = final + extra;
      votes += unitVotes;
      all += unitTotal;
      perUnit[index]!.totals.push(unitTotal);
      perUnit[index]!.margins.push(unitTotal > 0 ? unitVotes / unitTotal : 0);
    }
    finalMargins.push(all > 0 ? votes / all : 0);
    finalTotals.push(all);
    shareIn.push(all > 0 ? Math.min(1, counted / all) : 0);
    if (votes > 0) {
      winsA += 1;
    }
  }
  const margin = band(finalMargins);
  const flip = margin.mid < 0;
  const leader = flip ? optionB : optionA;
  const runnerUp = flip ? optionA : optionB;
  const shareInBand = band(shareIn);
  const reportedUnits = found.filter((unit) => unit.total > 0).length;
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
      [optionA.id]: winsA / MODEL.draws,
      [optionB.id]: 1 - winsA / MODEL.draws,
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
