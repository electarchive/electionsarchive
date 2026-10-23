import { m } from "#lib/paraglide/messages.js";

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
  isIndependent?: boolean;
}) {
  if (entry.caucus != null) {
    return `${entry.party ?? "independent"}>${entry.caucus}`;
  }
  return entry.party == null && entry.isIndependent === true
    ? "independent"
    : entry.party;
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
