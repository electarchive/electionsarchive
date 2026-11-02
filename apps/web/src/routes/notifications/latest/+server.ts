import { error, json } from "@sveltejs/kit";
import { latest } from "#lib/server/notifications.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ locals, setHeaders }) => {
  if (locals.user == null) {
    error(401, "Sign in to read your notifications.");
  }
  setHeaders({ "cache-control": "no-store" });
  return json(await latest(locals.user.id));
};
