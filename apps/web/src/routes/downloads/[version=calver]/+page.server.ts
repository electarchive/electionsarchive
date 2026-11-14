import { release, releases } from "#lib/server/downloads.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => ({
  origin: url.origin,
  releases: await releases(),
  release: await release(params.version),
});
