import { version as build, building } from "$app/env";
import { PAGES_DATABASE_URL } from "$app/env/private";
import { os as base, Procedure } from "@orpc/server";
import { RESP_TYPES } from "@redis/client";
import { sql } from "drizzle-orm";
import { AsyncLocalStorage } from "node:async_hooks";
import { Buffer } from "node:buffer";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";
import { deserialize, serialize } from "node:v8";
import { zstdCompress, zstdDecompress } from "node:zlib";
import pg from "pg";
import { codeVersion, procedureVersions } from "virtual:code-version";
import { PROJECT } from "#lib/constants.js";
import { baseLocale, getLocale } from "#lib/paraglide/runtime.js";
import { db, pagesPoolMax } from "#lib/server/db/index.js";
import { localizeNames } from "#lib/server/names.js";
import { redis } from "#lib/server/redis.js";

const LIMIT = 20_000;
const BUDGET = 1024 ** 3;
const LARGEST = 64 * 1024 ** 2;
const LIFETIME = 30 * 86_400;
const CHECK = 5000;
const LOCK = 75_000;
const PATIENCE = 70_000;
const GRACE = 4;
const UNDATED = "-undated";
const GENERATION = codeVersion ?? build;
const VERSIONS = new Map<string, string>();
const parsed: unknown = JSON.parse(procedureVersions ?? "{}");
for (const [name, version] of Object.entries<unknown>(
  typeof parsed === "object" && parsed != null ? { ...parsed } : {},
)) {
  if (typeof version === "string") {
    VERSIONS.set(name, version);
  }
}

const pack = promisify(zstdCompress);
const unpack = promisify(zstdDecompress);

type Entry = {
  value: Promise<unknown>;
  version: string;
  size: number;
  isSettled: boolean;
  isRenewing: boolean;
};

export const refreshing = new AsyncLocalStorage<boolean>();

export function dataVersion() {
  return `${state.version}:${state.today}`;
}

const entries = new Map<string, Entry>();
const names = new WeakMap<object, string>();
const state: {
  version: string;
  today: string;
  checked: number;
  checking: Promise<void> | null;
  size: number;
} = { version: "", today: "", checked: 0, checking: null, size: 0 };

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

let shelved: Promise<pg.Pool | null> | null = null;

async function open() {
  const pool = new pg.Pool({
    connectionString: PAGES_DATABASE_URL,
    max: pagesPoolMax,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 2000,
    statement_timeout: 10_000,
    application_name: PROJECT,
  });
  pool.on("error", (error) => {
    console.error("An idle cache connection failed:", error);
  });
  await pool.query(`
    create table if not exists entry (
      slot text primary key,
      version text not null,
      body bytea not null,
      stored_at timestamptz not null default now()
    );
    alter table entry alter column body set storage external;
  `);
  return pool;
}

async function shelf() {
  if (building || PAGES_DATABASE_URL == null) {
    return null;
  }
  shelved ??= open().catch((error: unknown) => {
    console.error("The persistent cache is unavailable:", error);
    setTimeout(() => {
      shelved = null;
    }, 30_000);
    return null;
  });
  return shelved;
}

async function retrieve(slot: string) {
  const reader = client();
  const cached = await reader?.get(slot).catch(() => null);
  if (cached != null) {
    return cached;
  }
  const pool = await shelf();
  if (pool == null) {
    return null;
  }
  const result = await pool.query<{ body: Buffer }>(
    "select body from entry where slot = $1",
    [slot],
  );
  const stored = result.rows[0]?.body;
  if (stored != null && reader != null) {
    void reader.set(slot, stored, { EX: LIFETIME }).catch(() => null);
  }
  return stored ?? null;
}

