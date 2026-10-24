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
export const COUNTRY_GOALS = [
  "national",
  "fine",
  "shapes",
  "subnational",
  "local",
  "referendums",
  "ep",
] as const;
export type UsGoal = (typeof GOALS)[number];
export type CountryGoal = (typeof COUNTRY_GOALS)[number];
export type Goal = UsGoal | CountryGoal;

export function isGoal(goal: string): goal is UsGoal {
  return (GOALS as ReadonlyArray<string>).includes(goal);
}

export function isCountryGoal(goal: string): goal is CountryGoal {
  return (COUNTRY_GOALS as ReadonlyArray<string>).includes(goal);
}
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
  local: ["complete", "totals", "bare", "absent"],
  national: ["complete", "totals", "bare", "absent"],
  fine: ["complete", "coarse", "totals", "bare", "absent"],
  subnational: ["complete", "totals", "bare", "absent"],
  referendums: ["complete", "totals", "bare", "absent"],
  ep: ["complete", "totals", "bare", "absent"],
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

const COUNTRY_LABELS: Partial<Record<Reason, () => string>> = {
  coarse: m.coverage_country_reason_coarse,
  unshaped: m.coverage_country_reason_unshaped,
  undrawn: m.coverage_country_reason_undrawn,
};

export function label(goal: Goal, reason: Reason, polity = "us") {
  return (
    (goal === "turnout" ? TURNOUT_LABELS[reason] : null) ??
    (polity === "us" ? null : COUNTRY_LABELS[reason]) ??
    LABELS[reason]
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

const COUNTRY_LEVELS: Record<string, () => string> = {
  br_president: m.coverage_level_br_president,
  br_camara: m.coverage_level_br_camara,
  br_senado: m.coverage_level_br_senado,
  br_governor: m.coverage_level_br_governor,
  br_assemblies: m.coverage_level_br_assemblies,
  br_municipal: m.coverage_level_br_municipal,
  ca_commons: m.coverage_level_ca_commons,
  ca_commons_by: m.coverage_level_ca_commons_by,
  ca_provincial: m.coverage_level_ca_provincial,
  ch_national_council: m.coverage_level_ch_national_council,
  ch_council_of_states: m.coverage_level_ch_council_of_states,
  ch_federal_votes: m.coverage_level_ch_federal_votes,
  de_bundestag: m.coverage_level_de_bundestag,
  de_landtage: m.coverage_level_de_landtage,
  fr_assemblee: m.coverage_level_fr_assemblee,
  fr_regionales: m.coverage_level_fr_regionales,
  fr_departementales: m.coverage_level_fr_departementales,
  fr_municipales: m.coverage_level_fr_municipales,
  gb_commons: m.coverage_level_gb_commons,
  gb_commons_by: m.coverage_level_gb_commons_by,
  gb_holyrood: m.coverage_level_gb_holyrood,
  gb_senedd: m.coverage_level_gb_senedd,
  gb_ni_assembly: m.coverage_level_gb_ni_assembly,
  gb_stormont: m.coverage_level_gb_stormont,
  gb_councils: m.coverage_level_gb_councils,
  gb_referendums: m.coverage_level_gb_referendums,
  ie_dail: m.coverage_level_ie_dail,
  ie_dail_by: m.coverage_level_ie_dail_by,
  ie_local: m.coverage_level_ie_local,
  it_camera: m.coverage_level_it_camera,
  it_senato: m.coverage_level_it_senato,
  it_regionali: m.coverage_level_it_regionali,
  tr_tbmm: m.coverage_level_tr_tbmm,
  tr_tbmm_by: m.coverage_level_tr_tbmm_by,
  tr_senate: m.coverage_level_tr_senate,
  tr_local: m.coverage_level_tr_local,
  ep: m.coverage_level_ep,
  president: m.coverage_level_country_president,
  referendums: m.coverage_level_country_referendums,
};

export function levelLabel(polity: string, key: string) {
  if (polity === "us") {
    return LEVELS[key]?.() ?? key;
  }
  return (COUNTRY_LEVELS[`${polity}_${key}`] ?? COUNTRY_LEVELS[key])?.() ?? key;
}

export function spans(years: Array<number>, breaks: Array<number>) {
  const runs: Array<[number, number]> = [];
  const sorted = years.toSorted((a, b) => a - b);
  for (const year of sorted) {
    const last = runs.at(-1);
    if (last != null && breaks.every((at) => at <= last[1] || at >= year)) {
      last[1] = year;
    } else {
      runs.push([year, year]);
    }
  }
  return runs
    .map(([from, to]) => (from === to ? String(from) : `${from}–${to}`))
    .join(", ");
}
