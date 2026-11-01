import type { Reroute, Transport } from "@sveltejs/kit/hooks";
import { READ_ONLY } from "$app/env/public";
import { deLocalizeUrl } from "#lib/paraglide/runtime.js";

const CLOSED =
  /^\/(?:edit|proposals|review|workbench|uploads|settings\/keys|lab\/contrib)(?:\/|$)/u;

export const reroute: Reroute = (request) => {
  const { pathname } = deLocalizeUrl(request.url);
  return READ_ONLY && CLOSED.test(pathname) ? "/closed" : pathname;
};

const LONG = 64;

const sparse = {
  encode: (value: unknown) => {
    if (!Array.isArray(value) || value.length < LONG) {
      return false;
    }
    const items: Array<unknown> = value;
    for (const fill of [0, null]) {
      const indices: Array<number> = [];
      const kept: Array<unknown> = [];
      for (const [index, item] of items.entries()) {
        if (item !== fill) {
          indices.push(index);
          kept.push(item);
          if (indices.length > items.length / 4) {
            break;
          }
        }
      }
      if (indices.length <= items.length / 4) {
        return [items.length, fill, indices, kept];
      }
    }
    return false;
  },
  decode: ([length, fill, indices, kept]: [
    number,
    0 | null,
    Array<number>,
    Array<unknown>,
  ]) => {
    const spots = new Map(indices.map((index, at) => [index, at]));
    return Array.from({ length }, (_, index) => {
      const at = spots.get(index);
      return at == null ? fill : kept[at];
    });
  },
};

const runs = {
  encode: (value: unknown) => {
    if (!Array.isArray(value) || value.length < LONG) {
      return false;
    }
    const items: Array<unknown> = value;
    const bounds: Array<number> = [];
    let last: number | null = null;
    for (const item of items) {
      if (
        typeof item !== "number" ||
        !Number.isSafeInteger(item) ||
        (last != null && item <= last)
      ) {
        return false;
      }
      if (last != null && item === last + 1) {
        bounds[bounds.length - 1] = item;
      } else {
        bounds.push(item, item);
        if (bounds.length > items.length / 4) {
          return false;
        }
      }
      last = item;
    }
    return bounds;
  },
  decode: (bounds: Array<number>) =>
    bounds.flatMap((start, at) =>
      at % 2 === 0
        ? Array.from(
            { length: (bounds[at + 1] ?? start) - start + 1 },
            (_, step) => start + step,
          )
        : [],
    ),
};

function isPlain(value: unknown): value is Record<string, unknown> {
  return (
    value != null &&
    typeof value === "object" &&
    Object.getPrototypeOf(value) === Object.prototype &&
    Object.getOwnPropertySymbols(value).length === 0
  );
}

function isFlat(value: unknown, seen: Set<unknown>): boolean {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return true;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) && !Object.is(value, -0);
  }
  if (
    !Array.isArray(value) ||
    value.length >= LONG ||
    seen.has(value) ||
    Object.keys(value).length !== value.length
  ) {
    return false;
  }
  seen.add(value);
  const items: Array<unknown> = value;
  return items.every((item) => isFlat(item, seen));
}

const columns = {
  encode: (value: unknown) => {
    if (!Array.isArray(value) || value.length < LONG) {
      return false;
    }
    const rows: Array<unknown> = value;
    const first = rows[0];
    if (!isPlain(first)) {
      return false;
    }
    const keys = Object.keys(first);
    if (keys.length === 0) {
      return false;
    }
    const seen = new Set<unknown>();
    const cells = keys.map((): Array<unknown> => []);
    for (const row of rows) {
      if (!isPlain(row) || seen.has(row)) {
        return false;
      }
      seen.add(row);
      const own = Object.keys(row);
      if (
        own.length !== keys.length ||
        own.some((key, at) => key !== keys[at])
      ) {
        return false;
      }
      for (const [at, key] of keys.entries()) {
        cells[at]?.push(row[key]);
      }
    }
    const packed = new Set<unknown>();
    return [
      keys,
      cells.map((cell) =>
        cell.every((item) => isFlat(item, packed))
          ? JSON.stringify(cell)
          : cell,
      ),
    ];
  },
  decode: ([keys, cells]: [Array<string>, Array<string | Array<unknown>>]) => {
    const lists = cells.map((cell): Array<unknown> => {
      if (typeof cell !== "string") {
        return cell;
      }
      const parsed: unknown = JSON.parse(cell);
      return Array.isArray(parsed) ? parsed : [];
    });
    return Array.from({ length: lists[0]?.length ?? 0 }, (_, index) =>
      Object.fromEntries(keys.map((key, at) => [key, lists[at]?.[index]])),
    );
  },
};

export const transport = { sparse, runs, columns } satisfies Transport;
