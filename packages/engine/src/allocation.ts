import type { Rules, Threshold } from "./rules.ts";

type Entry = { id: string; votes: number; won?: number; inert?: boolean };
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
  {
    formula:
      | "hare"
      | "droop"
      | "hagenbach_bischoff_quota"
      | "imperiali_quota"
      | "reinforced_imperiali_quota";
  }
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
  const start =
    rules.formula === "reinforced_imperiali_quota"
      ? 3
      : rules.formula === "imperiali_quota"
        ? 2
        : rules.formula === "hagenbach_bischoff_quota"
          ? 1
          : 0;
  for (let extra = start; ; extra--) {
    const [numerator, denominator] =
      rules.formula === "droop"
        ? [Math.floor(total / (seats + 1)) + 1, 1]
        : [total, seats + extra];
    const automatic: Record<string, number> = {};
    const claims: Array<Claim> = [];
    let awarded = 0;
    for (const { id, votes } of entries) {
      const won = Math.floor((votes * denominator) / numerator);
      automatic[id] = won;
      awarded += won;
      claims.push({ id, score: votes * denominator - won * numerator, votes });
    }
    if (awarded <= seats || extra <= 0)
      return { automatic, claims, quota: numerator / denominator };
  }
}

function byDivisor(
  rules: Exclude<Allocating, Quota>,
  seats: number,
  entries: Array<Entry>,
) {
  const [slope, offset] = divisors[rules.formula];
  const isCompensatory =
    "compensation" in rules && rules.compensation === "constituency_seats";
  const automatic: Record<string, number> = {};
  const claims: Array<Claim> = [];
  for (const { id, votes, won = 0 } of entries) {
    automatic[id] = 0;
    const start = isCompensatory ? won : 0;
    for (let k = start; k < start + (slope === 0 ? 1 : seats); k++) {
      const divisor =
        rules.formula === "modified_sainte_lague" && k === 0
          ? rules.firstDivisor
          : slope * k + offset;
      claims.push({ id, score: votes / divisor, votes });
    }
  }
  return { automatic, claims };
}

