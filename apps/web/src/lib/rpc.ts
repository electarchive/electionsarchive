import type { RouterClient } from "@orpc/server";
import { browser } from "$app/env";
import { BASE_URL } from "$app/env/public";
import { createORPCClient } from "@orpc/client";
import { RPCLink } from "@orpc/client/fetch";
import { createTanstackQueryUtils } from "@orpc/tanstack-query";
import type { Router } from "#lib/server/rpc/router.js";

const client: RouterClient<Router> = createORPCClient(
  new RPCLink({ url: "/rpc", origin: browser ? location.origin : BASE_URL }),
);

export const orpc = createTanstackQueryUtils(client);
