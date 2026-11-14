import { latestCalver, release, releases } from "#lib/server/downloads.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url }) => {
  const calver = await latestCalver();
  return {
    origin: url.origin,
    releases: await releases(),
    release: calver == null ? null : await release(calver),
  };
};