async function recall(slot: string) {
  try {
    const found = await retrieve(slot);
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

async function shelve(slot: string, version: string, packed: Buffer) {
  const pool = await shelf();
  await pool?.query(
    `insert into entry (slot, version, body) values ($1, $2, $3)
      on conflict (slot) do update set version = excluded.version, body = excluded.body, stored_at = now()`,
    [slot, version, packed],
  );
}

async function exclusive(
  slot: string,
  isWanted: (found: { version: string; value: unknown }) => boolean,
  compute: () => Promise<unknown>,
) {
  const lock = `lock:${slot}`;
  const token = randomUUID();
  const deadline = Date.now() + PATIENCE;
  for (;;) {
    const owner = client();
    if (owner == null || Date.now() >= deadline) {
      return compute();
    }
    const acquired = await owner
      .set(lock, token, { NX: true, PX: LOCK })
      .catch(() => "OK");
    if (acquired != null) {
      try {
        return await compute();
      } finally {
        void owner
          .get(lock)
          .then(async (held) =>
            held?.toString() === token ? owner.del(lock) : null,
          )
          .catch(() => null);
      }
    }
    let idle = 0;
    while (idle < GRACE && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const found = await recall(slot);
      if (found != null && isWanted(found)) {
        return found.value;
      }
      const held = await owner.exists(lock).catch(() => 1);
      idle = held === 0 ? idle + 1 : 0;
    }
  }
}

function keep(slot: string, version: string, value: unknown) {
  try {
    const payload = serialize({ version, value });
    if (payload.length <= LARGEST) {
      void pack(payload)
        .then(async (packed) =>
          Promise.all([
            client()?.set(slot, packed, { EX: LIFETIME }),
            shelve(slot, version, packed),
          ]),
        )
        .catch(() => null);
    }
    return payload.length;
  } catch {
    return 0;
  }
}

export async function stash(slot: string, text: string) {
  await client()?.set(slot, await pack(Buffer.from(text)), { EX: LIFETIME });
}

export async function unstash(slot: string) {
  const found = await client()
    ?.get(slot)
    .catch(() => null);
  return found == null ? null : (await unpack(found)).toString();
}

async function refresh() {
  try {
    const result = await db.execute<{ version: string; today: string }>(sql`
      select concat_ws(':', count(*) filter (where status = 'published'), max(published_at),
        count(*) filter (where status = 'rejected'), (select built_at from coverage_build), (select name_day())) as version,
        current_date::text as today
      from changeset
    `);
    state.version = result.rows[0]?.version ?? "";
    state.today = result.rows[0]?.today ?? "";
    state.checked = Date.now();
  } finally {
    state.checking = null;
  }
}

function drop(key: string) {
  const entry = entries.get(key);
  if (entry == null) {
    return;
  }

  state.size -= entry.size;
  entries.delete(key);
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

function store<T>(
  key: string,
  slot: string,
  compute: () => Promise<T>,
  isStrict: boolean,
  current: () => string,
) {
  const entry: Entry = {
    value: Promise.resolve(),
    version: current(),
    size: 0,
    isSettled: false,
    isRenewing: false,
  };
  let recalled = 0;
  entry.value = (async () => {
    const found = await recall(slot);
    if (found != null && (found.version === entry.version || !isStrict)) {
      entry.version = found.version;
      recalled = found.size;
      return found.value;
    }
    return exclusive(
      slot,
      (other) => other.version === entry.version || !isStrict,
      compute,
    );
  })();
  void (async () => {
    try {
      const value = await entry.value;
      const isFresh = entry.version === current();
      const size =
        isFresh && recalled === 0 ? keep(slot, entry.version, value) : recalled;
      entry.isSettled = true;
      if (entries.get(key) !== entry) {
        return;
      }
      entry.size = size;
      state.size += size;
      evict();
      if (!isFresh) {
        renew(key, slot, entry, compute, current);
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

function renew(
  key: string,
  slot: string,
  hit: Entry,
  compute: () => Promise<unknown>,
  current: () => string,
) {
  hit.isRenewing = true;
  waiting.push(async () => {
    const version = current();
    try {
      const value = await compute();
      const next: Entry = {
        value: Promise.resolve(value),
        version,
        size: keep(slot, version, value),
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
  shared: string,
  compute: () => Promise<T>,
  isStrict = false,
  generation = GENERATION,
  isNeutral = false,
) {
  const isRefresh = isStrict || refreshing.getStore() === true;
  const locale = getLocale();
  const isBase = isNeutral || locale === baseLocale;
  const key = isBase ? shared : `${locale}|${shared}`;
  const slot = `c:${generation}:${key}`;
  const current = generation.endsWith(UNDATED)
    ? () => state.version
    : () => `${state.version}:${state.today}`;
  const run = isBase
    ? compute
    : async () => localizeNames(await compute(), locale);
  if (isStrict || Date.now() - state.checked >= CHECK) {
    state.checking ??= refresh();
    await state.checking;
  }
  const hit = entries.get(key);
  if (hit != null) {
    entries.delete(key);
    entries.set(key, hit);
    const version = current();
    if (isRefresh && hit.version !== version) {
      const next = store(key, slot, run, true, current);
      put(key, next);
      return next.value as Promise<T>;
    }
    if (hit.version !== version && hit.isSettled && !hit.isRenewing) {
      renew(key, slot, hit, run, current);
    }
    return hit.value as Promise<T>;
  }
  const entry = store(key, slot, run, isRefresh, current);
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
      VERSIONS.get(name),
    );
    return { output, context: {} };
  });
}

export const os = cached(false);
export const strict = cached(true);
