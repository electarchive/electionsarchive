import { part } from "svelte-islands/server";
import { home } from "#lib/server/pages.js";
import { atlas } from "#lib/server/world.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => {
  const found = await home();
  return {
    ...found,
    world: part("home.world", atlas(found.polities)),
    strata:
      found.strata.rows.length === 0 ? null : part("home.strata", found.strata),
  };
};
