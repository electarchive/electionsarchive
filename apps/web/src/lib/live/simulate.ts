import type {
  LiveElection,
  LiveFrame,
  LiveRace,
  LiveUnitCount,
} from "@ea/analytics/live";
import { hash, stream } from "#lib/live/random.js";
import { pair, sum } from "#lib/live/standing.js";

export type Order =
  | "realistic"
  | "urban-first"
  | "rural-first"
  | "mail-last"
  | "shuffled";

export type Scenario = {
  seed: number;
  swing: number;
  turnout: number;
  earlyLean: number;
  earlyShare: number;
  order: Order;
  pace: number;
};

export type EdgeKind =
  | "correction"
  | "stall"
  | "withdrawn"
  | "overcount"
  | "empty"
  | "late"
  | "certified";

export type Edge = { kind: EdgeKind; at: number };

export type Seed = { race: LiveRace; close: number; side: 1 | -1 };

type Event = {
  at: number;
  unit: number;
  votes: Array<number>;
  precincts: number;
  kind?: "withdrawn" | "late";
};

export type Plan = {
  events: Array<Event>;
  truth: Array<Array<number>>;
  precincts: Array<number>;
  start: number;
  close: number;
  end: number;
  certifiedAt: number;
  finalMargin: number;
  pair: [number, number];
};

export const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const STAMP = 2 * MINUTE;
const SIZES: Record<LiveRace["office"], number> = {
  senate: 2_400_000,
  governor: 2_400_000,
  house: 290_000,
  other: 24_000,
};

const MAJOR = /democrat|republican/iu;

export const DEFAULT_SCENARIO: Scenario = {
  seed: 2026,
  swing: 0,
  turnout: 0.82,
  earlyLean: 16,
  earlyShare: 0.52,
  order: "realistic",
  pace: 1,
};

function leans(race: LiveRace) {
  const [a] = pair(race);
  return /democrat/iu.test(race.options[a]?.party?.name ?? "") ? 1 : -1;
}

export function start(seed: Seed): Scenario {
  return { ...DEFAULT_SCENARIO, earlyLean: 16 * leans(seed.race) };
}

export function seeds(election: LiveElection, lead: string | null) {
  const fallback = Date.parse(`${election.date}T23:00:00Z`) + 2 * HOUR;
  const closes = new Map(
    election.pollClose.map((entry) => [entry.state, Date.parse(entry.at)]),
  );
  const races = election.races.map((race) => ({
    ...race,
    frames: [],
    counts: [],
    certified: null,
  }));
  const anchor = races.find((race) => race.id === lead) ?? races[0];
  const side = anchor == null ? 1 : leans(anchor);
  return races.map((race): Seed => ({
    race,
    close: closes.get(race.state) ?? fallback,
    side: leans(race) === side ? 1 : -1,
  }));
}

function split(total: number, parts: number, random: () => number) {
  const weights = Array.from({ length: parts }, () => 0.35 + random());
  const whole = sum(weights);
  return weights.map((weight) => (weight / whole) * total);
}

function normalize(shares: Array<number>) {
  const whole = sum(shares);
  return shares.map((share) => (whole > 0 ? share / whole : 0));
}

function tilt(shares: Array<number>, [a, b]: [number, number], by: number) {
  const next = [...shares];
  next[a] = Math.max(0.005, (next[a] ?? 0) + by / 2);
  next[b] = Math.max(0.005, (next[b] ?? 0) - by / 2);
  return normalize(next);
}

function cells(race: LiveRace, random: ReturnType<typeof stream>) {
  const known = new Map(race.baseline.map((row) => [row.unitId, row]));
  const turnouts = race.baseline
    .map((row) => row.turnout)
    .toSorted((x, y) => x - y);
  const typical = turnouts[Math.floor(turnouts.length / 2)];
  const length = Math.max(1, race.units.length);
  const parts =
    typical == null
      ? split(SIZES[race.office] * (0.5 + random.next()), length, random.next)
      : [];
  const shares = normalize(
    race.options.map(
      (option) =>
        (MAJOR.test(option.party?.name ?? "") ? 6 : 0.5) *
        (0.7 + random.next() * 0.6),
    ),
  );
  return Array.from({ length }, (_, index) => {
    const base = known.get(race.units[index]?.id ?? "");
    return base == null
      ? { turnout: typical ?? parts[index] ?? 0, shares }
      : { turnout: base.turnout, shares: normalize(base.shares) };
  });
}

