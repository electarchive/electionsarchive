export type Match = "party" | "person" | "family";

export type Grouping = "option" | "alliance" | "party";

export type Option = {
  contest: string;
  kind: string;
  party: string | null;
  person: string | null;
  label: string;
  short: string | null;
  color: string | null;
  lean: number | null;
  votes: number;
  outcome: string | null;
  note?: string | null;
  isIndependent?: boolean;
  seats: number | null;
  alliance: { id: string; label: string; anchor: string | null } | null;
  line: string | null;
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
  label: string;
  person: string | null;
  party: string | null;
  votes: number;
  share: number;
  outcome: string | null;
  note: string | null;
  isIndependent: boolean;
  seats: number | null;
  lines: Array<{ label: string; votes: number }>;
};

export type Row = {
  key: string;
  label: string;
  short: string | null;
  color: string | null;
  lean: number | null;
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
  "absorbed_by",
  "regional_branch",
]);

type Group = { lead: Option; cell: Cell; members: Array<Option> };

export function ancestry(relations: Array<Relation>, match: Match) {
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
      if (from === relation.from) {
        parent.set(from, to);
      } else {
        parent.set(to, from);
      }
    }
  }
  return root;
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
          label: option.label,
          person: option.person,
          party: option.party,
          votes: option.votes,
          share: 0,
          outcome: option.outcome,
          note: option.note ?? null,
          isIndependent: option.isIndependent ?? false,
          seats: option.seats,
          lines: [],
        },
        members: [option],
      });
      continue;
    }
    group.members.push(option);
    group.cell.votes += option.votes;
    group.cell.outcome ??= option.outcome;
    group.cell.note ??= option.note ?? null;
    group.cell.isIndependent &&= option.isIndependent ?? false;
    if (option.seats != null) {
      group.cell.seats = (group.cell.seats ?? 0) + option.seats;
    }
    if (option.votes > group.lead.votes) {
      group.lead = option;
      group.cell.label = option.label;
      group.cell.person = option.person;
      group.cell.party = option.party;
    }
  }
  for (const group of groups.values()) {
    group.cell.share = side.valid > 0 ? group.cell.votes / side.valid : 0;
    const isFused = group.members.every(
      (member) => member.person != null && member.person === group.lead.person,
    );
    const lines = group.members.flatMap((member) =>
      !isFused || member.line == null || member.votes === 0
        ? []
        : [{ label: member.line, votes: member.votes }],
    );
    group.cell.lines =
      lines.length < 2 ? [] : lines.toSorted((a, b) => b.votes - a.votes);
  }
  return groups;
}

function alliances(options: Array<Option>) {
  const members = new Map<
    string,
    Map<string, { option: Option; votes: number }>
  >();
  const leads = new Map<string, string>();
  for (const option of options) {
    const id = option.alliance?.id;
    if (option.alliance?.anchor != null) {
      leads.set(option.alliance.id, option.alliance.anchor);
    }
    if (id == null || option.party == null) {
      continue;
    }
    const parties =
      members.get(id) ?? new Map<string, { option: Option; votes: number }>();
    members.set(id, parties);
    const member = parties.get(option.party) ?? { option, votes: 0 };
    member.votes += option.votes;
    parties.set(option.party, member);
  }
  for (const id of leads.keys()) {
    if (!members.has(id)) {
      members.set(id, new Map());
    }
  }
  return new Map(
    members.entries().map(([id, parties]) => {
      const ranked = parties
        .values()
        .filter((member) => member.votes > 0)
        .toArray()
        .toSorted((a, b) => b.votes - a.votes);
      const lead = leads.get(id);
      const option =
        ranked.find((member) => member.option.party === lead)?.option ??
        ranked[0]?.option ??
        null;
      return [
        id,
        {
          option,
          party: lead ?? option?.party ?? null,
          roster: ranked
            .map((member) => member.option.short ?? member.option.label)
            .join(", "),
        },
      ] as const;
    }),
  );
}

function isPooledIndependent(
  option: Option,
  party: string | null,
  match: Match,
  grouping: Grouping,
  contests: number,
) {
  return (
    match !== "person" &&
    (grouping === "party" || contests > 1) &&
    party == null &&
    option.kind === "candidate" &&
    option.isIndependent === true
  );
}

function independent(row: Row): Row {
  if (row.key !== "independent") {
    return row;
  }
  return {
    ...row,
    label: "Independent",
    short: "Independent",
    color: null,
    current: row.current && {
      ...row.current,
      label: "Independent",
      person: null,
    },
    earlier: row.earlier && {
      ...row.earlier,
      label: "Independent",
      person: null,
    },
  };
}

