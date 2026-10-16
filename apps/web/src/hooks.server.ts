import type { Handle } from "@sveltejs/kit/hooks";
import { building } from "$app/env";
import { sequence } from "@sveltejs/kit/hooks";
import { svelteKitHandler } from "better-auth/svelte-kit";
import { paraglideMiddleware } from "#lib/paraglide/server.js";
import { auth } from "#lib/server/auth.js";
import { twin } from "#lib/server/twin.js";

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
  return response;
};

export const handle: Handle = sequence(paraglideHandle, twinHandle, authHandle);