export function plan(seed: Seed, scenario: Scenario): Plan {
  const { race, close, side } = seed;
  const random = stream(hash(`${scenario.seed}:${race.id}`));
  const two = pair(race);
  const all = cells(race, random);
  const sorted = all.map((cell) => cell.turnout).toSorted((x, y) => y - x);
  const large = sorted[3] ?? 0;
  const medium = sorted[Math.min(sorted.length - 1, 20)] ?? 0;
  const latest =
    all.length > 3 ? all.findIndex((cell) => cell.turnout === sorted[0]) : -1;
  const events: Array<Event> = [];
  const truth: Array<Array<number>> = [];
  const precincts: Array<number> = [];
  const regional = random.normal() * 0.012;
  const ranks = all.map(() => random.next());
  for (const [index, { turnout: turnoutBase, shares }] of all.entries()) {
    const isLarge = turnoutBase >= large;
    const isMedium = !isLarge && turnoutBase >= medium;
    const isLate = index === latest;
    const swing =
      (side * scenario.swing) / 100 +
      random.normal() * 0.022 +
      (isLarge ? regional : -regional);
    const truthShares = tilt(shares, two, swing * 2);
    const votes = Math.round(
      turnoutBase *
        scenario.turnout *
        Math.max(0.7, 1 + random.normal() * 0.04),
    );
    truth.push(truthShares.map((share) => share * votes));
    const expected = Math.max(4, Math.round(turnoutBase / 1100));
    precincts.push(expected);
    const early = Math.min(
      0.8,
      Math.max(0.25, scenario.earlyShare * (1 + random.normal() * 0.1)),
    );
    const lean = (side * scenario.earlyLean) / 100;
    const earlyShares = tilt(truthShares, two, 2 * lean * (1 - early));
    const dayShares = tilt(truthShares, two, -2 * lean * early);
    const tail = 0.006 + random.next() * 0.006;
    const earlyVotes = votes * early;
    const dayVotes = votes * (1 - early) * (1 - tail);
    const opens = close;
    const speed = scenario.pace;
    const order = scenario.order;
    const sizeRank = isLarge ? 2 : isMedium ? 1 : 0;
    const factor =
      order === "urban-first"
        ? [1.5, 1, 0.55][sizeRank]
        : order === "rural-first"
          ? [0.6, 1, 1.4][sizeRank]
          : order === "shuffled"
            ? 0.5 + (ranks[index] ?? 0.5)
            : 1;
    const span =
      (isLate ? 520 : isLarge ? 380 : isMedium ? 290 : 190) * (factor ?? 1);
    const earlyCount = Math.max(
      1,
      Math.round((isLarge ? 3 : isMedium ? 2 : 1) / speed),
    );
    const dayCount = Math.max(
      1,
      Math.round((isLarge ? 7 : isMedium ? 4 : 2.5) / speed),
    );
    const earlyStart =
      (order === "mail-last" ? span * 0.7 : 12 + random.next() * 40) *
      (factor ?? 1);
    const dayStart =
      order === "mail-last" ? 20 + random.next() * 30 : 45 + random.next() * 45;
    const earlyParts = split(earlyVotes, earlyCount, random.next);
    const dayParts = split(dayVotes, dayCount, random.next);
    const dayPrecincts = split(expected, dayCount, random.next);
    let given = 0;
    for (const [part, amount] of earlyParts.entries()) {
      const late = isLate && part === earlyParts.length - 1 ? 1.25 : 1;
      const minute =
        earlyStart +
        (part / Math.max(1, earlyParts.length)) * span * 0.6 * late +
        random.next() * 15;
      events.push({
        at: opens + minute * MINUTE,
        unit: index,
        votes: earlyShares.map((share) =>
          Math.round(amount * Math.max(0, share + random.normal() * 0.004)),
        ),
        precincts: 0,
      });
    }
    for (const [part, amount] of dayParts.entries()) {
      const minute =
        dayStart * (factor ?? 1) +
        ((part + 1) / dayParts.length) * (span - dayStart) +
        random.normal() * 12;
      const counted =
        part === dayParts.length - 1
          ? expected - given
          : Math.round(dayPrecincts[part] ?? 0);
      given += counted;
      events.push({
        at: opens + Math.max(dayStart, minute) * MINUTE,
        unit: index,
        votes: dayShares.map((share) =>
          Math.round(amount * Math.max(0, share + random.normal() * 0.006)),
        ),
        precincts: counted,
      });
    }
  }
  for (const event of events) {
    event.at = Math.ceil(event.at / STAMP) * STAMP;
  }
  events.sort((x, y) => x.at - y.at || x.unit - y.unit);
  const totals = race.options.map((_, option) =>
    sum(truth.map((row) => row[option] ?? 0)),
  );
  const whole = sum(totals);
  const end = (events.at(-1)?.at ?? close) + 30 * MINUTE;
  return {
    events,
    truth,
    precincts,
    start: close - 2 * HOUR,
    close,
    end,
    certifiedAt: end + 60 * MINUTE,
    pair: two,
    finalMargin:
      whole > 0 ? ((totals[two[0]] ?? 0) - (totals[two[1]] ?? 0)) / whole : 0,
  };
}

