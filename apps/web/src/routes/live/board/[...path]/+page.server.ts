import { call, ORPCError } from "@orpc/server";
import { error, redirect } from "@sveltejs/kit";
import { remember } from "#lib/server/cache.js";
import { house } from "#lib/server/live-board.js";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, setHeaders }) => {
  const found = await call(router.live.election, { path: params.path }).catch(
    (error_: unknown) => {
      if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
        error(404);
      }
      throw error_;
    },
  );
  const { election } = found;
  if (election.slug !== params.path) {
    redirect(307, `/live/board/${election.slug}`);
  }
  setHeaders({ "x-robots-tag": "noindex, follow" });
  const isNation = election.slug.split("/").length === 2;
  const district = isNation
    ? await remember(`live:house:v2:${election.date}`, async () =>
        house(election.id, election.date),
      )
    : null;
  return { ...found, isNation, cartogram: district?.cartogram ?? null };
};
