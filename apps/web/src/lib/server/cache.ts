import { os as base } from "@orpc/server";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";

const LIMIT = 800;
const CHECK = 5000;

const entries = new Map<string, Promise<unknown>>();
const procedures = new WeakMap<object, number>();
const state: {
  version: string;
  checked: number;
  checking: Promise<void> | null;
  procedures: number;
} = { version: "", checked: 0, checking: null, procedures: 0 };

async function refresh() {
  try {
    const result = await db.execute<{ version: string }>(sql`
      select concat_ws(':', current_date, count(*) filter (where status = 'published'), max(published_at),
        count(*) filter (where status = 'rejected')) as version
      from changeset
    `);
    const version = result.rows[0]?.version ?? "";
    if (version !== state.version) {
      entries.clear();
      state.version = version;
    }
    state.checked = Date.now();
  } finally {
    state.checking = null;
  }
}

export async function remember<T>(key: string, compute: () => Promise<T>) {
  if (Date.now() - state.checked >= CHECK) {
    state.checking ??= refresh();
    await state.checking;
  }
  const hit = entries.get(key);
  if (hit != null) {
    entries.delete(key);
    entries.set(key, hit);
    return hit as Promise<T>;
  }
  const pending = compute();
  entries.set(key, pending);
  void (async () => {
    try {
      await pending;
    } catch {
      if (entries.get(key) === pending) {
        entries.delete(key);
      }
    }
  })();
  if (entries.size > LIMIT) {
    const oldest = entries.keys().next().value;
    if (oldest != null) {
      entries.delete(oldest);
    }
  }
  return pending;
}

export const os = base.use(async ({ next, procedure }, input) => {
  const id = procedures.get(procedure) ?? state.procedures++;
  procedures.set(procedure, id);
  const output = await remember(`${id}:${JSON.stringify(input)}`, async () => {
    const result = await next();
    return result.output;
  });
  return { output, context: {} };
});
