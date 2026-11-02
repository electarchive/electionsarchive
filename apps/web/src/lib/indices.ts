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

const EFFECTIVE = [
  m.indices_band_effective_1,
  m.indices_band_effective_2,
  m.indices_band_effective_3,
  m.indices_band_effective_4,
  m.indices_band_effective_5,
];
const PROPORTION = [
  m.indices_band_gallagher_1,
  m.indices_band_gallagher_2,
  m.indices_band_gallagher_3,
  m.indices_band_gallagher_4,
  m.indices_band_gallagher_5,
];
const EFFECTIVE_CUTS = [1.8, 2.6, 4, 6];

const SCALES: Record<
  Family,
  {
    from: number;
    to: number;
    cuts: Array<number>;
    names: Array<() => string>;
    low: () => string;
    high: () => string;
  }
> = {
  effective: {
    from: 1,
    to: 8,
    cuts: EFFECTIVE_CUTS,
    names: EFFECTIVE,
    low: m.indices_end_effective_low,
    high: m.indices_end_effective_high,
  },
  fragmentation: {
    from: 0,
    to: 0.9,
    cuts: EFFECTIVE_CUTS.map((cut) => 1 - 1 / cut),
    names: EFFECTIVE,
    low: m.indices_end_effective_low,
    high: m.indices_end_effective_high,
  },
  gallagher: {
    from: 0,
    to: 30,
    cuts: [2, 5, 10, 17],
    names: PROPORTION,
    low: m.indices_end_gallagher_low,
    high: m.indices_end_gallagher_high,
  },
  loosemore: {
    from: 0,
    to: 45,
    cuts: [4, 8, 15, 25],
    names: PROPORTION,
    low: m.indices_end_gallagher_low,
    high: m.indices_end_gallagher_high,
  },
  volatility: {
    from: 0,
    to: 50,
    cuts: [5, 10, 17, 30],
    names: [
      m.indices_band_volatility_1,
      m.indices_band_volatility_2,
      m.indices_band_volatility_3,
      m.indices_band_volatility_4,
      m.indices_band_volatility_5,
    ],
    low: m.indices_end_volatility_low,
    high: m.indices_end_volatility_high,
  },
  nationalization: {
    from: 0.4,
    to: 1,
    cuts: [0.6, 0.75, 0.85],
    names: [
      m.indices_band_nationalization_1,
      m.indices_band_nationalization_2,
      m.indices_band_nationalization_3,
      m.indices_band_nationalization_4,
    ],
    low: m.indices_end_nationalization_low,
    high: m.indices_end_nationalization_high,
  },
};

export const scale = (figure: Figure) => {
  const { from, to, cuts, names, low, high } = SCALES[FAMILIES[figure]];
  const span = to - from;
  const at = (value: number) => Math.min(1, Math.max(0, (value - from) / span));
  const edges = [from, ...cuts, to];
  const bands = names.map((name, index) => ({
    name: name(),
    start: at(edges[index] ?? from),
    end: at(edges[index + 1] ?? to),
    tone: names.length === 1 ? 1 : index / (names.length - 1),
  }));
  const band = (value: number) => {
    let index = 0;
    for (const cut of cuts) {
      if (value >= cut) {
        index += 1;
      }
    }
    return index;
  };
  return {
    bands,
    ticks: cuts.map((cut) => ({
      at: at(cut),
      text: numbers({ maximumFractionDigits: 2 }).format(cut),
    })),
    low: low(),
    high: high(),
    at,
    band,
  };
};

export const pairReading = (votes: number, seats: number) =>
  seats < votes * 0.8 ? m.indices_pair_concentrated() : m.indices_pair_even();

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
