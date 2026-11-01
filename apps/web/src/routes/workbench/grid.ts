import type { ViewSpec } from "@ea/view-spec";
import { m } from "#lib/paraglide/messages.js";

export const COLUMNS = 24;
export const ROW = 20;
export const GAP = 6;

export const KINDS = [
  "summary",
  "bar",
  "place",
  "map",
  "table",
  "closest",
  "movers",
  "flips",
  "scatter",
  "strip",
  "hemicycle",
  "note",
] as const;

export type Kind = (typeof KINDS)[number];
export type Spec = ViewSpec["panels"][number];
export type Cell = { x: number; y: number; w: number; h: number };

export const SIZES: Record<
  Kind,
  { w: number; h: number; minW: number; minH: number }
> = {
  summary: { w: 7, h: 7, minW: 4, minH: 4 },
  bar: { w: 7, h: 11, minW: 4, minH: 5 },
  place: { w: 6, h: 11, minW: 4, minH: 5 },
  map: { w: 11, h: 22, minW: 5, minH: 8 },
  table: { w: 12, h: 18, minW: 6, minH: 6 },
  closest: { w: 6, h: 14, minW: 4, minH: 6 },
  movers: { w: 6, h: 14, minW: 4, minH: 6 },
  flips: { w: 6, h: 14, minW: 4, minH: 6 },
  scatter: { w: 9, h: 16, minW: 5, minH: 9 },
  strip: { w: 9, h: 11, minW: 5, minH: 6 },
  hemicycle: { w: 8, h: 14, minW: 5, minH: 8 },
  note: { w: 5, h: 5, minW: 3, minH: 2 },
};

export const KIND_TEXT: Record<
  Kind,
  { name: () => string; about: () => string }
> = {
  summary: {
    name: m.workbench_kind_summary,
    about: m.workbench_kind_summary_about,
  },
  bar: { name: m.workbench_panel_results, about: m.workbench_kind_bar_about },
  place: { name: m.workbench_kind_place, about: m.workbench_kind_place_about },
  map: { name: m.workbench_panel_map, about: m.workbench_kind_map_about },
  table: {
    name: m.workbench_panel_places,
    about: m.workbench_kind_table_about,
  },
  closest: {
    name: m.workbench_kind_closest,
    about: m.workbench_kind_closest_about,
  },
  movers: {
    name: m.workbench_kind_movers,
    about: m.workbench_kind_movers_about,
  },
  flips: { name: m.workbench_kind_flips, about: m.workbench_kind_flips_about },
  scatter: {
    name: m.workbench_panel_scatter,
    about: m.workbench_kind_scatter_about,
  },
  strip: { name: m.workbench_kind_strip, about: m.workbench_kind_strip_about },
  hemicycle: {
    name: m.workbench_kind_seats,
    about: m.workbench_kind_seats_about,
  },
  note: { name: m.board_kind_note, about: m.board_kind_note_about },
};

export function kindOf(panel: Spec): Kind {
  if (panel.kind === "chart") {
    return panel.chart === "hemicycle" ||
      panel.chart === "scatter" ||
      panel.chart === "strip"
      ? panel.chart
      : "bar";
  }
  if (panel.kind === "list") {
    return panel.list;
  }
  return panel.kind;
}

export function specOf(kind: Kind): Spec {
  if (kind === "map") {
    return { kind: "map", color: "winner" };
  }
  if (kind === "closest" || kind === "movers" || kind === "flips") {
    return { kind: "list", list: kind };
  }
  if (kind === "bar" || kind === "scatter" || kind === "strip") {
    return { kind: "chart", chart: kind };
  }
  if (kind === "hemicycle") {
    return { kind: "chart", chart: "hemicycle" };
  }
  return { kind };
}

function collides(a: Cell, b: Cell) {
  return (
    a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
  );
}

export function settle(cells: Array<Cell>, pinned: number | null = null) {
  const placed: Array<{ at: number; cell: Cell }> = [];
  const fixed = pinned == null ? null : cells[pinned];
  if (fixed != null && pinned != null) {
    placed.push({ at: pinned, cell: fixed });
  }
  const order = cells
    .map((cell, at) => ({ at, cell }))
    .filter((entry) => entry.at !== pinned)
    .toSorted((a, b) => a.cell.y - b.cell.y || a.cell.x - b.cell.x);
  for (const entry of order) {
    const w = Math.min(entry.cell.w, COLUMNS);
    const next = {
      ...entry.cell,
      w,
      x: Math.min(entry.cell.x, COLUMNS - w),
      y: 0,
    };
    while (placed.some((other) => collides(next, other.cell))) {
      next.y += 1;
    }
    placed.push({ at: entry.at, cell: next });
  }
  const found: Array<Cell> = [...cells];
  for (const entry of placed) {
    found[entry.at] = entry.cell;
  }
  return found;
}

export function vacancy(cells: Array<Cell>, w: number, h: number) {
  for (let y = 0; ; y += 1) {
    for (let x = 0; x <= COLUMNS - w; x += 1) {
      const probe = { x, y, w, h };
      if (cells.every((cell) => !collides(probe, cell))) {
        return { x, y };
      }
    }
  }
}

export function standard(options: {
  hasChamber: boolean;
  hasPlaces: boolean;
}): ViewSpec["panels"] {
  const area = (x: number, y: number, w: number, h: number) => ({ x, y, w, h });
  if (!options.hasPlaces) {
    return [
      { kind: "summary", area: area(0, 0, 8, 7) },
      { kind: "chart", chart: "bar", area: area(0, 7, 8, 14) },
      ...(options.hasChamber
        ? [
            {
              kind: "chart" as const,
              chart: "hemicycle" as const,
              area: area(8, 0, 10, 16),
            },
          ]
        : []),
      { kind: "note", area: area(18, 0, 6, 7) },
    ];
  }
  return [
    { kind: "summary", area: area(0, 0, 7, 7) },
    { kind: "chart", chart: "bar", area: area(0, 7, 7, 12) },
    { kind: "map", color: "winner", area: area(7, 0, 10, 23) },
    { kind: "chart", chart: "scatter", area: area(17, 0, 7, 15) },
    { kind: "list", list: "flips", area: area(17, 15, 7, 8) },
    { kind: "place", area: area(0, 19, 7, 11) },
    { kind: "table", area: area(7, 23, 11, 16) },
    { kind: "list", list: "closest", area: area(18, 23, 6, 16) },
    { kind: "chart", chart: "strip", area: area(0, 30, 7, 9) },
    ...(options.hasChamber
      ? [
          {
            kind: "chart" as const,
            chart: "hemicycle" as const,
            area: area(0, 39, 9, 14),
          },
        ]
      : []),
  ];
}
