import { error, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { change, PublicId } from "#lib/server/history.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const id = v.safeParse(PublicId, params.id);
  if (!id.success || !v.is(v.pipe(v.string(), v.uuid()), params.changeset)) {
    error(404);
  }
  const found = await change(id.output, params.changeset);
  if (found.entity.public_id !== params.id) {
    redirect(301, `/history/${found.entity.public_id}/${params.changeset}`);
  }
  return found;
};
