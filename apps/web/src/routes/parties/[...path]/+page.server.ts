import { call, ORPCError } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params }) => {
  const segments = params.path.split("/");
  const slug = segments.at(-1) ?? "";
  try {
    const { id } = await call(router.parties.locate, {
      polity: segments.length > 1 ? segments.slice(0, -1).join("/") : undefined,
      slug,
    });
    const [party, record] = await Promise.all([
      call(router.parties.get, { id }),
      call(router.parties.elections, { id }),
    ]);
    return { id, party, record };
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      error(404);
    }
    throw error_;
  }
};
