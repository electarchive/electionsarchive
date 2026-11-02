export const CAVEATS = [
  "pooled_independents",
  "uncontested_excluded",
  "first_round_votes",
  "list_votes",
  "sisters_joined",
  "alliance_seats_credited",
  "alliances_as_units",
  "lineage_joined",
  "split_new",
  "unmatched_major",
  "few_units",
] as const;

export type Caveat = (typeof CAVEATS)[number];

export type Entry = {
  key: string;
  label: string;
  votes: number;
  seats: number;
  isPooled: boolean;
};

export type Measured<Inputs> = {
  value: number;
  inputs: Inputs;
  caveats: Array<Caveat>;
};

type Totals = { votes: number; seats: number; parties: number };

const totals = (entries: Array<Entry>): Totals => {
  let [votes, seats] = [0, 0];
  for (const entry of entries) {
    votes += entry.votes;
    seats += entry.seats;
  }
  return {
    votes,
    seats,
    parties: entries.filter((entry) => entry.votes > 0 || entry.seats > 0)
      .length,
  };
};

const pooledCaveat = (entries: Array<Entry>, on: "votes" | "seats") =>
  entries.some((entry) => entry.isPooled && entry[on] > 0)
    ? (["pooled_independents"] as Array<Caveat>)
    : [];

function concentration(entries: Array<Entry>, on: "votes" | "seats") {
  const total = on === "votes" ? totals(entries).votes : totals(entries).seats;
  if (total <= 0) {
    return null;
  }
  let sum = 0;
  for (const entry of entries) {
    if (entry.isPooled) {
      sum += on === "seats" ? entry.seats / total ** 2 : 0;
      continue;
    }
    sum += (entry[on] / total) ** 2;
  }
  return { sum, total };
}

export function effectiveParties(
  entries: Array<Entry>,
  on: "votes" | "seats",
): Measured<{ on: "votes" | "seats"; total: number; parties: number }> | null {
  const found = concentration(entries, on);
  if (found == null || found.sum <= 0) {
    return null;
  }
  return {
    value: 1 / found.sum,
    inputs: { on, total: found.total, parties: totals(entries).parties },
    caveats: pooledCaveat(entries, on),
  };
}

export function fragmentation(
  entries: Array<Entry>,
  on: "votes" | "seats",
): Measured<{ on: "votes" | "seats"; total: number; parties: number }> | null {
  const found = concentration(entries, on);
  if (found == null) {
    return null;
  }
  return {
    value: 1 - found.sum,
    inputs: { on, total: found.total, parties: totals(entries).parties },
    caveats: pooledCaveat(entries, on),
  };
}

function gaps(entries: Array<Entry>) {
  const { votes, seats } = totals(entries);
  if (votes <= 0 || seats <= 0) {
    return null;
  }
  return entries.map((entry) => ({
    key: entry.key,
    label: entry.label,
    votes: entry.votes / votes,
    seats: entry.seats / seats,
  }));
}

export function gallagher(entries: Array<Entry>): Measured<Totals> | null {
  const found = gaps(entries);
  if (found == null) {
    return null;
  }
  let sum = 0;
  for (const gap of found) {
    sum += (gap.votes - gap.seats) ** 2;
  }
  return {
    value: Math.sqrt(sum / 2) * 100,
    inputs: totals(entries),
    caveats: pooledCaveat(entries, "votes"),
  };
}

export function loosemoreHanby(entries: Array<Entry>): Measured<Totals> | null {
  const found = gaps(entries);
  if (found == null) {
    return null;
  }
  let sum = 0;
  for (const gap of found) {
    sum += Math.abs(gap.votes - gap.seats);
  }
  return {
    value: (sum / 2) * 100,
    inputs: totals(entries),
    caveats: pooledCaveat(entries, "votes"),
  };
}

export type Moved = {
  key: string;
  labels: Array<string>;
  now: number;
  before: number;
};

