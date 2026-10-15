export type Match = "party" | "person" | "family";

export type Option = {
  contest: string;
  kind: string;
  party: string | null;
  person: string | null;
  label: string;
  short: string | null;
  color: string | null;
  votes: number;
  outcome: string | null;
  seats: number | null;
};

export type Side = {
  valid: number;
  options: Array<Option>;
  backing: Array<{ party: string; candidate: string }>;
};

export type Relation = {
  from: string;
  to: string;
  kind: string;
  toLabel: string;
};

export type Cell = {
  votes: number;
  share: number;
  outcome: string | null;
  seats: number | null;
};

export type Row = {
  key: string;
  label: string;
  short: string | null;
  color: string | null;
  current: Cell | null;
  earlier: Cell | null;
  delta: number | null;
  backed: string | null;
  origin: string | null;
};

const LINEAGE = new Set([
  "successor_of",
  "refounded_as",
  "list_vehicle_for",
  "merged_into",
]);

type Group = { lead: Option; cell: Cell };

function lineage(relations: Array<Relation>, match: Match) {
  const parent = new Map<string, string>();
  const root = (party: string): string => {
    let node = party;
    while (parent.has(node) && parent.get(node) !== node) {
      node = parent.get(node) ?? node;
    }
    return node;
  };
  for (const relation of relations) {
    if (
      relation.kind === "closed_by_court" ||
      (match !== "family" && !LINEAGE.has(relation.kind))
    ) {
      continue;
    }
    const [from, to] = [root(relation.from), root(relation.to)];
    if (from !== to) {
      parent.set(from, to);
    }
  }
  return root;
}

function crowd(sides: Array<Side>, root: (party: string) => string) {
  const crowded = new Set<string>();
  for (const side of sides) {
    const heads = new Map<string, string>();
    for (const option of side.options) {
      if (option.party == null || option.person == null) {
        continue;
      }
      const party = root(option.party);
      const seat = `${option.contest}:${party}`;
      if ((heads.get(seat) ?? option.person) !== option.person) {
        crowded.add(party);
      }
      heads.set(seat, option.person);
    }
  }
  return crowded;
}

function tally(side: Side, identify: (option: Option) => string) {
  const groups = new Map<string, Group>();
  for (const option of side.options) {
    const key = identify(option);
    const group = groups.get(key);
    if (group == null) {
      groups.set(key, {
        lead: option,
        cell: {
          votes: option.votes,
          share: 0,
          outcome: option.outcome,
          seats: option.seats,
        },
      });
      continue;
    }
    group.cell.votes += option.votes;
    group.cell.outcome ??= option.outcome;
    if (option.seats != null) {
      group.cell.seats = (group.cell.seats ?? 0) + option.seats;
    }
    if (option.votes > group.lead.votes) {
      group.lead = option;
    }
  }
  for (const group of groups.values()) {
    group.cell.share = side.valid > 0 ? group.cell.votes / side.valid : 0;
  }
  return groups;
}

export function compare(
  current: Side,
  earlier: Side | null,
  relations: Array<Relation>,
  match: Match = "party",
): Array<Row> {
  const root = lineage(relations, match);
  const crowded = crowd(earlier == null ? [current] : [current, earlier], root);
  const identify = (option: Option) => {
    const party = option.party == null ? null : root(option.party);
    if (
      option.person != null &&
      (match === "person" || party == null || crowded.has(party))
    ) {
      return `person:${option.person}`;
    }
    return party == null ? `kind:${option.kind}` : `party:${party}`;
  };
  const now = tally(current, identify);
  const then =
    earlier == null ? new Map<string, Group>() : tally(earlier, identify);
  const pairs: Array<{
    key: string;
    lead: Option;
    present: Group | null;
    past: Group | null;
  }> = [];
  for (const [key, group] of now) {
    pairs.push({
      key,
      lead: group.lead,
      present: group,
      past: then.get(key) ?? null,
    });
  }
  for (const [key, group] of then) {
    if (!now.has(key)) {
      pairs.push({ key, lead: group.lead, present: null, past: group });
    }
  }
  const rows = pairs.map(({ key, lead, present, past }): Row => {
    const party = key.startsWith("party:") ? key.slice(6) : null;
    const absent = present == null ? current : past == null ? earlier : null;
    return {
      key,
      label: lead.label,
      short: lead.short,
      color: lead.color,
      current: present?.cell ?? null,
      earlier: past?.cell ?? null,
      delta:
        present == null || past == null
          ? null
          : present.cell.share - past.cell.share,
      backed:
        absent?.backing.find((entry) => root(entry.party) === party)
          ?.candidate ?? null,
      origin:
        earlier == null || past != null
          ? null
          : (relations.find(
              (relation) =>
                relation.kind === "split_from" && root(relation.from) === party,
            )?.toLabel ?? null),
    };
  });
  const rank = (row: Row) => (row.key.startsWith("kind:") ? 1 : 0);
  return rows.toSorted(
    (a, b) =>
      Number(a.current == null) - Number(b.current == null) ||
      rank(a) - rank(b) ||
      (b.current?.votes ?? 0) - (a.current?.votes ?? 0) ||
      (b.earlier?.votes ?? 0) - (a.earlier?.votes ?? 0),
  );
}
