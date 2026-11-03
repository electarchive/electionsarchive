import { error, json } from "@sveltejs/kit";
import { remember } from "#lib/server/cache.js";
import { house } from "#lib/server/live-board.js";
import { night } from "#lib/server/live-page.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params }) => {
  if (!/^us\/\d{4}-\d{2}-\d{2}-[a-z-]+$/u.test(params.path)) {
    error(404);
  }
  const { election } = await night(params.path);
  if (election.slug !== params.path) {
    error(404);
  }
  return json(
    await remember(`live:house:v3:${election.date}`, async () =>
      house(election.id, election.date),
    ),
  );
};
