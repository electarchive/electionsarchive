import type { Release, ReleaseFile } from "#lib/server/downloads.js";
import { numbers } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";
import { getLocale } from "#lib/paraglide/runtime.js";
import { REPOSITORY } from "#lib/site.js";

export const SHARED_FILES = [
  "electionsarchive.sql",
  "SHA256SUMS",
  "manifest.json",
  "ATTRIBUTION.md",
] as const;

const SHARED_LABELS: Record<(typeof SHARED_FILES)[number], () => string> = {
  "electionsarchive.sql": m.downloads_shared_sql,
  SHA256SUMS: m.downloads_shared_sums,
  "manifest.json": m.downloads_shared_manifest,
  "ATTRIBUTION.md": m.downloads_shared_attribution,
};

export const sharedLabel = (file: (typeof SHARED_FILES)[number]) =>
  SHARED_LABELS[file]();

export const quickstart = (calver: string) => [
  {
    text: m.downloads_step_download(),
    code: `gh release download data-${calver} -R electarchive/electionsarchive -p '*.parquet' -p electionsarchive.sql -p SHA256SUMS`,
  },
  {
    text: m.downloads_step_verify(),
    code: "sha256sum --ignore-missing -c SHA256SUMS",
  },
  {
    text: m.downloads_step_open(),
    code: 'duckdb -cmd ".read electionsarchive.sql"',
  },
];

export const BUNDLE_ORDER = ["cc-by", "cc-by-sa", "odbl"];

export type Format = "parquet" | "geoparquet" | "csv";

export const FORMATS: ReadonlyArray<Format> = ["parquet", "geoparquet", "csv"];

export const MEDIA: Record<Format, string | Array<string>> = {
  parquet: "application/vnd.apache.parquet",
  geoparquet: "application/vnd.apache.parquet",
  csv: ["text/csv", "application/gzip"],
};

export const tag = (calver: string) =>
  `${REPOSITORY}/releases/tag/data-${calver}`;

export const fileUrl = (origin: string, calver: string, file: string) =>
  `${origin}/downloads/${calver}/${file}`;

export const latestUrl = (origin: string, file: string) =>
  `${origin}/downloads/latest/${file}`;

export const githubUrl = (calver: string, file: string) =>
  `${REPOSITORY}/releases/download/data-${calver}/${file}`;

export function formatOf(file: ReleaseFile): Format {
  if (file.file.endsWith(".csv.gz")) {
    return "csv";
  }
  return file.table === "geometry" ? "geoparquet" : "parquet";
}

const UNITS = [
  [1e12, "terabyte"],
  [1e9, "gigabyte"],
  [1e6, "megabyte"],
  [1e3, "kilobyte"],
] as const;

export function bytes(value: number) {
  for (const [size, unit] of UNITS) {
    if (value >= size) {
      return numbers({
        style: "unit",
        unit,
        unitDisplay: "short",
        maximumFractionDigits: value / size >= 100 ? 0 : 1,
      }).format(value / size);
    }
  }
  return numbers({ style: "unit", unit: "byte", unitDisplay: "short" }).format(
    value,
  );
}

export function compact(value: number) {
  return numbers({
    notation: "compact",
    maximumFractionDigits: value >= 1e9 ? 2 : 1,
  }).format(value);
}

export type FileGroup = {
  table: string;
  rows: number;
  bytes: number;
  files: number;
  items: Array<{
    key: string;
    bundle: string;
    part: string;
    rows: number;
    parquet: Array<ReleaseFile>;
    csv: ReleaseFile | null;
  }>;
};

