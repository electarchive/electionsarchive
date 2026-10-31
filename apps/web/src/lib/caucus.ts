import { m } from "#lib/paraglide/messages.js";
import { independent } from "#lib/party.js";

export const ARRIVALS: Record<string, () => string> = {
  appointed: m.chamber_via_appointed,
  elected: m.chamber_via_elected,
  by_election: m.chamber_via_by_election,
  succession: m.chamber_via_succession,
  list_replacement: m.chamber_via_list_replacement,
  co_opted: m.chamber_via_co_opted,
  trustee: m.chamber_via_trustee,
  acting: m.chamber_via_acting,
};
export const DEPARTURES: Record<string, () => string> = {
  resigned: m.chamber_end_resigned,
  died: m.chamber_end_died,
  removed: m.chamber_end_removed,
  annulled: m.chamber_end_annulled,
  dissolved: m.chamber_end_dissolved,
  expelled: m.chamber_end_expelled,
  lost_seat: m.chamber_end_lost_seat,
  disqualified: m.chamber_end_disqualified,
  elevated: m.chamber_end_elevated,
  appointed: m.chamber_end_appointed,
  withdrew: m.chamber_end_withdrew,
  term_end: m.chamber_end_term_end,
};

type Party = {
  id: string;
  label: string;
  short: string | null;
  color: string | null;
  lean: number | null;
  parent?: string | null;
};

export function effective(entry: {
  party: string | null;
  caucus?: string | null;
  category?: "elected" | "appointed" | "ex_officio";
  isIndependent?: boolean;
}) {
  if (entry.category != null && entry.category !== "elected") {
    return `category:${entry.category}`;
  }
  if (entry.caucus != null) {
    return `${entry.party ?? "independent"}>${entry.caucus}`;
  }
  return entry.party == null && entry.isIndependent === true
    ? "independent"
    : entry.party;
}

export function partyOn(
  member: {
    party: string | null;
    caucuses?: Array<{ party: string; from: string | null; to: string | null }>;
    category?: "elected" | "appointed" | "ex_officio";
    isIndependent: boolean;
    from: string | null;
    to: string | null;
    spells: Array<{
      party: string | null;
      from: string | null;
      to: string | null;
    }>;
  },
  day: string,
) {
  const covering = <T extends { from: string | null; to: string | null }>(
    list: Array<T>,
    value: string,
  ) =>
    list.find(
      (entry) =>
        (entry.from ?? "") <= value && (entry.to == null || entry.to > value),
    );
  const at =
    member.to != null && member.to <= day
      ? new Date(Date.parse(member.to) - 864e5).toISOString().slice(0, 10)
      : member.from != null && member.from > day
        ? member.from
        : day;
  const spells = member.spells.filter(
    (entry) => entry.to == null || entry.to > (member.from ?? ""),
  );
  const spell =
    covering(spells, at) ??
    ((spells[0]?.from ?? "") > at ? spells[0] : undefined);
  const party =
    spell != null ? spell.party : spells.length === 0 ? member.party : null;
  const caucus = covering(member.caucuses ?? [], at)?.party ?? null;
  return effective({
    ...member,
    party,
    caucus: caucus === party ? null : caucus,
    isIndependent:
      spell != null
        ? spell.party == null
        : spells.length > 0 || member.isIndependent,
  });
}

export function segments(
  member: Parameters<typeof partyOn>[0],
  opened: string,
  closed: string,
  renames: Set<string> = new Set(),
) {
  const low =
    member.from != null && member.from > opened ? member.from : opened;
  const high = member.to != null && member.to < closed ? member.to : closed;
  const cuts = [
    ...new Set([
      low,
      ...member.spells.flatMap((spell) =>
        [spell.from, spell.to].filter(
          (value): value is string =>
            value != null && value > low && value < high,
        ),
      ),
      high,
    ]),
  ].toSorted((a, b) => (a < b ? -1 : Number(a > b)));
  const out: Array<{ party: string | null; from: string; to: string }> = [];
  const starts = [...cuts.slice(0, -1).entries()];
  for (const [index, from] of starts) {
    const party = partyOn(member, from);
    const to = cuts[index + 1] ?? high;
    const last = out.at(-1);
    if (
      last != null &&
      (last.party === party || renames.has(`${last.party}|${party}`))
    ) {
      last.party = party;
      last.to = to;
    } else {
      out.push({ party, from, to });
    }
  }
  return out;
}

export function stand(
  keys: Iterable<string | null>,
  parties: Array<Party>,
): Array<
  Party & { caucus: string | null; isStriped: boolean; isMade: boolean }
> {
  const info = new Map<string, Party>(
    parties.map((party) => [party.id, party]),
  );
  const named = (id: string) =>
    id === "independent"
      ? m.chamber_independent()
      : (info.get(id)?.short ?? info.get(id)?.label ?? "");
  const added: Array<
    Party & { caucus: string | null; isStriped: boolean; isMade: boolean }
  > = [];
  const unique = new Set(keys);
  for (const key of unique) {
    if (key === "category:appointed" || key === "category:ex_officio") {
      const isAppointed = key === "category:appointed";
      const label = isAppointed ? m.seats_appointed() : m.seats_ex_officio();
      added.push({
        id: key,
        label,
        short: label,
        color: independent(isAppointed ? 1 : 2),
        lean: Number.MAX_SAFE_INTEGER,
        caucus: null,
        isStriped: false,
        isMade: true,
      });
      continue;
    }
    if (
      key == null ||
      info.has(key) ||
      (key !== "independent" && !key.includes(">"))
    ) {
      continue;
    }
    const [party = "", caucus = null] = key.split(">", 2);
    const label =
      caucus == null
        ? m.chamber_independent()
        : m.chamber_caucus({ party: named(party), caucus: named(caucus) });
    const lean = caucus == null ? null : (info.get(caucus)?.lean ?? null);
    added.push({
      id: key,
      label,
      short: label,
      color: caucus == null ? null : (info.get(caucus)?.color ?? null),
      lean: lean == null ? null : lean + 0.01,
      caucus,
      isStriped: caucus != null,
      isMade: true,
    });
  }
  return [
    ...parties.map((party) => ({
      ...party,
      caucus: null,
      isStriped: false,
      isMade: false,
    })),
    ...added,
  ];
}

export const isUnplaced = (party: { key: string; lean: number | null }) =>
  party.lean == null && /^(?:party:)?[0-9a-f]{8}-/.test(party.key);

export function arrange<
  T extends {
    key: string;
    keys?: Array<string>;
    lean: number | null;
    near?: string | null;
    seats: number;
  },
>(parties: Array<T>) {
  const known = parties
    .filter((party) => party.lean != null)
    .toSorted((a, b) => (a.lean ?? 0) - (b.lean ?? 0) || b.seats - a.seats);
  let rest = 0;
  for (const party of known) {
    rest += party.seats;
  }
  rest /= 2;
  let middle: number | null = null;
  for (const party of known) {
    rest -= party.seats;
    if (rest <= 0) {
      middle = party.lean;
      break;
    }
  }
  const spot = (party: T) =>
    party.lean ??
    (isUnplaced(party)
      ? ((party.near == null
          ? null
          : parties.find((other) =>
              (other.keys ?? [other.key]).includes(party.near ?? ""),
            )?.lean) ??
          middle ??
          Infinity) + 0.005
      : Infinity);
  return parties.toSorted(
    (a, b) =>
      spot(a) - spot(b) || b.seats - a.seats || a.key.localeCompare(b.key),
  );
}
