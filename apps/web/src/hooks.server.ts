import type { Handle } from "@sveltejs/kit/hooks";
import { building } from "$app/env";
import { READ_ONLY } from "$app/env/public";
import { sequence } from "@sveltejs/kit/hooks";
import { svelteKitHandler } from "better-auth/svelte-kit";
import { paraglideMiddleware } from "#lib/paraglide/server.js";
import { auth } from "#lib/server/auth.js";
import { READ_ONLY_MESSAGE } from "#lib/server/rpc/router.js";
import { twin } from "#lib/server/twin.js";

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

const WRITABLE =
  /^\/(?:api\/auth|rpc|api\/v1|mcp|sign-in|sign-up|sign-out|settings\/username)(?:\/|$)/u;

const SHUT =
  /^\/api\/auth\/(?:api-key|admin|update-user|change-email|delete-user)(?:\/|$)/u;

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
  const response = (await twin(event)) ?? (await resolve(event));
  if (response.headers.get("content-type")?.startsWith("text/html") === true) {
    response.headers.append("vary", "Accept");
  }
  if (
    /^\/(?:live|rpc\/live|api\/v1\/live)(?:[/.]|$)/.test(event.url.pathname)
  ) {
    response.headers.set("cache-control", "no-store");
  }
  return response;
};

export const handle: Handle = sequence(
  readOnlyHandle,
  paraglideHandle,
  twinHandle,
  authHandle,
);
