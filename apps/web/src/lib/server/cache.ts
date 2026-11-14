import { version as build, building } from "$app/env";
import { CACHE_SHADOW, PAGES_DATABASE_URL } from "$app/env/private";
import { os as base, Procedure } from "@orpc/server";
import { RESP_TYPES } from "@redis/client";
import { sql } from "drizzle-orm";
import { AsyncLocalStorage } from "node:async_hooks";
import { Buffer } from "node:buffer";
import { createHash, randomUUID } from "node:crypto";
import { isDeepStrictEqual, promisify } from "node:util";
import { deserialize, serialize } from "node:v8";
import { zstdCompress, zstdDecompress } from "node:zlib";
import pg from "pg";
import { codeVersion, procedureVersions } from "virtual:code-version";
import type { Reads } from "#lib/server/reads.js";
import { PROJECT } from "#lib/constants.js";
import { baseLocale, getLocale } from "#lib/paraglide/runtime.js";
import { db, pagesPoolMax } from "#lib/server/db/index.js";
import { localizeNames } from "#lib/server/names.js";
import { fresh, reading } from "#lib/server/reads.js";
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

type Deps = Promise<Array<string> | null>;

type Entry = {
  value: Promise<unknown>;
  deps: Deps;
  version: string;
  mark: number | null;
  day: string;
  born: number;
  used: number;
  origin: "renew" | "store";
  size: number;
  isSettled: boolean;
  isRenewing: boolean;
};

type Traced = { value: unknown; deps: Deps; isLocal: boolean };

type Totals = {
  today: string;
  published: string;
  latest: string | null;
  rejected: string;
  built: string | null;
  named: string | null;
};

const NONE: Deps = Promise.resolve(null);
const WIDEST = 50_000;
const REMEMBERED = 100_000;
const REPORT = 600_000;
const ANCHOR = sql`
  left join lateral (
    select u.k from (
      select x.polity_id from election x where e.type = 'election' and x.id = e.id
      union all select x.polity_id from office x where e.type = 'office' and x.id = e.id
      union all select x.polity_id from body x where e.type = 'body' and x.id = e.id
      union all select x.polity_id from electoral_system x where e.type = 'electoral_system' and x.id = e.id
      union all select x.polity_id from government x where e.type = 'government' and x.id = e.id
      union all select v.k from contest x join election l on l.id = x.election_id
        left join body b on b.id = x.body_id left join office f on f.id = x.office_id
        cross join lateral (values (l.polity_id), (b.polity_id), (f.polity_id)) v(k)
        where e.type = 'contest' and x.id = e.id
      union all select v.k from result_set r join contest x on x.id = r.contest_id join election l on l.id = x.election_id
        left join body b on b.id = x.body_id left join office f on f.id = x.office_id
        cross join lateral (values (l.polity_id), (b.polity_id), (f.polity_id)) v(k)
        where e.type = 'result_set' and r.id = e.id
      union all select v.k from contest_option o join contest x on x.id = o.contest_id join election l on l.id = x.election_id
        left join body b on b.id = x.body_id left join office f on f.id = x.office_id
        cross join lateral (values (l.polity_id), (b.polity_id), (f.polity_id), (o.party_id)) v(k)
        where e.type = 'contest_option' and o.id = e.id
      union all select v.k from candidacy d join contest_option o on o.id = d.option_id join contest x on x.id = o.contest_id
        join election l on l.id = x.election_id
        left join body b on b.id = x.body_id left join office f on f.id = x.office_id
        cross join lateral (values (l.polity_id), (b.polity_id), (f.polity_id), (d.person_id), (d.member_of_party_id)) v(k)
        where e.type = 'candidacy' and d.id = e.id
      union all select v.k from tenure t left join body b on b.id = t.body_id left join office f on f.id = t.office_id
        cross join lateral (values (coalesce(b.polity_id, f.polity_id)), (t.person_id)) v(k)
        where e.type = 'tenure' and t.id = e.id
      union all select b.polity_id from seat x join body b on b.id = x.body_id where e.type = 'seat' and x.id = e.id
      union all select l.polity_id from alliance x join election l on l.id = x.election_id
        where e.type = 'alliance' and x.id = e.id
    ) u(k) where u.k is not null
  ) a on true
`;

