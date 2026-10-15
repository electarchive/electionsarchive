import type { Handle } from "@sveltejs/kit/hooks";
import { building } from "$app/env";
import { sequence } from "@sveltejs/kit/hooks";
import { svelteKitHandler } from "better-auth/svelte-kit";
import { paraglideMiddleware } from "#lib/paraglide/server.js";
import { auth } from "#lib/server/auth.js";

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

export const handle: Handle = sequence(paraglideHandle, authHandle);
