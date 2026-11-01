import type { LiveElection, LiveOffice, LiveRace } from "@ea/analytics/live";
import * as v from "valibot";

export const COLUMNS = 24;
export const ROW = 20;
export const GAP = 6;

export const KINDS = [
  "race",
  "map",
  "nation",
  "hexes",
  "power",
  "chance",
  "trend",
  "counties",
  "closest",
  "flips",
  "seats",
  "ticker",
  "closings",
  "note",
] as const;

export type Kind = (typeof KINDS)[number];

const Office = v.picklist(["senate", "governor", "house", "other"]);

const PanelSchema = v.object({
  id: v.pipe(v.string(), v.maxLength(40)),
  kind: v.picklist(KINDS),
  x: v.pipe(v.number(), v.integer(), v.minValue(0), v.maxValue(COLUMNS - 1)),
  y: v.pipe(v.number(), v.integer(), v.minValue(0), v.maxValue(4000)),
  w: v.pipe(v.number(), v.integer(), v.minValue(1), v.maxValue(COLUMNS)),
  h: v.pipe(v.number(), v.integer(), v.minValue(1), v.maxValue(200)),
  race: v.optional(v.nullable(v.pipe(v.string(), v.maxLength(200)))),
  office: v.optional(Office),
  mode: v.optional(v.picklist(["lead", "shift", "counted"])),
  text: v.optional(v.pipe(v.string(), v.maxLength(2000))),
  title: v.optional(v.pipe(v.string(), v.maxLength(80))),
});

const PageSchema = v.object({
  id: v.pipe(v.string(), v.maxLength(40)),
  name: v.pipe(v.string(), v.maxLength(40)),
  panels: v.pipe(v.array(PanelSchema), v.maxLength(80)),
});

const BoardSchema = v.object({
  version: v.literal(1),
  pages: v.pipe(v.array(PageSchema), v.minLength(1), v.maxLength(12)),
  current: v.pipe(v.number(), v.integer(), v.minValue(0)),
  density: v.optional(v.picklist(["compact", "cozy"])),
  cycle: v.optional(v.pipe(v.number(), v.integer(), v.minValue(0))),
});

export type Panel = v.InferOutput<typeof PanelSchema>;
export type Page = v.InferOutput<typeof PageSchema>;
export type Board = v.InferOutput<typeof BoardSchema>;
export type Cell = Pick<Panel, "x" | "y" | "w" | "h">;

export const SIZES: Record<
  Kind,
  { w: number; h: number; minW: number; minH: number }
> = {
  race: { w: 6, h: 9, minW: 4, minH: 5 },
  map: { w: 8, h: 14, minW: 4, minH: 7 },
  nation: { w: 10, h: 15, minW: 6, minH: 8 },
  hexes: { w: 9, h: 15, minW: 6, minH: 8 },
  power: { w: 8, h: 4, minW: 5, minH: 3 },
  chance: { w: 6, h: 8, minW: 4, minH: 5 },
  trend: { w: 8, h: 9, minW: 5, minH: 6 },
  counties: { w: 7, h: 14, minW: 5, minH: 6 },
  closest: { w: 7, h: 12, minW: 5, minH: 6 },
  flips: { w: 6, h: 10, minW: 4, minH: 5 },
  seats: { w: 12, h: 6, minW: 6, minH: 3 },
  ticker: { w: 6, h: 12, minW: 4, minH: 6 },
  closings: { w: 5, h: 12, minW: 4, minH: 6 },
  note: { w: 5, h: 5, minW: 3, minH: 2 },
};

