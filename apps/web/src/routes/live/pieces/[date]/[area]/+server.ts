import { error } from "@sveltejs/kit";
import { remember } from "#lib/server/cache.js";
import { pieces } from "#lib/server/live-board.js";
import type { RequestHandler } from "./$types";

const DAY = /^\d{4}-\d{2}-\d{2}$/u;
const ID = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/u;

export const GET: RequestHandler = async ({ params, setHeaders }) => {
  if (!DAY.test(params.date) || !ID.test(params.area)) {
    error(404);
  }
  const found = await remember(
    `live:pieces:v5:${params.date}:${params.area}:us.county`,
    async () => pieces(params.area, params.date, "us.county"),
  );
  if (found == null) {
    error(404);
  }
  setHeaders({ "cache-control": "public, max-age=3600" });
  return Response.json(found);
};
