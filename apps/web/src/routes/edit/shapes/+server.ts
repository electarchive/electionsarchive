import { error } from "@sveltejs/kit";
import * as v from "valibot";
import { shapes } from "#lib/server/workspace.js";
import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async ({ locals, request }) => {
  if (locals.user == null) {
    error(401, "Sign in to edit a draft.");
  }
  const body = v.safeParse(
    v.object({
      upload: v.pipe(v.string(), v.uuid()),
      scheme: v.pipe(v.string(), v.maxLength(60)),
      field: v.pipe(v.string(), v.maxLength(200)),
      name: v.pipe(v.string(), v.maxLength(200)),
      on: v.pipe(v.string(), v.isoDate()),
    }),
    await request.json(),
  );
  if (!body.success) {
    error(400, body.issues[0].message);
  }
  const { upload, ...options } = body.output;
  const found = await shapes(upload, locals.user.id, options);
  if (found == null) {
    error(404, "That boundary file is not ready.");
  }
  return Response.json(found);
};
