import { dev } from "$app/env";
import { sql } from "drizzle-orm";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { env } from "node:process";
import { promisify } from "node:util";
import * as v from "valibot";
import { remember } from "#lib/server/cache.js";
import { db, liveDb } from "#lib/server/db/index.js";

const run = promisify(execFile);
const FRESH = 30_000;
const STALE = 15 * 60_000;
const UTC = `'YYYY-MM-DD"T"HH24:MI:SS"Z"'`;

const ShipmentSchema = v.object({
  version: v.string(),
  commit: v.string(),
  date: v.string(),
  commits: v.array(v.object({ sha: v.string(), subject: v.string() })),
});

export type Shipment = v.InferOutput<typeof ShipmentSchema>;

function memo<T>(compute: () => Promise<T>) {
  let kept: { at: number; value: Promise<T> } | null = null;
  return async () => {
    if (kept == null || Date.now() - kept.at >= FRESH) {
      const value = compute();
      kept = { at: Date.now(), value };
      value.catch(() => {
        kept = null;
      });
    }
    return kept.value;
  };
}

async function git(...args: Array<string>) {
  return (await run("git", args, { maxBuffer: 64 * 1024 ** 2 })).stdout.trim();
}

export const build = memo(async () => {
  if (
    env.EA_VERSION != null &&
    env.EA_VERSION !== "" &&
    env.EA_COMMIT != null &&
    env.EA_COMMIT !== ""
  ) {
    return { version: env.EA_VERSION, commit: env.EA_COMMIT, isDirty: false };
  }
  try {
    const [commit, status] = await Promise.all([
      git("rev-parse", "HEAD"),
      git("status", "--porcelain", "--untracked-files=no"),
    ]);
    return { version: "dev", commit, isDirty: status !== "" };
  } catch {
    return { version: "dev", commit: null, isDirty: false };
  }
});

export const changelog = memo(async (): Promise<Array<Shipment>> => {
  if (!dev) {
    try {
      return v.parse(
        v.array(ShipmentSchema),
        JSON.parse(await readFile("changelog.json", "utf8")),
      );
    } catch {
      return [];
    }
  }
  const tags = (
    await git(
      "tag",
      "--list",
      "v*",
      "--sort=creatordate",
      "--format=%(refname:short)%09%(if)%(*objectname)%(then)%(*objectname)%(else)%(objectname)%(end)%09%(creatordate:iso-strict)",
    )
  )
    .split("\n")
    .filter((line) => line !== "")
    .map((line) => {
      const [tag = "", commit = "", date = ""] = line.split("\t", 3);
      return { tag, commit, date };
    });
  const shipped: Array<Shipment> = [];
  for (const [index, { tag, commit, date }] of tags.entries()) {
    const previous = tags[index - 1];
    const log =
      previous == null
        ? ""
        : await git("log", "--format=%H%x09%s", `${previous.tag}..${tag}`);
    shipped.unshift({
      version: tag.slice(1),
      commit,
      date,
      commits: log
        .split("\n")
        .filter((line) => line !== "")
        .map((line) => {
          const at = line.indexOf("\t");
          return { sha: line.slice(0, at), subject: line.slice(at + 1) };
        }),
    });
  }
  return shipped;
});

export async function stamp() {
  return remember("site:stamp", async () => {
    const found = await db.execute<{
      published: string | null;
      calver: string | null;
      doi: string | null;
    }>(sql`
      select
        (select to_char(max(published_at) at time zone 'UTC', ${sql.raw(UTC)}) from changeset where status = 'published') as published,
        r.calver, r.doi
      from (select 1) one
      left join lateral (select calver, doi from release order by created_at desc limit 1) r on true
    `);
    const row = found.rows[0];
    return {
      published: row?.published ?? null,
      release:
        row?.calver == null ? null : { calver: row.calver, doi: row.doi },
    };
  });
}

export const live = memo(async () => {
  const found = await liveDb.execute<{
    isShown: boolean;
    newest: string | null;
  }>(sql`
    select coalesce((select shown from live.switch where name = 'results'), false) as "isShown",
      (select to_char(as_of at time zone 'UTC', ${sql.raw(UTC)}) from live.frame where status = 'active' order by k desc limit 1) as newest
  `);
  const row = found.rows[0];
  const newest = row?.newest ?? null;
  return {
    state: !(row?.isShown ?? false)
      ? ("off" as const)
      : newest != null && Date.now() - Date.parse(newest) < STALE
        ? ("updating" as const)
        : ("paused" as const),
    newest,
  };
});

export async function site() {
  const [shipped, data, status] = await Promise.all([build(), stamp(), live()]);
  return { build: shipped, ...data, live: status };
}

export type Site = Awaited<ReturnType<typeof site>>;
