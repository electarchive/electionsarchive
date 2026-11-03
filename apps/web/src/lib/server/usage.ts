import { building } from "$app/env";
import { sql } from "drizzle-orm";
import { createHash, randomBytes } from "node:crypto";
import { isIP } from "node:net";
import { PROJECT } from "#lib/constants.js";
import { authDb } from "#lib/server/db/index.js";
import { bucket } from "#lib/server/guard.js";
import { redis } from "#lib/server/redis.js";

type Owner = { keyId: string; userId: string } | null;

const KEPT = 3 * 86_400;
const ROLLUP = 5 * 60_000;
const UNKNOWN = "unknown";
const ANONYMOUS = "anon:";

const salts = new Map<string, string>();

const today = () => new Date().toISOString().slice(0, 10);

const counts = (day: string) => `${PROJECT}:mcp:calls:${day}`;
const lasts = (day: string) => `${PROJECT}:mcp:last:${day}`;
const names = (day: string) => `${PROJECT}:mcp:clients:${day}`;

function clean(name: string | null | undefined) {
  const text = (name ?? "")
    .replaceAll(/\p{Cc}/gu, "")
    .trim()
    .slice(0, 80);
  return text === "" ? UNKNOWN : text;
}

function agent(headers: Headers) {
  return clean(/^[\w.@-]+/u.exec(headers.get("user-agent") ?? "")?.[0]);
}

async function salt(day: string) {
  const known = salts.get(day);
  if (known != null) {
    return known;
  }
  const key = `${PROJECT}:mcp:salt:${day}`;
  await redis().set(key, randomBytes(32).toString("hex"), {
    condition: "NX",
    expiration: { type: "EX", value: 2 * 86_400 },
  });
  const stored = await redis().get(key);
  if (stored == null) {
    throw new Error("The usage salt is missing.");
  }
  salts.clear();
  salts.set(day, stored);
  return stored;
}

async function caller(headers: Headers, owner: Owner, day: string) {
  if (owner != null) {
    return owner.keyId;
  }
  const address =
    headers.get("x-forwarded-for")?.split(",", 1)[0]?.trim() ?? "";
  const family = isIP(address);
  const place = family === 0 ? "local" : bucket(address, family === 4 ? 4 : 6);
  const hash = createHash("sha256")
    .update(`${await salt(day)}|${place}`)
    .digest("hex")
    .slice(0, 20);
  return `${ANONYMOUS}${hash}`;
}

function background(work: () => Promise<unknown>) {
  void work().catch((error: unknown) => {
    console.error("MCP usage:", error);
  });
}

export function introduce(headers: Headers, owner: Owner, name: string) {
  background(async () => {
    const day = today();
    const who = await caller(headers, owner, day);
    await redis()
      .multi()
      .hSet(names(day), `${who}\t${agent(headers)}`, clean(name))
      .expire(names(day), KEPT)
      .exec();
  });
}

export function count(headers: Headers, owner: Owner, tool: string) {
  background(async () => {
    const day = today();
    const who = await caller(headers, owner, day);
    const client =
      (await redis().hGet(names(day), `${who}\t${agent(headers)}`)) ??
      agent(headers);
    const field = [who, clean(client), tool, owner?.userId ?? ""].join("\t");
    await redis()
      .multi()
      .hIncrBy(counts(day), field, 1)
      .hSet(lasts(day), field, String(Date.now()))
      .expire(counts(day), KEPT)
      .expire(lasts(day), KEPT)
      .exec();
  });
}

async function rollup() {
  const isMine = await redis().set(`${PROJECT}:mcp:rollup`, "1", {
    condition: "NX",
    expiration: { type: "PX", value: ROLLUP - 10_000 },
  });
  if (isMine == null) {
    return;
  }
  const now = Date.now();
  for (const day of [
    new Date(now - 86_400_000).toISOString().slice(0, 10),
    today(),
  ]) {
    const [tally, seen] = await Promise.all([
      redis().hGetAll(counts(day)),
      redis().hGetAll(lasts(day)),
    ]);
    const rows: Array<{
      caller: string;
      client: string;
      tool: string;
      key_id: string | null;
      user_id: string | null;
      calls: number;
      last: number;
    }> = [];
    for (const [field, value] of Object.entries(tally)) {
      const [who = "", client = UNKNOWN, tool = "", userId = ""] = field.split(
        "\t",
        4,
      );
      const calls = Number(value);
      if (who === "" || tool === "" || calls <= 0 || Number.isNaN(calls)) {
        continue;
      }
      const isKeyed = !who.startsWith(ANONYMOUS);
      rows.push({
        caller: who,
        client,
        tool,
        key_id: isKeyed ? who : null,
        user_id: isKeyed && userId !== "" ? userId : null,
        calls,
        last: Number(seen[field] ?? now),
      });
    }
    if (rows.length === 0) {
      continue;
    }
    await authDb.execute(sql`
      insert into mcp_usage (day, caller, client, tool, key_id, user_id, calls, last_at)
      select ${day}::date, t.caller, t.client, t.tool,
             (select id from apikeys where id = t.key_id),
             (select id from users where id = t.user_id),
             t.calls, to_timestamp(t.last / 1000.0)
      from jsonb_to_recordset(${JSON.stringify(rows)}::jsonb)
        as t(caller text, client text, tool text, key_id uuid, user_id uuid, calls integer, last double precision)
      on conflict (day, caller, client, tool) do update
        set calls = greatest(mcp_usage.calls, excluded.calls),
            last_at = greatest(mcp_usage.last_at, excluded.last_at),
            key_id = coalesce(excluded.key_id, mcp_usage.key_id),
            user_id = coalesce(excluded.user_id, mcp_usage.user_id)
    `);
  }
}

