import { m } from "#lib/paraglide/messages.js";

export const GOALS = [
  "county",
  "history",
  "turnout",
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
  precinct: ["complete", "coarse", "partial", "totals", "bare", "absent"],
  shapes: ["complete", "units", "unshaped", "undrawn"],
  shapes_history: ["complete", "units", "unshaped", "undrawn"],
  local: ["complete", "absent"],
  turnout: ["complete", "partial", "coarse", "absent"],
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

const TURNOUT_LABELS: Partial<Record<Reason, () => string>> = {
  partial: m.coverage_reason_turnout_partial,
  coarse: m.coverage_reason_turnout_coarse,
  absent: m.coverage_reason_turnout_absent,
};

export function label(goal: Goal, reason: Reason) {
  return (
    (goal === "turnout" ? TURNOUT_LABELS[reason] : null) ?? LABELS[reason]
  )();
}

export const PARTS = {
  national: m.coverage_part_national,
  legislature: m.coverage_part_legislature,
  recent: m.coverage_part_turnout_recent,
  earlier: m.coverage_part_turnout_earlier,
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
  general: m.coverage_level_general,
  primary: m.coverage_level_primary,
  runoff: m.coverage_level_runoff,
  referendum: m.coverage_level_referendum,
  county: m.coverage_group_county,
  municipality: m.coverage_group_municipality,
  township: m.coverage_group_township,
  school: m.coverage_group_school,
  special: m.coverage_group_special,
  other: m.coverage_group_other,
};
