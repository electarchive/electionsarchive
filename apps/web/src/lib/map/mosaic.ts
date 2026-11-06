import type * as Maplibre from "maplibre-gl";
import type { PMTiles } from "pmtiles";
import type { Retries } from "#lib/map/retries.js";

type Raster = { ids: Uint32Array; index: Uint8Array | Uint16Array };
type Fade = number | ReadonlyArray<unknown>;
type Texture = {
  index: WebGLTexture;
  palette: WebGLTexture;
  tinted: number;
  spotted: number;
  focus: number;
  used: number;
};
type Program = {
  program: WebGLProgram;
  places: Record<string, WebGLUniformLocation | null>;
};

const SIZE = 512;
const TOP = 8;
const ROW = 4096;
const KEPT = 240;
const HELD = 160;

const decoding = new Map<string, Promise<Raster | null>>();
const loaded = new Map<string, Raster | null>();

const VERTEX = `#version 300 es
in vec2 a_unit;
uniform mat4 u_matrix;
uniform vec4 u_rect;
uniform vec4 u_uv;
out vec2 v_uv;
void main() {
  v_uv = mix(u_uv.xy, u_uv.zw, a_unit);
  gl_Position = u_matrix * vec4(mix(u_rect.xy, u_rect.zw, a_unit), 0.0, 1.0);
}`;

const HEAD = `#version 300 es
precision highp float;
precision highp int;
precision highp usampler2D;
uniform usampler2D u_index;
in vec2 v_uv;
out vec4 color;
int at(ivec2 texel) {
  return int(texelFetch(u_index, clamp(texel, ivec2(0), ivec2(${SIZE - 1})), 0).r);
}`;

const FILL = `${HEAD}
uniform sampler2D u_palette;
uniform float u_opacity;
void main() {
  int i = at(ivec2(floor(v_uv * ${SIZE}.0)));
  color = texelFetch(u_palette, ivec2(i % ${ROW}, i / ${ROW}), 0) * u_opacity;
}`;

const EDGE = `${HEAD}
uniform int u_focus;
uniform vec4 u_ink;
uniform float u_width;
void main() {
  vec2 p = v_uv * ${SIZE}.0;
  if (u_focus < 0 || at(ivec2(floor(p))) != u_focus) {
    discard;
  }
  vec2 step = fwidth(p);
  for (float d = 1.0; d <= u_width; d += 1.0) {
    for (int i = 0; i < 8; i++) {
      float turn = float(i) * 0.785398;
      vec2 q = floor(p + vec2(cos(turn), sin(turn)) * step * d);
      if (any(lessThan(q, vec2(0.0))) || any(greaterThan(q, vec2(${SIZE - 1}.0)))) {
        continue;
      }
      if (at(ivec2(q)) != u_focus) {
        color = u_ink;
        return;
      }
    }
  }
  discard;
}`;

function decode(data: ArrayBuffer): Raster {
  const view = new DataView(data);
  const total = view.getUint32(0, true);
  const ids = new Uint32Array(total);
  let id = 0;
  for (const j of ids.keys()) {
    id += view.getInt32(4 + 4 * j, true);
    ids[j] = id;
  }
  return {
    ids,
    index:
      total <= 256
        ? new Uint8Array(data, 4 + 4 * total, SIZE * SIZE)
        : new Uint16Array(data, 4 + 4 * total, SIZE * SIZE),
  };
}

async function fetchTile(
  archive: PMTiles,
  name: string,
  z: number,
  x: number,
  y: number,
) {
  let pending = decoding.get(name);
  if (pending == null) {
    pending = archive
      .getZxy(z, x, y)
      .then((found) => {
        const tile = found == null ? null : decode(found.data);
        loaded.set(name, tile);
        return tile;
      })
      .catch((error: unknown) => {
        decoding.delete(name);
        throw error;
      });
    decoding.set(name, pending);
    if (decoding.size > KEPT) {
      const oldest = decoding.keys().next().value ?? "";
      decoding.delete(oldest);
      loaded.delete(oldest);
    }
  }
  return pending;
}

export function sampled(key: string, z: number, x: number, y: number) {
  return loaded.get(`${key}/${z}/${x}/${y}`) ?? null;
}