export function id() {
  return [...crypto.getRandomValues(new Uint8Array(4))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function collides(a: Panel, b: Panel) {
  return (
    a.id !== b.id &&
    a.x < b.x + b.w &&
    b.x < a.x + a.w &&
    a.y < b.y + b.h &&
    b.y < a.y + a.h
  );
}

export function settle(panels: Array<Panel>, pinned: string | null = null) {
  const fixed = panels.find((panel) => panel.id === pinned);
  const placed: Array<Panel> = fixed == null ? [] : [fixed];
  const rest = panels
    .filter((panel) => panel !== fixed)
    .toSorted((a, b) => a.y - b.y || a.x - b.x);
  for (const panel of rest) {
    const w = Math.min(panel.w, COLUMNS);
    const next = { ...panel, w, x: Math.min(panel.x, COLUMNS - w), y: 0 };
    while (placed.some((other) => collides(next, other))) {
      next.y += 1;
    }
    placed.push(next);
  }
  const order = new Map(panels.map((panel, index) => [panel.id, index]));
  return placed.toSorted(
    (a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0),
  );
}

export function vacancy(panels: Array<Panel>, w: number, h: number) {
  for (let y = 0; ; y += 1) {
    for (let x = 0; x <= COLUMNS - w; x += 1) {
      const probe = { id: "", kind: "note" as const, x, y, w, h };
      if (panels.every((panel) => !collides(probe, panel))) {
        return { x, y };
      }
    }
  }
}

export function bottom(panels: Array<Panel>) {
  let found = 0;
  for (const panel of panels) {
    found = Math.max(found, panel.y + panel.h);
  }
  return found;
}

function panel(
  kind: Kind,
  x: number,
  y: number,
  w: number,
  h: number,
  extra: Partial<Panel> = {},
): Panel {
  return { id: id(), kind, x, y, w, h, ...extra };
}

function cards(
  races: Array<LiveRace>,
  top: number,
  each: { w: number; h: number },
) {
  const per = Math.floor(COLUMNS / each.w);
  return races.map((race, index) =>
    panel(
      "race",
      (index % per) * each.w,
      top + Math.floor(index / per) * each.h,
      each.w,
      each.h,
      { race: race.slug },
    ),
  );
}

export function standard(
  election: LiveElection,
  names: {
    overview: string;
    desk: string;
    offices: Record<LiveOffice, string>;
  },
): Board {
  const isNation = election.slug.split("/").length === 2;
  const closes = new Map(
    election.pollClose.map((entry) => [entry.state, entry.at]),
  );
  const ordered = election.races.toSorted(
    (a, b) =>
      (closes.get(a.state) ?? "").localeCompare(closes.get(b.state) ?? "") ||
      a.state.localeCompare(b.state) ||
      Number(a.district ?? 0) - Number(b.district ?? 0),
  );
  const of = (office: LiveOffice) =>
    ordered.filter((race) => race.office === office);
  const has = (office: LiveOffice) => of(office).length > 0;
  const pages: Array<Page> = [];
  if (isNation) {
    pages.push(
      {
        id: id(),
        name: names.overview,
        panels: [
          panel("power", 0, 0, 8, 4, { office: "senate" }),
          panel("power", 8, 0, 8, 4, { office: "house" }),
          panel("power", 16, 0, 8, 4, { office: "governor" }),
          panel("nation", 0, 4, 10, 16, { office: "senate" }),
          panel("hexes", 10, 4, 8, 16),
          panel("race", 18, 4, 6, 9, { race: null }),
          panel("chance", 18, 13, 6, 7, { race: null }),
          panel("closest", 0, 20, 7, 12, { office: "senate" }),
          panel("closest", 7, 20, 6, 12, { office: "house" }),
          panel("ticker", 13, 20, 6, 12),
          panel("closings", 19, 20, 5, 12),
        ],
      },
      {
        id: id(),
        name: names.desk,
        panels: [
          panel("race", 0, 0, 6, 10, { race: null }),
          panel("map", 6, 0, 11, 20, { race: null, mode: "lead" }),
          panel("chance", 17, 0, 7, 8, { race: null }),
          panel("trend", 17, 8, 7, 12, { race: null }),
          panel("counties", 0, 10, 6, 18, { race: null }),
          panel("map", 6, 20, 11, 14, { race: null, mode: "shift" }),
          panel("ticker", 17, 20, 7, 14),
        ],
      },
    );
    for (const office of ["senate", "governor"] as const) {
      if (!has(office)) {
        continue;
      }
      const head = [
        panel("power", 0, 0, 12, 4, { office }),
        panel("seats", 12, 0, 12, 4, { office }),
        panel("nation", 0, 4, 12, 16, { office }),
        panel("closest", 12, 4, 6, 16, { office }),
        panel("flips", 18, 4, 6, 16, { office }),
      ];
      pages.push({
        id: id(),
        name: names.offices[office],
        panels: [...head, ...cards(of(office), 20, { w: 6, h: 9 })],
      });
    }
    if (has("house")) {
      pages.push({
        id: id(),
        name: names.offices.house,
        panels: [
          panel("power", 0, 0, 24, 4, { office: "house" }),
          panel("hexes", 0, 4, 14, 22),
          panel("closest", 14, 4, 5, 22, { office: "house" }),
          panel("flips", 19, 4, 5, 11, { office: "house" }),
          panel("race", 19, 15, 5, 11, { race: null }),
          panel("seats", 0, 26, 24, 6, { office: "house" }),
        ],
      });
    }
  } else {
    const lead = of("senate")[0] ?? of("governor")[0] ?? ordered[0];
    const second = [...of("senate"), ...of("governor")].find(
      (race) => race !== lead,
    );
    const panels: Array<Panel> = [];
    if (lead != null) {
      panels.push(
        panel("race", 0, 0, 7, 10, { race: lead.slug }),
        panel("map", 7, 0, 10, 20, { race: lead.slug, mode: "lead" }),
        panel("chance", 17, 0, 7, 8, { race: lead.slug }),
        panel("trend", 17, 8, 7, 12, { race: lead.slug }),
        panel("counties", 0, 10, 7, 18, { race: lead.slug }),
      );
    }
    if (second != null) {
      panels.push(
        panel("race", 7, 20, 6, 10, { race: second.slug }),
        panel("map", 13, 20, 11, 16, { race: second.slug, mode: "lead" }),
      );
    }
    panels.push(panel("ticker", 7, 30, 6, 12), panel("closings", 0, 28, 7, 8));
    pages.push({ id: id(), name: names.overview, panels: settle(panels) });
    if (has("house")) {
      pages.push({
        id: id(),
        name: names.offices.house,
        panels: [
          panel("power", 0, 0, 12, 4, { office: "house" }),
          panel("seats", 12, 0, 12, 4, { office: "house" }),
          ...cards(of("house"), 4, { w: 6, h: 9 }),
        ],
      });
    }
  }
  return { version: 1, pages, current: 0, density: "compact", cycle: 0 };
}

export function parse(value: unknown): Board | null {
  const found = v.safeParse(BoardSchema, value);
  if (!found.success) {
    return null;
  }
  const board = found.output;
  return {
    ...board,
    current: Math.min(board.current, board.pages.length - 1),
    pages: board.pages.map((page) => ({
      ...page,
      panels: settle(
        page.panels.map((entry) => ({
          ...entry,
          w: Math.max(SIZES[entry.kind].minW, Math.min(entry.w, COLUMNS)),
          h: Math.max(SIZES[entry.kind].minH, entry.h),
        })),
      ),
    })),
  };
}

async function bytes(stream: ReadableStream<Uint8Array>) {
  return new Response(stream).arrayBuffer();
}

export async function pack(board: Board) {
  const input = new Blob([JSON.stringify(board)]).stream();
  const packed = new Uint8Array(
    await bytes(input.pipeThrough(new CompressionStream("deflate-raw"))),
  );
  return packed.toBase64({ alphabet: "base64url", omitPadding: true });
}

export async function unpack(text: string) {
  try {
    const packed = Uint8Array.fromBase64(text, { alphabet: "base64url" });
    const input = new Blob([packed]).stream();
    const raw = await bytes(
      input.pipeThrough(new DecompressionStream("deflate-raw")),
    );
    return parse(JSON.parse(new TextDecoder().decode(raw)));
  } catch {
    return null;
  }
}

export function load(key: string) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? null : parse(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function save(key: string, board: Board) {
  try {
    localStorage.setItem(key, JSON.stringify(board));
  } catch {}
}

export function forget(key: string) {
  try {
    localStorage.removeItem(key);
  } catch {}
}
