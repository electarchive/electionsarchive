import type { DiffRow, NameKind, Polygonal, Scheme } from "#lib/contrib.js";
import type { Box } from "#lib/map/engine.svelte.js";
import {
  hex,
  isDay,
  kindLabel,
  languages,
  nameKinds,
  partySchemes,
  period,
  personSchemes,
  schemeLabel,
  schemePattern,
  schemes,
} from "#lib/contrib.js";
import { m } from "#lib/paraglide/messages.js";
import { getLocale } from "#lib/paraglide/runtime.js";

export const tables = ["identifiers", "names", "colors"] as const;

export type Table = (typeof tables)[number];

export const templates = [
  "election",
  "results",
  "merge",
  "lineage",
  "translate",
  "boundaries",
  ...tables,
] as const;

export type Template = (typeof templates)[number];

export const electionKinds = [
  "general",
  "by_election",
  "primary",
  "runoff",
  "referendum",
  "recall",
  "local",
  "special",
] as const;

export const contestKinds = [
  "office",
  "body_seats",
  "list_allocation",
  "ballot_question",
] as const;

export type ContestKind = (typeof contestKinds)[number];

export const optionKinds = {
  office: ["candidate", "write_in", "others"],
  body_seats: ["candidate", "write_in", "others"],
  list_allocation: ["list", "others"],
  ballot_question: ["answer"],
} as const satisfies Record<ContestKind, ReadonlyArray<string>>;

export type OptionKind = (typeof optionKinds)[ContestKind][number];

export const questionTypes = [
  "initiative",
  "mandatory_referendum",
  "optional_referendum",
  "plebiscite",
  "recall_question",
] as const;

export const relationKinds = [
  "merged_into",
  "split_from",
  "successor_of",
  "refounded_as",
  "absorbed_by",
  "closed_by_court",
  "closed_by_government",
  "list_vehicle_for",
] as const;

export type RelationKind = (typeof relationKinds)[number];

export const closures: ReadonlyArray<RelationKind> = [
  "closed_by_court",
  "closed_by_government",
];

export const relationLabel: Record<RelationKind, () => string> = {
  merged_into: m.ws_rel_merged_into,
  split_from: m.ws_rel_split_from,
  successor_of: m.ws_rel_successor_of,
  refounded_as: m.ws_rel_refounded_as,
  absorbed_by: m.ws_rel_absorbed_by,
  closed_by_court: m.ws_rel_closed_by_court,
  closed_by_government: m.ws_rel_closed_by_government,
  list_vehicle_for: m.ws_rel_list_vehicle_for,
};

export const templateTitle: Record<Template, () => string> = {
  election: m.ws_template_election,
  results: m.ws_template_results,
  merge: m.ws_template_merge,
  lineage: m.ws_template_lineage,
  translate: m.ws_template_translate,
  boundaries: m.ws_template_boundaries,
  identifiers: m.ws_template_identifiers,
  names: m.ws_template_names,
  colors: m.ws_template_colors,
};

export const templateBody: Record<Template, () => string> = {
  election: m.ws_template_election_body,
  results: m.ws_template_results_body,
  merge: m.ws_template_merge_body,
  lineage: m.ws_template_lineage_body,
  translate: m.ws_template_translate_body,
  boundaries: m.ws_template_boundaries_body,
  identifiers: m.ws_template_identifiers_body,
  names: m.ws_template_names_body,
  colors: m.ws_template_colors_body,
};

export const qualities = ["official", "osm", "academic", "digitized"] as const;

export type Quality = (typeof qualities)[number];

export const qualityLabel: Record<Quality, () => string> = {
  official: m.ws_quality_official,
  osm: m.ws_quality_osm,
  academic: m.ws_quality_academic,
  digitized: m.ws_quality_digitized,
};

export type Schemed = { code: string; noun: string };

