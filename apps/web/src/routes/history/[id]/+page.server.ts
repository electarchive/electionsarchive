import { error, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { Before, history } from "#lib/server/history.js";
import { PublicId } from "#lib/server/talk.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const id = v.safeParse(PublicId, params.id);
  if (!id.success) {
    error(404);
  }
  const before = url.searchParams.get("before");
  const found = await history(
    id.output,
    before != null && v.is(Before, before) ? before : null,
  );
  if (found.entity.public_id !== params.id) {
    redirect(301, `/history/${found.entity.public_id}${url.search}`);
  }
  return { ...found, paged: before != null };
};
