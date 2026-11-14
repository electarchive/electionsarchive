import type { Change, Release, ReleaseRow } from "#lib/server/downloads.js";
import {
  bytes,
  changeLabel,
  contestLabel,
  countryName,
  describe,
  electionLabel,
  fileUrl,
  formulaLabel,
  groupFiles,
  latestUrl,
  levelLabel,
  questionLabel,
  quickstart,
  SHARED_FILES,
  sharedLabel,
  span,
  stageLabel,
  tag,
  TOTAL_ORDER,
  totalLabel,
} from "#lib/downloads.js";
import { m } from "#lib/paraglide/messages.js";
import { cite } from "#lib/site.js";

const inline = (value: string) =>
  value.replaceAll(/[\\`*_[\]<>|]/gu, (character) => `\\${character}`);

const number = (value: number) => new Intl.NumberFormat("en").format(value);

const year = (value: number | null | undefined) =>
  value == null ? "" : String(value);

function table(head: Array<string>, rows: Array<Array<string>>) {
  return [
    `| ${head.join(" | ")} |`,
    `| ${head.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${row.join(" | ")} |`),
  ];
}

export function releaseMarkdown({
  origin,
  all,
  release,
  changes,
  isIndex,
}: {
  origin: string;
  all: Array<ReleaseRow>;
  release: Release | null;
  changes: Array<Change>;
  isIndex: boolean;
}) {
  const lines: Array<string> = [];
  if (release == null) {
    lines.push(
      `# ${m.downloads_title()}`,
      "",
      m.downloads_lede(),
      "",
      m.downloads_empty(),
    );
    return `${lines.join("\n")}\n`;
  }
  const summary = release.summary;
  lines.push(
    `# ${isIndex ? m.downloads_title() : m.downloads_release_title({ version: release.calver })}`,
    "",
    describe(release, "en"),
    "",
    `- ${m.downloads_version()}: ${release.calver} ([${m.downloads_github()}](${tag(release.calver)}))`,
    `- ${m.downloads_published_label()}: ${release.created_at.slice(0, 10)}`,
    `- ${m.downloads_as_of_label()}: ${release.upto.slice(0, 16).replace("T", " ")} UTC`,
    `- ${m.downloads_size_label()}: ${bytes(release.bytes)}, ${m.downloads_file_count({ count: release.files.length })}`,
  );
  if (release.doi != null) {
    lines.push(`- DOI: [${release.doi}](https://doi.org/${release.doi})`);
  }
  if (!release.isLatest) {
    lines.push(
      "",
      m.downloads_not_latest({ version: release.latest }),
      `${origin}/downloads/${release.latest}.md`,
    );
  }
  if (summary != null) {
    lines.push("", `## ${m.downloads_contents()}`, "");
    const previous = release.previous?.totals;
    lines.push(
      ...table(
        [
          m.downloads_col_record(),
          m.downloads_col_count(),
          ...(previous == null
            ? []
            : [
                m.downloads_delta_head({
                  version: release.previous?.calver ?? "",
                }),
              ]),
        ],
        TOTAL_ORDER.flatMap((key) => {
          const value = summary.totals[key];
          if (value == null) {
            return [];
          }
          const before = previous?.[key];
          return [
            [
              totalLabel(key),
              number(value),
              ...(previous == null
                ? []
                : [
                    before == null
                      ? ""
                      : `${value >= before ? "+" : "−"}${number(Math.abs(value - before))}`,
                  ]),
            ],
          ];
        }),
      ),
    );
    if (summary.span.first != null && summary.span.last != null) {
      lines.push(
        "",
        m.downloads_span_line({
          first: summary.span.first,
          last: summary.span.last,
        }),
      );
    }
    if (summary.countries.length > 0) {
      lines.push(
        "",
        `## ${m.downloads_countries()}`,
        "",
        ...table(
          [
            m.downloads_col_country(),
            m.downloads_col_years(),
            m.downloads_col_elections(),
            m.downloads_col_contests(),
            m.downloads_col_results(),
            m.downloads_col_finest(),
          ],
          summary.countries.map((country) => [
            `[${inline(countryName(country.code, country.name))}](${origin}/${country.code.toLowerCase()}.md)`,
            span(country.first, country.last),
            number(country.elections),
            number(country.contests),
            number(country.results),
            inline(country.units.at(-1)?.noun ?? ""),
          ]),
        ),
      );
      for (const country of summary.countries) {
        lines.push(
          "",
          `### ${inline(countryName(country.code, country.name))}`,
          "",
        );
        if (country.levels.length > 0) {
          lines.push(
            `- ${m.downloads_levels()}: ${country.levels.map((level) => `${levelLabel(level.kind)} (${number(level.contests)}, ${span(level.first, level.last)})`).join("; ")}`,
          );
        }
        if (country.units.length > 0) {
          lines.push(
            `- ${m.downloads_units()}: ${country.units.map((unit) => `${inline(unit.noun ?? unit.scheme)} (${number(unit.results)}, ${span(unit.first, unit.last)})`).join("; ")}`,
          );
        }
        if (country.election_kinds.length > 0) {
          lines.push(
            `- ${m.downloads_election_kinds()}: ${country.election_kinds.map((kind) => `${electionLabel(kind.kind)} (${number(kind.count)}, ${span(kind.first, kind.last)})`).join("; ")}`,
          );
        }
        if (country.ballot_questions.length > 0) {
          lines.push(
            `- ${m.downloads_questions()}: ${country.ballot_questions.map((question) => `${questionLabel(question.type)} (${number(question.count)}, ${year(question.first)})`).join("; ")}`,
          );
        }
        if (country.formulas.length > 0) {
          lines.push(
            `- ${m.downloads_formulas()}: ${country.formulas.map((formula) => `${formulaLabel(formula.formula)} (${number(formula.contests)})`).join("; ")}`,
          );
        }
      }
    }
    const breakdowns: Array<[string, Array<[string, number]>]> = [
      [
        m.downloads_contest_kinds(),
        summary.contest_kinds.map((row) => [contestLabel(row.kind), row.count]),
      ],
      [
        m.downloads_election_kinds(),
        summary.election_kinds.map((row) => [
          electionLabel(row.kind),
          row.count,
        ]),
      ],
      [
        m.downloads_questions(),
        summary.ballot_questions.map((row) => [
          questionLabel(row.type),
          row.count,
        ]),
      ],
      [
        m.downloads_formulas(),
        summary.formulas.map((row) => [
          formulaLabel(row.formula),
          row.contests,
        ]),
      ],
      [
        m.downloads_stages(),
        summary.stages.map((row) => [stageLabel(row.stage), row.count]),
      ],
      [
        m.downloads_measures(),
        summary.measures.map((row) => [row.code, row.rows]),
      ],
      [
        m.downloads_channels(),
        summary.channels
          .filter((row) => row.code !== "all")
          .map((row) => [row.code, row.rows]),
      ],
    ];
    for (const [heading, rows] of breakdowns) {
      if (rows.length > 0) {
        lines.push(
          "",
          `### ${heading}`,
          "",
          ...rows.map(
            ([label, value]) => `- ${inline(label)}: ${number(value)}`,
          ),
        );
      }
    }
    if (summary.shapes.length > 0) {
      lines.push(
        "",
        `## ${m.downloads_shapes()}`,
        "",
        ...table(
          [
            m.downloads_col_country(),
            m.downloads_col_boundaries(),
            m.downloads_col_shapes(),
            m.downloads_col_areas(),
            m.downloads_col_years(),
          ],
          summary.shapes.map((shape) => [
            inline(countryName(shape.country)),
            `${inline(shape.noun ?? shape.scheme)} (\`${shape.scheme}\`)`,
            number(shape.shapes),
            number(shape.areas),
            span(shape.first, shape.last),
          ]),
        ),
      );
    }
  }
  lines.push("", `## ${m.downloads_quickstart()}`, "");
  for (const [index, step] of quickstart(release.calver).entries()) {
    lines.push(
      `${index + 1}. ${step.text}`,
      "",
      "   ```sh",
      `   ${step.code}`,
      "   ```",
      "",
    );
  }
  lines.push(
    m.downloads_one_file(),
    "",
    "```sh",
    `curl -LO ${latestUrl(origin, release.files[0]?.file ?? "manifest.json")}`,
    "```",
    "",
    `## ${m.downloads_stable()}`,
    "",
    m.downloads_stable_lede(),
    "",
  );
  for (const file of SHARED_FILES) {
    lines.push(`- ${latestUrl(origin, file)}: ${sharedLabel(file)}`);
  }
  lines.push(
    `- ${latestUrl(origin, "<file>")}`,
    "",
    `## ${m.downloads_licenses()}`,
    "",
  );
  for (const [name, bundle] of Object.entries(release.bundles)) {
    lines.push(
      `- \`${name}\`: [${inline(bundle.license)}](${bundle.license_url}). ${inline(bundle.scope)}`,
    );
  }
  if (release.held.length > 0) {
    lines.push(
      "",
      `### ${m.downloads_held()}`,
      "",
      m.downloads_held_lede(),
      "",
      ...release.held.map((entry) => `- ${inline(entry)}`),
    );
  }
  lines.push(
    "",
    `## ${m.downloads_files()}`,
    "",
    `### ${m.downloads_shared()}`,
    "",
  );
  for (const file of SHARED_FILES) {
    lines.push(
      `- [${file}](${fileUrl(origin, release.calver, file)}): ${sharedLabel(file)}`,
    );
  }
  for (const group of groupFiles(release.files)) {
    lines.push(
      "",
      `### \`${group.table}\``,
      "",
      m.downloads_table_line({
        rows: number(group.rows),
        size: bytes(group.bytes),
        files: number(group.files),
      }),
      "",
    );
    for (const item of group.items) {
      const links = [
        ...item.parquet,
        ...(item.csv == null ? [] : [item.csv]),
      ].map(
        (file) =>
          `[${file.file}](${fileUrl(origin, release.calver, file.file)}) (${bytes(file.bytes)}, sha256 \`${file.sha256}\`)`,
      );
      lines.push(
        `- ${item.bundle}, ${inline(item.part === "" ? group.table : item.part)}, ${m.downloads_rows({ count: item.rows })}: ${links.join(", ")}`,
      );
    }
  }
  lines.push("", `## ${m.downloads_changes()}`, "", changesLede(release), "");
  const kinds = new Map<string, number>();
  for (const group of release.groups) {
    kinds.set(group.kind, (kinds.get(group.kind) ?? 0) + group.count);
  }
  for (const [kind, value] of [...kinds].toSorted((a, b) => b[1] - a[1])) {
    lines.push(`- ${changeLabel(kind)}: ${number(value)}`);
  }
  const pipelines = new Map<string, number>();
  for (const group of release.groups) {
    const key = group.pipeline ?? "";
    pipelines.set(key, (pipelines.get(key) ?? 0) + group.count);
  }
  if (pipelines.size > 0) {
    lines.push(
      "",
      `### ${m.downloads_by_pipeline()}`,
      "",
      ...table(
        [m.downloads_col_pipeline(), m.downloads_col_changes()],
        [...pipelines]
          .toSorted((a, b) => b[1] - a[1])
          .map(([pipeline, value]) => [
            `\`${pipeline === "" ? "-" : pipeline}\``,
            number(value),
          ]),
      ),
    );
  }
  if (changes.length > 0) {
    lines.push("", `### ${m.downloads_all_changes()}`, "");
    for (const change of changes) {
      const text = inline(change.summary);
      lines.push(
        `- ${change.published_at.slice(0, 10)}, ${changeLabel(change.kind)}, \`${change.pipeline ?? "-"}\`: ${change.path == null ? text : `[${text}](${origin}/${change.path}.md)`}`,
      );
    }
  }
  lines.push(
    "",
    `## ${m.downloads_cite()}`,
    "",
    "```",
    cite("APA", {
      origin,
      published: release.upto,
      release,
      today: new Date(),
    }),
    "```",
    "",
    "```bibtex",
    cite("BibTeX", {
      origin,
      published: release.upto,
      release,
      today: new Date(),
    }),
    "```",
  );
  if (all.length > 0) {
    lines.push(
      "",
      `## ${m.downloads_releases()}`,
      "",
      ...table(
        [
          m.downloads_version(),
          m.downloads_published_label(),
          m.downloads_size_label(),
          m.downloads_col_results(),
          m.downloads_col_changes(),
        ],
        all.map((row) => [
          `[${row.calver}](${origin}/downloads/${row.calver}.md)`,
          row.created_at.slice(0, 10),
          bytes(row.bytes),
          row.totals?.results == null ? "" : number(row.totals.results),
          number(row.changes),
        ]),
      ),
    );
  }
  return `${lines.join("\n")}\n`;
}

export function changesLede(release: Release) {
  let total = 0;
  for (const group of release.groups) {
    total += group.count;
  }
  return release.since == null
    ? m.downloads_changes_first({
        count: total,
        until: release.upto.slice(0, 10),
      })
    : m.downloads_changes_window({
        count: total,
        since: release.since.slice(0, 10),
        until: release.upto.slice(0, 10),
      });
}
