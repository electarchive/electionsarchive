import { error } from "@sveltejs/kit";
import { mock } from "#lib/server/live-board.js";
import type { PageServerLoad } from "./$types";

const PATHS = new Set([
  "us/mi/2026-11-03-general",
  "us/mi/2026-11-03-general/senate",
]);

export const load: PageServerLoad = async ({ params, setHeaders }) => {
  if (!PATHS.has(params.path)) {
    error(404);
  }
  setHeaders({ "x-robots-tag": "noindex, nofollow" });
  return mock();
};
