import type { Handle } from "@sveltejs/kit/hooks";
import { building } from "$app/env";
import { sequence } from "@sveltejs/kit/hooks";
import { fetchRequestHandler } from "@trpc/server/adapters/fetch";
import { svelteKitHandler } from "better-auth/svelte-kit";
import { paraglideMiddleware } from "#lib/paraglide/server.js";
import { createTRPCContext } from "#lib/server/api/trpc.js";
import { auth } from "#lib/server/auth.js";
import { appRouter } from "#lib/trpc/router.js";

const endpoint = "/api/trpc";

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

const trpcHandle: Handle = async ({ event, resolve }) => {
  if (event.url.pathname.startsWith(endpoint)) {
    return fetchRequestHandler({
      endpoint,
      req: event.request,
      router: appRouter,
      createContext: createTRPCContext,
    });
  }

  const response = await resolve(event);
  return response;
};

export const handle: Handle = sequence(paraglideHandle, authHandle, trpcHandle);
