import type { InferRouterOutputs } from "@orpc/server";
import type { Router } from "#lib/server/rpc/router.js";

type Outputs = InferRouterOutputs<Router>;
type Raw = Outputs["contests"]["mosaic"];

export type Sheet = Outputs["contests"]["atlas"];
export type Shell = Omit<Sheet, "places">;
export type Ask = {
  unit?: string | null;
  level?: string | null;
  vs?: string | null;
  group?: string | null;
  whole?: string | null;
};
export type Mosaic = {
  key: string;
  layers: Raw["layers"];
  part: Array<number | null>;
  layer: Array<number>;
  set: Sheet;
};

const KEYS = ["unit", "level", "vs", "group", "whole"] as const;
const sheets = new Map<string, Promise<Sheet>>();
const mosaics = new Map<string, Promise<Mosaic | null>>();

function query(ask: Ask) {
  const params = new URLSearchParams();
  for (const key of KEYS) {
    const value = ask[key];
    if (value != null && value !== "") {
      params.set(key, value);
    }
  }
  return params.size === 0 ? "" : `?${params}`;
}

export function sheetUrl(contest: string, ask: Ask) {
  return `/api/v1/contests/${contest}/atlas${query(ask)}`;
}

export function mosaicUrl(contest: string, ask: Ask) {
  return `/api/v1/contests/${contest}/mosaic${query({ vs: ask.vs, group: ask.group })}`;
}

async function get<T>(url: string, signal?: AbortSignal) {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new Error(`${response.status} ${url}`);
  }
  return (await response.json()) as T;
}

export function shell(sheet: Sheet): Shell {
  const { places: _, ...rest } = sheet;
  return rest;
}

export function keep(contest: string, ask: Ask, sheet: Sheet) {
  sheets.set(sheetUrl(contest, ask), Promise.resolve(sheet));
}

export async function peek(contest: string, ask: Ask) {
  return sheets.get(sheetUrl(contest, ask)) ?? null;
}

export async function load(contest: string, ask: Ask) {
  const url = sheetUrl(contest, ask);
  let found = sheets.get(url);
  if (found == null) {
    found = get<Sheet>(url);
    sheets.set(url, found);
    found.catch(() => {
      if (sheets.get(url) === found) {
        sheets.delete(url);
      }
    });
    if (sheets.size > 64) {
      sheets.delete(sheets.keys().next().value ?? "");
    }
  }
  return found;
}

export async function loadMosaic(contest: string, ask: Ask, base: Sheet) {
  const url = mosaicUrl(contest, ask);
  let found = mosaics.get(url);
  if (found == null) {
    found = get<Raw>(url).then((raw) => decode(url, raw, base));
    mosaics.set(url, found);
    found.catch(() => {
      if (mosaics.get(url) === found) {
        mosaics.delete(url);
      }
    });
  }
  return found;
}

function decode(key: string, raw: Raw, base: Sheet): Mosaic | null {
  if (raw.layers.length === 0) {
    return null;
  }
  const k: Array<number> = [];
  let at = 0;
  for (const delta of raw.places.k) {
    at += delta;
    k.push(at);
  }
  const empty = k.map(() => null);
  const scale = (column: Array<number | null>) =>
    column.map((value) => (value == null ? null : value * 10));
  return {
    key,
    layers: raw.layers,
    part: raw.places.part,
    layer: raw.places.layer,
    set: {
      ...base,
      scope: { ...base.scope, name: "" },
      target: raw.target,
      legend: raw.legend,
      places: {
        k,
        name: raw.places.name,
        valid: raw.places.valid,
        x: empty,
        y: empty,
        leader: raw.places.leader,
        held: raw.places.held,
        prior: raw.places.prior.length > 0 ? raw.places.prior : empty,
        shares: raw.places.shares.map((column) => scale(column)),
        before:
          raw.places.before.length > 0
            ? raw.places.before.map((column) => scale(column))
            : raw.legend.map(() => empty),
        previous:
          raw.places.previous != null && raw.places.previous.length > 0
            ? raw.places.previous
            : raw.places.valid,
        redrawn: empty,
      },
    },
  };
}
