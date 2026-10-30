import { error } from "@sveltejs/kit";
import * as v from "valibot";
import { notes } from "#lib/server/workspace.js";
import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async ({ locals, request }) => {
  if (locals.user == null) {
    error(401, "Sign in to edit a draft.");
  }
  const body = v.safeParse(
    v.object({
      entities: v.pipe(
        v.array(v.pipe(v.string(), v.uuid())),
        v.minLength(1),
        v.maxLength(50),
      ),
    }),
    await request.json(),
  );
  if (!body.success) {
    error(400, body.issues[0].message);
  }
  return Response.json(await notes(body.output.entities));
};
