import { dev } from "$app/env";
import { redirect } from "@sveltejs/kit";
import { part } from "svelte-islands/server";
import { remember } from "#lib/server/cache.js";
import { house } from "#lib/server/live-board.js";
import { night, stamped } from "#lib/server/live-page.js";
import { liveParts } from "#lib/server/live-parts.js";
import { holders } from "#lib/server/rpc/tonight.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, setHeaders, url }) => {
  const [{ election, estimates, shown }, stamp] = await Promise.all([
    night(params.path),
    stamped(params.path),
  ]);
  if (election.slug !== params.path) {
    redirect(307, `/live/board/${election.slug}`);
  }
  setHeaders({ "x-robots-tag": "noindex, follow" });
  const isNation = election.slug.split("/").length === 2;
  const [district, control] = await Promise.all([
    isNation
      ? remember(`live:house:v3:${election.date}`, async () =>
          house(election.id, election.date),
        )
      : null,
    holders(election.races, election.date, election.slug, isNation),
  ]);
  return {
    ...liveParts(
      election,
      estimates,
      isNation ? control : null,
      dev && url.searchParams.has("rehearsal"),
    ),
    shown,
    stamp,
    isNation,
    cartogram:
      district?.cartogram == null
        ? null
        : part("live.cartogram", district.cartogram, { fetch: "preload" }),
    holders: isNation ? null : control,
  };
};
