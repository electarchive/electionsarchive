import type { RequestEvent } from "@sveltejs/kit";
import type { Handle } from "@sveltejs/kit/hooks";
import { building, version } from "$app/env";
import { RESP_TYPES } from "@redis/client";
import { Buffer } from "node:buffer";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";
import { deserialize, serialize } from "node:v8";
import { zstdCompress, zstdDecompress } from "node:zlib";
import { cookieName } from "#lib/paraglide/runtime.js";
import { isTraining } from "#lib/server/crawlers.js";
import { LIVE, LOCALE, SIGNED } from "#lib/server/edge.js";
import { redis } from "#lib/server/redis.js";
import { negotiate } from "#lib/server/twin.js";

const FRESH = 5000;
const STALE = 60_000;
const HELD = 30_000;
const PATIENCE = 8000;
const POLL = 150;
const LARGEST = 16 * 1024 ** 2;
const BUDGET = 256 * 1024 ** 2;
const KEPT = 96;
const REFRESH = "x-ea-refresh";
const SHAREABLE = "cloudflare-cdn-cache-control";
const DROPPED = new Set([
  "connection",
  "content-length",
  "date",
  "keep-alive",
  "server-timing",
  "set-cookie",
  "transfer-encoding",
]);
const CHOSEN = new RegExp(String.raw`(?:^|;\s*)${cookieName}=([^;]*)`, "u");

type Shared = {
  at: number;
  status: number;
  headers: Array<[string, string]>;
  body: Uint8Array<ArrayBuffer>;
};

const pack = promisify(zstdCompress);
const unpack = promisify(zstdDecompress);
const local = new Map<string, Shared>();
const flights = new Map<string, Promise<Shared | null>>();
let size = 0;

let connection: ReturnType<typeof connect> | null = null;

function connect() {
  return redis().withTypeMapping({ [RESP_TYPES.BLOB_STRING]: Buffer });
}

function client() {
  connection ??= connect();
  redis();
  return connection.isReady ? connection : null;
}

function keyOf({ request, url }: RequestEvent) {
  if (building || request.method !== "GET" || !LIVE.test(url.pathname)) {
    return null;
  }
  const { headers } = request;
  const cookie = headers.get("cookie") ?? "";
  const edge = headers.get(LOCALE);
  const chosen = CHOSEN.exec(cookie)?.[1] ?? null;
  if (
    SIGNED.test(cookie) ||
    headers.has("authorization") ||
    headers.has("x-api-key") ||
    headers.has(REFRESH) ||
    isTraining(headers.get("user-agent")) ||
    (chosen != null && chosen !== edge)
  ) {
    return null;
  }
  const format = negotiate(headers.get("accept")) ?? "html";
  return `${version}|${edge ?? ""}|${format}|${url.pathname}${url.search}`;
}

function age(shared: Shared) {
  return Date.now() - shared.at;
}

function hold(key: string, shared: Shared) {
  const kept = local.get(key);
  if (kept != null) {
    if (kept.at >= shared.at) {
      return kept;
    }
    size -= kept.body.byteLength;
    local.delete(key);
  }
  local.set(key, shared);
  size += shared.body.byteLength;
  for (const [oldest, entry] of local) {
    if (local.size <= KEPT && size <= BUDGET) {
      break;
    }
    size -= entry.body.byteLength;
    local.delete(oldest);
  }
  return shared;
}

async function fetchShared(key: string) {
  try {
    const found = await client()?.get(`share:${key}`);
    if (found == null) {
      return null;
    }
    const parsed: unknown = deserialize(await unpack(found));
    if (
      typeof parsed !== "object" ||
      parsed == null ||
      !("at" in parsed && "status" in parsed && "headers" in parsed) ||
      !("body" in parsed && parsed.body instanceof Uint8Array) ||
      typeof parsed.at !== "number" ||
      typeof parsed.status !== "number" ||
      !Array.isArray(parsed.headers)
    ) {
      return null;
    }
    const headers: Array<[string, string]> = [];
    for (const pair of parsed.headers) {
      if (
        Array.isArray(pair) &&
        typeof pair[0] === "string" &&
        typeof pair[1] === "string"
      ) {
        headers.push([pair[0], pair[1]]);
      }
    }
    return hold(key, {
      at: parsed.at,
      status: parsed.status,
      headers,
      body: new Uint8Array(parsed.body),
    });
  } catch {
    return null;
  }
}

