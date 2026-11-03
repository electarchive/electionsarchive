import { m } from "#lib/paraglide/messages.js";

export const CATEGORIES = [
  "authorities",
  "local",
  "legislatures",
  "boundaries",
  "records",
  "research",
  "reference",
  "print",
  "news",
  "parties",
  "live",
] as const;

export const SUPPLIES = [
  "results",
  "candidates",
  "boundaries",
  "live",
  "people",
  "parties",
  "reference",
] as const;

export type Category = (typeof CATEGORIES)[number];
export type Supply = (typeof SUPPLIES)[number];

export const categoryTitles: Record<Category, () => string> = {
  authorities: m.sources_page_category_authorities,
  local: m.sources_page_category_local,
  legislatures: m.sources_page_category_legislatures,
  boundaries: m.sources_page_category_boundaries,
  records: m.sources_page_category_records,
  research: m.sources_page_category_research,
  reference: m.sources_page_category_reference,
  print: m.sources_page_category_print,
  news: m.sources_page_category_news,
  parties: m.sources_page_category_parties,
  live: m.sources_page_category_live,
};

export const categoryLedes: Record<Category, () => string> = {
  authorities: m.sources_page_category_authorities_lede,
  local: m.sources_page_category_local_lede,
  legislatures: m.sources_page_category_legislatures_lede,
  boundaries: m.sources_page_category_boundaries_lede,
  records: m.sources_page_category_records_lede,
  research: m.sources_page_category_research_lede,
  reference: m.sources_page_category_reference_lede,
  print: m.sources_page_category_print_lede,
  news: m.sources_page_category_news_lede,
  parties: m.sources_page_category_parties_lede,
  live: m.sources_page_category_live_lede,
};

export const supplyLabels: Record<Supply, () => string> = {
  results: m.sources_page_supplies_results,
  candidates: m.sources_page_supplies_candidates,
  boundaries: m.sources_page_supplies_boundaries,
  live: m.sources_page_supplies_live,
  people: m.sources_page_supplies_people,
  parties: m.sources_page_supplies_parties,
  reference: m.sources_page_supplies_reference,
};

export type Held = {
  sets: number;
  contests: number;
  shapes: number;
  first: number | null;
  last: number | null;
};

export function figures(held: Held) {
  return [
    held.sets > 0 ? m.sources_page_sets({ count: held.sets }) : null,
    held.contests > 0
      ? m.sources_page_contests({ count: held.contests })
      : null,
    held.shapes > 0 ? m.sources_page_shapes({ count: held.shapes }) : null,
  ].filter((figure) => figure != null);
}

export function leadFigure(held: Pick<Held, "contests" | "shapes">) {
  if (held.contests > 0) {
    return m.sources_page_contests({ count: held.contests });
  }
  return held.shapes > 0 ? m.sources_page_shapes({ count: held.shapes }) : null;
}

export function years(held: Held) {
  if (held.first == null || held.last == null) {
    return null;
  }
  return held.first === held.last
    ? String(held.first)
    : `${held.first}–${held.last}`;
}
