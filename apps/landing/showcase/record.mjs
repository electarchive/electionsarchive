import { Buffer } from "node:buffer";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { gunzipSync, gzipSync } from "node:zlib";
import { pages } from "./pages.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "../../..");
const web = path.join(root, "apps/web/node_modules");
const require = createRequire(
  realpathSync(path.join(web, "@sveltejs/kit/package.json")),
);
const { tileIdToZxy } = await import(
  path.join(realpathSync(path.join(web, "pmtiles")), "dist/esm/index.js")
);
const base = process.argv[2] ?? "http://localhost:5173";
const out = path.join(root, "apps/landing/public");
const shapes = path.join(root, "data/tiles");

if (!existsSync(path.join(here, "node_modules"))) {
  symlinkSync(web, path.join(here, "node_modules"));
}
const { unflatten } = await import(require.resolve("devalue"));

async function json(url, init) {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`${response.status} ${url}`);
  }
  return response.json();
}

const photos = new Set();
const frames = new Map();

let current = "";

function harvest(value, found) {
  const text = JSON.stringify(value);
  for (const match of text.matchAll(/\/photos\/[\da-f]+\.webp/g)) {
    photos.add(match[0]);
  }
  const names = new Set(
    Array.from(
      text.matchAll(/"([a-z]{2}\.[a-z_]+\/[\w-]+)"/g),
      (match) => match[1],
    ),
  );
  for (const name of names) {
    const at = `${current}|${name}`;
    frames.set(at, [...(frames.get(at) ?? []), ...found]);
  }
}

const lon = (x, z) => (x / 2 ** z) * 360 - 180;
const lat = (y, z) =>
  (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / 2 ** z))) * 180) / Math.PI;

function plainBoxes(value, found = []) {
  if (Array.isArray(value)) {
    for (const child of value) {
      plainBoxes(child, found);
    }
  } else if (value instanceof Map) {
    plainBoxes(value.values().toArray(), found);
  } else if (value != null && typeof value === "object") {
    const frames = Array.isArray(value.frames) ? value.frames : [];
    for (const frame of frames) {
      const box = [frame?.west, frame?.south, frame?.east, frame?.north];
      if (box.every((side) => typeof side === "number")) {
        found.push(box);
      }
    }
    for (const child of Object.values(value)) {
      plainBoxes(child, found);
    }
  }
  return found;
}

