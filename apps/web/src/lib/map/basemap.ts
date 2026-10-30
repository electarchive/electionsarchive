import type * as Maplibre from "maplibre-gl";
import type { Engine } from "./engine.svelte.js";

export const SCHEMES = new Set([
  "us",
  "ca",
  "br",
  "gb",
  "ie",
  "fr",
  "de",
  "it",
  "ch",
  "tr",
]);
const REGULAR = ["Sofia Sans Semi Condensed Regular"];
const STRONG = ["Sofia Sans Semi Condensed SemiBold"];
const HALO = "color-mix(in oklab, var(--card) 88%, transparent)";

export function basemap(
  engine: Engine,
  scheme: string,
  isShown: boolean,
  outline: Array<Array<[number, number]>>,
  lifted: Array<[string, Maplibre.ExpressionSpecification]>,
) {
  engine.drop("basemap");
  if (!isShown || !SCHEMES.has(scheme)) {
    return;
  }
  const source = engine.tiles(`world.basemap/${scheme}`, "basemap");
  const metro = engine.tiles(`world.basemap/${scheme}.metro`, "basemap");
  const inside: Maplibre.ExpressionSpecification | true =
    outline.length === 0
      ? true
      : [
          "within",
          {
            type: "MultiPolygon",
            coordinates: outline.map((ring) => [ring.toReversed()]),
          },
        ];
  const add = (
    spec: Maplibre.LayerSpecification extends infer Layer
      ? Layer extends Maplibre.LayerSpecification
        ? Omit<Layer, "source" | "id">
        : never
      : never,
    id: string,
    slot: "etch" | "labels",
  ) =>
    engine.add(
      {
        ...spec,
        id: `basemap:${id}`,
        source:
          "source-layer" in spec &&
          (spec["source-layer"] === "streets" ||
            spec["source-layer"] === "hoods")
            ? metro
            : source,
      } as Maplibre.LayerSpecification,
      slot,
      { group: "basemap" },
    );
  add(
    {
      type: "fill",
      "source-layer": "water",
      paint: { "fill-color": "var(--card)" },
    },
    "water",
    "etch",
  );
  add(
    {
      type: "line",
      "source-layer": "rivers",
      minzoom: 9,
      paint: {
        "line-color": "var(--card)",
        "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.6, 14, 2.5],
      },
    },
    "river",
    "etch",
  );
  for (const [kind, layer, minzoom, widths, ink] of [
    ["minor", "streets", 12.5, [12.5, 0.3, 15, 2.2], 12],
    ["tertiary", "streets", 11, [11, 0.3, 15, 2.8], 15],
    ["secondary", "roads", 9.5, [9.5, 0.3, 15, 3.4], 17],
    ["primary", "roads", 8, [8, 0.35, 15, 4], 19],
    ["highway", "roads", 6, [6, 0.5, 9, 1.1, 15, 4.5], 22],
  ] as const) {
    add(
      {
        type: "line",
        "source-layer": layer,
        minzoom,
        filter: ["==", ["get", "kind"], kind],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": `color-mix(in oklab, var(--map-road) ${ink}%, transparent)`,
          "line-width": [
            "interpolate",
            ["exponential", 1.6],
            ["zoom"],
            ...widths,
          ],
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0.45, 11, 1],
        },
      },
      kind,
      "etch",
    );
  }
  add(
    {
      type: "line",
      "source-layer": "streets",
      minzoom: 11,
      filter: ["==", ["get", "kind"], "rail"],
      paint: {
        "line-color": "color-mix(in oklab, var(--map-road) 22%, transparent)",
        "line-width": 0.8,
        "line-dasharray": [3, 2],
      },
    },
    "rail",
    "etch",
  );
  if (outline.length > 0) {
    engine.add(
      {
        id: "basemap:mask",
        type: "fill",
        source: engine.shapes(
          "basemap:mask",
          {
            type: "Polygon",
            coordinates: [
              [
                [-180, -85],
                [180, -85],
                [180, 85],
                [-180, 85],
                [-180, -85],
              ],
              ...outline,
            ],
          },
          "basemap",
        ),
        paint: { "fill-color": "var(--card)" },
      },
      "etch",
      { group: "basemap" },
    );
    for (const [id, filter] of lifted) {
      engine.lift(id, filter, "basemap");
    }
  }
  for (const [kind, layer, minzoom] of [
    ["minor", "streets", 14.5],
    ["tertiary", "streets", 13.5],
    ["secondary", "roads", 13],
    ["primary", "roads", 13],
  ] as const) {
    add(
      {
        type: "symbol",
        "source-layer": layer,
        minzoom,
        filter: [
          "all",
          ["==", ["get", "kind"], kind],
          ["!=", ["get", "name"], ""],
          inside,
        ],
        layout: {
          "symbol-placement": "line",
          "symbol-spacing": 320,
          "text-field": ["get", "name"],
          "text-font": REGULAR,
          "text-size": 11,
        },
        paint: {
          "text-color": "var(--muted-foreground)",
          "text-halo-color":
            "color-mix(in oklab, var(--card) 70%, transparent)",
          "text-halo-width": 1,
        },
      },
      `street-${kind}`,
      "labels",
    );
  }
  for (const [kind, minzoom, size, font, spacing] of [
    ["neighbourhood", 12.5, 11.5, REGULAR, 0],
    ["macrohood", 10.5, 12, REGULAR, 0],
    ["district", 8.5, 12.5, STRONG, 0.06],
  ] as const) {
    add(
      {
        type: "symbol",
        "source-layer": "hoods",
        minzoom,
        filter: ["all", ["==", ["get", "kind"], kind], inside],
        layout: {
          "text-field": ["get", "name"],
          "text-font": font,
          "text-size": size,
          "text-letter-spacing": spacing,
          "text-max-width": 7,
          "symbol-sort-key": ["-", ["get", "pop"]],
        },
        paint: {
          "text-color":
            "color-mix(in oklab, var(--foreground) 72%, var(--muted-foreground))",
          "text-halo-color": HALO,
          "text-halo-width": 1.2,
          "text-halo-blur": 0.4,
        },
      },
      kind,
      "labels",
    );
  }
  add(
    {
      type: "circle",
      "source-layer": "places",
      maxzoom: 8,
      filter: ["all", ["<=", ["get", "tier"], 1], inside],
      paint: {
        "circle-radius": ["match", ["get", "tier"], 0, 2.75, 2.25],
        "circle-color": "var(--foreground)",
        "circle-stroke-color": "var(--card)",
        "circle-stroke-width": 1,
      },
    },
    "dot",
    "labels",
  );
  for (const [tier, size, font, minzoom] of [
    [3, 12, REGULAR, 9],
    [2, 12.5, REGULAR, 6],
    [1, 13, STRONG, 0],
    [0, 14, STRONG, 0],
  ] as const) {
    add(
      {
        type: "symbol",
        "source-layer": "places",
        minzoom,
        filter: ["all", ["==", ["get", "tier"], tier], inside],
        layout: {
          "text-field": ["get", "name"],
          "text-font": font,
          "text-size": size,
          "text-max-width": 8,
          "text-variable-anchor": ["top", "bottom", "left", "right"],
          "text-radial-offset": 0.5,
          "symbol-sort-key": ["-", ["get", "mz"], ["/", ["get", "pop"], 1e8]],
        },
        paint: {
          "text-color": "var(--foreground)",
          "text-halo-color": HALO,
          "text-halo-width": 1.3,
          "text-halo-blur": 0.4,
        },
      },
      `place-${tier}`,
      "labels",
    );
  }
}