export type Shaped = {
  id: string;
  filename: string;
  count: number;
  bbox: [number, number, number, number] | null;
  fields: Array<{ name: string; samples: Array<string>; unique: boolean }>;
  map: {
    frame: Box;
    tiles: string | null;
    features: Array<{
      id: number;
      code: string | null;
      label: string | null;
      area: string | null;
      k: number | null;
      name: string | null;
      geometry: Polygonal | null;
    }>;
  } | null;
};

export const columns = {
  identifiers: ["scheme", "value"],
  names: ["text", "lang", "kind"],
  colors: ["color", "from", "to"],
} as const;

export type Column = "record" | (typeof columns)[Table][number];

export type Cells = Partial<Record<Column, string>>;

export const needsSource: Record<Table, boolean> = {
  identifiers: false,
  names: true,
  colors: true,
};

export type Found = {
  id: string;
  type: string;
  name: string;
  ids: Array<{ scheme: Scheme; value: string }>;
  names: Array<{ text: string; lang: string; kind: NameKind }>;
  colors: Array<{
    color: string;
    valid_from: string | null;
    valid_to: string | null;
  }>;
  from: string | null;
  to: string | null;
  polity: string | null;
};

export type Lookup =
  | ({ state: "found" } & Found)
  | { state: "missing" }
  | { state: "retired" };

export const isRef = (value: string) =>
  value.includes("/") ||
  /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/iu.test(
    value.trim(),
  );

export const blank = (template: Table) =>
  Object.fromEntries(columns[template].map((column) => [column, ""]));

const text = (cells: Cells, column: Column) => (cells[column] ?? "").trim();

const schemeOf = (cells: Cells) =>
  schemes.find((scheme) => scheme === text(cells, "scheme"));

const langOf = (cells: Cells) =>
  languages.find((tag) => tag === text(cells, "lang"));

const kindOf = (cells: Cells) =>
  nameKinds.find((kind) => kind === text(cells, "kind"));

const colorOf = (cells: Cells) => {
  const value = text(cells, "color").toLowerCase();
  return value === "" || value.startsWith("#") ? value : `#${value}`;
};

const languageNames = () =>
  new Intl.DisplayNames([getLocale()], { type: "language" });

export const languageLabel = (tag: string) => languageNames().of(tag) ?? tag;

export const languageItems = () =>
  languages.map((tag) => ({
    value: tag,
    label: languageLabel(tag),
    detail: tag,
  }));

const personFit: ReadonlyArray<Scheme> = personSchemes;
const partyFit: ReadonlyArray<Scheme> = partySchemes;

const isFit = (type: string, scheme: Scheme) =>
  type === "person"
    ? personFit.includes(scheme)
    : type === "party"
      ? partyFit.includes(scheme)
      : scheme === "wikidata";

const kinds: Record<string, () => string> = {
  person: m.ws_kind_person,
  party: m.ws_kind_party,
  alliance: m.ws_kind_alliance,
  election: m.ws_kind_election,
  contest: m.ws_kind_contest,
  polity: m.ws_kind_polity,
  area: m.ws_kind_area,
  office: m.ws_kind_office,
  body: m.ws_kind_body,
};

export const recordKind = (type: string) => (kinds[type] ?? m.ws_kind_other)();

export const isBlank = (cells: Cells) =>
  Object.values(cells).every((value) => value.trim() === "");

type Judged = { problem: string; field: Column } | null;

function judgeIdentifier(cells: Cells, entity: Found | null): Judged {
  const scheme = schemeOf(cells);
  if (scheme == null) {
    return null;
  }
  const value = text(cells, "value");
  if (value !== "" && !schemePattern[scheme].test(value)) {
    return {
      problem: m.ws_value_invalid({ scheme: schemeLabel[scheme]() }),
      field: "value",
    };
  }
  if (entity != null && !isFit(entity.type, scheme)) {
    return {
      problem: m.ws_scheme_unfit({ scheme: schemeLabel[scheme]() }),
      field: "scheme",
    };
  }
  const held = entity?.ids.find((id) => id.scheme === scheme);
  return held == null
    ? null
    : {
        problem: m.ws_value_held({
          scheme: schemeLabel[scheme](),
          value: held.value,
        }),
        field: "value",
      };
}

