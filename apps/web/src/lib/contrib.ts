import type { Box } from "#lib/map/engine.svelte.js";
import { date } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";

export type ProposalState =
  | "draft"
  | "open"
  | "changes_requested"
  | "approved"
  | "published"
  | "rejected"
  | "withdrawn"
  | "reverted";

export const nameKinds = [
  "official",
  "native",
  "english",
  "historical",
  "transliteration",
  "abbreviation",
  "ballot",
  "short",
] as const;

export type NameKind = (typeof nameKinds)[number];

export type NoteKind = "context" | "caveat" | "methodology" | "editorial";

export const noteKinds = ["context", "caveat", "methodology"] as const;

export const noteKindLabel: Record<NoteKind, () => string> = {
  context: m.note_kind_context,
  caveat: m.note_kind_caveat,
  methodology: m.note_kind_methodology,
  editorial: m.note_kind_editorial,
};

export const noteLimit = 10_000;

export const schemes = ["wikidata", "bioguide", "mnis", "parlgov"] as const;

export type Scheme = (typeof schemes)[number];

export const schemePattern: Record<Scheme, RegExp> = {
  wikidata: /^Q[1-9]\d*$/,
  bioguide: /^[A-Z]\d{6}$/,
  mnis: /^[1-9]\d*$/,
  parlgov: /^[1-9]\d*$/,
};

export const schemeExample: Record<Scheme, string> = {
  wikidata: "Q42",
  bioguide: "S000148",
  mnis: "4514",
  parlgov: "1017",
};

export const schemeLabel: Record<Scheme, () => string> = {
  wikidata: m.id_scheme_wikidata,
  bioguide: m.id_scheme_bioguide,
  mnis: m.id_scheme_mnis,
  parlgov: m.id_scheme_parlgov,
};

export const personSchemes = ["wikidata", "bioguide", "mnis"] as const;

export const partySchemes = ["wikidata", "parlgov"] as const;

export const hex = /^#[0-9a-f]{6}$/iu;

export const isDay = (value: string) =>
  /^\d{4}-\d{2}-\d{2}$/u.test(value) &&
  !Number.isNaN(Date.parse(`${value}T00:00:00Z`)) &&
  new Date(`${value}T00:00:00Z`).toISOString().startsWith(value);

export const precisions = ["day", "month", "year", "unknown"] as const;

export const labelKinds = [
  "ballot",
  "integrity",
  "recognition",
  "data_quality",
] as const;

export type LabelKind = (typeof labelKinds)[number];

export const labelTypes: Record<LabelKind, Array<string> | null> = {
  ballot: ["contest"],
  integrity: ["election", "polity"],
  recognition: ["polity", "area"],
  data_quality: null,
};

export const ballotValues = ["no_candidate_qualified"] as const;

export const assessed = new Set<LabelKind>(["integrity", "recognition"]);

export const labelKindLabel: Record<LabelKind, () => string> = {
  ballot: m.label_kind_ballot,
  integrity: m.label_kind_integrity,
  recognition: m.label_kind_recognition,
  data_quality: m.label_kind_data_quality,
};

export const labelValue = (value: string) =>
  value === "no_candidate_qualified"
    ? m.label_value_no_candidate_qualified()
    : value;

export type Precision = (typeof precisions)[number];

export const dated = (value: string, precision: Precision) =>
  date(
    precision === "day"
      ? value
      : precision === "month"
        ? value.slice(0, 7)
        : value.slice(0, 4),
  );

export const period = (
  from: string | null,
  fromPrecision: Precision,
  to: string | null,
  toPrecision: Precision,
) =>
  from == null
    ? to == null
      ? m.edit_color_always()
      : m.edit_color_until({ date: dated(to, toPrecision) })
    : to == null
      ? m.edit_color_since({ date: dated(from, fromPrecision) })
      : m.edit_color_between({
          from: dated(from, fromPrecision),
          to: dated(to, toPrecision),
        });

export const schemeHref: Partial<Record<Scheme, (value: string) => string>> = {
  wikidata: (value) => `https://www.wikidata.org/wiki/${value}`,
  bioguide: (value) => `https://bioguide.congress.gov/search/bio/${value}`,
  mnis: (value) => `https://members.parliament.uk/member/${value}/career`,
};

export const languages = [
  "en",
  "fr",
  "de",
  "it",
  "es",
  "pt",
  "pt-BR",
  "tr",
  "nl",
  "ga",
  "rm",
  "cy",
  "gd",
  "ca",
  "eu",
  "gl",
  "bg",
  "cs",
  "da",
  "el",
  "et",
  "fi",
  "hr",
  "hu",
  "lt",
  "lv",
  "mt",
  "pl",
  "ro",
  "sk",
  "sl",
  "sv",
  "ku",
  "ar",
  "ru",
  "uk",
  "ja",
  "ko",
  "zh-Hans",
  "zh-Hant",
] as const;