export function pedersen(
  current: Array<Entry>,
  previous: Array<Entry>,
  group: (key: string) => string,
  splits: Set<string> = new Set(),
): Measured<{ moved: Array<Moved>; joined: number }> | null {
  const [now, before] = [totals(current).votes, totals(previous).votes];
  if (now <= 0 || before <= 0) {
    return null;
  }
  const moved = new Map<string, Moved>();
  const members = new Map<string, Set<string>>();
  const add = (entry: Entry, total: number, side: "now" | "before") => {
    const key = entry.isPooled ? "pooled" : group(entry.key);
    const row = moved.get(key) ?? { key, labels: [], now: 0, before: 0 };
    row[side] += entry.votes / total;
    if (!row.labels.includes(entry.label)) {
      row.labels.push(entry.label);
    }
    moved.set(key, row);
    members.set(key, (members.get(key) ?? new Set()).add(entry.key));
  };
  for (const entry of current) {
    add(entry, now, "now");
  }
  for (const entry of previous) {
    add(entry, before, "before");
  }
  let sum = 0;
  let joined = 0;
  for (const row of moved.values()) {
    sum += Math.abs(row.now - row.before);
    if (row.key !== "pooled" && (members.get(row.key)?.size ?? 0) > 1) {
      joined += 1;
    }
  }
  const caveats: Array<Caveat> = [];
  if (joined > 0) {
    caveats.push("lineage_joined");
  }
  if (
    moved
      .values()
      .some(
        (row) =>
          row.key !== "pooled" &&
          Math.min(row.now, row.before) === 0 &&
          Math.max(row.now, row.before) >= 0.1,
      )
  ) {
    caveats.push("unmatched_major");
  }
  if (current.some((entry) => splits.has(entry.key))) {
    caveats.push("split_new");
  }
  if (
    current.some((entry) => entry.isPooled && entry.votes > 0) ||
    previous.some((entry) => entry.isPooled && entry.votes > 0)
  ) {
    caveats.push("pooled_independents");
  }
  return {
    value: (sum / 2) * 100,
    inputs: {
      moved: moved
        .values()
        .toArray()
        .toSorted(
          (a, b) => Math.abs(b.now - b.before) - Math.abs(a.now - a.before),
        ),
      joined,
    },
    caveats,
  };
}

export type Unit = { valid: number; votes: Record<string, number> };

const gini = (values: Array<number>) => {
  const sorted = values.toSorted((a, b) => a - b);
  let [sum, weighted] = [0, 0];
  for (const [index, value] of sorted.entries()) {
    sum += value;
    weighted += (index + 1) * value;
  }
  const n = sorted.length;
  return sum <= 0 ? null : (2 * weighted) / (n * sum) - (n + 1) / n;
};

export function nationalization(
  units: Array<Unit>,
  entries: Array<Entry>,
): Measured<{
  units: number;
  parties: Array<{ key: string; label: string; share: number; score: number }>;
}> | null {
  const counted = units.filter((unit) => unit.valid > 0);
  const total = totals(entries).votes;
  if (counted.length < 2 || total <= 0) {
    return null;
  }
  const parties: Array<{
    key: string;
    label: string;
    share: number;
    score: number;
  }> = [];
  let value = 0;
  for (const entry of entries) {
    if (entry.isPooled || entry.votes <= 0) {
      continue;
    }
    const found = gini(
      counted.map((unit) => (unit.votes[entry.key] ?? 0) / unit.valid),
    );
    if (found == null) {
      continue;
    }
    const share = entry.votes / total;
    parties.push({
      key: entry.key,
      label: entry.label,
      share,
      score: 1 - found,
    });
    value += share * (1 - found);
  }
  if (parties.length === 0) {
    return null;
  }
  let weight = 0;
  for (const party of parties) {
    weight += party.share;
  }
  return {
    value: value / weight,
    inputs: {
      units: counted.length,
      parties: parties.toSorted((a, b) => b.share - a.share),
    },
    caveats: counted.length < 10 ? ["few_units"] : [],
  };
}
