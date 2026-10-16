import { call, ORPCError } from "@orpc/server";
import { error, redirect } from "@sveltejs/kit";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params }) => {
  let path: string | null = null;
  try {
    ({ path } = await call(router.entities.path, { id: params.id }));
  } catch (error_) {
    if (!(error_ instanceof ORPCError)) {
      throw error_;
    }
  }
  if (path == null) {
    error(404);
  }
  redirect(307, `/${path}`);
};
