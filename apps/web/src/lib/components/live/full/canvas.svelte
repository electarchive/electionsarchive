<script lang="ts">
  import type { Snippet } from "svelte";
  import { untrack } from "svelte";
  import type { Geography } from "#lib/live/standing.js";
  import { prefs } from "#lib/live/prefs.svelte.js";
  import { basemap } from "#lib/map/basemap.js";
  import { Engine } from "#lib/map/engine.svelte.js";
  import type { Dot, Fill } from "./model.js";
  import { BLANK } from "./model.js";

  let {
    geography,
    fills,
    dots = [],
    labels = [],
    selected = null,
    hovered = $bindable(null),
    pulses = [],
    inset = { top: 0, bottom: 0 },
    label,
    onPick,
    tip,
  }: {
    geography: Geography | null;
    fills: Map<string, Fill>;
    dots?: Array<Dot>;
    labels?: Array<{ areaId: string; text: string; rank: number }>;
    selected?: string | null;
    hovered?: string | null;
    pulses?: Array<{ key: string; areaId: string; color: string }>;
    inset?: { top: number; bottom: number };
    label: string;
    onPick: (areaId: string | null) => void;
    tip: Snippet<[string]>;
  } = $props();

  const places = $derived(geography?.places ?? []);
  const kOf = $derived(new Map(places.map((place) => [place.areaId, place.k])));
  const areaOf = $derived(
    new Map(places.map((place) => [place.k, place.areaId])),
  );
  const atOf = $derived(
    new Map(places.map((place) => [place.areaId, place.at])),
  );

  let pointer = $state<{ x: number; y: number } | null>(null);
  let width = $state(0);
  let height = $state(0);
  let tipWidth = $state(240);
  let tipHeight = $state(120);
  let moved = $state(0);
  let current: {
    key: string;
    group: string;
    source: string;
    fill: string;
  } | null = null;
  let serial = 0;
  let retired: Array<string> = [];

  const engine = new Engine(
    untrack(() => geography?.frame) ?? {
      west: -125,
      south: 24,
      east: -66,
      north: 50,
    },
    (w, h) => {
      const edge = Math.max(12, Math.round(Math.min(w, h) * 0.04));
      return {
        top: edge + inset.top,
        right: edge,
        bottom: edge + Math.min(inset.bottom, h * 0.6),
        left: edge,
      };
    },
    {
      maxZoom: 12,
      onReady: () => untrack(draw),
      onMove: () => {
        moved += 1;
      },
      onPointer: ({ kind, point, isTouch }) => {
        if (kind === "leave") {
          hovered = null;
          pointer = null;
          engine.cursor(false);
          return;
        }
        const k = current == null ? null : engine.pick(point, [current.fill]);
        const areaId = k == null ? null : (areaOf.get(k) ?? null);
        if (kind === "click") {
          pointer = null;
          onPick(areaId);
          return;
        }
        if (isTouch) {
          return;
        }
        hovered = areaId;
        pointer = areaId == null ? null : point;
        engine.cursor(areaId != null);
      },
    },
  );

  function layers(found: Geography) {
    serial += 1;
    const group = `full-${serial}`;
    const source = engine.tiles(found.tiles);
    const ks = found.places.map((place) => place.k);
    const kept = new Set(ks);
    const ground = (found.ground ?? []).filter((k) => !kept.has(k));
    const within: ["in", ["id"], ["literal", Array<number>]] = [
      "in",
      ["id"],
      ["literal", ks],
    ];
    if (ground.length > 0) {
      engine.add(
        {
          id: `${group}-ground`,
          type: "fill",
          source,
          "source-layer": "areas",
          filter: ["in", ["id"], ["literal", ground]],
          paint: {
            "fill-color":
              "color-mix(in oklab, var(--muted-foreground) 5%, var(--background))",
          },
        },
        "fills",
        { group, rank: 0 },
      );
      engine.add(
        {
          id: `${group}-ground-seam`,
          type: "line",
          source,
          "source-layer": "areas",
          filter: ["in", ["id"], ["literal", ground]],
          paint: {
            "line-color":
              "color-mix(in oklab, var(--muted-foreground) 22%, var(--background))",
            "line-width": 0.6,
          },
        },
        "lines",
        { group },
      );
    }
    engine.add(
      {
        id: `${group}-fill`,
        type: "fill",
        source,
        "source-layer": "areas",
        filter: within,
        paint: {
          "fill-color": ["coalesce", ["feature-state", "color"], BLANK],
          "fill-color-transition": { duration: 600 },
        },
      },
      "fills",
      { group, rank: 1 },
    );
    engine.add(
      {
        id: `${group}-later`,
        type: "fill",
        source,
        "source-layer": "areas",
        filter: within,
        paint: {
          "fill-pattern": "hatch",
          "fill-opacity": [
            "case",
            ["boolean", ["feature-state", "later"], false],
            1,
            0,
          ],
        },
      },
      "fills",
      { group, rank: 2 },
    );
    engine.add(
      {
        id: `${group}-seam`,
        type: "line",
        source,
        "source-layer": "areas",
        filter: within,
        paint: {
          "line-color": "var(--background)",
          "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.6, 9, 1.6],
        },
      },
      "lines",
      { group },
    );
    engine.add(
      {
        id: `${group}-focus`,
        type: "line",
        source,
        "source-layer": "areas",
        filter: within,
        layout: { "line-join": "round" },
        paint: {
          "line-color": "var(--foreground)",
          "line-width": [
            "case",
            ["boolean", ["feature-state", "pinned"], false],
            2.6,
            ["boolean", ["feature-state", "hover"], false],
            1.6,
            0,
          ],
        },
      },
      "focus",
      { group },
    );
    if (found.outline.length > 0) {
      engine.add(
        {
          id: `${group}-veil`,
          type: "fill",
          source: engine.veil(`${group}-veil`, found.outline, group),
          paint: {
            "fill-color": "var(--background)",
            "fill-opacity": 0.55,
            "fill-antialias": false,
          },
        },
        "etch",
        { group, rank: 1 },
      );
      engine.add(
        {
          id: `${group}-edge`,
          type: "line",
          source: engine.shapes(
            `${group}-edge`,
            { type: "MultiLineString", coordinates: found.outline },
            group,
          ),
          layout: { "line-join": "round" },
          paint: {
            "line-color":
              "color-mix(in oklab, var(--foreground) 60%, transparent)",
            "line-width": [
              "interpolate",
              ["linear"],
              ["zoom"],
              5,
              0.8,
              10,
              1.6,
            ],
          },
        },
        "focus",
        { group },
      );
    }
    return { group, source, fill: `${group}-fill` };
  }

  function draw() {
    const gl = engine.gl;
    if (gl == null || geography == null) {
      return;
    }
    const key = `${geography.tiles}|${places.length}|${JSON.stringify(geography.frame)}`;
    if (current?.key !== key) {
      const isFirst = current == null;
      if (current != null) {
        retired.push(current.group);
      }
      current = { key, ...layers(geography) };
      const keep = current.group;
      gl.once("idle", () => {
        for (const group of retired) {
          if (group !== keep) {
            engine.drop(group);
          }
        }
        retired = retired.filter((group) => group === keep);
      });
      engine.frame(geography.frame, !isFirst);
    }
    const { group, source } = current;
    engine.paint(
      source,
      new Map(
        places.flatMap(
          (place): Array<[number, Record<string, string | boolean>]> => {
            const fill = fills.get(place.areaId);
            return fill == null
              ? []
              : [
                  [
                    place.k,
                    fill.isLater === true
                      ? { color: fill.color, later: true }
                      : { color: fill.color },
                  ],
                ];
          },
        ),
      ),
    );
    const largest = Math.max(1, ...dots.map((dot) => dot.value));
    const circles = engine.points(
      `${group}-dots`,
      dots.flatMap((dot) => {
        const at = atOf.get(dot.areaId);
        return at == null
          ? []
          : [
              {
                k: kOf.get(dot.areaId),
                at,
                properties: {
                  r: Math.max(2.5, 34 * Math.sqrt(dot.value / largest)),
                  c: engine.read(dot.color),
                },
              },
            ];
      }),
      group,
    );
    if (!engine.has(`${group}-dots`)) {
      engine.add(
        {
          id: `${group}-dots`,
          type: "circle",
          source: circles,
          paint: {
            "circle-radius": [
              "interpolate",
              ["exponential", 2],
              ["zoom"],
              3,
              ["*", ["get", "r"], 0.4],
              6,
              ["*", ["get", "r"], 0.9],
              10,
              ["*", ["get", "r"], 3],
            ],
            "circle-color": ["get", "c"],
            "circle-opacity": 0.84,
            "circle-stroke-color": "var(--background)",
            "circle-stroke-width": 1,
          },
        },
        "points",
        { group },
      );
    }
    const named = engine.points(
      `${group}-names`,
      labels.flatMap((entry) => {
        const at = atOf.get(entry.areaId);
        return at == null
          ? []
          : [{ at, properties: { name: entry.text, rank: entry.rank } }];
      }),
      group,
    );
    if (!engine.has(`${group}-names`)) {
      engine.add(
        {
          id: `${group}-names`,
          type: "symbol",
          source: named,
          layout: {
            "text-field": ["get", "name"],
            "text-font": ["Sofia Sans Semi Condensed SemiBold"],
            "text-size": ["interpolate", ["linear"], ["zoom"], 5, 10, 10, 13],
            "text-max-width": 8,
            "text-padding": 3,
            "symbol-sort-key": ["get", "rank"],
          },
          paint: {
            "text-color": "var(--foreground)",
            "text-halo-color":
              "color-mix(in oklab, var(--card) 85%, transparent)",
            "text-halo-width": 1.4,
            "text-opacity": [
              "interpolate",
              ["linear"],
              ["zoom"],
              5.5,
              0,
              6.5,
              1,
            ],
          },
        },
        "labels",
        { group },
      );
    }
    engine.mark(
      "hover",
      source,
      hovered == null ? null : (kOf.get(hovered) ?? null),
    );
    engine.mark(
      "pinned",
      source,
      selected == null ? null : (kOf.get(selected) ?? null),
    );
  }

  $effect(() => {
    void [geography, fills, dots, labels, selected, hovered, engine.theme];
    if (engine.gl != null) {
      untrack(draw);
    }
  });

  $effect(() => {
    if (engine.gl == null || geography == null) {
      return;
    }
    const scheme = geography.scheme;
    const outline = geography.outline;
    const isDetailed = prefs.isBasemap;
    untrack(() => basemap(engine, scheme, isDetailed, outline));
  });

  export function focus(areaId: string) {
    const k = kOf.get(areaId);
    if (current == null || k == null) {
      return false;
    }
    const box = engine.shape(current.source, k);
    if (box == null) {
      return false;
    }
    engine.fit(box);
    return true;
  }

  export function fitTo(areaIds: Array<string>) {
    const box = {
      west: Infinity,
      south: Infinity,
      east: -Infinity,
      north: -Infinity,
    };
    for (const areaId of areaIds) {
      const k = kOf.get(areaId);
      const part =
        current == null || k == null ? null : engine.shape(current.source, k);
      if (part != null) {
        box.west = Math.min(box.west, part.west);
        box.south = Math.min(box.south, part.south);
        box.east = Math.max(box.east, part.east);
        box.north = Math.max(box.north, part.north);
      }
    }
    if (!Number.isFinite(box.west)) {
      return false;
    }
    engine.fit(box);
    return true;
  }

  export function zoomIn() {
    engine.zoomIn();
  }

  export function zoomOut() {
    engine.zoomOut();
  }

  export function reset() {
    engine.reset();
  }

  export function isLoaded() {
    return engine.isLoaded;
  }

  const rings = $derived.by(() => {
    void moved;
    if (engine.gl == null) {
      return [];
    }
    return pulses.flatMap((pulse) => {
      const at = atOf.get(pulse.areaId);
      const point = at == null ? null : engine.project(at);
      return point == null ||
        point.x < 0 ||
        point.y < 0 ||
        point.x > width ||
        point.y > height
        ? []
        : [{ ...pulse, x: point.x, y: point.y }];
    });
  });
  const tipLeft = $derived(
    pointer == null
      ? 0
      : pointer.x + 18 + tipWidth > width
        ? Math.max(8, pointer.x - 18 - tipWidth)
        : pointer.x + 18,
  );
  const tipTop = $derived(
    pointer == null
      ? 0
      : Math.min(
          Math.max(8, pointer.y - tipHeight / 2),
          Math.max(8, height - tipHeight - 8),
        ),
  );
