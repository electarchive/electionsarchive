import type { Rules, Threshold } from "./rules.ts";

type Entry = { id: string; votes: number };
type Claim = { id: string; score: number; votes: number };
type Allocating = Exclude<
  Rules,
  {
    formula:
      | "referendum"
      | "two_round"
      | "supplementary"
      | "top_two"
      | "irv"
      | "stv"
      | "repeated_ballot"
      | "caucus";
  }
>;
export type Allocation = {
  seats: Record<string, number>;
  tie?: { ids: Array<string>; seats: number } | undefined;
};

const divisors = {
  fptp: [0, 1],
  winner_take_all: [0, 1],
  block: [0, 1],
  sntv: [0, 1],
  limited: [0, 1],
  cumulative: [0, 1],
  approval: [0, 1],
  dhondt: [1, 1],
  hagenbach_bischoff: [1, 1],
  imperiali: [1, 2],
  sainte_lague: [2, 1],
  sainte_lague_schepers: [2, 1],
  modified_sainte_lague: [2, 1],
} as const;

type Quota = Extract<
  Allocating,
  { formula: "hare" | "droop" | "imperiali_quota" }
>;

function award(
  claims: Array<Claim>,
  seats: number,
  tieBreak: Allocating["tieBreak"],
) {
  const isByVotes = tieBreak === "votes";
  const sorted = claims.toSorted(
    (a, b) => b.score - a.score || (isByVotes ? b.votes - a.votes : 0),
  );
  const last = sorted[seats - 1];
  const isTied = (claim: Claim | undefined) =>
    last != null &&
    claim?.score === last.score &&
    (!isByVotes || claim.votes === last.votes);
  const firm = isTied(sorted[seats])
    ? sorted.findIndex((claim) => isTied(claim))
    : Math.min(seats, sorted.length);
  const tie =
    firm < seats && firm < sorted.length
      ? {
          ids: [
            ...new Set(
              sorted.filter((claim) => isTied(claim)).map(({ id }) => id),
            ),
          ],
          seats: seats - firm,
        }
      : undefined;
  return { winners: sorted.slice(0, firm), tie };
}

function byQuota(rules: Quota, seats: number, entries: Array<Entry>) {
  let total = 0;
  for (const { votes } of entries) total += votes;
  const [numerator, denominator] = {
    hare: [total, seats] as const,
    droop: [Math.floor(total / (seats + 1)) + 1, 1] as const,
    imperiali_quota: [total, seats + 2] as const,
  }[rules.formula];
  const automatic: Record<string, number> = {};
  const claims: Array<Claim> = [];
  for (const { id, votes } of entries) {
    const won = Math.floor((votes * denominator) / numerator);
    automatic[id] = won;
    claims.push({ id, score: votes * denominator - won * numerator, votes });
  }
  return { automatic, claims };
}

function byDivisor(
  rules: Exclude<Allocating, Quota>,
  seats: number,
  entries: Array<Entry>,
) {
  const [slope, offset] = divisors[rules.formula];
  const sequence = Array.from(
    { length: slope === 0 ? 1 : seats },
    (_, won) => slope * won + offset,
  );
  if (rules.formula === "modified_sainte_lague")
    sequence[0] = rules.firstDivisor;
  const automatic: Record<string, number> = {};
  const claims: Array<Claim> = [];
  for (const { id, votes } of entries) {
    automatic[id] = 0;
    for (const divisor of sequence)
      claims.push({ id, score: votes / divisor, votes });
  }
  return { automatic, claims };
}

function byPool(
  rules: Allocating,
  seats: number,
  entries: Array<Entry & { group?: string }>,
): Allocation {
  const pools = new Map<string, Array<Entry>>();
  for (const { id, group = id, votes } of entries)
    pools.set(group, [...(pools.get(group) ?? []), { id, votes }]);
  const pooled: Array<Entry> = [];
  for (const [id, members] of pools) {
    let votes = 0;
    for (const member of members) votes += member.votes;
    pooled.push({ id, votes });
  }
  const unpooled = { ...rules, pooling: undefined };
  const first = allocate(unpooled, seats, pooled);
  const allocation: Allocation = { seats: {}, tie: first.tie };
  for (const [id, members] of pools) {
    const inner = allocate(unpooled, first.seats[id] ?? 0, members);
    Object.assign(allocation.seats, inner.seats);
    allocation.tie ??= inner.tie;
  }
  return allocation;
}

export function allocate(
  rules: Allocating,
  seats: number,
  entries: Array<Entry & { group?: string }>,
): Allocation {
  if ("pooling" in rules && rules.pooling != null)
    return byPool(rules, seats, entries);
  let start: ReturnType<typeof byQuota>;
  switch (rules.formula) {
    case "hare":
    case "droop":
    case "imperiali_quota": {
      start = byQuota(rules, seats, entries);
      break;
    }
    default: {
      start = byDivisor(rules, seats, entries);
    }
  }
  let remaining = seats;
  for (const won of Object.values(start.automatic)) remaining -= won;
  if (remaining < 0)
    throw new Error(
      `The ${rules.formula} quota awards more than ${seats} seats`,
    );
  const isSlate = rules.formula === "winner_take_all";
  const { winners, tie } = award(
    start.claims,
    isSlate ? Math.min(remaining, 1) : remaining,
    rules.tieBreak,
  );
  for (const { id } of winners)
    start.automatic[id] = (start.automatic[id] ?? 0) + (isSlate ? seats : 1);
  return {
    seats: start.automatic,
    tie: isSlate && tie != null ? { ids: tie.ids, seats } : tie,
  };
}

export function qualify(
  threshold: Threshold,
  entries: Array<Entry & { group?: string; constituencies?: number }>,
  valid?: number,
) {
  let total = 0;
  const groups = new Map<
    string,
    { votes: number; constituencies: number; exempt: boolean }
  >();
  for (const { id, group = id, votes, constituencies = 0 } of entries) {
    const tally = groups.get(group) ?? {
      votes: 0,
      constituencies: 0,
      exempt: false,
    };
    tally.votes += votes;
    tally.constituencies += constituencies;
    tally.exempt ||=
      threshold.exempt?.includes(id) === true ||
      threshold.exempt?.includes(group) === true;
    groups.set(group, tally);
    total += votes;
  }
  const base = valid ?? total;
  const passed = new Set<string>();
  for (const [group, { votes, constituencies, exempt }] of groups) {
    if (
      exempt ||
      votes * 100 >= threshold.percent * base ||
      (threshold.constituencies != null &&
        constituencies >= threshold.constituencies)
    ) {
      passed.add(group);
    }
  }
  return passed;
}