for (const showcase of pages) {
  const folder = path.join(out, "showcase", showcase.slug);
  current = showcase.slug;
  rmSync(path.join(folder, "views"), { recursive: true, force: true });
  rmSync(path.join(folder, "tiles"), { recursive: true, force: true });
  mkdirSync(path.join(folder, "views"), { recursive: true });
  const ids = { person: new Set(), party: new Set() };
  let within = null;
  for (const [index, view] of showcase.views.entries()) {
    const [route, search = ""] = view.split("?", 2);
    const payload = await json(
      `${base}/${route}/__data.json${search === "" ? "" : `?${search}`}`,
    );
    const nodes = payload.nodes.map((node) => {
      if (node?.type !== "data") {
        throw new Error(`${view}: ${JSON.stringify(node).slice(0, 200)}`);
      }
      return node.data;
    });
    const data = unflatten(nodes.at(-1));
    harvest(nodes, plainBoxes(data));
    const people = (value) => {
      if (Array.isArray(value)) {
        for (const child of value) {
          people(child);
        }
      } else if (value instanceof Map) {
        people(value.values().toArray());
      } else if (value != null && typeof value === "object") {
        for (const key of ["person", "party"]) {
          if (
            typeof value[key] === "string" &&
            /^[\da-f-]{36}$/.test(value[key])
          ) {
            ids[key].add(value[key]);
          }
        }
        for (const child of Object.values(value)) {
          people(child);
        }
      }
    };
    people(data);
    within = typeof data.within === "string" ? data.within : within;
    writeFileSync(
      path.join(folder, "views", `${index}.json`),
      JSON.stringify({ view, nodes }),
    );
  }
  const calls = [
    ...showcase.rpc,
    ...[...ids.person].slice(0, 40).map((id) => ["people/get", { id }]),
    ...[...ids.party]
      .slice(0, 40)
      .map((id) => ["parties/get", within == null ? { id } : { id, within }]),
  ];
  const rpc = {};
  for (const [procedure, input] of calls) {
    let body = null;
    try {
      body = await json(`${base}/rpc/${procedure}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ json: input }),
      });
    } catch (error) {
      console.warn(
        `skip ${procedure} ${JSON.stringify(input)}: ${error.message}`,
      );
    }
    if (body != null) {
      rpc[
        `${procedure} ${JSON.stringify(
          input,
          Object.keys(input).toSorted((a, b) => a.localeCompare(b, "en")),
        )}`
      ] = body;
      harvest(body, plainBoxes(body.json));
    }
  }
  writeFileSync(path.join(folder, "rpc.json"), JSON.stringify(rpc));
  console.warn(
    `${showcase.slug}: ${showcase.views.length} views, ${Object.keys(rpc).length} calls`,
  );
}

mkdirSync(path.join(out, "photos"), { recursive: true });
for (const photo of photos) {
  const target = path.join(out, photo);
  if (!existsSync(target)) {
    const response = await fetch(`${base}${photo}`);
    if (response.ok) {
      writeFileSync(target, Buffer.from(await response.arrayBuffer()));
    }
  }
}
console.warn(`${photos.size} photos`);

function varint(bytes, at) {
  let value = 0;
  let shift = 1;
  for (;;) {
    const byte = bytes[at.i++];
    value += (byte % 128) * shift;
    if (byte < 0x80) {
      return value;
    }
    shift *= 128;
  }
}

function readDirectory(bytes) {
  const at = { i: 0 };
  const count = varint(bytes, at);
  const entries = Array.from({ length: count }, () => ({
    tileId: 0,
    runLength: 0,
    length: 0,
    offset: 0,
  }));
  let last = 0;
  for (const entry of entries) {
    last += varint(bytes, at);
    entry.tileId = last;
  }
  for (const entry of entries) {
    entry.runLength = varint(bytes, at);
  }
  for (const entry of entries) {
    entry.length = varint(bytes, at);
  }
  for (const [index, entry] of entries.entries()) {
    const value = varint(bytes, at);
    entry.offset =
      value === 0 && index > 0
        ? entries[index - 1].offset + entries[index - 1].length
        : value - 1;
  }
  return entries;
}

function writeVarint(list, value) {
  while (value >= 0x80) {
    list.push((value % 128) | 0x80);
    value = Math.floor(value / 128);
  }
  list.push(value);
}

function writeDirectory(entries) {
  const list = [];
  writeVarint(list, entries.length);
  let last = 0;
  for (const entry of entries) {
    writeVarint(list, entry.tileId - last);
    last = entry.tileId;
  }
  for (const entry of entries) {
    writeVarint(list, entry.runLength);
  }
  for (const entry of entries) {
    writeVarint(list, entry.length);
  }
  for (const [index, entry] of entries.entries()) {
    const previous = entries[index - 1];
    writeVarint(
      list,
      index > 0 && entry.offset === previous.offset + previous.length
        ? 0
        : entry.offset + 1,
    );
  }
  return gzipSync(Buffer.from(list));
}

for (const [at, list] of frames) {
  const [slug, name] = at.split("|", 2);
  const file = path.join(shapes, `${name}.pmtiles`);
  if (!existsSync(file)) {
    continue;
  }
  const source = readFileSync(file);
  const u64 = (offset) => Number(source.readBigUInt64LE(offset));
  const unzip = (bytes) => (source[97] === 2 ? gunzipSync(bytes) : bytes);
  const entries = [];
  const visit = (offset, length) => {
    const listed = readDirectory(
      unzip(source.subarray(offset, offset + length)),
    );
    for (const entry of listed) {
      if (entry.runLength === 0) {
        visit(u64(40) + entry.offset, entry.length);
      } else {
        entries.push(entry);
      }
    }
  };
  visit(u64(8), u64(16));
  const boxesFor = list.length === 0 ? [[-180, -85, 180, 85]] : list;
  const reach = boxesFor.map(([west, south, east, north]) => {
    const width = Math.max(east - west, (north - south) * 1.3, 0.01);
    const fit = Math.floor(Math.log2(360 / width)) + 2;
    const padX = (east - west) * 0.4;
    const padY = (north - south) * 0.4;
    return {
      west: west - padX,
      south: south - padY,
      east: east + padX,
      north: north + padY,
      min: fit - 3,
      max: fit + 1,
    };
  });
  const kept = [];
  for (const entry of entries) {
    for (let step = 0; step < entry.runLength; step++) {
      const tileId = entry.tileId + step;
      const [z, x, y] = tileIdToZxy(tileId);
      const fits = reach.some(
        (box) =>
          z >= box.min &&
          z <= box.max &&
          lon(x + 1, z) >= box.west &&
          lon(x, z) <= box.east &&
          lat(y, z) >= box.south &&
          lat(y + 1, z) <= box.north,
      );
      if (fits) {
        kept.push({ tileId, z, offset: entry.offset, length: entry.length });
      }
    }
  }
  kept.sort((a, b) => a.tileId - b.tileId);
  const contents = new Map();
  const chunks = [];
  let size = 0;
  const written = [];
  for (const tile of kept) {
    const key = `${tile.offset}:${tile.length}`;
    let offset = contents.get(key);
    if (offset == null) {
      offset = size;
      contents.set(key, offset);
      const start = u64(56) + tile.offset;
      chunks.push(source.subarray(start, start + tile.length));
      size += tile.length;
    }
    const previous = written.at(-1);
    if (
      previous != null &&
      previous.offset === offset &&
      previous.tileId + previous.runLength === tile.tileId
    ) {
      previous.runLength += 1;
    } else {
      written.push({
        tileId: tile.tileId,
        runLength: 1,
        length: tile.length,
        offset,
      });
    }
  }
  const metadata = source.subarray(u64(24), u64(24) + u64(32));
  let rootDirectory = writeDirectory(written);
  let leaves = Buffer.alloc(0);
  if (127 + rootDirectory.length > 16_384) {
    const parts = [];
    const pointers = [];
    let at = 0;
    for (let start = 0; start < written.length; start += 4096) {
      const leaf = writeDirectory(written.slice(start, start + 4096));
      pointers.push({
        tileId: written[start].tileId,
        runLength: 0,
        length: leaf.length,
        offset: at,
      });
      parts.push(leaf);
      at += leaf.length;
    }
    rootDirectory = writeDirectory(pointers);
    leaves = Buffer.concat(parts);
  }
  const header = Buffer.from(source.subarray(0, 127));
  const rootOffset = 127;
  const metadataOffset = rootOffset + rootDirectory.length;
  const leafOffset = metadataOffset + metadata.length;
  const dataOffset = leafOffset + leaves.length;
  for (const [offset, value] of [
    [8, rootOffset],
    [16, rootDirectory.length],
    [24, metadataOffset],
    [32, metadata.length],
    [40, leafOffset],
    [48, leaves.length],
    [56, dataOffset],
    [64, size],
    [72, kept.length],
    [80, written.length],
    [88, contents.size],
  ]) {
    header.writeBigUInt64LE(BigInt(value), offset);
  }
  header[97] = 2;
  header[101] = Math.max(...kept.map((tile) => tile.z), source[100]);
  const target = path.join(out, "showcase", slug, "tiles", `${name}.pmtiles`);
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(
    target,
    Buffer.concat([
      header,
      rootDirectory,
      source[97] === 2 ? metadata : gzipSync(metadata),
      leaves,
      ...chunks,
    ]),
  );
  console.warn(
    `${slug} ${name}: ${kept.length} tiles, ${(size / 1024).toFixed(0)} KB`,
  );
}
