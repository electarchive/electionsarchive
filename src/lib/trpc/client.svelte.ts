import { browser } from "$app/env";
import { page } from "$app/state";
import {
  createTRPCClient,
  httpBatchStreamLink,
  loggerLink,
} from "@trpc/client";
import SuperJSON from "superjson";
import type { AppRouter } from "./router";

export function useTRPC() {
  return createTRPCClient<AppRouter>({
    links: [
      loggerLink({
        enabled: (op) =>
          import.meta.env.DEV ||
          (op.direction === "down" && op.result instanceof Error),
      }),
      httpBatchStreamLink({
        transformer: SuperJSON,
        url: browser ? "/api/trpc" : `${page.url.origin}/api/trpc`,
        headers: () => {
          const headers = new Headers();
          headers.set("x-trpc-source", "svelte");
          return headers;
        },
      }),
    ],
  });
}
