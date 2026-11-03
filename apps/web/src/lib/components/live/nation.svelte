<script lang="ts">
  import type { Snippet } from "svelte";
  import { untrack } from "svelte";
  import type { Geography } from "#lib/live/standing.js";
  import { Engine } from "#lib/map/engine.svelte.js";

  let {
    geography,
    codes,
    colors,
    later = [],
    label,
    hovered = $bindable(null),
    onPick,
    tip,
  }: {
    geography: Geography;
    codes: Record<string, string>;
    colors: Record<string, string>;
    later?: Array<string>;
    label: string;
    hovered?: string | null;
    onPick: (code: string) => void;
    tip: Snippet<[string]>;
  } = $props();

  const codeOf = $derived(
    new Map(Object.entries(codes).map(([code, area]) => [area, code])),
  );
  const byK = $derived(
    new Map(
      geography.places.flatMap((place) => {
        const code = codeOf.get(place.areaId);
        return code == null ? [] : [[place.k, code] as const];
      }),
    ),
  );
  const kOf = $derived(new Map([...byK].map(([k, code]) => [code, k])));

  let pointer = $state<{ x: number; y: number } | null>(null);
  let painted = false;

  const engine = new Engine(
    untrack(() => geography.frame),
    (width, height) => {
      const edge = Math.max(8, Math.round(Math.min(width, height) * 0.03));
      return { top: edge, right: edge, bottom: edge, left: edge };
    },
    {
      onReady: () => {
        const gl = engine.gl;
        if (gl != null) {
          gl.scrollZoom.disable();
          gl.dragPan.disable();
          gl.doubleClickZoom.disable();
          gl.boxZoom.disable();
          gl.touchZoomRotate.disable();
        }
        untrack(draw);
      },
      onPointer: ({ kind, point, isTouch }) => {
        if (kind === "leave") {
          hovered = null;
          pointer = null;
          engine.cursor(false);
          return;
        }
        const k = engine.pick(point, ["nation-fill"]);
        const code = k == null ? null : (byK.get(k) ?? null);
        if (kind === "click") {
          if (code != null) {
            onPick(code);
          }
          return;
        }
        if (isTouch) {
          return;
        }
        hovered = code;
        pointer = code == null ? null : point;
        engine.cursor(code != null);
      },
    },
  );

  function draw() {
    if (engine.gl == null) {
      return;
    }
    const source = engine.tiles(geography.tiles, "nation");
    if (!painted) {
      const ks = [...byK.keys()];
      const ground = (geography.ground ?? []).filter((k) => !byK.has(k));
      if (ground.length > 0) {
        engine.add(
          {
            id: "nation-ground",
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
          { group: "nation-layers", rank: 0 },
        );
        engine.add(
          {
            id: "nation-ground-seam",
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
          { group: "nation-layers" },
        );
      }
      engine.add(
        {
          id: "nation-fill",
          type: "fill",
          source,
          "source-layer": "areas",
          filter: ["in", ["id"], ["literal", ks]],
          paint: {
            "fill-color": [
              "coalesce",
              ["feature-state", "color"],
              "color-mix(in oklab, var(--muted-foreground) 9%, var(--background))",
            ],
            "fill-color-transition": { duration: 700 },
          },
        },
        "fills",
        { group: "nation-layers", rank: 0 },
      );
      engine.add(
        {
          id: "nation-later",
          type: "fill",
          source,
          "source-layer": "areas",
          filter: ["in", ["id"], ["literal", ks]],
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
        { group: "nation-layers", rank: 1 },
      );
      engine.add(
        {
          id: "nation-seam",
          type: "line",
          source,
          "source-layer": "areas",
          filter: ["in", ["id"], ["literal", ks]],
          paint: { "line-color": "var(--map-seam)", "line-width": 1.1 },
        },
        "lines",
        { group: "nation-layers" },
      );
      engine.add(
        {
          id: "nation-focus",
          type: "line",
          source,
          "source-layer": "areas",
          filter: ["in", ["id"], ["literal", ks]],
          paint: {
            "line-color": "var(--foreground)",
            "line-width": [
              "case",
              ["boolean", ["feature-state", "hover"], false],
              2,
              0,
            ],
          },
        },
        "focus",
        { group: "nation-layers" },
      );
      engine.frame(geography.frame);
      painted = true;
    }
    engine.paint(
      source,
      new Map(
        [...byK].flatMap(([k, code]) => {
          const color = colors[code];
          return color == null
            ? []
            : [[k, { color, later: later.includes(code) }] as const];
        }),
      ),
    );
    engine.mark(
      "hover",
      source,
      hovered == null ? null : (kOf.get(hovered) ?? null),
    );
  }

  $effect(() => {
    void [colors, later, hovered, engine.theme];
    if (engine.isLoaded) {
      untrack(draw);
    }
  });
</script>

<div class="relative">
  <div
    class="aspect-[1.6] inline-full [&_.maplibregl-canvas-container]:cursor-default!"
    role="img"
    aria-label={label}
    {@attach engine.mount}
  ></div>
  {#if hovered != null && pointer != null}
    <div
      class="pointer-events-none absolute z-10 grid gap-1 rounded-sm bg-popover/95 px-3 py-2 text-xs shadow-menu ring-1 ring-border min-inline-48"
      style:left="{pointer.x + 14}px"
      style:top="{pointer.y + 14}px"
    >
      {@render tip(hovered)}
    </div>
  {/if}
</div>
