<script lang="ts" module>
  let handoff: { path: string; image: string } | null = null;
  const warmed: Record<string, boolean> = {};
</script>

<script lang="ts">
  import type * as Maplibre from "maplibre-gl";
  import { goto, preloadData } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowUpLeftIcon from "@lucide/svelte/icons/arrow-up-left";
  import MinusIcon from "@lucide/svelte/icons/minus";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import ScanIcon from "@lucide/svelte/icons/scan";
  import XIcon from "@lucide/svelte/icons/x";
  import { mode } from "mode-watcher";
  import { untrack } from "svelte";
  import { on } from "svelte/events";
  import { fade, fly } from "svelte/transition";
  import Export from "#lib/components/export.svelte";
  import * as Select from "#lib/components/ui/select/index.js";
  import { count, percent, points, precision } from "#lib/format.js";
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
    nested = null,
    frames,
    blank = null,
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
    sources = null,
    explore = null,
    neighbors = null,
    up = null,
  }: {
    places: Places;
    colors: Array<string>;
    tiles: string;
    own: { tiles: string; k: number } | null;
    nested?: { tiles: string; ks: Array<number> } | null;
    frames: Array<{ west: number; south: number; east: number; north: number }>;
    blank?: Array<{ k: number; name: string }> | null;
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
    sources?: Array<string> | null;
    explore?: string | null;
    neighbors?:
      | (() => Promise<Array<{ k: number; name: string; path: string }>>)
      | null;
    up?: { name: string; path: string } | null;
  } = $props();

  const BINS = [0.05, 0.1, 0.2];
  const STRENGTHS = [34, 54, 76, 100];
  const STEPS = [16, 36, 56, 78, 100];
  const SWINGS = [0.02, 0.05, 0.1];
  const COMPARED = new Set(["swing", "arrows", "flips", "turnout_change"]);
  const MODES = [
    "winner",
    "bubbles",
    "turnout",
    "turnout_change",
    "swing",
    "arrows",
    "flips",
  ] as const;

  const uid = $props.id();
  let maps = $state<Array<Maplibre.Map>>([]);
  let isLoaded = $state(false);
  let opening = $state<number | null>(null);
  let still = $state(
    untrack(() =>
      handoff != null &&
      decodeURI(handoff.path) === decodeURI(page.url.pathname)
        ? handoff.image
        : null,
    ),
  );
  handoff = null;
  let dwell: ReturnType<typeof setTimeout> | undefined;
  let credits = $state<Array<string>>([]);
  const credited = $derived(sources ?? credits);
  let pinned = $state<number | null>(null);
  let empty = $state<number | null>(null);
  let pointer = $state<{ x: number; y: number } | null>(null);
  let near = $state<number | null>(null);
  let nearPinned = $state(false);
  let nearby = $state<Array<{ k: number; name: string; path: string }>>([]);
  let seen = $state<Array<number>>([]);
  let isMoved = false;
  let boxWidth = $state(0);
  let boxHeight = $state(0);
  let cardWidth = $state(0);
  let cardHeight = $state(0);
  const besides = $derived(new Map(nearby.map((entry) => [entry.k, entry])));
  const beside = $derived(near == null ? null : (besides.get(near) ?? null));
  const adjoining = $derived(
    seen
      .flatMap((k) => {
        const entry = besides.get(k);
        return entry == null || k === own?.k ? [] : [entry];
      })
      .toSorted((a, b) =>
        a.name.localeCompare(b.name, undefined, { numeric: true }),
      ),
  );
  let grab = $state<number | null>(null);
  let drag = $state(0);
  const calm =
    typeof matchMedia !== "undefined" &&
    matchMedia("(prefers-reduced-motion: reduce)").matches;
  let shading = $state<(typeof MODES)[number] | number>(
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

  const rows = $derived(
    new Map(
      [...places.rows, ...(places.districts ?? [])].map((row) => [row.k, row]),
    ),
  );
  const inner = $derived(new Set(nested?.ks ?? []));
  const blanks = $derived(
    new Map((blank ?? []).map((entry) => [entry.k, entry.name])),
  );
  const scopes = $derived(
    frames.map((frame): Maplibre.FilterSpecification | null => {
      if (frames.length < 2) {
        return null;
      }
      const padX = ((frame.east - frame.west + 360) % 360 || 360) * 0.06;
      const padY = (frame.north - frame.south) * 0.06;
      const west = frame.west - padX;
      const east = frame.east + padX;
      return [
        "any",
        ["!", ["in", ["id"], ["literal", [...rows.keys()]]]],
        [
          "in",
          ["id"],
          [
            "literal",
            [...places.rows, ...(places.districts ?? [])].flatMap((row) => {
              const at = row.at;
              if (at == null) {
                return [row.k];
              }
              const [x, y] = at;
              const isAcross =
                west <= east ? x >= west && x <= east : x >= west || x <= east;
              return isAcross &&
                y >= frame.south - padY &&
                y <= frame.north + padY
                ? [row.k]
                : [];
            }),
          ],
        ],
      ];
    }),
  );
  const hit = $derived(nested == null ? ["fill"] : ["nested-fill", "fill"]);
  const isCompared = $derived(
    span != null &&
      places.rows.some((row) => row.earlier != null || row.redrawn != null),
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
  const unmatched = $derived(
    new Map(
      isCompared
        ? [...places.rows, ...(places.districts ?? [])].flatMap((row) =>
            row.earlier == null && (row.valid > 0 || row.leader != null)
              ? [[row.k, row.redrawn ?? null] as const]
              : [],
          )
        : [],
    ),
  );
  const turnouts = $derived(
    new Map(
      [...places.rows, ...(places.districts ?? [])].flatMap((row) =>
        row.turnout == null ? [] : [[row.k, row.turnout[0] / row.turnout[1]]],
      ),
    ),
  );
  const surges = $derived(
    new Map(
      [...places.rows, ...(places.districts ?? [])].flatMap((row) =>
        row.turnout == null || row.turnedBefore == null
          ? []
          : [[row.k, row.turnout[0] / row.turnout[1] - row.turnedBefore]],
      ),
    ),
  );
  const view = $derived(
    (!isCompared && typeof shading === "string" && COMPARED.has(shading)) ||
      (shading === "turnout" && turnouts.size === 0) ||
      (shading === "turnout_change" && surges.size === 0)
      ? "winner"
      : shading,
  );
  const isUnmatched = $derived(
    unmatched.size > 0 && typeof view === "string" && COMPARED.has(view),
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
    return Math.min(
      frames.length > 1 ? 1.5 : 2,
      Math.max(0.8, (wide / Math.max(tall, 0.0001)) * 1.04),
    );
  });
  const leads = $derived(
    Map.groupBy(
      places.rows.filter((row) => row.leader != null),
      (row) => row.leader ?? 0,
    )
      .entries()
      .map(
        ([index, won]) =>
          [index, won.filter((row) => row.isCounted !== false).length] as const,
      )
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
    if (typeof view !== "number" && view !== "turnout") {
      return [];
    }
    const index = view;
    const sorted = (
      typeof index === "number"
        ? places.rows.map((row) => row.shares[index] ?? 0)
        : [...turnouts.values()]
    ).toSorted((a, b) => a - b);
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
  let isShaded = $state(false);
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

  const spot = $derived.by(() => {
    let anchor = pointer;
    if (anchor == null && current?.at != null && maps[0] != null) {
      const projected = maps[0].project(current.at);
      anchor =
        projected.x >= 0 &&
        projected.y >= 0 &&
        projected.x <= boxWidth &&
        projected.y <= boxHeight
          ? { x: projected.x, y: projected.y }
          : null;
    }
    if (anchor == null) {
      return { x: 8, y: up == null ? 8 : 48 };
    }
    const gap = 16;
    const right = boxWidth - cardWidth - 8;
    const bottom = boxHeight - cardHeight - 8;
    const x =
      anchor.x + gap + cardWidth > boxWidth - 8
        ? anchor.x - gap - cardWidth
        : anchor.x + gap;
    const y =
      anchor.y + gap + cardHeight > boxHeight - 8
        ? anchor.y - gap - cardHeight
        : anchor.y + gap;
    return {
      x: Math.max(8, Math.min(right, x)),
      y: Math.max(8, Math.min(bottom, y)),
    };
  });

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
    opening = k;
    pinned = k;
    focus = k;
    let isGone = false;
    const leave = () => {
      if (!isGone) {
        isGone = true;
        void loading.then(
          () => {
            try {
              handoff = {
                path: href,
                image: map.getCanvas().toDataURL("image/webp", 0.85),
              };
            } catch {
              handoff = null;
            }
            void goto(href).finally(() => {
              opening = null;
            });
          },
          () => {
            opening = null;
            void goto(href);
          },
        );
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
    for (const row of [...places.rows, ...(places.districts ?? [])]) {
      if (view === "turnout_change") {
        const change = surges.get(row.k);
        if (change != null) {
          let step = 0;
          for (const edge of SWINGS) {
            if (Math.abs(change) >= edge) {
              step += 1;
            }
          }
          result.push([
            row.k,
            read(
              `color-mix(in oklab, var(${change < 0 ? "--map-fall" : "--map-rise"}) ${STEPS[step + 1] ?? 100}%, var(--map-low))`,
            ),
          ]);
        }
        continue;
      }
      if (view === "turnout") {
        const share = turnouts.get(row.k);
        if (share != null) {
          let step = 0;
          for (const edge of edges) {
            if (share > edge) {
              step += 1;
            }
          }
          result.push([
            row.k,
            read(
              `color-mix(in oklab, var(--foreground) ${STEPS[step] ?? 100}%, var(--map-low))`,
            ),
          ]);
        }
        continue;
      }
      if (row.leader == null) {
        continue;
      }
      const dimmed =
        only != null && typeof only === "number" && only !== row.leader;
      let strength = dimmed
        ? 10
        : isDeclared || row.valid === 0 || !isShaded
          ? 100
          : (STRENGTHS[bin(row.margin)] ?? 100);
      let tone = colors[row.leader] ?? "var(--muted-foreground)";
      const [red = 0, green = 0, blue = 0] = (
        /\d+, \d+, \d+/u.exec(read(tone))?.[0] ?? ""
      )
        .split(", ")
        .map(Number);
      if (
        !dimmed &&
        Math.max(red, green, blue) - Math.min(red, green, blue) < 24
      ) {
        strength = 100;
      }
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
        read(`color-mix(in oklab, ${tone} ${strength}%, var(--map-low))`),
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
        [tiles, own?.tiles, nested?.tiles].flatMap((name) => {
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
                text
                  .split(text.includes("\n") ? "\n" : "; ")
                  .filter((part) => part.trim() !== ""),
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
      const pen = hatch.getContext("2d", { willReadFrequently: true });
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
      const arrow = shaft.getContext("2d", { willReadFrequently: true });
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
      const edge = Math.max(
        24,
        Math.round(Math.min(host.clientWidth, host.clientHeight) * 0.06),
      );
      const room = {
        top: edge + (up == null ? 0 : 20),
        right: edge + 36,
        bottom: (surfaces[1]?.clientHeight ?? 0) + edge,
        left: edge,
      };
      fit = room;
      for (const [index, surface] of surfaces.entries()) {
        const frame = frames[index];
        const map = new maplibre.Map({
          container: surface,
          style: {
            version: 8,
            projection: { type: "globe" },
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
              ...(nested == null
                ? {}
                : {
                    nested: {
                      type: "vector" as const,
                      url: `pmtiles://${origin}/tiles/${nested.tiles}.pmtiles`,
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
              ...(blanks.size === 0
                ? []
                : [
                    {
                      id: "blank",
                      type: "fill" as const,
                      source: "places",
                      "source-layer": "areas",
                      filter: [
                        "in",
                        ["id"],
                        ["literal", [...blanks.keys()]],
                      ] as Maplibre.FilterSpecification,
                      paint: { "fill-pattern": "hatch" },
                    },
                  ]),
              {
                id: "unmatched",
                type: "fill" as const,
                source: "places",
                "source-layer": "areas",
                filter: ["in", ["id"], ["literal", []]],
                paint: { "fill-pattern": "hatch" },
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
                      id: "veil",
                      type: "fill" as const,
                      source: "own",
                      "source-layer": "areas",
                      filter: [
                        "!=",
                        ["id"],
                        own.k,
                      ] as Maplibre.FilterSpecification,
                      paint: {
                        "fill-color": colorsNow.paper,
                        "fill-opacity": [
                          "case",
                          ["boolean", ["feature-state", "near"], false],
                          0.35,
                          0.6,
                        ] as Maplibre.ExpressionSpecification,
                      },
                    },
                  ]),
              ...(nested == null
                ? []
                : [
                    {
                      id: "nested-fill",
                      type: "fill" as const,
                      source: "nested",
                      "source-layer": "areas",
                      filter: [
                        "in",
                        ["id"],
                        ["literal", nested.ks],
                      ] as Maplibre.FilterSpecification,
                      paint: {
                        "fill-color": [
                          "coalesce",
                          ["feature-state", "color"],
                          "rgba(0, 0, 0, 0)",
                        ] as Maplibre.ExpressionSpecification,
                      },
                    },
                    {
                      id: "nested-seam",
                      type: "line" as const,
                      source: "nested",
                      "source-layer": "areas",
                      filter: [
                        "in",
                        ["id"],
                        ["literal", nested.ks],
                      ] as Maplibre.FilterSpecification,
                      paint: {
                        "line-color": colorsNow.seam,
                        "line-width": 0.75,
                      },
                    },
                    {
                      id: "nested-focus",
                      type: "line" as const,
                      source: "nested",
                      "source-layer": "areas",
                      filter: [
                        "in",
                        ["id"],
                        ["literal", nested.ks],
                      ] as Maplibre.FilterSpecification,
                      paint: {
                        "line-color": colorsNow.ink,
                        "line-width": 2.25,
                        "line-opacity": [
                          "case",
                          ["boolean", ["feature-state", "focus"], false],
                          1,
                          0,
                        ] as Maplibre.ExpressionSpecification,
                      },
                    },
                  ]),
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
                    {
                      id: "near",
                      type: "line" as const,
                      source: "own",
                      "source-layer": "areas",
                      filter: [
                        "!=",
                        ["id"],
                        own.k,
                      ] as Maplibre.FilterSpecification,
                      paint: {
                        "line-color": colorsNow.ink,
                        "line-width": 1.5,
                        "line-dasharray": [3, 2],
                        "line-opacity": [
                          "case",
                          ["boolean", ["feature-state", "near"], false],
                          1,
                          0,
                        ] as Maplibre.ExpressionSpecification,
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
        map.once("style.load", () => {
          if (frame != null) {
            map.fitBounds([frame.west, frame.south, frame.east, frame.north], {
              padding: index === 0 ? room : 6,
              animate: false,
            });
            if (index === 0) {
              map.setMinZoom(Math.max(0, map.getZoom() - 1.25));
            }
          }
        });
        if (index === 0) {
          map.on("movestart", (event) => {
            if (event.originalEvent != null) {
              isMoved = true;
            }
          });
          const watcher = new ResizeObserver(() => {
            if (frame == null || isMoved || opening != null) {
              return;
            }
            map.resize();
            map.setMinZoom(0);
            map.fitBounds([frame.west, frame.south, frame.east, frame.north], {
              padding: room,
              animate: false,
            });
            map.setMinZoom(Math.max(0, map.getZoom() - 1.25));
          });
          watcher.observe(host);
          cleanup.push(() => watcher.disconnect());
        }
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
          const id = map.queryRenderedFeatures(event.point, { layers: hit })[0]
            ?.id;
          const k = typeof id === "number" && rows.has(id) ? id : null;
          const path = k == null ? null : rows.get(k)?.path;
          empty =
            k == null && typeof id === "number" && blanks.has(id) ? id : null;
          map.getCanvas().style.cursor = path != null ? "pointer" : "";
          if (pinned == null && focus !== k) {
            focus = k;
            clearTimeout(dwell);
            if (path != null) {
              dwell = setTimeout(() => {
                const href = resolve("/[...path]", { path });
                if (focus === k && warmed[href] !== true) {
                  warmed[href] = true;
                  void preloadData(href);
                }
              }, 350);
            }
          }
        });
        map.on("mouseleave", "fill", () => {
          clearTimeout(dwell);
          empty = null;
          map.getCanvas().style.cursor = "";
          if (pinned == null) {
            focus = null;
          }
        });
        map.on("mousemove", (event) => {
          const bounds = host.getBoundingClientRect();
          pointer = {
            x: event.originalEvent.clientX - bounds.left,
            y: event.originalEvent.clientY - bounds.top,
          };
          if (own == null || nearPinned) {
            return;
          }
          const place = map
            .queryRenderedFeatures(event.point, { layers: hit })
            .find(
              (feature) =>
                typeof feature.id === "number" && rows.has(feature.id),
            )?.id;
          const id =
            place == null
              ? map.queryRenderedFeatures(event.point, { layers: ["veil"] })[0]
                  ?.id
              : null;
          const k = typeof id === "number" && besides.has(id) ? id : null;
          if (k !== near) {
            near = k;
          }
          map.getCanvas().style.cursor =
            k != null ||
            (typeof place === "number" && rows.get(place)?.path != null)
              ? "pointer"
              : "";
        });
        map.on("mouseout", () => {
          pointer = null;
          if (!nearPinned) {
            near = null;
          }
        });
        map.on("click", (event) => {
          const [feature] = map.queryRenderedFeatures(event.point, {
            layers: hit,
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
            const id =
              own == null
                ? null
                : map.queryRenderedFeatures(event.point, {
                    layers: ["veil"],
                  })[0]?.id;
            const next = typeof id === "number" ? besides.get(id) : undefined;
            if (next == null) {
              nearPinned = false;
              near = null;
            } else if (isTouch && !(nearPinned && near === next.k)) {
              near = next.k;
              nearPinned = true;
            } else {
              void goto(resolve("/[...path]", { path: next.path }));
            }
          } else if (isTouch && pinned !== k) {
            nearPinned = false;
            near = null;
            pinned = k;
            focus = k;
          } else {
            open(k);
          }
        });
        map.once("idle", () => {
          if (index === 0) {
            still = null;
          }
        });
        map.on("load", () => {
          ready.add(map);
          if (index === 0) {
            isLoaded = true;
            if (own != null && neighbors != null) {
              const look = () => {
                seen = [
                  ...new Set(
                    map
                      .queryRenderedFeatures({ layers: ["veil"] })
                      .flatMap((feature) =>
                        typeof feature.id === "number" ? [feature.id] : [],
                      ),
                  ),
                ];
              };
              map.once("idle", look);
              map.on("moveend", look);
              neighbors().then(
                (list) => {
                  if (!stop.signal.aborted) {
                    nearby = list;
                  }
                },
                () => {
                  nearby = [];
                },
              );
            }
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
      clearTimeout(dwell);
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

  const ready = new WeakSet<Maplibre.Map>();
  let repaint = $state(0);
  $effect(() => {
    const bump = () => requestAnimationFrame(() => (repaint += 1));
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style", "data-theme"],
    });
    const offScheme = on(
      matchMedia("(prefers-color-scheme: dark)"),
      "change",
      bump,
    );
    return () => {
      observer.disconnect();
      offScheme();
    };
  });

  $effect(() => {
    const theme = mode.current;
    void repaint;
    const host = maps[0]?.getContainer().closest<HTMLElement>("[data-map]");
    if (host == null || theme == null) {
      return;
    }
    const tones = paint(host);
    const colorsNow = style(tones.read);
    const painted = fills(tones.read);
    const hatched = isUnmatched ? [...unmatched.keys()] : [];
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
        for (const layer of ["fill", "hatch", "seam"]) {
          if (map.getLayer(layer) != null) {
            map.setFilter(layer, scopes[index] ?? null);
          }
        }
        map.setFilter("unmatched", ["in", ["id"], ["literal", hatched]]);
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
          map.setPaintProperty("veil", "fill-color", colorsNow.paper);
          map.setPaintProperty("near", "line-color", colorsNow.ink);
        }
        map.setPaintProperty("bubble", "circle-stroke-color", colorsNow.seam);
        map.setPaintProperty("arrow", "icon-halo-color", colorsNow.seam);
        if (map.getLayer("nested-seam") != null) {
          map.setPaintProperty("nested-seam", "line-color", colorsNow.seam);
          map.setPaintProperty("nested-focus", "line-color", colorsNow.ink);
          map.removeFeatureState({ source: "nested", sourceLayer: "areas" });
        }
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
          if (inner.has(k)) {
            if (view !== "bubbles") {
              map.setFeatureState(
                { source: "nested", sourceLayer: "areas", id: k },
                { color },
              );
            }
          } else if (view === "bubbles") {
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
            {
              source: inner.has(k) ? "nested" : "places",
              sourceLayer: "areas",
              id: k,
            },
            { focus: true },
          );
        }
      };
      if (ready.has(map)) {
        apply();
      } else {
        map.once("load", apply);
      }
    }
  });

  let lit: number | null = null;
  $effect(() => {
    const k = near;
    for (const map of maps) {
      const apply = () => {
        if (map.getSource("own") == null) {
          return;
        }
        if (lit != null) {
          map.setFeatureState(
            { source: "own", sourceLayer: "areas", id: lit },
            { near: false },
          );
        }
        if (k != null) {
          map.setFeatureState(
            { source: "own", sourceLayer: "areas", id: k },
            { near: true },
          );
        }
      };
      if (ready.has(map)) {
        apply();
      } else {
        map.once("load", apply);
      }
    }
    lit = k;
  });

  let shown: number | null = null;
  $effect(() => {
    const k = focus;
    for (const map of maps) {
      const apply = () => {
        if (shown != null) {
          map.setFeatureState(
            {
              source: inner.has(shown) ? "nested" : "places",
              sourceLayer: "areas",
              id: shown,
            },
            { focus: false },
          );
        }
        if (k != null) {
          map.setFeatureState(
            {
              source: inner.has(k) ? "nested" : "places",
              sourceLayer: "areas",
              id: k,
            },
            { focus: true },
          );
        }
      };
      if (ready.has(map)) {
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
        <Select.Root
          type="single"
          value={String(view)}
          onValueChange={(value: string) => {
            shading = MODES.find((choice) => choice === value) ?? Number(value);
          }}
        >
          <Select.Trigger
            class="text-sm font-semibold min-inline-0 data-[size=default]:block-10 sm:hidden"
            aria-labelledby="{uid}-shading"
          >
            <span class="truncate"
              >{typeof view === "number"
                ? (options.find((option) => option.index === view)?.name ?? "")
                : {
                    winner: m.map_mode_winner,
                    bubbles: m.map_mode_bubbles,
                    turnout: m.map_mode_turnout,
                    turnout_change: m.map_mode_turnout_change,
                    swing: m.map_mode_swing,
                    arrows: m.map_mode_arrows,
                    flips: m.map_mode_flips,
                  }[view]()}</span
            >
          </Select.Trigger>
          <Select.Content align="start">
            <Select.Item value="winner" label={m.map_mode_winner()} />
            {#each options as option (option.index)}
              <Select.Item value={String(option.index)} label={option.name} />
            {/each}
            <Select.Item value="bubbles" label={m.map_mode_bubbles()} />
            {#if turnouts.size > 0}
              <Select.Item value="turnout" label={m.map_mode_turnout()} />
            {/if}
            {#if isCompared}
              <Select.Item value="swing" label={m.map_mode_swing()} />
              <Select.Item value="arrows" label={m.map_mode_arrows()} />
              <Select.Item value="flips" label={m.map_mode_flips()} />
              {#if surges.size > 0}
                <Select.Item
                  value="turnout_change"
                  label={m.map_mode_turnout_change()}
                />
              {/if}
            {/if}
          </Select.Content>
        </Select.Root>
        <div class="-my-1 flex flex-wrap gap-2 py-1 min-inline-0 max-sm:hidden">
          {#each [[{ value: "winner" as const, label: m.map_mode_winner() }, ...options.map( (option) => ({ value: option.index, label: option.name }) ), { value: "bubbles" as const, label: m.map_mode_bubbles() }, ...(turnouts.size > 0 ? [{ value: "turnout" as const, label: m.map_mode_turnout() }] : [])], isCompared ? [{ value: "swing" as const, label: m.map_mode_swing() }, { value: "arrows" as const, label: m.map_mode_arrows() }, { value: "flips" as const, label: m.map_mode_flips() }, ...(surges.size > 0 ? [{ value: "turnout_change" as const, label: m.map_mode_turnout_change() }] : [])] : []] as group, at (at)}
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
    <div class="ms-auto flex items-center gap-2">
      {#if explore != null}
        <a
          href={resolve("/map/[...path]", { path: explore })}
          class="flex items-center rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary pointer-coarse:block-10"
          >{m.atlas_explore()}</a
        >
      {/if}
      <Export name="map" />
    </div>
  </div>
  <div
    class="relative overflow-hidden rounded-sm bg-card ring-1 ring-border inline-full max-block-[min(38rem,70svh)]"
    style:aspect-ratio={ratio}
    bind:clientWidth={boxWidth}
    bind:clientHeight={boxHeight}
    {@attach mount}
  >
    <div
      class={cn(
        "transition-opacity duration-500 ease-out block-full inline-full motion-reduce:transition-none",
        { "opacity-0": !isLoaded },
      )}
      data-map-surface
    ></div>
    {#if still != null}
      <img
        class="pointer-events-none absolute inset-0 object-cover block-full inline-full"
        src={still}
        alt=""
        out:fade={{ duration: calm ? 0 : 180 }}
      />
    {:else if !isLoaded}
      <p
        class="absolute inset-0 grid place-items-center text-[0.8125rem] text-muted-foreground"
      >
        {m.map_loading()}
      </p>
    {/if}
    {#if opening != null}
      <p
        class="pointer-events-none absolute inset-x-0 inset-be-3 mx-auto flex items-center gap-2 rounded-sm bg-card/95 px-3 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border block-8 inline-fit"
        role="status"
        transition:fade={{ duration: calm ? 0 : 120 }}
      >
        <span
          class="animate-spin rounded-full border-2 border-border border-bs-foreground block-3.5 inline-3.5 motion-reduce:animate-none"
          aria-hidden="true"
        ></span>
        {m.map_opening({ place: rows.get(opening)?.name ?? "" })}
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
        onclick={() => {
          isMoved = true;
          maps[0]?.zoomIn();
        }}
      >
        <PlusIcon class="block-4 inline-4" />
      </button>
      <button
        type="button"
        class="grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary pointer-coarse:block-10 pointer-coarse:inline-10"
        aria-label={m.map_zoom_out()}
        title={m.map_zoom_out()}
        onclick={() => {
          isMoved = true;
          maps[0]?.zoomOut();
        }}
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
            isMoved = false;
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
    {#if up != null}
      <a
        class="absolute inset-s-2 inset-bs-2 flex items-center gap-1.5 rounded-sm bg-card ps-2 pe-2.5 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border block-8 max-inline-[calc(100%-4.5rem)] hover:bg-secondary pointer-coarse:block-10"
        href={resolve("/[...path]", { path: up.path })}
        data-export-skip
        ><ArrowUpLeftIcon
          class="shrink-0 block-4 inline-4"
          aria-hidden="true"
        /><span class="sr-only">{m.map_up()}</span><span class="truncate"
          >{up.name}</span
        ></a
      >
    {/if}
    {#if current != null || (pointer != null && (empty != null || beside != null))}
      <div
        class={cn(
          "pointer-events-none absolute inset-s-0 inset-bs-0 grid gap-1.5 rounded-sm bg-card/95 px-3 py-2.5 shadow-menu ring-1 ring-border max-sm:hidden",
          {
            "inline-[min(17rem,calc(100%-1rem))]": current != null,
            "gap-0.5 inline-fit": current == null,
          },
        )}
        style:translate="{spot.x}px {spot.y}px"
        bind:clientWidth={cardWidth}
        bind:clientHeight={cardHeight}
        aria-live="polite"
      >
        {#if current != null}
          {@render readout()}
        {:else if empty != null}
          <span class="font-semibold text-pretty">{blanks.get(empty)}</span>
          <span class="text-xs text-muted-foreground">{m.map_no_results()}</span
          >
        {:else if beside != null}
          <span class="font-semibold text-pretty">{beside.name}</span>
          <span class="text-xs text-muted-foreground">{m.map_click_open()}</span
          >
        {/if}
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
                  {strength}%, var(--map-low))"
                ></span>
              {/each}
              {#each STRENGTHS as strength, at (`a${at}`)}
                <span
                  class="block-3 inline-5 last:rounded-e-xs"
                  style:background-color="color-mix(in oklab, {colors[0] ??
                    'var(--muted-foreground)'}
                  {strength}%, var(--map-low))"
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
    {:else if view === "turnout_change"}
      <div class="flex flex-wrap items-center gap-2.5" aria-hidden="true">
        <span class="text-muted-foreground">{m.map_turnout_change_key()}</span>
        <span
          class="flex items-center gap-1.5 font-figures text-xs tabular-nums"
        >
          <span>{m.map_turnout_fell()}</span>
          <span class="flex">
            {#each [...[3, 2, 1, 0].map((step) => ["--map-fall", STEPS[step + 1] ?? 100, -step - 1] as const), ...[0, 1, 2, 3].map((step) => ["--map-rise", STEPS[step + 1] ?? 100, step + 1] as const)] as [token, strength, key] (key)}
              <span
                class="block-3 inline-5 first:rounded-s-xs last:rounded-e-xs"
                style:background-color="color-mix(in oklab, var({token}) {strength}%,
                var(--map-low))"
              ></span>
            {/each}
          </span>
          <span>{m.map_turnout_rose()}</span>
        </span>
        <span class="font-figures text-xs text-muted-foreground tabular-nums"
          >{m.map_turnout_bins({
            low: String((SWINGS[0] ?? 0) * 100),
            mid: String((SWINGS[1] ?? 0) * 100),
            high: String((SWINGS[2] ?? 0) * 100),
          })}</span
        >
      </div>
    {:else if view === "turnout"}
      <div class="flex flex-wrap items-center gap-2.5" aria-hidden="true">
        <span class="text-muted-foreground">{m.map_turnout_key()}</span>
        <span
          class="flex items-center gap-1.5 font-figures text-xs tabular-nums"
        >
          <span>{percent(edges[0] ?? 0)}</span>
          <span class="flex">
            {#each STEPS.slice(0, edges.length + 1) as strength (strength)}
              <span
                class="block-3 inline-6 first:rounded-s-xs last:rounded-e-xs"
                style:background-color="color-mix(in oklab, var(--foreground) {strength}%,
                var(--map-low))"
              ></span>
            {/each}
          </span>
          <span>{percent(edges.at(-1) ?? 0)}</span>
        </span>
        {#if turnouts.size < places.rows.length}
          <span
            class="flex items-baseline gap-1.5 text-xs text-muted-foreground"
            ><span>{m.map_turnout_none()}</span><span
              class="font-figures tabular-nums"
              >{count(places.rows.length - turnouts.size)}</span
            ></span
          >
        {/if}
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
                {strength}%, var(--map-low))"
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
            {#if won > 0}
              <span
                class="font-figures text-xs text-muted-foreground tabular-nums"
                >{won}</span
              >
            {/if}
          </button>
        {/each}
        {#if blanks.size > 0}
          <span
            class="flex items-center gap-1.5 px-1.5 text-[0.8125rem] text-muted-foreground block-7"
            title={[...blanks.values()].join(", ")}
          >
            <span
              class="rounded-xs bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_3px)] ring-1 ring-border block-2.5 inline-2.5"
            ></span>
            <span>{m.map_blank_key()}</span>
            <span class="font-figures text-xs tabular-nums">{blanks.size}</span>
          </span>
        {/if}
      </div>
      {#if isDeclared}{:else if view === "bubbles"}
        <span class="text-muted-foreground"
          >{m.map_bubbles_key({ votes: votes.toLowerCase() })}</span
        >
      {:else}
        <div class="flex items-center gap-2.5">
          <span
            class={cn(
              "flex items-center gap-1.5 font-figures text-xs tabular-nums",
              { hidden: !isShaded },
            )}
            aria-hidden="true"
          >
            <span>{m.map_margin_under({ points: "5" })}</span>
            <span class="flex">
              {#each STRENGTHS as strength (strength)}
                <span
                  class="block-3 inline-6 first:rounded-s-xs last:rounded-e-xs"
                  style:background-color="color-mix(in oklab, #3d434b {strength}%,
                  var(--map-low))"
                ></span>
              {/each}
            </span>
            <span>{m.map_margin_over({ points: "20" })}</span>
          </span>
          <button
            type="button"
            class={cn(
              "flex items-center gap-1.5 rounded-xs px-1.5 py-0.5 font-semibold ring-1 ring-border hover:bg-secondary",
              { "bg-foreground text-background hover:bg-foreground": isShaded },
            )}
            aria-pressed={isShaded}
            onclick={() => (isShaded = !isShaded)}>{m.map_margin()}</button
          >
        </div>
      {/if}
    {/if}
    {#if isUnmatched && span != null}
      {const redrawn = $derived(
        unmatched
          .values()
          .filter((overlap) => overlap != null)
          .toArray().length,
      )}
      <span class="flex flex-wrap items-center gap-x-1.5 text-muted-foreground">
        <span
          class="rounded-xs bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_4px)] ring-1 ring-border block-3 inline-4"
        ></span>
        <span class="text-foreground">{m.map_unmatched_key()}</span>
        <span class="font-figures text-xs tabular-nums"
          >{[
            redrawn > 0
              ? m.map_unmatched_redrawn({
                  count: count(redrawn),
                  year: span.from,
                })
              : null,
            unmatched.size > redrawn
              ? m.map_unmatched_missing({
                  count: count(unmatched.size - redrawn),
                  year: span.forward ? span.to : span.from,
                })
              : null,
          ]
            .filter((part) => part != null)
            .join(", ")}</span
        >
      </span>
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
  {#if credited.length > 0}
    {const notices = $derived(
      credited.filter((credit) => /©|: |\.$|^Contains /u.test(credit)),
    )}
    {const publishers = $derived(
      credited.filter(
        (credit) => !notices.some((notice) => notice.includes(credit)),
      ),
    )}
    <div class="grid gap-0.5 px-1 text-xs text-muted-foreground">
      {#if publishers.length > 0}
        <p>
          {m.map_boundaries({
            publishers: new Intl.ListFormat(getLocale(), {
              type: "conjunction",
            }).format(publishers),
          })}
        </p>
      {/if}
      {#each notices as notice (notice)}
        <p>{notice}</p>
      {/each}
    </div>
  {/if}

  {#if adjoining.length > 0}
    <nav
      class="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-1 text-[0.8125rem]"
      aria-label={m.map_nearby()}
    >
      <span class="text-muted-foreground">{m.map_nearby()}</span>
      {#each adjoining as entry (entry.k)}
        <a
          class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={resolve("/[...path]", { path: entry.path })}
          onpointerenter={() => (near = entry.k)}
          onpointerleave={() => {
            if (!nearPinned) {
              near = null;
            }
          }}>{entry.name}</a
        >
      {/each}
    </nav>
  {/if}
  <p class="px-1 text-[0.8125rem] text-muted-foreground sm:hidden">
    {m.map_pick_touch()}
  </p>
  {#if nearPinned && beside != null && pinned == null}
    {const place = $derived(beside)}
    <div
      class="fixed inset-x-0 inset-be-0 z-40 grid gap-2 rounded-t-lg bg-card px-4 pbs-3 pbe-[max(1rem,env(safe-area-inset-bottom))] shadow-menu ring-1 ring-border sm:hidden"
      role="region"
      aria-label={place.name}
      data-export-skip
      transition:fly={{ y: 48, duration: calm ? 0 : 220 }}
    >
      <div class="flex items-center justify-between gap-3">
        <span class="font-semibold">{place.name}</span>
        <button
          type="button"
          class="-me-2 grid place-items-center rounded-sm text-muted-foreground block-10 inline-10 hover:bg-secondary"
          aria-label={m.map_sheet_close()}
          onclick={() => {
            nearPinned = false;
            near = null;
          }}
        >
          <XIcon class="block-4 inline-4" />
        </button>
      </div>
      <a
        class="flex items-center justify-center rounded-sm border border-foreground bg-marker text-sm font-semibold text-[#15171a] block-11"
        href={resolve("/[...path]", { path: place.path })}
        >{m.map_open({ place: place.name })}</a
      >
    </div>
  {/if}
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
            {percent(entry.share, precision(current.margin))}</span
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
    {#if isCompared && current.earlier == null && span != null}
      <span class="text-xs text-pretty text-muted-foreground"
        >{current.redrawn == null
          ? m.map_unmatched_missing_line({
              year: span.forward ? span.to : span.from,
            })
          : m.map_unmatched_redrawn_line({
              year: span.from,
              percent: percent(current.redrawn),
            })}</span
      >
    {/if}
    {#if current.valid === 0 && current.leader != null}
      <span
        class="flex items-center gap-2 text-[0.8125rem]"
        style:--party={colors[current.leader]}
      >
        <span class="rounded-xs bg-(--party) block-2.5 inline-2.5"></span>
        <span class="font-semibold"
          >{places.legend[current.leader]?.label ?? names[current.leader]}</span
        >
        {#if !isDeclared}
          <span class="text-muted-foreground">{m.map_unopposed()}</span>
        {/if}
      </span>
    {/if}
    {#if current.turnout != null}
      <span class="flex items-baseline justify-between gap-3 text-xs">
        <span class="font-semibold"
          >{m.map_turnout_line({
            share: percent(current.turnout[0] / current.turnout[1]),
          })}</span
        >
        <span class="font-figures text-muted-foreground tabular-nums"
          >{#if current.turnedBefore != null}{m.map_turnout_change({
              points: points(
                current.turnout[0] / current.turnout[1] - current.turnedBefore,
              ),
            })}{:else}{m.stat_turnout_count({
              voters: count(current.turnout[0]),
              registered: count(current.turnout[1]),
            })}{/if}</span
        >
      </span>
    {/if}
    <span class="flex items-baseline justify-between gap-3 text-xs">
      <span class="text-muted-foreground"
        >{#if current.valid > 0}{votes}
          <span class="font-figures tabular-nums">{count(current.valid)}</span
          >{/if}</span
      >
      {#if current.path != null && !isSheet && pinned == null}
        <span class="text-muted-foreground">{m.map_click_open()}</span>
      {/if}
    </span>
  {/if}
{/snippet}