export const refreshing = new AsyncLocalStorage<boolean>();

export function dataVersion() {
  return `${state.version}:${state.today}`;
}

const entries = new Map<string, Entry>();
const waiting = new Map<string, { hit: Entry; task: () => Promise<void> }>();
const names = new WeakMap<object, string>();
const state: {
  version: string;
  today: string;
  checked: number;
  checking: Promise<void> | null;
  size: number;
} = { version: "", today: "", checked: 0, checking: null, size: 0 };
const shadow: {
  seq: number;
  own: Map<string, number>;
  below: Map<string, number>;
  seen: Set<string>;
  last: Totals | null;
  loading: Promise<void>;
  anchors: Map<string, Array<string>>;
  interned: Map<string, string>;
  reported: number;
} = {
  seq: 0,
  own: new Map(),
  below: new Map(),
  seen: new Set(),
  last: null,
  loading: Promise.resolve(),
  anchors: new Map(),
  interned: new Map(),
  reported: Date.now(),
};
const tally = {
  renewed: 0,
  unmarked: 0,
  wide: 0,
  deps: 0,
  same: 0,
  changed: 0,
  keptOwn: 0,
  keptBelow: 0,
  missOwn: 0,
  missBelow: 0,
  unstable: 0,
};

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

function shelfSlot(slot: string) {
  if (Buffer.byteLength(slot) <= 2000) {
    return slot;
  }
  const digest = createHash("sha256").update(slot).digest("hex");
  return `${slot.split(":", 2).join(":")}:#${digest}`;
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
    [shelfSlot(slot)],
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
      const deps =
        "deps" in parsed && Array.isArray(parsed.deps)
          ? parsed.deps.filter((key): key is string => typeof key === "string")
          : null;
      return {
        version: parsed.version,
        value: parsed.value,
        size: payload.length,
        deps,
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
    [shelfSlot(slot), version, packed],
  );
}

async function exclusive(
  slot: string,
  isWanted: (found: { version: string; value: unknown }) => boolean,
  compute: () => Promise<Traced>,
): Promise<Traced> {
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
        return {
          value: found.value,
          deps: Promise.resolve(found.deps),
          isLocal: false,
        };
      }
      const held = await owner.exists(lock).catch(() => 1);
      idle = held === 0 ? idle + 1 : 0;
    }
  }
}

function keep(
  slot: string,
  version: string,
  value: unknown,
  deps: Array<string> | null,
) {
  try {
    const payload = serialize(
      deps == null ? { version, value } : { version, value, deps },
    );
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
    const result = await reading.exit(async () =>
      db.execute<Totals>(sql`
        select current_date::text as today,
          count(*) filter (where status = 'published')::text as published,
          max(published_at)::text as latest,
          count(*) filter (where status = 'rejected')::text as rejected,
          (select built_at from coverage_build)::text as built,
          (select name_day())::text as named
        from changeset
      `),
    );
    const row = result.rows[0];
    state.version =
      row == null
        ? ""
        : [row.published, row.latest, row.rejected, row.built, row.named]
            .filter((part) => part != null)
            .join(":");
    state.today = row?.today ?? "";
    state.checked = Date.now();
    if (CACHE_SHADOW && row != null) {
      observe(row);
    }
  } finally {
    state.checking = null;
  }
}

function intern(key: string) {
  const found = shadow.interned.get(key);
  if (found != null) {
    return found;
  }
  shadow.interned.set(key, key);
  return key;
}

async function anchor(ids: Array<string>) {
  const anchors = new Map<string, Array<string>>();
  const batches: Array<Array<string>> = [];
  for (const id of ids) {
    const found = shadow.anchors.get(id);
    if (found != null) {
      anchors.set(id, found);
      continue;
    }
    const last = batches.at(-1);
    if (last == null || last.length >= 5000) {
      batches.push([id]);
    } else {
      last.push(id);
    }
  }
  for (const batch of batches) {
    const result = await db.execute<{ id: string; keys: Array<string> }>(sql`
      select e.id::text as id, array_agg(distinct coalesce(a.k, e.id)::text) as keys
      from entity e ${ANCHOR}
      where e.id = any(${sql.param(batch)}::uuid[])
      group by e.id
    `);
    if (shadow.anchors.size > REMEMBERED) {
      shadow.anchors.clear();
      shadow.interned.clear();
    }
    for (const id of batch) {
      anchors.set(id, []);
    }
    for (const row of result.rows) {
      anchors.set(
        row.id,
        row.keys.map((key) => intern(key)),
      );
    }
    for (const id of batch) {
      shadow.anchors.set(id, anchors.get(id) ?? []);
    }
  }
  return anchors;
}

