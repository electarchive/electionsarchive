import { error } from "@sveltejs/kit";
import { changes, releases } from "#lib/server/downloads.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  if ((await releases()).every((row) => row.calver !== params.version)) {
    error(404);
  }
  const offset = Number(url.searchParams.get("offset") ?? "0");
  const pipeline = url.searchParams.get("pipeline");
  const country = url.searchParams.get("country");
  if (
    !Number.isSafeInteger(offset) ||
    offset < 0 ||
    (country != null && !/^(?:[A-Z]{2}|-)$/u.test(country)) ||
    (pipeline != null && pipeline.length > 200)
  ) {
    error(400);
  }
  return Response.json(
    await changes(params.version, { offset, pipeline, country }),
    { headers: { "cache-control": "public, max-age=300" } },
  );
};