export function groupFiles(files: Array<ReleaseFile>) {
  const tables = new Map<string, FileGroup>();
  const csvs = new Set(
    files
      .filter((file) => formatOf(file) === "csv")
      .map((file) => `${file.bundle}|${file.table}|${file.part}`),
  );
  for (const file of files) {
    const format = formatOf(file);
    const suffix = /(?:^|_)\d+$/u.exec(file.part);
    const trimmed =
      suffix == null ? file.part : file.part.slice(0, suffix.index);
    const part =
      format !== "csv" &&
      trimmed !== file.part &&
      csvs.has(`${file.bundle}|${file.table}|${trimmed}`)
        ? trimmed
        : file.part;
    const key = `${file.bundle}|${part}`;
    let group = tables.get(file.table);
    if (group == null) {
      group = { table: file.table, rows: 0, bytes: 0, files: 0, items: [] };
      tables.set(file.table, group);
    }
    group.bytes += file.bytes;
    group.files += 1;
    let item = group.items.find((entry) => entry.key === key);
    if (item == null) {
      item = {
        key,
        bundle: file.bundle,
        part,
        rows: 0,
        parquet: [],
        csv: null,
      };
      group.items.push(item);
    }
    if (format === "csv") {
      item.csv = file;
      item.rows = file.rows;
    } else {
      item.parquet.push(file);
      if (!csvs.has(`${file.bundle}|${file.table}|${part}`)) {
        item.rows += file.rows;
      }
    }
  }
  const groups = [...tables.values()];
  for (const group of groups) {
    for (const item of group.items) {
      if (item.csv == null && item.rows === 0) {
        for (const file of item.parquet) {
          item.rows += file.rows;
        }
      }
      group.rows += item.rows;
    }
    group.items.sort(
      (a, b) =>
        BUNDLE_ORDER.indexOf(a.bundle) - BUNDLE_ORDER.indexOf(b.bundle) ||
        a.part.localeCompare(b.part, undefined, { numeric: true }),
    );
  }
  const first = ["results", "geometry", "result_set", "outcome"];
  return groups.toSorted((a, b) => {
    const left = first.indexOf(a.table);
    const right = first.indexOf(b.table);
    if (left !== -1 || right !== -1) {
      return (
        (left === -1 ? first.length : left) -
        (right === -1 ? first.length : right)
      );
    }
    return a.table.localeCompare(b.table);
  });
}

export function sizes(files: Array<ReleaseFile>) {
  const cells = new Map<string, { bytes: number; files: number }>();
  const add = (key: string, file: ReleaseFile) => {
    const cell = cells.get(key) ?? { bytes: 0, files: 0 };
    cell.bytes += file.bytes;
    cell.files += 1;
    cells.set(key, cell);
  };
  const bundles = new Set<string>();
  for (const file of files) {
    const format = formatOf(file);
    bundles.add(file.bundle);
    add(`${file.bundle}|${format}`, file);
    add(`${file.bundle}|all`, file);
    add(`all|${format}`, file);
    add("all|all", file);
  }
  return {
    bundles: [...bundles].toSorted(
      (a, b) => BUNDLE_ORDER.indexOf(a) - BUNDLE_ORDER.indexOf(b),
    ),
    cell: (bundle: string, format: Format | "all") =>
      cells.get(`${bundle}|${format}`) ?? null,
  };
}

let regions: Intl.DisplayNames | null = null;
let regionsLocale = "";

export function countryName(code: string | null, fallback?: string) {
  if (code == null || code === "" || code === "WORLD") {
    return m.downloads_worldwide();
  }
  const locale = getLocale();
  if (regions == null || regionsLocale !== locale) {
    regions = new Intl.DisplayNames([locale], {
      type: "region",
      fallback: "none",
    });
    regionsLocale = locale;
  }
  try {
    return regions.of(code.toUpperCase()) ?? fallback ?? code;
  } catch {
    return fallback ?? code;
  }
}

const LEVELS: Record<string, () => string> = {
  sovereign_state: m.downloads_level_sovereign_state,
  supranational: m.downloads_level_supranational,
  federal_unit: m.downloads_level_federal_unit,
  region: m.downloads_level_region,
  county: m.downloads_level_county,
  municipality: m.downloads_level_municipality,
  submunicipal: m.downloads_level_submunicipal,
  historical_state: m.downloads_level_historical_state,
  school_district: m.downloads_level_school_district,
  judicial_district: m.downloads_level_judicial_district,
  special_district: m.downloads_level_special_district,
  tribal_nation: m.downloads_level_tribal_nation,
};

const ELECTIONS: Record<string, () => string> = {
  general: m.ws_ekind_general,
  by_election: m.ws_ekind_by_election,
  primary: m.ws_ekind_primary,
  runoff: m.ws_ekind_runoff,
  referendum: m.ws_ekind_referendum,
  recall: m.ws_ekind_recall,
  local: m.ws_ekind_local,
  special: m.ws_ekind_special,
  caucus: m.downloads_ekind_caucus,
  indirect: m.downloads_ekind_indirect,
};

