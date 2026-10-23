import { m } from "#lib/paraglide/messages.js";

export const GOALS = [
  "county",
  "history",
  "legislature_history",
  "precinct",
  "shapes",
  "shapes_history",
  "local",
] as const;
export type Goal = (typeof GOALS)[number];
export type Reason =
  | "complete"
  | "coarse"
  | "partial"
  | "totals"
  | "bare"
  | "absent"
  | "units"
  | "unshaped"
  | "undrawn";
export type Counts = Record<
  Reason | "total" | "turnout" | "precincts" | "shaped" | "icpsr",
  number
>;

export const REASONS: Record<Goal, Array<Reason>> = {
  county: ["complete", "partial", "totals", "bare", "absent"],
  history: ["complete", "partial", "totals", "bare", "absent"],
  legislature_history: ["complete", "bare", "absent"],
  precinct: ["complete", "coarse", "partial", "totals", "bare", "absent"],
  shapes: ["complete", "units", "unshaped", "undrawn"],
  shapes_history: ["complete", "units", "unshaped", "undrawn"],
  local: ["complete", "absent"],
};

export const LABELS: Record<Reason, () => string> = {
  complete: m.coverage_reason_complete,
  coarse: m.coverage_reason_coarse,
  partial: m.coverage_reason_partial,
  totals: m.coverage_reason_totals,
  bare: m.coverage_reason_bare,
  absent: m.coverage_reason_absent,
  units: m.coverage_reason_units,
  unshaped: m.coverage_reason_unshaped,
  undrawn: m.coverage_reason_undrawn,
};

export const LEVELS: Record<string, () => string> = {
  president: m.coverage_level_president,
  us_senate: m.coverage_level_us_senate,
  us_house: m.coverage_level_us_house,
  governor: m.coverage_level_governor,
  statewide_exec: m.coverage_level_statewide_exec,
  judicial: m.coverage_level_judicial,
  state_upper: m.coverage_level_state_upper,
  state_lower: m.coverage_level_state_lower,
  statewide_measure: m.coverage_level_statewide_measure,
  county: m.coverage_group_county,
  municipality: m.coverage_group_municipality,
  township: m.coverage_group_township,
  school: m.coverage_group_school,
  special: m.coverage_group_special,
  other: m.coverage_group_other,
};
