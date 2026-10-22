import type { RouterClient } from "@orpc/server";
import { createORPCClient } from "@orpc/client";
import { RPCLink } from "@orpc/client/fetch";
import { createTanstackQueryUtils } from "@orpc/tanstack-query";
import type { Router } from "#lib/server/rpc/router.js";
import { recorded } from "./router.svelte.js";

const client: RouterClient<Router> = createORPCClient(
  new RPCLink({
    url: "/rpc",
    origin: location.origin,
    fetch: async (_url, init, _options, path) => {
      const body: unknown =
        typeof init.body === "string" ? JSON.parse(init.body) : {};
      const input =
        body != null &&
        typeof body === "object" &&
        "json" in body &&
        body.json != null &&
        typeof body.json === "object"
          ? body.json
          : {};
      const found = await recorded(
        `${path.join("/")} ${JSON.stringify(
          input,
          Object.keys(input).toSorted((a, b) => a.localeCompare(b, "en")),
        )}`,
      );
      if (found === undefined) {
        console.info("showcase: not recorded", path.join("/"), input);
        return Response.json(
          {
            json: {
              defined: false,
              code: "NOT_FOUND",
              status: 404,
              message: "Not in this preview",
            },
            meta: [],
          },
          { status: 404 },
        );
      }
      return Response.json(found);
    },
  }),
);

export const orpc = createTanstackQueryUtils(client);