if (!building) {
  setInterval(() => {
    background(rollup);
  }, ROLLUP).unref();
}

export async function keyUsage(userId: string) {
  try {
    const { rows } = await authDb.execute<{
      key_id: string;
      today: number;
      month: number;
      last_at: string;
      tools: Array<string>;
    }>(sql`
      with tools as (
        select key_id, tool,
               sum(calls) filter (where day = (now() at time zone 'utc')::date) as today,
               sum(calls) as month, max(last_at) as last_at
        from mcp_usage
        where user_id = ${userId} and key_id is not null
          and day > (now() at time zone 'utc')::date - 30
        group by key_id, tool
      )
      select key_id, coalesce(sum(today), 0)::int as today, sum(month)::int as month,
             max(last_at)::text as last_at,
             (array_agg(tool order by month desc, tool))[1:3] as tools
      from tools
      group by key_id
    `);
    return new Map(rows.map((row) => [row.key_id, row]));
  } catch (error) {
    console.error("MCP usage:", error);
    return null;
  }
}

export async function usageReport() {
  const window = sql`day > (now() at time zone 'utc')::date - 30`;
  const day = sql`day = (now() at time zone 'utc')::date`;
  const anonymous = sql`caller like ${`${ANONYMOUS}%`}`;
  try {
    const [totals, keys, users, clients, tools] = await Promise.all([
      authDb.execute<{
        today: number;
        month: number;
        anonymous_today: number;
        anonymous_month: number;
        anonymous_callers: number;
      }>(sql`
        select coalesce(sum(calls) filter (where ${day}), 0)::int as today,
               coalesce(sum(calls), 0)::int as month,
               coalesce(sum(calls) filter (where ${day} and ${anonymous}), 0)::int as anonymous_today,
               coalesce(sum(calls) filter (where ${anonymous}), 0)::int as anonymous_month,
               count(distinct caller) filter (where ${day} and ${anonymous})::int as anonymous_callers
        from mcp_usage where ${window}
      `),
      authDb.execute<{
        key_id: string;
        name: string | null;
        start: string | null;
        owner: string | null;
        today: number;
        month: number;
        last_at: string;
      }>(sql`
        select u.key_id, k.name, k.start, users.name as owner,
               coalesce(sum(u.calls) filter (where ${day}), 0)::int as today,
               sum(u.calls)::int as month, max(u.last_at)::text as last_at
        from mcp_usage u
        join apikeys k on k.id = u.key_id
        left join users on users.id = u.user_id
        where ${window}
        group by u.key_id, k.name, k.start, users.name
        order by month desc
        limit 20
      `),
      authDb.execute<{
        user_id: string;
        name: string | null;
        keys: number;
        today: number;
        month: number;
      }>(sql`
        select u.user_id, users.name, count(distinct u.key_id)::int as keys,
               coalesce(sum(u.calls) filter (where ${day}), 0)::int as today,
               sum(u.calls)::int as month
        from mcp_usage u
        join users on users.id = u.user_id
        where ${window}
        group by u.user_id, users.name
        order by month desc
        limit 20
      `),
      authDb.execute<{
        client: string;
        callers: number;
        today: number;
        month: number;
      }>(sql`
        select client, count(distinct caller)::int as callers,
               coalesce(sum(calls) filter (where ${day}), 0)::int as today,
               sum(calls)::int as month
        from mcp_usage where ${window}
        group by client
        order by month desc
        limit 20
      `),
      authDb.execute<{ tool: string; today: number; month: number }>(sql`
        select tool, coalesce(sum(calls) filter (where ${day}), 0)::int as today,
               sum(calls)::int as month
        from mcp_usage where ${window}
        group by tool
        order by month desc
      `),
    ]);
    return {
      totals: totals.rows[0] ?? null,
      keys: keys.rows,
      users: users.rows,
      clients: clients.rows,
      tools: tools.rows,
    };
  } catch (error) {
    console.error("MCP usage:", error);
    return null;
  }
}
