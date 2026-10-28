import { m } from "#lib/paraglide/messages.js";

export const RELATIONS: Record<
  string,
  [(input: { name: string }) => string, (input: { name: string }) => string]
> = {
  merged_into: [m.rel_merged_into_out, m.rel_merged_into_in],
  split_from: [m.rel_split_from_out, m.rel_split_from_in],
  successor_of: [m.rel_successor_of_out, m.rel_successor_of_in],
  refounded_as: [m.rel_refounded_as_out, m.rel_refounded_as_in],
  absorbed_by: [m.rel_absorbed_by_out, m.rel_absorbed_by_in],
  list_vehicle_for: [m.rel_list_vehicle_for_out, m.rel_list_vehicle_for_in],
};

export const AFFILIATIONS: Record<string, (input: { name: string }) => string> =
  {
    ep_group: m.aff_ep_group,
    federation: m.aff_federation,
    international: m.aff_international,
    regional_branch: m.aff_regional_branch,
    coalition: m.aff_coalition,
  };

export function named(entry: { from: string | null; to: string | null }) {
  const from = entry.from?.slice(0, 4) ?? null;
  const to = entry.to?.slice(0, 4) ?? null;
  return from == null
    ? to == null
      ? ""
      : m.party_name_until({ year: to })
    : to == null
      ? m.party_name_from({ year: from })
      : from === to
        ? from
        : `${from}–${to}`;
}

export function formerly(
  names: Array<{
    name: string;
    short: string | null;
    from: string | null;
    to: string | null;
  }>,
  locale: string,
) {
  const former = names.filter((entry) => entry.to != null);
  const [only] = former;
  if (only == null) {
    return null;
  }
  if (former.length === 1) {
    return m.party_formerly_one({
      name: only.short == null ? only.name : `${only.name} (${only.short})`,
      years: named(only),
    });
  }
  return m.party_formerly({
    names: new Intl.ListFormat(locale, { type: "conjunction" }).format(
      former.map((entry) =>
        former.length > 2
          ? `${entry.short ?? entry.name} (${named(entry)})`
          : `${entry.name} (${[entry.short, named(entry)].filter((part) => part != null).join(", ")})`,
      ),
    ),
  });
}