function judgeName(cells: Cells, entity: Found | null): Judged {
  if (text(cells, "lang") !== "" && langOf(cells) == null) {
    return { problem: m.ws_lang_unknown(), field: "lang" };
  }
  if (text(cells, "kind") !== "" && kindOf(cells) == null) {
    return { problem: m.ws_kind_unknown(), field: "kind" };
  }
  const isHeld =
    entity?.names.some(
      (held) =>
        held.text === text(cells, "text") &&
        held.lang === text(cells, "lang") &&
        held.kind === text(cells, "kind"),
    ) === true;
  return isHeld ? { problem: m.ws_name_held(), field: "text" } : null;
}

function judgeColor(cells: Cells, entity: Found | null): Judged {
  if (entity != null && entity.type !== "party") {
    return { problem: m.ws_record_not_party(), field: "record" };
  }
  if (colorOf(cells) !== "" && !hex.test(colorOf(cells))) {
    return { problem: m.edit_color_invalid(), field: "color" };
  }
  const from = text(cells, "from");
  if (from !== "" && !isDay(from)) {
    return { problem: m.edit_date_invalid(), field: "from" };
  }
  const to = text(cells, "to");
  if (to !== "" && !isDay(to)) {
    return { problem: m.edit_date_invalid(), field: "to" };
  }
  return from !== "" && to !== "" && to <= from
    ? { problem: m.edit_color_order(), field: "to" }
    : null;
}

export const judge = (
  template: Table,
  cells: Cells,
  entity: Found | null,
): Judged =>
  template === "identifiers"
    ? judgeIdentifier(cells, entity)
    : template === "names"
      ? judgeName(cells, entity)
      : judgeColor(cells, entity);

export const isComplete = (template: Table, cells: Cells) =>
  template === "identifiers"
    ? schemeOf(cells) != null && text(cells, "value") !== ""
    : template === "names"
      ? text(cells, "text") !== "" &&
        langOf(cells) != null &&
        kindOf(cells) != null
      : colorOf(cells) !== "";

export function repeats(
  template: Table,
  cells: Cells,
  entity: string,
): { key: string; problem: (row: number) => string; field: Column } {
  if (template === "identifiers") {
    return {
      key: `${text(cells, "scheme")}:${text(cells, "value")}`,
      problem: (row) => m.ws_row_duplicate({ row }),
      field: "value",
    };
  }
  if (template === "names") {
    return {
      key: [
        entity,
        text(cells, "text"),
        text(cells, "lang"),
        text(cells, "kind"),
      ].join("\n"),
      problem: (row) => m.ws_row_duplicate({ row }),
      field: "text",
    };
  }
  return {
    key: entity,
    problem: (row) => m.ws_party_repeated({ row }),
    field: "record",
  };
}

export function op(template: Table, cells: Cells, entity: string) {
  if (template === "identifiers") {
    return {
      op: "add",
      table: "external_id",
      values: {
        entity_id: entity,
        scheme: text(cells, "scheme"),
        value: text(cells, "value"),
      },
    };
  }
  if (template === "names") {
    return {
      op: "add",
      table: "name",
      values: {
        entity_id: entity,
        text: text(cells, "text").normalize("NFC"),
        lang: text(cells, "lang"),
        kind: text(cells, "kind"),
      },
    };
  }
  const from = text(cells, "from");
  const to = text(cells, "to");
  return {
    op: "add",
    table: "party_color",
    values: {
      party_id: entity,
      color: colorOf(cells),
      ...(from !== "" && { valid_from: from }),
      ...(to !== "" && { valid_to: to }),
    },
  };
}

const guess = (value: string) =>
  /^Q[1-9]\d*$/u.test(value)
    ? "wikidata"
    : /^[A-Z]\d{6}$/u.test(value)
      ? "bioguide"
      : "";

