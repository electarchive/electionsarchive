import type { RequestEvent } from "@sveltejs/kit";
import { version as build, building } from "$app/env";
import { CLOUDFLARE_PURGE_TOKEN, CLOUDFLARE_ZONE_ID } from "$app/env/private";
import { BASE_URL } from "$app/env/public";
import { cookieName, isLocale } from "#lib/paraglide/runtime.js";
import { redis } from "#lib/server/redis.js";

const LOCALE = "x-locale";

const PERSONAL = /^\/(?:api\/auth|mcp|rpc)(?:\/|$)/u;

const SIGNED = /(?:^|;\s*)(?:__Secure-)?better-auth\./u;

function isShareable(
  { cookies, locals, request, url }: RequestEvent,
  response: Response,
) {
  if (
    !["GET", "HEAD"].includes(request.method) ||
    response.status !== 200 ||
    response.headers.has("cache-control") ||
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

export function mark(event: RequestEvent, response: Response) {
  if (!isShareable(event, response)) {
    if (!response.headers.has("cache-control")) {
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
  response.headers.set(
    "cloudflare-cdn-cache-control",
    "public, max-age=300, stale-while-revalidate=86400, stale-if-error=86400",
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