export function solveSwing(seed: Seed, scenario: Scenario, target: number) {
  let swing = scenario.swing;
  for (let step = 0; step < 6; step += 1) {
    const reached = plan(seed, { ...scenario, swing }).finalMargin;
    swing += ((target - reached) * 100) / 2;
  }
  return Math.round(swing * 1000) / 1000;
}

function blank(planned: Plan) {
  return (planned.truth[0] ?? []).map(() => 0);
}

function largest(planned: Plan, before: number) {
  const seen = new Map<number, number>();
  for (const event of planned.events) {
    if (event.at > before) {
      break;
    }
    seen.set(event.unit, (seen.get(event.unit) ?? 0) + sum(event.votes));
  }
  return [...seen].toSorted((x, y) => y[1] - x[1])[0]?.[0] ?? 0;
}

function waiting(planned: Plan, before: number) {
  const started = new Set(
    planned.events
      .filter((event) => event.at <= before)
      .map((event) => event.unit),
  );
  const left = planned.truth
    .map((row, unit) => ({ unit, size: sum(row) }))
    .filter((entry) => !started.has(entry.unit))
    .toSorted((x, y) => x.size - y.size);
  return left[0]?.unit ?? null;
}

function apply(planned: Plan, edges: Array<Edge>, now: number) {
  const events = planned.events.map((event) => ({ ...event }));
  for (const edge of edges) {
    const at = Math.ceil(edge.at / STAMP) * STAMP;
    switch (edge.kind) {
      case "stall": {
        const until = at + 46 * MINUTE;
        for (const event of events) {
          if (event.at > at && event.at <= until) {
            event.at = until;
          }
        }
        break;
      }
      case "correction": {
        const unit = largest(planned, edge.at);
        const cut = blank(planned);
        for (const event of events) {
          if (event.unit === unit && event.at <= edge.at) {
            for (const [option, value] of event.votes.entries()) {
              cut[option] = (cut[option] ?? 0) + value;
            }
          }
        }
        if (sum(cut) === 0) {
          break;
        }
        const removed = cut.map((value) => -Math.round(value * 0.08));
        events.push({ at: at + STAMP, unit, votes: removed, precincts: 0 });
        const next = events.find(
          (event) => event.unit === unit && event.at > at + STAMP,
        );
        if (next == null) {
          events.push({
            at: at + 20 * MINUTE,
            unit,
            votes: removed.map((value) => -value),
            precincts: 0,
          });
        } else {
          next.votes = next.votes.map(
            (value, index) => value - (removed[index] ?? 0),
          );
        }
        break;
      }
      case "withdrawn": {
        if (now >= at + 6 * MINUTE) {
          break;
        }
        const unit = largest(planned, edge.at);
        events.push({
          at: at + MINUTE + 17_000,
          unit,
          votes: (planned.truth[unit] ?? []).map((value, index) =>
            Math.round(value * (index === planned.pair[1] ? 0.4 : 0.03)),
          ),
          precincts: 0,
          kind: "withdrawn",
        });
        break;
      }
      case "overcount": {
        const unit = largest(planned, edge.at);
        const row = planned.truth[unit] ?? [];
        const total = planned.precincts[unit] ?? 0;
        let counted = 0;
        for (const event of events) {
          if (event.unit === unit && event.at <= at + STAMP) {
            counted += event.precincts;
          }
        }
        events.push({
          at: at + STAMP,
          unit,
          votes: row.map((value) => Math.round(value * 0.16)),
          precincts: Math.max(0, total - counted) + Math.ceil(total * 0.04) + 1,
        });
        break;
      }
      case "empty": {
        const unit = waiting(planned, edge.at);
        if (unit == null) {
          break;
        }
        events.push({
          at: at + STAMP,
          unit,
          votes: (planned.truth[unit] ?? []).map(() => 0),
          precincts: Math.max(
            1,
            Math.round((planned.precincts[unit] ?? 0) * 0.4),
          ),
        });
        break;
      }
      case "late": {
        if (now < at + STAMP) {
          break;
        }
        const unit = waiting(planned, edge.at) ?? largest(planned, edge.at);
        const row = planned.truth[unit] ?? [];
        events.push({
          at: at - 17 * MINUTE - 23_000,
          unit,
          votes: row.map((value) => Math.round(value * 0.12)),
          precincts: Math.round((planned.precincts[unit] ?? 0) * 0.1),
          kind: "late",
        });
        break;
      }
      case "certified": {
        break;
      }
    }
  }
  return events.toSorted((x, y) => x.at - y.at || x.unit - y.unit);
}