function byPool(
  rules: Allocating,
  seats: number,
  entries: Array<Entry & { group?: string }>,
): Allocation {
  const pools = new Map<string, Array<Entry>>();
  for (const { id, group = id, votes, won } of entries)
    pools.set(group, [...(pools.get(group) ?? []), { id, votes, won }]);
  const pooled: Array<Entry> = [];
  for (const [id, members] of pools) {
    let votes = 0;
    let won = 0;
    for (const member of members) {
      votes += member.votes;
      won += member.won ?? 0;
    }
    pooled.push({ id, votes, won });
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
  let start: Pick<ReturnType<typeof byQuota>, "automatic" | "claims">;
  switch (rules.formula) {
    case "hare":
    case "droop":
    case "hagenbach_bischoff_quota":
    case "imperiali_quota":
    case "reinforced_imperiali_quota": {
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

export type Distribution = Allocation & {
  districts: Record<string, Record<string, number>>;
};

export function distribute(
  rules: Quota,
  districts: Array<{ id: string; seats: number; entries: Array<Entry> }>,
): Distribution {
  const access = "remainderAccess" in rules ? rules.remainderAccess : undefined;
  const isRelative =
    "remainderPlacement" in rules &&
    rules.remainderPlacement === "remainder_over_quota";
  const placed: Record<string, Record<string, number>> = {};
  const open = new Map<string, number>();
  const totals = new Map<
    string,
    { votes: number; quotas: number; residual: number }
  >();
  const outside = new Map<string, number>();
  const residuals: Array<{ district: string; id: string; residual: number }> =
    [];
  let remaining = 0;
  let tie: Allocation["tie"];
  for (const { id: district, seats, entries } of districts) {
    const special = rules.districtFormulas?.find(
      (entry) => entry.seats === seats,
    );
    const formula = special?.formula ?? rules.formula;
    let automatic: Record<string, number>;
    let quota = 0;
    if (formula === "fptp") {
      const plurality = allocate(
        { formula, tieBreak: rules.tieBreak },
        seats,
        entries,
      );
      automatic = plurality.seats;
      tie ??= plurality.tie;
    } else {
      ({ automatic, quota } = byQuota({ ...rules, formula }, seats, entries));
    }
    const placement: Record<string, number> = {};
    placed[district] = placement;
    if (special?.pooled === false) {
      for (const { id, inert } of entries) {
        const won = inert ? 0 : (automatic[id] ?? 0);
        placement[id] = won;
        outside.set(id, (outside.get(id) ?? 0) + won);
      }
      continue;
    }
    let left = seats;
    for (const { id, votes, inert } of entries) {
      const won = inert ? 0 : (automatic[id] ?? 0);
      placement[id] = won;
      left -= won;
      const residual =
        inert || (formula === "fptp" && won > 0) ? 0 : votes - won * quota;
      if (!inert)
        residuals.push({
          district,
          id,
          residual: isRelative && quota > 0 ? residual / quota : residual,
        });
      const total = totals.get(id) ?? { votes: 0, quotas: 0, residual: 0 };
      total.votes += votes;
      total.quotas += won;
      total.residual += residual;
      totals.set(id, total);
    }
    if (left < 0)
      throw new Error(
        `The ${formula} quota awards more than ${seats} seats in ${district}`,
      );
    open.set(district, left);
    remaining += left;
  }
  const pool: Array<Entry> = [];
  for (const [id, { votes, quotas, residual }] of totals)
    if (
      (access?.quotas == null || quotas >= access.quotas) &&
      (access?.votes == null || votes >= access.votes) &&
      residual > 0
    )
      pool.push({ id, votes: residual });
  const national =
    remaining > 0 && pool.length > 0
      ? allocate({ formula: "hare", tieBreak: rules.tieBreak }, remaining, pool)
      : { seats: {} };
  const seats: Record<string, number> = {};
  for (const [id, { quotas }] of totals)
    seats[id] = quotas + (national.seats[id] ?? 0);
  for (const [id, won] of outside) seats[id] = (seats[id] ?? 0) + won;
  const owed = new Map(Object.entries(national.seats));
  const order = residuals.toSorted((a, b) => b.residual - a.residual);
  for (let isMoving = true; isMoving;) {
    isMoving = false;
    for (const { district, id } of order) {
      const left = open.get(district) ?? 0;
      const due = owed.get(id) ?? 0;
      const won = placed[district];
      if (left === 0 || due === 0 || won == null) continue;
      won[id] = (won[id] ?? 0) + 1;
      open.set(district, left - 1);
      owed.set(id, due - 1);
      isMoving = true;
    }
  }
  return { seats, tie: tie ?? national.tie, districts: placed };
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

function rounded(value: number, rounding: "up" | "down" | "nearest") {
  const epsilon = 1e-9;
  if (rounding === "up") return Math.ceil(value - epsilon);
  if (rounding === "down") return Math.floor(value + epsilon);
  return Math.round(value);
}

type Proportional = Exclude<
  Allocating,
  {
    formula:
      | "fptp"
      | "winner_take_all"
      | "block"
      | "sntv"
      | "limited"
      | "cumulative"
      | "approval";
  }
>;

export type Apportionment = Allocation & {
  qualified: Array<string>;
  leader?: string | undefined;
  bonus: number;
};

export function apportion(
  rules: Proportional,
  seats: number,
  entries: Array<Entry & { group?: string }>,
  { valid, round = 1 }: { valid?: number; round?: number } = {},
): Apportionment {
  let total = 0;
  for (const { votes } of entries) total += votes;
  const base = valid ?? total;
  const reaches = (votes: number, percent: number) =>
    votes * 100 >= percent * base;
  const thresholds =
    rules.threshold == null
      ? []
      : Array.isArray(rules.threshold)
        ? rules.threshold
        : [rules.threshold];
  const isExempt = (threshold: Threshold, id: string, group?: string) =>
    threshold.exempt?.includes(id) === true ||
    (group != null && threshold.exempt?.includes(group) === true);
  const coalitions = new Map<string, Array<Entry>>();
  for (const { id, votes, won, group } of entries)
    if (group != null)
      coalitions.set(group, [
        ...(coalitions.get(group) ?? []),
        { id, votes, won },
      ]);
  const pooled = new Map<string, number>();
  for (const [group, members] of coalitions) {
    let votes = 0;
    for (const member of members) votes += member.votes;
    pooled.set(group, votes);
  }
  const standing = new Set<string>();
  for (const [group, members] of coalitions) {
    const isStanding = thresholds
      .filter(({ level }) => level === "coalition" || level === "alliance")
      .every(
        (threshold) =>
          isExempt(threshold, group) ||
          (reaches(pooled.get(group) ?? 0, threshold.percent) &&
            (threshold.requires == null ||
              members.some(({ votes }) =>
                reaches(votes, threshold.requires?.percent ?? 0),
              ))),
      );
    if (isStanding) standing.add(group);
  }
  const qualified = new Set<string>();
  const listThresholds = thresholds.filter(
    ({ level }) => level === "list" || level === "party",
  );
  for (const { id, votes, group } of entries) {
    const inCoalition = group != null && standing.has(group);
    const passes = listThresholds
      .filter(
        ({ members }) =>
          members === "any" || (members === "coalition") === inCoalition,
      )
      .every(
        (threshold) =>
          isExempt(threshold, id, group) || reaches(votes, threshold.percent),
      );
    if (passes) qualified.add(id);
  }
  for (const threshold of listThresholds) {
    if (!threshold.bestLoser) continue;
    for (const group of standing) {
      const losers = (coalitions.get(group) ?? [])
        .filter(({ id }) => !qualified.has(id))
        .toSorted((a, b) => b.votes - a.votes);
      const best = losers[0];
      if (best != null && best.votes !== losers[1]?.votes)
        qualified.add(best.id);
    }
  }
  const contenders: Array<Entry> = [];
  const members = new Map<string, Array<Entry>>();
  for (const [group, list] of coalitions) {
    if (!standing.has(group)) continue;
    const admitted = list.filter(({ id }) => qualified.has(id));
    if (admitted.length === 0) continue;
    members.set(group, admitted);
    let won = 0;
    for (const member of admitted) won += member.won ?? 0;
    contenders.push({ id: group, votes: pooled.get(group) ?? 0, won });
  }
  for (const { id, votes, won, group } of entries)
    if (qualified.has(id) && (group == null || !standing.has(group)))
      contenders.push({ id, votes, won });
  const plain = {
    ...rules,
    pooling: undefined,
    threshold: undefined,
    bonus: undefined,
  };
  let allocation = allocate(plain, seats, contenders);
  let leader: string | undefined;
  let extra = 0;
  const { bonus } = rules;
  const ranked = contenders.toSorted((a, b) => b.votes - a.votes);
  const top = ranked[0];
  const isTriggered =
    bonus != null &&
    top != null &&
    top.votes !== ranked[1]?.votes &&
    (bonus.share == null ||
      (bonus.share.rounds != null && !bonus.share.rounds.includes(round)) ||
      (bonus.share.strict
        ? top.votes * 100 > bonus.share.percent * base
        : reaches(top.votes, bonus.share.percent)));
  if (isTriggered && top != null) {
    leader = top.id;
    const { size } = bonus;
    const others = contenders.filter(({ id }) => id !== top.id);
    if (size.kind === "target") {
      if (size.seats == null && size.fraction == null)
        throw new Error("A target bonus needs seats or a fraction");
      const target =
        size.seats ??
        rounded(seats * (size.fraction ?? 0), size.rounding ?? "up");
      const won = allocation.seats[top.id] ?? 0;
      if (won < target) {
        extra = target - won;
        const rest = allocate(plain, seats - target, others);
        allocation = {
          seats: { ...rest.seats, [top.id]: target },
          tie: rest.tie,
        };
      }
    } else {
      extra =
        size.kind === "seats"
          ? size.seats
          : rounded(
              seats * size.fraction,
              size.below != null && seats < size.below.seats
                ? size.below.rounding
                : size.rounding,
            );
      const rest = allocate(
        plain,
        seats - extra,
        bonus.remainder === "others" ? others : contenders,
      );
      allocation = {
        seats: { ...rest.seats, [top.id]: (rest.seats[top.id] ?? 0) + extra },
        tie: rest.tie,
      };
    }
  }
  const result: Record<string, number> = {};
  let tie = allocation.tie;
  for (const { id } of entries) result[id] = 0;
  for (const [id, won] of Object.entries(allocation.seats)) {
    const list = members.get(id);
    if (list == null) {
      result[id] = won;
      continue;
    }
    const inner = allocate(plain, won, list);
    Object.assign(result, inner.seats);
    tie ??= inner.tie;
  }
  return {
    seats: result,
    tie,
    qualified: [...qualified],
    leader,
    bonus: extra,
  };
}