export function compare(
  current: Side,
  earlier: Side | null,
  relations: Array<Relation>,
  match: Match = "party",
  grouping: Grouping = "option",
): Array<Row> {
  const root = ancestry(relations, match);
  const options = (earlier == null ? [current] : [current, earlier]).flatMap(
    (side) => side.options,
  );
  const anchors = alliances(options);
  const anchored = new Set(
    anchors
      .values()
      .flatMap((entry) => (entry.party == null ? [] : [root(entry.party)])),
  );
  const allied = new Set(
    options.flatMap((option) =>
      option.alliance == null ? [] : [option.contest],
    ),
  );
  const lines = new Map<string, Option>();
  const totals = new Map<string, number>();
  const owned = new Map<string, string>(
    options.flatMap((option) =>
      option.person == null || option.party == null || option.line != null
        ? []
        : [[`${option.contest}:${option.person}`, option.party] as const],
    ),
  );
  for (const option of options) {
    const seat = `${option.contest}:${option.person}`;
    totals.set(
      option.contest,
      (totals.get(option.contest) ?? 0) + option.votes,
    );
    const held = lines.get(seat);
    const own = owned.get(seat);
    if (
      option.person != null &&
      option.party != null &&
      (held == null ||
        (own != null && held.party !== own && option.party === own) ||
        ((own == null || (held.party === own) === (option.party === own)) &&
          held.votes < option.votes))
    ) {
      lines.set(seat, option);
    }
  }
  const heads = new Map<string, Option>();
  for (const line of lines.values()) {
    const seat = `${line.contest}:${root(line.party ?? "")}`;
    if ((heads.get(seat)?.votes ?? -1) < line.votes) {
      heads.set(seat, line);
    }
  }
  const crowded = new Set(
    lines
      .values()
      .filter(
        (line) =>
          heads.get(`${line.contest}:${root(line.party ?? "")}`)?.person !==
            line.person && line.votes >= 0.01 * (totals.get(line.contest) ?? 0),
      )
      .map((line) => root(line.party ?? "")),
  );
  const contests = new Set(current.options.map((option) => option.contest))
    .size;
  const base = (option: Option) => {
    if (grouping === "alliance" && option.alliance != null) {
      const anchor = anchors.get(option.alliance.id)?.party;
      return `alliance:${anchor == null ? option.alliance.id : root(anchor)}`;
    }
    const line = lines.get(`${option.contest}:${option.person}`) ?? option;
    const party = line.party == null ? null : root(line.party);
    if (
      grouping === "alliance" &&
      party != null &&
      anchored.has(party) &&
      allied.has(option.contest)
    ) {
      return `alliance:${party}`;
    }
    if (isPooledIndependent(option, party, match, grouping, contests)) {
      return "independent";
    }
    if (
      option.person != null &&
      (grouping !== "party" || party == null) &&
      (match === "person" ||
        party == null ||
        crowded.has(party) ||
        heads.get(`${option.contest}:${party}`)?.person !== option.person)
    ) {
      return `person:${option.person}`;
    }
    if (option.kind === "answer") {
      return `answer:${option.label.toLowerCase()}`;
    }
    if (party != null) {
      return `party:${party}`;
    }
    return option.kind === "others" || option.kind === "candidate"
      ? `kind:${option.kind}:${option.label.toLowerCase()}`
      : `kind:${option.kind}`;
  };
  const both = new Set(
    current.options
      .map((option) => option.person)
      .filter(
        (person) =>
          person != null &&
          earlier?.options.some((option) => option.person === person) === true,
      ),
  );
  const personal = new Set(
    options
      .filter(
        (option) =>
          both.has(option.person) && base(option).startsWith("person:"),
      )
      .map((option) => option.person),
  );
  const identify = (option: Option) =>
    personal.has(option.person) && !base(option).startsWith("alliance:")
      ? `person:${option.person}`
      : base(option);
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
    const alliance = key.startsWith("alliance:") ? lead.alliance : null;
    const name = (group: Group | null) => {
      const own = group?.lead.alliance;
      if (own == null) {
        return null;
      }
      const roster = anchors.get(own.id)?.roster ?? "";
      return { label: own.label === "" ? roster : own.label, roster };
    };
    const flatten = (group: Group | null) => {
      const named = alliance == null ? null : name(group);
      return group == null || named == null
        ? (group?.cell ?? null)
        : { ...group.cell, label: named.label, person: null, party: null };
    };
    const anchor = alliance == null ? null : anchors.get(alliance.id)?.option;
    const named = alliance == null ? null : name(present ?? past);
    return {
      key,
      label: named?.label ?? lead.label,
      short:
        named == null ? lead.short : named.roster === "" ? null : named.roster,
      color: anchor?.color ?? lead.color,
      lean: anchor?.lean ?? lead.lean,
      current: flatten(present),
      earlier: flatten(past),
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
  const rank = (row: Row) =>
    row.key.startsWith("kind:") && !row.key.startsWith("kind:candidate:")
      ? 1
      : 0;
  return rows
    .map((row) => independent(row))
    .toSorted(
      (a, b) =>
        Number(a.current == null) - Number(b.current == null) ||
        rank(a) - rank(b) ||
        (b.current?.votes ?? 0) - (a.current?.votes ?? 0) ||
        (b.earlier?.votes ?? 0) - (a.earlier?.votes ?? 0),
    );
}
