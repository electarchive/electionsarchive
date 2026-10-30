import { os as base } from "@orpc/server";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";

const LIMIT = 800;
const CHECK = 5000;

const entries = new Map<
  string,
  {
    value: Promise<unknown>;
    version: string;
    isSettled: boolean;
    isRenewing: boolean;
  }
>();
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
        count(*) filter (where status = 'rejected'), (select built_at from coverage_build)) as version
      from changeset
    `);
    state.version = result.rows[0]?.version ?? "";
    state.checked = Date.now();
  } finally {
    state.checking = null;
  }
}

function store<T>(key: string, compute: () => Promise<T>) {
  const entry = {
    value: compute() as Promise<unknown>,
    version: state.version,
    isSettled: false,
    isRenewing: false,
  };
  void (async () => {
    try {
      await entry.value;
      entry.isSettled = true;
    } catch {
      if (entries.get(key) === entry) {
        entries.delete(key);
      }
    }
  })();
  return entry;
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
      const next = store(key, compute);
      entries.set(key, next);
      return next.value as Promise<T>;
    }
    if (hit.version !== state.version && hit.isSettled && !hit.isRenewing) {
      hit.isRenewing = true;
      const next = store(key, compute);
      void (async () => {
        try {
          await next.value;
          if (entries.get(key) === hit) {
            entries.set(key, next);
          }
        } catch {
          hit.isRenewing = false;
        }
      })();
    }
    return hit.value as Promise<T>;
  }
  const entry = store(key, compute);
  entries.set(key, entry);
  if (entries.size > LIMIT) {
    const oldest = entries.keys().next().value;
    if (oldest != null) {
      entries.delete(oldest);
    }
  }
  return entry.value as Promise<T>;
}

function cached(isStrict: boolean) {
  return base.use(async ({ next, procedure }, input) => {
    const id = procedures.get(procedure) ?? state.procedures++;
    procedures.set(procedure, id);
    const output = await remember(
      `${id}:${JSON.stringify(input)}`,
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