</script>

<div
  class="relative overflow-hidden bg-card block-full inline-full"
  bind:clientWidth={width}
  bind:clientHeight={height}
>
  <div
    class="transition-opacity duration-500 ease-out block-full inline-full motion-reduce:transition-none"
    class:opacity-0={!engine.isLoaded}
    role="img"
    aria-label={label}
    {@attach engine.mount}
  ></div>
  {#each rings as ring (ring.key)}
    <span
      class="pointer-events-none absolute z-10 -translate-1/2 rounded-full block-3 inline-3"
      style:inset-inline-start="{ring.x}px"
      style:inset-block-start="{ring.y}px"
      aria-hidden="true"
    >
      <span
        class="absolute inset-0 animate-[full-ping_1.6s_ease-out_3] rounded-full ring-2 motion-reduce:animate-none"
        style:--tw-ring-color={ring.color}
      ></span>
      <span
        class="absolute inset-0.75 rounded-full ring-2 ring-card"
        style:background-color={ring.color}
      ></span>
    </span>
  {/each}
  {#if pointer != null && hovered != null}
    <div
      class="pointer-events-none absolute z-30 grid gap-1.5 rounded-sm bg-popover/95 px-3 py-2.5 text-[0.8125rem] shadow-menu ring-1 ring-border backdrop-blur-sm inline-60"
      style:inset-inline-start="{tipLeft}px"
      style:inset-block-start="{tipTop}px"
      bind:offsetWidth={tipWidth}
      bind:offsetHeight={tipHeight}
    >
      {@render tip(hovered)}
    </div>
  {/if}
</div>

<style>
  @keyframes -global-full-ping {
    0% {
      transform: scale(1);
      opacity: 0.9;
    }
    100% {
      transform: scale(4.5);
      opacity: 0;
    }
  }
</style>
