import type * as Maplibre from "maplibre-gl";
import type { PMTiles } from "pmtiles";
import { dev } from "$app/env";
import earcut from "earcut";
import { untrack } from "svelte";
import { on } from "svelte/events";
import { isHover, isTouch } from "#lib/explain.svelte.js";
import { STRIPE } from "#lib/live/flips.js";
import { Mosaic, sampled } from "#lib/map/mosaic.js";
import { ForgivingCache, isAborted, sharedReads } from "#lib/map/reads.js";
import { Retries } from "#lib/map/retries.js";

export type Box = { west: number; south: number; east: number; north: number };
export type Arrow = {
  at: [number, number];
  swing: number;
  color: string;
  weight?: number;
  isMuted?: boolean;
};

const REACHES = [0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3];
const TILT = (35 * Math.PI) / 180;
const LARGEST = 64;

function isPoint(
  value: unknown,
): value is { type: "Point"; coordinates: Array<number> } {
  return (
    typeof value === "object" &&
    value != null &&
    "type" in value &&
    value.type === "Point" &&
    "coordinates" in value &&
    Array.isArray(value.coordinates)
  );
}

export function reach(swings: Array<number>) {
  const sizes = swings
    .map((swing) => Math.abs(swing))
    .filter((size) => size > 0)
    .toSorted((a, b) => a - b);
  const top = sizes[Math.floor(sizes.length * 0.9)] ?? 0.1;
  return REACHES.find((edge) => edge >= top) ?? 0.3;
}

export function radius(
  size: Maplibre.ExpressionSpecification,
): Maplibre.ExpressionSpecification {
  return [
    "interpolate",
    ["exponential", 2],
    ["zoom"],
    ...Array.from({ length: 23 }, (_, zoom) => [
      zoom,
      ["min", LARGEST, ["*", size, 2 ** zoom]],
    ]).flat(),
  ] as Maplibre.ExpressionSpecification;
}
type Padding = { top: number; right: number; bottom: number; left: number };
type Slot =
  | "ground"
  | "fills"
  | "etch"
  | "lines"
  | "points"
  | "labels"
  | "focus";
type Paint =
  Parameters<Maplibre.Map["setPaintProperty"]> extends [
    string,
    infer K,
    infer V,
    ...Array<unknown>,
  ]
    ? [K, V]
    : never;
type State = Record<string, string | number | boolean>;
type Options = {
  cooperative?: boolean;
  maxZoom?: number;
  limit?: Box | null;
  gestures?: Record<string, string>;
  onReady?: () => void;
  onPointer?: (event: {
    kind: "move" | "leave" | "click";
    point: { x: number; y: number };
    isTouch: boolean;
  }) => void;
  onMove?: () => void;
  onMoveEnd?: () => void;
};

const OPACITY: Partial<Record<string, Array<string>>> = {
  fill: ["fill-opacity"],
  line: ["line-opacity"],
  symbol: ["text-opacity", "icon-opacity"],
  circle: ["circle-opacity", "circle-stroke-opacity"],
  raster: ["raster-opacity"],
  custom: ["opacity"],
};

