import { version as build, building } from "$app/env";
import { os as base, Procedure } from "@orpc/server";
import { RESP_TYPES } from "@redis/client";
import { sql } from "drizzle-orm";
import { Buffer } from "node:buffer";
import { promisify } from "node:util";
import { deserialize, serialize } from "node:v8";
import { zstdCompress, zstdDecompress } from "node:zlib";
import { db } from "#lib/server/db/index.js";
import { redis } from "#lib/server/redis.js";

const LIMIT = 20_000;
const BUDGET = 1024 ** 3;
const LARGEST = 64 * 1024 ** 2;
const LIFETIME = 30 * 86_400;
const CHECK = 5000;

const pack = promisify(zstdCompress);
const unpack = promisify(zstdDecompress);

type Entry = {
  value: Promise<unknown>;
  version: string;
  size: number;
  isSettled: boolean;
  isRenewing: boolean;
};

const entries = new Map<string, Entry>();
const names = new WeakMap<object, string>();
const state: {
  version: string;
  checked: number;
  checking: Promise<void> | null;
  size: number;
} = { version: "", checked: 0, checking: null, size: 0 };

let shared: ReturnType<typeof connect> | null = null;

function connect() {
  return redis().withTypeMapping({ [RESP_TYPES.BLOB_STRING]: Buffer });
}

function client() {
  if (building) {
    return null;
  }
  shared ??= connect();
  redis();
  return shared.isReady ? shared : null;
}

client();

async function recall(key: string) {
  const reader = client();
  if (reader == null) {
    return null;
  }
  try {
    const found = await reader.get(`c:${build}:${key}`);
    if (found == null) {
      return null;
    }
    const payload = await unpack(found);
    const parsed: unknown = deserialize(payload);
    if (
      typeof parsed === "object" &&
      parsed != null &&
      "version" in parsed &&
      typeof parsed.version === "string" &&
      "value" in parsed
    ) {
      return {
        version: parsed.version,
        value: parsed.value,
        size: payload.length,
      };
    }
    return null;
  } catch {
    return null;
  }
}

function keep(key: string, version: string, value: unknown) {
  try {
    const payload = serialize({ version, value });
    const writer = client();
    if (writer != null && payload.length <= LARGEST) {
      void pack(payload)
        .then(async (packed) =>
          writer.set(`c:${build}:${key}`, packed, { EX: LIFETIME }),
        )
        .catch(() => null);
    }
    return payload.length;
  } catch {
    return 0;
  }
}

async function refresh() {
  try {
    const result = await db.execute<{ version: string }>(sql`
      select concat_ws(':', current_date, count(*) filter (where status = 'published'), max(published_at),
        count(*) filter (where status = 'rejected'), (select built_at from coverage_build)) as version
      from changeset
    `);
    state.version = result.rows[0]?.version ?? "";
    state.checked = Date.now();
  } finally {
    state.checking = null;
  }
}

function drop(key: string) {
  const entry = entries.get(key);
  if (entry != null) {
    state.size -= entry.size;
    entries.delete(key);
  }
}

function evict() {
  for (const oldest of entries.keys()) {
    if (entries.size <= LIMIT && state.size <= BUDGET) {
      break;
    }
    drop(oldest);
  }
}

function put(key: string, entry: Entry) {
  drop(key);
  entries.set(key, entry);
  state.size += entry.size;
  evict();
}

function store<T>(key: string, compute: () => Promise<T>, isStrict: boolean) {
  const entry: Entry = {
    value: Promise.resolve(),
    version: state.version,
    size: 0,
    isSettled: false,
    isRenewing: false,
  };
  let recalled = 0;
  entry.value = (async () => {
    const found = await recall(key);
    if (found != null && (found.version === entry.version || !isStrict)) {
      entry.version = found.version;
      recalled = found.size;
      return found.value;
    }
    return compute();
  })();
  void (async () => {
    try {
      const value = await entry.value;
      const isFresh = entry.version === state.version;
      const size =
        isFresh && recalled === 0 ? keep(key, entry.version, value) : recalled;
      entry.isSettled = true;
      if (entries.get(key) !== entry) {
        return;
      }
      entry.size = size;
      state.size += size;
      evict();
      if (!isFresh) {
        renew(key, entry, compute);
      }
    } catch {
      if (entries.get(key) === entry) {
        drop(key);
      }
    }
  })();
  return entry;
}

const RENEWING = 4;
const waiting: Array<() => Promise<void>> = [];
let renewing = 0;

async function drain() {
  while (renewing < RENEWING) {
    const task = waiting.shift();
    if (task == null) {
      return;
    }
    renewing++;
    try {
      await task();
    } finally {
      renewing--;
    }
  }
}

function renew(key: string, hit: Entry, compute: () => Promise<unknown>) {
  hit.isRenewing = true;
  waiting.push(async () => {
    const version = state.version;
    try {
      const value = await compute();
      const next: Entry = {
        value: Promise.resolve(value),
        version,
        size: keep(key, version, value),
        isSettled: true,
        isRenewing: false,
      };
      if (entries.get(key) === hit) {
        put(key, next);
      }
    } catch {
      hit.isRenewing = false;
    }
  });
  void drain();
}

export async function remember<T>(
  key: string,
  compute: () => Promise<T>,
  isStrict = false,
) {
  if (isStrict || Date.now() - state.checked >= CHECK) {
    state.checking ??= refresh();
    await state.checking;
  }
  const hit = entries.get(key);
  if (hit != null) {
    entries.delete(key);
    entries.set(key, hit);
    if (isStrict && hit.version !== state.version) {
      const next = store(key, compute, true);
      put(key, next);
      return next.value as Promise<T>;
    }
    if (hit.version !== state.version && hit.isSettled && !hit.isRenewing) {
      renew(key, hit, compute);
    }
    return hit.value as Promise<T>;
  }
  const entry = store(key, compute, isStrict);
  put(key, entry);
  return entry.value as Promise<T>;
}

export function label(
  router: Record<string, unknown>,
  path: Array<string> = [],
) {
  for (const [key, value] of Object.entries<unknown>(router)) {
    if (value instanceof Procedure) {
      names.set(value, [...path, key].join("."));
    } else if (typeof value === "object" && value != null) {
      label(Object.fromEntries(Object.entries(value)), [...path, key]);
    }
  }
}

function cached(isStrict: boolean) {
  return base.use(async ({ next, procedure }, input) => {
    const name = names.get(procedure);
    if (name == null) {
      return next();
    }
    const output = await remember(
      `${name}:${JSON.stringify(input)}`,
      async () => {
        const result = await next();
        return result.output;
      },
      isStrict,
    );
    return { output, context: {} };
  });
}

export const os = cached(false);
export const strict = cached(true);
