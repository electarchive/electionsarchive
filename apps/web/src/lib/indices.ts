import type { Caveat } from "@ea/analytics/indices";
import { numbers } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";

export type Figure =
  | "effectiveVotes"
  | "effectiveSeats"
  | "fragmentationVotes"
  | "fragmentationSeats"
  | "gallagher"
  | "loosemoreHanby"
  | "volatility"
  | "nationalization";

type Family =
  | "effective"
  | "fragmentation"
  | "gallagher"
  | "loosemore"
  | "volatility"
  | "nationalization";

const FAMILIES: Record<Figure, Family> = {
  effectiveVotes: "effective",
  effectiveSeats: "effective",
  fragmentationVotes: "fragmentation",
  fragmentationSeats: "fragmentation",
  gallagher: "gallagher",
  loosemoreHanby: "loosemore",
  volatility: "volatility",
  nationalization: "nationalization",
};

const DIGITS: Record<Family, number> = {
  effective: 2,
  fragmentation: 2,
  gallagher: 1,
  loosemore: 1,
  volatility: 1,
  nationalization: 2,
};

const ABOUT: Record<
  Family,
  {
    meaning: () => string;
    range: () => string;
    formula: string;
    section: string;
  }
> = {
  effective: {
    meaning: m.indices_effective_meaning,
    range: m.indices_effective_range,
    formula: "N = 1 / Σ pᵢ²",
    section: "effective",
  },
  fragmentation: {
    meaning: m.indices_fragmentation_meaning,
    range: m.indices_fragmentation_range,
    formula: "F = 1 − Σ pᵢ²",
    section: "effective",
  },
  gallagher: {
    meaning: m.indices_gallagher_meaning,
    range: m.indices_gallagher_range,
    formula: "LSq = √(½ Σ (vᵢ − sᵢ)²)",
    section: "disproportionality",
  },
  loosemore: {
    meaning: m.indices_loosemore_meaning,
    range: m.indices_loosemore_range,
    formula: "D = ½ Σ |vᵢ − sᵢ|",
    section: "disproportionality",
  },
  volatility: {
    meaning: m.indices_volatility_meaning,
    range: m.indices_volatility_range,
    formula: "V = ½ Σ |pᵢ,t − pᵢ,t−1|",
    section: "volatility",
  },
  nationalization: {
    meaning: m.indices_nationalization_meaning,
    range: m.indices_nationalization_range,
    formula: "PSNS = Σ pᵢ (1 − Giniᵢ)",
    section: "nationalization",
  },
};

export const figureLabel = (figure: Figure) =>
  ({
    effectiveVotes: m.indices_effective_votes,
    effectiveSeats: m.indices_effective_seats,
    fragmentationVotes: m.indices_fragmentation_votes,
    fragmentationSeats: m.indices_fragmentation_seats,
    gallagher: m.indices_gallagher_name,
    loosemoreHanby: m.indices_loosemore,
    volatility: m.indices_volatility,
    nationalization: m.indices_nationalization,
  })[figure]();

export const about = (figure: Figure) => ABOUT[FAMILIES[figure]];

export const figure = (kind: Figure, value: number) => {
  const digits = DIGITS[FAMILIES[kind]];
  return numbers({
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
};

export const caveatLabel = (caveat: Caveat) =>
  ({
    pooled_independents: m.indices_caveat_pooled_independents,
    uncontested_excluded: m.indices_caveat_uncontested_excluded,
    first_round_votes: m.indices_caveat_first_round_votes,
    list_votes: m.indices_caveat_list_votes,
    sisters_joined: m.indices_caveat_sisters_joined,
    alliance_seats_credited: m.indices_caveat_alliance_seats_credited,
    alliances_as_units: m.indices_caveat_alliances_as_units,
    lineage_joined: m.indices_caveat_lineage_joined,
    split_new: m.indices_caveat_split_new,
    unmatched_major: m.indices_caveat_unmatched_major,
    few_units: m.indices_caveat_few_units,
  })[caveat]();