export const graphemes = (text: string) =>
  [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text)]
    .length;

export const origins = ["human", "machine", "official"] as const;

export type Origin = (typeof origins)[number];

export const originLabel: Record<Origin, () => string> = {
  human: m.translation_origin_human,
  machine: m.translation_origin_machine,
  official: m.translation_origin_official,
};

export const translationLimit = (original: string) =>
  Math.max(noteLimit, 2 * graphemes(original));

export const md5Pattern = /^[0-9a-f]{32}$/u;

export type Prose = {
  body: string;
  lang: string;
  source_lang: string | null;
  origin: Origin | null;
  stale: boolean;
  alternate: string | null;
};

const commonsPattern =
  /^(?:https?:\/\/commons\.(?:m\.)?wikimedia\.org\/wiki\/)?(?:(?:File|Image):)?([^/#?|[\]{}<>]+\.(?:svg|png|jpe?g|gif|tiff?|webp))$/iu;

export const commonsName = (input: string) => {
  const found = commonsPattern.exec(input.normalize("NFC").trim());
  if (found?.[1] == null) {
    return null;
  }
  try {
    return decodeURIComponent(found[1]).replaceAll("_", " ").trim();
  } catch {
    return null;
  }
};

const freeCode = /^(?:pd|cc0|cc-by(?:-sa)?-[0-9.]+(?:-[a-z]+)?)$/u;
const freeName =
  /^(?:Public domain|CC0(?: 1\.0)?|CC BY(?:-SA)? [0-9.]+(?: [A-Za-z]+)?|OGL(?: v?[0-9.]+)?|Copyrighted free use(?:-link)?|No restrictions|Attribution|PDM-owner|Licence Ouverte)$/u;

export const isFree = (code: string, name: string) =>
  !/\bnc\b|-nc-|\bnd\b|-nd-/u.test(code) &&
  !/\bNC\b|\bND\b/u.test(name) &&
  (freeCode.test(code) || freeName.test(name));

export const stages = [
  "preliminary",
  "official",
  "certified",
  "court_revised",
  "recount",
  "alternative",
] as const;

export const stageLabel: Record<(typeof stages)[number], () => string> = {
  preliminary: m.stage_preliminary,
  official: m.stage_official,
  certified: m.stage_certified,
  court_revised: m.stage_court_revised,
  recount: m.stage_recount,
  alternative: m.stage_alternative,
};

export type Cell = {
  unit: string | { scheme: string; code: string };
  option: string | null;
  measure: string;
  channel: string;
  count_round: number;
  value: number;
};

export type Change =
  | { kind: "results"; stage: (typeof stages)[number]; rows: Array<Cell> }
  | { kind: "correct"; resultSet: string; cells: Array<Cell> }
  | { kind: "name"; id: string; text: string }
  | {
      kind: "note_add";
      noteKind: (typeof noteKinds)[number];
      lang: string;
      body: string;
    }
  | { kind: "note_set"; id: string; body: string }
  | { kind: "note_drop"; id: string }
  | {
      kind: "translate";
      note: string;
      md5: string;
      lang: string;
      origin: Origin;
      body: string;
    }
  | { kind: "image"; file: string; from: string | null; to: string | null }
  | { kind: "cite" }
  | { kind: "cite_drop"; id: string }
  | {
      kind: "label";
      label: LabelKind;
      value: string;
      assessor: string | null;
      from: string | null;
      to: string | null;
    }
  | { kind: "label_drop"; id: string }
  | { kind: "id_add"; scheme: Scheme; value: string }
  | { kind: "id_drop"; scheme: Scheme; value: string }
  | { kind: "color_set"; from: string | null; color: string }
  | { kind: "color_end"; from: string | null; to: string }
  | {
      kind: "color_add";
      color: string;
      from: string | null;
      to: string | null;
    };

export const ESCALATIONS = [
  "unchecked",
  "findings",
  "prescreen",
  "newcomer",
  "preferred_name",
  "large",
  "conflict",
] as const;

export type Escalation = (typeof ESCALATIONS)[number];

export const VERDICTS = [
  "clean",
  "spam",
  "abuse",
  "missing_evidence",
  "evidence_mismatch",
  "malformed",
  "duplicate",
] as const;

export type Prescreen = {
  revision: number;
  verdict: (typeof VERDICTS)[number];
  reason: string;
  model: string;
  batch: string;
  at: string;
};

export type Prereview = {
  revision: number;
  summary: string;
  facts: Array<{
    fact: string;
    status: "verified" | "unverified" | "contradicted";
    note?: string;
  }>;
  risks: Array<string>;
  recommendation: "approve" | "request_changes" | "reject";
  by: string;
  at: string;
};

export const escalationLabel: Record<Escalation, () => string> = {
  unchecked: m.escalation_unchecked,
  findings: m.escalation_findings,
  prescreen: m.escalation_prescreen,
  newcomer: m.escalation_newcomer,
  preferred_name: m.escalation_preferred_name,
  large: m.escalation_large,
  conflict: m.escalation_conflict,
};

export const verdictLabel: Record<Prescreen["verdict"], () => string> = {
  clean: m.prescreen_clean,
  spam: m.prescreen_spam,
  abuse: m.prescreen_abuse,
  missing_evidence: m.prescreen_missing_evidence,
  evidence_mismatch: m.prescreen_evidence_mismatch,
  malformed: m.prescreen_malformed,
  duplicate: m.prescreen_duplicate,
};

export const recommendationLabel: Record<
  Prereview["recommendation"],
  () => string
> = {
  approve: m.prereview_recommends_approve,
  request_changes: m.prereview_recommends_request_changes,
  reject: m.prereview_recommends_reject,
};

export const factLabel: Record<
  Prereview["facts"][number]["status"],
  () => string
> = {
  verified: m.prereview_verified,
  unverified: m.prereview_unverified,
  contradicted: m.prereview_contradicted,
};

export type DiffRow = {
  table: string;
  key: Record<string, string>;
  entity_id: string | null;
  op: "add" | "change" | "drop";
  fields: Record<string, [unknown, unknown]>;
  labels?: {
    unit: string;
    option: string | null;
    measure: string;
    channel: string;
  };
  locator?: {
    page_id: string;
    region: [number, number, number, number] | null;
  };
};

export type Polygonal =
  | { type: "Polygon"; coordinates: Array<Array<[number, number]>> }
  | { type: "MultiPolygon"; coordinates: Array<Array<Array<[number, number]>>> };

export function extent(
  geometry: Polygonal | null,
  box: Box | null = null,
): Box | null {
  if (geometry == null) {
    return box;
  }
  const rings =
    geometry.type === "Polygon"
      ? geometry.coordinates
      : geometry.coordinates.flat();
  let next = box;
  for (const ring of rings) {
    for (const [x, y] of ring) {
      next =
        next == null
          ? { west: x, south: y, east: x, north: y }
          : {
              west: Math.min(next.west, x),
              south: Math.min(next.south, y),
              east: Math.max(next.east, x),
              north: Math.max(next.north, y),
            };
    }
  }
  return next;
}

export type Drawn = {
  type: "FeatureCollection";
  tiles?: Array<string>;
  features: Array<{
    type: "Feature";
    properties: {
      kind: "after" | "before" | "moved" | "overlap" | "gap" | "parent";
      area: string | null;
      label: string | null;
      code: string | null;
      status: string | null;
    };
    geometry: Polygonal;
  }>;
};

export type Diff = {
  rows: Array<DiffRow>;
  truncated: boolean;
  entities: Array<string>;
  map?: Drawn;
  fold?: {
    sets: Array<{ result_set: string; pipeline: string }>;
    cells: number;
    checked?: string;
    published_by?: string;
    mismatch?: { changeset: string; cells: number };
  };
};

export type Checks = {
  diff?: Record<string, Record<string, number>>;
  findings?: Record<string, number>;
  examples?: Record<string, Array<{ details?: { pipeline?: string } }>>;
  unchanged?: boolean;
};

export type Editable = {
  names: Array<{
    id: string;
    text: string;
    kind: NameKind;
    lang: string | null;
    script: string | null;
    is_preferred: boolean;
    shown: boolean;
    source_id: string | null;
    source: string | null;
    changed: string | null;
  }>;
  notes: Array<{
    id: string;
    kind: NoteKind;
    body_md: string;
    lang: string;
    md5: string;
    changed: string | null;
    translations: Array<{
      lang: string;
      body: string;
      origin: Origin;
      stale: boolean;
    }>;
  }>;
  ids: Array<{ scheme: Scheme; value: string }>;
  kind: "party" | "person" | null;
  images: Array<{
    path: string;
    license: string | null;
    artist: string | null;
    source_url: string | null;
    valid_from: string | null;
    valid_from_precision: Precision;
    valid_to: string | null;
    valid_to_precision: Precision;
  }>;
  colors: Array<{
    color: string;
    valid_from: string | null;
    valid_from_precision: Precision;
    valid_to: string | null;
    valid_to_precision: Precision;
    source: string | null;
  }>;
  sources: Array<{ id: string; title: string; publisher: string | null }>;
  citations: Array<{
    id: string;
    title: string;
    publisher: string;
    url: string | null;
    own: Array<string>;
  }>;
  open: Array<{ number: number; title: string; state: ProposalState }>;
  labels: {
    held: Array<{
      id: string;
      kind: LabelKind;
      value: string;
      assessor: string | null;
      valid_from: string | null;
      valid_to: string | null;
      source: string | null;
    }>;
    kinds: Array<LabelKind>;
  };
  results: {
    options: Array<{
      id: string;
      label: string;
      kind: string;
      position: number | null;
    }>;
    sets: Array<{
      id: string;
      stage: string;
      status: string;
      is_default: boolean;
      pipeline: string;
      source: string | null;
      as_of: string | null;
      cells: number;
    }>;
  } | null;
};

export const stateLabel: Record<ProposalState, () => string> = {
  draft: m.proposal_state_draft,
  open: m.proposal_state_open,
  changes_requested: m.proposal_state_changes_requested,
  approved: m.proposal_state_approved,
  published: m.proposal_state_published,
  rejected: m.proposal_state_rejected,
  withdrawn: m.proposal_state_withdrawn,
  reverted: m.proposal_state_reverted,
};

const measures: Record<string, () => string> = {
  votes: m.measure_votes,
  registered: m.measure_registered,
  ballots_cast: m.measure_ballots_cast,
  valid: m.measure_valid,
  invalid: m.measure_invalid,
  blank: m.measure_blank,
  voters: m.measure_voters,
  present: m.measure_present,
  seats: m.measure_seats,
  list_votes: m.measure_list_votes,
  electoral_votes: m.measure_electoral_votes,
  state_delegate_equivalents: m.measure_state_delegate_equivalents,
  caucus_delegates: m.measure_caucus_delegates,
};

export const measureLabel = (code: string) =>
  measures[code]?.() ?? code.replaceAll("_", " ");

const channels: Record<string, () => string> = {
  election_day: m.channel_election_day,
  early: m.channel_early,
  postal: m.channel_postal,
  abroad: m.channel_abroad,
  provisional: m.channel_provisional,
  mobile: m.channel_mobile,
  prison: m.channel_prison,
  customs: m.channel_customs,
  special_rules: m.channel_special_rules,
};

export const channelLabel = (code: string) =>
  channels[code]?.() ?? code.replaceAll("_", " ");

export const tierLabel: Record<number, () => string> = {
  1: m.proposal_tier_1,
  2: m.proposal_tier_2,
  3: m.proposal_tier_3,
};

export const sourceKinds = [
  "official_body",
  "aggregator",
  "compendium",
  "news_agency",
  "academic",
  "community",
] as const;

export const sourceKindLabel: Record<
  (typeof sourceKinds)[number],
  () => string
> = {
  official_body: m.source_kind_official_body,
  aggregator: m.source_kind_aggregator,
  compendium: m.source_kind_compendium,
  news_agency: m.source_kind_news_agency,
  academic: m.source_kind_academic,
  community: m.source_kind_community,
};

export const kindLabel: Record<NameKind, () => string> = {
  official: m.name_kind_official,
  native: m.name_kind_native,
  english: m.name_kind_english,
  historical: m.name_kind_historical,
  transliteration: m.name_kind_transliteration,
  abbreviation: m.name_kind_abbreviation,
  ballot: m.name_kind_ballot,
  short: m.name_kind_short,
};

const checkLabel: Record<string, (pipelines: string) => string> = {
  "scope.owner": (pipelines) => m.proposal_check_scope_owner({ pipelines }),
  "scope.polity": m.proposal_check_scope_polity,
  "name.capitals": m.proposal_check_name_capitals,
  "translation.held": m.proposal_check_translation_held,
  "facts.immutable": m.proposal_check_facts_immutable,
  "reference.facts": m.proposal_check_reference_facts,
  "geometry.valid": m.proposal_check_geometry_valid,
  "geometry.overlap": m.proposal_check_geometry_overlap,
  "geometry.gap": m.proposal_check_geometry_gap,
  "geometry.parent": m.proposal_check_geometry_parent,
};

export const findingsOf = (checks: Checks | null | undefined) =>
  Object.entries(checks?.findings ?? {})
    .filter(([, value]) => value > 0)
    .map(([code, value]) => {
      const pipelines: Array<string> = [];
      const examples = checks?.examples?.[code] ?? [];
      for (const example of examples) {
        const pipeline = example.details?.pipeline;
        if (pipeline != null && !pipelines.includes(pipeline)) {
          pipelines.push(pipeline);
        }
      }
      return {
        code,
        value,
        label: checkLabel[code]?.(pipelines.join(", ")) ?? code,
      };
    });
