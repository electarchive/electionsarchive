import type { RequestEvent } from "@sveltejs/kit";
import { version as build, building } from "$app/env";
import { CLOUDFLARE_PURGE_TOKEN, CLOUDFLARE_ZONE_ID } from "$app/env/private";
import { BASE_URL } from "$app/env/public";
import { createHash } from "node:crypto";
import { cookieName, isLocale } from "#lib/paraglide/runtime.js";
import { redis } from "#lib/server/redis.js";

const LOCALE = "x-locale";

const LIVE = /^\/(?:live|rpc\/live|api\/v1\/live)(?:[/.]|$)/u;

const PERSONAL = /^\/(?:api\/auth|mcp|rpc(?!\/live\/stamp$))(?:\/|$)/u;

const SIGNED = /(?:^|;\s*)(?:__Secure-)?better-auth\./u;

function isShareable(
  { cookies, isDataRequest, locals, request, url }: RequestEvent,
  response: Response,
) {
  if (
    !["GET", "HEAD"].includes(request.method) ||
    response.status !== 200 ||
    (response.headers.has("cache-control") && !isDataRequest) ||
    response.headers.has("set-cookie") ||
    locals.user != null ||
    PERSONAL.test(url.pathname) ||
    SIGNED.test(request.headers.get("cookie") ?? "") ||
    request.headers.has("authorization") ||
    request.headers.has("x-api-key")
  ) {
    return false;
  }
  const chosen = cookies.get(cookieName);
  return (
    (request.headers.get(LOCALE) ?? undefined) === chosen &&
    (chosen == null || isLocale(chosen))
  );
}

async function settled(response: Response) {
  if (response.headers.get("content-type") !== "application/json") {
    return null;
  }
  const text = await response.clone().text();
  return text.startsWith('{"type":"data","nodes":[') &&
    !text.includes('{"type":"error"')
    ? text
    : null;
}

export async function mark(event: RequestEvent, response: Response) {
  const isLive = LIVE.test(event.url.pathname);
  const isOpen = isShareable(event, response);
  const data =
    isOpen && event.isDataRequest && !isLive
      ? await settled(response)
      : undefined;
  if (!isOpen || data === null) {
    if (isLive) {
      response.headers.set("cache-control", "no-store");
    } else if (!response.headers.has("cache-control")) {
      response.headers.set("cache-control", "private, no-cache");
    }
    return response;
  }
  const varies = new Set(
    (response.headers.get("vary") ?? "")
      .split(",")
      .map((name) => name.trim().toLowerCase())
      .filter((name) => !["", "cookie", "accept-language"].includes(name)),
  );
  varies.add(LOCALE);
  response.headers.set("vary", [...varies].join(", "));
  response.headers.set("cache-control", "no-cache");
  if (data != null) {
    response.headers.set(
      "etag",
      `"${createHash("sha1").update(data).digest("base64url")}"`,
    );
  }
  response.headers.set(
    "cloudflare-cdn-cache-control",
    isLive
      ? "public, max-age=5, stale-while-revalidate=10, stale-if-error=3600"
      : "public, max-age=300, stale-while-revalidate=86400, stale-if-error=86400",
  );
  return response;
}

export async function purge() {
  if (
    building ||
    CLOUDFLARE_ZONE_ID == null ||
    CLOUDFLARE_PURGE_TOKEN == null
  ) {
    return;
  }
  const host = new URL(BASE_URL).host;
  const key = `edge:purged:${host}`;
  try {
    if ((await redis().get(key)) === build) {
      return;
    }
    const response = await fetch(
      `https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/purge_cache`,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${CLOUDFLARE_PURGE_TOKEN}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ hosts: [host] }),
      },
    );
    if (!response.ok) {
      console.error(
        "Edge purge failed:",
        response.status,
        await response.text(),
      );
      return;
    }
    await redis().set(key, build);
  } catch (error) {
    console.error("Edge purge failed:", error);
  }
}
