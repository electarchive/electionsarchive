<script lang="ts">
  import type * as Maplibre from "maplibre-gl";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import MinusIcon from "@lucide/svelte/icons/minus";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import ScanIcon from "@lucide/svelte/icons/scan";
  import { mode } from "mode-watcher";
  import { untrack } from "svelte";
  import { on } from "svelte/events";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  type Places = NonNullable<
    Extract<PageData, { kind: "contest" }>["contest"]["places"]
  >;

  let {
    places,
    colors,
    tiles,
    own,
    frames,
    isApproximate,
    noun,
    votes,
    only,
    focus = $bindable(null),
  }: {
    places: Places;
    colors: Array<string>;
    tiles: string;
    own: { tiles: string; k: number } | null;
    frames: Array<{ west: number; south: number; east: number; north: number }>;
    isApproximate: boolean;
    noun: string;
    votes: string;
    only: number | string | null;
    focus?: number | null;
  } = $props();

  const BINS = [0.05, 0.1, 0.2];
  const STRENGTHS = [34, 54, 76, 100];

  const uid = $props.id();
  let maps = $state<Array<Maplibre.Map>>([]);
  let isLoaded = $state(false);
  let pinned = $state<number | null>(null);

  const rows = $derived(new Map(places.rows.map((row) => [row.k, row])));
  const order = $derived(
    places.rows
      .toSorted((a, b) =>
        a.name.localeCompare(b.name, undefined, { numeric: true }),
      )
      .map((row) => row.k),
  );
  const ratio = $derived.by(() => {
    const frame = frames[0];
    if (frame == null) {
      return 4 / 3;
    }
    const y = (latitude: number) =>
      Math.log(Math.tan(Math.PI / 4 + (latitude * Math.PI) / 360));
    const wide = ((frame.east - frame.west) * Math.PI) / 180;
    const tall = y(frame.north) - y(frame.south);
    return Math.min(2, Math.max(0.8, (wide / Math.max(tall, 0.0001)) * 1.04));
  });
  const current = $derived(focus == null ? null : (rows.get(focus) ?? null));
  const ranked = $derived(
    current == null
      ? []
      : current.shares
          .map((share, index) => ({ share, index }))
          .filter((entry) => entry.share > 0)
          .toSorted((a, b) => b.share - a.share)
          .slice(0, 3),
  );

  function bin(margin: number | null) {
    const value = margin ?? 0;
    let index = 0;
    for (const edge of BINS) {
      if (value >= edge) {
        index += 1;
      }
    }
    return index;
  }

  function open(k: number) {
    const path = rows.get(k)?.path;
    if (path != null) {
      void goto(resolve("/[...path]", { path }));
    }
  }

  function paint(host: HTMLElement) {
    const probe = document.createElement("span");
    host.append(probe);
    const context = document
      .createElement("canvas")
      .getContext("2d", { willReadFrequently: true });
    const cache: Record<string, string> = {};
    const read = (css: string) => {
      const known = cache[css];
      if (known != null) {
        return known;
      }
      probe.style.color = css;
      const computed = getComputedStyle(probe).color;
      let value = computed;
      if (context != null) {
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = computed;
        context.fillRect(0, 0, 1, 1);
        const [r = 0, g = 0, b = 0, a = 255] = context.getImageData(
          0,
          0,
          1,
          1,
        ).data;
        value = `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
      }
      cache[css] = value;
      return value;
    };
    return { read, done: () => probe.remove() };
  }

  function style(read: (css: string) => string) {
    return {
      paper: read("var(--card)"),
      ground: read(
        "color-mix(in oklab, var(--muted-foreground) 14%, var(--card))",
      ),
      seam: read("var(--card)"),
      ink: read("var(--foreground)"),
      hatch: read("color-mix(in oklab, var(--foreground) 32%, transparent)"),
    };
  }

  function fills(read: (css: string) => string) {
    const result: Array<[number, string]> = [];
    for (const row of places.rows) {
      if (row.leader == null) {
        continue;
      }
      const dimmed =
        only != null && typeof only === "number" && only !== row.leader;
      const strength = dimmed ? 10 : (STRENGTHS[bin(row.margin)] ?? 100);
      result.push([
        row.k,
        read(
          `color-mix(in oklab, ${colors[row.leader] ?? "var(--muted-foreground)"} ${strength}%, var(--card))`,
        ),
      ]);
    }
    return result;
  }

  const keys = (node: HTMLElement) => {
    node.tabIndex = 0;
    const offKey = on(node, "keydown", (event) => {
      if (event.target !== node) {
        return;
      }
      if (event.key === "Enter" && focus != null) {
        event.preventDefault();
        open(focus);
        return;
      }
      const step =
        event.key === "ArrowRight" || event.key === "ArrowDown"
          ? 1
          : event.key === "ArrowLeft" || event.key === "ArrowUp"
            ? -1
            : 0;
      if (step === 0 || order.length === 0) {
        if (event.key === "Escape") {
          focus = null;
          pinned = null;
        }
        return;
      }
      event.preventDefault();
      const at = focus == null ? -1 : order.indexOf(focus);
      const next =
        order[(at + step + order.length) % order.length] ?? order[0] ?? null;
      pinned = next;
      focus = next;
    });
    const offBlur = on(node, "blur", () => {
      pinned = null;
    });
    return () => {
      offKey();
      offBlur();
    };
  };

  const mount = (host: HTMLElement) => {
    const stop = new AbortController();
    const built: Array<Maplibre.Map> = [];
    const cleanup: Array<() => void> = [];
    void (async () => {
      const [maplibre, { Protocol }, worker] = await Promise.all([
        import("maplibre-gl"),
        import("pmtiles"),
        import("maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url"),
        import("maplibre-gl/dist/maplibre-gl.css"),
      ]);
      if (stop.signal.aborted) {
        return;
      }
      maplibre.setWorkerUrl(worker.default);
      const protocol = new Protocol();
      maplibre.addProtocol("pmtiles", protocol.tile);
      const tones = paint(host);
      cleanup.push(tones.done);
      const colorsNow = style(tones.read);
      const origin = globalThis.location.origin;
      const hatch = document.createElement("canvas");
      hatch.width = 8;
      hatch.height = 8;
      const pen = hatch.getContext("2d");
      if (pen != null) {
        pen.strokeStyle = colorsNow.hatch;
        pen.lineWidth = 1;
        pen.beginPath();
        pen.moveTo(0, 8);
        pen.lineTo(8, 0);
        pen.stroke();
      }
      const surfaces = [
        ...host.querySelectorAll<HTMLElement>("[data-map-surface]"),
      ];
      for (const [index, surface] of surfaces.entries()) {
        const frame = frames[index];
        const map = new maplibre.Map({
          container: surface,
          style: {
            version: 8,
            sources: {
              places: {
                type: "vector",
                url: `pmtiles://${origin}/tiles/${tiles}.pmtiles`,
              },
              ...(own == null
                ? {}
                : {
                    own: {
                      type: "vector" as const,
                      url: `pmtiles://${origin}/tiles/${own.tiles}.pmtiles`,
                    },
                  }),
            },
            layers: [
              {
                id: "paper",
                type: "background",
                paint: { "background-color": colorsNow.paper },
              },
              {
                id: "fill",
                type: "fill",
                source: "places",
                "source-layer": "areas",
                paint: {
                  "fill-color": [
                    "coalesce",
                    ["feature-state", "color"],
                    colorsNow.ground,
                  ],
                },
              },
              ...(isApproximate
                ? [
                    {
                      id: "hatch",
                      type: "fill" as const,
                      source: "places",
                      "source-layer": "areas",
                      paint: { "fill-pattern": "hatch" },
                    },
                  ]
                : []),
              {
                id: "seam",
                type: "line",
                source: "places",
                "source-layer": "areas",
                paint: {
                  "line-color": colorsNow.seam,
                  "line-width": [
                    "interpolate",
                    ["linear"],
                    ["zoom"],
                    2,
                    0.6,
                    6,
                    1,
                    10,
                    1.6,
                  ],
                },
              },
              ...(own == null
                ? []
                : [
                    {
                      id: "outline",
                      type: "line" as const,
                      source: "own",
                      "source-layer": "areas",
                      filter: [
                        "==",
                        ["id"],
                        own.k,
                      ] as Maplibre.FilterSpecification,
                      paint: {
                        "line-color": colorsNow.ink,
                        "line-width": 1.75,
                      },
                    },
                  ]),
              {
                id: "focus",
                type: "line",
                source: "places",
                "source-layer": "areas",
                paint: {
                  "line-color": colorsNow.ink,
                  "line-width": 2.25,
                  "line-opacity": [
                    "case",
                    ["boolean", ["feature-state", "focus"], false],
                    1,
                    0,
                  ],
                },
              },
            ],
          },
          bounds:
            frame == null
              ? undefined
              : [frame.west, frame.south, frame.east, frame.north],
          fitBoundsOptions: { padding: index === 0 ? 24 : 6 },
          renderWorldCopies: false,
          attributionControl: false,
          interactive: index === 0,
          cooperativeGestures: index === 0,
          dragRotate: false,
          pitchWithRotate: false,
          touchPitch: false,
          locale: {
            "CooperativeGesturesHandler.WindowsHelpText":
              m.map_gesture_windows(),
            "CooperativeGesturesHandler.MacHelpText": m.map_gesture_mac(),
            "CooperativeGesturesHandler.MobileHelpText": m.map_gesture_mobile(),
          },
        });
        map.touchZoomRotate.disableRotation();
        map.keyboard.disable();
        map.addImage("hatch", {
          width: 8,
          height: 8,
          data: new Uint8Array(
            pen?.getImageData(0, 0, 8, 8).data.buffer ?? new ArrayBuffer(256),
          ),
        });
        map.on("mousemove", "fill", (event) => {
          const id = event.features?.[0]?.id;
          const k = typeof id === "number" && rows.has(id) ? id : null;
          map.getCanvas().style.cursor =
            k != null && rows.get(k)?.path != null ? "pointer" : "";
          if (pinned == null) {
            focus = k;
          }
        });
        map.on("mouseleave", "fill", () => {
          map.getCanvas().style.cursor = "";
          if (pinned == null) {
            focus = null;
          }
        });
        map.on("click", (event) => {
          const [feature] = map.queryRenderedFeatures(event.point, {
            layers: ["fill"],
          });
          const k =
            typeof feature?.id === "number" && rows.has(feature.id)
              ? feature.id
              : null;
          const isTouch =
            (event.originalEvent as PointerEvent).pointerType === "touch";
          if (k == null) {
            pinned = null;
            focus = null;
          } else if (isTouch && pinned !== k) {
            pinned = k;
            focus = k;
          } else {
            open(k);
          }
        });
        map.on("load", () => {
          if (index === 0) {
            isLoaded = true;
          }
        });
        built.push(map);
      }
      maps = built;
    })();
    return () => {
      stop.abort();
      for (const map of built) {
        map.remove();
      }
      for (const done of cleanup) {
        done();
      }
      maps = [];
      isLoaded = false;
    };
  };

  $effect(() => {
    const theme = mode.current;
    const host = maps[0]?.getContainer().closest<HTMLElement>("[data-map]");
    if (host == null || theme == null) {
      return;
    }
    const tones = paint(host);
    const colorsNow = style(tones.read);
    const painted = fills(tones.read);
    tones.done();
    for (const map of maps) {
      const apply = () => {
        map.setPaintProperty("paper", "background-color", colorsNow.paper);
        map.setPaintProperty("fill", "fill-color", [
          "coalesce",
          ["feature-state", "color"],
          colorsNow.ground,
        ]);
        map.setPaintProperty("seam", "line-color", colorsNow.seam);
        map.setPaintProperty("focus", "line-color", colorsNow.ink);
        if (map.getLayer("outline") != null) {
          map.setPaintProperty("outline", "line-color", colorsNow.ink);
        }
        map.removeFeatureState({ source: "places", sourceLayer: "areas" });
        for (const [k, color] of painted) {
          map.setFeatureState(
            { source: "places", sourceLayer: "areas", id: k },
            { color },
          );
        }
        const k = untrack(() => focus);
        if (k != null) {
          map.setFeatureState(
            { source: "places", sourceLayer: "areas", id: k },
            { focus: true },
          );
        }
      };
      if (map.isStyleLoaded()) {
        apply();
      } else {
        map.once("load", apply);
      }
    }
  });

  let shown: number | null = null;
  $effect(() => {
    const k = focus;
    for (const map of maps) {
      const apply = () => {
        if (shown != null) {
          map.setFeatureState(
            { source: "places", sourceLayer: "areas", id: shown },
            { focus: false },
          );
        }
        if (k != null) {
          map.setFeatureState(
            { source: "places", sourceLayer: "areas", id: k },
            { focus: true },
          );
        }
      };
      if (map.isStyleLoaded()) {
        apply();
      } else {
        map.once("load", apply);
      }
    }
    shown = k;
  });
