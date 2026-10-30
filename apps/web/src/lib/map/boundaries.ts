import type { Drawn } from "#lib/contrib.js";
import type { Engine } from "#lib/map/engine.svelte.js";

export const ADDED = "color-mix(in oklab, var(--marker) 70%, transparent)";
export const KEPT = "color-mix(in oklab, var(--foreground) 5%, transparent)";
export const CURRENT =
  "color-mix(in oklab, var(--foreground) 38%, transparent)";

export function sketch(
  engine: Engine,
  drawn: Drawn | null,
  group: string,
  isWhole = false,
) {
  engine.drop(group);
  if (drawn == null) {
    return;
  }
  const source = engine.shapes(`${group}-source`, drawn, group);
  const keys = drawn.tiles ?? [];
  if (isWhole) {
    for (const key of keys) {
      engine.add(
        {
          id: `${group}-current:${key}`,
          type: "line",
          source: engine.tiles(key, group),
          "source-layer": "areas",
          paint: { "line-color": CURRENT, "line-width": 0.75 },
        },
        "etch",
        { group },
      );
    }
    engine.add(
      {
        id: `${group}-after`,
        type: "fill",
        source,
        filter: ["==", ["get", "kind"], "after"],
        paint: {
          "fill-color": ["match", ["get", "status"], "new", ADDED, KEPT],
        },
      },
      "fills",
      { group, rank: 1 },
    );
    engine.add(
      {
        id: `${group}-before`,
        type: "line",
        source,
        filter: ["==", ["get", "kind"], "before"],
        paint: { "line-color": CURRENT, "line-width": 0.75 },
      },
      "etch",
      { group },
    );
    engine.add(
      {
        id: `${group}-outline`,
        type: "line",
        source,
        filter: ["==", ["get", "kind"], "after"],
        paint: { "line-color": "var(--foreground)", "line-width": 1.25 },
      },
      "lines",
      { group },
    );
  }
  for (const [kind, image] of [
    ["moved", "hatch"],
    ["gap", "back"],
    ["overlap", "cross"],
  ] as const) {
    engine.add(
      {
        id: `${group}-${kind}`,
        type: "fill",
        source,
        filter: ["==", ["get", "kind"], kind],
        paint: { "fill-pattern": image },
      },
      "fills",
      { group, rank: 2 },
    );
  }
  engine.add(
    {
      id: `${group}-edge`,
      type: "line",
      source,
      filter: ["in", ["get", "kind"], ["literal", ["gap", "overlap"]]],
      paint: { "line-color": "var(--foreground)", "line-width": 0.75 },
    },
    "lines",
    { group },
  );
  engine.add(
    {
      id: `${group}-parent`,
      type: "line",
      source,
      filter: ["==", ["get", "kind"], "parent"],
      paint: {
        "line-color": "var(--destructive)",
        "line-width": 1.5,
        "line-dasharray": [1, 1.5],
      },
    },
    "lines",
    { group },
  );
}
