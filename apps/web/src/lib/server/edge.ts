import type { RequestEvent } from "@sveltejs/kit";
import { version as build, dev } from "$app/env";
import { createHash } from "node:crypto";
import { cookieName, isLocale } from "#lib/paraglide/runtime.js";

const LOCALE = "x-locale";

const BUILD = "build";

const LIVE = /^\/(?:live|rpc\/live|api\/v1\/live)(?:[/.]|$)/u;

const PERSONAL = /^\/(?:api\/auth|mcp|rpc(?!\/live\/stamp$))(?:\/|$)/u;

const SIGNED = /(?:^|;\s*)(?:__Secure-)?better-auth\./u;

function isShareable(
  { cookies, isDataRequest, locals, request, url }: RequestEvent,
  response: Response,
) {
  if (
    !["GET", "HEAD"].includes(request.method) ||
    (response.status !== 200 &&
      !(
        url.pathname.startsWith("/id/") && [307, 308].includes(response.status)
      )) ||
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

async function settled(response: Response, isMoved: boolean) {
  if (response.headers.get("content-type") !== "application/json") {
    return null;
  }
  const text = await response.clone().text();
  if (isMoved && text.startsWith('{"type":"redirect",')) {
    return text;
  }
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
      ? await settled(response, event.url.pathname.startsWith("/id/"))
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
  const tag = page(event.url.pathname);
  if (tag != null) {
    response.headers.set("cache-tag", tag);
  }
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

function page(pathname: string) {
  const tag =
    pathname.replace(/\.(?:md|json)$/u, "").replace(/^\/index$/u, "") || "/";
  return tag.length <= 1024 && /^[\u{21}-\u{2B}\u{2D}-\u{7E}]+$/u.test(tag)
    ? tag
    : null;
}

export function skewed({ isDataRequest, url }: RequestEvent) {
  if (!isDataRequest) {
    return null;
  }
  const sent = url.searchParams.get(BUILD);
  url.searchParams.delete(BUILD);
  if (dev || sent === build) {
    return null;
  }
  return Response.json(
    { message: "This page was built by an earlier version of the site" },
    {
      status: 409,
      headers: { "cache-control": "no-store", "x-sveltekit-version": build },
    },
  );
}
