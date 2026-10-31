<script lang="ts" module>
  let handoff: { path: string; image: string } | null = null;
  const warmed: Record<string, boolean> = {};
</script>

<script lang="ts">
  import type * as Maplibre from "maplibre-gl";
  import { goto, preloadData } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import ArrowUpLeftIcon from "@lucide/svelte/icons/arrow-up-left";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import ChevronUpIcon from "@lucide/svelte/icons/chevron-up";
  import GlobeIcon from "@lucide/svelte/icons/globe";
  import MapIcon from "@lucide/svelte/icons/map";
  import MinusIcon from "@lucide/svelte/icons/minus";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import ScanIcon from "@lucide/svelte/icons/scan";
  import XIcon from "@lucide/svelte/icons/x";
  import { untrack } from "svelte";
  import { on } from "svelte/events";
  import { fade, fly } from "svelte/transition";
  import type { Box } from "#lib/map/engine.svelte.js";
  import Export from "#lib/components/export.svelte";
  import Term from "#lib/components/term.svelte";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { isHover, isTouch } from "#lib/explain.svelte.js";
  import { count, percent, plural, points, precision } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { basemap, SCHEMES } from "#lib/map/basemap.js";
  import { Engine, largest, proportional } from "#lib/map/engine.svelte.js";
  import { covering } from "#lib/map/hexes.js";
  import { isNarrowLayout } from "#lib/narrow-layout.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import { MODES, unit } from "./unit.js";

  type Places = NonNullable<
    Extract<PageData, { kind: "contest" }>["contest"]["places"]
  >;

  let {
    places,
    isTally = false,
    colors,
    tiles,
    own,
    nested = null,
    frame,
    limit = null,
    blank = null,
    outlying = null,
    parted = null,
    approximate,
    noun,
    votes,
    names,
    levels,
    home = null,
    span,
    only = $bindable(null),
    focus = $bindable(null),
    shade = "winner",
    isLinked = false,
    pick,
    onshade,
    sources = null,
    explore = null,
    neighbors = null,
    up = null,
    tallies = null,
    hexes = null,
  }: {
    places: Places;
    isTally?: boolean;
    colors: Array<string>;
    tiles: string;
    own: {
      tiles: string;
      k: number;
      parted?: { isOnly: boolean; ks: Array<number> } | null;
    } | null;
    nested?: { tiles: string; ks: Array<number> } | null;
    frame: Box;
    limit?: Box | null;
    blank?: Array<{
      k: number;
      name: string;
      isHeld: boolean;
      isOutside: boolean;
      isPending: boolean;
    }> | null;
    outlying?: Array<{
      k: number;
      code?: string;
      group: "territory" | "overseas" | "abroad";
    }> | null;
    parted?: { isOnly: boolean; ks: Array<number> } | null;
    approximate: Array<number>;
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
      isPending?: boolean;
    }>;
    home?: string | null;
    span: { from: string; to: string; forward: boolean } | null;
    only?: number | string | null;
    focus?: number | null;
    shade?: (typeof MODES)[number] | number;
    isLinked?: boolean;
    pick?: (code: string) => void;
    onshade?: (shade: (typeof MODES)[number] | number) => void;
    sources?: Array<string> | null;
    explore?: string | null;
    neighbors?:
      | (() => Promise<
          Array<{ k: number; name: string; path: string; isAdjacent: boolean }>
        >)
      | null;
    up?: { name: string; path: string } | null;
    tallies?: Array<number | null> | null;
    hexes?: Array<{
      credit: string | null;
      cells: Array<{ k: number; q: number; r: number }>;
      borders: Array<{ isNation: boolean; edges: Array<Array<number>> }>;
      labels: Array<{ q: number; r: number; text: string; k?: number }>;
      isPartial: boolean;
    }> | null;
  } = $props();

  const BINS = [0.05, 0.1, 0.2, 0.3, 0.45];
  const LEANS = [26, 40, 54, 68, 84, 100];
  const STRENGTHS = [34, 54, 76, 100];
  const STEPS = [16, 36, 56, 78, 100];
  const SWINGS = [0.02, 0.05, 0.1];
  const COMPARED = new Set(["swing", "arrows", "flips", "turnout_change"]);

  const uid = $props.id();
  let opening = $state<number | null>(null);
  let going = $state<string | null>(null);
  let listing = $state<string | null>(null);
  const globe = $derived(
    levels.length > 1
      ? levels.find((level) => level.code === "world.country")
      : undefined,
  );
  const tiers = $derived(levels.filter((level) => level !== globe));
  const narrow = $derived(isNarrowLayout());
  let stripHeight = $state(0);
  let ladder = $state<Awaited<ReturnType<typeof orpc.contests.context.call>>>(
    [],
  );
  let sight = $state<{
    x: number;
    y: number;
    width: number;
    height: number;
  } | null>(null);
  let nook = $state({
    isTop: false,
    isEnd: false,
    isCompact: false,
    isPlaced: false,
  });
  let inset: HTMLElement | null = null;
  let footprint = { width: 0, height: 0 };
  let upHeight = $state(0);
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
  let pinned = $state<number | null>(null);
  let empty = $state<number | null>(null);
  let pointer = $state<{ x: number; y: number } | null>(null);
  let near = $state<number | null>(null);
  let nearPinned = $state(false);
  let nearby = $state<
    Array<{ k: number; name: string; path: string; isAdjacent: boolean }>
  >([]);
  let seen = $state<Array<number>>([]);
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
        return entry == null ||
          k === own?.k ||
          (!entry.isAdjacent && nearby.some((other) => other.isAdjacent))
          ? []
          : [entry];
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
      keep({ shade: shading === shade ? null : String(shading) });
    }
    onshade?.(shading);
  });
  let isBasemap = $state(page.url.searchParams.get("basemap") !== "off");
  const scheme = $derived(tiles.split(/[./]/u)[0] ?? "");
  const engine = new Engine(
    untrack(() => frame),
    (width, height) => {
      const edge = Math.max(24, Math.round(Math.min(width, height) * 0.06));
      return {
        top: edge + (up == null ? 0 : 20),
        right: edge + 36,
        bottom: Math.max(edge, stripHeight + 16),
        left: edge,
      };
    },
    {
      cooperative: true,
      limit: untrack(() => limit),
      gestures: {
        "CooperativeGesturesHandler.WindowsHelpText": m.map_gesture_windows(),
        "CooperativeGesturesHandler.MacHelpText": m.map_gesture_mac(),
        "CooperativeGesturesHandler.MobileHelpText": m.map_gesture_mobile(),
      },
      onReady: () => {
        untrack(setup);
        track();
        engine.gl?.once("idle", nestle);
      },
      onMove: () => track(),
      onMoveEnd: () => {
        engine.gl?.once("idle", nestle);
        if (own != null) {
          seen = engine.rendered("veil");
        }
      },
      onPointer: ({ kind, point, isTouch }) => {
        if (kind === "leave") {
          clearTimeout(dwell);
          pointer = null;
          empty = null;
          engine.cursor(false);
          if (pinned == null) {
            focus = null;
          }
          if (!nearPinned) {
            near = null;
          }
          return;
        }
        const id = engine.pick(point);
        const k = id != null && rows.has(id) ? id : null;
        const other =
          k == null && own != null ? engine.pick(point, ["veil"]) : null;
        const next = other == null ? undefined : besides.get(other);
        if (kind === "move") {
          pointer = point;
          const path = k == null ? null : rows.get(k)?.path;
          empty =
            k == null && id != null && (blanks.has(id) || notes.has(id))
              ? id
              : null;
          if (!nearPinned) {
            near = next?.k ?? null;
          }
          engine.cursor(path != null || next != null);
          if (pinned == null && focus !== k) {
            focus = k;
            clearTimeout(dwell);
            if (path != null) {
              dwell = setTimeout(() => {
                const href = unit(path);
                if (focus === k && warmed[href] !== true) {
                  warmed[href] = true;
                  void preloadData(href);
                }
              }, 350);
            }
          }
          return;
        }
        if (k == null) {
          pinned = null;
          focus = null;
          if (next == null) {
            nearPinned = false;
            near = null;
          } else if (isTouch && !(nearPinned && near === next.k)) {
            near = next.k;
            nearPinned = true;
          } else {
            open(next.k, next.path, own?.tiles);
          }
        } else if (isTouch && pinned !== k) {
          nearPinned = false;
          near = null;
          pinned = k;
          focus = k;
        } else {
          open(k);
        }
      },
    },
  );
  const source = engine.tiles(untrack(() => tiles));

  untrack(() => {
    if (own != null) {
      void orpc.contests.context.call({ k: own.k }).then(
        (found) => {
          ladder = found;
          const top = found.at(-1);
          if (top != null) {
            engine.widen(top.frame);
          }
        },
        () => {},
      );
    }
  });

  const context = $derived.by(() => {
    if (sight == null) {
      return ladder.at(-1) ?? null;
    }
    const seen = Math.sqrt(sight.width * sight.height);
    return (
      ladder.find(
        (rung) => Math.sqrt(rung.extent.width * rung.extent.height) >= seen * 2,
      ) ??
      ladder.at(-1) ??
      null
    );
  });

  $effect(() => {
    const k = context?.k;
    const tiles = context?.tiles;
    if (engine.gl == null) {
      return;
    }
    untrack(() => {
      engine.drop("context");
      if (k != null && tiles != null) {
        engine.add(
          {
            id: "context",
            type: "line",
            source: engine.tiles(tiles, "context"),
            "source-layer": "areas",
            filter: ["==", ["id"], k],
            paint: {
              "line-color": "var(--muted-foreground)",
              "line-width": 1.5,
            },
          },
          "lines",
          { group: "context" },
        );
      }
    });
  });

  const overview = $derived.by(() => {
    if (context == null || sight == null || isSummary) {
      return null;
    }
    const { extent } = context;
    const latitude =
      2 * Math.atan(Math.exp(-(sight.y + sight.height / 2) / 6378.137)) -
      Math.PI / 2;
    if (
      Math.sqrt(sight.width * sight.height) * Math.cos(latitude) >= 40 ||
      sight.width * sight.height >= extent.width * extent.height * 0.35
    ) {
      return null;
    }
    return {
      ...sight,
      isDot:
        sight.width < extent.width * 0.05 &&
        sight.height < extent.height * 0.05,
      cx: sight.x + sight.width / 2,
      cy: sight.y + sight.height / 2,
      r: Math.max(extent.width, extent.height) * 0.035,
    };
  });

  function track() {
    const bounds = engine.gl?.getBounds();
    if (bounds == null) {
      return;
    }
    const x = (lng: number) => (lng * Math.PI * 6378.137) / 180;
    const y = (lat: number) =>
      -Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) * 6378.137;
    sight = {
      x: x(bounds.getWest()),
      y: y(bounds.getNorth()),
      width: x(bounds.getEast()) - x(bounds.getWest()),
      height: y(bounds.getSouth()) - y(bounds.getNorth()),
    };
  }
  function nestle() {
    if (
      overview == null ||
      inset == null ||
      engine.gl?.getLayer("fill") == null ||
      !engine.gl.isSourceLoaded(source) ||
      !engine.gl.areTilesLoaded()
    ) {
      return;
    }
    if (!nook.isCompact) {
      footprint = { width: inset.offsetWidth, height: inset.offsetHeight };
    }
    const isNarrow = matchMedia("(width < 40rem)").matches;
    const scored = [
      { isTop: false, isEnd: false },
      { isTop: true, isEnd: false },
      { isTop: isNarrow, isEnd: true },
    ].map((spot) => {
      const share = (scale: number) => {
        const width = footprint.width * scale + 12;
        const height = footprint.height * scale + 12;
        const x = spot.isEnd ? boxWidth - 8 - width : 8;
        const y = spot.isTop
          ? 8 + (spot.isEnd || up == null ? 0 : upHeight + 8)
          : boxHeight - 8 - height;
        let hits = 0;
        for (let column = 0; column <= 6; column += 1) {
          for (let row = 0; row <= 5; row += 1) {
            const point = {
              x: x + (width * column) / 6,
              y: y + (height * row) / 5,
            };
            if (
              engine.pick(point, ["fill", "nested-fill"]) != null &&
              engine.pick(point, ["veil"]) == null
            ) {
              hits += 1;
            }
          }
        }
        return hits / 42;
      };
      const full = share(1);
      const compact = full === 0 ? 0 : share(0.5);
      return { ...spot, rank: full === 0 ? 0 : 1 + compact };
    });
    const best = scored.toSorted((a, b) => a.rank - b.rank)[0];
    if (best == null) {
      return;
    }
    const held =
      (nook.isPlaced
        ? scored.find(
            (spot) => spot.isTop === nook.isTop && spot.isEnd === nook.isEnd,
          )
        : null) ?? best;
    const chosen =
      held.rank - best.rank > (best.rank > 1 ? 0.1 : 0) ? best : held;
    nook = {
      isTop: chosen.isTop,
      isEnd: chosen.isEnd,
      isCompact: chosen.rank > 0,
      isPlaced: true,
    };
  }
  const outer = untrack(() => (own == null ? null : engine.tiles(own.tiles)));
  const inside = untrack(() =>
    nested == null ? null : engine.tiles(nested.tiles),
  );

  const rows = $derived(
    new Map(
      [...places.rows, ...(places.districts ?? [])].map((row) => [row.k, row]),
    ),
  );
  const seated = $derived.by(() => {
    const nested = Map.groupBy(places.districts ?? [], (row) => row.within);
    const sum = (list: Array<number>) => {
      let total = 0;
      for (const value of list) {
        total += value;
      }
      return total;
    };
    const totals = new Map(
      places.rows.map((row) => {
        const held = sum((row.held ?? []).map(([, won]) => won));
        const own =
          row.seats ??
          (held > 0 || !row.contest || row.leader == null ? held : 1);
        const within = sum(
          (nested.get(row.k) ?? []).map((district) => district.seats ?? 0),
        );
        return [row.k, own + within] as const;
      }),
    );
    return totals.values().some((seats) => seats > 1)
      ? new Map(totals.entries().filter(([, seats]) => seats > 0))
      : null;
  });
  let layout = $state<"map" | "hexes">("hexes");
  const hexed = $derived(
    covering(hexes, { has: (k) => rows.has(k), size: places.rows.length }),
  );
  const headings: Record<string, [number, number]> = {
    ArrowRight: [1, 0],
    ArrowLeft: [-1, 0],
    ArrowDown: [0, 1],
    ArrowUp: [0, -1],
  };
  const framed = (left: number, top: number, right: number, bottom: number) => {
    const width = Math.max(right - left, 12);
    const height = Math.max(bottom - top, 9);
    return {
      x: (left + right - width) / 2,
      y: (top + bottom - height) / 2,
      width,
      height,
    };
  };
  const hexagon = (x: number, y: number) =>
    `M${x.toFixed(3)} ${(y - 1).toFixed(3)}l0.866 0.5v1l-0.866 0.5l-0.866 -0.5v-1z`;
  const tiled = $derived.by(() => {
    if (hexed == null) {
      return null;
    }
    const center = (q: number, r: number) => ({
      x: Math.sqrt(3) * (q + (r & 1) / 2),
      y: -1.5 * r,
    });
    const cells = hexed.cells.map((cell) => {
      const at = center(cell.q, cell.r);
      return { k: cell.k, ...at, d: hexagon(at.x, at.y) };
    });
    const corners = [
      [0, -1],
      [0.866, -0.5],
      [0.866, 0.5],
      [0, 1],
      [-0.866, 0.5],
      [-0.866, -0.5],
    ];
    const line = (edges: Array<Array<number>>) =>
      edges
        .map(([q = 0, r = 0, e = 1]) => {
          const at = center(q, r);
          const side = Math.abs(e) - 1;
          const [ax = 0, ay = 0] = corners[side] ?? [];
          const [bx = 0, by = 0] = corners[(side + 1) % 6] ?? [];
          return `M${(at.x + ax).toFixed(3)} ${(at.y + ay).toFixed(3)}L${(at.x + bx).toFixed(3)} ${(at.y + by).toFixed(3)}`;
        })
        .join("");
    let left = Infinity;
    let right = -Infinity;
    let top = Infinity;
    let bottom = -Infinity;
    for (const cell of cells) {
      left = Math.min(left, cell.x);
      right = Math.max(right, cell.x);
      top = Math.min(top, cell.y);
      bottom = Math.max(bottom, cell.y);
    }
    return {
      cells,
      at: new Map(cells.map((cell) => [cell.k, cell])),
      ...framed(left - 1.2, top - 1.4, right + 1.2, bottom + 1.4),
      seam: 0.12,
      size: 0.62,
      credit: hexed.credit,
      labels: hexed.labels.map((label) => ({
        ...center(label.q, label.r),
        text: label.text,
        seats: seated?.get(label.k ?? -1) ?? null,
      })),
      nations: line(
        hexed.borders.flatMap((border) =>
          border.isNation ? border.edges : [],
        ),
      ),
      regions: line(
        hexed.borders.flatMap((border) =>
          border.isNation ? [] : border.edges,
        ),
      ),
    };
  });
  const views = $derived([
    "map" as const,
    ...(tiled == null ? [] : ["hexes" as const]),
  ]);
  const grid = $derived(layout === "hexes" ? tiled : null);
  const isSummary = $derived(grid != null);
  let zoom = $state({ scale: 1, x: 0, y: 0 });
  let touches: Array<{ id: number; x: number; y: number }> = [];
  const pinch = (
    svg: SVGSVGElement,
    moved: { id: number; x: number; y: number },
  ) => {
    const matrix = svg.getScreenCTM()?.inverse();
    const before = touches.slice(0, 2);
    touches = touches.map((touch) => (touch.id === moved.id ? moved : touch));
    const after = touches.slice(0, 2);
    if (
      grid == null ||
      matrix == null ||
      (after.length < 2 && zoom.scale <= 1)
    ) {
      return;
    }
    const center = (list: typeof touches) => {
      let x = 0;
      let y = 0;
      for (const touch of list) {
        x += touch.x / list.length;
        y += touch.y / list.length;
      }
      return new DOMPoint(x, y).matrixTransform(matrix);
    };
    const spread = (list: typeof touches) => {
      const [a, b] = list;
      return a == null || b == null ? 1 : Math.hypot(a.x - b.x, a.y - b.y) || 1;
    };
    const scale = Math.min(
      Math.max((zoom.scale * spread(after)) / spread(before), 1),
      8,
    );
    const from = center(before);
    const to = center(after);
    const ratio = zoom.scale / scale;
    const x = from.x - (to.x - grid.x - zoom.x) * ratio - grid.x;
    const y = from.y - (to.y - grid.y - zoom.y) * ratio - grid.y;
    zoom = {
      scale,
      x: Math.min(Math.max(x, 0), grid.width - grid.width / scale),
      y: Math.min(Math.max(y, 0), grid.height - grid.height / scale),
    };
  };
  const tinted = $derived.by(() => {
    void engine.theme;
    return isSummary
      ? new Map(fills((css) => engine.read(css)))
      : new Map<number, string>();
  });
  const credited = $derived(
    grid == null
      ? (sources ?? engine.credits)
      : grid.credit == null
        ? []
        : [m.map_hex_credit({ credit: grid.credit })],
  );
  const stretch = $derived(
    grid == null ? 1 : Math.min(boxWidth / grid.width, boxHeight / grid.height),
  );
  const place = (k: number) => {
    const cell = grid?.at.get(k);
    return grid == null || cell == null
      ? null
      : {
          x:
            (boxWidth - grid.width * stretch) / 2 + (cell.x - grid.x) * stretch,
          y:
            (boxHeight - grid.height * stretch) / 2 +
            (cell.y - grid.y) * stretch,
        };
  };
  const inner = $derived(new Set(nested?.ks ?? []));
  const blanks = $derived(
    new Map(
      (blank ?? [])
        .filter((entry) => !entry.isOutside)
        .map((entry) => [entry.k, entry]),
    ),
  );
  const notes = $derived(
    new Map((places.noted ?? []).map((entry) => [entry.k, entry])),
  );
  const missing = $derived(
    blanks
      .values()
      .filter((entry) => entry.isHeld)
      .toArray(),
  );
  const pending = $derived(
    blanks
      .values()
      .filter((entry) => entry.isPending)
      .toArray(),
  );
  const idle = $derived(
    blanks
      .values()
      .filter((entry) => !entry.isHeld && !entry.isPending)
      .toArray(),
  );
  const remote = $derived.by(() => {
    const codes = new Map((outlying ?? []).map((entry) => [entry.k, entry]));
    const painted = new Map(fills((css) => css));
    return [
      ...places.rows
        .filter(
          (row) =>
            codes.has(row.k) || (row.isPool === true && row.leader != null),
        )
        .map((row) => ({
          k: row.k,
          name: row.name,
          leader: row.leader,
          isBlank: false,
        })),
      ...[...missing, ...pending]
        .filter((entry) => codes.has(entry.k))
        .map((entry) => ({
          k: entry.k,
          name: entry.name,
          leader: null,
          isBlank: true,
        })),
    ]
      .map((entry) => ({
        ...entry,
        code: codes.get(entry.k)?.code,
        group: codes.get(entry.k)?.group ?? "pool",
        fill:
          painted.get(entry.k) ??
          (entry.leader == null ? undefined : colors[entry.leader]),
      }))
      .toSorted((a, b) =>
        (a.code ?? a.name).localeCompare(b.code ?? b.name, getLocale(), {
          numeric: true,
        }),
      );
  });
  const segments = $derived(
    (["territory", "overseas", "abroad", "pool"] as const).flatMap((group) => {
      const entries = remote.filter((entry) => entry.group === group);
      return entries.length === 0 ? [] : [{ group, entries }];
    }),
  );
  const away = $derived(
    new Set([
      ...(outlying ?? []).map((entry) => entry.k),
      ...places.rows
        .filter(
          (row) =>
            parted != null && parted.ks.includes(row.k) !== parted.isOnly,
        )
        .map((row) => row.k),
    ]),
  );
  const inland: Maplibre.ExpressionSpecification = $derived([
    "all",
    [
      "!",
      [
        "in",
        ["id"],
        [
          "literal",
          [
            ...(blank ?? [])
              .filter((entry) => entry.isOutside)
              .map((entry) => entry.k),
            ...(outlying ?? []).map((entry) => entry.k),
          ],
        ],
      ],
    ],
    parted == null
      ? true
      : parted.isOnly
        ? ["in", ["id"], ["literal", parted.ks]]
        : ["!", ["in", ["id"], ["literal", parted.ks]]],
  ]);
  const around: Maplibre.ExpressionSpecification = $derived([
    "!",
    [
      "in",
      ["id"],
      [
        "literal",
        [
          ...places.rows.map((row) => row.k),
          ...(blank ?? [])
            .filter((entry) => !entry.isOutside)
            .map((entry) => entry.k),
        ],
      ],
    ],
  ]);
  const skipped = $derived.by(() => {
    if (typeof view !== "number") {
      return [];
    }
    const index = view;
    return places.rows.filter((row) => row.absent?.includes(index) === true);
  });
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
  const isProportional = $derived(
    proportional(
      places.rows.flatMap((row) => (row.leader == null ? [] : [row.held])),
    ),
  );
  const view = $derived(
    (!isCompared && typeof shading === "string" && COMPARED.has(shading)) ||
      (shading === "split" && !isProportional) ||
      (shading === "turnout" && turnouts.size === 0) ||
      (shading === "turnout_change" && surges.size === 0) ||
      (isTally &&
        (typeof shading === "number" ||
          shading === "swing" ||
          shading === "arrows"))
      ? "winner"
      : shading,
  );
  const isBanded = $derived(
    view === "split" || (view === "winner" && !isProportional),
  );
  const leading = (row: (typeof places.rows)[number]) =>
    view === "winner" && isProportional && row.held != null
      ? largest(row.held, (index) => row.shares[index] ?? 0)
      : row.leader;
  const isUnmatched = $derived(
    unmatched.size > 0 && typeof view === "string" && COMPARED.has(view),
  );
  const isEstimated = $derived(
    places.estimated === true &&
      changes.size > 0 &&
      typeof view === "string" &&
      COMPARED.has(view),
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
    if (isSummary && grid != null) {
      return Math.min(2, Math.max(0.6, grid.width / grid.height));
    }
    const y = (latitude: number) =>
      Math.log(Math.tan(Math.PI / 4 + (latitude * Math.PI) / 360));
    const wide = ((frame.east - frame.west) * Math.PI) / 180;
    const tall = y(frame.north) - y(frame.south);
    return Math.min(2, Math.max(0.8, (wide / Math.max(tall, 0.0001)) * 1.04));
  });
  let isKeyed = $state(false);
  const leads = $derived.by(() => {
    const counted = Map.groupBy(
      places.rows.filter(
        (row) => row.leader != null && !(isBanded && row.held != null),
      ),
      (row) => leading(row) ?? 0,
    )
      .entries()
      .map(
        ([index, won]) =>
          [index, won.filter((row) => row.isCounted !== false).length] as const,
      )
      .toArray()
      .toSorted((a, b) => b[1] - a[1]);
    const listed = new Set(counted.map(([index]) => index));
    return [
      ...counted,
      ...new Set(
        places.rows.flatMap((row) =>
          (row.held ?? []).flatMap(([index]) =>
            listed.has(index) ? [] : [index],
          ),
        ),
      )
        .values()
        .map((index) => [index, 0] as const),
    ];
  });
  const ramps = $derived(
    (isKeyed ? leads : leads.slice(0, 6)).flatMap(([index, won]) => {
      if (won === 0) {
        return [];
      }
      const tone = colors[index] ?? "var(--muted-foreground)";
      const [red = 0, green = 0, blue = 0] = (
        /\d+, \d+, \d+/u.exec(engine.read(tone))?.[0] ?? ""
      )
        .split(", ")
        .map(Number);
      const isFlat =
        Math.max(red, green, blue) - Math.min(red, green, blue) < 24;
      const counts = LEANS.map(() => 0);
      for (const row of places.rows) {
        if (
          leading(row) === index &&
          !(isBanded && row.held != null) &&
          row.isCounted !== false &&
          row.valid > 0 &&
          row.margin != null
        ) {
          const at = row.held == null ? bin(row.margin) : LEANS.length - 1;
          counts[at] = (counts[at] ?? 0) + 1;
        }
      }
      return [
        {
          index,
          name: names[index] ?? "",
          cells: LEANS.map((strength, at) => ({
            tone: `color-mix(in oklab, ${tone} ${isFlat ? 100 : strength}%, var(--map-low))`,
            count: counts[at] ?? 0,
          })),
        },
      ];
    }),
  );
  const anchor = untrack(() => (typeof shading === "number" ? shading : null));
  const parties = $derived(
    (isTally ? [] : places.legend)
      .map((item, index) => ({ index, name: names[index] ?? item.label }))
      .filter(
        (option) =>
          option.index === anchor ||
          places.rows.some((row) => (row.shares[option.index] ?? 0) > 0),
      ),
  );
  const options = $derived([
    ...parties.filter((option) => option.index === anchor),
    ...parties.filter((option) => option.index !== anchor && option.index < 3),
  ]);
  const extras = $derived(
    parties.filter((option) => !options.includes(option)),
  );
  const extra = $derived(extras.find((option) => option.index === view));
  const more = $derived(
    extras.every((option) =>
      places.legend[option.index]?.key.startsWith("person:"),
    )
      ? m.map_more_candidates()
      : m.map_more_parties(),
  );
  const edges = $derived.by(() => {
    if (typeof view !== "number") {
      return [];
    }
    const index = view;
    const sorted = places.rows
      .filter((row) => row.absent?.includes(index) !== true)
      .map((row) => row.shares[index] ?? 0)
      .toSorted((a, b) => a - b);
    const cuts = [0.2, 0.4, 0.6, 0.8].map(
      (at) => sorted[Math.floor(at * (sorted.length - 1))] ?? 0,
    );
    return [...new Set(cuts)];
  });
  const scale = $derived.by(() => {
    const values = [...turnouts.values()].toSorted((a, b) => a - b);
    if (values.length === 0) {
      return null;
    }
    const trim = values.length >= 20 ? 0.05 : 0;
    const low = values[Math.floor(trim * (values.length - 1))] ?? 0;
    const high = values[Math.ceil((1 - trim) * (values.length - 1))] ?? 0;
    const span = (size: number) =>
      Math.max(
        1,
        Math.ceil(high / size - 1e-9) - Math.floor(low / size + 1e-9),
      );
    const step =
      [0.01, 0.02, 0.025, 0.05, 0.1, 0.2].find((size) => span(size) <= 6) ??
      0.25;
    const count = span(step);
    const from = Math.floor(low / step + 1e-9) * step;
    return {
      from,
      ticks: Array.from({ length: count + 1 }, (_, index) =>
        index === 0
          ? Math.min(from, values[0] ?? from)
          : index === count
            ? Math.max(from + count * step, values.at(-1) ?? 0)
            : from + index * step,
      ),
      step,
      count,
      digits: step < 0.01 || step === 0.025 ? 1 : 0,
      tones: Array.from(
        { length: count },
        (_, index) =>
          `color-mix(in oklab, var(--turnout-high) ${count === 1 ? 100 : Math.round((index / (count - 1)) * 100)}%, var(--turnout-low))`,
      ),
    };
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
    if (anchor == null && isSummary && current != null) {
      anchor = place(current.k);
    }
    const projected =
      anchor == null && !isSummary && current?.at != null
        ? engine.project(current.at)
        : null;
    if (projected != null) {
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

  function open(k: number, path = rows.get(k)?.path, layer = tiles) {
    if (path == null) {
      return;
    }
    const href = unit(path);
    const box = away.has(k) ? null : engine.shape(engine.tiles(layer), k);
    if (box == null || calm) {
      opening = k;
      void goto(href).finally(() => {
        opening = null;
      });
      return;
    }
    opening = k;
    if (rows.has(k)) {
      pinned = k;
      focus = k;
    }
    let isMoving = true;
    let isGone = false;
    let exact: Box | null | undefined;
    const leave = () => {
      if (!isGone && opening === k) {
        isGone = true;
        const image = engine.snapshot();
        handoff =
          image == null ? null : { path: href.split("?")[0] ?? href, image };
        void goto(href).finally(() => {
          opening = null;
        });
      }
    };
    const settle = () => {
      const target = exact;
      if (isMoving || target === undefined) {
        return;
      }
      const span = Math.max(box.east - box.west, box.north - box.south);
      if (
        target == null ||
        (["west", "south", "east", "north"] as const).every(
          (side) => Math.abs(target[side] - box[side]) < span * 0.02,
        )
      ) {
        leave();
        return;
      }
      setTimeout(leave, 1200);
      engine.fit(target, leave);
    };
    const moved = () => {
      if (isMoving) {
        isMoving = false;
        settle();
      }
    };
    setTimeout(moved, 1200);
    engine.fit(box, moved);
    void preloadData(href).then(
      (result) => {
        const data: Partial<Extract<PageData, { kind: "contest" }>> =
          result.type === "loaded" ? result.data : {};
        exact =
          data.kind === "contest"
            ? (data.boundaries?.find(
                (entry) => entry.k != null && entry.frame != null,
              )?.frame ?? null)
            : null;
        settle();
      },
      () => {
        opening = null;
        void goto(href);
      },
    );
  }

  function fills(read: (css: string) => string) {
    const result: Array<[number, string]> = [];
    const empty = read(
      "color-mix(in oklab, var(--muted-foreground) 6%, var(--card))",
    );
    for (const entry of [...missing, ...pending]) {
      result.push([entry.k, empty]);
    }
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
        if (share != null && scale != null) {
          const at = Math.floor((share - scale.from) / scale.step + 1e-9);
          result.push([
            row.k,
            read(scale.tones[Math.min(scale.count - 1, Math.max(0, at))] ?? ""),
          ]);
        }
        continue;
      }
      if (row.leader == null) {
        if (
          (view === "winner" || view === "split") &&
          only == null &&
          row.declared != null
        ) {
          result.push([
            row.k,
            read(
              `color-mix(in oklab, ${swatch(row.declared.color, `party:${row.declared.party}`)} 100%, var(--map-low))`,
            ),
          ]);
        }
        continue;
      }
      const shown =
        typeof only === "number" &&
        (row.held?.some(([index]) => index === only) ?? false)
          ? only
          : (leading(row) ?? row.leader);
      const dimmed = only != null && typeof only === "number" && only !== shown;
      let strength = dimmed
        ? 10
        : isDeclared ||
            row.valid === 0 ||
            !isShaded ||
            (view === "winner" && isProportional && row.held != null)
          ? 100
          : (LEANS[bin(row.margin)] ?? 100);
      let tone = colors[shown] ?? "var(--muted-foreground)";
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
        if (row.absent?.includes(view) === true) {
          result.push([row.k, empty]);
          continue;
        }
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

  function setup() {
    engine.tiles(tiles);
    if (own != null) {
      engine.tiles(own.tiles);
    }
    if (nested != null) {
      engine.tiles(nested.tiles);
    }
    const areas = { source, "source-layer": "areas" } as const;
    const shown = (key: string): Maplibre.ExpressionSpecification => [
      "case",
      ["boolean", ["feature-state", key], false],
      1,
      0,
    ];
    engine.add(
      {
        id: "fill",
        type: "fill",
        ...areas,
        filter: own == null ? inland : ["all", inland, ["!", around]],
        paint: {
          "fill-color": [
            "coalesce",
            ["feature-state", "color"],
            "color-mix(in oklab, var(--muted-foreground) 14%, var(--card))",
          ],
        },
      },
      "fills",
      { rank: 0 },
    );
    for (const [id, image] of [
      ["split", "hatch"],
      ["blank", "absent"],
      ["norun", "norun"],
      ["unmatched", "hatch"],
    ] as const) {
      engine.add(
        {
          id,
          type: "fill",
          ...areas,
          filter: [
            "all",
            inland,
            [
              "in",
              ["id"],
              [
                "literal",
                id === "blank"
                  ? [...missing, ...pending].map((entry) => entry.k)
                  : [],
              ],
            ],
          ],
          paint: { "fill-pattern": image },
        },
        "fills",
      );
    }
    const side = Math.sqrt(
      (boxWidth * boxHeight) / Math.max(1, places.rows.length),
    );
    const settled = engine.gl?.getZoom() ?? 0;
    engine.add(
      {
        id: "seam",
        type: "line",
        ...areas,
        filter: own == null ? inland : ["all", inland, ["!", around]],
        paint: {
          "line-color": "var(--card)",
          "line-width":
            side < 16
              ? [
                  "interpolate",
                  ["exponential", 2],
                  ["zoom"],
                  settled,
                  Math.max(0.15, side / 16),
                  settled + 4,
                  1.6,
                ]
              : ["interpolate", ["linear"], ["zoom"], 2, 0.6, 6, 1, 10, 1.6],
        },
      },
      "fills",
    );
    engine.add(
      {
        id: "rough",
        type: "line",
        ...areas,
        filter: ["all", inland, ["in", ["id"], ["literal", approximate]]],
        paint: {
          "line-color":
            "color-mix(in oklab, var(--foreground) 45%, transparent)",
          "line-width": ["interpolate", ["linear"], ["zoom"], 2, 0.75, 8, 1.1],
          "line-dasharray": [3, 2.5],
        },
      },
      "fills",
    );
    if (own != null && outer != null) {
      const ownAreas = { source: outer, "source-layer": "areas" } as const;
      const beyond: Maplibre.ExpressionSpecification = [
        "all",
        ["!=", ["id"], own.k],
        own.parted == null
          ? true
          : own.parted.isOnly
            ? ["in", ["id"], ["literal", own.parted.ks]]
            : ["!", ["in", ["id"], ["literal", own.parted.ks]]],
      ];
      engine.add(
        {
          id: "veil",
          type: "fill",
          ...ownAreas,
          filter: beyond,
          paint: { "fill-color": "var(--card)", "fill-opacity": 0 },
        },
        "fills",
      );
      engine.add(
        {
          id: "outline",
          type: "line",
          ...ownAreas,
          filter: ["==", ["id"], own.k],
          paint: {
            "line-color": "var(--foreground)",
            "line-width": [
              "interpolate",
              ["linear"],
              ["zoom"],
              settled,
              1,
              settled + 3,
              1.75,
            ],
          },
        },
        "lines",
      );
      engine.add(
        {
          id: "near",
          type: "line",
          ...ownAreas,
          filter: beyond,
          paint: {
            "line-color": "var(--foreground)",
            "line-width": 1.5,
            "line-dasharray": [3, 2],
            "line-opacity": shown("near"),
          },
        },
        "lines",
      );
      seen = engine.rendered("veil");
      neighbors?.().then(
        (list) => {
          nearby = list;
        },
        () => {
          nearby = [];
        },
      );
    }
    if (nested != null && inside != null) {
      const nestedAreas: {
        source: string;
        "source-layer": string;
        filter: Maplibre.FilterSpecification;
      } = {
        source: inside,
        "source-layer": "areas",
        filter: ["in", ["id"], ["literal", nested.ks]],
      };
      engine.add(
        {
          id: "nested-fill",
          type: "fill",
          ...nestedAreas,
          paint: {
            "fill-color": [
              "coalesce",
              ["feature-state", "color"],
              "rgba(0, 0, 0, 0)",
            ],
          },
        },
        "fills",
        { rank: 1 },
      );
      engine.add(
        {
          id: "nested-seam",
          type: "line",
          ...nestedAreas,
          paint: { "line-color": "var(--card)", "line-width": 0.75 },
        },
        "fills",
      );
      engine.add(
        {
          id: "nested-focus",
          type: "line",
          ...nestedAreas,
          paint: {
            "line-color": "var(--foreground)",
            "line-width": 2.25,
            "line-opacity": shown("focus"),
          },
        },
        "focus",
      );
    }
    engine.points("dots", []);
    engine.points("arrows", []);
    engine.add(
      {
        id: "bubble",
        type: "circle",
        source: "dots",
        paint: {
          "circle-radius": ["coalesce", ["feature-state", "radius"], 0],
          "circle-color": [
            "coalesce",
            ["feature-state", "color"],
            "color-mix(in oklab, var(--muted-foreground) 14%, var(--card))",
          ],
          "circle-opacity": 0.86,
          "circle-stroke-color": "var(--card)",
          "circle-stroke-width": [
            "case",
            [">", ["coalesce", ["feature-state", "radius"], 0], 0],
            0.75,
            0,
          ],
        },
      },
      "points",
    );
    engine.add(
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
          "icon-halo-color": "var(--card)",
          "icon-halo-width": 1,
        },
      },
      "points",
    );
    engine.add(
      {
        id: "focus",
        type: "line",
        ...areas,
        filter: inland,
        paint: {
          "line-color": "var(--foreground)",
          "line-width": 2.25,
          "line-opacity": shown("focus"),
        },
      },
      "focus",
    );
  }

  let outline = $state<Array<Array<[number, number]>> | null>(
    untrack(() => (own == null ? [] : null)),
  );

  $effect(() => {
    if (own != null) {
      void orpc.contests.outline.call({ k: own.k }).then(
        (found) => {
          outline = found;
        },
        () => {},
      );
    }
  });

  $effect(() => {
    if (engine.gl == null) {
      return;
    }
    const shown = isSummary ? null : outline;
    const isDetailed = isBasemap;
    const polity = scheme;
    untrack(() => basemap(engine, polity, isDetailed, shown));
  });

  $effect(() => {
    if (engine.isLoaded) {
      still = null;
    }
  });

  $effect(() => {
    void engine.theme;
    if (engine.gl == null) {
      return;
    }
    const read = (css: string) => engine.read(css);
    const painted = fills(read);
    const hatched = [
      ...(isUnmatched ? unmatched.keys() : []),
      ...(isEstimated ? changes.keys() : []),
      ...(view === "turnout" || view === "turnout_change"
        ? [...places.rows, ...(places.districts ?? [])]
            .filter(
              (row) => !(view === "turnout" ? turnouts : surges).has(row.k),
            )
            .map((row) => row.k)
        : []),
    ];
    const splits =
      isBanded && only == null
        ? places.rows.flatMap((row) =>
            row.held == null || row.leader == null || inner.has(row.k)
              ? []
              : [
                  {
                    k: row.k,
                    image: engine.bands(
                      row.held.map(([index, seats]) => [
                        colors[index] ?? "var(--muted-foreground)",
                        seats,
                      ]),
                    ),
                  },
                ],
          )
        : [];
    const tips = [0, 1].map((index) =>
      read(colors[index] ?? "var(--muted-foreground)"),
    );
    const rough: Maplibre.FilterSpecification = [
      "all",
      inland,
      ["in", ["id"], ["literal", approximate]],
    ];
    untrack(() => {
      engine.filter("rough", rough);
      engine.filter("unmatched", [
        "all",
        inland,
        ["in", ["id"], ["literal", hatched]],
      ]);
      engine.filter("norun", [
        "all",
        inland,
        ["in", ["id"], ["literal", skipped.map((row) => row.k)]],
      ]);
      engine.filter("split", [
        "all",
        inland,
        ["in", ["id"], ["literal", splits.map((entry) => entry.k)]],
      ]);
      engine.style(
        "split",
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
      engine.points(
        "dots",
        places.rows.flatMap((row) =>
          row.at == null || away.has(row.k) ? [] : [{ k: row.k, at: row.at }],
        ),
      );
      engine.points(
        "arrows",
        view === "arrows"
          ? places.rows.flatMap((row) => {
              const change = changes.get(row.k)?.swing;
              if (
                row.at == null ||
                away.has(row.k) ||
                change == null ||
                Math.abs(change) < 0.001
              ) {
                return [];
              }
              const size = Math.min(1, Math.abs(change) / 0.2);
              const tilt = 12 + size * 48;
              return [
                {
                  at: row.at,
                  properties: {
                    color: tips[change >= 0 ? 0 : 1] ?? "",
                    size: 0.35 + size * 0.95,
                    rotate: change >= 0 ? -tilt : 180 + tilt,
                    order: size,
                  },
                },
              ];
            })
          : [],
      );
      const areas = new Map<number, Record<string, string | number>>();
      const deeper = new Map<number, Record<string, string | number>>();
      const bubbles = new Map<number, Record<string, string | number>>();
      for (const [k, color] of painted) {
        if (inner.has(k)) {
          if (view !== "bubbles") {
            deeper.set(k, { color });
          }
        } else if (view === "bubbles") {
          bubbles.set(k, {
            color,
            radius: Math.sqrt((rows.get(k)?.valid ?? 0) / biggest) * 28,
          });
        } else {
          areas.set(k, { color });
        }
      }
      engine.paint(source, areas);
      engine.paint("dots", bubbles);
      if (inside != null) {
        engine.paint(inside, deeper);
      }
    });
  });

  $effect(() => {
    const k = near;
    if (engine.gl != null) {
      untrack(() => engine.mark("near", outer, k));
    }
  });

  $effect(() => {
    const k = focus;
    if (engine.gl != null) {
      untrack(() =>
        engine.mark("focus", k != null && inner.has(k) ? inside : source, k),
      );
    }
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
    class="@container flex flex-wrap items-center justify-between gap-x-4 gap-y-2"
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
          class="shrink-0 text-sm/7 font-semibold whitespace-nowrap @xl:self-start"
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
            class="text-sm font-semibold min-inline-0 data-[size=default]:block-10 @xl:hidden"
            aria-labelledby="{uid}-shading"
          >
            <span class="truncate"
              >{typeof view === "number"
                ? (parties.find((option) => option.index === view)?.name ?? "")
                : {
                    winner: m.map_mode_winner,
                    split: m.map_mode_split,
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
            {#if isProportional}
              <Select.Item value="split" label={m.map_mode_split()} />
            {/if}
            {#each options as option (option.index)}
              <Select.Item value={String(option.index)} label={option.name} />
            {/each}
            {#if extras.length > 0}
              <Select.Group>
                <Select.GroupHeading>{more}</Select.GroupHeading>
                {#each extras as option (option.index)}
                  <Select.Item
                    value={String(option.index)}
                    label={option.name}
                  />
                {/each}
              </Select.Group>
            {/if}
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
        <div
          class="-my-1 flex flex-wrap gap-2 py-1 min-inline-0 @max-xl:hidden"
        >
          {#each [[{ value: "winner" as const, label: m.map_mode_winner() }, ...(isProportional ? [{ value: "split" as const, label: m.map_mode_split() }] : []), ...options.map( (option) => ({ value: option.index, label: option.name }) ), ...(extras.length > 0 ? [{ value: "more" as const, label: extra?.name ?? more }] : []), { value: "bubbles" as const, label: m.map_mode_bubbles() }, ...(turnouts.size > 0 ? [{ value: "turnout" as const, label: m.map_mode_turnout() }] : [])], isCompared ? [...(isTally ? [] : [{ value: "swing" as const, label: m.map_mode_swing() }, { value: "arrows" as const, label: m.map_mode_arrows() }]), { value: "flips" as const, label: m.map_mode_flips() }, ...(surges.size > 0 ? [{ value: "turnout_change" as const, label: m.map_mode_turnout_change() }] : [])] : []] as group, at (at)}
            {#if group.length > 0}
              <div class="scroll-fade-x flex max-inline-full">
                {#each group as choice (choice.value)}
                  {#if choice.value === "more"}
                    <DropdownMenu.Root>
                      <DropdownMenu.Trigger
                        class={cn(
                          "-ms-px flex items-center gap-1 border border-input bg-card ps-2.5 pe-2 text-[0.8125rem] font-semibold whitespace-nowrap keycap block-7 max-inline-44 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary xl:max-inline-none",
                          {
                            "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                              extra != null,
                          },
                        )}
                        aria-pressed={extra != null}
                        title={extra?.name}
                        ><span class="truncate">{choice.label}</span
                        ><ChevronDownIcon
                          class="shrink-0 block-3.5 inline-3.5"
                        /></DropdownMenu.Trigger
                      >
                      <DropdownMenu.Content
                        align="start"
                        class="max-block-80 min-inline-48"
                      >
                        <DropdownMenu.RadioGroup
                          value={extra == null ? "" : String(extra.index)}
                          onValueChange={(value) => (shading = Number(value))}
                        >
                          {#each extras as option (option.index)}
                            <DropdownMenu.RadioItem
                              class="gap-2"
                              value={String(option.index)}
                              closeOnSelect
                              ><span
                                class="shrink-0 rounded-xs block-2.5 inline-2.5"
                                style:background-color={colors[option.index]}
                              ></span>{option.name}</DropdownMenu.RadioItem
                            >
                          {/each}
                        </DropdownMenu.RadioGroup>
                      </DropdownMenu.Content>
                    </DropdownMenu.Root>
                  {:else}
                    <button
                      type="button"
                      class={cn(
                        "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold whitespace-nowrap keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                        {
                          "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                            view === choice.value,
                        },
                        {
                          "max-inline-44 xl:max-inline-none":
                            typeof choice.value === "number",
                        },
                      )}
                      title={typeof choice.value === "number"
                        ? choice.label
                        : undefined}
                      aria-pressed={view === choice.value}
                      onclick={() => (shading = choice.value)}
                      ><span class="truncate">{choice.label}</span></button
                    >
                  {/if}
                {/each}
              </div>
            {/if}
          {/each}
        </div>
      </div>
    {/if}
    {#if views.length > 1}
      <div class="flex" role="group" aria-label={m.map_view()}>
        {#each views as choice (choice)}
          <button
            type="button"
            class={cn(
              "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary pointer-coarse:block-10",
              {
                "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                  (grid == null ? "map" : layout) === choice,
              },
            )}
            aria-pressed={(grid == null ? "map" : layout) === choice}
            onclick={() => {
              layout = choice;
              zoom = { scale: 1, x: 0, y: 0 };
            }}
            >{choice === "hexes"
              ? m.map_view_hexes()
              : m.map_view_map()}</button
          >
        {/each}
      </div>
    {/if}
    {#if tiers.length > 1}
      <div
        class="scroll-fade-x flex max-inline-full"
        role="group"
        aria-label={m.map_level()}
      >
        {#each tiers as level (level.code)}
          {const isOn = $derived(
            going == null ? level.isCurrent : going === level.code,
          )}
          {const isBusy = $derived(
            level.isPending === true ||
              (going === level.code && !level.isCurrent),
          )}
          <svelte:element
            this={pick == null ? "a" : "button"}
            class={cn(
              "-ms-px flex items-center gap-1.5 border border-input bg-card px-2.5 text-[0.8125rem] font-semibold whitespace-nowrap keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary pointer-coarse:block-10",
              {
                "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                  isOn,
              },
            )}
            href={pick == null
              ? resolve(`/[...path]?${level.query}`, { path: level.path })
              : undefined}
            type={pick == null ? undefined : "button"}
            role={pick == null ? undefined : "button"}
            aria-current={pick == null && isOn ? "true" : undefined}
            aria-pressed={pick == null ? undefined : isOn}
            aria-busy={isBusy ? "true" : undefined}
            data-sveltekit-reset="false"
            onclick={(event: MouseEvent) => {
              if (pick != null) {
                pick(level.code);
                return;
              }
              if (
                event.metaKey ||
                event.ctrlKey ||
                event.shiftKey ||
                event.altKey ||
                event.button !== 0 ||
                level.isCurrent
              ) {
                return;
              }
              event.preventDefault();
              const url = new URL(location.href);
              const asked = new URLSearchParams(level.query);
              for (const [key, value] of asked) {
                if (value !== page.url.searchParams.get(key)) {
                  url.searchParams.set(key, value);
                }
              }
              url.pathname = resolve("/[...path]", { path: level.path });
              going = level.code;
              void goto(url, { reset: false }).finally(() => {
                going = null;
              });
            }}
            >{level.label}
            {#if isBusy}
              <span
                class="animate-spin rounded-full border-2 border-current/30 border-bs-current block-3 inline-3 motion-reduce:animate-none"
                aria-hidden="true"
              ></span>
            {:else}
              <span
                class="font-figures text-[0.6875rem] font-normal tabular-nums"
                >{level.count}</span
              >
            {/if}</svelte:element
          >
        {/each}
      </div>
    {/if}
    <div class="ms-auto flex items-center gap-2">
      {#if explore != null}
        <a
          href={page.url.searchParams.get("group") === "alliance"
            ? resolve("/map/[...path]?group=alliance", { path: explore })
            : resolve("/map/[...path]", { path: explore })}
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
  >
    <div
      class={cn(
        "transition-opacity duration-500 ease-out block-full inline-full motion-reduce:transition-none",
        { "opacity-0": !engine.isLoaded },
        { invisible: isSummary },
      )}
      {@attach engine.mount}
    ></div>
    {#if isSummary && grid != null}
      <svg
        class="absolute inset-0 block-full inline-full"
        viewBox="{grid.x + zoom.x} {grid.y + zoom.y} {grid.width /
          zoom.scale} {grid.height / zoom.scale}"
        style:touch-action={zoom.scale > 1 ? "none" : "pan-y"}
        role="group"
        aria-label={m.map_label({ noun })}
        onpointerdown={(event) => {
          if (event.pointerType === "touch") {
            touches = [
              ...touches,
              { id: event.pointerId, x: event.clientX, y: event.clientY },
            ];
          }
        }}
        onpointermove={(event) => {
          if (touches.some((touch) => touch.id === event.pointerId)) {
            pinch(event.currentTarget, {
              id: event.pointerId,
              x: event.clientX,
              y: event.clientY,
            });
          }
        }}
        onpointerup={(event) => {
          touches = touches.filter((touch) => touch.id !== event.pointerId);
        }}
        onpointercancel={(event) => {
          touches = touches.filter((touch) => touch.id !== event.pointerId);
        }}
        onpointerleave={() => {
          pointer = null;
          if (pinned == null) {
            focus = null;
          }
        }}
      >
        {#each grid.cells as cell (cell.k)}
          {const row = $derived(rows.get(cell.k))}
          <path
            class={cn("outline-none", { "cursor-pointer": row != null })}
            d={cell.d}
            fill={tinted.get(cell.k) ??
              "color-mix(in oklab, var(--muted-foreground) 14%, var(--card))"}
            stroke="var(--card)"
            stroke-width={grid.seam}
            role="button"
            tabindex={cell.k === (focus ?? grid.cells[0]?.k) ? 0 : -1}
            aria-label={row == null
              ? undefined
              : row.leader == null
                ? row.name
                : m.map_hex_seat({
                    name: row.name,
                    winner: names[row.leader] ?? "",
                  })}
            data-k={cell.k}
            onpointermove={(event) => {
              if (!isHover(event)) {
                return;
              }
              const bounds =
                event.currentTarget.ownerSVGElement?.parentElement?.getBoundingClientRect();
              if (bounds != null) {
                pointer = {
                  x: event.clientX - bounds.left,
                  y: event.clientY - bounds.top,
                };
              }
              if (pinned == null) {
                focus = cell.k;
              }
            }}
            onfocus={() => {
              pointer = null;
              focus = cell.k;
            }}
            onblur={() => {
              if (pinned == null) {
                focus = null;
              }
            }}
            onclick={(event) => {
              if (isTouch(event) && pinned !== cell.k) {
                pinned = cell.k;
                focus = cell.k;
              } else {
                open(cell.k);
              }
            }}
            onkeydown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                open(cell.k);
                return;
              }
              const [dx, dy] = headings[event.key] ?? [0, 0];
              if (dx === 0 && dy === 0) {
                return;
              }
              event.preventDefault();
              let next = null;
              let best = Infinity;
              for (const other of grid.cells) {
                const x = other.x - cell.x;
                const y = other.y - cell.y;
                const distance = Math.hypot(x, y);
                const along = (x * dx + y * dy) / distance;
                if (along > 0.5 && distance * (2 - along) < best) {
                  best = distance * (2 - along);
                  next = other.k;
                }
              }
              if (next != null) {
                event.currentTarget.ownerSVGElement
                  ?.querySelector<SVGPathElement>(`[data-k="${next}"]`)
                  ?.focus();
              }
            }}
          />
        {/each}
        <path
          d={grid.regions}
          fill="none"
          stroke="var(--foreground)"
          stroke-opacity="0.45"
          stroke-width="0.14"
          stroke-linecap="round"
          pointer-events="none"
        />
        <path
          d={grid.nations}
          fill="none"
          stroke="var(--foreground)"
          stroke-width="0.2"
          stroke-linecap="round"
          pointer-events="none"
        />
        {#each grid.size * stretch * zoom.scale < 7 ? [] : grid.labels as label (label.text)}
          <text
            class="fill-foreground font-semibold"
            x={label.x}
            y={label.seats == null ? label.y : label.y - grid.size * 0.42}
            font-size={label.seats == null ? grid.size : grid.size * 0.85}
            text-anchor="middle"
            dominant-baseline="central"
            stroke="var(--card)"
            stroke-width={grid.size * 0.22}
            stroke-linejoin="round"
            paint-order="stroke"
            pointer-events="none">{label.text}</text
          >
          {#if label.seats != null}
            <text
              class="fill-foreground font-figures tabular-nums"
              x={label.x}
              y={label.y + grid.size * 0.5}
              font-size={grid.size * 0.8}
              text-anchor="middle"
              dominant-baseline="central"
              stroke="var(--card)"
              stroke-width={grid.size * 0.22}
              stroke-linejoin="round"
              paint-order="stroke"
              pointer-events="none">{count(label.seats)}</text
            >
          {/if}
        {/each}
        {#if focus != null && grid.at.has(focus)}
          {const cell = $derived(grid.at.get(focus))}
          {#if cell != null}
            <path
              d={cell.d}
              fill="none"
              stroke="var(--foreground)"
              stroke-width="0.24"
              pointer-events="none"
            />
          {/if}
        {/if}
      </svg>
    {/if}
    {#if isSummary}{:else if still != null}
      <img
        class="pointer-events-none absolute inset-0 object-cover block-full inline-full"
        src={still}
        alt=""
        out:fade={{ duration: calm ? 0 : 180 }}
      />
    {:else if !engine.isLoaded}
      <p
        class="absolute inset-0 grid place-items-center text-[0.8125rem] text-muted-foreground"
      >
        {m.map_loading()}
      </p>
    {/if}
    {#if opening != null}
      <p
        class={cn(
          "pointer-events-none absolute inset-x-0 mx-auto flex items-center gap-2 rounded-sm bg-card/95 px-3 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border block-8 inline-fit",
          {
            "inset-be-3": remote.length === 0,
            "inset-be-11 pointer-coarse:inset-be-14": remote.length > 0,
          },
        )}
        role="status"
        transition:fade={{ duration: calm ? 0 : 120 }}
      >
        <span
          class="animate-spin rounded-full border-2 border-border border-bs-foreground block-3.5 inline-3.5 motion-reduce:animate-none"
          aria-hidden="true"
        ></span>
        {m.map_opening({
          place: rows.get(opening)?.name ?? besides.get(opening)?.name ?? "",
        })}
      </p>
    {/if}
    <div
      class={cn(
        "absolute inset-e-2 inset-bs-2 flex flex-col overflow-hidden rounded-sm bg-card shadow-menu ring-1 ring-border max-sm:inset-bs-auto max-sm:inset-be-2",
        { hidden: isSummary },
      )}
      data-export-skip
    >
      <button
        type="button"
        class="grid place-items-center block-8 inline-8 hover:bg-secondary focus-visible:-outline-offset-2 pointer-coarse:block-10 pointer-coarse:inline-10"
        aria-label={m.map_zoom_in()}
        title={m.map_zoom_in()}
        onclick={() => engine.zoomIn()}
      >
        <PlusIcon class="block-4 inline-4" />
      </button>
      <button
        type="button"
        class="grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary focus-visible:-outline-offset-2 pointer-coarse:block-10 pointer-coarse:inline-10"
        aria-label={m.map_zoom_out()}
        title={m.map_zoom_out()}
        onclick={() => engine.zoomOut()}
      >
        <MinusIcon class="block-4 inline-4" />
      </button>
      <button
        type="button"
        class="grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary focus-visible:-outline-offset-2 pointer-coarse:block-10 pointer-coarse:inline-10"
        aria-label={m.map_reset()}
        title={m.map_reset()}
        onclick={() => engine.reset()}
      >
        <ScanIcon class="block-4 inline-4" />
      </button>
      {#if SCHEMES.has(scheme)}
        <button
          type="button"
          class={cn(
            "grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary focus-visible:-outline-offset-2 pointer-coarse:block-10 pointer-coarse:inline-10",
            { "text-muted-foreground": !isBasemap },
          )}
          aria-label={m.map_basemap()}
          aria-pressed={isBasemap}
          title={m.map_basemap()}
          onclick={() => {
            isBasemap = !isBasemap;
            keep({ basemap: isBasemap ? null : "off" });
          }}
        >
          <MapIcon class="block-4 inline-4" />
        </button>
      {/if}
    </div>
    {#if segments.length > 0 || globe != null}
      <div
        class="pointer-events-none absolute inset-e-2 inset-be-2 flex flex-wrap-reverse justify-end gap-1.5 max-sm:inset-s-2 max-sm:inset-e-14"
        bind:clientHeight={stripHeight}
      >
        {#each segments as segment (segment.group)}
          {const heading = $derived(
            {
              territory: m.map_outlying(),
              overseas: m.map_overseas(),
              abroad: m.map_abroad(),
              pool: m.map_offmap(),
            }[segment.group],
          )}
          {#if segment.entries.length === 1 || (segment.entries.every((entry) => entry.code != null) && segment.entries.length <= (narrow ? 6 : 12))}
            <div
              class="pointer-events-auto flex overflow-hidden rounded-sm bg-card/95 shadow-menu ring-1 ring-border"
              role="group"
              aria-label={heading}
            >
              {#each segment.entries as entry (entry.k)}
                {const reach = (node: HTMLElement) => {
                  const box =
                    node.offsetParent?.parentElement?.getBoundingClientRect();
                  const rect = node.getBoundingClientRect();
                  if (box != null) {
                    pointer = {
                      x: rect.left - box.left + rect.width / 2,
                      y: rect.top - box.top,
                    };
                  }
                }}
                <button
                  type="button"
                  class={cn(
                    "flex items-center gap-1.5 border-s border-border px-2 text-xs font-semibold block-7 first:border-s-0 hover:bg-secondary pointer-coarse:block-10",
                    { "bg-secondary": pinned === entry.k || focus === entry.k },
                    { "font-normal text-muted-foreground": entry.isBlank },
                  )}
                  aria-label={entry.name}
                  aria-pressed={entry.isBlank ? undefined : pinned === entry.k}
                  onpointermove={(event) => {
                    if (
                      !isHover(event) ||
                      (pointer != null &&
                        (entry.isBlank ? empty : focus) === entry.k)
                    ) {
                      return;
                    }
                    reach(event.currentTarget);
                    if (entry.isBlank) {
                      empty = entry.k;
                    } else if (pinned == null) {
                      focus = entry.k;
                    }
                  }}
                  onpointerleave={() => {
                    pointer = null;
                    empty = null;
                    if (pinned == null && focus === entry.k) {
                      focus = null;
                    }
                  }}
                  onfocus={(event) => {
                    reach(event.currentTarget);
                    if (entry.isBlank) {
                      empty = entry.k;
                    } else if (pinned == null) {
                      focus = entry.k;
                    }
                  }}
                  onblur={() => {
                    pointer = null;
                    empty = null;
                    if (pinned == null && focus === entry.k) {
                      focus = null;
                    }
                  }}
                  onclick={(event) => {
                    if (entry.isBlank) {
                      return;
                    }
                    if (isTouch(event) && pinned !== entry.k) {
                      pinned = entry.k;
                      focus = entry.k;
                    } else {
                      open(entry.k);
                    }
                  }}
                >
                  <span
                    class={cn("rounded-xs bg-(--party) block-2.5 inline-2.5", {
                      "ring-1 ring-border": entry.isBlank,
                    })}
                    style:--party={entry.fill}
                    aria-hidden="true"
                  ></span>
                  {entry.code ?? entry.name}
                </button>
              {/each}
            </div>
          {:else}
            {const blocks = $derived(
              Map.groupBy(segment.entries, (entry) => {
                const cut = entry.name.lastIndexOf(", ");
                const prefix = cut > 0 ? entry.name.slice(0, cut) : "";
                return prefix !== "" &&
                  segment.entries.filter((other) =>
                    other.name.startsWith(`${prefix}, `),
                  ).length > 1
                  ? prefix
                  : "";
              })
                .entries()
                .map(([header, entries]) => ({
                  header,
                  lines: entries.map((entry) => {
                    const cut = entry.name.lastIndexOf(", ");
                    const rest = entry.name.slice(cut + 2);
                    return {
                      entry,
                      label:
                        header === ""
                          ? cut > 0 && entry.name.slice(0, cut) === rest
                            ? rest
                            : entry.name
                          : rest,
                    };
                  }),
                }))
                .toArray(),
            )}
            {#if narrow}
              <Drawer.Root
                bind:open={
                  () => listing === segment.group,
                  (value: boolean) => {
                    listing = value ? segment.group : null;
                  }
                }
              >
                <Drawer.Trigger
                  class="pointer-events-auto flex items-center gap-2 rounded-sm bg-card/95 ps-2.5 pe-2 text-xs font-semibold shadow-menu ring-1 ring-border block-7 hover:bg-secondary data-[state=open]:bg-secondary pointer-coarse:block-10"
                >
                  {@render summary(segment.entries, heading)}
                </Drawer.Trigger>
                <Drawer.Content>
                  <Drawer.Header
                    class="flex-row items-baseline justify-between px-4 pbs-3 pbe-1"
                  >
                    <Drawer.Title class="text-base">{heading}</Drawer.Title>
                    <span
                      class="font-figures text-sm text-muted-foreground tabular-nums"
                      >{count(segment.entries.length)}</span
                    >
                  </Drawer.Header>
                  <div class="px-2 pbe-[max(1rem,env(safe-area-inset-bottom))]">
                    {@render roster(blocks, true)}
                  </div>
                </Drawer.Content>
              </Drawer.Root>
            {:else}
              <Popover.Root
                bind:open={
                  () => listing === segment.group,
                  (value: boolean) => {
                    listing = value ? segment.group : null;
                  }
                }
              >
                <Popover.Trigger
                  class="pointer-events-auto flex items-center gap-2 rounded-sm bg-card/95 ps-2.5 pe-2 text-xs font-semibold shadow-menu ring-1 ring-border block-7 hover:bg-secondary data-[state=open]:bg-secondary pointer-coarse:block-10"
                >
                  {@render summary(segment.entries, heading)}
                </Popover.Trigger>
                <Popover.Content
                  side="top"
                  align="end"
                  class="gap-0 overflow-y-auto p-1 inline-80 max-block-[min(24rem,60dvh)]"
                >
                  {@render roster(blocks, false)}
                </Popover.Content>
              </Popover.Root>
            {/if}
          {/if}
        {/each}
        {#if globe != null}
          {const target = $derived(globe.isCurrent ? tiers[0] : globe)}
          {#if target != null && pick == null}
            <a
              class="pointer-events-auto flex items-center gap-1.5 rounded-sm bg-card/95 ps-2 pe-2.5 text-xs font-semibold shadow-menu ring-1 ring-border block-7 hover:bg-secondary pointer-coarse:block-10"
              href={resolve(`/[...path]?${target.query}`, {
                path: target.path,
              })}
              data-sveltekit-reset="false"
            >
              {@render traveler(globe, target)}
            </a>
          {:else if target != null}
            <button
              type="button"
              class="pointer-events-auto flex items-center gap-1.5 rounded-sm bg-card/95 ps-2 pe-2.5 text-xs font-semibold shadow-menu ring-1 ring-border block-7 hover:bg-secondary pointer-coarse:block-10"
              onclick={() => pick?.(target.code)}
            >
              {@render traveler(globe, target)}
            </button>
          {/if}
        {/if}
      </div>
    {/if}
    {#if overview != null && context != null}
      {#key `${nook.isTop}:${nook.isEnd}:${context.k}`}
        <button
          type="button"
          class={cn(
            "group absolute grid justify-items-center gap-1 rounded-sm bg-card/95 p-1.5 shadow-menu ring-1 ring-border transition-opacity duration-150 hover:bg-secondary motion-reduce:transition-none",
            { "pointer-events-none opacity-0": !nook.isPlaced },
          )}
          style:inset-inline-start={nook.isEnd ? null : "8px"}
          style:inset-inline-end={nook.isEnd ? "8px" : null}
          style:inset-block-start={nook.isTop
            ? `${8 + (nook.isEnd || up == null ? 0 : upHeight + 8)}px`
            : null}
          style:inset-block-end={nook.isTop ? null : "8px"}
          aria-label={m.map_context({ place: context.name })}
          title={m.map_context({ place: context.name })}
          onclick={() => {
            if (context != null) {
              engine.fit(context.frame);
            }
          }}
          {@attach (node) => {
            inset = node;
            untrack(() => {
              nestle();
              engine.gl?.once("idle", nestle);
            });
          }}
          transition:fade|global={{ duration: calm ? 0 : 160 }}
          data-export-skip
        >
          <svg
            class={cn(
              "transition-[inline-size,max-block-size] duration-200 ease-out motion-reduce:transition-none",
              {
                "inline-28 max-block-24 max-sm:inline-20 max-sm:max-block-16":
                  !nook.isCompact,
              },
              {
                "inline-12 max-block-10 group-hover:inline-28 group-hover:max-block-24 group-focus-visible:inline-28 group-focus-visible:max-block-24":
                  nook.isCompact,
              },
            )}
            viewBox="{context.extent.x} {context.extent.y} {context.extent
              .width} {context.extent.height}"
            aria-hidden="true"
          >
            <path
              d={context.outline}
              class="fill-muted-foreground/25 stroke-muted-foreground"
              stroke-width="1"
              stroke-linejoin="round"
              vector-effect="non-scaling-stroke"
            />
            {#if overview.isDot}
              <circle
                cx={overview.cx}
                cy={overview.cy}
                r={overview.r}
                class="fill-foreground stroke-card"
                stroke-width="1.5"
                vector-effect="non-scaling-stroke"
              />
            {:else}
              <rect
                x={overview.x}
                y={overview.y}
                width={overview.width}
                height={overview.height}
                class="fill-foreground/15 stroke-foreground"
                stroke-width="1.5"
                vector-effect="non-scaling-stroke"
              />
            {/if}
          </svg>
          <span
            class={cn(
              "truncate text-[0.6875rem] leading-none text-muted-foreground max-inline-28 max-sm:max-inline-20",
              {
                "hidden group-hover:block group-focus-visible:block":
                  nook.isCompact,
              },
            )}>{context.name}</span
          >
        </button>
      {/key}
    {/if}
    {#if up != null}
      <a
        class="absolute inset-s-2 inset-bs-2 flex items-center gap-1.5 rounded-sm bg-card ps-2 pe-2.5 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border block-8 max-inline-[calc(100%-4.5rem)] hover:bg-secondary pointer-coarse:block-10"
        href={unit(up.path)}
        bind:clientHeight={upHeight}
        data-export-skip
        ><ArrowUpLeftIcon
          class="shrink-0 block-4 inline-4"
          aria-hidden="true"
        /><span class="sr-only">{m.map_up()}</span><span class="truncate"
          >{up.name}</span
        ></a
      >
    {/if}
    {#if opening == null && (current != null || (pointer != null && (empty != null || beside != null)))}
      <div
        class={cn(
          "pointer-events-none absolute inset-s-0 inset-bs-0 grid gap-1.5 rounded-sm bg-card/95 px-3 py-2.5 shadow-menu ring-1 ring-border max-sm:hidden",
          {
            "inline-[min(20rem,calc(100%-1rem))]": current != null,
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
        {:else if empty != null && notes.has(empty)}
          <span class="font-semibold text-pretty">{notes.get(empty)?.name}</span
          >
          <span class="text-xs text-pretty text-muted-foreground max-inline-68"
            >{notes.get(empty)?.status}</span
          >
        {:else if empty != null}
          <span class="font-semibold text-pretty"
            >{blanks.get(empty)?.name}</span
          >
          <span class="text-xs text-muted-foreground"
            >{blanks.get(empty)?.isHeld === true
              ? m.map_no_results()
              : blanks.get(empty)?.isPending === true
                ? m.map_pending()
                : m.map_no_race()}</span
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
            <span class="rounded-xs bg-(--party) chip block-2.5 inline-2.5"
            ></span>
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
    {:else if view === "turnout" && scale != null}
      <div
        class="flex flex-wrap items-center gap-x-4 gap-y-2"
        aria-hidden="true"
      >
        <span class="text-muted-foreground">{m.map_turnout_key()}</span>
        <span
          class="relative flex pbe-4.5 font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
        >
          {#each scale.tones as tone, index (index)}
            <span
              class="block block-3 inline-10 first:rounded-s-xs last:rounded-e-xs"
              style:background-color={tone}
            ></span>
          {/each}
          {#each scale.ticks as tick, index (index)}
            <span
              class="absolute inset-bs-3 -translate-x-1/2 border-s border-foreground/50 block-1"
              style:inset-inline-start="{index * 2.5}rem"
            ></span>
            <span
              class="absolute inset-bs-4 -translate-x-1/2 leading-none whitespace-nowrap"
              style:inset-inline-start="{index * 2.5}rem"
              >{percent(tick, scale.digits)}</span
            >
          {/each}
        </span>
        {#if turnouts.size < places.rows.length}
          <span class="flex items-center gap-1.5 text-xs text-muted-foreground"
            ><span
              class="rounded-xs bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_4px)] ring-1 ring-border block-3 inline-4"
            ></span><span>{m.map_turnout_none()}</span><span
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
        {#if skipped.length > 0}
          {@render gap(
            "norun",
            m.map_norun_key(),
            skipped.map((row) => row.name),
          )}
        {/if}
        {#if missing.length > 0}
          {@render gap(
            "absent",
            m.map_blank_key(),
            missing.map((entry) => entry.name),
          )}
        {/if}
        {#if pending.length > 0}
          {@render gap(
            "absent",
            m.map_pending_key(),
            pending.map((entry) => entry.name),
          )}
        {/if}
      </div>
    {:else}
      <div class="flex flex-wrap gap-1.5">
        {#each isKeyed ? leads : leads.slice(0, 6) as [index, won] (index)}
          <button
            type="button"
            class={cn(
              "flex items-center gap-1.5 rounded-xs px-1.5 text-[0.8125rem] block-7 hover:bg-secondary",
              { "marked bg-secondary": only === index },
            )}
            style:--party={colors[index]}
            aria-pressed={only === index}
            onclick={() => {
              only = only === index ? null : index;
            }}
          >
            <span class="rounded-xs bg-(--party) chip block-2.5 inline-2.5"
            ></span>
            <span class="font-semibold">{names[index]}</span>
            {#if tallies?.[index] != null}
              <span
                class="font-figures text-xs text-muted-foreground tabular-nums"
                >{m.map_electoral({ count: count(tallies[index]) })}</span
              >
            {:else if won > 0}
              <span
                class="font-figures text-xs text-muted-foreground tabular-nums"
                >{m.map_won({
                  count: count(won),
                  noun,
                  nouns: plural(noun),
                })}</span
              >
            {/if}
          </button>
        {/each}
        {#if leads.length > 6}
          <button
            type="button"
            class="rounded-xs px-1.5 text-[0.8125rem] font-semibold text-muted-foreground block-7 hover:bg-secondary hover:text-foreground"
            aria-expanded={isKeyed}
            onclick={() => (isKeyed = !isKeyed)}
            >{isKeyed
              ? m.map_key_fewer()
              : m.map_key_more({ count: count(leads.length - 6) })}</button
          >
        {/if}
        {#if idle.length > 0}
          <span
            class="flex items-center gap-1.5 px-1.5 text-[0.8125rem] text-muted-foreground block-7"
            title={idle.map((entry) => entry.name).join(", ")}
          >
            <span
              class="rounded-xs bg-[color-mix(in_oklab,var(--muted-foreground)_14%,var(--card))] ring-1 ring-border block-2.5 inline-2.5"
            ></span>
            <span>{m.map_idle_key()}</span>
            <span class="font-figures text-xs tabular-nums">{idle.length}</span>
          </span>
        {/if}
        {#if missing.length > 0}
          {@render gap(
            "absent",
            m.map_blank_key(),
            missing.map((entry) => entry.name),
          )}
        {/if}
        {#if pending.length > 0}
          {@render gap(
            "absent",
            m.map_pending_key(),
            pending.map((entry) => entry.name),
          )}
        {/if}
        {const splits = $derived(
          isBanded && only == null
            ? places.rows.filter(
                (row) => row.held != null && row.leader != null,
              ).length
            : 0,
        )}
        {#if splits > 0}
          <span
            class="flex items-center gap-1.5 px-1.5 text-[0.8125rem] text-muted-foreground block-7"
          >
            <span
              class="rounded-xs bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_2.5px,color-mix(in_oklab,var(--muted-foreground)_35%,var(--card))_2.5px_5px)] block-2.5 inline-2.5"
            ></span>
            <span>{m.map_split_key()}</span>
            <span class="font-figures text-xs tabular-nums">{splits}</span>
          </span>
        {/if}
        {#if view === "winner" && isProportional && only == null}
          <span
            class="flex items-center px-1.5 text-[0.8125rem] text-muted-foreground block-7"
            >{m.map_largest_key({ noun })}</span
          >
        {/if}
      </div>
      {#if isDeclared}{:else if view === "bubbles"}
        <span class="text-muted-foreground"
          >{m.map_bubbles_key({ votes: votes.toLowerCase() })}</span
        >
      {:else if !isTally && places.rows.some((row) => row.margin != null)}
        <button
          type="button"
          class={cn(
            "flex items-center gap-1.5 self-start rounded-xs px-1.5 py-0.5 font-semibold ring-1 ring-border hover:bg-secondary",
            { "bg-foreground text-background hover:bg-foreground": isShaded },
          )}
          aria-pressed={isShaded}
          onclick={() => (isShaded = !isShaded)}>{m.map_margin()}</button
        >
        {#if isShaded && ramps.length > 0}
          <div class="basis-full overflow-x-auto">
            <table
              class="border-separate border-spacing-x-0.5 border-spacing-y-1"
            >
              <thead>
                <tr
                  class="font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
                >
                  <th
                    class="pe-2 text-start font-normal max-inline-32"
                    scope="col"
                  >
                    {m.map_margin_lead()}
                  </th>
                  {#each LEANS as strength, at (strength)}
                    <th
                      class="px-0.5 text-center font-normal whitespace-nowrap"
                      scope="col"
                    >
                      {at === 0
                        ? m.map_margin_under({
                            points: String(Math.round((BINS[0] ?? 0) * 100)),
                          })
                        : at === LEANS.length - 1
                          ? m.map_margin_over({
                              points: String(
                                Math.round((BINS.at(-1) ?? 0) * 100),
                              ),
                            })
                          : m.map_margin_range({
                              from: String(
                                Math.round((BINS[at - 1] ?? 0) * 100),
                              ),
                              to: String(Math.round((BINS[at] ?? 0) * 100)),
                            })}
                    </th>
                  {/each}
                </tr>
              </thead>
              <tbody>
                {#each ramps as ramp (ramp.index)}
                  <tr>
                    <th
                      class="truncate pe-2 text-start font-semibold max-inline-32"
                      scope="row"
                    >
                      {ramp.name}
                    </th>
                    {#each ramp.cells as cell, at (at)}
                      <td
                        class="p-0"
                        title={m.map_margin_cell({
                          name: ramp.name,
                          count: count(cell.count),
                          noun: cell.count === 1 ? noun : plural(noun),
                        })}
                      >
                        <span
                          class={cn(
                            "grid place-items-center rounded-xs font-figures text-[0.6875rem] tabular-nums block-5 min-inline-9",
                            { "opacity-35": cell.count === 0 },
                          )}
                          style:background-color={cell.tone}
                        >
                          <span
                            class="rounded-xs bg-card/85 px-1 leading-4 text-foreground"
                            >{count(cell.count)}</span
                          >
                        </span>
                      </td>
                    {/each}
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
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
    {#if isEstimated && span != null}
      <span class="grid gap-0.5 text-muted-foreground">
        <span class="flex items-center gap-1.5">
          <span
            class="rounded-xs bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_4px)] ring-1 ring-border block-3 inline-4"
          ></span>
          <span class="text-foreground">{m.atlas_estimated()}</span>
        </span>
        <span class="text-xs text-pretty max-inline-120"
          >{m.atlas_estimated_note({ year: span.from })}</span
        >
      </span>
    {/if}
  </div>
  {#if credited.length > 0 || approximate.length > 0}
    {const notices = $derived(
      credited.filter((credit) => /©|: |\.$|^Contains /u.test(credit)),
    )}
    {const publishers = $derived(
      credited.filter(
        (credit) =>
          !notices.includes(credit) &&
          !credited.some((other) => other !== credit && other.includes(credit)),
      ),
    )}
    <div class="grid gap-0.5 px-1 text-xs text-muted-foreground">
      {#if approximate.length > 0}
        <p class="flex items-center gap-1.5">
          <span
            class="border-bs border-dashed border-foreground/45 inline-4"
            aria-hidden="true"
          ></span>
          <Term
            label={m.map_approximate_key()}
            definition={m.term_boundaries_approximate()}
          />
        </p>
      {/if}
      {#if publishers.length > 0}
        {const listed = $derived(
          new Intl.ListFormat(getLocale(), { type: "conjunction" }).format(
            publishers,
          ),
        )}
        <p>{m.map_boundaries({ publishers: listed })}</p>
      {/if}
      {#each notices as notice (notice)}
        <p>{notice}</p>
      {/each}
      {#if credited.length > 0}
        <p>{m.map_water_credit()}</p>
      {/if}
      {#if !isSummary && SCHEMES.has(scheme)}
        <p>{m.map_basemap_credit()}</p>
      {/if}
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
          href={unit(entry.path)}
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
  {#if places.rows.some((row) => row.valid > 0 || row.leader != null)}
    <p class="px-1 text-[0.8125rem] text-muted-foreground sm:hidden">
      {m.map_pick_touch()}
    </p>
  {/if}
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
        href={unit(place.path)}>{m.map_open({ place: place.name })}</a
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

{#snippet gap(shape: "absent" | "norun", label: string, names: Array<string>)}
  {const isListed = $derived(names.length <= 5)}
  <span
    class="flex items-center gap-1.5 px-1.5 text-[0.8125rem] text-muted-foreground min-block-7"
    title={isListed ? undefined : names.join(", ")}
  >
    <svg
      viewBox="0 0 10 10"
      class="rounded-xs bg-[color-mix(in_oklab,var(--muted-foreground)_6%,var(--card))] ring-1 ring-border block-2.5 inline-2.5"
      aria-hidden="true"
    >
      <path
        d={shape === "absent"
          ? "M2.5 2.5h.01M7.5 7.5h.01M7.5 2.5h.01M2.5 7.5h.01"
          : "M1 3h4M6 7h4M0 7h1"}
        stroke="currentColor"
        stroke-width={shape === "absent" ? 1.75 : 1}
        stroke-linecap={shape === "absent" ? "round" : "butt"}
      />
    </svg>
    <span class="shrink-0">{label}</span>
    {#if isListed}
      <span class="text-pretty text-foreground"
        >{new Intl.ListFormat(getLocale(), { type: "conjunction" }).format(
          names.toSorted((a, b) => a.localeCompare(b, getLocale())),
        )}</span
      >
    {:else}
      <span class="font-figures text-xs tabular-nums">{names.length}</span>
    {/if}
  </span>
{/snippet}

{#snippet traveler(
  world: (typeof levels)[number],
  target: (typeof levels)[number],
)}
  {#if world.isCurrent}
    <ArrowLeftIcon
      class="text-muted-foreground block-3.5 inline-3.5"
      aria-hidden="true"
    />
  {:else}
    <GlobeIcon
      class="text-muted-foreground block-3.5 inline-3.5"
      aria-hidden="true"
    />
  {/if}
  <span
    >{world.isCurrent
      ? home == null
        ? target.label
        : m.map_world_back({ place: home })
      : m.map_world()}</span
  >
  {#if !world.isCurrent}
    <span class="font-figures font-normal text-muted-foreground tabular-nums"
      >{count(world.count)}</span
    >
  {/if}
{/snippet}

{#snippet summary(entries: typeof remote, heading: string)}
  {heading}
  <span class="flex gap-0.5" aria-hidden="true">
    {#each entries.slice(0, 12) as entry (entry.k)}
      <span
        class={cn("rounded-[1px] bg-(--party) block-2.5 inline-1.5", {
          "ring-1 ring-border": entry.isBlank,
        })}
        style:--party={entry.fill}
      ></span>
    {/each}
  </span>
  <span class="font-figures font-normal text-muted-foreground tabular-nums"
    >{count(entries.length)}</span
  >
  <ChevronUpIcon
    class="text-muted-foreground block-3.5 inline-3.5"
    aria-hidden="true"
  />
{/snippet}

{#snippet roster(
  blocks: Array<{
    header: string;
    lines: Array<{ entry: (typeof remote)[number]; label: string }>;
  }>,
  isSheet: boolean,
)}
  {#each blocks as block (block.header)}
    <div
      class={cn({
        "not-first:mbs-1 not-first:border-bs not-first:border-border":
          block.header !== "",
      })}
    >
      {#if block.header !== ""}
        <p
          class={cn("px-2 pbs-2 pbe-0.5 text-xs text-muted-foreground", {
            "text-[0.8125rem]": isSheet,
          })}
        >
          {block.header}
        </p>
      {/if}
      {#each block.lines as line (line.entry.k)}
        {const row = $derived(rows.get(line.entry.k))}
        {#if row?.path == null}
          <div
            class={cn("flex items-center gap-2.5 rounded-xs px-2 py-1.5", {
              "py-2.5": isSheet,
            })}
          >
            {@render outlier(line.entry, row, line.label)}
          </div>
        {:else}
          <button
            type="button"
            class={cn(
              "flex items-center gap-2.5 rounded-xs px-2 py-1.5 text-start inline-full hover:bg-secondary focus-visible:bg-secondary",
              { "py-2.5": isSheet },
            )}
            onclick={() => {
              listing = null;
              open(line.entry.k);
            }}
          >
            {@render outlier(line.entry, row, line.label)}
          </button>
        {/if}
      {/each}
    </div>
  {/each}
{/snippet}

{#snippet outlier(
  entry: (typeof remote)[number],
  row: ReturnType<typeof rows.get>,
  label: string,
)}
  <span
    class={cn(
      "mbs-1 shrink-0 self-start rounded-xs bg-(--party) block-2.5 inline-2.5",
      { "ring-1 ring-border": entry.isBlank },
    )}
    style:--party={entry.fill}
    aria-hidden="true"
  ></span>
  <span class="grid flex-1 gap-0.5 min-inline-0">
    <span
      class={cn(
        "text-[0.8125rem] font-semibold text-pretty first-letter:uppercase",
        { "font-normal text-muted-foreground": entry.isBlank },
      )}>{label}</span
    >
    <span class="truncate text-xs text-muted-foreground"
      >{entry.leader == null ? m.map_blank_key() : names[entry.leader]}</span
    >
  </span>
  {#if entry.leader != null && row != null}
    <span class="shrink-0 self-start font-figures text-[0.8125rem] tabular-nums"
      >{percent(row.shares[entry.leader] ?? 0)}</span
    >
  {/if}
{/snippet}

{#snippet readout(isSheet = false)}
  {#if current != null}
    {const tallied = $derived(isTally || current.isTally === true)}
    {const lead = $derived.by(() => {
      const [first, second] = current.shares
        .map((share, index) => ({ share, index }))
        .toSorted((a, b) => b.share - a.share);
      return current.margin == null ||
        places.legend.length === 1 ||
        first == null ||
        second == null ||
        second.share === 0 ||
        first.index !== current.leader ||
        Math.abs(current.margin - (first.share - second.share)) > 1e-4
        ? null
        : Math.round(first.share * current.valid) -
            Math.round(second.share * current.valid);
    })}
    <span class="flex items-baseline justify-between gap-3">
      <span class="font-semibold text-pretty">{current.name}</span>
      {#if moved?.from != null && moved.to != null && moved.from !== moved.to}
        <span
          class="flex shrink-0 items-center gap-1 rounded-xs bg-secondary px-1.5 text-xs font-semibold"
          style:--party={colors[moved.to]}
          ><span class="rounded-xs bg-(--party) chip block-2 inline-2"
          ></span>{m.map_flipped()}</span
        >
      {:else if current.margin != null && places.legend.length === 1}
        <span class="shrink-0 text-xs text-muted-foreground"
          >{m.map_unopposed()}</span
        >
      {:else if current.margin != null}
        <span
          class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
          >{tallied
            ? new Intl.NumberFormat(getLocale(), {
                signDisplay: "exceptZero",
              }).format(lead ?? Math.round(current.margin * current.valid))
            : points(current.margin)}</span
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
          {const person = $derived(current.people?.[entry.index] ?? null)}
          {const tag = $derived.by(() => {
            const lines = current.tags?.[entry.index];
            return lines != null
              ? new Intl.ListFormat(getLocale(), {
                  type: "unit",
                  style: "short",
                }).format(lines)
              : person == null
                ? null
                : (names[entry.index] ?? option?.label ?? null);
          })}
          <span class="flex items-baseline gap-1.5 min-inline-0">
            <span
              class={cn("truncate", {
                "font-semibold": entry.index === current.leader,
              })}>{person ?? names[entry.index] ?? option?.label}</span
            >
            {#if tag != null}
              <span
                class="shrink-999 truncate text-xs text-muted-foreground min-inline-0"
                title={tag}>{tag}</span
              >
            {/if}
          </span>
          <span
            class="flex items-baseline gap-2 font-figures text-xs tabular-nums"
          >
            {#if current.valid > 0 && !tallied}
              <span class="text-muted-foreground"
                >{count(Math.round(entry.share * current.valid))}</span
              >
            {/if}
            <span class="text-end min-inline-10"
              >{tallied
                ? count(Math.round(entry.share * current.valid))
                : percent(entry.share, precision(current.margin))}</span
            >
          </span>
          <span class="col-span-2 rounded-xs bg-muted block-1.5">
            <span
              class="block rounded-xs bg-(--party) block-full"
              style:inline-size="{entry.share * 100}%"
            ></span>
          </span>
        </span>
      {/each}
    </span>
    {#if isCompared && places.estimated === true && current.earlier != null && span != null}
      <span class="text-xs text-muted-foreground"
        >{m.map_estimated_line({ year: span.from })}</span
      >
    {/if}
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
    {#if current.valid === 0 && (current.leader != null || current.declared != null)}
      <span
        class="flex items-center gap-2 text-[0.8125rem]"
        style:--party={current.leader == null
          ? swatch(current.declared?.color, `party:${current.declared?.party}`)
          : colors[current.leader]}
      >
        <span class="rounded-xs bg-(--party) chip block-2.5 inline-2.5"></span>
        <span class="font-semibold"
          >{current.leader == null
            ? current.declared?.label
            : (places.legend[current.leader]?.label ??
              names[current.leader])}</span
        >
        {#if !isDeclared}
          <span class="text-muted-foreground"
            >{current.declared?.isTacit === false
              ? current.declared.kind === "elected"
                ? m.outcome_elected()
                : m.outcome_won()
              : m.map_unopposed()}</span
          >
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
    {#if current.status != null}
      <span class="text-xs text-pretty text-muted-foreground"
        >{current.status}</span
      >
    {/if}
    <span class="flex items-baseline justify-between gap-3 text-xs">
      <span class="text-muted-foreground"
        >{#if current.valid > 0}{votes}
          <span class="font-figures tabular-nums">{count(current.valid)}</span
          >{#if lead != null && !tallied}<span class="mx-1.5" aria-hidden="true"
              >·</span
            >{m.map_lead()}
            <span class="font-figures tabular-nums">{count(lead)}</span
            >{/if}{/if}</span
      >
      {#if current.path != null && !isSheet && pinned == null}
        <span class="text-muted-foreground">{m.map_click_open()}</span>
      {/if}
    </span>
  {/if}
{/snippet}
