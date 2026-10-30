import { error } from "@sveltejs/kit";
import * as v from "valibot";
import { relations } from "#lib/server/workspace.js";
import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async ({ locals, request }) => {
  if (locals.user == null) {
    error(401, "Sign in to edit a draft.");
  }
  const body = v.safeParse(
    v.object({ party: v.pipe(v.string(), v.uuid()) }),
    await request.json(),
  );
  if (!body.success) {
    error(400, body.issues[0].message);
  }
  return Response.json(await relations(body.output.party));
};
