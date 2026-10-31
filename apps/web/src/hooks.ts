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

export const transport = { sparse, runs } satisfies Transport;
