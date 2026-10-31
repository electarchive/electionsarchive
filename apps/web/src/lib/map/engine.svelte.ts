import type * as Maplibre from "maplibre-gl";
import type { PMTiles, Protocol } from "pmtiles";
import { untrack } from "svelte";
import { on } from "svelte/events";
import { isHover, isTouch } from "#lib/explain.svelte.js";

export type Box = { west: number; south: number; east: number; north: number };
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
type Raster = { ids: Uint32Array; index: Uint8Array | Uint16Array };
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

let library: Promise<{
  maplibre: typeof Maplibre;
  PMTiles: typeof PMTiles;
  protocol: Protocol;
}> | null = null;
const engines = new Map<string, Engine>();
const archives = new Map<string, PMTiles>();
const rasters = new Map<string, Promise<Raster | null>>();
const loaded = new Map<string, Raster>();
let serial = 0;
let kit: { maplibre: typeof Maplibre; PMTiles: typeof PMTiles } | null = null;

const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

const archive = (make: typeof PMTiles, key: string) => {
  let found = archives.get(key);
  if (found == null) {
    found = new make(`${location.origin}/tiles/${key}.pmtiles`);
    archives.set(key, found);
  }
  return found;
};

async function load() {
  library ??= Promise.all([
    import("maplibre-gl"),
    import("pmtiles"),
    import("maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url"),
    import("maplibre-gl/dist/maplibre-gl.css"),
  ]).then(([maplibre, { Protocol, PMTiles }, worker]) => {
    maplibre.setWorkerUrl(worker.default);
    const protocol = new Protocol();
    maplibre.addProtocol("pmtiles", protocol.tile);
    maplibre.addProtocol("ids", async ({ url }) => {
      const match =
        /^ids:\/\/(\d+)\/(.+)\/(\d+)\/(\d+)\/(\d+)\?v=\d+(?:&focus=(\d+))?$/u.exec(
          url,
        );
      const [, owner = "", key = "", z = "0", x = "0", y = "0", focus] =
        match ?? [];
      const engine = engines.get(owner);
      const name = `${key}/${z}/${x}/${y}`;
      let pending = rasters.get(name);
      if (pending == null) {
        pending = archive(PMTiles, key)
          .getZxy(Number(z), Number(x), Number(y))
          .then((found) => {
            if (found == null) {
              return null;
            }
            const view = new DataView(found.data);
            const total = view.getUint32(0, true);
            const ids = new Uint32Array(total);
            let id = 0;
            for (const j of ids.keys()) {
              id += view.getInt32(4 + 4 * j, true);
              ids[j] = id;
            }
            const tile = {
              ids,
              index:
                total <= 256
                  ? new Uint8Array(found.data, 4 + 4 * total, SIZE * SIZE)
                  : new Uint16Array(found.data, 4 + 4 * total, SIZE * SIZE),
            };
            loaded.set(name, tile);
            return tile;
          });
        rasters.set(name, pending);
        if (rasters.size > 240) {
          const oldest = rasters.keys().next().value ?? "";
          rasters.delete(oldest);
          loaded.delete(oldest);
        }
      }
      const tile = await pending;
      const pixels = new Uint32Array(SIZE * SIZE);
      if (tile != null && engine != null && focus == null) {
        const lut = tile.ids.map((k) => engine.tints.get(k) ?? 0);
        let p = 0;
        for (const value of tile.index) {
          pixels[p] = lut[value] ?? 0;
          p += 1;
        }
      } else if (tile != null && engine != null && focus !== "0") {
        const target = tile.ids.indexOf(Number(focus));
        const { index } = tile;
        let p = 0;
        for (const value of target === -1 ? [] : index) {
          if (value === target) {
            const column = p % SIZE;
            const row = (p - column) / SIZE;
            for (const d of [1, 2]) {
              if (
                (column >= d && index[p - d] !== target) ||
                (column < SIZE - d && index[p + d] !== target) ||
                (row >= d && index[p - SIZE * d] !== target) ||
                (row < SIZE - d && index[p + SIZE * d] !== target)
              ) {
                pixels[p] = engine.ink;
              }
            }
          }
          p += 1;
        }
      }
      return {
        data: await createImageBitmap(
          new ImageData(new Uint8ClampedArray(pixels.buffer), SIZE, SIZE),
        ),
      };
    });
    kit = { maplibre, PMTiles };
    return { maplibre, PMTiles, protocol };
  });
  return library;
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

function arrow() {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 24;
  const pen = canvas.getContext("2d", { willReadFrequently: true });
  if (pen != null) {
    pen.fillStyle = "#fff";
    pen.shadowColor = "#fff";
    pen.shadowBlur = 3;
    pen.beginPath();
    for (const [x, y] of [
      [4, 9.5],
      [40, 9.5],
      [40, 3.5],
      [59, 12],
      [40, 20.5],
      [40, 14.5],
      [4, 14.5],
    ] as const) {
      pen.lineTo(x, y);
    }
    pen.closePath();
    pen.fill();
  }
  return {
    width: 64,
    height: 24,
    data: new Uint8Array(
      pen?.getImageData(0, 0, 64, 24).data.buffer ?? new ArrayBuffer(6144),
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
  credits = $state<Array<string>>([]);
  theme = $state(0);
  gl = $state.raw<Maplibre.Map | null>(null);
  tints = new Map<number, number>();
  ink = 0;

  #id = String((serial += 1));
  #frame: Box;
  #padding: (width: number, height: number) => Padding;
  #options: Options;
  #isFramed = true;
  #limit: Box | null;
  #layers = new Map<
    string,
    { spec: Maplibre.LayerSpecification; group: string; rank: number }
  >();
  #sources = new Map<string, { group: string; isVector: boolean }>();
  #painted = new Map<string, Map<number, State>>();
  #marks = new Map<string, { source: string; k: number }>();
  #tinted = new Map<number, string>();
  #bands = new Map<string, Array<[string, number]>>();
  #slots = new Map<string, Slot>();
  #rasterKeys: Array<string> = [];
  #version = 0;
  #spotted = 0;
  #probe: HTMLElement | null = null;
  #cache = new Map<string, string>();
  #canvas: CanvasRenderingContext2D | null = null;

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
    engines.set(this.#id, this);
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
      gl.on("movestart", (event) => {
        if (event.originalEvent != null) {
          this.#isFramed = false;
        }
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
      });
      gl.on("resize", () => this.#hold(gl));
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
    );
    return () => {
      isGone = true;
      for (const off of offs) {
        off();
      }
      engines.delete(this.#id);
      this.#probe?.remove();
      this.gl?.remove();
      this.gl = null;
      this.isLoaded = false;
    };
  };

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
    if (!gl.hasImage("arrow")) {
      gl.addImage("arrow", arrow(), { sdf: true });
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
    if (this.#isFramed) {
      gl.jumpTo(framed);
    }
  }

  frame(box: Box, isAnimated = false) {
    this.#frame = box;
    this.#isFramed = true;
    const gl = this.gl;
    if (gl == null) {
      return;
    }
    if (!isAnimated) {
      this.#hold(gl);
      return;
    }
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
    this.gl?.zoomIn();
  }

  zoomOut() {
    this.#isFramed = false;
    this.gl?.zoomOut();
  }

  fit(box: Box, onDone?: () => void) {
    const gl = this.gl;
    if (gl == null) {
      return;
    }
    this.#isFramed = false;
    if (onDone != null) {
      gl.once("moveend", onDone);
    }
    const canvas = gl.getCanvas();
    gl.fitBounds([box.west, box.south, box.east, box.north], {
      padding: this.#padding(canvas.clientWidth, canvas.clientHeight),
      maxZoom: FIT,
      duration: calm() ? 0 : 750,
      essential: false,
    });
  }

  fly(center: [number, number], zoom: number) {
    this.#isFramed = false;
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

  project(at: [number, number]) {
    return this.gl?.project(at) ?? null;
  }

  snapshot() {
    try {
      return this.gl?.getCanvas().toDataURL("image/webp", 0.85) ?? null;
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
    gl.addSource(id, {
      type: "vector",
      url: `pmtiles://${location.origin}/tiles/${key}.pmtiles`,
    });
    this.#sources.set(id, { group, isVector: true });
    const { PMTiles } = kit ?? {};
    if (PMTiles != null) {
      void archive(PMTiles, key)
        .getMetadata()
        .then((meta) => {
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
        });
    }
    return id;
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
  ) {
    const gl = this.gl;
    if (gl == null) {
      return id;
    }
    const source = gl.getSource(id);
    if (source?.type === "geojson") {
      void (source as Maplibre.GeoJSONSource).setData(data);
    } else {
      gl.addSource(id, { type: "geojson", data });
      this.#sources.set(id, { group, isVector: false });
    }
    return id;
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
    spec: Maplibre.LayerSpecification,
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
    const resolved = {
      ...spec,
      paint: Object.fromEntries(
        Object.entries(spec.paint ?? {}).map(([key, value]) => [
          key,
          this.#resolve(value),
        ]),
      ),
    } as Maplibre.LayerSpecification;
    gl.addLayer(resolved, after ?? `slot:${slot}`);
    this.#layers.set(spec.id, { spec, group: options.group ?? "", rank });
    this.#slots.set(spec.id, slot);
  }

  has(id: string) {
    return this.#layers.has(id);
  }

  filter(id: string, filter: Maplibre.FilterSpecification | null) {
    if (this.gl?.getLayer(id) != null) {
      this.gl.setFilter(id, filter);
    }
  }

  style(id: string, key: string, value: unknown) {
    const layer = this.#layers.get(id);
    if (layer == null || this.gl == null) {
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
      this.#sources.has(`ids:${key}`),
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
    } else {
      gl.removeFeatureState(where);
      this.#painted.set(source, new Map(states));
    }
    for (const [k, state] of states) {
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
    if (source != null && next != null && k != null) {
      gl.setFeatureState(
        { source, ...(next.isVector && { sourceLayer: "areas" }), id: k },
        { [key]: true },
      );
      this.#marks.set(key, { source, k });
    }
  }

  drape(keys: Array<string>, group = "") {
    const gl = this.gl;
    if (gl == null) {
      return;
    }
    this.#rasterKeys = keys;
    for (const key of keys) {
      for (const isFocus of [false, true]) {
        const id = `${isFocus ? "spot" : "ids"}:${key}`;
        if (gl.getSource(id) == null) {
          gl.addSource(id, {
            type: "raster",
            tiles: [this.#rasterUrl(key, isFocus)],
            tileSize: 256,
            maxzoom: 8,
          });
          this.#sources.set(id, { group, isVector: false });
        }
        this.add(
          {
            id,
            type: "raster",
            source: id,
            paint: { "raster-fade-duration": 0 },
          },
          isFocus ? "focus" : "fills",
          { group, rank: isFocus ? -1 : 0.5 },
        );
      }
    }
  }

  #rasterUrl(key: string, isFocus: boolean) {
    return `ids://${this.#id}/${key}/{z}/{x}/{y}?v=${this.#version}${isFocus ? `&focus=${this.#spotted}` : ""}`;
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
    this.tints.clear();
    for (const [k, css] of colors) {
      let value = packed.get(css);
      if (value == null) {
        value = pack(css);
        packed.set(css, value);
      }
      this.tints.set(k, value);
    }
    this.ink = pack("var(--foreground)");
    this.#version += 1;
    for (const key of this.#rasterKeys) {
      this.gl
        ?.getSource<Maplibre.RasterTileSource>(`ids:${key}`)
        ?.setTiles([this.#rasterUrl(key, false)]);
    }
  }

  spot(k: number | null) {
    const next = k ?? 0;
    if (next === this.#spotted) {
      return;
    }
    this.#spotted = next;
    this.#version += 1;
    for (const key of this.#rasterKeys) {
      this.gl
        ?.getSource<Maplibre.RasterTileSource>(`spot:${key}`)
        ?.setTiles([this.#rasterUrl(key, true)]);
    }
  }

  pick(point: { x: number; y: number }, layers?: Array<string>) {
    const gl = this.gl;
    if (gl == null) {
      return null;
    }
    const ranked = (
      layers ??
      [...this.#layers]
        .filter(([, layer]) => layer.rank >= 0 && layer.spec.type !== "raster")
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
    const at = gl.unproject([point.x, point.y]);
    const sin = Math.sin((at.lat * Math.PI) / 180);
    const top = Math.min(8, Math.max(0, Math.round(gl.getZoom() + 1)));
    for (const key of this.#rasterKeys.toReversed()) {
      for (let z = top; z >= 0; z -= 1) {
        const world = SIZE * 2 ** z;
        const x = ((at.lng + 180) / 360) * world;
        const y =
          (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * world;
        const tile = loaded.get(
          `${key}/${z}/${Math.floor(x / SIZE)}/${Math.floor(y / SIZE)}`,
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
        if (this.tints.has(k)) {
          return k;
        }
        break;
      }
    }
    return found[0]?.k ?? null;
  }
}
