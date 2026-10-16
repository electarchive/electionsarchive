<script lang="ts">
  import type * as Maplibre from "maplibre-gl";
  import { goto, preloadData } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import MinusIcon from "@lucide/svelte/icons/minus";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import ScanIcon from "@lucide/svelte/icons/scan";
  import XIcon from "@lucide/svelte/icons/x";
  import { mode } from "mode-watcher";
  import { untrack } from "svelte";
  import { on } from "svelte/events";
  import { fly } from "svelte/transition";
  import Export from "#lib/components/export.svelte";
  import { count, percent, points } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
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
    names,
    levels,
    span,
    only = $bindable(null),
    focus = $bindable(null),
    shade = "winner",
    isLinked = false,
    pick,
  }: {
    places: Places;
    colors: Array<string>;
    tiles: string;
    own: { tiles: string; k: number } | null;
    frames: Array<{ west: number; south: number; east: number; north: number }>;
    isApproximate: boolean;
    noun: string;
    votes: string;
    names: Array<string>;
    levels: Array<{
      code: string;
      label: string;
      count: number;
      path: string;
      query: string;
      isCurrent: boolean;
    }>;
    span: { from: string; to: string; forward: boolean } | null;
    only?: number | string | null;
    focus?: number | null;
    shade?: "winner" | number;
    isLinked?: boolean;
    pick?: (code: string) => void;
  } = $props();

  const BINS = [0.05, 0.1, 0.2];
  const STRENGTHS = [34, 54, 76, 100];
  const STEPS = [16, 36, 56, 78, 100];
  const SWINGS = [0.02, 0.05, 0.1];
  const COMPARED = new Set(["swing", "arrows", "flips"]);
  const MODES = ["winner", "bubbles", "swing", "arrows", "flips"] as const;

  const uid = $props.id();
  let maps = $state<Array<Maplibre.Map>>([]);
  let isLoaded = $state(false);
  let credits = $state<Array<string>>([]);
  let pinned = $state<number | null>(null);
  let grab = $state<number | null>(null);
  let drag = $state(0);
  const calm =
    typeof matchMedia !== "undefined" &&
    matchMedia("(prefers-reduced-motion: reduce)").matches;
  let shading = $state<
    "winner" | "bubbles" | "swing" | "arrows" | "flips" | number
  >(
    untrack(() => {
      const asked = isLinked ? page.url.searchParams.get("shade") : null;
      if (asked == null) {
        return shade;
      }
      return (
        MODES.find((choice) => choice === asked) ??
        (/^\d+$/.test(asked) ? Number(asked) : shade)
      );
    }),
  );

  $effect(() => {
    if (isLinked) {
      keep({ shade: shading === "winner" ? null : String(shading) });
    }
  });
  let fit: Maplibre.PaddingOptions = {
    top: 24,
    right: 24,
    bottom: 24,
    left: 24,
  };

  const rows = $derived(new Map(places.rows.map((row) => [row.k, row])));
  const isCompared = $derived(
    span != null && places.rows.some((row) => row.earlier != null),
  );
  const changes = $derived(
    new Map(
      places.rows.flatMap((row) => {
        if (row.earlier == null) {
          return [];
        }
        const isForward = span?.forward === true;
        const before = isForward ? row.shares : row.earlier;
        const after = isForward ? row.earlier : row.shares;
        return [
          [
            row.k,
            {
              swing:
                (after[0] ?? 0) -
                (after[1] ?? 0) -
                ((before[0] ?? 0) - (before[1] ?? 0)),
              from: isForward ? row.leader : row.prior,
              to: isForward ? row.prior : row.leader,
              before,
              after,
            },
          ] as const,
        ];
      }),
    ),
  );
  const view = $derived(
    !isCompared && typeof shading === "string" && COMPARED.has(shading)
      ? "winner"
      : shading,
  );
  const flips = $derived(
    Map.groupBy(
      changes
        .values()
        .filter(
          (change) =>
            change.from != null &&
            change.to != null &&
            change.from !== change.to,
        ),
      (change) => `${change.from}>${change.to}`,
    )
      .entries()
      .map(
        ([key, won]) =>
          [key, won[0]?.from ?? 0, won[0]?.to ?? 0, won.length] as const,
      )
      .toArray()
      .toSorted((a, b) => b[3] - a[3]),
  );
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
  const leads = $derived(
    Map.groupBy(
      places.rows.filter((row) => row.leader != null),
      (row) => row.leader ?? 0,
    )
      .entries()
      .map(([index, won]) => [index, won.length] as const)
      .toArray()
      .toSorted((a, b) => b[1] - a[1]),
  );
  const options = $derived(
    places.legend
      .map((item, index) => ({ index, name: names[index] ?? item.label }))
      .slice(0, Math.min(3, places.legend.length))
      .filter((option) =>
        places.rows.some((row) => (row.shares[option.index] ?? 0) > 0),
      ),
  );
  const edges = $derived.by(() => {
    if (typeof view !== "number") {
      return [];
    }
    const index = view;
    const sorted = places.rows
      .map((row) => row.shares[index] ?? 0)
      .toSorted((a, b) => a - b);
    const cuts = [0.2, 0.4, 0.6, 0.8].map(
      (at) => sorted[Math.floor(at * (sorted.length - 1))] ?? 0,
    );
    return [...new Set(cuts)];
  });
  const dots = $derived({
    type: "FeatureCollection" as const,
    features: places.rows.flatMap((row) =>
      row.at == null
        ? []
        : [
            {
              type: "Feature" as const,
              id: row.k,
              properties: {},
              geometry: { type: "Point" as const, coordinates: row.at },
            },
          ],
    ),
  });
  const biggest = $derived(Math.max(1, ...places.rows.map((row) => row.valid)));
  const isDeclared = $derived(places.rows.every((row) => row.valid === 0));
  const current = $derived(focus == null ? null : (rows.get(focus) ?? null));
  const moved = $derived(
    focus == null || !isCompared ? null : (changes.get(focus) ?? null),
  );
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
    if (path == null) {
      return;
    }
    const href = resolve("/[...path]", { path });
    const map = maps[0];
    const parts =
      map?.querySourceFeatures("places", {
        sourceLayer: "areas",
        filter: ["==", ["id"], k],
      }) ?? [];
    const box = [Infinity, Infinity, -Infinity, -Infinity];
    for (const part of parts) {
      for (const pair of JSON.stringify(part.geometry).matchAll(
        /\[([\d.e-]+),([\d.e-]+)\]/g,
      )) {
        const x = Number(pair[1]);
        const y = Number(pair[2]);
        box[0] = Math.min(box[0] ?? x, x);
        box[1] = Math.min(box[1] ?? y, y);
        box[2] = Math.max(box[2] ?? x, x);
        box[3] = Math.max(box[3] ?? y, y);
      }
    }
    if (
      map == null ||
      !Number.isFinite(box[0]) ||
      matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      void goto(href);
      return;
    }
    const loading = preloadData(href);
    let isGone = false;
    const leave = () => {
      if (!isGone) {
        isGone = true;
        void loading.finally(() => void goto(href));
      }
    };
    map.once("moveend", leave);
    setTimeout(leave, 1200);
    map.fitBounds(
      [
        [box[0] ?? 0, box[1] ?? 0],
        [box[2] ?? 0, box[3] ?? 0],
      ],
      { padding: fit, duration: 650, essential: false },
    );
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
      let strength = dimmed
        ? 10
        : isDeclared
          ? 100
          : (STRENGTHS[bin(row.margin)] ?? 100);
      let tone = colors[row.leader] ?? "var(--muted-foreground)";
      if (view === "bubbles") {
        strength = dimmed ? 18 : 100;
      }
      if (view === "arrows") {
        continue;
      }
      if (view === "swing" || view === "flips") {
        const change = changes.get(row.k);
        if (change == null) {
          continue;
        }
        const isFlip =
          change.from != null && change.to != null && change.from !== change.to;
        let size = 0;
        for (const edge of SWINGS) {
          if (Math.abs(change.swing) >= edge) {
            size += 1;
          }
        }
        strength =
          view === "flips"
            ? isFlip &&
              (only == null ||
                only === "flip" ||
                only === change.to ||
                only === `${change.from}>${change.to}`)
              ? 100
              : 13
            : (STRENGTHS[size] ?? 100);
        tone =
          colors[
            view === "flips"
              ? (change.to ?? row.leader)
              : change.swing >= 0
                ? 0
                : 1
          ] ?? "var(--muted-foreground)";
      }
      if (typeof view === "number") {
        const share = row.shares[view] ?? 0;
        let step = 0;
        for (const edge of edges) {
          if (share > edge) {
            step += 1;
          }
        }
        strength = STEPS[step] ?? 100;
        tone = colors[view] ?? "var(--muted-foreground)";
      }
      result.push([
        row.k,
        read(`color-mix(in oklab, ${tone} ${strength}%, var(--card))`),
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
      const [maplibre, { Protocol, PMTiles }, worker] = await Promise.all([
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
      void Promise.all(
        [tiles, own?.tiles].flatMap((name) => {
          if (name == null) {
            return [];
          }
          const archive = new PMTiles(
            `${globalThis.location.origin}/tiles/${name}.pmtiles`,
          );
          protocol.add(archive);
          return [
            archive
              .getMetadata()
              .then((meta) =>
                String((meta as { attribution?: unknown }).attribution ?? ""),
              ),
          ];
        }),
      ).then((found) => {
        if (!stop.signal.aborted) {
          credits = [
            ...new Set(
              found.flatMap((text) =>
                text.split("; ").filter((part) => part.trim() !== ""),
              ),
            ),
          ];
        }
      });
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
      const shaft = document.createElement("canvas");
      shaft.width = 64;
      shaft.height = 24;
      const arrow = shaft.getContext("2d");
      if (arrow != null) {
        arrow.fillStyle = "#fff";
        arrow.shadowColor = "#fff";
        arrow.shadowBlur = 3;
        arrow.beginPath();
        arrow.moveTo(4, 9.5);
        arrow.lineTo(40, 9.5);
        arrow.lineTo(40, 3.5);
        arrow.lineTo(59, 12);
        arrow.lineTo(40, 20.5);
        arrow.lineTo(40, 14.5);
        arrow.lineTo(4, 14.5);
        arrow.closePath();
        arrow.fill();
      }
      const surfaces = [
        ...host.querySelectorAll<HTMLElement>("[data-map-surface]"),
      ];
      const room = {
        top: 20,
        right: 52,
        bottom: (surfaces[1]?.clientHeight ?? 0) + 20,
        left: 20,
      };
      fit = room;
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
              dots: { type: "geojson", data: dots },
              arrows: {
                type: "geojson",
                data: { type: "FeatureCollection", features: [] },
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
                id: "bubble",
                type: "circle",
                source: "dots",
                paint: {
                  "circle-radius": ["coalesce", ["feature-state", "radius"], 0],
                  "circle-color": [
                    "coalesce",
                    ["feature-state", "color"],
                    colorsNow.ground,
                  ],
                  "circle-opacity": 0.86,
                  "circle-stroke-color": colorsNow.seam,
                  "circle-stroke-width": [
                    "case",
                    [">", ["coalesce", ["feature-state", "radius"], 0], 0],
                    0.75,
                    0,
                  ],
                },
              },
              {
                id: "arrow",
                type: "symbol",
                source: "arrows",
                layout: {
                  "icon-image": "arrow",
                  "icon-size": ["get", "size"],
                  "icon-rotate": ["get", "rotate"],
                  "icon-rotation-alignment": "map",
                  "icon-anchor": "left",
                  "icon-allow-overlap": true,
                  "icon-ignore-placement": true,
                  "symbol-sort-key": ["get", "order"],
                },
                paint: {
                  "icon-color": ["get", "color"],
                  "icon-halo-color": colorsNow.seam,
                  "icon-halo-width": 1,
                },
              },
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
          fitBoundsOptions: { padding: index === 0 ? room : 6 },
          renderWorldCopies: false,
          attributionControl: false,
          canvasContextAttributes: { preserveDrawingBuffer: true },
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
        map.getCanvas().removeAttribute("role");
        map.getCanvas().removeAttribute("aria-label");
        map.addImage(
          "arrow",
          {
            width: 64,
            height: 24,
            data: new Uint8Array(
              arrow?.getImageData(0, 0, 64, 24).data.buffer ??
                new ArrayBuffer(6144),
            ),
          },
          { sdf: true },
        );
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
          const box = map.getCanvas();
          const side = Math.sqrt(
            (box.clientWidth * box.clientHeight) /
              Math.max(1, places.rows.length),
          );
          const fit = map.getZoom();
          if (side < 16) {
            map.setPaintProperty("seam", "line-width", [
              "interpolate",
              ["exponential", 2],
              ["zoom"],
              fit,
              Math.max(0.15, side / 16),
              fit + 4,
              1.6,
            ]);
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
    const tips = [0, 1].map((index) =>
      tones.read(colors[index] ?? "var(--muted-foreground)"),
    );
    const arrows = (scale: number) =>
      places.rows.flatMap((row) => {
        const change = changes.get(row.k)?.swing;
        if (row.at == null || change == null || Math.abs(change) < 0.001) {
          return [];
        }
        const size = Math.min(1, Math.abs(change) / 0.2);
        const tilt = 12 + size * 48;
        return [
          {
            type: "Feature" as const,
            properties: {
              color: tips[change >= 0 ? 0 : 1] ?? "",
              size: (0.35 + size * 0.95) * scale,
              rotate: change >= 0 ? -tilt : 180 + tilt,
              order: size,
            },
            geometry: { type: "Point" as const, coordinates: row.at },
          },
        ];
      });
    tones.done();
    for (const [index, map] of maps.entries()) {
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
        map.setPaintProperty("bubble", "circle-stroke-color", colorsNow.seam);
        map.setPaintProperty("arrow", "icon-halo-color", colorsNow.seam);
        const source = map.getSource("dots");
        if (source?.type === "geojson") {
          void (source as Maplibre.GeoJSONSource).setData(dots);
        }
        const pointers = map.getSource("arrows");
        if (pointers?.type === "geojson") {
          void (pointers as Maplibre.GeoJSONSource).setData({
            type: "FeatureCollection",
            features: view === "arrows" ? arrows(index === 0 ? 1 : 0.4) : [],
          });
        }
        map.removeFeatureState({ source: "places", sourceLayer: "areas" });
        map.removeFeatureState({ source: "dots" });
        for (const [k, color] of painted) {
          if (view === "bubbles") {
            map.setFeatureState(
              { source: "dots", id: k },
              {
                color,
                radius:
                  Math.sqrt((rows.get(k)?.valid ?? 0) / biggest) *
                  (index === 0 ? 28 : 9),
              },
            );
          } else {
            map.setFeatureState(
              { source: "places", sourceLayer: "areas", id: k },
              { color },
            );
          }
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
  class="relative grid grid-cols-[minmax(0,1fr)] gap-2"
  data-map
  data-export
  role="application"
  aria-roledescription="map"
  aria-label={m.map_label({ noun })}
  aria-describedby="{uid}-hint"
  {@attach keys}
>
  <p id="{uid}-hint" class="sr-only">{m.map_hint()}</p>
  <div
    class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2"
    data-export-skip
  >
    {#if !isDeclared}
      <div
        class="flex items-center gap-2.5 min-inline-0"
        role="group"
        aria-labelledby="{uid}-shading"
      >
        <span
          id="{uid}-shading"
          class="shrink-0 text-sm/7 font-semibold whitespace-nowrap sm:self-start"
          >{m.map_color_by()}</span
        >
        <select
          class="rounded-sm border border-input bg-card ps-2.5 pe-8 text-sm font-semibold keycap block-10 min-inline-0 sm:hidden"
          aria-labelledby="{uid}-shading"
          value={String(view)}
          onchange={(event) => {
            const value = event.currentTarget.value;
            shading = MODES.find((choice) => choice === value) ?? Number(value);
          }}
        >
          <option value="winner">{m.map_mode_winner()}</option>
          {#each options as option (option.index)}
            <option value={String(option.index)}>{option.name}</option>
          {/each}
          <option value="bubbles">{m.map_mode_bubbles()}</option>
          {#if isCompared}
            <option value="swing">{m.map_mode_swing()}</option>
            <option value="arrows">{m.map_mode_arrows()}</option>
            <option value="flips">{m.map_mode_flips()}</option>
          {/if}
        </select>
        <div class="-my-1 flex flex-wrap gap-2 py-1 min-inline-0 max-sm:hidden">
          {#each [[{ value: "winner" as const, label: m.map_mode_winner() }, ...options.map( (option) => ({ value: option.index, label: option.name }) ), { value: "bubbles" as const, label: m.map_mode_bubbles() }], isCompared ? [{ value: "swing" as const, label: m.map_mode_swing() }, { value: "arrows" as const, label: m.map_mode_arrows() }, { value: "flips" as const, label: m.map_mode_flips() }] : []] as group, at (at)}
            {#if group.length > 0}
              <div class="flex shrink-0">
                {#each group as choice (choice.value)}
                  <button
                    type="button"
                    class={cn(
                      "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold whitespace-nowrap keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                      {
                        "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                          view === choice.value,
                      },
                      {
                        "truncate max-inline-36 max-sm:max-inline-24":
                          typeof choice.value === "number",
                      },
                    )}
                    aria-pressed={view === choice.value}
                    onclick={() => (shading = choice.value)}
                    >{choice.label}</button
                  >
                {/each}
              </div>
            {/if}
          {/each}
        </div>
      </div>
    {/if}
    {#if levels.length > 1}
      <div class="flex" role="group" aria-label={m.map_level()}>
        {#each levels as level (level.code)}
          <svelte:element
            this={pick == null ? "a" : "button"}
            class={cn(
              "-ms-px flex items-center gap-1.5 border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary pointer-coarse:block-10",
              {
                "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                  level.isCurrent,
              },
            )}
            href={pick == null
              ? resolve(`/[...path]?${level.query}`, { path: level.path })
              : undefined}
            type={pick == null ? undefined : "button"}
            role={pick == null ? undefined : "button"}
            aria-current={pick == null && level.isCurrent ? "true" : undefined}
            aria-pressed={pick == null ? undefined : level.isCurrent}
            data-sveltekit-reset="false"
            data-sveltekit-noscroll
            onclick={() => pick?.(level.code)}
            >{level.label}
            <span class="font-figures text-[0.6875rem] font-normal tabular-nums"
              >{level.count}</span
            ></svelte:element
          >
        {/each}
      </div>
    {/if}
    <Export name="map" class="ms-auto" />
  </div>
  <div
    class="relative overflow-hidden rounded-sm bg-card ring-1 ring-border inline-full max-block-[min(38rem,70svh)]"
    style:aspect-ratio={ratio}
    {@attach mount}
  >
    <div
      class={cn(
        "transition-opacity duration-500 ease-out block-full inline-full motion-reduce:transition-none",
        { "opacity-0": !isLoaded },
      )}
      data-map-surface
    ></div>
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
            class="pointer-events-auto relative overflow-hidden rounded-xs bg-card ring-1 ring-border block-11 inline-15 sm:block-20 sm:inline-28"
          >
            <div class="block-full inline-full" data-map-surface></div>
          </div>
        {/each}
      </div>
    {/if}
    <div
      class="absolute inset-e-2 inset-bs-2 flex flex-col overflow-hidden rounded-sm bg-card shadow-menu ring-1 ring-border max-sm:inset-bs-auto max-sm:inset-be-2"
      data-export-skip
    >
      <button
        type="button"
        class="grid place-items-center block-8 inline-8 hover:bg-secondary pointer-coarse:block-10 pointer-coarse:inline-10"
        aria-label={m.map_zoom_in()}
        title={m.map_zoom_in()}
        onclick={() => maps[0]?.zoomIn()}
      >
        <PlusIcon class="block-4 inline-4" />
      </button>
      <button
        type="button"
        class="grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary pointer-coarse:block-10 pointer-coarse:inline-10"
        aria-label={m.map_zoom_out()}
        title={m.map_zoom_out()}
        onclick={() => maps[0]?.zoomOut()}
      >
        <MinusIcon class="block-4 inline-4" />
      </button>
      <button
        type="button"
        class="grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary pointer-coarse:block-10 pointer-coarse:inline-10"
        aria-label={m.map_reset()}
        title={m.map_reset()}
        onclick={() => {
          const frame = frames[0];
          if (frame != null) {
            maps[0]?.fitBounds(
              [frame.west, frame.south, frame.east, frame.north],
              { padding: fit },
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
    {#if (view === "swing" || view === "arrows") && span != null}
      <div class="flex flex-wrap items-center gap-2.5" aria-hidden="true">
        <span class="text-muted-foreground"
          >{m.map_swing_key({ year: span.from })}</span
        >
        <span class="flex items-center gap-1.5 text-xs">
          <span class="font-semibold">{names[1]}</span>
          {#if view === "swing"}
            <span class="flex font-figures tabular-nums">
              {#each [...STRENGTHS].toReversed() as strength, at (`b${at}`)}
                <span
                  class="block-3 inline-5 first:rounded-s-xs"
                  style:background-color="color-mix(in oklab, {colors[1] ??
                    'var(--muted-foreground)'}
                  {strength}%, var(--card))"
                ></span>
              {/each}
              {#each STRENGTHS as strength, at (`a${at}`)}
                <span
                  class="block-3 inline-5 last:rounded-e-xs"
                  style:background-color="color-mix(in oklab, {colors[0] ??
                    'var(--muted-foreground)'}
                  {strength}%, var(--card))"
                ></span>
              {/each}
            </span>
          {:else}
            <span class="flex items-center gap-1">
              <svg
                class="rotate-180 block-3 inline-6"
                viewBox="0 0 64 24"
                style:color={colors[1]}
                ><path
                  fill="currentColor"
                  d="M4 9.5h36v-6l19 8.5-19 8.5v-6H4z"
                /></svg
              >
              <svg
                class="block-3 inline-6"
                viewBox="0 0 64 24"
                style:color={colors[0]}
                ><path
                  fill="currentColor"
                  d="M4 9.5h36v-6l19 8.5-19 8.5v-6H4z"
                /></svg
              >
            </span>
          {/if}
          <span class="font-semibold">{names[0]}</span>
        </span>
        <span class="font-figures text-xs text-muted-foreground tabular-nums"
          >{m.map_swing_scale({ low: "2", high: "10" })}</span
        >
      </div>
    {:else if view === "flips" && span != null}
      <div class="flex flex-wrap items-center gap-1.5">
        {#if flips.length === 0}
          <span class="text-muted-foreground"
            >{m.map_flips_none({ year: span.from })}</span
          >
        {/if}
        {#each flips as [key, from, to, won] (key)}
          <button
            type="button"
            class={cn(
              "flex items-center gap-1.5 rounded-xs px-1.5 text-[0.8125rem] block-7 hover:bg-secondary",
              { "marked bg-secondary": only === key },
            )}
            style:--party={colors[to]}
            aria-pressed={only === key}
            onclick={() => {
              only = only === key ? null : key;
            }}
          >
            <span
              class="rounded-xs block-2.5 inline-2.5"
              style:background-color={colors[from]}
            ></span>
            <span class="text-muted-foreground">{names[from]}</span>
            <span aria-hidden="true" class="text-muted-foreground">›</span>
            <span class="rounded-xs bg-(--party) block-2.5 inline-2.5"></span>
            <span class="font-semibold">{names[to]}</span>
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              >{won}</span
            >
          </button>
        {/each}
      </div>
    {:else if typeof view === "number"}
      {const index = $derived(typeof view === "number" ? view : 0)}
      <div class="flex flex-wrap items-center gap-2.5" aria-hidden="true">
        <span class="text-muted-foreground"
          >{m.map_share_key({ name: names[index] ?? "" })}</span
        >
        <span
          class="flex items-center gap-1.5 font-figures text-xs tabular-nums"
        >
          <span>{percent(edges[0] ?? 0)}</span>
          <span class="flex">
            {#each STEPS.slice(0, edges.length + 1) as strength (strength)}
              <span
                class="block-3 inline-6 first:rounded-s-xs last:rounded-e-xs"
                style:background-color="color-mix(in oklab, {colors[index] ??
                  'var(--muted-foreground)'}
                {strength}%, var(--card))"
              ></span>
            {/each}
          </span>
          <span>{percent(edges.at(-1) ?? 0)}</span>
        </span>
      </div>
    {:else}
      <div class="flex flex-wrap gap-1.5">
        {#each leads.slice(0, 6) as [index, won] (index)}
          <button
            type="button"
            class={cn(
              "flex items-center gap-1.5 rounded-xs px-1.5 text-[0.8125rem] block-7 hover:bg-secondary",
              { "marked bg-secondary": only === index },
            )}
            style:--party={colors[index]}
            aria-pressed={only === index}
            aria-label={m.places_leads({
              label: names[index] ?? "",
              count: won,
            })}
            onclick={() => {
              only = only === index ? null : index;
            }}
          >
            <span class="rounded-xs bg-(--party) block-2.5 inline-2.5"></span>
            <span class="font-semibold">{names[index]}</span>
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              >{won}</span
            >
          </button>
        {/each}
      </div>
      {#if isDeclared}{:else if view === "bubbles"}
        <span class="text-muted-foreground"
          >{m.map_bubbles_key({ votes: votes.toLowerCase() })}</span
        >
      {:else}
        <div class="flex items-center gap-2.5" aria-hidden="true">
          <span class="text-muted-foreground">{m.map_margin()}</span>
          <span
            class="flex items-center gap-1.5 font-figures text-xs tabular-nums"
          >
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
      {/if}
    {/if}
    {#if isApproximate}
      <span class="flex items-center gap-1.5 text-muted-foreground">
        <span
          class="rounded-xs bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_4px)] ring-1 ring-border block-3 inline-4"
        ></span>
        {m.boundaries_approximate()}
      </span>
    {/if}
  </div>
  {#if credits.length > 0}
    {const publishers = $derived(
      credits.filter((credit) => !credit.startsWith("©")),
    )}
    <p class="px-1 text-xs text-muted-foreground">
      {[
        ...(publishers.length === 0
          ? []
          : [
              m.map_boundaries({
                publishers: new Intl.ListFormat(getLocale(), {
                  type: "conjunction",
                }).format(publishers),
              }),
            ]),
        ...credits.filter((credit) => credit.startsWith("©")),
      ].join(". ")}
    </p>
  {/if}

  <p class="px-1 text-[0.8125rem] text-muted-foreground sm:hidden">
    {m.map_pick_touch()}
  </p>
  {#if pinned != null && current != null}
    <div
      class="fixed inset-x-0 inset-be-0 z-40 grid touch-none gap-2 rounded-t-lg bg-card px-4 pbs-1.5 pbe-[max(1rem,env(safe-area-inset-bottom))] shadow-menu ring-1 ring-border sm:hidden"
      style:translate="0 {Math.max(0, drag)}px"
      style:transition={drag === 0 ? "translate 200ms ease-out" : "none"}
      role="region"
      aria-label={current.name}
      aria-live="polite"
      data-export-skip
      transition:fly={{ y: 48, duration: calm ? 0 : 220 }}
      onpointerdown={(event) => {
        if (event.pointerType === "touch") {
          grab = event.clientY;
        }
      }}
      onpointermove={(event) => {
        if (grab != null) {
          drag = event.clientY - grab;
        }
      }}
      onpointerup={() => {
        if (drag > 56) {
          pinned = null;
          focus = null;
        }
        grab = null;
        drag = 0;
      }}
      onpointercancel={() => {
        grab = null;
        drag = 0;
      }}
    >
      <div class="flex items-center justify-between">
        <span
          class="mx-auto rounded-full bg-border block-1 inline-10"
          aria-hidden="true"
        ></span>
        <button
          type="button"
          class="-me-2 grid place-items-center rounded-sm text-muted-foreground block-10 inline-10 hover:bg-secondary"
          aria-label={m.map_sheet_close()}
          onclick={() => {
            pinned = null;
            focus = null;
          }}
        >
          <XIcon class="block-4 inline-4" />
        </button>
      </div>
      {@render readout(true)}
      {#if current.path != null}
        {const place = $derived(current)}
        <button
          type="button"
          class="flex items-center justify-center rounded-sm border border-foreground bg-marker text-sm font-semibold text-[#15171a] block-11"
          onclick={() => open(place.k)}
          >{m.map_open({ place: place.name })}</button
        >
      {/if}
    </div>
  {/if}
</div>

{#snippet readout(isSheet = false)}
  {#if current != null}
    <span class="flex items-baseline justify-between gap-3">
      <span class="font-semibold text-pretty">{current.name}</span>
      {#if moved?.from != null && moved.to != null && moved.from !== moved.to}
        <span
          class="flex shrink-0 items-center gap-1 rounded-xs bg-secondary px-1.5 text-xs font-semibold"
          style:--party={colors[moved.to]}
          ><span class="rounded-xs bg-(--party) block-2 inline-2"
          ></span>{m.map_flipped()}</span
        >
      {:else if current.margin != null}
        <span
          class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
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
            })}>{names[entry.index] ?? option?.label}</span
          >
          <span
            class="flex items-baseline gap-2 font-figures text-xs tabular-nums"
          >
            {#if moved != null}
              <span class="text-muted-foreground"
                >{points(
                  (moved.after[entry.index] ?? 0) -
                    (moved.before[entry.index] ?? 0),
                )}</span
              >
            {/if}
            {percent(entry.share)}</span
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
    {#if isDeclared && current.leader != null}
      <span
        class="flex items-center gap-2 text-[0.8125rem]"
        style:--party={colors[current.leader]}
      >
        <span class="rounded-xs bg-(--party) block-2.5 inline-2.5"></span>
        <span class="font-semibold"
          >{places.legend[current.leader]?.label ?? names[current.leader]}</span
        >
      </span>
    {/if}
    <span class="flex items-baseline justify-between gap-3 text-xs">
      <span class="text-muted-foreground"
        >{#if !isDeclared}{votes}
          <span class="font-figures tabular-nums">{count(current.valid)}</span
          >{/if}</span
      >
      {#if current.path != null && !isSheet}
        <a
          class="pointer-events-auto font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={resolve("/[...path]", { path: current.path })}
          >{m.map_open({ place: current.name })}</a
        >
      {/if}
    </span>
  {/if}
{/snippet}