const CONTESTS: Record<string, () => string> = {
  office: m.ws_ckind_office,
  body_seats: m.ws_ckind_body_seats,
  list_allocation: m.ws_ckind_list_allocation,
  ballot_question: m.ws_ckind_ballot_question,
  elector_slate: m.downloads_ckind_elector_slate,
  delegate_allocation: m.downloads_ckind_delegate_allocation,
};

const QUESTIONS: Record<string, () => string> = {
  initiative: m.ws_qtype_initiative,
  mandatory_referendum: m.ws_qtype_mandatory_referendum,
  optional_referendum: m.ws_qtype_optional_referendum,
  plebiscite: m.ws_qtype_plebiscite,
  recall_question: m.ws_qtype_recall_question,
  referendum: m.ws_qtype_referendum,
  counter_proposal: m.downloads_question_counter_proposal,
  tie_break: m.downloads_question_tie_break,
};

const STAGES: Record<string, () => string> = {
  live: m.stage_live,
  preliminary: m.stage_preliminary,
  official: m.stage_official,
  certified: m.stage_certified,
  court_revised: m.stage_court_revised,
  recount: m.stage_recount,
  alternative: m.stage_alternative,
  notional: m.downloads_stage_notional,
};

const FORMULAS: Record<string, () => string> = {
  fptp: m.downloads_formula_fptp,
  two_round: m.downloads_formula_two_round,
  repeated_ballot: m.downloads_formula_repeated_ballot,
  block: m.downloads_formula_block,
  limited: m.downloads_formula_limited,
  cumulative: m.downloads_formula_cumulative,
  sntv: m.downloads_formula_sntv,
  stv: m.downloads_formula_stv,
  irv: m.downloads_formula_irv,
  supplementary: m.downloads_formula_supplementary,
  top_two: m.downloads_formula_top_two,
  dhondt: m.downloads_formula_dhondt,
  sainte_lague: m.downloads_formula_sainte_lague,
  modified_sainte_lague: m.downloads_formula_modified_sainte_lague,
  sainte_lague_schepers: m.downloads_formula_sainte_lague_schepers,
  hare: m.downloads_formula_hare,
  hagenbach_bischoff: m.downloads_formula_hagenbach_bischoff,
  hagenbach_bischoff_quota: m.downloads_formula_hagenbach_bischoff_quota,
  imperiali_quota: m.downloads_formula_imperiali_quota,
  winner_take_all: m.downloads_formula_winner_take_all,
  contingent: m.downloads_formula_contingent,
  caucus: m.downloads_formula_caucus,
  referendum: m.downloads_formula_referendum,
};

const KINDS: Record<string, () => string> = {
  import: m.downloads_change_import,
  correction: m.downloads_change_correction,
  match: m.downloads_change_match,
  edit: m.downloads_change_edit,
  release: m.downloads_change_release,
};

const TOTALS: Record<string, () => string> = {
  results: m.downloads_total_results,
  result_sets: m.downloads_total_result_sets,
  elections: m.downloads_total_elections,
  contests: m.downloads_total_contests,
  candidacies: m.downloads_total_candidacies,
  people: m.downloads_total_people,
  parties: m.downloads_total_parties,
  tenures: m.downloads_total_tenures,
  polities: m.downloads_total_polities,
  reporting_units: m.downloads_total_reporting_units,
  shapes: m.downloads_total_shapes,
  sources: m.downloads_total_sources,
  changesets: m.downloads_total_changesets,
};

export const TOTAL_ORDER = Object.keys(TOTALS);

const plain = (code: string) => code.replaceAll("_", " ");

export const levelLabel = (code: string) => LEVELS[code]?.() ?? plain(code);
export const electionLabel = (code: string) =>
  ELECTIONS[code]?.() ?? plain(code);
export const contestLabel = (code: string) => CONTESTS[code]?.() ?? plain(code);
export const questionLabel = (code: string) =>
  QUESTIONS[code]?.() ?? plain(code);
export const stageLabel = (code: string) => STAGES[code]?.() ?? plain(code);
export const formulaLabel = (code: string) => FORMULAS[code]?.() ?? plain(code);
export const changeLabel = (code: string) => KINDS[code]?.() ?? plain(code);
export const totalLabel = (code: string) => TOTALS[code]?.() ?? plain(code);

