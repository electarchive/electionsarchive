import type * as Maplibre from "maplibre-gl";
import type { Lens } from "#lib/atlas/color.js";
import type { Mosaic, Sheet } from "#lib/atlas/data.js";
import type { Tier } from "#lib/atlas/tiers.js";
import type { Arrow, Engine } from "#lib/map/engine.svelte.js";
import { admits, hue, QUIET, read, tone } from "#lib/atlas/color.js";
import { fade } from "#lib/atlas/tiers.js";
import { radius } from "#lib/map/engine.svelte.js";

export type Hit = { set: Sheet; i: number; tier: number; source: string };
type Drawn = { set: Sheet; source: string; tier: number; dots: string | null };

const GROUP = "sheet";

export class Stage {
  version = $state(0);

  #engine: Engine;
  #drawn = new Map<string, Drawn>();
  #hits: Array<Map<number, Hit>> = [];
  #begins: Array<number> = [];
  #tiers: Array<Tier> = [];
  #locked: number | null = null;
  #mosaic: { mosaic: Mosaic; tier: number } | null = null;
  #tinted = "";
  #painted = new Map<string, string>();
  #hovered: { k: number; source: string } | null = null;

  constructor(engine: Engine) {
    this.#engine = engine;
  }

  get tiers() {
    return this.#tiers;
  }

  reset(
    tiers: Array<Tier>,
    begins: Array<number>,
    locked: number | null,
    scope: { outline: string | null; k: number; isNested: boolean },
    parted: Sheet["parted"],
  ) {
    const engine = this.#engine;
    engine.drop(GROUP);
    engine.credits = [];
    this.#drawn.clear();
    this.#painted.clear();
    this.#hits = tiers.map(() => new Map());
    this.#tiers = tiers;
    this.#begins = begins;
    this.#locked = locked;
    this.#mosaic = null;
    this.#tinted = "";
    this.#hovered = null;
    engine.tint(new Map());
    engine.spot(null);
    if (scope.outline != null) {
      const id = engine.tiles(scope.outline, GROUP);
      const near: Maplibre.ExpressionSpecification | undefined =
        parted == null
          ? undefined
          : parted.isOnly
            ? ["in", ["id"], ["literal", parted.ks]]
            : ["!", ["in", ["id"], ["literal", parted.ks]]];
      engine.add(
        {
          id: "context",
          type: "fill",
          source: id,
          "source-layer": "areas",
          ...(near != null && { filter: near }),
          paint: {
            "fill-color":
              "color-mix(in oklab, var(--muted-foreground) 9%, var(--card))",
          },
        },
        "fills",
        { group: GROUP },
      );
      engine.add(
        {
          id: "context-seam",
          type: "line",
          source: id,
          "source-layer": "areas",
          ...(near != null && { filter: near }),
          paint: {
            "line-color":
              "color-mix(in oklab, var(--foreground) 13%, transparent)",
            "line-width": [
              "interpolate",
              ["linear"],
              ["zoom"],
              4,
              0.4,
              10,
              0.9,
            ],
          },
        },
        "fills",
        { group: GROUP },
      );
      if (scope.isNested) {
        engine.add(
          {
            id: "scope",
            type: "line",
            source: id,
            "source-layer": "areas",
            filter: ["==", ["id"], scope.k],
            paint: { "line-color": "var(--foreground)", "line-width": 1.5 },
          },
          "lines",
          { group: GROUP },
        );
      }
    }
  }

  extend(tier: Tier, begin: number) {
    this.#tiers = [...this.#tiers, tier];
    this.#begins = [...this.#begins, begin];
    this.#hits.push(new Map());
  }

