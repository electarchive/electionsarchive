import { error } from "@sveltejs/kit";
import * as v from "valibot";
import { upload } from "#lib/server/uploads.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, locals, setHeaders }) => {
  if (!v.is(v.pipe(v.string(), v.uuid()), params.id)) {
    error(404, "There is no upload with that ID.");
  }
  setHeaders({ "cache-control": "no-store" });
  return Response.json(await upload(params.id, locals.user?.id));
};