async function settle(reads: Reads) {
  try {
    const inner = await Promise.all(reads.pending);
    if (reads.isWide || inner.includes(null)) {
      return null;
    }
    const keys = new Set(reads.keys);
    for (const found of inner) {
      for (const key of found ?? []) {
        keys.add(key);
      }
    }
    const anchors = await reading.exit(async () => anchor([...reads.ids]));
    for (const found of anchors.values()) {
      for (const key of found) {
        keys.add(key);
      }
    }
    return keys.size > WIDEST ? null : [...keys];
  } catch {
    return null;
  }
}

async function trace(compute: () => Promise<unknown>): Promise<Traced> {
  if (!CACHE_SHADOW) {
    return { value: await compute(), deps: NONE, isLocal: true };
  }
  const reads = fresh();
  const value = await reading.run(reads, compute);
  return { value, deps: settle(reads), isLocal: true };
}

async function recent() {
  const result = await db.execute<{ id: string }>(sql`
    select id::text as id from changeset
    where status = 'published' and published_at > now() - interval '2 days'
  `);
  const found: Array<string> = [];
  for (const { id } of result.rows) {
    if (!shadow.seen.has(id)) {
      shadow.seen.add(id);
      found.push(id);
    }
  }
  return found;
}

async function touch() {
  const ids = await recent();
  const own: Array<string> = [];
  const below: Array<string> = [];
  if (ids.length === 0) {
    return { own, below };
  }
  const result = await db.execute<{ key: string; is_own: boolean }>(sql`
    with recursive own as (
      select distinct coalesce(a.k, e.id) as key
      from changeset_entity ce join entity e on e.id = ce.entity_id ${ANCHOR}
      where ce.changeset_id = any(${sql.param(ids)}::uuid[])
    ), up(key, is_own) as (
      select key, true from own
      union
      select p.parent_id, false from polity_parent p join up on p.polity_id = up.key
    )
    select key::text as key, bool_or(is_own) as is_own from up group by key
    union all
    select 'translation', true where exists (
      select from translation where changeset_id = any(${sql.param(ids)}::uuid[])
    )
  `);
  for (const row of result.rows) {
    (row.is_own ? own : below).push(row.key);
  }
  return { own, below };
}

function observe(row: Totals) {
  const last = shadow.last;
  shadow.last = row;
  if (last == null) {
    shadow.loading = (async () => {
      await reading.exit(async () => recent()).catch(() => null);
    })();
    return;
  }
  const isPublished =
    row.published !== last.published || row.latest !== last.latest;
  const bumped: Array<string> = [];
  if (isPublished || row.rejected !== last.rejected) {
    bumped.push("changesets");
  }
  if (row.built !== last.built) {
    bumped.push("coverage");
  }
  if (row.named !== last.named) {
    bumped.push("*");
  }
  if (bumped.length === 0) {
    return;
  }
  shadow.loading = shadow.loading.then(async () => {
    const touched = isPublished
      ? await reading
          .exit(async () => touch())
          .catch(() => ({ own: ["*"], below: [] }))
      : { own: [], below: [] };
    shadow.seq++;
    for (const key of [...bumped, ...touched.own]) {
      shadow.own.set(key, shadow.seq);
      shadow.below.set(key, shadow.seq);
    }
    for (const key of touched.below) {
      shadow.below.set(key, shadow.seq);
    }
  });
}

function holds(
  deps: Array<string>,
  versions: Map<string, number>,
  mark: number,
) {
  if ((versions.get("*") ?? 0) > mark) {
    return false;
  }
  for (const key of deps) {
    if ((versions.get(key) ?? 0) > mark) {
      return false;
    }
  }
  return true;
}

function same(left: unknown, right: unknown) {
  try {
    return isDeepStrictEqual(left, right);
  } catch {
    return false;
  }
}

