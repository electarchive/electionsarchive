import type { RequestEvent } from "@sveltejs/kit";
import type { Handle, ServerInit } from "@sveltejs/kit/hooks";
import { building, dev } from "$app/env";
import { READ_ONLY } from "$app/env/public";
import { sequence } from "@sveltejs/kit/hooks";
import { svelteKitHandler } from "better-auth/svelte-kit";
import {
  cookieName,
  defineCustomServerStrategy,
  isLocale,
} from "#lib/paraglide/runtime.js";
import { paraglideMiddleware } from "#lib/paraglide/server.js";
import { auth } from "#lib/server/auth.js";
import { refreshing } from "#lib/server/cache.js";
import { isTraining } from "#lib/server/crawlers.js";
import { LOCALE, mark, skewed } from "#lib/server/edge.js";
import { guardHandle } from "#lib/server/guard.js";
import { timing } from "#lib/server/pages.js";
import { READ_ONLY_MESSAGE } from "#lib/server/rpc/router.js";
import { twin } from "#lib/server/twin.js";

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

const CHOSEN = new RegExp(String.raw`(?:^|;\s*)${cookieName}=`, "u");

defineCustomServerStrategy("custom-edge", {
  getLocale: (request) => {
    const edge = request?.headers.get(LOCALE);
    return edge != null &&
      isLocale(edge) &&
      !CHOSEN.test(request?.headers.get("cookie") ?? "")
      ? edge
      : undefined;
  },
});

const REFRESH = "x-ea-refresh";

const LOOPBACK = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);

const WRITABLE =
  /^\/(?:api\/auth|rpc|api\/v1|mcp|sign-in|sign-up|sign-out|forgot-password|reset-password|verify-email|settings\/profile|settings\/keys|unsubscribe)(?:\/|$)/u;

const FORM_TYPES = new Set([
  "application/x-www-form-urlencoded",
  "multipart/form-data",
  "text/plain",
  "application/x-sveltekit-formdata",
]);

const ONE_CLICK = /^\/unsubscribe\/[0-9a-f]{64}$/u;

const PRELOADED =
  /\/sofia-sans-semi-condensed-latin-wght-normal\.[\w-]+\.woff2$/u;

const SHUT =
  /^\/api\/auth\/(?:api-key|admin|update-user|change-email|delete-user)(?:\/|$)/u;

const csrfHandle: Handle = async ({ event, resolve }) => {
  const { request, url } = event;
  const type = request.headers
    .get("content-type")
    ?.split(";", 1)[0]
    ?.trim()
    .toLowerCase();
  if (
    !dev &&
    !SAFE.has(request.method) &&
    (type == null || FORM_TYPES.has(type)) &&
    request.headers.get("origin") !== url.origin &&
    !(request.method === "POST" && ONE_CLICK.test(url.pathname))
  ) {
    return new Response(
      `Cross-site ${request.method} form submissions are forbidden`,
      { status: 403 },
    );
  }
  return resolve(event);
};

const readOnlyHandle: Handle = async ({ event, resolve }) => {
  const { pathname } = event.url;
  if (
    READ_ONLY &&
    !SAFE.has(event.request.method) &&
    (!WRITABLE.test(pathname) || SHUT.test(pathname))
  ) {
    return Response.json({ message: READ_ONLY_MESSAGE }, { status: 403 });
  }
  return resolve(event);
};

const paraglideHandle: Handle = async ({ event, resolve }) =>
  paraglideMiddleware(
    event.request,
    async ({ request: localizedRequest, locale }) =>
      resolve(
        { ...event, request: localizedRequest },
        {
          transformPageChunk: ({ html }) =>
            html.replace("%lang%", () => locale),
          preload: ({ type, path }) =>
            type === "js" ||
            type === "css" ||
            (type === "font" && PRELOADED.test(path)),
        },
      ),
  );

const authHandle: Handle = async ({ event, resolve }) => {
  const session = await auth.api.getSession({ headers: event.request.headers });

  if (session !== null) {
    event.locals.user = session.user;
    event.locals.session = session.session;
  }

  return svelteKitHandler({ event, resolve, auth, building });
};

const twinHandle: Handle = async ({ event, resolve }) => {
  const isReader = isTraining(event.request.headers.get("user-agent"));
  const twinned = await twin(event, isReader);
  const isFallback =
    isReader &&
    twinned?.status === 404 &&
    !/\.(?:md|json)$/u.test(event.url.pathname);
  const response =
    twinned == null || isFallback ? await resolve(event) : twinned;
  if (response.headers.get("content-type")?.startsWith("text/html") === true) {
    response.headers.append("vary", "Accept");
  }
  if (isFallback) {
    response.headers.append("vary", "User-Agent");
    response.headers.set("cache-control", "private, no-cache");
  }
  return response;
};

const edgeHandle: Handle = async ({ event, resolve }) =>
  skewed(event) ?? mark(event, await resolve(event));

function isLocal(event: RequestEvent) {
  try {
    return LOOPBACK.has(event.getClientAddress());
  } catch {
    return false;
  }
}

const refreshHandle: Handle = async ({ event, resolve }) =>
  event.request.headers.get(REFRESH) === "1" && isLocal(event)
    ? refreshing.run(true, async () => resolve(event))
    : resolve(event);

const timingHandle: Handle = async ({ event, resolve }) => {
  const response = await resolve(event);
  const entries = timing(event.locals);
  if (entries != null) {
    response.headers.set("server-timing", entries);
  }
  return response;
};

export const init: ServerInit = () => {
  process.on("unhandledRejection", (error) => {
    console.error("An unhandled rejection:", error);
  });
};

export const handle: Handle = sequence(
  guardHandle,
  csrfHandle,
  edgeHandle,
  refreshHandle,
  timingHandle,
  readOnlyHandle,
  paraglideHandle,
  twinHandle,
  authHandle,
);