function level(value: Fade, zoom: number) {
  if (typeof value === "number") {
    return value;
  }
  const stops = value.slice(3).map(Number);
  if (stops.length < 2) {
    return 1;
  }
  if (zoom <= (stops[0] ?? 0)) {
    return stops[1] ?? 1;
  }
  for (let i = 2; i < stops.length; i += 2) {
    const from = stops[i - 2] ?? 0;
    const to = stops[i] ?? 0;
    if (zoom <= to) {
      const a = stops[i - 1] ?? 0;
      const b = stops[i + 1] ?? 0;
      return a + ((b - a) * (zoom - from)) / Math.max(1e-6, to - from);
    }
  }
  return stops.at(-1) ?? 1;
}

function compile(
  gl: WebGL2RenderingContext,
  fragment: string,
  names: Array<string>,
) {
  const program = gl.createProgram();
  for (const [kind, source] of [
    [gl.VERTEX_SHADER, VERTEX],
    [gl.FRAGMENT_SHADER, fragment],
  ] as const) {
    const shader = gl.createShader(kind);
    if (shader == null) {
      continue;
    }
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    gl.attachShader(program, shader);
  }
  gl.bindAttribLocation(program, 0, "a_unit");
  gl.linkProgram(program);
  return {
    program,
    places: Object.fromEntries(
      names.map((name) => [name, gl.getUniformLocation(program, name)]),
    ),
  };
}

export class Mosaic {
  #map: Maplibre.Map;
  #open: (key: string) => PMTiles;
  #gl: WebGL2RenderingContext | null = null;
  #fill: Program | null = null;
  #edge: Program | null = null;
  #quad: WebGLVertexArrayObject | null = null;
  #textures = new Map<string, Texture>();
  #fades = new Map<string, Fade>();
  #tints = new Map<number, number>();
  #ink: [number, number, number, number] = [0, 0, 0, 1];
  #version = 1;
  #spotted = 0;
  #frame = 0;
  #retries: Retries;
  #asking = new Set<string>();
  #waiting = new Set<string>();

  constructor(
    map: Maplibre.Map,
    open: (key: string) => PMTiles,
    retries: Retries,
  ) {
    this.#map = map;
    this.#open = open;
    this.#retries = retries;
  }

  get tints() {
    return this.#tints;
  }

  layer(
    id: string,
    key: string,
    isFocus: boolean,
  ): Maplibre.CustomLayerInterface {
    return {
      id,
      type: "custom",
      renderingMode: "2d",
      onAdd: (_map, gl) => {
        if (gl instanceof WebGL2RenderingContext) {
          this.#setup(gl);
        }
      },
      render: (_gl, options) => {
        this.#render(key, isFocus, options.defaultProjectionData.mainMatrix);
      },
    };
  }