</script>

<div
  class="relative grid gap-2"
  data-map
  role="application"
  aria-roledescription="map"
  aria-label={m.map_label({ noun })}
  aria-describedby="{uid}-hint"
  {@attach keys}
>
  <p id="{uid}-hint" class="sr-only">{m.map_hint()}</p>
  <div
    class="relative overflow-hidden rounded-sm bg-card ring-1 ring-border inline-full max-block-[min(38rem,70svh)]"
    style:aspect-ratio={ratio}
    {@attach mount}
  >
    <div class="block-full inline-full" data-map-surface></div>
    {#if !isLoaded}
      <p
        class="absolute inset-0 grid place-items-center text-[0.8125rem] text-muted-foreground"
      >
        {m.map_loading()}
      </p>
    {/if}
    {#if frames.length > 1}
      <div class="pointer-events-none absolute inset-s-2 inset-be-2 flex gap-2">
        {#each frames.slice(1) as _frame, index (index)}
          <div
            class="pointer-events-auto relative overflow-hidden rounded-xs bg-card ring-1 ring-border block-14 inline-18 sm:block-20 sm:inline-28"
          >
            <div class="block-full inline-full" data-map-surface></div>
          </div>
        {/each}
      </div>
    {/if}
    <div
      class="absolute inset-e-2 inset-bs-2 flex flex-col overflow-hidden rounded-sm bg-card shadow-menu ring-1 ring-border"
    >
      <button
        type="button"
        class="grid place-items-center block-8 inline-8 hover:bg-secondary"
        aria-label={m.map_zoom_in()}
        title={m.map_zoom_in()}
        onclick={() => maps[0]?.zoomIn()}
      >
        <PlusIcon class="block-4 inline-4" />
      </button>
      <button
        type="button"
        class="grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary"
        aria-label={m.map_zoom_out()}
        title={m.map_zoom_out()}
        onclick={() => maps[0]?.zoomOut()}
      >
        <MinusIcon class="block-4 inline-4" />
      </button>
      <button
        type="button"
        class="grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary"
        aria-label={m.map_reset()}
        title={m.map_reset()}
        onclick={() => {
          const frame = frames[0];
          if (frame != null) {
            maps[0]?.fitBounds(
              [frame.west, frame.south, frame.east, frame.north],
              { padding: 24 },
            );
          }
        }}
      >
        <ScanIcon class="block-4 inline-4" />
      </button>
    </div>
    {#if current != null}
      <div
        class="pointer-events-none absolute inset-s-2 inset-bs-2 grid gap-1.5 rounded-sm bg-card/95 px-3 py-2.5 shadow-menu ring-1 ring-border inline-[min(17rem,calc(100%-4.5rem))] max-sm:hidden"
        aria-live="polite"
      >
        {@render readout()}
      </div>
    {/if}
  </div>

  <div
    class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-[0.8125rem]"
  >
    <div class="flex items-center gap-2.5" aria-hidden="true">
      <span class="text-muted-foreground">{m.map_margin()}</span>
      <span class="flex items-center gap-1.5 font-mono text-xs tabular-nums">
        <span>{m.map_margin_under({ points: "5" })}</span>
        <span class="flex">
          {#each STRENGTHS as strength (strength)}
            <span
              class="block-3 inline-6 first:rounded-s-xs last:rounded-e-xs"
              style:background-color="color-mix(in oklab, var(--foreground) {strength *
                0.85}%, var(--card))"
            ></span>
          {/each}
        </span>
        <span>{m.map_margin_over({ points: "20" })}</span>
      </span>
    </div>
    {#if isApproximate}
      <span class="flex items-center gap-1.5 text-muted-foreground">
        <span
          class="rounded-xs bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_4px)] ring-1 ring-border block-3 inline-4"
        ></span>
        {m.boundaries_approximate()}
      </span>
    {/if}
  </div>

  <div class="sm:hidden" aria-live="polite">
    {#if current != null}
      <div class="grid gap-1.5 rounded-sm p-1">
        {@render readout()}
      </div>
    {:else}
      <p class="px-1 text-[0.8125rem] text-muted-foreground">{m.map_pick()}</p>
    {/if}
  </div>
</div>

{#snippet readout()}
  {#if current != null}
    <span class="flex items-baseline justify-between gap-3">
      <span class="font-semibold text-pretty">{current.name}</span>
      {#if current.margin != null}
        <span
          class="shrink-0 font-mono text-xs text-muted-foreground tabular-nums"
          >{points(current.margin)}</span
        >
      {/if}
    </span>
    <span class="grid gap-1">
      {#each ranked as entry (entry.index)}
        {const option = $derived(places.legend[entry.index])}
        <span
          class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2.5 gap-y-0.5 text-[0.8125rem]"
          style:--party={colors[entry.index]}
        >
          <span
            class={cn("truncate", {
              "font-semibold": entry.index === current.leader,
            })}>{option?.short ?? option?.label}</span
          >
          <span class="font-mono text-xs tabular-nums"
            >{percent(entry.share)}</span
          >
          <span class="col-span-2 rounded-xs bg-muted block-1.5">
            <span
              class="block rounded-xs bg-(--party) block-full"
              style:inline-size="{entry.share * 100}%"
            ></span>
          </span>
        </span>
      {/each}
    </span>
    <span class="flex items-baseline justify-between gap-3 text-xs">
      <span class="text-muted-foreground"
        >{votes}
        <span class="font-mono tabular-nums">{count(current.valid)}</span></span
      >
      {#if current.path != null}
        <a
          class="pointer-events-auto font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={resolve("/[...path]", { path: current.path })}
          >{m.map_open({ place: current.name })}</a
        >
      {/if}
    </span>
  {/if}
{/snippet}
