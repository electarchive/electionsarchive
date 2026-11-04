<script lang="ts">
  import type { Snippet } from "svelte";
  import { untrack } from "svelte";
  import type { Geography, Pieces } from "#lib/live/standing.js";
  import { prefs } from "#lib/live/prefs.svelte.js";
  import { features } from "#lib/live/standing.js";
  import { basemap } from "#lib/map/basemap.js";
  import { Engine } from "#lib/map/engine.svelte.js";
  import { cn } from "#lib/utils.js";
  import type { Dot, Fill, Swing } from "./model.js";
  import { BLANK, GROUND } from "./model.js";

  let {
    geography,
    around = null,
    aim = null,
    seat = null,
    hold = null,
    fills,
    dots = [],
    swings = [],
    reach = 0.1,
    labels = [],
    selected = null,
    hovered = $bindable(null),
    pulses = [],
    inset = { top: 0, bottom: 0, end: 0 },
    isAlone = false,
    label,
    onPick,
    tip,
  }: {
    geography: Geography | null;
    around?: {
      tiles: string;
      places: Array<{
        areaId: string;
        k: number;
        at: [number, number];
        name: string;
      }>;
    } | null;
    aim?: Geography["frame"] | null;
    seat?: Pieces | null;
    hold?: { tiles: string; k: number; color: string } | null;
    fills: Map<string, Fill>;
    dots?: Array<Dot>;
    swings?: Array<Swing>;
    reach?: number;
    labels?: Array<{ areaId: string; text: string; rank: number }>;
    selected?: string | null;
    hovered?: string | null;
    pulses?: Array<{ key: string; areaId: string; color: string }>;
    inset?: { top: number; bottom: number; end: number };
    isAlone?: boolean;
    label: string;
    onPick: (areaId: string | null) => void;
    tip: Snippet<[string]>;
  } = $props();

  const places = $derived(geography?.places ?? []);
  const kOf = $derived(new Map(places.map((place) => [place.areaId, place.k])));
  const nearOf = $derived(
    new Map((around?.places ?? []).map((place) => [place.areaId, place.k])),
  );
  const areaOf = $derived(
    new Map(places.map((place) => [place.k, place.areaId])),
  );
  const nearAreaOf = $derived(
    new Map((around?.places ?? []).map((place) => [place.k, place.areaId])),
  );
  const atOf = $derived(
    new Map((seat?.pieces ?? places).map((place) => [place.areaId, place.at])),
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
    near: { source: string; fill: string } | null;
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
        right: edge + Math.min(inset.end, w * 0.2),
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
        const isOutside =
          seat == null &&
          hold != null &&
          engine.pick(point, ["hold-fill"]) == null;
        const own =
          seat != null ? "seat-fill" : isOutside ? null : current?.fill;
        const k = own == null ? null : engine.pick(point, [own]);
        const nearK =
          k != null || current?.near == null
            ? null
            : engine.pick(point, [current.near.fill]);
        const areaId =
          k != null
            ? (areaOf.get(k) ?? null)
            : nearK == null
              ? null
              : (nearAreaOf.get(nearK) ?? null);
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
          paint: { "fill-color": GROUND },
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
          "line-color": "var(--map-seam)",
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
    return { group, source, fill: `${group}-fill`, near: neighbors(group) };
  }

  function neighbors(group: string) {
    if (around == null || around.places.length === 0) {
      return null;
    }
    const source = engine.tiles(around.tiles);
    const filter: ["in", ["id"], ["literal", Array<number>]] = [
      "in",
      ["id"],
      ["literal", around.places.map((place) => place.k)],
    ];
    engine.add(
      {
        id: `${group}-near`,
        type: "fill",
        source,
        "source-layer": "areas",
        filter,
        paint: {
          "fill-color": "var(--foreground)",
          "fill-opacity": [
            "case",
            ["boolean", ["feature-state", "near"], false],
            0.07,
            0,
          ],
        },
      },
      "etch",
      { group, rank: 2 },
    );
    engine.add(
      {
        id: `${group}-near-edge`,
        type: "line",
        source,
        "source-layer": "areas",
        filter,
        layout: { "line-join": "round" },
        paint: {
          "line-color": "var(--foreground)",
          "line-width": 1.5,
          "line-dasharray": [3, 2],
          "line-opacity": [
            "case",
            ["boolean", ["feature-state", "near"], false],
            1,
            0,
          ],
        },
      },
      "focus",
      { group },
    );
    engine.add(
      {
        id: `${group}-near-names`,
        type: "symbol",
        source: engine.points(
          `${group}-near-names`,
          around.places.map((place) => ({
            at: place.at,
            properties: { name: place.name },
          })),
          group,
        ),
        layout: {
          "text-field": ["get", "name"],
          "text-font": ["Sofia Sans Semi Condensed SemiBold"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 4, 11, 8, 14],
          "text-max-width": 8,
          "text-padding": 6,
        },
        paint: {
          "text-color": "var(--muted-foreground)",
          "text-halo-color":
            "color-mix(in oklab, var(--background) 85%, transparent)",
          "text-halo-width": 1.4,
        },
      },
      "labels",
      { group },
    );
    return { source, fill: `${group}-near` };
  }

  function outline() {
    const held = seat == null ? hold : null;
    if (held == null) {
      engine.drop("hold");
    } else {
      const only: ["==", ["id"], number] = ["==", ["id"], held.k];
      const rest: ["!=", ["id"], number] = ["!=", ["id"], held.k];
      if (engine.has("hold-fill")) {
        engine.filter("hold-fill", only);
        engine.filter("hold-edge", only);
        engine.filter("hold-veil", rest);
        engine.style("hold-fill", "fill-color", held.color);
      } else {
        const tiles = engine.tiles(held.tiles);
        engine.add(
          {
            id: "hold-veil",
            type: "fill",
            source: tiles,
            "source-layer": "areas",
            filter: rest,
            paint: {
              "fill-color": "var(--background)",
              "fill-opacity": 0.5,
              "fill-antialias": false,
            },
          },
          "etch",
          { group: "hold", rank: 3 },
        );
        engine.add(
          {
            id: "hold-fill",
            type: "fill",
            source: tiles,
            "source-layer": "areas",
            filter: only,
            paint: { "fill-color": held.color },
          },
          "fills",
          { group: "hold", rank: 3 },
        );
        engine.add(
          {
            id: "hold-edge",
            type: "line",
            source: tiles,
            "source-layer": "areas",
            filter: only,
            layout: { "line-join": "round", "line-cap": "round" },
            paint: {
              "line-color": "var(--foreground)",
              "line-width": [
                "interpolate",
                ["linear"],
                ["zoom"],
                5,
                1.4,
                10,
                2.4,
              ],
            },
          },
          "focus",
          { group: "hold" },
        );
      }
    }
    if (seat == null) {
      engine.drop("seat");
      return;
    }
    engine.shapes("seat-pieces", features(seat), "seat");
    engine.shapes(
      "seat-line",
      { type: "MultiLineString", coordinates: seat.edge },
      "seat",
    );
    engine.shapes(
      "seat-veil",
      { type: "MultiPolygon", coordinates: seat.veil },
      "seat",
    );
    if (engine.has("seat-fill")) {
      return;
    }
    const pieces = { source: "seat-pieces" } as const;
    engine.add(
      {
        id: "seat-fill",
        type: "fill",
        ...pieces,
        paint: {
          "fill-color": ["coalesce", ["feature-state", "color"], BLANK],
          "fill-color-transition": { duration: 600 },
        },
      },
      "fills",
      { group: "seat", rank: 3 },
    );
    engine.add(
      {
        id: "seat-later",
        type: "fill",
        ...pieces,
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
      { group: "seat", rank: 4 },
    );
    engine.add(
      {
        id: "seat-veil",
        type: "fill",
        source: "seat-veil",
        paint: {
          "fill-color": "var(--background)",
          "fill-opacity": 0.5,
          "fill-antialias": false,
        },
      },
      "etch",
      { group: "seat", rank: 3 },
    );
    engine.add(
      {
        id: "seat-seam",
        type: "line",
        ...pieces,
        paint: {
          "line-color": "var(--map-seam)",
          "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.8, 10, 1.6],
        },
      },
      "lines",
      { group: "seat" },
    );
    engine.add(
      {
        id: "seat-focus",
        type: "line",
        ...pieces,
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
      { group: "seat" },
    );
    engine.add(
      {
        id: "seat-edge",
        type: "line",
        source: "seat-line",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: {
          "line-color": "var(--foreground)",
          "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1.4, 10, 2.4],
        },
      },
      "focus",
      { group: "seat" },
    );
  }

  function keyOf(shown: Geography) {
    return `${shown.tiles}|${shown.places.length}|${around?.tiles ?? ""}|${around?.places.map((place) => place.k).join() ?? ""}|${JSON.stringify(shown.frame)}`;
  }

  let aimed = "";
  let wasAlone = false;

  function draw() {
    const gl = engine.gl;
    if (gl == null || geography == null) {
      return;
    }
    const key = keyOf(geography);
    if (current?.key !== key) {
      const isMoved = current != null && !isAlone && !wasAlone;
      wasAlone = isAlone;
      if (current != null) {
        retired.push(current.group);
      }
      current = { key, ...layers(geography) };
      const fresh = current.source;
      let isDone = false;
      const swap = () => {
        if (isDone) {
          return;
        }
        isDone = true;
        gl.off("sourcedata", check);
        const gone = [...retired];
        for (const group of gone) {
          engine.fadeOut(group);
        }
        setTimeout(() => {
          for (const group of gone) {
            engine.drop(group);
          }
          retired = retired.filter((group) => !gone.includes(group));
        }, 450);
      };
      const check = () => {
        if (gl.getSource(fresh) == null || gl.isSourceLoaded(fresh)) {
          swap();
        }
      };
      gl.on("sourcedata", check);
      setTimeout(swap, 1500);
      aimed = JSON.stringify(aim);
      if (aim == null) {
        engine.frame(geography.frame, isMoved);
      } else {
        engine.home(geography.frame);
        engine.fit(aim, undefined, isMoved);
      }
    }
    outline();
    const { group } = current;
    const source = seat == null ? current.source : "seat-pieces";
    if (seat != null || hold != null) {
      engine.paint(
        current.source,
        new Map(places.map((place) => [place.k, { color: GROUND }])),
      );
    }
    engine.paint(
      source,
      new Map(
        (hold == null ? (seat?.pieces ?? places) : []).flatMap(
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
      "near",
      current.near?.source ?? null,
      hovered == null ? null : (nearOf.get(hovered) ?? null),
    );
    engine.mark(
      "pinned",
      source,
      selected == null ? null : (kOf.get(selected) ?? null),
    );
  }

  $effect(() => {
    void [
      geography,
      around,
      seat,
      hold,
      fills,
      dots,
      labels,
      selected,
      hovered,
      engine.theme,
    ];
    if (engine.gl != null) {
      untrack(draw);
    }
  });

  $effect(() => {
    const items = swings.flatMap((entry) => {
      const at = atOf.get(entry.areaId);
      return at == null
        ? []
        : [
            {
              at,
              swing: entry.swing,
              color: entry.color,
              weight: entry.weight,
              isMuted: selected != null && selected !== entry.areaId,
            },
          ];
    });
    const size = reach;
    if (engine.gl != null) {
      untrack(() => engine.arrows(items, size));
    }
  });

  $effect(() => {
    const target = aim;
    const next = JSON.stringify(target);
    if (engine.gl == null || geography == null || next === aimed) {
      return;
    }
    aimed = next;
    const shown = geography;
    untrack(() => {
      if (current?.key !== keyOf(shown)) {
        return;
      }
      if (target == null) {
        engine.reset();
      } else {
        engine.fit(target);
      }
    });
  });

  $effect(() => {
    if (engine.gl == null || geography == null) {
      return;
    }
    const scheme = geography.scheme;
    const outline = geography.outline;
    const isDetailed = prefs.isBasemap;
    const isShownAlone = isAlone;
    untrack(() =>
      basemap(engine, scheme, isDetailed, outline, false, isShownAlone),
    );
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

  export function fly(box: Geography["frame"], isHome: boolean) {
    if (isHome) {
      engine.frame(box, true);
    } else {
      engine.fit(box);
    }
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
  class={cn("relative overflow-hidden bg-card block-full inline-full", {
    "animate-pulse bg-muted motion-reduce:animate-none": !engine.isLoaded,
  })}
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
