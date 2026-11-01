import { error, json } from "@sveltejs/kit";
import { ballotLocals } from "#lib/server/live-ballot.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, setHeaders }) => {
  if (!/^us\/[a-z]{2}\/\d{4}-\d{2}-\d{2}-[a-z-]+$/u.test(params.path)) {
    error(404);
  }
  const found = await ballotLocals(params.path);
  if (found == null) {
    error(404);
  }
  setHeaders({ "cache-control": "public, max-age=300" });
  return json(found);
};