const matching = <T extends string>(
  list: ReadonlyArray<T>,
  input: string,
  label: (item: T) => string,
) => {
  const lower = input.trim().toLowerCase();
  return (
    list.find(
      (item) =>
        item.toLowerCase() === lower || label(item).toLowerCase() === lower,
    ) ?? input.trim()
  );
};

export function parse(template: Table, parts: Array<string>): Cells {
  const [record = "", first = "", second = "", third = ""] = parts;
  if (template === "identifiers") {
    const value = parts.length >= 3 ? second : first;
    const scheme =
      parts.length >= 3
        ? matching(schemes, first, (item) => schemeLabel[item]())
        : guess(value);
    return {
      record,
      scheme: schemes.find((item) => item === scheme) ?? guess(value),
      value,
    };
  }
  if (template === "names") {
    return {
      record,
      text: first,
      lang: matching(languages, second, languageLabel),
      kind: matching(nameKinds, third, (item) => kindLabel[item]()),
    };
  }
  return { record, color: first, from: second, to: third };
}

export function summarize(template: Table, cells: Cells) {
  if (template === "identifiers") {
    const scheme = schemeOf(cells);
    return `${scheme == null ? "" : schemeLabel[scheme]()} ${text(cells, "value")}`;
  }
  if (template === "names") {
    const kind = kindOf(cells);
    return [
      text(cells, "text"),
      langOf(cells) == null ? null : languageLabel(text(cells, "lang")),
      kind == null ? null : kindLabel[kind](),
    ]
      .filter((part) => part != null)
      .join(" · ");
  }
  const from = text(cells, "from");
  const to = text(cells, "to");
  return `${colorOf(cells)} · ${period(from === "" ? null : from, "day", to === "" ? null : to, "day")}`;
}

export function holdings(template: Table, entity: Found) {
  if (template === "identifiers") {
    return entity.ids.map(
      (held) => `${schemeLabel[held.scheme]()} ${held.value}`,
    );
  }
  if (template === "names") {
    return [
      ...new Set(entity.names.map((held) => `${held.text} (${held.lang})`)),
    ];
  }
  return entity.colors.map(
    (held) =>
      `${period(held.valid_from, "day", held.valid_to, "day")}: ${held.color}`,
  );
}

export const swatch = (template: Table, cells: Cells) =>
  template === "colors" && hex.test(colorOf(cells)) ? colorOf(cells) : null;

const sideOf = (row: DiffRow, column: string) => {
  const value = row.fields[column]?.[row.op === "drop" ? 0 : 1];
  return typeof value === "string" ? value : null;
};

function describedColor(row: DiffRow) {
  const color = sideOf(row, "color");
  const from = row.key.valid_from ?? null;
  const until = period(from, "day", sideOf(row, "valid_to"), "day");
  if (row.op !== "change") {
    return {
      text: `${color ?? ""} · ${until}`,
      color: color != null && hex.test(color) ? color : null,
    };
  }
  const recolored = row.fields.color?.map((value) =>
    typeof value === "string" ? value : "",
  );
  return {
    text: [recolored?.join(" → "), row.fields.valid_to == null ? null : until]
      .filter((part) => part != null)
      .join(" · "),
    color: color != null && hex.test(color) ? color : null,
  };
}

export function described(row: DiffRow) {
  if (row.table === "external_id") {
    const scheme = schemes.find((item) => item === row.key.scheme);
    return {
      text: `${scheme == null ? (row.key.scheme ?? "") : schemeLabel[scheme]()} ${row.key.value ?? ""}`,
      color: null,
    };
  }
  if (row.table === "name") {
    const kind = nameKinds.find((item) => item === sideOf(row, "kind"));
    const lang = sideOf(row, "lang");
    return {
      text: [
        sideOf(row, "text"),
        lang == null ? null : languageLabel(lang),
        kind == null ? null : kindLabel[kind](),
      ]
        .filter((part) => part != null)
        .join(" · "),
      color: null,
    };
  }
  return describedColor(row);
}
