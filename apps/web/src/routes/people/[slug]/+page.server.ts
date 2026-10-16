import { call, ORPCError } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params }) => {
  try {
    const { id } = await call(router.people.locate, { slug: params.slug });
    return { id, person: await call(router.people.get, { id }) };
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      error(404);
    }
    throw error_;
  }
};
