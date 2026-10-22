import { call } from "@orpc/server";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url }) => {
  const q = (url.searchParams.get("q") ?? "").trim().slice(0, 200);
  return { q, hits: await call(router.search.query, { q, limit: 50 }) };
};
