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
  };
