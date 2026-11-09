import type pg from "pg";
import { AsyncLocalStorage } from "node:async_hooks";

export type Reads = {
  ids: Set<string>;
  keys: Set<string>;
  pending: Array<Promise<Array<string> | null>>;
  isWide: boolean;
};

export const reading = new AsyncLocalStorage<Reads>();

const WIDEST = 200_000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;
const UUIDS = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/giu;
const TABLES: Array<[RegExp, string]> = [
  [/\bcoverage_\w+/iu, "coverage"],
  [/\b(?:changeset|changeset_entity|proposal\w*)\b/iu, "changesets"],
  [/\btranslation\b/iu, "translation"],
];
const SCANNED = new Set([25, 114, 1009, 1043, 2950, 2951, 3802]);
const tables = new Map<string, Array<string>>();

export function fresh(): Reads {
  return { ids: new Set(), keys: new Set(), pending: [], isWide: false };
}

function add(reads: Reads, id: string) {
  reads.ids.add(id.toLowerCase());
  if (reads.ids.size <= WIDEST) {
    return;
  }
  reads.isWide = true;
  reads.ids.clear();
}

function scan(reads: Reads, value: unknown, isText: boolean) {
  if (reads.isWide) {
    return;
  }
  if (typeof value === "string") {
    if (value.length === 36) {
      if (UUID.test(value)) {
        add(reads, value);
      }
    } else if (isText && value.length > 36) {
      for (const [found] of value.matchAll(UUIDS)) {
        add(reads, found);
      }
    }
  } else if (Array.isArray(value)) {
    for (const item of value) {
      scan(reads, item, isText);
    }
  } else if (typeof value === "object" && value != null) {
    for (const item of Object.values(value)) {
      scan(reads, item, isText);
    }
  }
}

function keyed(text: string) {
  let found = tables.get(text);
  if (found == null) {
    found = [];
    for (const [pattern, key] of TABLES) {
      if (pattern.test(text)) {
        found.push(key);
      }
    }
    if (tables.size > 10_000) {
      tables.clear();
    }
    tables.set(text, found);
  }
  return found;
}

function record(reads: Reads, result: unknown) {
  if (Array.isArray(result)) {
    for (const part of result) {
      record(reads, part);
    }
    return;
  }
  if (
    typeof result !== "object" ||
    result == null ||
    !("fields" in result) ||
    !Array.isArray(result.fields) ||
    !("rows" in result) ||
    !Array.isArray(result.rows)
  ) {
    return;
  }
  const fields: Array<unknown> = result.fields;
  const columns: Array<[number, string]> = [];
  for (const [index, field] of fields.entries()) {
    if (
      typeof field === "object" &&
      field != null &&
      "dataTypeID" in field &&
      typeof field.dataTypeID === "number" &&
      SCANNED.has(field.dataTypeID) &&
      "name" in field &&
      typeof field.name === "string"
    ) {
      columns.push([index, field.name]);
    }
  }
  if (columns.length === 0) {
    return;
  }
  for (const row of result.rows) {
    if (typeof row !== "object" || row == null) {
      continue;
    }
    const isArray = Array.isArray(row);
    for (const [index, name] of columns) {
      scan(reads, Reflect.get(row, isArray ? index : name), false);
    }
    if (reads.isWide) {
      return;
    }
  }
}

function note(reads: Reads, config: unknown, values: unknown, result: unknown) {
  const text =
    typeof config === "string"
      ? config
      : typeof config === "object" &&
          config != null &&
          "text" in config &&
          typeof config.text === "string"
        ? config.text
        : "";
  for (const key of keyed(text)) {
    reads.keys.add(key);
  }
  const params =
    values ??
    (typeof config === "object" && config != null && "values" in config
      ? config.values
      : null);
  scan(reads, params, true);
  record(reads, result);
}

export function watch(pool: pg.Pool) {
  const query: (...args: Array<unknown>) => unknown = pool.query.bind(pool);
  const watched = (...args: Array<unknown>) => {
    const reads = reading.getStore();
    const pending = query(...args);
    if (reads == null || !(pending instanceof Promise)) {
      return pending;
    }
    const [config, values] = args;
    return pending.then((result: unknown) => {
      note(reads, config, values, result);
      return result;
    });
  };
  Object.assign(pool, { query: watched });
}
