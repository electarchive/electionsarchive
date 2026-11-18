import { version as build, building } from "$app/env";
import { CACHE_KEYS, CACHE_SHADOW, PAGES_DATABASE_URL } from "$app/env/private";
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
const SHELF_AGE = 7 * 86_400;
const SHELF_BUDGET = 25 * 1024 ** 3;
const SHELF_BATCH = 5000;
const PRUNE = 3_600_000;
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

type Traced = { value: unknown; deps: Deps; mark: number | null; day: string };

type Recalled = {
  version: string;
  value: unknown;
  size: number;
  deps: Array<string> | null;
  mark: number | null;
  day: string;
};

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
const FLUSH = 60_000;
const SAMPLE = 0.02;
const SYNC_WAIT = 250;
const VERSIONED = 2_000_000;
const TRACING = CACHE_SHADOW || CACHE_KEYS;
const MODE = CACHE_KEYS ? "keys" : "shadow";
const HELD = sql`(
  v.k = l.polity_id or v.k <> all(array(
    with recursive up(k) as (
      select l.polity_id
      union
      select p.parent_id from polity_parent p join up on p.polity_id = up.k
    )
    select k from up
  ))
)`;
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
        where e.type = 'contest' and x.id = e.id and ${HELD}
      union all select v.k from result_set r join contest x on x.id = r.contest_id join election l on l.id = x.election_id
        left join body b on b.id = x.body_id left join office f on f.id = x.office_id
        cross join lateral (values (l.polity_id), (b.polity_id), (f.polity_id)) v(k)
        where e.type = 'result_set' and r.id = e.id and ${HELD}
      union all select v.k from contest_option o join contest x on x.id = o.contest_id join election l on l.id = x.election_id
        left join body b on b.id = x.body_id left join office f on f.id = x.office_id
        cross join lateral (values (l.polity_id), (b.polity_id), (f.polity_id), (o.party_id)) v(k)
        where e.type = 'contest_option' and o.id = e.id and ${HELD}
      union all select v.k from candidacy d join contest_option o on o.id = d.option_id join contest x on x.id = o.contest_id
        join election l on l.id = x.election_id
        left join body b on b.id = x.body_id left join office f on f.id = x.office_id
        cross join lateral (values (l.polity_id), (b.polity_id), (f.polity_id), (d.person_id), (d.member_of_party_id)) v(k)
        where e.type = 'candidacy' and d.id = e.id and ${HELD}
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
  epochs: Map<string, number>;
  seen: number | null;
  mapped: Set<string>;
  total: number | null;
  pending: Array<[string, string]>;
  last: Totals | null;
  loading: Promise<void>;
  anchors: Map<string, Array<string>>;
  interned: Map<string, string>;
  reported: number;
  flushed: number;
  sent: Map<string, number>;
} = {
  epochs: new Map(),
  seen: null,
  mapped: new Set(),
  total: null,
  pending: [],
  last: null,
  loading: Promise.resolve(),
  anchors: new Map(),
  interned: new Map(),
  reported: Date.now(),
  flushed: Date.now(),
  sent: new Map(),
};
const tally = {
  computed: 0,
  restamped: 0,
  sampled: 0,
  renewed: 0,
  unmarked: 0,
  wide: 0,
  deps: 0,
  same: 0,
  changed: 0,
  kept: 0,
  miss: 0,
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
    create index if not exists entry_stored_at_idx on entry (stored_at);
    create sequence if not exists epoch start ${Date.now()};
    create table if not exists bump (
      key text primary key,
      epoch bigint not null
    );
    create index if not exists bump_epoch_idx on bump (epoch);
    create table if not exists bumped (
      changeset_id uuid primary key,
      epoch bigint not null
    );
    create table if not exists synced (
      id boolean primary key default true check (id),
      total bigint not null
    );
    insert into bump (key, epoch) values ('*', nextval('epoch')) on conflict do nothing;
  `);
  setInterval(() => {
    prune(pool).catch((error: unknown) => {
      console.error("Pruning the persistent cache failed:", error);
    });
  }, PRUNE).unref();
  return pool;
}

async function prune(pool: pg.Pool) {
  const connection = await pool.connect();
  try {
    const locked = await connection.query<{ isLocked: boolean }>(
      `select pg_try_advisory_lock(hashtext('cache prune')) as "isLocked"`,
    );
    if (locked.rows[0]?.isLocked !== true) {
      return;
    }
    try {
      const found = await connection.query<{ cutoff: Date }>(
        `with sampled as (
          select stored_at, sum(pg_column_size(body)::float8 * 1000) over (order by stored_at desc) as total
          from entry tablesample system (0.1)
        )
        select greatest(
          now() - make_interval(secs => $1),
          (select min(stored_at) from sampled where total <= $2)
        ) as cutoff`,
        [SHELF_AGE, SHELF_BUDGET],
      );
      const cutoff = found.rows[0]?.cutoff;
      if (cutoff == null) {
        return;
      }
      let deleted = SHELF_BATCH;
      while (deleted === SHELF_BATCH) {
        const result = await connection.query(
          "delete from entry where ctid = any(array(select ctid from entry where stored_at < $1 limit $2))",
          [cutoff, SHELF_BATCH],
        );
        deleted = result.rowCount ?? 0;
      }
    } finally {
      await connection.query(
        "select pg_advisory_unlock(hashtext('cache prune'))",
      );
    }
  } finally {
    connection.release();
  }
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

async function recall(slot: string): Promise<Recalled | null> {
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
        mark:
          "mark" in parsed && typeof parsed.mark === "number"
            ? parsed.mark
            : null,
        day:
          "day" in parsed && typeof parsed.day === "string" ? parsed.day : "",
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
  isWanted: (found: Recalled) => Promise<boolean>,
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
      if (found != null && (await isWanted(found))) {
        return {
          value: found.value,
          deps: Promise.resolve(found.deps),
          mark: found.mark,
          day: found.day,
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
  mark: number | null,
  day: string,
) {
  try {
    const payload = serialize(
      deps == null ? { version, value } : { version, value, deps, mark, day },
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
    if (TRACING && row != null) {
      observe(row);
      report();
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
    const result = await db.execute<{
      id: string;
      keys: Array<string>;
      isRow: boolean;
    }>(sql`
      select e.id::text as id, array_agg(distinct coalesce(a.k, e.id)::text) as keys,
        e.type in ('body', 'office') as "isRow"
      from entity e ${ANCHOR}
      where e.id = any(${sql.param(batch)}::uuid[])
      group by e.id, e.type
    `);
    if (shadow.anchors.size > REMEMBERED) {
      shadow.anchors.clear();
      shadow.interned.clear();
    }
    for (const id of batch) {
      anchors.set(id, []);
    }
    for (const row of result.rows) {
      const keys = row.keys.map((key) => intern(key));
      anchors.set(row.id, row.isRow ? [`row:${row.id}`, ...keys] : keys);
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
    for (const [id, found] of anchors) {
      const [first] = found;
      if (first?.startsWith("row:") === true && !reads.asked.has(id)) {
        keys.add(first);
        continue;
      }
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
  const mark = shadow.seen;
  const day = state.today;
  if (!TRACING) {
    return { value: await compute(), deps: NONE, mark: null, day };
  }
  const reads = fresh();
  const value = await reading.run(reads, compute);
  return { value, deps: settle(reads), mark, day };
}

let following = false;

export function follow() {
  following = true;
}

async function checked() {
  if (Date.now() - state.checked >= CHECK) {
    state.checking ??= refresh();
    await state.checking;
  }
  await shadow.loading;
}

export async function traced<T>(compute: () => Promise<T>) {
  await checked();
  const mark = shadow.seen;
  if (!TRACING || mark == null) {
    return { value: await compute(), mark: null, deps: null };
  }
  const reads = fresh();
  const value = await reading.run(reads, compute);
  return { value, mark, deps: await settle(reads) };
}

export async function keeps(mark: number | null, deps: Array<string> | null) {
  if (!CACHE_KEYS || mark == null || deps == null) {
    return null;
  }
  await checked();
  return (
    mark <= (shadow.seen ?? 0) && (await isKept(mark, deps, state.today, false))
  );
}

async function recent() {
  const result = await db.execute<{ total: string; ids: Array<string> }>(sql`
    select (select count(*) from changeset where status = 'published')::text as total,
      array(
        select id::text from changeset
        where status = 'published' and published_at > now() - interval '2 days'
      ) as ids
  `);
  const row = result.rows[0];
  return { total: Number(row?.total ?? 0), ids: row?.ids ?? [] };
}

async function touch(ids: Array<string>) {
  const result = await db.execute<{ key: string }>(sql`
    with recursive own as (
      select distinct coalesce(a.k, e.id) as key
      from changeset_entity ce join entity e on e.id = ce.entity_id ${ANCHOR}
      where ce.changeset_id = any(${sql.param(ids)}::uuid[])
    ), up(key) as (
      select key from own
      union
      select p.parent_id from polity_parent p join up on p.polity_id = up.key
    )
    select key::text as key from up
    union all
    select distinct 'row:' || e.id from changeset_entity ce join entity e on e.id = ce.entity_id
    where ce.changeset_id = any(${sql.param(ids)}::uuid[]) and e.type in ('party', 'person', 'polity', 'body', 'office')
    union all
    select 'translation' where exists (
      select from translation where changeset_id = any(${sql.param(ids)}::uuid[])
    )
  `);
  return result.rows.map((row) => row.key);
}

async function bump(
  pool: pg.Pool,
  ids: Array<string>,
  tokens: Array<[string, string]>,
  total: number,
  isStart: boolean,
) {
  const connection = await pool.connect();
  try {
    await connection.query("begin");
    await connection.query("set local statement_timeout = 120000");
    await connection.query(
      "select pg_advisory_xact_lock(hashtext('cache bump'))",
    );
    const next = await connection.query<{ epoch: string }>(
      "select nextval('epoch')::text as epoch",
    );
    const epoch = next.rows[0]?.epoch;
    const won = await connection.query<{ id: string }>(
      `insert into bumped (changeset_id, epoch) select unnest($1::uuid[]), $2::bigint
        on conflict do nothing returning changeset_id::text as id`,
      [ids, epoch],
    );
    const marked = await connection.query<{ token: string; key: string }>(
      `with t(token, key) as (select * from unnest($1::text[], $2::text[])),
        won as (
          insert into bumped (changeset_id, epoch) select md5(token)::uuid, $3::bigint from t
          on conflict do nothing returning changeset_id
        )
        select t.token, t.key from t join won on won.changeset_id = md5(t.token)::uuid`,
      [tokens.map(([token]) => token), tokens.map(([, key]) => key), epoch],
    );
    const before = await connection.query<{ total: string }>(
      "select total::text as total from synced",
    );
    await connection.query(
      `insert into synced (total) values ($1::bigint)
        on conflict (id) do update set total = excluded.total`,
      [total],
    );
    const keys = marked.rows.map((row) => row.key);
    const reasons = marked.rows
      .filter((row) => row.key === "*")
      .map((row) => row.token);
    const last = before.rows[0]?.total;
    if (isStart && (last == null || Number(last) + won.rows.length !== total)) {
      keys.push("*");
      reasons.push(`start:${last ?? "none"}+${won.rows.length}:${total}`);
    }
    if (won.rows.length > 0 && !keys.includes("*")) {
      keys.push("changesets");
      const touched = await reading
        .exit(async () => touch(won.rows.map((row) => row.id)))
        .catch((error: unknown) => {
          console.error("The cache could not map changesets to keys:", error);
          reasons.push("touch");
          return ["*"];
        });
      for (const key of touched) {
        keys.push(key);
      }
    }
    const isFloor = keys.includes("*");
    if (keys.length > 0) {
      await connection.query(
        `insert into bump (key, epoch) select distinct unnest($1::text[]), $2::bigint
          on conflict (key) do update set epoch = excluded.epoch`,
        [isFloor ? ["*"] : keys, epoch],
      );
    }
    if (isFloor) {
      await connection.query("delete from bump where epoch < $1::bigint", [
        epoch,
      ]);
    }
    await connection.query("commit");
    if (isFloor) {
      console.info(`cache floor ${epoch}: ${reasons.join(" ")}`);
    }
  } catch (error) {
    await connection.query("rollback").catch(() => null);
    throw error;
  } finally {
    connection.release();
  }
}

export async function bumpKeys(keys: Array<string>) {
  const pool = await shelf();
  if (pool == null || keys.length === 0) {
    return;
  }
  const connection = await pool.connect();
  try {
    await connection.query("begin");
    await connection.query("set local statement_timeout = 120000");
    await connection.query(
      "select pg_advisory_xact_lock(hashtext('cache bump'))",
    );
    await connection.query(
      `insert into bump (key, epoch) select distinct unnest($1::text[]), nextval('epoch')
        on conflict (key) do update set epoch = excluded.epoch`,
      [keys],
    );
    await connection.query("commit");
  } catch (error) {
    await connection.query("rollback").catch(() => null);
    throw error;
  } finally {
    connection.release();
  }
}

export async function closeShelf() {
  const pool = await shelved?.catch(() => null);
  await pool?.end();
}

async function sync(observed: Array<[string, string]>) {
  const tokens = [...shadow.pending, ...observed];
  shadow.pending = [];
  try {
    const pool = await shelf();
    if (pool == null) {
      shadow.pending = tokens;
      shadow.seen = null;
      return;
    }
    const found = await reading.exit(async () => recent());
    const ids = found.ids.filter((id) => !shadow.mapped.has(id));
    if (shadow.total != null && found.total !== shadow.total + ids.length) {
      tokens.push([randomUUID(), "*"]);
    }
    if (shadow.epochs.size > VERSIONED) {
      tokens.push([`prune:${shadow.epochs.get("*")}`, "*"]);
    }
    await bump(
      pool,
      ids,
      tokens,
      found.total,
      shadow.total == null && !following,
    );
    shadow.mapped = new Set(found.ids);
    shadow.total = found.total;
    const result = await pool.query<{ key: string; epoch: string }>(
      "select key, epoch::text as epoch from bump where epoch > $1",
      [shadow.seen ?? -1],
    );
    let seen = shadow.seen ?? 0;
    for (const row of result.rows) {
      const epoch = Number(row.epoch);
      shadow.epochs.set(row.key, epoch);
      seen = Math.max(seen, epoch);
    }
    shadow.seen = seen;
    const floor = shadow.epochs.get("*") ?? 0;
    if (result.rows.some((row) => row.key === "*")) {
      for (const [key, epoch] of shadow.epochs) {
        if (epoch < floor) {
          shadow.epochs.delete(key);
        }
      }
    }
  } catch (error) {
    console.error("The cache could not sync its key versions:", error);
    shadow.pending = tokens;
    shadow.seen = null;
  }
}

function observe(row: Totals) {
  const last = shadow.last;
  shadow.last = row;
  const isMoved =
    last == null ||
    row.published !== last.published ||
    row.latest !== last.latest ||
    row.rejected !== last.rejected ||
    row.built !== last.built ||
    row.named !== last.named;
  if (!isMoved && shadow.seen != null) {
    return;
  }
  const tokens: Array<[string, string]> = [
    [`rejected:${row.rejected}`, "changesets"],
    [`coverage:${row.built}`, "coverage"],
    [`named:${row.named}`, "*"],
  ];
  shadow.loading = shadow.loading.then(async () => sync(tokens));
}

function holds(deps: Array<string>, mark: number) {
  if ((shadow.epochs.get("*") ?? 0) > mark) {
    return false;
  }
  for (const key of deps) {
    if ((shadow.epochs.get(key) ?? 0) > mark) {
      return false;
    }
  }
  return true;
}

async function isKept(
  mark: number | null,
  deps: Array<string> | null,
  day: string,
  isDated: boolean,
  isBounded = false,
) {
  if (
    !CACHE_KEYS ||
    mark == null ||
    deps == null ||
    (isDated && day !== state.today)
  ) {
    return false;
  }
  if (isBounded) {
    let timer: NodeJS.Timeout | undefined;
    const isSynced = await Promise.race([
      shadow.loading.then(() => true),
      new Promise<boolean>((resolve) => {
        timer = setTimeout(resolve, SYNC_WAIT, false);
      }),
    ]);
    clearTimeout(timer);
    if (!isSynced) {
      return false;
    }
  } else {
    await shadow.loading;
  }
  return shadow.seen != null && holds(deps, mark);
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
  const now = Date.now();
  const owner = client();
  if (now - shadow.flushed >= FLUSH && owner != null) {
    shadow.flushed = now;
    for (const [field, count] of Object.entries(tally)) {
      const delta = count - (shadow.sent.get(field) ?? 0);
      if (delta > 0) {
        shadow.sent.set(field, count);
        void owner
          .hIncrBy("cache:tally", `${MODE}.${field}`, delta)
          .catch(() => null);
      }
    }
  }
  if (now - shadow.reported < REPORT) {
    return;
  }
  shadow.reported = now;
  console.info(`cache ${MODE}: ${JSON.stringify(tally)}`);
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
    return;
  }
  tally.deps += deps.length;
  const mark = hit.mark;
  const held = () =>
    (!isDated || hit.day === state.today) &&
    shadow.seen != null &&
    holds(deps, mark);
  const isSame = same(await hit.value, value);
  let isHeld = held();
  if (isHeld && !isSame) {
    await state.checking;
    state.checking ??= refresh();
    await state.checking;
    await shadow.loading;
    isHeld = held();
  }
  tally.kept += Number(isHeld);
  tally.same += Number(isSame);
  tally.changed += Number(!isSame);
  if (isSame || !isHeld) {
    return;
  }
  const again = await reading.exit(compute);
  if (!same(again, value)) {
    tally.unstable++;
    return;
  }
  tally.miss++;
  const old = await hit.value;
  console.warn(
    `cache ${MODE} miss: ${JSON.stringify({
      key: key.slice(0, 300),
      deps: deps.length,
      origin: hit.origin,
      age: Math.round((Date.now() - hit.born) / 1000),
      marked: [hit.mark, shadow.seen],
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
    mark: null,
    day: state.today,
    born: Date.now(),
    used: Date.now(),
    origin: "store",
    size: 0,
    isSettled: false,
    isRenewing: false,
  };
  let recalled = 0;
  const accept = async (found: Recalled) => {
    if (found.version === entry.version) {
      return true;
    }
    if (!isStrict) {
      entry.version = found.version;
      return true;
    }
    const isHeld = await isKept(
      found.mark,
      found.deps,
      found.day,
      isDated,
      true,
    );
    tally.restamped += Number(isHeld);
    return isHeld;
  };
  const traced = (async (): Promise<Traced> => {
    const found = await recall(slot);
    if (found != null && (await accept(found))) {
      recalled = found.size;
      return {
        value: found.value,
        deps: Promise.resolve(found.deps),
        mark: found.mark,
        day: found.day,
      };
    }
    return exclusive(slot, accept, async () => trace(compute));
  })();
  entry.value = traced.then((found) => {
    entry.mark = found.mark;
    entry.day = found.day;
    return found.value;
  });
  entry.deps = traced.then(async (found) => found.deps).catch(() => null);
  void (async () => {
    try {
      const value = await entry.value;
      const isFresh = entry.version === current();
      const size =
        isFresh && recalled === 0
          ? keep(
              slot,
              entry.version,
              value,
              await entry.deps,
              entry.mark,
              entry.day,
            )
          : recalled;
      entry.isSettled = true;
      if (entries.get(key) !== entry) {
        return;
      }
      entry.size = size;
      state.size += size;
      evict();
      if (!isFresh) {
        revalidate(key, slot, entry, compute, current, isDated);
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
  isSample = false,
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
      const found = isSample ? null : await recall(slot);
      if (
        found != null &&
        (found.version === version ||
          (await isKept(found.mark, found.deps, found.day, isDated)))
      ) {
        tally.restamped += Number(found.version !== version);
        if (entries.get(key) === hit) {
          put(key, {
            value: Promise.resolve(found.value),
            deps: Promise.resolve(found.deps),
            version,
            mark: found.mark,
            day: found.day,
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
      const traced = await trace(compute);
      const deps = await traced.deps;
      tally.computed++;
      const next: Entry = {
        value: Promise.resolve(traced.value),
        deps: Promise.resolve(deps),
        version,
        mark: traced.mark,
        day: traced.day,
        born: Date.now(),
        used: hit.used,
        origin: "renew",
        size: keep(slot, version, traced.value, deps, traced.mark, traced.day),
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
    if (renewed != null && (CACHE_SHADOW || isSample)) {
      await verify(key, hit, renewed.value, compute, isDated).catch(() => null);
    }
    hit.isRenewing = false;
  };
  waiting.set(key, { hit, task });
  void drain();
}

function revalidate(
  key: string,
  slot: string,
  hit: Entry,
  compute: () => Promise<unknown>,
  current: () => string,
  isDated: boolean,
) {
  if (!CACHE_KEYS) {
    renew(key, slot, hit, compute, current, isDated);
    return;
  }
  hit.isRenewing = true;
  const version = current();
  void (async () => {
    if (!(await isKept(hit.mark, await hit.deps, hit.day, isDated))) {
      renew(key, slot, hit, compute, current, isDated);
      return;
    }
    hit.version = version;
    hit.isRenewing = false;
    tally.restamped++;
    if (Math.random() >= SAMPLE) {
      return;
    }
    tally.sampled++;
    renew(key, slot, hit, compute, current, isDated, true);
  })().catch(() => {
    renew(key, slot, hit, compute, current, isDated);
  });
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
  const parent = TRACING ? reading.getStore() : undefined;
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
      if (
        CACHE_KEYS &&
        hit.isSettled &&
        (await isKept(hit.mark, await hit.deps, hit.day, isDated, true))
      ) {
        hit.version = version;
        tally.restamped++;
        return hand(hit);
      }
      const next = store(key, slot, run, true, current, isDated);
      put(key, next);
      return hand(next);
    }
    if (hit.version !== version && hit.isSettled && !hit.isRenewing) {
      revalidate(key, slot, hit, run, current, isDated);
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

export function versionOf(name: string) {
  return VERSIONS.get(name);
}

export const os = cached(false);
export const strict = cached(true);
