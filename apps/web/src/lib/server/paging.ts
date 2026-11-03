import { error } from "@sveltejs/kit";

type Json =
  | null
  | boolean
  | number
  | string
  | Array<Json>
  | { [key: string]: Json };

type Cut = { total: number; count: number };

const BUDGET = 262_144;
const FLOOR = 2048;
const TEXT = 131_072;

const escape = (key: string) => key.replaceAll("~", "~0").replaceAll("/", "~1");

function cap(
  value: Json,
  pointer: string,
  limit: number,
  start = 0,
): { value: Json; size: number; cuts: Map<string, Cut> } {
  const cuts = new Map<string, Cut>();
  if (Array.isArray(value)) {
    const kept: Array<Json> = [];
    let size = 2;
    for (const [index, item] of value.entries()) {
      const capped = cap(
        item,
        `${pointer}/${start + index}`,
        Math.max(FLOOR, limit / 4),
      );
      if (kept.length > 0 && size + capped.size + 1 > limit) {
        cuts.set(pointer, {
          total: start + value.length,
          count: start + index,
        });
        break;
      }
      kept.push(capped.value);
      size += capped.size + 1;
      for (const [path, cut] of capped.cuts) {
        cuts.set(path, cut);
      }
    }
    return { value: kept, size, cuts };
  }
  if (value != null && typeof value === "object") {
    const kept: Record<string, Json> = {};
    let size = 2;
    for (const [key, item] of Object.entries(value)) {
      const capped = cap(item, `${pointer}/${escape(key)}`, limit);
      kept[key] = capped.value;
      size += JSON.stringify(key).length + capped.size + 2;
      for (const [path, cut] of capped.cuts) {
        cuts.set(path, cut);
      }
    }
    return { value: kept, size, cuts };
  }
  return { value, size: JSON.stringify(value).length, cuts };
}

function fit(value: Json, pointer: string, start = 0) {
  let capped = cap(value, pointer, Infinity, start);
  for (let limit = BUDGET; capped.size > BUDGET && limit >= FLOOR; limit /= 2) {
    capped = cap(value, pointer, limit, start);
  }
  return capped;
}

function find(value: Json, pointer: string) {
  let found: Json | undefined = value;
  for (const token of pointer.split("/").slice(1)) {
    const key = token.replaceAll("~1", "/").replaceAll("~0", "~");
    if (Array.isArray(found)) {
      found = /^\d+$/u.test(key) ? found[Number(key)] : undefined;
    } else if (found != null && typeof found === "object") {
      found = Object.hasOwn(found, key) ? found[key] : undefined;
    } else {
      found = undefined;
    }
  }
  return found;
}

export function jsonPage(data: unknown, url: URL): Json {
  const text = JSON.stringify(data);
  const value = JSON.parse(text) as Json;
  const next = (pointer: string, offset: number) => {
    const target = new URL(url);
    target.searchParams.set("list", pointer);
    target.searchParams.set("offset", String(offset));
    return target.href;
  };
  const paging = (cuts: Map<string, Cut>) =>
    Object.fromEntries(
      cuts
        .entries()
        .map(([pointer, cut]) => [
          pointer,
          { ...cut, next: next(pointer, cut.count) },
        ]),
    );
  const list = url.searchParams.get("list");
  if (list == null) {
    const { value: kept, cuts } = fit(value, "");
    if (
      cuts.size === 0 ||
      kept == null ||
      typeof kept !== "object" ||
      Array.isArray(kept)
    ) {
      return kept;
    }
    return { ...kept, paging: paging(cuts) };
  }
  const found = list.startsWith("/") ? find(value, list) : undefined;
  if (!Array.isArray(found)) {
    error(404);
  }
  const offset = Math.max(
    0,
    Math.trunc(Number(url.searchParams.get("offset"))) || 0,
  );
  const { value: items, cuts } = fit(found.slice(offset), list, offset);
  const own = cuts.get(list);
  cuts.delete(list);
  return {
    list,
    total: found.length,
    offset,
    items,
    next: own == null ? null : next(list, own.count),
    paging: paging(cuts),
  };
}

function split(block: string, room: number) {
  const lines = block.split("\n");
  const chunks: Array<string> = [];
  let header: Array<string> = [];
  let chunk: Array<string> = [];
  let size = 0;
  let budget = room;
  for (const [index, line] of lines.entries()) {
    const isRow = line.startsWith("|");
    if (!isRow) {
      header = [];
    } else if (
      header.length === 0 &&
      !(lines[index - 1] ?? "").startsWith("|") &&
      /^\|[\s:|-]+\|$/u.test(lines[index + 1] ?? "")
    ) {
      header = [line, lines[index + 1] ?? ""];
    }
    if (
      chunk.length > 0 &&
      size + line.length + 1 > budget &&
      line !== header[1]
    ) {
      chunks.push(chunk.join("\n"));
      chunk = isRow && line !== header[0] ? [...header] : [];
      size = chunk.join("\n").length;
      budget = TEXT;
    }
    chunk.push(line);
    size += line.length + 1;
  }
  chunks.push(chunk.join("\n"));
  return chunks;
}

export function markdownPages(blocks: Array<string>) {
  const pages: Array<Array<string>> = [];
  let size = 0;
  for (const block of blocks) {
    const page = pages.at(-1);
    const room = page == null ? 0 : TEXT - size - 2;
    if (page != null && block.length <= room) {
      page.push(block);
      size += block.length + 2;
    } else if (block.length <= TEXT) {
      pages.push([block]);
      size = block.length;
    } else {
      const isJoined = page != null && room >= FLOOR;
      const [first = "", ...rest] = split(block, isJoined ? room : TEXT);
      if (isJoined) {
        page.push(first);
      } else {
        pages.push([first]);
      }
      size = isJoined ? size + first.length + 2 : first.length;
      for (const chunk of rest) {
        pages.push([chunk]);
        size = chunk.length;
      }
    }
  }
  return pages.map((page) => page.join("\n\n"));
}