function differ(before: string, after: string) {
  let at = 0;
  while (at < before.length && before[at] === after[at]) {
    at++;
  }
  return at;
}

function sample(old: unknown, value: unknown) {
  try {
    const before = JSON.stringify(old) ?? "undefined";
    const after = JSON.stringify(value) ?? "undefined";
    if (before !== after) {
      const at = differ(before, after);
      const from = Math.max(0, at - 80);
      return {
        json: [before.length, after.length],
        at,
        old: before.slice(from, at + 160),
        new: after.slice(from, at + 160),
      };
    }
    const left = serialize(old).toString("hex");
    const right = serialize(value).toString("hex");
    const at = differ(left, right);
    const from = Math.max(0, at - 40);
    return {
      json: "equal",
      v8: [left.length / 2, right.length / 2],
      at: at / 2,
      old: left.slice(from, at + 80),
      new: right.slice(from, at + 80),
    };
  } catch (error) {
    return { error: String(error).slice(0, 200) };
  }
}

function report() {
  if (Date.now() - shadow.reported < REPORT) {
    return;
  }
  shadow.reported = Date.now();
  console.info(`cache shadow: ${JSON.stringify(tally)}`);
}

async function verify(
  key: string,
  hit: Entry,
  value: unknown,
  compute: () => Promise<unknown>,
  isDated: boolean,
) {
  tally.renewed++;
  const deps = await hit.deps;
  await shadow.loading;
  if (hit.mark == null || deps == null) {
    tally.unmarked += Number(hit.mark == null);
    tally.wide += Number(hit.mark != null);
    report();
    return;
  }
  tally.deps += deps.length;
  const isCurrent = !isDated || hit.day === state.today;
  const keptOwn = isCurrent && holds(deps, shadow.own, hit.mark);
  const keptBelow = isCurrent && holds(deps, shadow.below, hit.mark);
  const isSame = same(await hit.value, value);
  tally.keptOwn += Number(keptOwn);
  tally.keptBelow += Number(keptBelow);
  tally.same += Number(isSame);
  tally.changed += Number(!isSame);
  report();
  if (isSame || !keptOwn) {
    return;
  }
  const again = await reading.exit(compute);
  if (!same(again, value)) {
    tally.unstable++;
    return;
  }
  tally.missOwn++;
  tally.missBelow += Number(keptBelow);
  const old = await hit.value;
  console.warn(
    `cache shadow miss: ${JSON.stringify({
      key: key.slice(0, 300),
      deps: deps.length,
      below: keptBelow,
      origin: hit.origin,
      age: Math.round((Date.now() - hit.born) / 1000),
      marked: [hit.mark, shadow.seq],
      ...sample(old, value),
    })}`,
  );
}

