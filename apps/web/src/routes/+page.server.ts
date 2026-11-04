import { part } from "svelte-islands/server";
import { home } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => {
  const found = await home();
  return {
    ...found,
    strata:
      found.strata.rows.length === 0 ? null : part("home.strata", found.strata),
  };
};
