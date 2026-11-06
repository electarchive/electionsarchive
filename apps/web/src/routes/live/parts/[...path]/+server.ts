import type { Part } from "svelte-islands";
import { error, json } from "@sveltejs/kit";
import { night } from "#lib/server/live-page.js";
import { liveParts } from "#lib/server/live-parts.js";
import { holders } from "#lib/server/rpc/tonight.js";
import type { RequestHandler } from "./$types";

function described({ name, hash, bytes }: Part, path: string) {
  return { name, hash, source: `/live/board/${path}`, bytes };
}

export const GET: RequestHandler = async ({ params }) => {
  const { election, estimates, shown } = await night(params.path);
  if (election.slug !== params.path) {
    error(404);
  }
  const { board, updates } = liveParts(
    election,
    estimates,
    election.slug.split("/").length === 2
      ? await holders(election.races, election.date, election.slug, true)
      : null,
  );
  return json({
    shown,
    head: board.head,
    stable: described(board.stable, election.slug),
    changing: described(board.changing, election.slug),
    updates,
  });
};