function drop(key: string) {
  const entry = entries.get(key);
  if (entry == null) {
    return;
  }

  state.size -= entry.size;
  entries.delete(key);
  waiting.delete(key);
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

function store(
  key: string,
  slot: string,
  compute: () => Promise<unknown>,
  isStrict: boolean,
  current: () => string,
  isDated: boolean,
) {
  const entry: Entry = {
    value: Promise.resolve(),
    deps: NONE,
    version: current(),
    mark: shadow.seq,
    day: state.today,
    born: Date.now(),
    used: Date.now(),
    origin: "store",
    size: 0,
    isSettled: false,
    isRenewing: false,
  };
  let recalled = 0;
  const traced = (async (): Promise<Traced> => {
    const found = await recall(slot);
    if (found != null && (found.version === entry.version || !isStrict)) {
      entry.version = found.version;
      recalled = found.size;
      return {
        value: found.value,
        deps: Promise.resolve(found.deps),
        isLocal: false,
      };
    }
    return exclusive(
      slot,
      (other) => other.version === entry.version || !isStrict,
      async () => trace(compute),
    );
  })();
  entry.value = traced.then((found) => {
    if (!found.isLocal) {
      entry.mark = null;
    }
    return found.value;
  });
  entry.deps = traced.then(async (found) => found.deps).catch(() => null);
  void (async () => {
    try {
      const value = await entry.value;
      const isFresh = entry.version === current();
      const size =
        isFresh && recalled === 0
          ? keep(slot, entry.version, value, await entry.deps)
          : recalled;
      entry.isSettled = true;
      if (entries.get(key) !== entry) {
        return;
      }
      entry.size = size;
      state.size += size;
      evict();
      if (!isFresh) {
        renew(key, slot, entry, compute, current, isDated);
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
const ACROSS = 4;
const SEATS = "renewing";
const SEAT = 120_000;
const RECENT = 600_000;
const ADMIT = `
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
  return 0
end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
`;
let renewing = 0;

async function admit() {
  const token = randomUUID();
  for (;;) {
    const owner = client();
    const admitted =
      owner == null
        ? 1
        : await owner
            .eval(ADMIT, {
              keys: [SEATS],
              arguments: [String(ACROSS), String(SEAT), token],
            })
            .catch(() => 1);
    if (admitted === 1) {
      return () => {
        void owner?.zRem(SEATS, token).catch(() => null);
      };
    }
    await new Promise((resolve) =>
      setTimeout(resolve, 250 + Math.random() * 250),
    );
  }
}

async function claim(slot: string) {
  const owner = client();
  if (owner == null) {
    return () => null;
  }
  const lock = `lock:${slot}`;
  const token = randomUUID();
  const acquired = await owner
    .set(lock, token, { NX: true, PX: LOCK })
    .catch(() => "OK");
  if (acquired == null) {
    return null;
  }
  return () => {
    void owner
      .get(lock)
      .then(async (held) =>
        held?.toString() === token ? owner.del(lock) : null,
      )
      .catch(() => null);
  };
}

async function drain() {
  while (renewing < RENEWING) {
    const key = waiting.keys().next().value;
    const next = key == null ? undefined : waiting.get(key);
    if (key == null || next == null) {
      return;
    }
    waiting.delete(key);
    if (Date.now() - next.hit.used > RECENT) {
      next.hit.isRenewing = false;
      continue;
    }
    renewing++;
    try {
      await next.task();
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
  isDated: boolean,
) {
  hit.isRenewing = true;
  const attempt = async () => {
    const leave = await admit();
    const release = await claim(slot);
    try {
      if (release == null) {
        return null;
      }
      const version = current();
      const found = await recall(slot);
      if (found?.version === version) {
        if (entries.get(key) === hit) {
          put(key, {
            value: Promise.resolve(found.value),
            deps: Promise.resolve(found.deps),
            version,
            mark: null,
            day: state.today,
            born: Date.now(),
            used: hit.used,
            origin: "renew",
            size: found.size,
            isSettled: true,
            isRenewing: false,
          });
        }
        return null;
      }
      const mark = shadow.seq;
      const day = state.today;
      const traced = await trace(compute);
      const deps = await traced.deps;
      const next: Entry = {
        value: Promise.resolve(traced.value),
        deps: Promise.resolve(deps),
        version,
        mark,
        day,
        born: Date.now(),
        used: hit.used,
        origin: "renew",
        size: keep(slot, version, traced.value, deps),
        isSettled: true,
        isRenewing: false,
      };
      if (entries.get(key) === hit) {
        put(key, next);
      }
      return { value: traced.value };
    } finally {
      release?.();
      leave();
    }
  };
  const task = async () => {
    const renewed = await attempt().catch(() => null);
    if (renewed != null && CACHE_SHADOW) {
      await verify(key, hit, renewed.value, compute, isDated).catch(() => null);
    }
    hit.isRenewing = false;
  };
  waiting.set(key, { hit, task });
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
  const isDated = !generation.endsWith(UNDATED);
  const current = isDated
    ? () => `${state.version}:${state.today}`
    : () => state.version;
  const parent = CACHE_SHADOW ? reading.getStore() : undefined;
  const hand = async (entry: Entry) => {
    parent?.pending.push(entry.deps);
    return entry.value as Promise<T>;
  };
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
    hit.used = Date.now();
    const version = current();
    if (isRefresh && hit.version !== version) {
      const next = store(key, slot, run, true, current, isDated);
      put(key, next);
      return hand(next);
    }
    if (hit.version !== version && hit.isSettled && !hit.isRenewing) {
      renew(key, slot, hit, run, current, isDated);
    }
    return hand(hit);
  }
  const entry = store(key, slot, run, isRefresh, current, isDated);
  put(key, entry);
  return hand(entry);
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
