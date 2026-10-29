import { call, ORPCError } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  try {
    return {
      night: await call(router.live.get, { path: params.path }),
      at: url.searchParams.get("at"),
    };
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      error(404);
    }
    throw error_;
  }
};