  #window(tier: number) {
    if (this.#locked != null) {
      return tier === this.#locked ? { start: -Infinity, end: Infinity } : null;
    }
    return {
      start: this.#begins[tier] ?? -Infinity,
      end: this.#begins[tier + 1] ?? Infinity,
    };
  }

  #layers(tiles: string, tier: number, isDense: boolean, from: number | null) {
    const engine = this.#engine;
    const id = engine.tiles(tiles, GROUP);
    const range = this.#window(tier);
    if (engine.has(`fill:${id}`) || range == null) {
      return id;
    }
    const on: Maplibre.ExpressionSpecification = [
      "case",
      ["boolean", ["feature-state", "on"], false],
      1,
      0,
    ];
    const start =
      from == null ? range.start : from - Math.log2(devicePixelRatio);
    const rim = from ?? range.start;
    const isFading = from != null || this.#locked == null;
    const appear = isFading ? fade(start) : 1;
    const layer = { source: id, "source-layer": "areas" } as const;
    const rank = tier + 1;
    const options = { group: GROUP, rank };
    engine.add(
      {
        id: `fill:${id}`,
        type: "fill",
        ...layer,
        paint: {
          "fill-color": [
            "coalesce",
            ["feature-state", "color"],
            "rgba(0, 0, 0, 0)",
          ],
          "fill-opacity":
            appear as Maplibre.DataDrivenPropertyValueSpecification<number>,
        },
      },
      "fills",
      options,
    );
    engine.add(
      {
        id: `split:${id}`,
        type: "fill",
        ...layer,
        filter: ["in", ["id"], ["literal", []]],
        paint: { "fill-pattern": "hatch" },
      },
      "fills",
      options,
    );
    for (const [shape, flag] of [
      ["hatch", "hatched"],
      ["norun", "absent"],
    ] as const) {
      engine.add(
        {
          id: `${shape}:${id}`,
          type: "fill",
          ...layer,
          paint: {
            "fill-pattern": shape,
            "fill-opacity": [
              "case",
              ["boolean", ["feature-state", flag], false],
              1,
              0,
            ],
          },
        },
        "fills",
        options,
      );
    }
    const width: Maplibre.ExpressionSpecification =
      tier === 0
        ? ["interpolate", ["linear"], ["zoom"], 2, 0.7, 5, 1.3, 8, 2.2]
        : [
            "interpolate",
            ["linear"],
            ["zoom"],
            Math.max(0, Number.isFinite(rim) ? rim : 2),
            isDense ? 0.15 : 0.5,
            Math.max(1, (Number.isFinite(rim) ? rim : 2) + 3),
            isDense ? 0.7 : 1.1,
          ];
    engine.add(
      {
        id: `edge:${id}`,
        type: "line",
        ...layer,
        paint: {
          "line-color": "var(--card)",
          "line-width": width,
          "line-opacity":
            isFading && Number.isFinite(rim)
              ? ["interpolate", ["linear"], ["zoom"], rim, 0, rim + 0.4, on]
              : on,
        },
      },
      "lines",
      { group: GROUP, rank: -rank },
    );
    engine.add(
      {
        id: `focus:${id}`,
        type: "line",
        ...layer,
        paint: {
          "line-color": "var(--foreground)",
          "line-width": [
            "case",
            ["boolean", ["feature-state", "picked"], false],
            2.2,
            1.4,
          ],
          "line-opacity": [
            "case",
            [
              "any",
              ["boolean", ["feature-state", "picked"], false],
              ["boolean", ["feature-state", "hover"], false],
            ],
            1,
            0,
          ],
        },
      },
      "focus",
      { group: GROUP },
    );
    return id;
  }

  #dots(set: Sheet, source: string, tier: number) {
    const engine = this.#engine;
    const range = this.#window(tier);
    const id = `dots:${source}:${set.scope.k}:${set.level ?? ""}`;
    if (range == null || engine.has(id)) {
      return id;
    }
    engine.points(
      id,
      set.places.k.flatMap((k, i) => {
        const x = set.places.x[i];
        const y = set.places.y[i];
        return x == null || y == null
          ? []
          : [
              {
                k,
                at: [x, y] as [number, number],
                properties: { votes: set.places.valid[i] ?? 0 },
              },
            ];
      }),
      GROUP,
    );
    const size: Maplibre.ExpressionSpecification = [
      "coalesce",
      ["feature-state", "size"],
      0,
    ];
    const stops: Array<number> = [];
    if (this.#locked == null) {
      if (Number.isFinite(range.start)) {
        stops.push(range.start, 0, range.start + 0.4, 0.88);
      }
      if (Number.isFinite(range.end)) {
        if (stops.length === 0) {
          stops.push(range.end - 0.4, 0.88);
        }
        stops.push(range.end, 0);
      }
    }
    const opacity =
      stops.length >= 4
        ? ([
            "interpolate",
            ["linear"],
            ["zoom"],
            ...stops,
          ] as Maplibre.ExpressionSpecification)
        : 0.88;
    engine.add(
      {
        id,
        type: "circle",
        source: id,
        layout: { "circle-sort-key": ["-", 0, ["get", "votes"]] },
        paint: {
          "circle-radius": radius(size),
          "circle-color": [
            "coalesce",
            ["feature-state", "dot"],
            "rgba(0, 0, 0, 0)",
          ],
          "circle-opacity": opacity,
          "circle-stroke-opacity": opacity,
          "circle-stroke-color": "var(--card)",
          "circle-stroke-width": ["case", [">", size, 0], 1, 0],
        },
      },
      "points",
      { group: GROUP },
    );
    return id;
  }

  show(set: Sheet, tier: number, from: number | null = null) {
    const level = set.levels.find((entry) => entry.code === set.level);
    if (level?.tiles == null || this.#window(tier) == null) {
      return false;
    }
    const source = this.#layers(
      level.tiles,
      tier,
      set.places.k.length > 400,
      from,
    );
    const key = `${source}|${set.scope.k}|${set.level}`;
    if (this.#drawn.has(key)) {
      return true;
    }
    const hits = this.#hits[tier];
    if (hits == null) {
      return false;
    }
    for (const [i, k] of set.places.k.entries()) {
      hits.set(k, { set, i, tier, source });
    }
    this.#drawn.set(key, {
      set,
      source,
      tier,
      dots: set.places.x.some((x) => x != null)
        ? this.#dots(set, source, tier)
        : null,
    });
    this.version += 1;
    return true;
  }

  drape(mosaic: Mosaic, tier: number) {
    const range = this.#window(tier);
    const hits = this.#hits[tier];
    if (range == null || hits == null || this.#mosaic?.mosaic === mosaic) {
      return;
    }
    this.#mosaic = { mosaic, tier };
    this.#tinted = "";
    for (const [i, k] of mosaic.set.places.k.entries()) {
      hits.set(k, { set: mosaic.set, i, tier, source: "ids" });
    }
    const keys = mosaic.layers.map((layer) => layer.tiles);
    this.#engine.drape(keys, GROUP);
    const appear = this.#locked == null ? fade(range.start) : 1;
    for (const key of keys) {
      this.#engine.style(`ids:${key}`, "raster-opacity", appear);
    }
    this.version += 1;
  }

  hit(tier: number, k: number) {
    return this.#hits[tier]?.get(k) ?? null;
  }

  within(tier: number) {
    const box = this.#engine.gl?.getBounds() ?? null;
    for (let at = tier; at >= 0; at -= 1) {
      const hits: Array<Hit> = [];
      let isBounded = true;
      for (const hit of this.#hits[at]?.values() ?? []) {
        const { set, i } = hit;
        if (set.places.leader[i] == null) {
          continue;
        }
        const x = set.places.x[i];
        const y = set.places.y[i];
        if (x == null || y == null) {
          isBounded = false;
          hits.push(hit);
        } else if (box == null || box.contains([x, y])) {
          hits.push(hit);
        }
      }
      if (hits.length > 0 || at === 0) {
        return { hits, isBounded };
      }
    }
    return { hits: [], isBounded: true };
  }

  arrows(tier: number, lens: Lens, picked: Set<number>) {
    for (let at = tier; at >= 0; at -= 1) {
      const items: Array<Arrow> = [];
      for (const { set, i } of this.#hits[at]?.values() ?? []) {
        const x = set.places.x[i];
        const y = set.places.y[i];
        if (x == null || y == null) {
          continue;
        }
        const reading = read(set, i, lens);
        if (reading.swing == null || !admits(set, reading, lens)) {
          continue;
        }
        items.push({
          at: [x, y],
          swing: reading.swing,
          color: lens.tone(reading.swing >= 0 ? lens.first : lens.second),
          weight: set.places.valid[i] ?? 0,
          isMuted: picked.size > 0 && !picked.has(set.places.k[i] ?? 0),
        });
      }
      if (items.length > 0) {
        return items;
      }
    }
    return [];
  }

  find(k: number) {
    for (const hits of this.#hits.toReversed()) {
      const hit = hits.get(k);
      if (hit != null) {
        return hit;
      }
    }
    return null;
  }

  pick(point: { x: number; y: number }, tier: number) {
    const engine = this.#engine;
    for (let at = tier; at >= 0; at -= 1) {
      const hits = this.#hits[at];
      if (hits == null || hits.size === 0) {
        continue;
      }
      if (this.#mosaic?.tier === at) {
        const k = engine.sample(point);
        const hit = k == null ? null : hits.get(k);
        if (hit != null) {
          return hit;
        }
        continue;
      }
      const layers = [
        ...new Set(
          [...this.#drawn.values()]
            .filter((drawn) => drawn.tier === at)
            .map((drawn) => `fill:${drawn.source}`),
        ),
      ];
      const k = layers.length === 0 ? null : engine.pick(point, layers);
      const hit = k == null ? null : hits.get(k);
      if (hit != null) {
        return hit;
      }
    }
    return null;
  }

  hover(hit: Hit | null) {
    const engine = this.#engine;
    const next =
      hit == null || hit.source === "ids"
        ? null
        : { k: hit.set.places.k[hit.i] ?? 0, source: hit.source };
    engine.mark("hover", next?.source ?? null, next?.k ?? null);
    this.#hovered = next;
    engine.spot(
      hit?.source === "ids" ? (hit.set.places.k[hit.i] ?? null) : null,
    );
  }

  paint(
    lens: Lens,
    picked: Set<number>,
    mark: string,
    scale: number,
    isArrowed: boolean,
  ) {
    const engine = this.#engine;
    const isDotted = lens.mode === "votes";
    for (const [key, drawn] of this.#drawn) {
      if (this.#painted.get(key) === mark) {
        continue;
      }
      this.#painted.set(key, mark);
      const { set, source } = drawn;
      const fills = new Map<
        number,
        Record<string, string | number | boolean>
      >();
      const dots = new Map<number, Record<string, string | number | boolean>>();
      const splits: Array<{ k: number; image: string }> = [];
      for (const [i, k] of set.places.k.entries()) {
        const reading = read(set, i, lens);
        const color = tone(set, i, lens, reading);
        fills.set(k, {
          color: (isDotted || isArrowed) && drawn.dots != null ? QUIET : color,
          on: true,
          picked: picked.has(k),
          hatched:
            (lens.mode === "swing" || lens.mode === "flips") &&
            (set.estimated || reading.isUnmatched),
          absent: reading.isAbsent,
        });
        dots.set(k, {
          dot: color,
          size: isDotted ? scale * Math.sqrt(set.places.valid[i] ?? 0) : 0,
        });
        const held = set.places.held?.[i];
        if (
          lens.isBanded &&
          !isDotted &&
          held != null &&
          admits(set, reading, lens)
        ) {
          splits.push({
            k,
            image: engine.bands(
              held.map(([index, seats]) => [hue(set, index, lens), seats]),
            ),
          });
        }
      }
      engine.paint(source, fills, true);
      engine.filter(`split:${source}`, [
        "in",
        ["id"],
        ["literal", splits.map((entry) => entry.k)],
      ]);
      engine.style(
        `split:${source}`,
        "fill-pattern",
        splits[0] == null
          ? "hatch"
          : [
              "match",
              ["id"],
              ...splits.flatMap((entry) => [entry.k, entry.image]),
              splits[0].image,
            ],
      );
      if (drawn.dots != null) {
        engine.paint(drawn.dots, dots);
      }
    }
    const held = this.#mosaic;
    if (held != null && this.#tinted !== mark) {
      this.#tinted = mark;
      const colors = new Map<number, string>();
      const { set } = held.mosaic;
      for (const [i, k] of set.places.k.entries()) {
        if (set.places.leader[i] != null) {
          colors.set(k, isDotted ? QUIET : tone(set, i, lens));
        }
      }
      engine.tint(colors);
    }
    if (this.#hovered != null) {
      engine.mark("hover", this.#hovered.source, this.#hovered.k);
    }
  }

  letter(tier: number) {
    const engine = this.#engine;
    const gl = engine.gl;
    if (gl == null) {
      return;
    }
    const box = gl.getBounds();
    const west = box.getWest();
    const east = box.getEast();
    const south = box.getSouth();
    const north = box.getNorth();
    const padX = (east - west) / 2;
    const padY = (north - south) / 2;
    let inside = 0;
    const near: Array<{ set: Sheet; i: number }> = [];
    for (const drawn of this.#drawn.values()) {
      if (drawn.tier !== tier) {
        continue;
      }
      for (const [i, x] of drawn.set.places.x.entries()) {
        const y = drawn.set.places.y[i];
        if (
          x == null ||
          y == null ||
          x < west - padX ||
          x > east + padX ||
          y < south - padY ||
          y > north + padY
        ) {
          continue;
        }
        near.push({ set: drawn.set, i });
        if (x >= west && x <= east && y >= south && y <= north) {
          inside += 1;
        }
      }
    }
    const id = engine.points(
      "names",
      inside > 600
        ? []
        : near
            .filter(({ set, i }) => (set.places.name[i] ?? "") !== "")
            .toSorted(
              (a, b) =>
                (b.set.places.valid[b.i] ?? 0) - (a.set.places.valid[a.i] ?? 0),
            )
            .slice(0, 400)
            .map(({ set, i }) => ({
              k: set.places.k[i] ?? 0,
              at: [set.places.x[i] ?? 0, set.places.y[i] ?? 0] as [
                number,
                number,
              ],
              properties: {
                name: set.places.name[i] ?? "",
                votes: set.places.valid[i] ?? 0,
              },
            })),
      GROUP,
    );
    engine.add(
      {
        id,
        type: "symbol",
        source: id,
        layout: {
          "text-field": ["get", "name"],
          "text-font": ["Sofia Sans Semi Condensed SemiBold"],
          "text-size": 12,
          "text-max-width": 9,
          "text-padding": 4,
          "symbol-sort-key": ["-", 0, ["get", "votes"]],
        },
        paint: {
          "text-color": "var(--foreground)",
          "text-halo-color": "var(--card)",
          "text-halo-width": 1.6,
        },
      },
      "labels",
      { group: GROUP },
    );
  }

  shape(hit: Hit) {
    const k = hit.set.places.k[hit.i] ?? 0;
    return hit.source === "ids" ? null : this.#engine.shape(hit.source, k);
  }
}
