import { m } from "#lib/paraglide/messages.js";

export const GOALS = [
  "county",
  "history",
  "turnout",
  "precinct",
  "precinct_shapes",
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

export const US_TIERS = {
  federal: ["president", "us_senate", "us_house"],
  statewide: ["governor", "statewide_exec", "judicial"],
  legislative: ["state_upper", "state_lower"],
  local: [
    "county",
    "municipality",
    "township",
    "school",
    "college",
    "special",
    "other",
    "local_measure",
  ],
  referendums: ["statewide_measure"],
  precinct_shapes: ["precinct"],
  turnout: ["general", "primary", "runoff", "referendum"],
} as const;
export const COUNTRY_TIERS = [
  "national",
  "subnational",
  "local",
  "referendums",
  "ep",
] as const;
export type UsTier = keyof typeof US_TIERS;
export type CountryTier = (typeof COUNTRY_TIERS)[number];
export type Tier = UsTier | CountryTier;

export const US_COLUMNS = [
  "before_any",
  "before_county",
  "before_shapes",
  "since_any",
  "since_county",
  "since_shapes",
  "precinct_2000",
  "precinct_2016",
] as const;
export const COUNTRY_COLUMNS = [
  "any",
  "constituency",
  "fine",
  "shapes",
] as const;
export type Column =
  | (typeof US_COLUMNS)[number]
  | (typeof COUNTRY_COLUMNS)[number]
  | "before_district"
  | "before"
  | "since";

export const TIER_LABELS: Record<Tier, () => string> = {
  federal: m.coverage_tier_federal,
  statewide: m.coverage_tier_statewide,
  legislative: m.coverage_tier_legislative,
  local: m.coverage_tier_local,
  turnout: m.coverage_tier_turnout,
  precinct_shapes: m.coverage_column_precinct_shapes,
  national: m.coverage_tier_national,
  subnational: m.coverage_tier_subnational,
  referendums: m.coverage_tier_referendums,
  ep: m.coverage_tier_ep,
};

export const COLUMN_LABELS: Record<Column, () => string> = {
  before_any: m.coverage_column_any,
  before_county: m.coverage_column_county,
  before_district: m.coverage_column_district,
  before_shapes: m.coverage_column_shapes,
  since_any: m.coverage_column_any,
  since_county: m.coverage_column_county,
  since_shapes: m.coverage_column_shapes,
  precinct_2000: m.coverage_era_mid,
  precinct_2016: m.coverage_era_late,
  any: m.coverage_column_any,
  constituency: m.coverage_column_constituency,
  fine: m.coverage_column_fine,
  shapes: m.coverage_column_shapes,
  before: m.coverage_column_any,
  since: m.coverage_column_any,
};

export const ERA_LABELS = {
  before: m.coverage_era_before,
  since: m.coverage_era_since,
  mid: m.coverage_era_mid,
  late: m.coverage_era_late,
};

export const columnGroup = (column: Column) =>
  column.startsWith("before")
    ? m.coverage_era_before
    : column.startsWith("since")
      ? m.coverage_era_since
      : m.coverage_column_precinct;

export function headline(polity: string, tier: string) {
  if (polity !== "us") {
    return {
      column: "constituency",
      goal: tier as Goal,
      title:
        tier === "referendums"
          ? m.coverage_headline_region
          : m.coverage_headline_country,
      progress:
        tier === "referendums"
          ? m.coverage_progress_referendums
          : m.coverage_progress_races,
    } as const;
  }
  if (tier === "local") {
    return {
      column: "since_any",
      goal: "local",
      title: m.coverage_headline_local,
      progress: m.coverage_progress_local,
    } as const;
  }
  if (tier === "precinct_shapes") {
    return {
      column: "precinct_2016",
      goal: "precinct_shapes",
      title: m.coverage_column_precinct_shapes,
      progress: m.coverage_progress_precincts,
    } as const;
  }
  if (tier === "turnout") {
    return {
      column: "since",
      goal: "turnout",
      title: m.coverage_headline_turnout,
      progress: m.coverage_progress_turnout,
    } as const;
  }
  return {
    column: "since_county",
    goal: "county",
    title: m.coverage_headline_us,
    progress: m.coverage_progress,
  } as const;
}

export function measures(
  polity: string,
  tier: string,
): Array<{
  era: "before" | "since" | "mid" | "late" | null;
  items: Array<{ column: Column; goal: Goal; title: () => string }>;
}> {
  if (polity !== "us") {
    const lead = headline(polity, tier);
    return [
      {
        era: null,
        items: [
          { column: "constituency", goal: tier as Goal, title: lead.title },
          { column: "fine", goal: "fine", title: m.coverage_column_fine },
          { column: "shapes", goal: "shapes", title: m.coverage_column_shapes },
        ],
      },
    ];
  }
  if (tier === "local") {
    return [
      {
        era: "since",
        items: [
          { column: "since_any", goal: "local", title: m.coverage_column_any },
        ],
      },
    ];
  }
  if (tier === "precinct_shapes") {
    return [
      {
        era: "mid",
        items: [
          {
            column: "precinct_2000",
            goal: "precinct_shapes",
            title: m.coverage_column_precinct_shapes,
          },
        ],
      },
      {
        era: "late",
        items: [
          {
            column: "precinct_2016",
            goal: "precinct_shapes",
            title: m.coverage_column_precinct_shapes,
          },
        ],
      },
    ];
  }
  if (tier === "turnout") {
    return [
      {
        era: "before",
        items: [
          { column: "before", goal: "turnout", title: m.coverage_gap_turnout },
        ],
      },
      {
        era: "since",
        items: [
          { column: "since", goal: "turnout", title: m.coverage_gap_turnout },
        ],
      },
    ];
  }
  return [
    {
      era: "before",
      items: [
        tier === "legislative"
          ? {
              column: "before_district",
              goal: "history",
              title: m.coverage_column_district,
            }
          : {
              column: "before_county",
              goal: "history",
              title: m.coverage_column_county,
            },
        {
          column: "before_shapes",
          goal: "shapes_history",
          title: m.coverage_column_shapes,
        },
      ],
    },
    {
      era: "since",
      items: [
        {
          column: "since_county",
          goal: "county",
          title: m.coverage_column_county,
        },
        {
          column: "since_shapes",
          goal: "shapes",
          title: m.coverage_column_shapes,
        },
      ],
    },
    {
      era: "mid",
      items: [
        {
          column: "precinct_2000",
          goal: "precinct",
          title: m.coverage_column_precinct,
        },
      ],
    },
    {
      era: "late",
      items: [
        {
          column: "precinct_2016",
          goal: "precinct",
          title: m.coverage_column_precinct,
        },
      ],
    },
  ];
}

export function grains(polity: string, tier: string): Array<Goal> {
  if (polity !== "us") {
    return [tier as Goal, "fine", "shapes"];
  }
  if (tier === "local" || tier === "turnout" || tier === "precinct_shapes") {
    return [tier];
  }
  return ["county", "precinct", "shapes", "history", "shapes_history"];
}

export type Reason =
  | "complete"
  | "major"
  | "coarse"
  | "partial"
  | "totals"
  | "bare"
  | "knownGap"
  | "blocked"
  | "pending"
  | "absent"
  | "units"
  | "unshaped"
  | "undrawn";
export type Counts = Record<
  | Reason
  | "total"
  | "turnout"
  | "precincts"
  | "shaped"
  | "icpsr"
  | "published"
  | "upcoming"
  | "inapplicable"
  | "populationTotal"
  | "populationComplete"
  | "populationAbsent"
  | "populationEstimated",
  number
>;

export const REASONS: Record<Goal, Array<Reason>> = {
  county: [
    "complete",
    "pending",
    "partial",
    "totals",
    "bare",
    "absent",
    "blocked",
    "knownGap",
  ],
  history: [
    "complete",
    "partial",
    "totals",
    "bare",
    "absent",
    "blocked",
    "knownGap",
  ],
  precinct: [
    "complete",
    "pending",
    "major",
    "coarse",
    "partial",
    "totals",
    "bare",
    "absent",
    "blocked",
    "knownGap",
  ],
  precinct_shapes: ["complete", "unshaped"],
  shapes: ["complete", "units", "unshaped", "undrawn", "blocked", "knownGap"],
  shapes_history: [
    "complete",
    "units",
    "unshaped",
    "undrawn",
    "blocked",
    "knownGap",
  ],
  local: [
    "complete",
    "pending",
    "partial",
    "totals",
    "bare",
    "absent",
    "blocked",
    "knownGap",
  ],
  national: [
    "complete",
    "pending",
    "partial",
    "totals",
    "bare",
    "absent",
    "blocked",
    "knownGap",
  ],
  fine: [
    "complete",
    "pending",
    "coarse",
    "partial",
    "totals",
    "bare",
    "absent",
    "blocked",
    "knownGap",
  ],
  subnational: [
    "complete",
    "pending",
    "partial",
    "totals",
    "bare",
    "absent",
    "blocked",
    "knownGap",
  ],
  referendums: [
    "complete",
    "pending",
    "partial",
    "totals",
    "bare",
    "absent",
    "blocked",
    "knownGap",
  ],
  ep: [
    "complete",
    "pending",
    "partial",
    "totals",
    "bare",
    "absent",
    "blocked",
    "knownGap",
  ],
  turnout: ["complete", "partial", "coarse", "absent", "blocked", "knownGap"],
};

export const LABELS: Record<Reason, () => string> = {
  complete: m.coverage_reason_complete,
  major: m.coverage_reason_major,
  coarse: m.coverage_reason_coarse,
  partial: m.coverage_reason_partial,
  totals: m.coverage_reason_totals,
  bare: m.coverage_reason_bare,
  knownGap: m.coverage_reason_known_gap,
  blocked: m.coverage_reason_blocked,
  pending: m.coverage_reason_pending,
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

const PRECINCT_SHAPE_LABELS: Partial<Record<Reason, () => string>> = {
  complete: m.coverage_reason_precinct_shaped,
  unshaped: m.coverage_reason_precinct_unshaped,
};

const COUNTRY_LABELS: Partial<Record<Reason, () => string>> = {
  coarse: m.coverage_country_reason_coarse,
  partial: m.coverage_country_reason_partial,
  unshaped: m.coverage_country_reason_unshaped,
  undrawn: m.coverage_country_reason_undrawn,
};

export function label(goal: Goal, reason: Reason, polity = "us") {
  return (
    (goal === "turnout" ? TURNOUT_LABELS[reason] : null) ??
    (goal === "precinct_shapes" ? PRECINCT_SHAPE_LABELS[reason] : null) ??
    (polity === "us" ? null : COUNTRY_LABELS[reason]) ??
    LABELS[reason]
  )();
}

export const READY_LEVELS = [
  "us_house",
  "us_senate",
  "governor",
  "statewide_exec",
  "judicial",
  "state_upper",
  "state_lower",
  "statewide_measure",
  "territory",
  "county_office",
  "municipal",
  "school_board",
  "special_district",
  "local_judicial",
  "local_measure",
];

export const READY_COUNTS: Record<string, typeof m.coverage_ready_of> = {
  judicial: m.coverage_ready_of_states,
  county_office: m.coverage_ready_of_governments,
  municipal: m.coverage_ready_of_governments,
  school_board: m.coverage_ready_of_governments,
  special_district: m.coverage_ready_of_governments,
  local_judicial: m.coverage_ready_of_held,
  local_measure: m.coverage_ready_of_measures,
};

export const LEVELS: Record<string, () => string> = {
  president: m.coverage_level_president,
  us_senate: m.coverage_level_us_senate,
  us_house: m.coverage_level_us_house,
  governor: m.coverage_level_governor,
  statewide_exec: m.coverage_level_statewide_exec,
  judicial: m.coverage_level_judicial,
  county_office: m.coverage_level_county_office,
  municipal: m.coverage_level_municipal,
  school_board: m.coverage_level_school_board,
  local_judicial: m.coverage_level_local_judicial,
  special_district: m.coverage_level_special_district,
  local_measure: m.coverage_level_local_measure,
  territory: m.coverage_level_territory,
  state_upper: m.coverage_level_state_upper,
  state_lower: m.coverage_level_state_lower,
  statewide_measure: m.coverage_level_statewide_measure,
  precinct: m.coverage_level_precinct,
  general: m.coverage_level_general,
  primary: m.coverage_level_primary,
  runoff: m.coverage_level_runoff,
  referendum: m.coverage_level_referendum,
  county: m.coverage_group_county,
  municipality: m.coverage_group_municipality,
  township: m.coverage_group_township,
  school: m.coverage_group_school,
  college: m.coverage_group_college,
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

export const CATEGORY_REASONS = {
  known_gap: "knownGap",
  blocked: "blocked",
  not_applicable: "inapplicable",
} as const;

export const CATEGORY_LABELS = {
  known_gap: m.coverage_reason_known_gap,
  blocked: m.coverage_reason_blocked,
  not_applicable: m.coverage_reason_inapplicable,
};

export const GRAIN_LABELS: Record<string, () => string> = {
  results: m.coverage_grain_results,
  county: m.coverage_grain_county,
  precinct: m.coverage_grain_precinct,
  fine: m.coverage_grain_fine,
  shapes: m.coverage_grain_shapes,
  turnout: m.coverage_grain_turnout,
  candidates: m.coverage_grain_candidates,
  dates: m.coverage_grain_dates,
  party: m.coverage_grain_party,
};

export const GAP_GRAINS: Record<Goal, ReadonlyArray<string>> = {
  county: ["results", "county"],
  history: ["results", "county"],
  precinct: ["results", "county", "precinct"],
  precinct_shapes: [],
  shapes: ["shapes"],
  shapes_history: ["shapes"],
  local: ["results"],
  national: ["results"],
  fine: ["results", "fine"],
  subnational: ["results"],
  referendums: ["results"],
  ep: ["results"],
  turnout: ["turnout"],
};

type Gap = {
  goal: string;
  state?: string | null;
  from: number | null;
  to: number | null;
  label?: string | null;
  note: string;
  sources?: string;
  checked?: string | null;
  ref?: string | null;
};

export function gapSpan(gap: Gap) {
  const years =
    gap.from == null
      ? null
      : gap.to == null
        ? m.coverage_gap_since({ year: String(gap.from) })
        : gap.from === gap.to
          ? String(gap.from)
          : `${gap.from}–${gap.to}`;
  return [gap.state, years, gap.label]
    .filter((part) => part != null && part !== "")
    .join(", ");
}

export function gapNote(gap: Gap) {
  const grain = GRAIN_LABELS[gap.goal]?.() ?? gap.goal;
  return gap.checked == null
    ? `${grain}: ${gap.note}`
    : m.coverage_gap_note({ grain, reason: gap.note, date: gap.checked });
}

export function gapTried(gap: Gap) {
  return [
    gap.sources == null || gap.sources === ""
      ? null
      : gap.checked == null
        ? m.coverage_gap_tried_undated({ sources: gap.sources })
        : m.coverage_gap_tried({ sources: gap.sources, date: gap.checked }),
    gap.ref == null ? null : m.coverage_gap_ref({ ref: gap.ref }),
  ]
    .filter((part) => part != null)
    .join(" ");
}