const SLOTS: Array<Slot> = [
  "ground",
  "fills",
  "etch",
  "lines",
  "points",
  "labels",
  "focus",
];
const CSS = /^(var|color-mix)\(/u;
const SIZE = 512;
const FIT = 13;
const CHECKED = { validate: dev };

type Kit = { maplibre: typeof Maplibre; archive: (key: string) => PMTiles };

let library: Promise<Kit> | null = null;
let kit: Kit | null = null;

type Failed = {
  aborted: boolean;
  state: string;
  tileID: { canonical: { z: number; x: number; y: number } };
};

const tileName = (sourceId: string, { tileID: { canonical } }: Failed) =>
  `Map source ${sourceId} tile ${canonical.z}/${canonical.x}/${canonical.y}`;

const attach = (
  gl: Maplibre.Map,
  id: string,
  spec: Maplibre.SourceSpecification,
) => {
  gl.style.addSource(id, spec, CHECKED);
  gl._update(true);
};

const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

async function load() {
  library ??= Promise.all([
    import("maplibre-gl"),
    import("pmtiles"),
    import("maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url"),
    import("maplibre-gl/dist/maplibre-gl.css"),
  ]).then(([maplibre, { FetchSource, PMTiles, Protocol }, worker]) => {
    maplibre.setWorkerUrl(worker.default);
    const protocol = new Protocol();
    maplibre.addProtocol("pmtiles", protocol.tile);
    kit = {
      maplibre,
      archive: (key) => {
        const url = `${location.origin}/tiles/${key}.pmtiles`;
        const known = protocol.get(url);
        if (known != null) {
          return known;
        }
        const made = new PMTiles(
          sharedReads(new FetchSource(url)),
          new ForgivingCache(),
        );
        protocol.add(made);
        return made;
      },
    };
    return kit;
  });
  return library;
}

const HATCH_LINES = 8;

function hatchLines(zoom: number) {
  return Math.round(HATCH_LINES * 2 ** (zoom - Math.floor(zoom)));
}

function hatched(color: string, lines: number, size: number) {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const pen = canvas.getContext("2d", { willReadFrequently: true });
  const step = size / lines;
  if (pen != null) {
    pen.strokeStyle = color;
    pen.lineWidth = (step / Math.SQRT2) * (STRIPE.width / STRIPE.period);
    pen.beginPath();
    for (const line of Array.from({ length: lines * 2 + 1 }).keys()) {
      pen.moveTo(line * step + 2, -2);
      pen.lineTo(-2, line * step + 2);
    }
    pen.stroke();
  }
  return {
    width: size,
    height: size,
    data: new Uint8Array(
      pen?.getImageData(0, 0, size, size).data.buffer ??
        new ArrayBuffer(size * size * 4),
    ),
  };
}

function pattern(shape: string, color: string) {
  const canvas = document.createElement("canvas");
  canvas.width = 8;
  canvas.height = 8;
  const pen = canvas.getContext("2d", { willReadFrequently: true });
  if (pen != null) {
    pen.strokeStyle = color;
    pen.fillStyle = color;
    pen.lineWidth = shape === "absent" ? 2.5 : 1;
    pen.beginPath();
    if (shape === "hatch" || shape === "cross") {
      pen.moveTo(0, 8);
      pen.lineTo(8, 0);
      pen.stroke();
    }
    switch (shape) {
      case "back":
      case "cross": {
        pen.moveTo(0, 0);
        pen.lineTo(8, 8);
        pen.stroke();

        break;
      }
      case "absent": {
        pen.moveTo(3, 2);
        pen.arc(2, 2, 1, 0, Math.PI * 2);
        pen.moveTo(7, 6);
        pen.arc(6, 6, 1, 0, Math.PI * 2);
        pen.fill();

        break;
      }
      case "norun": {
        for (const [from, to, at] of [
          [1, 5, 2.5],
          [5, 8, 6.5],
          [0, 1, 6.5],
        ] as const) {
          pen.moveTo(from, at);
          pen.lineTo(to, at);
        }
        pen.stroke();

        break;
      }
      // No default
    }
  }
  return {
    width: 8,
    height: 8,
    data: new Uint8Array(
      pen?.getImageData(0, 0, 8, 8).data.buffer ?? new ArrayBuffer(256),
    ),
  };
}

function banded(entries: Array<[string, number]>) {
  let total = 0;
  for (const [, weight] of entries) {
    total += weight;
  }
  const side = 2 * Math.max(24, Math.ceil(total * 4 * Math.SQRT2));
  const canvas = document.createElement("canvas");
  canvas.width = side;
  canvas.height = side;
  const pen = canvas.getContext("2d", { willReadFrequently: true });
  if (pen != null && total > 0) {
    pen.fillStyle = entries[0]?.[0] ?? "transparent";
    pen.fillRect(0, 0, side, side);
    let from = 0;
    for (const [color, weight] of entries) {
      const to = from + (weight / total) * side;
      pen.fillStyle = color;
      for (const shift of [-side, 0, side, side * 2]) {
        pen.beginPath();
        pen.moveTo(from + shift, 0);
        pen.lineTo(to + shift, 0);
        pen.lineTo(to + shift - side, side);
        pen.lineTo(from + shift - side, side);
        pen.closePath();
        pen.fill();
      }
      from = to;
    }
  }
  return {
    width: side,
    height: side,
    data: new Uint8Array(
      pen?.getImageData(0, 0, side, side).data.buffer ??
        new ArrayBuffer(side * side * 4),
    ),
  };
}

function seated(colors: Array<string>, card: string, edge: string) {
  const across =
    colors.length > 6 ? Math.ceil(colors.length / 2) : colors.length;
  const down = Math.ceil(colors.length / across);
  const dot = 14;
  const gap = 4;
  const pad = 5;
  const width = pad * 2 + across * dot + (across - 1) * gap;
  const height = pad * 2 + down * dot + (down - 1) * gap;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const pen = canvas.getContext("2d", { willReadFrequently: true });
  if (pen != null) {
    pen.beginPath();
    pen.roundRect(1, 1, width - 2, height - 2, height / 2 - 1);
    pen.fillStyle = card;
    pen.fill();
    pen.lineWidth = 2;
    pen.strokeStyle = edge;
    pen.stroke();
    for (const [index, color] of colors.entries()) {
      const row = Math.floor(index / across);
      const inRow = Math.min(across, colors.length - row * across);
      const x =
        (width - (inRow * dot + (inRow - 1) * gap)) / 2 +
        (index % across) * (dot + gap) +
        dot / 2;
      const y = pad + row * (dot + gap) + dot / 2;
      pen.beginPath();
      pen.arc(x, y, dot / 2, 0, Math.PI * 2);
      pen.fillStyle = color;
      pen.fill();
    }
  }
  return {
    width,
    height,
    data: new Uint8Array(
      pen?.getImageData(0, 0, width, height).data.buffer ??
        new ArrayBuffer(width * height * 4),
    ),
  };
}

export function proportional(
  held: Iterable<Array<[number, number]> | null | undefined>,
) {
  let won = 0;
  let split = 0;
  for (const entry of held) {
    won += 1;
    if (entry != null) {
      split += 1;
    }
  }
  return won > 0 && split / won >= 0.8;
}

export function largest(
  held: Array<[number, number]>,
  votes: (index: number) => number,
) {
  let best = held[0];
  for (const entry of held) {
    if (
      best == null ||
      entry[1] > best[1] ||
      (entry[1] === best[1] && votes(entry[0]) > votes(best[0]))
    ) {
      best = entry;
    }
  }
  return best?.[0] ?? null;
}

export class Engine {
  isLoaded = $state(false);
  hasGaps = $state(false);
  credits = $state<Array<string>>([]);
  theme = $state(0);
  gl = $state.raw<Maplibre.Map | null>(null);
  #frame: Box;
  #padding: (width: number, height: number) => Padding;
  #options: Options;
  #isFramed = true;
  #goal: Box | null = null;
  #limit: Box | null;
  #layers = new Map<
    string,
    {
      spec: Maplibre.LayerSpecification | Maplibre.CustomLayerInterface;
      group: string;
      rank: number;
    }
  >();
  #sources = new Map<string, { group: string; isVector: boolean }>();
  #painted = new Map<string, Map<number, State>>();
  #pointed = new Map<string, string>();
  #marks = new Map<string, { source: string; k: number }>();
  #tinted = new Map<number, string>();
  #bands = new Map<string, Array<[string, number]>>();
  #seats = new Map<string, Array<string>>();
  #stripes = new Map<string, number>();
  #stripeLines = HATCH_LINES;
  #stripeTile = 128;
  #slots = new Map<string, Slot>();
  #rasterKeys: Array<string> = [];
  #mosaic: Mosaic | null = null;
  #retries: Retries | null = null;
  #probe: HTMLElement | null = null;
  #cache = new Map<string, string>();
  #canvas: CanvasRenderingContext2D | null = null;
  #overlay: HTMLCanvasElement | null = null;
  #arrows: { items: Array<Arrow>; reach: number } | null = null;

  constructor(
    frame: Box,
    padding: (width: number, height: number) => Padding,
    options: Options = {},
  ) {
    this.#frame = frame;
    this.#padding = padding;
    this.#options = options;
    this.#limit = options.limit ?? null;
  }

  mount = (host: HTMLElement) => {
    let isGone = false;
    const offs: Array<() => void> = [];
    const retries = new Retries((hasGaps) => {
      this.hasGaps = hasGaps;
    });
    this.#retries = retries;
    this.#probe = document.createElement("span");
    this.#probe.hidden = true;
    host.append(this.#probe);
    this.#canvas = document
      .createElement("canvas")
      .getContext("2d", { willReadFrequently: true });
    untrack(() => {
      this.theme += 1;
    });
    void load().then(({ maplibre }) => {
      if (isGone) {
        return;
      }
      const gl = new maplibre.Map({
        container: host,
        style: {
          version: 8,
          glyphs: `${location.origin}/glyphs/{fontstack}/{range}.pbf`,
          sources: {},
          layers: [
            {
              id: "paper",
              type: "background",
              paint: { "background-color": this.read("var(--card)") },
            },
            ...SLOTS.map((slot) => ({
              id: `slot:${slot}`,
              type: "background" as const,
              layout: { visibility: "none" as const },
            })),
          ],
        },
        renderWorldCopies: false,
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        maxPitch: 0,
        maxZoom: this.#options.maxZoom ?? 16,
        fadeDuration: 0,
        cooperativeGestures: this.#options.cooperative === true,
        canvasContextAttributes: { preserveDrawingBuffer: true },
        locale: this.#options.gestures ?? {},
        bounds: [
          this.#frame.west,
          this.#frame.south,
          this.#frame.east,
          this.#frame.north,
        ],
        fitBoundsOptions: { maxZoom: FIT },
      });
      gl.touchZoomRotate.disableRotation();
      gl.keyboard.disable();
      const canvas = gl.getCanvas();
      canvas.removeAttribute("role");
      canvas.removeAttribute("aria-label");
      canvas.tabIndex = -1;
      gl.on(
        "error",
        (event: Maplibre.ErrorEvent & { sourceId?: string; tile?: Failed }) =>
          this.#failed(gl, retries, event),
      );
      gl.on("sourcedata", (event) => {
        if (retries.isEmpty) {
          return;
        }
        if (event.sourceDataType === "metadata") {
          retries.worked(`Map source ${event.sourceId}`);
        } else if (event.tile != null) {
          retries.worked(tileName(event.sourceId, event.tile as Failed));
        }
      });
      gl.on("movestart", (event) => {
        if (event.originalEvent == null) {
          return;
        }
        this.#isFramed = false;
        this.#goal = null;
      });
      gl.on("move", () => this.#options.onMove?.());
      gl.on("moveend", () => {
        const view = gl.getBounds();
        const limit = gl.getMaxBounds();
        host.dataset.view = JSON.stringify({
          limit:
            limit == null
              ? null
              : {
                  west: limit.getWest(),
                  south: limit.getSouth(),
                  east: limit.getEast(),
                  north: limit.getNorth(),
                },
          zoom: gl.getZoom(),
          min: gl.getMinZoom(),
          west: view.getWest(),
          south: view.getSouth(),
          east: view.getEast(),
          north: view.getNorth(),
          frame: this.#frame,
        });
        this.#options.onMoveEnd?.();
        retries.wake();
      });
      this.#stripeTile = 128 * Math.ceil(gl.getPixelRatio());
      gl.on("zoomend", () => this.#fitStripes(gl));
      gl.on("resize", () => this.#hold(gl));
      const overlay = document.createElement("canvas");
      overlay.setAttribute("aria-hidden", "true");
      overlay.style.cssText =
        "position:absolute;inset:0;inline-size:100%;block-size:100%;pointer-events:none";
      host.append(overlay);
      this.#overlay = overlay;
      gl.on("render", () => this.#sketch(gl));
      for (const kind of ["mousemove", "click"] as const) {
        gl.on(kind, (event) => {
          if (kind === "mousemove" && !isHover(event.originalEvent)) {
            return;
          }
          this.#options.onPointer?.({
            kind: kind === "click" ? "click" : "move",
            point: { x: event.point.x, y: event.point.y },
            isTouch: isTouch(event.originalEvent),
          });
        });
      }
      gl.on("mouseout", (event) => {
        if (!isHover(event.originalEvent)) {
          return;
        }
        this.#options.onPointer?.({
          kind: "leave",
          point: { x: -1, y: -1 },
          isTouch: false,
        });
      });
      gl.once("style.load", () => {
        this.#dress(gl);
        this.#hold(gl);
        this.gl = gl;
        this.#options.onReady?.();
      });
      gl.once("idle", () => {
        this.isLoaded = true;
      });
    });
    const bump = () =>
      requestAnimationFrame(() => {
        this.#cache.clear();
        this.#restyle();
        this.theme += 1;
      });
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style", "data-theme"],
    });
    offs.push(
      () => observer.disconnect(),
      on(matchMedia("(prefers-color-scheme: dark)"), "change", bump),
      on(globalThis, "online", () => retries.wake()),
      on(document, "visibilitychange", () => {
        if (!document.hidden) {
          retries.wake();
        }
      }),
    );
    return () => {
      isGone = true;
      for (const off of offs) {
        off();
      }
      retries.stop();
      this.#retries = null;
      this.#probe?.remove();
      this.#overlay?.remove();
      this.gl?.remove();
      this.gl = null;
      this.#mosaic = null;
      this.isLoaded = false;
    };
  };

  #failed(
    gl: Maplibre.Map,
    retries: Retries,
    {
      error,
      sourceId,
      tile,
    }: Maplibre.ErrorEvent & { sourceId?: string; tile?: Failed },
  ) {
    if (isAborted(error)) {
      return;
    }
    if (sourceId == null || this.#sources.get(sourceId)?.isVector !== true) {
      console.error(error);
      return;
    }
    if (tile == null) {
      const name = `Map source ${sourceId}`;
      retries.failed(name, error, () => {
        const source = gl.getSource<Maplibre.VectorTileSource>(sourceId);
        if (source?.url == null) {
          retries.forget(name);
        } else {
          source.setUrl(source.url);
        }
      });
      return;
    }
    const name = tileName(sourceId, tile);
    retries.failed(name, error, () => {
      if (tile.aborted || gl.getSource(sourceId) == null) {
        retries.forget(name);
      } else if (tile.state === "errored") {
        const { z, x, y } = tile.tileID.canonical;
        gl.refreshTiles(sourceId, [{ z, x, y }]);
      }
    });
  }

  retry() {
    this.#retries?.wake();
  }

  read(css: string) {
    const known = this.#cache.get(css);
    if (known != null) {
      return known;
    }
    const probe = this.#probe;
    if (probe == null) {
      return css;
    }
    probe.style.color = css;
    let value = getComputedStyle(probe).color;
    const pen = this.#canvas;
    if (pen != null) {
      pen.clearRect(0, 0, 1, 1);
      pen.fillStyle = value;
      pen.fillRect(0, 0, 1, 1);
      const [r = 0, g = 0, b = 0, a = 255] = pen.getImageData(0, 0, 1, 1).data;
      value = `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
    }
    this.#cache.set(css, value);
    return value;
  }

  #resolve(value: unknown): unknown {
    if (typeof value === "string") {
      return CSS.test(value) ? this.read(value) : value;
    }
    return Array.isArray(value)
      ? value.map((part: unknown) => this.#resolve(part))
      : value;
  }

  #dress(gl: Maplibre.Map) {
    const ink = this.read(
      "color-mix(in oklab, var(--foreground) 32%, transparent)",
    );
    for (const shape of ["hatch", "back", "cross", "absent", "norun"]) {
      if (gl.hasImage(shape)) {
        gl.updateImage(shape, pattern(shape, ink));
      } else {
        gl.addImage(shape, pattern(shape, ink));
      }
    }
    const runoff = pattern(
      "hatch",
      this.read("color-mix(in oklab, var(--card) 85%, transparent)"),
    );
    if (gl.hasImage("runoff")) {
      gl.updateImage("runoff", runoff);
    } else {
      gl.addImage("runoff", runoff);
    }
  }

  #restyle() {
    const gl = this.gl;
    if (gl == null) {
      return;
    }
    gl.setPaintProperty("paper", "background-color", this.read("var(--card)"));
    this.#dress(gl);
    for (const [id, layer] of this.#layers) {
      if (layer.spec.type === "custom") {
        continue;
      }
      for (const [key, value] of Object.entries(layer.spec.paint ?? {})) {
        gl.setPaintProperty(
          id,
          key as Paint[0],
          this.#resolve(value) as Paint[1],
        );
      }
    }
    for (const [name, entries] of this.#bands) {
      gl.updateImage(
        name,
        banded(entries.map(([css, weight]) => [this.read(css), weight])),
      );
    }
    for (const [name, colors] of this.#seats) {
      gl.updateImage(name, this.#seated(colors));
    }
    this.#redrawStripes(gl);
    for (const [source, states] of this.#painted) {
      this.paint(source, states, true);
    }
    if (this.#tinted.size > 0) {
      this.tint(this.#tinted);
    }
  }

  #hold(gl: Maplibre.Map) {
    const canvas = gl.getCanvas();
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const box = this.#frame;
    const limit = this.#limit;
    const padding = this.#padding(width, height);
    const framed = gl.cameraForBounds(
      [box.west, box.south, box.east, box.north],
      { padding, maxZoom: FIT },
    );
    const camera =
      limit == null
        ? framed
        : gl.cameraForBounds(
            [
              Math.min(limit.west, box.west),
              Math.min(limit.south, box.south),
              Math.max(limit.east, box.east),
              Math.max(limit.north, box.north),
            ],
            { padding, maxZoom: FIT },
          );
    if (
      width === 0 ||
      framed == null ||
      camera?.zoom == null ||
      camera.center == null
    ) {
      return;
    }
    const { maplibre } = kit ?? {};
    if (maplibre == null) {
      return;
    }
    const middle = maplibre.MercatorCoordinate.fromLngLat(camera.center);
    const world = SIZE * 2 ** camera.zoom;
    const across = (width / 2 + 1) / world;
    const down = (height / 2 + 1) / world;
    const corner = (x: number, y: number) =>
      new maplibre.MercatorCoordinate(x, y).toLngLat();
    gl.setMaxBounds(null);
    gl.setMinZoom(Math.max(-2, camera.zoom - 0.001));
    gl.setMaxBounds(
      new maplibre.LngLatBounds(
        corner(middle.x - across, middle.y + down),
        corner(middle.x + across, middle.y - down),
      ),
    );
    if (!this.#isFramed) {
      return;
    }
    const drift = Math.abs((framed.zoom ?? 0) - gl.getZoom());
    if (drift < 0.1 && !calm() && !gl.isMoving()) {
      gl.easeTo({ ...framed, duration: 300, essential: false });
    } else {
      gl.jumpTo(framed);
    }
  }

  #isGoal(box: Box) {
    const goal = this.#goal;
    return (
      goal != null &&
      goal.west === box.west &&
      goal.south === box.south &&
      goal.east === box.east &&
      goal.north === box.north
    );
  }

  #settle(gl: Maplibre.Map) {
    if (gl.isMoving()) {
      gl.once("moveend", () => this.#hold(gl));
    } else {
      this.#hold(gl);
    }
  }

  frame(box: Box, isAnimated = false) {
    this.#frame = box;
    this.#isFramed = true;
    const gl = this.gl;
    if (gl == null) {
      return;
    }
    if (!isAnimated || this.#isGoal(box)) {
      this.#goal = box;
      this.#settle(gl);
      return;
    }
    this.#goal = box;
    gl.setMaxBounds(null);
    gl.setMinZoom(-2);
    gl.once("moveend", () => this.#hold(gl));
    const canvas = gl.getCanvas();
    gl.fitBounds([box.west, box.south, box.east, box.north], {
      padding: this.#padding(canvas.clientWidth, canvas.clientHeight),
      maxZoom: FIT,
      duration: calm() ? 0 : 900,
    });
  }

  widen(box: Box) {
    const limit = this.#limit ?? this.#frame;
    this.#limit = {
      west: Math.min(limit.west, box.west),
      south: Math.min(limit.south, box.south),
      east: Math.max(limit.east, box.east),
      north: Math.max(limit.north, box.north),
    };
    if (this.gl != null) {
      this.#hold(this.gl);
    }
  }

  home(box: Box) {
    this.#frame = box;
    this.#isFramed = this.#isGoal(box);
    if (this.gl != null) {
      this.#settle(this.gl);
    }
  }

  reset() {
    this.frame(this.#frame, true);
  }

  hold() {
    if (this.gl == null) {
      return;
    }
    if (this.gl.getZoom() - this.gl.getMinZoom() < 0.01) {
      this.#isFramed = true;
    }
    this.#hold(this.gl);
  }

  get isFramed() {
    return this.#isFramed;
  }

  zoomIn() {
    this.#isFramed = false;
    this.#goal = null;
    this.gl?.zoomIn();
  }

  zoomOut() {
    this.#isFramed = false;
    this.#goal = null;
    this.gl?.zoomOut();
  }

  fit(box: Box, onDone?: () => void, isAnimated = true) {
    const gl = this.gl;
    if (gl == null) {
      return;
    }
    if (this.#isGoal(box)) {
      if (onDone != null) {
        if (gl.isMoving()) {
          gl.once("moveend", onDone);
        } else {
          onDone();
        }
      }
      return;
    }
    this.#isFramed = false;
    this.#goal = box;
    if (onDone != null) {
      gl.once("moveend", onDone);
    }
    const canvas = gl.getCanvas();
    gl.fitBounds([box.west, box.south, box.east, box.north], {
      padding: this.#padding(canvas.clientWidth, canvas.clientHeight),
      maxZoom: FIT,
      duration: !isAnimated || calm() ? 0 : 750,
      essential: false,
    });
  }

  fly(center: [number, number], zoom: number) {
    this.#isFramed = false;
    this.#goal = null;
    this.gl?.flyTo({ center, zoom, duration: calm() ? 0 : 1100 });
  }

  fitZoom(box: Box = this.#frame) {
    const gl = this.gl;
    if (gl == null) {
      return 0;
    }
    const canvas = gl.getCanvas();
    return (
      gl.cameraForBounds([box.west, box.south, box.east, box.north], {
        padding: this.#padding(canvas.clientWidth, canvas.clientHeight),
        maxZoom: FIT,
      })?.zoom ?? gl.getZoom()
    );
  }

  shape(source: string, k: number): Box | null {
    const gl = this.gl;
    const entry = this.#sources.get(source);
    if (gl == null || entry == null) {
      return null;
    }
    const box = {
      west: Infinity,
      south: Infinity,
      east: -Infinity,
      north: -Infinity,
    };
    for (const part of gl.querySourceFeatures(source, {
      ...(entry.isVector && { sourceLayer: "areas" }),
      filter: ["==", ["id"], k],
    })) {
      for (const pair of JSON.stringify(part.geometry).matchAll(
        /\[([\d.e-]+),([\d.e-]+)\]/g,
      )) {
        const x = Number(pair[1]);
        const y = Number(pair[2]);
        box.west = Math.min(box.west, x);
        box.south = Math.min(box.south, y);
        box.east = Math.max(box.east, x);
        box.north = Math.max(box.north, y);
      }
    }
    return Number.isFinite(box.west) ? box : null;
  }

  arrows(items: Array<Arrow>, reach: number) {
    this.#arrows =
      items.length === 0
        ? null
        : {
            items: items.toSorted(
              (a, b) =>
                Number(a.isMuted === true) - Number(b.isMuted === true) ||
                (b.weight ?? 0) - (a.weight ?? 0) ||
                Math.abs(b.swing) - Math.abs(a.swing),
            ),
            reach,
          };
    const gl = this.gl;
    if (gl != null) {
      this.#sketch(gl);
    }
  }

  bubbles(total: number, box: Box = this.#frame) {
    const gl = this.gl;
    if (gl == null || total <= 0) {
      return 0;
    }
    const canvas = gl.getCanvas();
    const area = canvas.clientWidth * canvas.clientHeight * 0.5;
    return Math.sqrt((0.1 * area) / (Math.PI * total)) / 2 ** this.fitZoom(box);
  }

  compose() {
    const gl = this.gl;
    if (gl == null) {
      return null;
    }
    const source = gl.getCanvas();
    const out = document.createElement("canvas");
    out.width = source.width;
    out.height = source.height;
    const pen = out.getContext("2d");
    if (pen == null) {
      return null;
    }
    pen.drawImage(source, 0, 0);
    if (this.#overlay != null && this.#arrows != null) {
      pen.drawImage(this.#overlay, 0, 0, out.width, out.height);
    }
    return out;
  }

  #sketch(gl: Maplibre.Map) {
    const overlay = this.#overlay;
    const pen = overlay?.getContext("2d");
    if (overlay == null || pen == null) {
      return;
    }
    const width = overlay.clientWidth;
    const height = overlay.clientHeight;
    const scale = devicePixelRatio;
    if (overlay.width !== Math.round(width * scale)) {
      overlay.width = Math.round(width * scale);
    }
    if (overlay.height !== Math.round(height * scale)) {
      overlay.height = Math.round(height * scale);
    }
    pen.setTransform(scale, 0, 0, scale, 0, 0);
    pen.clearRect(0, 0, width, height);
    const arrows = this.#arrows;
    if (arrows == null) {
      return;
    }
    const placed: Array<{ x: number; y: number; arrow: Arrow }> = [];
    let inside = 0;
    for (const arrow of arrows.items) {
      const point = gl.project(arrow.at);
      if (
        point.x < -LARGEST ||
        point.y < -LARGEST ||
        point.x > width + LARGEST ||
        point.y > height + LARGEST
      ) {
        continue;
      }
      if (
        point.x >= 0 &&
        point.y >= 0 &&
        point.x <= width &&
        point.y <= height
      ) {
        inside += 1;
      }
      placed.push({ x: point.x, y: point.y, arrow });
    }
    const spacing = Math.sqrt((width * height) / Math.max(1, inside));
    const longest = Math.min(56, Math.max(18, spacing * 1.5));
    const weight = Math.min(2.25, Math.max(1.25, longest / 24));
    const gap = longest * 0.55;
    const taken = new Map<string, Array<{ x: number; y: number }>>();
    const kept: typeof placed = [];
    for (const entry of placed) {
      const column = Math.floor(entry.x / gap);
      const row = Math.floor(entry.y / gap);
      let isNear = false;
      for (let dx = -1; dx <= 1 && !isNear; dx += 1) {
        for (let dy = -1; dy <= 1 && !isNear; dy += 1) {
          for (const other of taken.get(`${column + dx}:${row + dy}`) ?? []) {
            if (Math.hypot(other.x - entry.x, other.y - entry.y) < gap) {
              isNear = true;
              break;
            }
          }
        }
      }
      if (isNear) {
        continue;
      }
      const cell = `${column}:${row}`;
      taken.set(cell, [...(taken.get(cell) ?? []), entry]);
      kept.push(entry);
    }
    kept.reverse();
    const marks = [...this.#slots]
      .filter(
        ([id, slot]) => slot === "labels" && gl.getLayer(id)?.type === "symbol",
      )
      .map(([id]) => id);
    const boxes =
      marks.length === 0
        ? []
        : gl.queryRenderedFeatures({ layers: marks }).flatMap((feature) => {
            const name = feature.properties.name;
            const shape: unknown = feature.geometry;
            if (!isPoint(shape) || typeof name !== "string") {
              return [];
            }
            const [lng = 0, lat = 0] = shape.coordinates;
            const point = gl.project([lng, lat]);
            const half = name.length * 3.6 + 6;
            return [
              {
                west: point.x - half,
                east: point.x + half,
                north: point.y - 10,
                south: point.y + 10,
              },
            ];
          });
    const halo = this.read("var(--card)");
    pen.lineCap = "round";
    pen.lineJoin = "round";
    for (const { x, y, arrow } of kept) {
      const size = Math.min(1, Math.abs(arrow.swing) / arrows.reach);
      if (size < 0.01) {
        continue;
      }
      const length = Math.max(4, longest * size);
      const side = arrow.swing >= 0 ? 1 : -1;
      const dx = side * Math.cos(TILT);
      const dy = -Math.sin(TILT);
      const from = [x - (dx * length) / 2, y - (dy * length) / 2] as const;
      const to = [x + (dx * length) / 2, y + (dy * length) / 2] as const;
      const head = Math.min(6, Math.max(2.5, length * 0.4)) + weight;
      const angle = Math.atan2(dy, dx);
      const path = new Path2D();
      path.moveTo(from[0], from[1]);
      path.lineTo(to[0], to[1]);
      path.moveTo(
        to[0] - head * Math.cos(angle - 0.55),
        to[1] - head * Math.sin(angle - 0.55),
      );
      path.lineTo(to[0], to[1]);
      path.lineTo(
        to[0] - head * Math.cos(angle + 0.55),
        to[1] - head * Math.sin(angle + 0.55),
      );
      const reachX = (Math.abs(dx) * length) / 2;
      const reachY = (Math.abs(dy) * length) / 2;
      const isCovered = boxes.some(
        (box) =>
          x + reachX > box.west &&
          x - reachX < box.east &&
          y + reachY > box.north &&
          y - reachY < box.south,
      );
      pen.globalAlpha = arrow.isMuted === true ? 0.22 : isCovered ? 0.45 : 1;
      pen.strokeStyle = halo;
      pen.lineWidth = weight + 2.5;
      pen.stroke(path);
      pen.strokeStyle = this.read(arrow.color);
      pen.lineWidth = weight;
      pen.stroke(path);
    }
    pen.globalAlpha = 1;
  }

  project(at: [number, number]) {
    return this.gl?.project(at) ?? null;
  }

  snapshot() {
    try {
      return this.compose()?.toDataURL("image/webp", 0.85) ?? null;
    } catch {
      return null;
    }
  }

  cursor(isPointer: boolean) {
    const gl = this.gl;
    if (gl != null) {
      gl.getCanvas().style.cursor = isPointer ? "pointer" : "";
    }
  }

  tiles(key: string, group = "") {
    const id = `t:${key}`;
    const gl = this.gl;
    if (gl == null || gl.getSource(id) != null) {
      return id;
    }
    const archive = kit?.archive(key);
    attach(gl, id, {
      type: "vector",
      url: `pmtiles://${location.origin}/tiles/${key}.pmtiles`,
    });
    this.#sources.set(id, { group, isVector: true });
    if (archive != null) {
      this.#credit(archive, key);
    }
    return id;
  }

  #credit(archive: PMTiles, key: string) {
    const name = `Map credits ${key}`;
    void archive
      .getMetadata()
      .then((meta) => {
        this.#retries?.worked(name);
        const text = String(
          (meta as { attribution?: unknown } | null)?.attribution ?? "",
        )
          .replace(/(?:Coastlines|Map data) © OpenStreetMap[^\n]*/u, "")
          .trim();
        const credits = new Set([
          ...this.credits,
          ...text
            .split(text.includes("\n") ? "\n" : "; ")
            .map((part) => part.trim())
            .filter((part) => part !== ""),
        ]);
        this.credits = [...credits].filter(
          (credit) => !credits.has(`© ${credit}`),
        );
      })
      .catch((error: unknown) => {
        this.#retries?.failed(name, error, () => this.#credit(archive, key));
      });
  }

  points(
    id: string,
    places: Array<{
      k?: number;
      at: [number, number];
      properties?: Record<string, string | number>;
    }>,
    group = "",
  ) {
    const key = JSON.stringify(places);
    if (this.#pointed.get(id) === key && this.gl?.getSource(id) != null) {
      return id;
    }
    this.#pointed.set(id, key);
    return this.shapes(
      id,
      {
        type: "FeatureCollection",
        features: places.map((place) => ({
          type: "Feature",
          ...(place.k != null && { id: place.k }),
          properties: place.properties ?? {},
          geometry: { type: "Point", coordinates: place.at },
        })),
      },
      group,
    );
  }

  shapes(
    id: string,
    data: Maplibre.GeoJSONSourceSpecification["data"],
    group = "",
    tolerance?: number,
  ) {
    const gl = this.gl;
    if (gl == null) {
      return id;
    }
    const source = gl.getSource(id);
    if (source?.type === "geojson") {
      void (source as Maplibre.GeoJSONSource).setData(data);
    } else {
      attach(gl, id, {
        type: "geojson",
        data,
        ...(tolerance != null && { tolerance }),
      });
      this.#sources.set(id, { group, isVector: false });
    }
    return id;
  }

  veil(id: string, outline: Array<Array<[number, number]>>, group = "") {
    // A world polygon with the outline as holes breaks when MapLibre snaps it to each tile's grid, so draw it as triangles.
    const points: Array<[number, number]> = [
      [-180, -85],
      [180, -85],
      [180, 85],
      [-180, 85],
    ];
    const holes: Array<number> = [];
    for (const ring of outline) {
      holes.push(points.length);
      points.push(...ring.slice(0, -1));
    }
    const corners = earcut(
      points.flatMap(([x, y]) => [
        x,
        Math.log(Math.tan(Math.PI / 4 + (y * Math.PI) / 360)),
      ]),
      holes,
    );
    const triangles: Array<Array<Array<[number, number]>>> = [];
    for (const index of corners.keys()) {
      if (index % 3 === 0) {
        const ring = corners.slice(index, index + 3).flatMap((corner) => {
          const point = points[corner];
          return point == null ? [] : [point];
        });
        triangles.push([[...ring, ...ring.slice(0, 1)]]);
      }
    }
    return this.shapes(
      id,
      { type: "MultiPolygon", coordinates: triangles },
      group,
      0,
    );
  }

  rendered(layer: string) {
    const gl = this.gl;
    if (gl?.getLayer(layer) == null) {
      return [];
    }
    return [
      ...new Set(
        gl
          .queryRenderedFeatures({ layers: [layer] })
          .flatMap((feature) =>
            typeof feature.id === "number" ? [feature.id] : [],
          ),
      ),
    ];
  }

  add(
    spec: Maplibre.LayerSpecification | Maplibre.CustomLayerInterface,
    slot: Slot,
    options: { group?: string; rank?: number } = {},
  ) {
    const gl = this.gl;
    if (gl == null || gl.getLayer(spec.id) != null) {
      return;
    }
    const rank = options.rank ?? -1;
    const after =
      rank < 0
        ? undefined
        : [...this.#layers]
            .filter(
              ([id, layer]) =>
                layer.rank > rank && this.#slots.get(id) === slot,
            )
            .toSorted(([, a], [, b]) => a.rank - b.rank)[0]?.[0];
    if (spec.type === "custom") {
      gl.addLayer(spec, after ?? `slot:${slot}`);
    } else {
      const resolved = {
        ...spec,
        paint: Object.fromEntries(
          Object.entries(spec.paint ?? {}).map(([key, value]) => [
            key,
            this.#resolve(value),
          ]),
        ),
      } as Maplibre.LayerSpecification;
      gl.style.addLayer(resolved, after ?? `slot:${slot}`, CHECKED);
      gl._update(true);
    }
    this.#layers.set(spec.id, { spec, group: options.group ?? "", rank });
    this.#slots.set(spec.id, slot);
  }

  has(id: string) {
    return this.#layers.has(id);
  }

  filter(id: string, filter: Maplibre.FilterSpecification | null) {
    if (this.gl?.getLayer(id) != null) {
      this.gl.setFilter(id, filter, CHECKED);
    }
  }

  style(id: string, key: string, value: unknown) {
    const layer = this.#layers.get(id);
    if (layer == null || this.gl == null) {
      return;
    }
    if (layer.spec.type === "custom") {
      this.#mosaic?.fade(id, value as number | ReadonlyArray<unknown>);
      return;
    }
    layer.spec = {
      ...layer.spec,
      paint: { ...layer.spec.paint, [key]: value },
    } as Maplibre.LayerSpecification;
    this.gl.setPaintProperty(
      id,
      key as Paint[0],
      this.#resolve(value) as Paint[1],
    );
  }

  show(id: string, isShown: boolean) {
    if (this.gl?.getLayer(id) != null) {
      this.gl.setLayoutProperty(id, "visibility", isShown ? "visible" : "none");
    }
  }

  fadeOut(group: string) {
    for (const [id, layer] of this.#layers) {
      if (layer.group !== group) {
        continue;
      }
      for (const key of OPACITY[layer.spec.type] ?? []) {
        this.style(id, key, 0);
      }
    }
  }

  drop(group: string) {
    const gl = this.gl;
    if (gl == null) {
      return;
    }
    for (const [id, layer] of [...this.#layers].toReversed()) {
      if (layer.group !== group) {
        continue;
      }

      gl.removeLayer(id);
      this.#layers.delete(id);
      this.#slots.delete(id);
    }
    for (const [id, source] of this.#sources) {
      if (source.group !== group) {
        continue;
      }

      gl.removeSource(id);
      this.#sources.delete(id);
      this.#painted.delete(id);
    }
    this.#rasterKeys = this.#rasterKeys.filter((key) =>
      this.#layers.has(`ids:${key}`),
    );
  }

  bands(entries: Array<[string, number]>) {
    const name = `bands:${JSON.stringify(entries)}`;
    const gl = this.gl;
    if (gl != null && !gl.hasImage(name)) {
      gl.addImage(
        name,
        banded(entries.map(([css, weight]) => [this.read(css), weight])),
        { pixelRatio: 2 },
      );
      this.#bands.set(name, entries);
    }
    return name;
  }

  #seated(colors: Array<string>) {
    return seated(
      colors.map((css) => this.read(css)),
      this.read("var(--card)"),
      this.read("color-mix(in oklab, var(--foreground) 22%, transparent)"),
    );
  }

  seats(colors: Array<string>) {
    const name = `seats:${JSON.stringify(colors)}`;
    const gl = this.gl;
    if (gl != null && !gl.hasImage(name)) {
      gl.addImage(name, this.#seated(colors), { pixelRatio: 2 });
      this.#seats.set(name, colors);
    }
    return name;
  }

  #redrawStripes(gl: Maplibre.Map) {
    this.#stripeLines = hatchLines(gl.getZoom());
    for (const [css, index] of this.#stripes) {
      const name = `stripes:${index}`;
      if (gl.hasImage(name)) {
        gl.updateImage(
          name,
          hatched(this.read(css), this.#stripeLines, this.#stripeTile),
        );
      }
    }
  }

  #fitStripes(gl: Maplibre.Map) {
    if (hatchLines(gl.getZoom()) !== this.#stripeLines) {
      this.#redrawStripes(gl);
    }
  }

  stripe(base: string, css: string) {
    const index = this.#stripes.get(css) ?? this.#stripes.size;
    this.#stripes.set(css, index);
    const name = `stripes:${index}`;
    const gl = this.gl;
    if (gl != null) {
      this.#fitStripes(gl);
      if (!gl.hasImage(name)) {
        gl.addImage(
          name,
          hatched(this.read(css), this.#stripeLines, this.#stripeTile),
          {
            pixelRatio:
              this.#stripeTile / (HATCH_LINES * STRIPE.period * Math.SQRT2),
          },
        );
      }
    }
    const layer = this.#layers.get(base);
    const slot = this.#slots.get(base);
    if (layer?.spec.type === "fill" && slot != null) {
      this.add(
        {
          ...layer.spec,
          id: `${base}:${name}`,
          paint: {
            "fill-pattern": name,
            "fill-opacity": [
              "case",
              ["==", ["feature-state", "stripe"], index],
              1,
              0,
            ],
          },
        },
        slot,
        { group: layer.group, rank: layer.rank },
      );
    }
    return index;
  }

  paint(source: string, states: Map<number, State>, isMerged = false) {
    const gl = this.gl;
    const entry = this.#sources.get(source);
    if (gl == null || entry == null) {
      return;
    }
    const where = entry.isVector
      ? { source, sourceLayer: "areas" }
      : { source };
    const held = this.#painted.get(source);
    if (isMerged && held != null) {
      for (const [k, state] of states) {
        held.set(k, state);
      }
    } else if (held == null) {
      gl.removeFeatureState(where);
    } else {
      for (const k of held.keys()) {
        if (!states.has(k)) {
          gl.removeFeatureState({ ...where, id: k });
        }
      }
    }
    if (!isMerged || held == null) {
      this.#painted.set(source, new Map(states));
    }
    for (const [k, state] of states) {
      const last = isMerged ? undefined : held?.get(k);
      if (last != null) {
        const keys = Object.keys(last);
        if (
          keys.length === Object.keys(state).length &&
          keys.every((key) => Object.is(last[key], state[key]))
        ) {
          continue;
        }
        if (keys.some((key) => !(key in state))) {
          gl.removeFeatureState({ ...where, id: k });
        }
      }
      gl.setFeatureState(
        { ...where, id: k },
        Object.fromEntries(
          Object.entries(state).map(([key, value]) => [
            key,
            typeof value === "string" ? this.read(value) : value,
          ]),
        ),
      );
    }
    for (const [key, mark] of this.#marks) {
      if (mark.source === source) {
        gl.setFeatureState({ ...where, id: mark.k }, { [key]: true });
      }
    }
  }

  mark(key: string, source: string | null, k: number | null) {
    const gl = this.gl;
    if (gl == null) {
      return;
    }
    const last = this.#marks.get(key);
    const entry = last == null ? null : this.#sources.get(last.source);
    if (last != null && entry != null) {
      gl.setFeatureState(
        {
          source: last.source,
          ...(entry.isVector && { sourceLayer: "areas" }),
          id: last.k,
        },
        { [key]: false },
      );
    }
    this.#marks.delete(key);
    const next = source == null ? null : this.#sources.get(source);
    if (source == null || next == null || k == null) {
      return;
    }
    gl.setFeatureState(
      { source, ...(next.isVector && { sourceLayer: "areas" }), id: k },
      { [key]: true },
    );
    this.#marks.set(key, { source, k });
  }

  drape(keys: Array<string>, group = "", rank = 0.5) {
    const gl = this.gl;
    const { archive } = kit ?? {};
    const retries = this.#retries;
    if (gl == null || archive == null || retries == null) {
      return;
    }
    this.#mosaic ??= new Mosaic(gl, archive, retries);
    this.#rasterKeys = keys;
    for (const key of keys) {
      for (const isFocus of [false, true]) {
        const id = `${isFocus ? "spot" : "ids"}:${key}`;
        this.add(
          this.#mosaic.layer(id, key, isFocus),
          isFocus ? "focus" : "fills",
          { group, rank: isFocus ? -1 : rank },
        );
      }
    }
  }

  tint(colors: Map<number, string>) {
    this.#tinted = colors;
    const packed = new Map<string, number>();
    const pack = (css: string) => {
      const [r = 0, g = 0, b = 0, a = 1] = (
        this.read(css).match(/[\d.]+/g) ?? []
      ).map(Number);
      return (r | (g << 8) | (b << 16) | (Math.round(a * 255) << 24)) >>> 0;
    };
    const tints = new Map<number, number>();
    for (const [k, css] of colors) {
      let value = packed.get(css);
      if (value == null) {
        value = pack(css);
        packed.set(css, value);
      }
      tints.set(k, value);
    }
    this.#mosaic?.tint(tints, pack("var(--foreground)"));
  }

  spot(k: number | null) {
    this.#mosaic?.spot(k ?? 0);
  }

  pick(point: { x: number; y: number }, layers?: Array<string>) {
    const gl = this.gl;
    if (gl == null) {
      return null;
    }
    const ranked = (
      layers ??
      [...this.#layers]
        .filter(
          ([, layer]) =>
            layer.rank >= 0 &&
            layer.spec.type !== "raster" &&
            layer.spec.type !== "custom",
        )
        .toSorted(([, a], [, b]) => b.rank - a.rank)
        .map(([id]) => id)
    ).filter((id) => gl.getLayer(id) != null);
    const found = gl
      .queryRenderedFeatures([point.x, point.y], { layers: ranked })
      .flatMap((feature) =>
        typeof feature.id === "number"
          ? [
              {
                k: feature.id,
                rank: this.#layers.get(feature.layer.id)?.rank ?? 0,
              },
            ]
          : [],
      );
    if (layers != null || (found[0]?.rank ?? 0) > 0) {
      return found[0]?.k ?? null;
    }
    return this.sample(point) ?? found[0]?.k ?? null;
  }

  sample(point: { x: number; y: number }) {
    const gl = this.gl;
    if (gl == null) {
      return null;
    }
    const at = gl.unproject([point.x, point.y]);
    const sin = Math.sin((at.lat * Math.PI) / 180);
    const top = Math.min(8, Math.max(0, Math.round(gl.getZoom() + 1)));
    for (const key of this.#rasterKeys.toReversed()) {
      for (let z = top; z >= 0; z -= 1) {
        const world = SIZE * 2 ** z;
        const x = ((at.lng + 180) / 360) * world;
        const y =
          (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * world;
        const tile = sampled(
          key,
          z,
          Math.floor(x / SIZE),
          Math.floor(y / SIZE),
        );
        if (tile == null) {
          continue;
        }
        const k =
          tile.ids[
            tile.index[
              (Math.floor(y) % SIZE) * SIZE + (Math.floor(x) % SIZE)
            ] ?? 0
          ] ?? 0;
        if (this.#mosaic?.tints.has(k) === true) {
          return k;
        }
        break;
      }
    }
    return null;
  }

  jump(center: [number, number], zoom: number) {
    this.#isFramed = false;
    this.#goal = null;
    this.gl?.jumpTo({ center, zoom });
  }
}
