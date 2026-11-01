import { m } from "#lib/paraglide/messages.js";
import type { Contest, Entry } from "./desk.svelte.js";
import type { Kind } from "./grid.js";

type File = { label: string; name: string; text: string; type: string };

const FALLBACK = "#8a8578";

function quote(value: string | number | null) {
  if (value == null) {
    return "";
  }
  return typeof value === "number"
    ? String(value)
    : `"${value.replaceAll('"', '""')}"`;
}

function table(lines: Array<Array<string | number | null>>) {
  return lines
    .map((line) => line.map((value) => quote(value)).join(","))
    .join("\n");
}

function vega(kind: Kind, contest: Contest, focus: Entry | null) {
  if (kind === "bar") {
    return {
      data: {
        values: contest.rows.flatMap((row) =>
          row.current == null
            ? []
            : [
                {
                  option: row.label,
                  share: row.current.share,
                  earlier: row.earlier?.share ?? null,
                  color: row.color ?? FALLBACK,
                },
              ],
        ),
      },
      encoding: {
        y: { field: "option", type: "nominal", sort: "-x", title: null },
      },
      layer: [
        {
          mark: "bar",
          encoding: {
            x: {
              field: "share",
              type: "quantitative",
              axis: { format: ".0%" },
              title: m.col_share(),
            },
            color: { field: "color", type: "nominal", scale: null },
          },
        },
        {
          mark: { type: "tick", color: "black" },
          encoding: { x: { field: "earlier", type: "quantitative" } },
        },
      ],
    };
  }
  if (kind !== "scatter") {
    return null;
  }
  const legend = contest.places?.legend ?? [];
  const at = legend.findIndex((entry) => entry.key === focus?.key);
  return {
    data: {
      values: (contest.places?.rows ?? []).flatMap((row) => {
        const earlier = row.earlier?.[at];
        const share = row.shares[at];
        return earlier == null || share == null
          ? []
          : [
              {
                place: row.name,
                before: earlier,
                change: share - earlier,
                votes: row.valid,
                leader:
                  row.leader == null
                    ? null
                    : (legend[row.leader]?.label ?? null),
              },
            ];
      }),
    },
    layer: [
      {
        mark: { type: "circle", opacity: 0.7 },
        encoding: {
          x: { field: "before", type: "quantitative", axis: { format: ".0%" } },
          y: {
            field: "change",
            type: "quantitative",
            axis: { format: "+.0%" },
          },
          size: { field: "votes", type: "quantitative", legend: null },
          color: {
            field: "leader",
            type: "nominal",
            scale: {
              domain: legend.map((entry) => entry.label),
              range: legend.map((entry) => entry.color ?? FALLBACK),
            },
          },
          tooltip: [
            { field: "place" },
            { field: "before", format: ".1%" },
            { field: "change", format: "+.1%" },
          ],
        },
      },
    ],
  };
}

function csv(kind: Kind, contest: Contest) {
  if (kind === "bar" || kind === "summary") {
    return table([
      [m.col_option(), m.col_votes(), m.col_share(), m.col_change()],
      ...contest.rows.flatMap((row) =>
        row.current == null
          ? []
          : [
              [
                row.label,
                row.current.votes,
                row.current.share,
                row.earlier == null
                  ? null
                  : row.current.share - row.earlier.share,
              ],
            ],
      ),
    ]);
  }
  if (kind === "hemicycle") {
    return contest.chamber == null
      ? null
      : table([
          [m.col_party(), m.col_seats()],
          ...contest.chamber.parties.map((party) => [party.label, party.seats]),
        ]);
  }
  if (contest.places == null || kind === "note") {
    return null;
  }
  const legend = contest.places.legend;
  return table([
    [
      contest.places.level ?? "",
      m.col_votes(),
      m.col_margin(),
      ...legend.map((entry) => entry.label),
      ...(contest.places.rows.some((row) => row.earlier != null)
        ? legend.map((entry) => `${entry.label}, ${m.col_change()}`)
        : []),
    ],
    ...contest.places.rows.map((row) => [
      row.name,
      row.valid,
      row.margin,
      ...row.shares,
      ...(row.earlier == null
        ? []
        : row.shares.map(
            (share, index) => share - (row.earlier?.[index] ?? 0),
          )),
    ]),
  ]);
}

export function files(
  kind: Kind,
  contest: Contest,
  focus: Entry | null,
  spec: unknown,
): Array<File> {
  const stem = contest.path?.replaceAll("/", "-") ?? "workbench";
  const found: Array<File> = [];
  const chart = vega(kind, contest, focus);
  if (chart != null) {
    found.push({
      label: m.workbench_export_vega(),
      name: `${stem}-${kind}.vl.json`,
      text: JSON.stringify(
        {
          $schema: "https://vega.github.io/schema/vega-lite/v5.json",
          title: `${contest.name}, ${contest.scope.name}`,
          width: 480,
          ...chart,
        },
        null,
        2,
      ),
      type: "application/json",
    });
  }
  const sheet = csv(kind, contest);
  if (sheet != null) {
    found.push({
      label: m.workbench_export_csv(),
      name: `${stem}-${kind}.csv`,
      text: sheet,
      type: "text/csv",
    });
  }
  found.push({
    label: m.workbench_export_spec(),
    name: "view.json",
    text: JSON.stringify(spec, null, 2),
    type: "application/json",
  });
  return found;
}

export function download(file: File) {
  const url = URL.createObjectURL(new Blob([file.text], { type: file.type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  link.click();
  URL.revokeObjectURL(url);
}