async function save(key: string, shared: Shared) {
  const packed = await pack(serialize(shared)).catch(() => null);
  if (packed != null) {
    await client()
      ?.set(`share:${key}`, packed, { PX: STALE })
      .catch(() => null);
  }
}

async function claim(key: string, token: string) {
  const owner = client();
  if (owner == null) {
    return true;
  }
  return (
    (await owner
      .set(`share:lock:${key}`, token, { NX: true, PX: HELD })
      .catch(() => "OK")) != null
  );
}

async function release(key: string, token: string) {
  const owner = client();
  const held = await owner?.get(`share:lock:${key}`).catch(() => null);
  if (held?.toString() === token) {
    await owner?.del(`share:lock:${key}`).catch(() => null);
  }
}

async function follow(key: string, since: number) {
  const deadline = Date.now() + PATIENCE;
  while (Date.now() < deadline) {
    await new Promise((done) => setTimeout(done, POLL));
    const found = await fetchShared(key);
    if (found != null && found.at > since) {
      return found;
    }
    const held = await client()
      ?.exists(`share:lock:${key}`)
      .catch(() => 1);
    if (held === 0) {
      return null;
    }
  }
  return null;
}

async function capture(response: Response) {
  if (
    response.status !== 200 ||
    !response.headers.has(SHAREABLE) ||
    response.headers.has("set-cookie") ||
    response.headers.get("content-type")?.startsWith("text/event-stream") ===
      true
  ) {
    return null;
  }
  const body = new Uint8Array(await response.arrayBuffer());
  return {
    at: Date.now(),
    status: response.status,
    headers: [...response.headers].filter(([name]) => !DROPPED.has(name)),
    body,
  };
}

function answer(shared: Shared, state: "hit" | "stale" | "miss") {
  const headers = new Headers(shared.headers);
  headers.set("x-share", state);
  return new Response(shared.body, { status: shared.status, headers });
}

async function obtain(
  key: string,
  event: RequestEvent,
  resolve: Parameters<Handle>[0]["resolve"],
) {
  const kept = local.get(key) ?? null;
  const stored = await fetchShared(key);
  const best = stored ?? kept;
  if (best != null && age(best) < FRESH) {
    return { shared: best, response: answer(best, "hit") };
  }
  const token = randomUUID();
  const isOwner = await claim(key, token);
  if (!isOwner) {
    if (best != null && age(best) < STALE) {
      return { shared: best, response: answer(best, "stale") };
    }
    const awaited = await follow(key, best?.at ?? 0);
    if (awaited != null) {
      return { shared: awaited, response: answer(awaited, "hit") };
    }
  }
  try {
    const response = await resolve(event);
    const captured = await capture(response);
    if (captured == null) {
      return { shared: null, response };
    }
    if (captured.body.byteLength > LARGEST) {
      return { shared: null, response: answer(captured, "miss") };
    }
    const shared = hold(key, captured);
    void save(key, shared);
    return { shared, response: answer(shared, "miss") };
  } finally {
    if (isOwner) {
      void release(key, token);
    }
  }
}

export const shareHandle: Handle = async ({ event, resolve }) => {
  const key = keyOf(event);
  if (key == null) {
    return resolve(event);
  }
  const kept = local.get(key);
  if (kept != null && age(kept) < FRESH) {
    return answer(kept, "hit");
  }
  const pending = flights.get(key);
  if (pending != null) {
    if (kept != null && age(kept) < STALE) {
      return answer(kept, "stale");
    }
    const shared = await pending;
    return shared == null ? resolve(event) : answer(shared, "hit");
  }
  const { promise, resolve: settle } = Promise.withResolvers<Shared | null>();
  flights.set(key, promise);
  try {
    const { shared, response } = await obtain(key, event, resolve);
    settle(shared);
    return response;
  } catch (error) {
    settle(null);
    throw error;
  } finally {
    flights.delete(key);
  }
};