export function span(
  first: number | null | undefined,
  last: number | null | undefined,
) {
  if (first == null && last == null) {
    return "";
  }
  if (first == null || last == null || first === last) {
    return String(first ?? last);
  }
  return `${first}–${last}`;
}

export function dataset({
  origin,
  url,
  release,
  isSeries,
}: {
  origin: string;
  url: string;
  release: Release;
  isSeries: boolean;
}) {
  const summary = release.summary;
  const countries = summary?.countries ?? [];
  const first = summary?.span.first ?? null;
  const last = summary?.span.last ?? null;
  const description = describe(release, "en");
  const licenses = BUNDLE_ORDER.flatMap((bundle) => {
    const found = release.bundles[bundle];
    return found == null ? [] : [found.license_url];
  });
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Dataset",
        "@id": `${url}#dataset`,
        name: isSeries
          ? m.downloads_dataset_name()
          : m.downloads_dataset_version_name({ version: release.calver }),
        alternateName: m.wordmark(),
        description,
        url,
        sameAs: tag(release.calver),
        version: release.calver,
        datePublished: release.created_at,
        dateModified: release.upto,
        inLanguage: "en",
        license:
          licenses.length > 1
            ? licenses
            : (licenses[0] ?? "https://creativecommons.org/licenses/by/4.0/"),
        isAccessibleForFree: true,
        ...(release.doi != null && {
          identifier: `https://doi.org/${release.doi}`,
        }),
        keywords: [
          "elections",
          "election results",
          "voting",
          "referendums",
          "political parties",
          "candidates",
          "electoral geography",
          "precinct results",
          "political science",
        ],
        creator: { "@id": `${origin}/#organization` },
        publisher: {
          "@type": "Organization",
          "@id": `${origin}/#organization`,
          name: m.wordmark(),
          url: `${origin}/`,
          logo: {
            "@type": "ImageObject",
            url: `${origin}/apple-touch-icon.png`,
          },
        },
        ...(first != null &&
          last != null && { temporalCoverage: `${first}/${last}` }),
        ...(countries.length > 0 && {
          spatialCoverage: countries.map((country) => ({
            "@type": "Place",
            name: countryName(country.code, country.name),
            ...(country.code.length === 2 && { identifier: country.code }),
          })),
        }),
        variableMeasured: [
          "votes",
          "registered voters",
          "ballots cast",
          "valid votes",
          "invalid votes",
          "seats won",
          "turnout",
          "candidates",
          "parties",
          "reporting-unit boundaries",
        ].map((name) => ({ "@type": "PropertyValue", name })),
        distribution: release.files.map((file) => {
          const format = formatOf(file);
          return {
            "@type": "DataDownload",
            name: file.file,
            contentUrl: fileUrl(origin, release.calver, file.file),
            encodingFormat: MEDIA[format],
            ...(format === "geoparquet" && {
              description: m.downloads_geoparquet(),
            }),
            contentSize: String(file.bytes),
            license: release.bundles[file.bundle]?.license_url,
          };
        }),
        includedInDataCatalog: {
          "@type": "DataCatalog",
          name: m.wordmark(),
          url: `${origin}/downloads`,
        },
        ...(!isSeries && {
          isPartOf: { "@id": `${origin}/downloads#dataset` },
        }),
      },
    ],
  };
}

export function describe(release: Release, locale?: string) {
  const totals = release.summary?.totals ?? {};
  const countries = (release.summary?.countries ?? []).filter(
    (country) => country.code !== "EU",
  );
  const results = totals.results;
  const elections = totals.elections;
  const first = release.summary?.span.first?.slice(0, 4);
  const last = release.summary?.span.last?.slice(0, 4);
  if (
    results == null ||
    elections == null ||
    first == null ||
    last == null ||
    countries.length === 0
  ) {
    return m.downloads_description_short({
      version: release.calver,
      size: bytes(release.bytes),
    });
  }
  const format = (value: number) =>
    new Intl.NumberFormat(locale ?? getLocale()).format(value);
  return m.downloads_description({
    results: format(results),
    elections: format(elections),
    countries: format(countries.length),
    first,
    last,
    version: release.calver,
  });
}