  tint(tints: Map<number, number>, ink: number) {
    this.#tints = tints;
    this.#ink = [
      (ink & 255) / 255,
      ((ink >>> 8) & 255) / 255,
      ((ink >>> 16) & 255) / 255,
      ((ink >>> 24) & 255) / 255,
    ];
    this.#version += 1;
    this.#map.triggerRepaint();
  }

  spot(k: number) {
    this.#spotted = k;
    this.#map.triggerRepaint();
  }

  fade(id: string, value: Fade) {
    this.#fades.set(id, value);
    this.#map.triggerRepaint();
  }

  #setup(gl: WebGL2RenderingContext) {
    if (this.#gl === gl) {
      return;
    }
    this.#gl = gl;
    this.#textures.clear();
    this.#fill = compile(gl, FILL, [
      "u_matrix",
      "u_rect",
      "u_uv",
      "u_index",
      "u_palette",
      "u_opacity",
    ]);
    this.#edge = compile(gl, EDGE, [
      "u_matrix",
      "u_rect",
      "u_uv",
      "u_index",
      "u_focus",
      "u_ink",
      "u_width",
    ]);
    this.#quad = gl.createVertexArray();
    gl.bindVertexArray(this.#quad);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]),
      gl.STATIC_DRAW,
    );
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);
  }

  #texture(gl: WebGL2RenderingContext, name: string, tile: Raster) {
    let held = this.#textures.get(name);
    if (held == null) {
      const index = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, index);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      const isWide = tile.index instanceof Uint16Array;
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        isWide ? gl.R16UI : gl.R8UI,
        SIZE,
        SIZE,
        0,
        gl.RED_INTEGER,
        isWide ? gl.UNSIGNED_SHORT : gl.UNSIGNED_BYTE,
        tile.index,
      );
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      held = {
        index,
        palette: gl.createTexture(),
        tinted: 0,
        spotted: -1,
        focus: -1,
        used: 0,
      };
      this.#textures.set(name, held);
      if (this.#textures.size > HELD) {
        const stale = [...this.#textures]
          .filter(([, texture]) => texture.used < this.#frame)
          .toSorted(([, a], [, b]) => a.used - b.used)
          .slice(0, this.#textures.size - HELD);
        for (const [old, texture] of stale) {
          gl.deleteTexture(texture.index);
          gl.deleteTexture(texture.palette);
          this.#textures.delete(old);
        }
      }
    }
    if (held.tinted !== this.#version) {
      held.tinted = this.#version;
      const count = tile.ids.length;
      const width = Math.max(1, Math.min(ROW, count));
      const height = Math.max(1, Math.ceil(count / ROW));
      const colors = new Uint8Array(width * height * 4);
      for (const [i, k] of tile.ids.entries()) {
        const packed = this.#tints.get(k) ?? 0;
        const alpha = (packed >>> 24) & 255;
        colors[i * 4] = Math.round(((packed & 255) * alpha) / 255);
        colors[i * 4 + 1] = Math.round((((packed >>> 8) & 255) * alpha) / 255);
        colors[i * 4 + 2] = Math.round((((packed >>> 16) & 255) * alpha) / 255);
        colors[i * 4 + 3] = alpha;
      }
      gl.bindTexture(gl.TEXTURE_2D, held.palette);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA8,
        width,
        height,
        0,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        colors,
      );
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    }
    if (held.spotted !== this.#spotted) {
      held.spotted = this.#spotted;
      held.focus = this.#spotted === 0 ? -1 : tile.ids.indexOf(this.#spotted);
    }
    held.used = this.#frame;
    return held;
  }

  #cover(key: string) {
    const map = this.#map;
    const zoom = map.getZoom();
    const z = Math.min(
      TOP,
      Math.max(0, Math.round(zoom + Math.log2(map.getPixelRatio()))),
    );
    const bounds = map.getBounds();
    const n = 2 ** z;
    const mercator = (lat: number) => {
      const sin = Math.sin(
        (Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI) / 180,
      );
      return 0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI);
    };
    const clamp = (value: number) =>
      Math.min(n - 1, Math.max(0, Math.floor(value * n)));
    const west = clamp((bounds.getWest() + 180) / 360);
    const east = clamp((bounds.getEast() + 180) / 360);
    const north = clamp(mercator(bounds.getNorth()));
    const south = clamp(mercator(bounds.getSouth()));
    const archive = this.#open(key);
    const draws: Array<{
      name: string;
      tile: Raster;
      rect: [number, number, number, number];
      uv: [number, number, number, number];
    }> = [];
    const ready = (tz: number, tx: number, ty: number) => {
      const name = `${key}/${tz}/${tx}/${ty}`;
      return loaded.has(name) ? { name, tile: loaded.get(name) ?? null } : null;
    };
    const want = (tz: number, tx: number, ty: number) => {
      const found = ready(tz, tx, ty);
      if (found == null) {
        this.#ask(archive, `${key}/${tz}/${tx}/${ty}`, tz, tx, ty);
      }
      return found;
    };
    for (let x = west; x <= east; x += 1) {
      for (let y = north; y <= south; y += 1) {
        const rect: [number, number, number, number] = [
          x / n,
          y / n,
          (x + 1) / n,
          (y + 1) / n,
        ];
        const own = want(z, x, y);
        if (own != null) {
          if (own.tile != null) {
            draws.push({
              name: own.name,
              tile: own.tile,
              rect,
              uv: [0, 0, 1, 1],
            });
          }
          continue;
        }
        if (z < TOP) {
          const children = [0, 1, 2, 3].map((i) => ({
            cx: 2 * x + (i % 2),
            cy: 2 * y + Math.floor(i / 2),
          }));
          const found = children.map(({ cx, cy }) => ready(z + 1, cx, cy));
          if (found.every((entry) => entry != null)) {
            for (const [i, entry] of found.entries()) {
              const { cx, cy } = children[i] ?? { cx: 0, cy: 0 };
              if (entry?.tile != null) {
                draws.push({
                  name: entry.name,
                  tile: entry.tile,
                  rect: [
                    cx / (2 * n),
                    cy / (2 * n),
                    (cx + 1) / (2 * n),
                    (cy + 1) / (2 * n),
                  ],
                  uv: [0, 0, 1, 1],
                });
              }
            }
            continue;
          }
        }
        for (let up = z - 1; up >= 0; up -= 1) {
          const scale = 2 ** (z - up);
          const ax = Math.floor(x / scale);
          const ay = Math.floor(y / scale);
          const parent = ready(up, ax, ay);
          if (parent == null) {
            continue;
          }
          if (parent.tile != null) {
            const u = x / scale - ax;
            const v = y / scale - ay;
            draws.push({
              name: parent.name,
              tile: parent.tile,
              rect,
              uv: [u, v, u + 1 / scale, v + 1 / scale],
            });
          }
          break;
        }
      }
    }
    return draws;
  }

  #ask(archive: PMTiles, name: string, z: number, x: number, y: number) {
    if (this.#asking.has(name) || this.#waiting.has(name)) {
      return;
    }
    this.#asking.add(name);
    const label = `Map mosaic tile ${name}`;
    const map = this.#map;
    void fetchTile(archive, name, z, x, y)
      .then(() => this.#retries.worked(label))
      .catch((error: unknown) => {
        this.#waiting.add(name);
        this.#retries.failed(label, error, () => {
          this.#waiting.delete(name);
          map.triggerRepaint();
          map.once("render", () => {
            if (!this.#asking.has(name)) {
              this.#retries.forget(label);
            }
          });
        });
      })
      .finally(() => {
        this.#asking.delete(name);
        map.triggerRepaint();
      });
  }

  #render(key: string, isFocus: boolean, matrix: Iterable<number>) {
    const gl = this.#gl;
    const program = isFocus ? this.#edge : this.#fill;
    if (gl == null || program == null || this.#quad == null) {
      return;
    }
    const opacity = Math.min(
      1,
      Math.max(
        0,
        level(this.#fades.get(`ids:${key}`) ?? 1, this.#map.getZoom()),
      ),
    );
    if (
      opacity <= 0 ||
      (isFocus && this.#spotted === 0) ||
      this.#tints.size === 0
    ) {
      return;
    }
    this.#frame += 1;
    const draws = this.#cover(key);
    const { places } = program;
    gl.useProgram(program.program);
    gl.bindVertexArray(this.#quad);
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.STENCIL_TEST);
    gl.disable(gl.CULL_FACE);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.uniformMatrix4fv(places.u_matrix ?? null, false, matrix);
    gl.uniform1i(places.u_index ?? null, 0);
    if (isFocus) {
      const [r, g, b, a] = this.#ink;
      gl.uniform4f(places.u_ink ?? null, r * a, g * a, b * a, a);
      gl.uniform1f(
        places.u_width ?? null,
        Math.max(2, Math.round(1.5 * this.#map.getPixelRatio())),
      );
    } else {
      gl.uniform1i(places.u_palette ?? null, 1);
      gl.uniform1f(places.u_opacity ?? null, opacity);
    }
    for (const draw of draws) {
      const texture = this.#texture(gl, draw.name, draw.tile);
      if (isFocus && texture.focus === -1) {
        continue;
      }
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texture.index);
      if (isFocus) {
        gl.uniform1i(places.u_focus ?? null, texture.focus);
      } else {
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, texture.palette);
      }
      gl.uniform4f(places.u_rect ?? null, ...draw.rect);
      gl.uniform4f(places.u_uv ?? null, ...draw.uv);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
    gl.bindVertexArray(null);
  }
}
