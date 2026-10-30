import { error } from "@sveltejs/kit";
import * as v from "valibot";
import { sheet } from "#lib/server/workspace.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, locals }) => {
  if (locals.user == null) {
    error(401, "Sign in to edit a draft.");
  }
  if (!v.is(v.pipe(v.string(), v.uuid()), params.id)) {
    error(404);
  }
  return Response.json(await sheet(params.id.toLowerCase()), {
    headers: { "cache-control": "no-store" },
  });
};