const iso = (at: number) => new Date(at).toISOString().replace(/\.\d+Z$/u, "Z");

export type Run = { seed: Seed; planned: Plan };

function tally(
  { seed: { race }, planned }: Run,
  events: Array<Event>,
  isCertified: boolean,
): LiveRace {
  const cumulative = planned.truth.map(() => race.options.map(() => 0));
  const reported = planned.truth.map(() => 0);
  const frames: Array<LiveFrame> = [];
  const counts: Array<LiveUnitCount> = [];
  const expectedAll = sum(planned.precincts);
  let index = 0;
  while (index < events.length) {
    const at = events[index]?.at ?? 0;
    const touched = new Set<number>();
    while (index < events.length && events[index]?.at === at) {
      const event = events[index];
      index += 1;
      if (event == null) {
        continue;
      }
      const row = cumulative[event.unit];
      if (row == null) {
        continue;
      }
      for (const [option, value] of event.votes.entries()) {
        row[option] = Math.max(0, (row[option] ?? 0) + value);
      }
      reported[event.unit] = (reported[event.unit] ?? 0) + event.precincts;
      touched.add(event.unit);
    }
    const stamp = iso(at);
    for (const unit of touched) {
      const id = race.units[unit]?.id;
      if (id == null) {
        continue;
      }
      counts.push({
        unitId: id,
        at: stamp,
        votes: [...(cumulative[unit] ?? [])],
        reporting: reported[unit] ?? 0,
        expected: planned.precincts[unit] ?? null,
      });
    }
    frames.push({
      at: stamp,
      totals: race.options.map((_, option) =>
        sum(cumulative.map((row) => row[option] ?? 0)),
      ),
      reporting: sum(reported),
      expected: expectedAll,
    });
  }
  const finalTotals = race.options.map((_, option) =>
    Math.round(sum(planned.truth.map((row) => row[option] ?? 0))),
  );
  const order = finalTotals
    .map((value, option) => ({ value, option }))
    .toSorted((x, y) => y.value - x.value);
  return {
    ...race,
    frames,
    counts,
    certified: isCertified
      ? {
          totals: finalTotals,
          winners: [race.options[order[0]?.option ?? 0]?.id ?? ""],
        }
      : null,
  };
}

function happened(
  run: Run,
  lead: string | null,
  edges: Array<Edge>,
  now: number,
) {
  return run.seed.race.id === lead
    ? apply(run.planned, edges, now)
    : run.planned.events;
}

export function replay(
  election: LiveElection,
  runs: Array<Run>,
  lead: string | null,
  edges: Array<Edge>,
  now: number,
): LiveElection {
  const certifiedEdge = edges.find((edge) => edge.kind === "certified");
  const isCertified = certifiedEdge != null && now >= certifiedEdge.at;
  const races = runs.map((run) =>
    tally(
      run,
      happened(run, lead, edges, now).filter(
        (event) => event.kind === "late" || event.at <= now,
      ),
      isCertified,
    ),
  );
  const newest = races
    .flatMap((race) => race.frames.at(-1)?.at ?? [])
    .toSorted((x, y) => x.localeCompare(y))
    .at(-1);
  return {
    ...election,
    newest: newest ?? null,
    isCertified: isCertified && races.length > 0,
    races,
  };
}

export function stamps(
  runs: Array<Run>,
  lead: string | null,
  edges: Array<Edge>,
  now: number,
) {
  return [
    ...new Set(
      runs.flatMap((run) =>
        happened(run, lead, edges, now).map((event) => event.at),
      ),
    ),
  ].toSorted((x, y) => x - y);
}
